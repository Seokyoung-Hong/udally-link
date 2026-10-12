"""장비 4경우 계획 검사. 실행: uv run --no-project --with fastapi,psutil,python-multipart,numpy python test_plan.py"""

from server import plan

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
print("ok")
