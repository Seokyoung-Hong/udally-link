# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = [
#   "faster-whisper>=1.1,<2",
#   "av<17",  # faster-whisper 1.2.1이 av.open(metadata_errors=)를 써서 av 19에서 깨진다(확인: 2026-10-12)
#   "fastapi>=0.110",
#   "uvicorn>=0.29",
#   "python-multipart>=0.0.9",
#   "psutil>=5.9",
# ]
# ///
"""유달리용 로컬 전사 서버(OpenAI 호환 + 모델 받기·장비 적합 상태). 실행: uv run server.py

디코딩·VAD·정밀도·GPU 병렬은 faster-whisper(CTranslate2), 받기·캐시는 huggingface_hub를 그대로 쓴다.
이 파일은 그 위에 HTTP 규약, 장비 감지, 메모리 계획, OOM 강등만 얹는다. 환경변수는 README 참고.
"""

import asyncio
import contextlib
import fnmatch
import logging
import math
import os
import secrets
import shutil
import subprocess
import threading
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy
import psutil
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, PlainTextResponse

env = os.environ.get
PRESETS = [m.strip() for m in env(
    "STT_MODELS",
    "Systran/faster-whisper-small,Systran/faster-whisper-medium,"
    "deepdml/faster-whisper-large-v3-turbo-ct2,Systran/faster-whisper-large-v3",
).split(",") if m.strip()]
API_KEY = env("STT_API_KEY") or None
DEVICE = env("STT_DEVICE", "auto")  # auto | cuda | cpu
FORCED_CT = env("STT_COMPUTE_TYPE") or None
GPU_FILTER = {int(i) for i in env("STT_GPUS", "").split(",") if i.strip()}  # 비우면 전부
CPU_FALLBACK = env("STT_CPU_FALLBACK", "1") == "1"
CONCURRENCY = int(env("STT_CONCURRENCY", "3"))
GPU_CTS = ["float16", "int8_float16", "int8"]  # 메모리가 모자라면 이 순서로 낮춘다
FILES = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]
log = logging.getLogger("udally-stt")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ---------- 메모리 계획(순수 함수: test_plan.py가 검사) ----------
def need_mb(weights_mb: float, ct: str, conc: int, cpu: bool = False) -> int:
    """필요 메모리 추정. weights_mb = model.bin 크기(fp16 저장 기준).
    근거(README 표): 이 PC 실측 turbo fp16 로드 +2.0GB·3동시 +0.35GB, int8_float16 +1.05GB/+0.22GB,
    faster-whisper 벤치 large-v2 fp16 4.5GB·small CPU int8 1.5GB."""
    w = weights_mb * {"float32": 2, "float16": 1, "bfloat16": 1}.get(ct, 0.5)
    return round(w * 1.25 + 150 * conc + (800 if cpu else 300))


def plan(weights_mb, gpus, ram_free_mb, level=0, device=DEVICE, forced=FORCED_CT,
         cpu_fallback=CPU_FALLBACK, conc=CONCURRENCY) -> dict:
    """gpus: [{"index", "free_mb"}]. level: OOM으로 강등된 단계(0=float16부터)."""
    if weights_mb is None:
        return {"fits": None, "device": None, "compute_type": None}
    if device != "cpu" and gpus:
        for ct in [forced] if forced else GPU_CTS[level:]:
            per_gpu = math.ceil(conc / len(gpus))
            ok = [g["index"] for g in gpus if g["free_mb"] >= need_mb(weights_mb, ct, per_gpu)]
            if ok:
                per_gpu = math.ceil(conc / len(ok))
                return {"fits": True, "device": "cuda:" + ",".join(map(str, ok)), "compute_type": ct,
                        "gpus": ok, "workers": per_gpu, "need_mb": need_mb(weights_mb, ct, per_gpu)}
    if device != "cpu" and gpus and not cpu_fallback:  # GPU가 있는데 모자라면 CPU로 내리지 않는다
        return {"fits": False, "device": "cuda", "compute_type": None}
    ct = forced if forced in ("int8", "float32") else "int8"
    need = need_mb(weights_mb, ct, conc, cpu=True)
    return {"fits": need <= ram_free_mb, "device": "cpu", "compute_type": ct, "gpus": [],
            "workers": conc, "need_mb": need}


# ---------- 장비 ----------
def gpus() -> list[dict]:
    if DEVICE == "cpu" or gpu_broken or not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        return []
    rows = [[c.strip() for c in line.split(",")] for line in out.strip().splitlines() if line.strip()]
    return [{"index": int(r[0]), "name": r[1], "total_mb": int(r[2]), "free_mb": int(r[3])}
            for r in rows if len(r) == 4 and (not GPU_FILTER or int(r[0]) in GPU_FILTER)]


def ram_free_mb() -> int:
    return psutil.virtual_memory().available // 2**20


# ---------- 상태 ----------
lock = threading.Lock()
sizes: dict[str, int] = {}  # 모델 → 받을 파일 전체 바이트(model.bin 크기는 weights[])
weights: dict[str, float] = {}  # 모델 → model.bin MB
downloading: set[str] = set()
errors: dict[str, str] = {}
levels: dict[str, int] = {}  # OOM 강등 단계
rtf: dict[str, float] = {}  # 처리시간/오디오길이 이동평균
current: dict = {}  # ponytail: 한 번에 한 모델만 올린다 {"id", "plan", "workers": [[WhisperModel, 진행 수]]}. 번갈아 쓰면 바꿀 때마다 다시 올린다.
load_lock = threading.Lock()
gpu_broken: list[str] = []  # GPU 실행이 OOM 말고 다른 이유로 실패하면(라이브러리 누락 등) 이후 CPU만 쓴다


def auth(authorization: str | None = Header(None)) -> None:
    if API_KEY and not secrets.compare_digest(authorization or "", f"Bearer {API_KEY}"):
        raise HTTPException(401, "API 키가 맞지 않습니다(Authorization: Bearer <키>).")


def local_path(model_id: str) -> str | None:
    from faster_whisper import download_model
    try:
        return download_model(model_id, local_files_only=True)
    except Exception:
        return None


def fill_sizes() -> None:
    """프리셋 크기를 한 번 알아 둔다(받은 모델은 디스크, 아니면 Hugging Face API)."""
    from huggingface_hub import HfApi
    for m in PRESETS:
        try:
            p = local_path(m)
            if p and m not in sizes:
                weights[m] = (Path(p) / "model.bin").stat().st_size / 2**20
                continue
            info = HfApi().model_info(m, files_metadata=True)
            files = [s for s in info.siblings if any(fnmatch.fnmatch(s.rfilename, pat) for pat in FILES)]
            sizes[m] = sum(s.size or 0 for s in files)
            weights[m] = sum(s.size or 0 for s in files if s.rfilename == "model.bin") / 2**20
        except Exception as e:
            log.warning("크기 확인 실패 %s: %s", m, e)


def blobs_size(model_id: str) -> int:
    from huggingface_hub import constants
    d = Path(constants.HF_HUB_CACHE) / f"models--{model_id.replace('/', '--')}" / "blobs"
    return sum(f.stat().st_size for f in d.glob("*") if f.is_file()) if d.is_dir() else 0


def meta(model_id: str) -> dict:
    """사람이 읽는 표시 정보. id는 받을 Hugging Face 저장소(CTranslate2 변환본), base_model은 원본."""
    m = re.search(r"whisper-(tiny|base|small|medium|large-v3-turbo|large-v3|large-v2|large)", model_id.lower())
    if not m:  # ponytail: 프리셋은 전부 Whisper 변환본이다. 다른 계열을 넣으면 여기에 규칙을 더한다.
        return {"owned_by": model_id.split("/")[0], "family": None, "display_name": model_id, "source": model_id}
    return {"owned_by": "openai", "family": "Whisper", "display_name": f"OpenAI Whisper {m.group(1)}",
            "base_model": f"openai/whisper-{m.group(1)}", "source": model_id, "format": "CTranslate2"}


def status_of(model_id: str, hw=None) -> dict:
    item = {"id": model_id, "object": "model", **meta(model_id)}
    if model_id in downloading:
        item["status"] = "downloading"
        if sizes.get(model_id):
            item["progress"] = round(min(blobs_size(model_id) / sizes[model_id], 0.99), 3)
    else:
        item["status"] = "ready" if local_path(model_id) else "not_downloaded"
        if model_id in errors and item["status"] == "not_downloaded":
            item["error"] = errors[model_id]
    if current.get("id") == model_id:  # 올라가 있으면 실제 배치를 보여 준다
        p = current["plan"]
    else:
        g, ram = hw or (gpus(), ram_free_mb())
        p = plan(weights.get(model_id), g, ram, levels.get(model_id, 0))
    item.update(fits=p["fits"], device=p["device"], compute_type=p["compute_type"])
    if item["status"] == "ready":
        item["rtf"] = round(rtf[model_id], 3) if model_id in rtf else None
    return item


def fetch(model_id: str) -> None:
    from faster_whisper import download_model
    try:
        download_model(model_id)
        weights[model_id] = (Path(local_path(model_id)) / "model.bin").stat().st_size / 2**20
    except Exception as e:
        errors[model_id] = f"{type(e).__name__}: {e}"[:300]
        return
    finally:
        downloading.discard(model_id)
    warm(model_id)


def warm(model_id: str) -> None:
    """[적용]된 모델을 미리 올려 첫 전사 요청이 올리기·CUDA 첫 실행 시간(이 PC 최대 약 30초)을 치르지 않게 한다."""
    try:
        load(model_id)
    except Exception as e:
        log.warning("미리 올리기 실패 %s: %s", model_id, e)


def is_oom(e: Exception) -> bool:
    return "out of memory" in str(e).lower() or isinstance(e, MemoryError)


def unload() -> None:
    for w in current.get("workers", []):
        w[2].shutdown(wait=False)
    current.clear()


def load(model_id: str) -> dict:
    """모델을 올린다. 올리다 OOM이면 정밀도를 낮춰(끝내 CPU로) 다시 시도한다."""
    from faster_whisper import WhisperModel
    with load_lock:
        if current.get("id") == model_id:
            return current
        unload()
        while True:
            p = plan(weights.get(model_id, 0), gpus(), ram_free_mb(), levels.get(model_id, 0))
            if not p["fits"]:
                raise HTTPException(503, f"이 장비의 메모리로는 {model_id}를 올릴 수 없습니다(필요 약 {p.get('need_mb')}MB).")
            try:
                path = local_path(model_id)
                # GPU마다 모델 하나 + 전용 스레드 묶음. 한 스레드가 여러 GPU의 모델을 번갈아 부르면
                # CTranslate2가 "CUDA failed with error invalid argument"로 실패한다(이 PC 실측).
                kw = dict(compute_type=p["compute_type"], num_workers=p["workers"])
                models = [WhisperModel(path, device="cuda", device_index=i, **kw) for i in p["gpus"]] or \
                         [WhisperModel(path, device="cpu", **kw)]
                workers = [[m, 0, ThreadPoolExecutor(p["workers"])] for m in models]
                # 1초 무음으로 한 번 돌려 본다: CUDA 라이브러리 누락(cublas64_12.dll 등)과 첫 실행 지연을 여기서 치른다.
                for w in workers:
                    w[2].submit(lambda m=w[0]: list(m.transcribe(
                        numpy.zeros(16000, numpy.float32), language="ko", vad_filter=False)[0])).result()
                current.update(id=model_id, plan=p, workers=workers)
                rtf.pop(model_id, None)
                log.info("모델 올림 %s %s %s", model_id, p["device"], p["compute_type"])
                return current
            except Exception as e:
                if p["device"] == "cpu" or isinstance(e, HTTPException):
                    raise
                if not is_oom(e):
                    if not CPU_FALLBACK:
                        raise
                    log.error("GPU를 쓸 수 없어 CPU로 바꿉니다: %s", e)
                    gpu_broken.append(str(e)[:200])
                    continue
                if FORCED_CT:  # 정밀도를 고정했으면 낮추지 않는다
                    raise HTTPException(503, f"GPU 메모리가 부족해 {model_id}를 {FORCED_CT}로 올리지 못했습니다.") from e
                log.warning("올리다 메모리 부족 %s %s %s → 낮춰 다시 시도", model_id, p["device"], p["compute_type"])
                levels[model_id] = levels.get(model_id, 0) + 1


def known(model_id: str) -> str:
    if model_id not in PRESETS:
        raise HTTPException(404, f"프리셋에 없는 모델입니다: {model_id}")
    return model_id


@contextlib.asynccontextmanager
async def lifespan(_app):
    threading.Thread(target=fill_sizes, daemon=True).start()
    g = gpus()
    log.info("장비: GPU %s, RAM 여유 %dMB", [f"{x['index']}:{x['name']} {x['free_mb']}/{x['total_mb']}MB" for x in g] or "없음", ram_free_mb())
    yield


app = FastAPI(title="udally-stt", dependencies=[Depends(auth)], lifespan=lifespan)


@app.get("/v1/models")
def list_models():
    hw = (gpus(), ram_free_mb())
    return {"object": "list", "data": [status_of(m, hw) for m in PRESETS]}


@app.get("/v1/models/{model_id:path}")
def get_one(model_id: str):
    return status_of(known(model_id))


@app.post("/v1/models/{model_id:path}")
def start_download(model_id: str):
    with lock:
        item = status_of(known(model_id))
        if item["fits"] is False:  # 이 장비에서 못 도는 모델은 받지 않는다
            return JSONResponse({**item, "detail": "이 장비의 메모리로는 이 모델을 돌릴 수 없습니다."}, status_code=409)
        if item["status"] == "not_downloaded":
            downloading.add(model_id)
            errors.pop(model_id, None)
            threading.Thread(target=fetch, args=(model_id,), daemon=True).start()
            item = status_of(model_id)
        elif item["status"] == "ready" and current.get("id") != model_id:
            threading.Thread(target=warm, args=(model_id,), daemon=True).start()
    return JSONResponse(item, status_code=200 if item["status"] == "ready" else 202)


def decode(w, model_id: str, data, language, prompt, cancel: threading.Event) -> str:
    if cancel.is_set():  # 차례를 기다리는 동안 끊긴 요청
        return ""
    start = time.monotonic()
    segments, info = w[0].transcribe(data, language=language or None, initial_prompt=prompt or None, vad_filter=True)
    parts = []
    for s in segments:  # 구간마다 취소를 확인한다(구간 하나 = 최대 30초 창)
        if cancel.is_set():
            log.info("요청 취소로 중단 %s", model_id)
            return ""
        parts.append(s.text)
    if info.duration > 0:
        r = (time.monotonic() - start) / info.duration
        rtf[model_id] = r if model_id not in rtf else 0.7 * rtf[model_id] + 0.3 * r
    return "".join(parts).strip()


def run(model_id: str, data, language, prompt, cancel: threading.Event) -> str:
    cur = load(model_id)
    with lock:  # 진행 중 요청이 가장 적은 GPU로
        w = min(cur["workers"], key=lambda x: x[1])
        w[1] += 1
    try:
        if cancel.is_set():
            return ""
        return w[2].submit(decode, w, model_id, data, language, prompt, cancel).result()
    except Exception as e:
        if not is_oom(e):
            raise
        log.error("전사 중 메모리 부족 %s %s → 다음 요청은 낮은 정밀도", model_id, cur["plan"]["device"])
        with load_lock:
            levels[model_id] = levels.get(model_id, 0) + 1
            unload()
        raise HTTPException(503, "전사 중 메모리가 부족했습니다. 다음 요청은 낮은 정밀도로 처리합니다.") from e
    finally:
        with lock:
            w[1] -= 1


@app.post("/v1/audio/transcriptions")
async def transcribe(
    request: Request,
    file: UploadFile = File(...),
    model: str = Form(...),
    language: str | None = Form(None),
    prompt: str | None = Form(None),
    response_format: str = Form("json"),
):
    if response_format not in ("json", "text"):
        raise HTTPException(400, "response_format은 json 또는 text만 지원합니다.")
    st = status_of(known(model))
    if st["status"] != "ready":
        # 409: 유달리는 4xx를 재시도하지 않고 실패로 기록한다. 받기는 설정 화면 [적용]이 시작한다.
        what = "받는 중" if st["status"] == "downloading" else "받지 않음"
        raise HTTPException(409, f"모델이 준비되지 않았습니다({what}): {model}. POST /v1/models/{{id}}로 받은 뒤 다시 요청해 주세요.")
    cancel = threading.Event()

    async def watch():
        while not cancel.is_set():
            if await request.is_disconnected():
                cancel.set()
            await asyncio.sleep(0.5)

    watcher = asyncio.create_task(watch())
    try:
        text = await run_in_threadpool(run, model, file.file, language, prompt, cancel)
    finally:
        cancel.set()
        watcher.cancel()
    return PlainTextResponse(text) if response_format == "text" else {"text": text}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=env("STT_HOST", "127.0.0.1"), port=int(env("STT_PORT", "8000")))
