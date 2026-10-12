# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""장치별 실행부. server.py는 이 모듈의 Backend만 본다.

- CT2: faster-whisper(CTranslate2). NVIDIA CUDA · AMD ROCm(HIP 빌드 휠, device="cuda" 그대로) · CPU.
- Nemotron: PyTorch·Transformers(NVIDIA 전용).
- OpenVINO: OpenVINO GenAI WhisperPipeline(Intel GPU, 없으면 OpenVINO CPU).
새 장치(예: whisper.cpp Vulkan)는 같은 칸을 가진 Backend를 하나 더 만들어 pick()에 넣는다.

Backend 칸: name · prefix(/v1/models device 접두어) · gpu_cts(높은 정밀도부터) · cpu_ct · gpu_error(GPU 고장으로 볼 오류) ·
experimental · no_unload(모델 해제 대신 프로세스 재시작) · gpus() · supports(id) · repo(id, ct) · files(id) ·
weights({파일: 바이트}, id, ct) → fp16 기준 MB · open(path, index|None, ct, workers) → .transcribe(data, language, initial_prompt, vad_filter)
"""

import contextlib
import ctypes
import glob
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy

env = os.environ.get
NEMOTRON = "nvidia/nemotron-3.5-asr-streaming-0.6b"
GPU_FILTER = {int(i) for i in env("STT_GPUS", "").split(",") if i.strip()}  # 비우면 전부
OOM = re.compile(r"out of (gpu |device )?memory|hipErrorOutOfMemory|CL_OUT_OF_RESOURCES|MEM_OBJECT_ALLOCATION", re.I)
CT2_FILES = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]
# Transformers 오프라인 전사에 필요한 파일만(.nemo·gguf 사본은 받지 않는다)
NEMOTRON_FILES = ["config.json", "generation_config.json", "processor_config.json", "tokenizer*.json", "model.safetensors"]
# 프리셋 id(CT2 변환본) → OpenVINO 공식 IR 저장소 앞부분. 정밀도마다 저장소가 따로 있다(-fp16-ov·-int8-ov·-int4-ov).
OV_REPOS = {"Systran/faster-whisper-small": "OpenVINO/whisper-small",
            "Systran/faster-whisper-medium": "OpenVINO/whisper-medium",
            "deepdml/faster-whisper-large-v3-turbo-ct2": "OpenVINO/whisper-large-v3-turbo",
            "Systran/faster-whisper-large-v3": "OpenVINO/whisper-large-v3"}
OV_SUFFIX = {"float16": "fp16", "int8": "int8", "int4": "int4"}


def nvidia_gpus() -> list[dict]:
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        return []
    rows = [[c.strip() for c in line.split(",")] for line in out.strip().splitlines() if line.strip()]
    return [{"index": int(r[0]), "name": r[1], "total_mb": int(r[2]), "free_mb": int(r[3])} for r in rows if len(r) == 4]


ROCM_VERSION = env("STT_ROCM_VERSION", "7.13.0")  # AMD pip 색인(repo.amd.com/rocm/whl/<gfx 계열>/)의 rocm 버전
# CT2 ROCm 휠 대상(CTranslate2 python/tools/prepare_build_environment_linux_rocm.sh) → AMD pip 색인 이름
GFX_INDEX = [(r"gfx103\d", "gfx103X-all"), (r"gfx110\d", "gfx110X-all"), (r"gfx1150", "gfx1150"),
             (r"gfx1151", "gfx1151"), (r"gfx120\d", "gfx120X-all")]


def gfx_targets(kfd="/sys/class/kfd/kfd/topology/nodes") -> list[str]:
    """Linux: kfd 토폴로지의 gfx_target_version(예 110001) → "gfx1101"."""
    out = []
    for f in glob.glob(f"{kfd}/*/properties"):
        m = re.search(r"gfx_target_version (\d+)", Path(f).read_text())
        if m and int(m.group(1)):
            v = int(m.group(1))
            out.append(f"gfx{v // 10000}{(v // 100) % 100:x}{v % 100:x}")
    return out


def gfx_index(targets) -> str | None:
    for t in targets:
        for pat, name in GFX_INDEX:
            if re.fullmatch(pat, t):
                return name
    return None


def rocm_lib_dirs(roots) -> list[str]:
    return sorted({str(Path(f).parent) for r in roots for f in glob.glob(f"{r}/_rocm_sdk_*/**/libamdhip64.so*", recursive=True)})


def ensure_rocm(cache: str) -> None:
    """Linux: ROCm 런타임(AMD pip `rocm[libraries]`)을 찾아 LD_LIBRARY_PATH에 넣고 다시 띄운다.
    이미지에는 ROCm을 넣지 않는다(재배포 조건이 구성요소마다 달라 사용자 PC가 AMD 색인에서 직접 받는다).
    STT_ROCM_INSTALL=1(rocm 이미지 기본)이면 처음 한 번 `cache`/rocm-<버전>에 받는다. Windows는 CT2 휠이 DLL 경로를 스스로 등록한다."""
    if sys.platform == "win32" or _hip():
        return
    target = Path(cache, f"rocm-{ROCM_VERSION}")
    dirs = rocm_lib_dirs([*sys.path, str(target)])
    if not dirs and env("STT_ROCM_INSTALL") == "1":
        if not gfx_targets():
            raise RuntimeError("AMD GPU(/dev/kfd)를 찾지 못했습니다. Docker는 --device /dev/kfd --device /dev/dri로 실행해 주세요.")
        idx = gfx_index(gfx_targets())
        if not idx:
            raise RuntimeError(f"CTranslate2 ROCm 휠이 지원하지 않는 AMD GPU입니다: {gfx_targets()}")
        print(f"ROCm {ROCM_VERSION}({idx})를 AMD 색인에서 처음 한 번 받습니다(수백 MB)…", flush=True)
        subprocess.run([sys.executable, "-m", "pip", "install", "--no-cache-dir", "--target", str(target),
                        "--index-url", f"https://repo.amd.com/rocm/whl/{idx}/", f"rocm[libraries]=={ROCM_VERSION}"], check=True)
        dirs = rocm_lib_dirs([str(target)])
    if not dirs or env("UDALLY_ROCM_REEXEC"):  # 다시 띄웠는데도 못 읽으면 반복하지 않는다
        raise RuntimeError("ROCm 런타임(libamdhip64)을 찾지 못했습니다. README의 AMD 설치를 따라 주세요.")
    os.environ["UDALLY_ROCM_REEXEC"] = "1"
    os.environ["LD_LIBRARY_PATH"] = os.pathsep.join([*dirs, env("LD_LIBRARY_PATH", "")]).rstrip(os.pathsep)
    os.execv(sys.executable, [sys.executable, *sys.argv])  # 로더는 시작할 때만 LD_LIBRARY_PATH를 읽는다


def _hip():
    names = ["amdhip64_7.dll", "amdhip64_6.dll", "amdhip64.dll"] if sys.platform == "win32" else ["libamdhip64.so", "libamdhip64.so.7", "libamdhip64.so.6"]
    for n in names:
        try:
            return ctypes.CDLL(n)
        except OSError:
            continue
    return None


def amd_gpus(hip=None, sysfs="/sys/class/drm") -> list[dict]:
    """HIP 런타임(hipMemGetInfo)으로 읽고, 없으면 Linux sysfs(amdgpu mem_info_vram_*)로 읽는다."""
    if sys.platform == "win32":
        with contextlib.suppress(Exception):
            import ctranslate2  # noqa: F401 — ROCm 휠이 _rocm_sdk_core DLL 폴더를 등록한다
    hip = hip or _hip()
    if hip:
        try:
            n = ctypes.c_int()
            if hip.hipGetDeviceCount(ctypes.byref(n)) == 0:
                out = []
                for i in range(n.value):
                    free, total = ctypes.c_size_t(), ctypes.c_size_t()
                    if hip.hipSetDevice(i) == 0 and hip.hipMemGetInfo(ctypes.byref(free), ctypes.byref(total)) == 0:
                        out.append({"index": i, "name": "AMD GPU", "total_mb": total.value // 2**20, "free_mb": free.value // 2**20})
                return out
        except Exception:
            pass
    out = []
    # ponytail: HIP 장치 번호 = sysfs card 순서(AMD만)라고 본다. 다른 회사 GPU가 섞여 순서가 다르면 ROCR_VISIBLE_DEVICES로 맞춘다.
    for card in sorted(glob.glob(f"{sysfs}/card[0-9]*/device"), key=lambda p: int(re.search(r"card(\d+)", p).group(1))):
        try:
            if Path(card, "vendor").read_text().strip() != "0x1002":
                continue
            total = int(Path(card, "mem_info_vram_total").read_text()) // 2**20
            used = int(Path(card, "mem_info_vram_used").read_text()) // 2**20
        except (OSError, ValueError):
            continue
        name = Path(card, "product_name").read_text().strip() if Path(card, "product_name").exists() else "AMD GPU"
        out.append({"index": len(out), "name": name, "total_mb": total, "free_mb": total - used})
    return out


class CT2:
    """faster-whisper. kind = cuda(NVIDIA) | rocm(AMD, 실험) | cpu."""

    gpu_cts = ["float16", "int8_float16", "int8"]  # 메모리가 모자라면 이 순서로 낮춘다
    cpu_ct = "int8"

    def __init__(self, kind: str):
        self.name = self.prefix = kind
        self.gpu_error = r"hip|rocm|hipblas|rocblas|miopen" if kind == "rocm" else r"cuda|cublas|cudnn|nvrtc"
        # ROCm: 모델 해제·종료 때 멈추는 버그(CTranslate2 #2038·#2085) → 해제하지 않고 프로세스를 다시 띄운다.
        self.experimental = self.no_unload = kind == "rocm"

    def gpus(self):
        return {"cuda": nvidia_gpus, "rocm": amd_gpus}.get(self.name, list)()

    def supports(self, model_id):
        return model_id != NEMOTRON

    def repo(self, model_id, ct=None):
        return model_id

    def files(self, model_id):
        return CT2_FILES

    def weights(self, files, model_id, ct=None):
        return files["model.bin"] / 2**20

    def open(self, path, index, ct, workers):
        from faster_whisper import WhisperModel
        if index is None:
            return WhisperModel(path, device="cpu", compute_type=ct, num_workers=workers)
        return WhisperModel(path, device="cuda", device_index=index, compute_type=ct, num_workers=workers)


class Torch:
    """NVIDIA Nemotron 3.5 ASR(Transformers). GPU float16, CPU float32. int8 단계는 없다."""

    name, prefix, gpu_cts, cpu_ct = "nemotron", "cuda", ["float16"], "float32"
    gpu_error = r"cuda|cublas|cudnn|nvrtc"
    experimental = no_unload = False

    def gpus(self):
        return nvidia_gpus()

    def supports(self, model_id):
        return model_id == NEMOTRON

    def repo(self, model_id, ct=None):
        return model_id

    def files(self, model_id):
        return NEMOTRON_FILES

    def weights(self, files, model_id, ct=None):  # need_mb는 float16 저장 크기 기준이다. safetensors는 float32라 절반으로 본다.
        return files["model.safetensors"] / 2**20 / 2

    def open(self, path, index, ct, workers):
        return Nemotron(path, index, ct)


class Nemotron:
    """모델 카드의 Transformers "Offline transcription" 예시를 faster-whisper WhisperModel과 같은 모양으로 감싼다.
    prompt(용어집)를 넣을 자리가 없어 무시한다. VAD 없이 구간 전체를 한 번에 넣는다."""

    def __init__(self, path, index, compute_type):
        import torch
        from transformers import AutoModelForRNNT, AutoProcessor
        self.torch = torch
        self.processor = AutoProcessor.from_pretrained(path)
        dtype = torch.float16 if compute_type == "float16" else torch.float32
        dev = "cpu" if index is None else f"cuda:{index}"
        self.model = AutoModelForRNNT.from_pretrained(path, dtype=dtype).to(dev).eval()
        # generate()가 호출마다 모델 객체에 상태를 써서 동시에 부르면 AttributeError가 난다(실측). GPU당 한 번에 하나.
        self.lock = threading.Lock()

    def transcribe(self, data, language=None, initial_prompt=None, vad_filter=True):
        from faster_whisper.audio import decode_audio
        sr = self.processor.feature_extractor.sampling_rate
        audio = data if isinstance(data, numpy.ndarray) else decode_audio(data, sampling_rate=sr)
        inputs = self.processor(audio, sampling_rate=sr, language=language or "auto")
        inputs.to(self.model.device, dtype=self.model.dtype)
        with self.lock, self.torch.inference_mode():
            out = self.model.generate(**inputs, return_dict_in_generate=True)
        text = self.processor.decode(out.sequences, skip_special_tokens=True)
        text = " ".join((text[0] if isinstance(text, list) else text).split())
        return [SimpleNamespace(text=text)], SimpleNamespace(duration=len(audio) / sr)


class OpenVINO:
    """OpenVINO GenAI WhisperPipeline. 정밀도는 저장소 단위라 float16 → int8 → int4 → OpenVINO CPU(int8)로 낮춘다."""

    name, prefix, cpu_ct = "openvino", "openvino:GPU.", "int8"
    gpu_cts = ["float16", "int8", "int4"]
    gpu_error = r"GPU|OpenCL|level.?zero|clGetPlatformIDs"
    experimental = no_unload = False

    def gpus(self):
        try:
            import openvino as ov
            core = ov.Core()
            out = []
            for d in core.available_devices:
                if not d.startswith("GPU"):
                    continue
                total = int(core.get_property(d, "GPU_DEVICE_TOTAL_MEM_SIZE")) // 2**20
                integrated = str(core.get_property(d, "DEVICE_TYPE")).lower().endswith("integrated")
                # ponytail: OpenVINO는 남은 VRAM을 알려 주지 않는다. 외장은 전체의 90%, 내장(공유 메모리)은 RAM 여유로 본다.
                free = min(total, _ram_free_mb()) if integrated else int(total * 0.9)
                out.append({"index": int(d.split(".")[1]) if "." in d else 0,
                            "name": core.get_property(d, "FULL_DEVICE_NAME"), "total_mb": total, "free_mb": free})
            return out
        except Exception:
            return []

    def supports(self, model_id):
        return model_id in OV_REPOS

    def repo(self, model_id, ct="int8"):
        return f"{OV_REPOS[model_id]}-{OV_SUFFIX[ct]}-ov"

    def files(self, model_id):
        return ["*.json", "*.xml", "*.bin", "*.txt"]

    def weights(self, files, model_id, ct="int8"):  # 저장소 정밀도와 무관하게 float16 기준으로 맞춘다(need_mb 기준).
        w = [v for k, v in files.items() if k in ("openvino_encoder_model.bin", "openvino_decoder_model.bin")]
        if len(w) < 2:
            raise KeyError("openvino_*_model.bin")
        return sum(w) / 2**20 * {"float16": 1, "int8": 2, "int4": 4}[ct]

    def open(self, path, index, ct, workers):
        return OVWhisper(path, "CPU" if index is None else f"GPU.{index}")


class OVWhisper:
    """WhisperPipeline을 WhisperModel 모양으로. 긴 오디오는 faster-whisper VAD로 30초 이하 구간을 만들어 차례로 넣는다."""

    def __init__(self, path, device):
        import openvino_genai
        self.pipe = openvino_genai.WhisperPipeline(str(path), device)
        self.lock = threading.Lock()  # ponytail: 파이프라인 동시 호출 안전성 미확인 → 한 번에 하나

    def transcribe(self, data, language=None, initial_prompt=None, vad_filter=True):
        from faster_whisper.audio import decode_audio
        from faster_whisper.vad import VadOptions, get_speech_timestamps
        audio = data if isinstance(data, numpy.ndarray) else decode_audio(data, sampling_rate=16000)
        spans = get_speech_timestamps(audio, VadOptions(max_speech_duration_s=29)) if vad_filter else \
            [{"start": s, "end": min(s + 29 * 16000, len(audio))} for s in range(0, len(audio), 29 * 16000)]
        kw = {"task": "transcribe", **({"language": f"<|{language}|>"} if language else {}),
              **({"initial_prompt": initial_prompt} if initial_prompt else {})}

        def segments():
            for s in spans:
                with self.lock:
                    r = self.pipe.generate(audio[s["start"]:s["end"]].tolist(), **kw)
                yield SimpleNamespace(text=" " + " ".join(str(r).split()))

        return segments(), SimpleNamespace(duration=len(audio) / 16000)


def _ram_free_mb() -> int:
    import psutil
    return psutil.virtual_memory().available // 2**20


def has(*mods) -> bool:
    return all(importlib.util.find_spec(m) for m in mods)


def ct2_rocm() -> bool:
    """설치된 ctranslate2가 ROCm(HIP) 빌드인지. CUDA 빌드와 같은 이름의 휠이라 HIP 런타임을 링크했는지로 본다."""
    spec = importlib.util.find_spec("ctranslate2")
    if not spec or not spec.submodule_search_locations:
        return False
    root = Path(next(iter(spec.submodule_search_locations)))
    # 휠 __init__.py는 CUDA·ROCm 공통이라(_rocm_sdk 문자열이 둘 다 있다) 라이브러리가 HIP 런타임을 가져오는지로 본다.
    libs = [*root.glob("*ctranslate2*.so*"), *root.glob("ctranslate2.dll"), *root.parent.glob("ctranslate2.libs/libctranslate2*")]
    return any(b"amdhip64" in p.read_bytes() for p in libs if p.is_file())


def pick(device: str = env("STT_DEVICE", "auto"), nvidia=None, rocm=None, openvino=None) -> "CT2 | OpenVINO":
    """STT_DEVICE = auto | cuda | rocm | openvino | cpu. auto는 NVIDIA > AMD > Intel > CPU."""
    if device in ("cuda", "rocm", "cpu"):
        return CT2(device)
    if device == "openvino":
        return OpenVINO()
    nvidia = bool(nvidia_gpus()) if nvidia is None else nvidia
    if nvidia:
        return CT2("cuda")
    rocm = ct2_rocm() if rocm is None else rocm
    if rocm:
        return CT2("rocm")
    openvino = (has("openvino_genai") and bool(OpenVINO().gpus())) if openvino is None else openvino
    return OpenVINO() if openvino else CT2("cpu")


def filtered(gpus: list[dict]) -> list[dict]:
    return [g for g in gpus if not GPU_FILTER or g["index"] in GPU_FILTER]
