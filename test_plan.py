# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""장비 4경우 계획 검사 + 장치 판별(가짜 장치) + 의존성 목록 일치.
실행: uv run --no-project --with fastapi,psutil,python-multipart,numpy python test_plan.py"""

import os
import re
import tempfile
import tomllib
from pathlib import Path

os.environ["STT_DEVICE"] = "cpu"
import backends  # noqa: E402
from server import plan  # noqa: E402

TURBO, LARGE = 1545, 2950  # model.bin MB(fp16)
g = lambda i, free: {"index": i, "free_mb": free}  # noqa: E731

# ① GPU 여러 개: 둘 다 들어가면 둘 다 쓴다, 하나만 들어가면 그것만
p = plan(LARGE, [g(0, 13000), g(1, 11000)], 30000, device="auto", forced=None)
assert (p["device"], p["compute_type"], p["workers"]) == ("cuda:0,1", "float16", 2), p
p = plan(LARGE, [g(0, 13000), g(1, 3000)], 30000, device="auto", forced=None)
assert (p["device"], p["compute_type"], p["workers"]) == ("cuda:0", "float16", 3), p
# ② GPU 1개·넉넉
assert plan(TURBO, [g(0, 8000)], 8000, device="auto", forced=None)["compute_type"] == "float16"
# ③ GPU 1개·부족: float16 → int8_float16 → CPU 폴백 / 폴백 끄면 거절
assert plan(LARGE, [g(0, 4000)], 30000, device="auto", forced=None)["compute_type"] == "int8_float16"
p = plan(LARGE, [g(0, 1500)], 30000, device="auto", forced=None, cpu_fallback=True)
assert (p["device"], p["fits"]) == ("cpu", True), p
assert plan(LARGE, [g(0, 1500)], 30000, device="auto", forced=None, cpu_fallback=False)["fits"] is False
# OOM 강등 단계는 다음 정밀도부터
assert plan(TURBO, [g(0, 8000)], 8000, level=1, device="auto", forced=None)["compute_type"] == "int8_float16"
# ④ GPU 없음: CPU int8, RAM이 모자라면 fits=False
assert plan(TURBO, [], 8000, device="auto", forced=None)["device"] == "cpu"
assert plan(LARGE, [], 1000, device="auto", forced=None)["fits"] is False
# 크기를 아직 모르면 판단 보류
assert plan(None, [], 8000)["fits"] is None

# 접두어: AMD rocm:0, Intel openvino:GPU.0(int4 단계)
assert plan(TURBO, [g(0, 8000)], 8000, device="auto", forced=None, prefix="rocm")["device"] == "rocm:0"
p = plan(LARGE, [g(0, 2000)], 30000, device="auto", forced=None, gpu_cts=["float16", "int8", "int4"], prefix="openvino:GPU.")
assert (p["device"], p["compute_type"]) == ("openvino:GPU.0", "int4"), p
assert plan(LARGE, [g(0, 100)], 30000, device="auto", forced=None, cpu_fallback=False, prefix="openvino:GPU.")["device"] == "openvino:GPU"

# ---------- 장치 판별(가짜 장치) ----------
assert backends.pick("auto", nvidia=True).name == "cuda"  # NVIDIA > AMD > Intel > CPU
assert backends.pick("auto", nvidia=False, rocm=True).name == "rocm"
assert backends.pick("auto", nvidia=False, rocm=False, openvino=True).name == "openvino"
assert backends.pick("auto", nvidia=False, rocm=False, openvino=False).name == "cpu"
assert backends.pick("rocm").experimental and backends.pick("rocm").no_unload and not backends.pick("cuda").no_unload
d = Path(tempfile.mkdtemp())
for i, (vendor, total, used, gfx) in enumerate([("0x10de", 8, 1, 0), ("0x1002", 16, 4, 110001), ("0x1002", 8, 0, 120001)]):
    c = d / "drm" / f"card{i}" / "device"
    c.mkdir(parents=True)
    (c / "vendor").write_text(vendor)
    (c / "mem_info_vram_total").write_text(str(total * 2**30))
    (c / "mem_info_vram_used").write_text(str(used * 2**30))
    n = d / "kfd" / str(i)
    n.mkdir(parents=True)
    (n / "properties").write_text(f"cpu_cores_count 0\ngfx_target_version {gfx}\n")


class NoHip:
    def hipGetDeviceCount(self, _):
        return 100  # hipErrorNoDevice


got = backends.amd_gpus(hip=NoHip(), sysfs=str(d / "drm"))
assert [(x["index"], x["total_mb"], x["free_mb"]) for x in got] == [(0, 16384, 12288), (1, 8192, 8192)], got
assert sorted(backends.gfx_targets(str(d / "kfd"))) == ["gfx1101", "gfx1201"]
assert backends.gfx_index(["gfx1101"]) == "gfx110X-all" and backends.gfx_index(["gfx1030"]) == "gfx103X-all"
assert backends.gfx_index(["gfx1151"]) == "gfx1151" and backends.gfx_index(["gfx906"]) is None
ov = backends.OpenVINO()
assert ov.repo("deepdml/faster-whisper-large-v3-turbo-ct2", "float16") == "OpenVINO/whisper-large-v3-turbo-fp16-ov"
assert not ov.supports(backends.NEMOTRON) and backends.CT2("cpu").supports("Systran/faster-whisper-small")
assert ov.weights({"openvino_encoder_model.bin": 2**29, "openvino_decoder_model.bin": 2**29}, "x", "int8") == 2048
assert backends.OOM.search("hipErrorOutOfMemory") and backends.OOM.search("CUDA failed with error out of memory")

# ---------- 의존성: server.py 머리(PEP 723) = pyproject.toml ----------
head = re.search(r"# /// script\n(.*?)# ///", Path("server.py").read_text(encoding="utf-8"), re.S).group(1)
pep723 = tomllib.loads("\n".join(line[2:] for line in head.splitlines()))
proj = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]
assert pep723["dependencies"] == proj["dependencies"], (pep723["dependencies"], proj["dependencies"])
assert pep723["requires-python"] == proj["requires-python"]
print("ok")
