# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = [
#   "faster-whisper>=1.1,<2",
#   "av<17",
#   "fastapi>=0.110",
#   "uvicorn>=0.29",
#   "python-multipart>=0.0.9",
#   "psutil>=5.9",
# ]
# ///
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# av<17: faster-whisper 1.2.1이 av.open(metadata_errors=)를 써서 av 19에서 깨진다(확인: 2026-10-12).
# 의존성 목록은 pyproject.toml과 같아야 한다(test_plan.py가 검사).
"""유달리용 로컬 전사 서버(OpenAI 호환 + 모델 받기·장비 적합 상태).

실행: uv run server.py (HTTP 서버) · uv run server.py connect (유달리에 내 PC로 연결, 포트를 열지 않음)
디코딩·VAD·정밀도·GPU 병렬은 faster-whisper(CTranslate2), 받기·캐시는 huggingface_hub를 그대로 쓴다.
장치별 실행부는 backends.py. 이 파일은 그 위에 HTTP 규약, 메모리 계획, OOM 강등만 얹는다. 환경변수는 README 참고.
"""

import asyncio
import contextlib
import fnmatch
import logging
import math
import os
import re
import secrets
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy
import psutil
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, PlainTextResponse

import backends
from backends import NEMOTRON, OOM

VERSION = "0.1.0"
env = backends.env
# Nemotron은 PyTorch·Transformers가 있는 이미지(udally-link:nemotron)에서만 목록에 넣는다.
HAS_TORCH = backends.has("torch", "transformers")
DEVICE = env("LINK_DEVICE", "auto")  # auto | cuda | rocm | openvino | cpu
BACKEND = backends.pick(DEVICE)  # Whisper 프리셋을 돌릴 실행부
if BACKEND.name == "rocm":
    backends.ensure_rocm(env("HF_HOME") or str(Path.home() / ".cache" / "udally-link"))
TORCH = backends.Torch()
PRESETS = [m for m in (x.strip() for x in env(
    "LINK_MODELS",
    "Systran/faster-whisper-small,Systran/faster-whisper-medium,"
    f"deepdml/faster-whisper-large-v3-turbo-ct2,Systran/faster-whisper-large-v3,{NEMOTRON}",
).split(",")) if m and (TORCH.supports(m) and HAS_TORCH and BACKEND.name in ("cuda", "cpu") or BACKEND.supports(m))]
API_KEY = env("LINK_API_KEY") or None
FORCED_CT = env("LINK_COMPUTE_TYPE") or None
CPU_FALLBACK = env("LINK_CPU_FALLBACK", "1") == "1"
CONCURRENCY = int(env("LINK_CONCURRENCY", "3"))
GPU_CTS = backends.CT2.gpu_cts
log = logging.getLogger("udally-link")
_handlers = [logging.StreamHandler()]
if _log_file := env("LINK_LOG_FILE"):  # 창 없이 띄울 때(Windows 시작프로그램) 로그를 볼 곳. 5MB×2개로 돌린다
    from logging.handlers import RotatingFileHandler

    os.makedirs(os.path.dirname(_log_file) or ".", exist_ok=True)
    _handlers.append(RotatingFileHandler(_log_file, maxBytes=5_000_000, backupCount=1, encoding="utf-8"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=_handlers)


def runtime(model_id: str):
    return TORCH if TORCH.supports(model_id) else BACKEND


# ---------- 메모리 계획(순수 함수: test_plan.py가 검사) ----------
def need_mb(weights_mb: float, ct: str, conc: int, cpu: bool = False) -> int:
    """필요 메모리 추정. weights_mb = model.bin 크기(fp16 저장 기준).
    근거(README 표): 이 PC 실측 turbo fp16 로드 +2.0GB·3동시 +0.35GB, int8_float16 +1.05GB/+0.22GB,
    faster-whisper 벤치 large-v2 fp16 4.5GB·small CPU int8 1.5GB."""
    w = weights_mb * {"float32": 2, "float16": 1, "bfloat16": 1, "int4": 0.25}.get(ct, 0.5)
    return round(w * 1.25 + 150 * conc + (800 if cpu else 300))


def plan(weights_mb, gpus, ram_free_mb, level=0, device=DEVICE, forced=FORCED_CT,
         cpu_fallback=CPU_FALLBACK, conc=CONCURRENCY, gpu_cts=GPU_CTS, cpu_ct="int8", prefix="cuda") -> dict:
    """gpus: [{"index", "free_mb"}]. level: OOM으로 강등된 단계(0=float16부터). prefix: device 표시 접두어(cuda·rocm·openvino:GPU.)."""
    if weights_mb is None:
        return {"fits": None, "device": None, "compute_type": None}
    sep = "" if prefix.endswith(".") else ":"
    if device != "cpu" and gpus:
        for ct in [forced] if forced else gpu_cts[level:]:
            per_gpu = math.ceil(conc / len(gpus))
            ok = [g["index"] for g in gpus if g["free_mb"] >= need_mb(weights_mb, ct, per_gpu)]
            if ok:
                per_gpu = math.ceil(conc / len(ok))
                return {"fits": True, "device": prefix + sep + ",".join(map(str, ok)), "compute_type": ct,
                        "gpus": ok, "workers": per_gpu, "need_mb": need_mb(weights_mb, ct, per_gpu)}
    if device != "cpu" and gpus and not cpu_fallback:  # GPU가 있는데 모자라면 CPU로 내리지 않는다
        return {"fits": False, "device": prefix.rstrip(":."), "compute_type": None}
    ct = forced if forced in ("int8", "float32") else cpu_ct
    need = need_mb(weights_mb, ct, conc, cpu=True)
    return {"fits": need <= ram_free_mb, "device": "cpu", "compute_type": ct, "gpus": [],
            "workers": conc, "need_mb": need}


# ---------- 장비 ----------
gpu_broken: list[str] = []  # GPU 실행이 OOM 말고 다른 이유로 실패하면(라이브러리 누락 등) 이후 CPU만 쓴다


def gpus(rt=None) -> list[dict]:
    rt = rt or BACKEND
    if DEVICE == "cpu" or gpu_broken or rt.name == "cpu":
        return []
    return backends.filtered(rt.gpus())


def ram_free_mb() -> int:
    return psutil.virtual_memory().available // 2**20


# ---------- 상태 ----------
lock = threading.Lock()
sizes: dict[str, int] = {}  # 모델 → 받을 파일 전체 바이트
weights: dict[str, float] = {}  # 모델 → 가중치 MB(fp16 기준)
downloading: dict[str, str | None] = {}  # 모델 → 받는 정밀도(저장소가 정밀도마다 다른 실행부만 의미가 있다)
errors: dict[str, str] = {}
levels: dict[str, int] = {}  # OOM 강등 단계
rtf: dict[str, float] = {}  # 처리시간/오디오길이 이동평균
current: dict = {}  # ponytail: 한 번에 한 모델만 올린다 {"id", "plan", "workers": [[모델, 진행 수, 스레드 묶음]]}. 번갈아 쓰면 바꿀 때마다 다시 올린다.
load_lock = threading.Lock()


def auth(authorization: str | None = Header(None)) -> None:
    if API_KEY and not secrets.compare_digest(authorization or "", f"Bearer {API_KEY}"):
        raise HTTPException(401, "API 키가 맞지 않습니다(Authorization: Bearer <키>).")


def cts_of(model_id: str, ct=None) -> list:
    """찾아볼 정밀도. 저장소가 하나면(CT2·Torch) [None] 하나다."""
    rt = runtime(model_id)
    if rt.repo(model_id, rt.cpu_ct) == rt.repo(model_id, rt.gpu_cts[0]):
        return [None]
    return [ct] if ct else list(dict.fromkeys([*rt.gpu_cts, rt.cpu_ct]))


def download(model_id: str, ct=None, local_files_only: bool = False) -> str:
    from huggingface_hub import snapshot_download
    rt = runtime(model_id)
    p = snapshot_download(rt.repo(model_id, ct or rt.cpu_ct), allow_patterns=rt.files(model_id), local_files_only=local_files_only)
    rt.weights(file_sizes(p), model_id, ct or rt.cpu_ct)  # 가중치 파일이 없으면 여기서 실패한다(받다 끊긴 경우)
    return p


def file_sizes(path) -> dict:
    return {f.name: f.stat().st_size for f in Path(path).iterdir() if f.is_file()}


def local(model_id: str, ct=None) -> tuple | None:
    """받아 둔 (정밀도, 경로). 저장소가 정밀도마다 다르면 높은 정밀도부터 찾는다."""
    for c in cts_of(model_id, ct):
        try:
            return c, download(model_id, c, local_files_only=True)
        except Exception:
            continue
    return None


def local_path(model_id: str, ct=None) -> str | None:
    hit = local(model_id, ct)
    return hit[1] if hit else None


def plan_for(model_id: str, gpus_, ram, level=0) -> dict:
    rt = runtime(model_id)
    gpu_cts, cpu_ct = rt.gpu_cts, rt.cpu_ct
    if cts_of(model_id) != [None]:  # 정밀도마다 저장소가 다르면 받아 둔 정밀도 안에서만 고른다(받기 전이면 전부)
        have = [c for c in gpu_cts if local(model_id, c)]
        if have:
            gpu_cts, cpu_ct = have, have[-1]
    forced = FORCED_CT if rt is BACKEND and isinstance(rt, backends.CT2) else None
    return plan(weights.get(model_id), gpus_, ram, level, forced=forced, gpu_cts=gpu_cts, cpu_ct=cpu_ct, prefix=rt.prefix)


def fill_sizes() -> None:
    """프리셋 크기를 한 번 알아 둔다(받은 모델은 디스크, 아니면 Hugging Face API)."""
    from huggingface_hub import HfApi
    for m in PRESETS:
        rt = runtime(m)
        try:
            hit = local(m)
            if hit and m not in sizes:
                weights[m] = rt.weights(file_sizes(hit[1]), m, hit[0] or rt.cpu_ct)
                continue
            info = HfApi().model_info(rt.repo(m, rt.cpu_ct), files_metadata=True)
            files = {s.rfilename: s.size or 0 for s in info.siblings
                     if any(fnmatch.fnmatch(s.rfilename, pat) for pat in rt.files(m))}
            sizes[m] = sum(files.values())
            weights[m] = rt.weights(files, m, rt.cpu_ct)
        except Exception as e:
            log.warning("크기 확인 실패 %s: %s", m, e)


def blobs_size(repo: str) -> int:
    from huggingface_hub import constants
    d = Path(constants.HF_HUB_CACHE) / f"models--{repo.replace('/', '--')}" / "blobs"
    return sum(f.stat().st_size for f in d.glob("*") if f.is_file()) if d.is_dir() else 0


def meta(model_id: str) -> dict:
    """사람이 읽는 표시 정보. id는 프리셋 id(CTranslate2 변환본 저장소), base_model은 원본. 실행부가 다른 저장소를 받아도 id는 그대로다."""
    fmt = {"openvino": "OpenVINO"}.get(BACKEND.name, "CTranslate2")
    if model_id == NEMOTRON:
        return {"owned_by": "nvidia", "family": "Nemotron", "display_name": "NVIDIA Nemotron 3.5 ASR 0.6B",
                "base_model": NEMOTRON, "source": NEMOTRON, "format": "Transformers"}
    m = re.search(r"whisper-(tiny|base|small|medium|large-v3-turbo|large-v3|large-v2|large)", model_id.lower())
    if not m:  # ponytail: 프리셋은 전부 Whisper 변환본이다. 다른 계열을 넣으면 여기에 규칙을 더한다.
        return {"owned_by": model_id.split("/")[0], "family": None, "display_name": model_id, "source": model_id}
    return {"owned_by": "openai", "family": "Whisper", "display_name": f"OpenAI Whisper {m.group(1)}",
            "base_model": f"openai/whisper-{m.group(1)}", "source": runtime(model_id).repo(model_id, runtime(model_id).cpu_ct),
            "format": fmt}


def status_of(model_id: str, hw=None) -> dict:
    item = {"id": model_id, "object": "model", **meta(model_id)}
    rt = runtime(model_id)
    if model_id in downloading:
        item["status"] = "downloading"
        if sizes.get(model_id):
            item["progress"] = round(min(blobs_size(rt.repo(model_id, downloading[model_id] or rt.cpu_ct)) / sizes[model_id], 0.99), 3)
    else:
        item["status"] = "ready" if local(model_id) else "not_downloaded"
        if model_id in errors and item["status"] == "not_downloaded":
            item["error"] = errors[model_id]
    if current.get("id") == model_id:  # 올라가 있으면 실제 배치를 보여 준다
        p = current["plan"]
    else:
        g, ram = hw if hw and rt is BACKEND else (gpus(rt), ram_free_mb())
        p = plan_for(model_id, g, ram, levels.get(model_id, 0))
    item.update(fits=p["fits"], device=p["device"], compute_type=p["compute_type"])
    if item["status"] == "ready":
        item["rtf"] = round(rtf[model_id], 3) if model_id in rtf else None
    return item


def fetch(model_id: str, ct=None) -> None:
    rt = runtime(model_id)
    try:
        p = download(model_id, ct)
        weights[model_id] = rt.weights(file_sizes(p), model_id, ct or rt.cpu_ct)
    except Exception as e:
        errors[model_id] = f"{type(e).__name__}: {e}"[:300]
        return
    finally:
        downloading.pop(model_id, None)
    warm(model_id)


def warm(model_id: str) -> None:
    """[적용]된 모델을 미리 올려 첫 전사 요청이 올리기·CUDA 첫 실행 시간(이 PC 최대 약 30초)을 치르지 않게 한다."""
    try:
        load(model_id)
    except Exception as e:
        log.warning("미리 올리기 실패 %s: %s", model_id, e)


def is_oom(e: Exception) -> bool:
    return bool(OOM.search(str(e))) or isinstance(e, MemoryError)


def unload() -> None:
    for w in current.get("workers", []):
        w[2].shutdown(wait=False)
    current.clear()


def restart() -> None:
    """모델 해제가 위험한 실행부(ROCm)에서 다른 모델로 바꿀 때: 같은 명령으로 프로세스를 다시 띄운다.
    connect 모드는 다시 뜬 뒤 첫 heartbeat(job_id 없음)에 core가 작업을 되돌려 다시 준다(IMPL-PLAN-11 §7.5)."""
    log.warning("%s: 모델을 바꾸려고 프로세스를 다시 시작합니다(해제 시 멈춤 버그 회피).", BACKEND.name)
    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable, "-m", "server", *sys.argv[1:]])


def load(model_id: str) -> dict:
    """모델을 올린다. 올리다 OOM이면 정밀도를 낮춰(끝내 CPU로) 다시 시도한다."""
    rt = runtime(model_id)
    with load_lock:
        if current.get("id") == model_id:
            return current
        if current and BACKEND.no_unload:
            restart()
        unload()
        while True:
            weights.setdefault(model_id, 0)
            p = plan_for(model_id, gpus(rt), ram_free_mb(), levels.get(model_id, 0))
            if not p["fits"]:
                raise HTTPException(503, f"이 장비의 메모리로는 {model_id}를 올릴 수 없습니다(필요 약 {p.get('need_mb')}MB).")
            try:
                path = local_path(model_id, p["compute_type"] if cts_of(model_id) != [None] else None)
                if not path:
                    raise HTTPException(409, f"모델이 준비되지 않았습니다: {model_id}")
                # GPU마다 모델 하나 + 전용 스레드 묶음. 한 스레드가 여러 GPU의 모델을 번갈아 부르면
                # CTranslate2가 "CUDA failed with error invalid argument"로 실패한다(이 PC 실측).
                models = [rt.open(path, i, p["compute_type"], p["workers"]) for i in p["gpus"]] or \
                         [rt.open(path, None, p["compute_type"], p["workers"])]
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
                    # GPU 라이브러리 문제만 GPU 고장으로 본다(의존성 누락 등은 그대로 실패시킨다).
                    if not CPU_FALLBACK or not re.search(rt.gpu_error, str(e), re.I):
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


def models_data() -> list[dict]:
    hw = (gpus(), ram_free_mb())
    return [status_of(m, hw) for m in PRESETS]


def pull(model_id: str) -> tuple[int, dict]:
    """[모델 적용]: 받지 않았으면 받기 시작, 받았으면 미리 올린다. (HTTP 상태, 항목) — HTTP와 connect 모드가 같이 쓴다."""
    with lock:
        item = status_of(known(model_id))
        if item["fits"] is False:  # 이 장비에서 못 도는 모델은 받지 않는다
            return 409, {**item, "detail": "이 장비의 메모리로는 이 모델을 돌릴 수 없습니다."}
        if item["status"] == "not_downloaded":
            ct = item["compute_type"] if cts_of(model_id) != [None] else None
            downloading[model_id] = ct
            errors.pop(model_id, None)
            threading.Thread(target=fetch, args=(model_id, ct), daemon=True).start()
            item = status_of(model_id)
        elif item["status"] == "ready" and current.get("id") != model_id:
            threading.Thread(target=warm, args=(model_id,), daemon=True).start()
    return (200 if item["status"] == "ready" else 202), item


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
            if not BACKEND.no_unload:
                unload()
        raise HTTPException(503, "전사 중 메모리가 부족했습니다. 다음 요청은 낮은 정밀도로 처리합니다.") from e
    finally:
        with lock:
            w[1] -= 1


def start() -> None:
    """HTTP·connect 공통 기동: 크기 조회 스레드 + 장비 기록."""
    threading.Thread(target=fill_sizes, daemon=True).start()
    g = gpus()
    log.info("실행부 %s%s, GPU %s, RAM 여유 %dMB", BACKEND.name, "(실험)" if BACKEND.experimental else "",
             [f"{x['index']}:{x['name']} {x['free_mb']}/{x['total_mb']}MB" for x in g] or "없음", ram_free_mb())


@contextlib.asynccontextmanager
async def lifespan(_app):
    start()
    yield


app = FastAPI(title="udally-link", dependencies=[Depends(auth)], lifespan=lifespan)


@app.get("/v1/models")
def list_models():
    return {"object": "list", "data": models_data()}


@app.get("/v1/models/{model_id:path}")
def get_one(model_id: str):
    return status_of(known(model_id))


@app.post("/v1/models/{model_id:path}")
def start_download(model_id: str):
    code, item = pull(model_id)
    return JSONResponse(item, status_code=code)


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


def cli(argv=None) -> None:
    """udally-link (HTTP 서버) · udally-link connect [--until-linked] (유달리 연결) · udally-link unlink (스스로 끊기)."""
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] in (["connect"], ["unlink"]):
        import link
        sys.exit(link.main(argv[1:] if argv[0] == "connect" else ["unlink"]))
    import uvicorn
    uvicorn.run(app, host=env("LINK_HOST", "127.0.0.1"), port=int(env("LINK_PORT", "8000")))


if __name__ == "__main__":
    sys.modules.setdefault("server", sys.modules[__name__])  # link.py가 `import server`로 같은 상태를 보게
    cli()
