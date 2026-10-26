"""장치 상태 보고 회귀 검사. 모델 다운로드·실제 GPU·네트워크 없이 실행한다."""

import os
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

os.environ["LINK_DEVICE"] = "cpu"
import server as s  # noqa: E402


class RuntimeStatusTests(unittest.TestCase):
    def setUp(self):
        self.scope = ExitStack()
        self.addCleanup(self.scope.close)
        for name in ("current", "levels", "runtime_errors", "weights", "errors", "downloading", "rtf"):
            self.scope.enter_context(patch.object(s, name, {}))
        self.scope.enter_context(patch.object(s, "gpu_broken", []))
        self.scope.enter_context(patch.object(s, "DEVICE", "auto"))

    def test_cpu_cause_and_private_path_redaction(self):
        cpu = {"device": "cpu"}
        self.assertEqual(s.cpu_state("test", cpu, [])['cpu_reason'], "gpu_unavailable")
        self.assertEqual(s.cpu_state("test", cpu, [{}])['cpu_reason'], "gpu_memory")
        with patch.object(s, "DEVICE", "cpu"):
            self.assertEqual(s.cpu_state("test", cpu, [])['cpu_reason'], "selected")
        s.gpu_broken.append(r"C:\Users\private-user\bin\cublas64_12.dll is missing")
        info = s.cpu_state("test", cpu, [])
        self.assertEqual(info["cpu_reason"], "gpu_library")
        self.assertEqual(info["gpu_error_detail"], "cublas64_12.dll")

    def test_download_ready_does_not_mean_loaded(self):
        plan = {"fits": True, "device": "cuda:0", "compute_type": "float16"}
        for name, value in (("meta", {}), ("local", True), ("plan_for", plan)):
            self.scope.enter_context(patch.object(s, name, return_value=value))
        info = s.status_of("test", ([{"index": 0}], 10000))
        self.assertEqual(info["status"], "ready")
        self.assertFalse(info["loaded"])
        self.assertIsNone(info["loaded_device"])
        s.current.update(id="test", plan={**plan, "device": "cpu", "compute_type": "int8"})
        info = s.status_of("test", ([{"index": 0}], 10000))
        self.assertTrue(info["loaded"])
        self.assertEqual(info["loaded_device"], "cpu")
        self.assertEqual(info["planned_device"], "cuda:0")

    def test_gpu_failure_is_reported_when_cpu_fallback_is_disabled(self):
        rt = Mock(gpu_error=r"cuda|cublas", no_unload=False)
        rt.open.side_effect = RuntimeError("cannot load cublas64_12.dll")
        plan = {"fits": True, "device": "cuda:0", "gpus": [0], "compute_type": "float16", "workers": 1}
        self.scope.enter_context(patch.object(s, "CPU_FALLBACK", False))
        for name, value in (("runtime", rt), ("gpus", [{}]), ("ram_free_mb", 10000),
                            ("plan_for", plan), ("local_path", "cached-model"), ("cts_of", ["float16"])):
            self.scope.enter_context(patch.object(s, name, return_value=value))
        with self.assertRaisesRegex(RuntimeError, "cublas"):
            s.load("test")
        self.assertEqual(s.runtime_errors["test"], {
            "runtime_error": "gpu_library", "runtime_error_detail": "cublas64_12.dll",
        })
        self.assertFalse(s.current)


if __name__ == "__main__":
    unittest.main()
