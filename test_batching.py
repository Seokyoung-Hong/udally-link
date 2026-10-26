"""CUDA 배치의 원본 구간 보존·재시도·메모리 선택 회귀 검사(모델 다운로드 없음)."""

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy

import backends
import link
import server
from whisper_runtime import CudaWhisper, batch_limit, choose_batch, wrap


class BatchTests(unittest.TestCase):
    def test_default_accuracy_profile_is_sequential_and_has_no_ngram_suppression(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "model.bin").write_bytes(b"test")
            model = Mock()
            wrapped = wrap(
                model,
                folder,
                {"index": 0, "total_mb": 16000, "free_mb": 12000},
                "float16",
                lambda key, default=None: default,
            )
            self.assertEqual(wrapped.profile, "accuracy")
            self.assertEqual(wrapped.batch_size, 0)
            wrapped.transcribe(numpy.zeros(16000), "ko", "용어")
            self.assertEqual(
                model.transcribe.call_args.kwargs["no_repeat_ngram_size"], 0
            )
            self.assertEqual(
                model.transcribe.call_args.kwargs["repetition_penalty"], 1.0
            )

    def test_cpu_thread_budget_and_container_limits(self):
        with (
            patch.object(backends, "available_cpus", return_value=8),
            patch.object(
                backends, "env", side_effect=lambda key, default=None: default
            ),
            patch("faster_whisper.WhisperModel") as model,
        ):
            backends.CT2("cpu").open("test", None, "int8", 2)
            self.assertEqual(model.call_args.kwargs["cpu_threads"], 4)
            self.assertEqual(model.call_args.kwargs["num_workers"], 2)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "cpu.max").write_text("200000 100000")
            (root / "memory.max").write_text(str(2 * 2**30))
            (root / "memory.current").write_text(str(1536 * 2**20))
            with (
                patch("psutil.Process") as proc,
                patch(
                    "psutil.virtual_memory",
                    return_value=SimpleNamespace(available=16 * 2**30),
                ),
            ):
                proc.return_value.cpu_affinity.return_value = list(range(8))
                self.assertEqual(backends.available_cpus(folder), 2)
                self.assertEqual(backends._ram_free_mb(folder), 512)
                (root / "cpu.max").unlink()
                (root / "cpu").mkdir()
                (root / "cpu/cpu.cfs_quota_us").write_text("100000")
                (root / "cpu/cpu.cfs_period_us").write_text("100000")
                self.assertEqual(backends.available_cpus(folder), 1)

    def test_accuracy_gpu_never_silently_quantizes_and_cpu_uses_available_ram(self):
        original_plan = server.plan
        allow_fallback = False

        def controlled_plan(*args, **kwargs):
            kwargs.update(device="auto", cpu_fallback=allow_fallback)
            return original_plan(*args, **kwargs)

        with (
            patch.object(server, "plan", side_effect=controlled_plan),
            patch.object(server, "PROFILE", "accuracy"),
            patch.object(server, "FORCED_CT", None),
            patch.object(server, "weights", {"test": 2950}),
            patch.object(server, "cts_of", return_value=[None]),
        ):
            with patch.object(server, "runtime", return_value=backends.CT2("cuda")):
                self.assertFalse(
                    server.plan_for("test", [{"index": 0, "free_mb": 4000}], 16000)[
                        "fits"
                    ]
                )
                single = server.plan_for(
                    "test", [{"index": 0, "free_mb": 12000}], 16000
                )
                self.assertEqual(single["workers"], 1)
                self.assertEqual(single["compute_type"], "float16")
                allow_fallback = True
                fallback = server.plan_for(
                    "test", [{"index": 0, "free_mb": 1000}], 16000
                )
                self.assertEqual(fallback["device"], "cpu")
                self.assertEqual(fallback["compute_type"], "float32")
                allow_fallback = False
            with patch.object(server, "runtime", return_value=backends.CT2("cpu")):
                self.assertEqual(
                    server.plan_for("test", [], 16000)["compute_type"], "float32"
                )
                self.assertEqual(
                    server.plan_for("test", [], 4000)["compute_type"], "int8"
                )

    def test_memory_limits_are_per_device_and_conservative(self):
        self.assertFalse(
            server.plan(2950, [], 16000, device="cuda", cpu_fallback=False)["fits"]
        )
        self.assertFalse(
            server.plan(
                2950, [], 16000, device="auto", cpu_fallback=False, gpu_expected=True
            )["fits"]
        )
        self.assertEqual(
            server.plan(2950, [], 16000, device="auto", cpu_fallback=False)["device"],
            "cpu",
        )
        self.assertEqual(
            choose_batch(2950, {"total_mb": 16311, "free_mb": 11372}, "float16"), 8
        )
        self.assertEqual(
            choose_batch(2950, {"total_mb": 12288, "free_mb": 7428}, "float16"), 4
        )
        self.assertLessEqual(
            choose_batch(2950, {"total_mb": 8192, "free_mb": 7000}, "float16"), 2
        )
        self.assertEqual(
            choose_batch(2950, {"total_mb": 16311, "free_mb": 5000}, "float16", 0), 0
        )
        self.assertEqual(
            choose_batch(2950, {"total_mb": 16311, "free_mb": 5000}, "float16"), 1
        )
        self.assertIsNone(batch_limit("auto"))
        for bad in ("-1", "33", "eight"):
            with self.assertRaises(ValueError):
                batch_limit(bad)

    def test_original_clip_owner_wins_over_generated_timestamps(self):
        model = Mock(frames_per_second=50)
        wrapper = CudaWhisper(model, 8, quality_guard=False)
        audios = [
            numpy.zeros(16000 * 4, numpy.float32),
            numpy.ones(16000 * 4, numpy.float32),
        ]
        pipeline = Mock()

        def infer(data, **kwargs):
            self.assertIsNone(kwargs["initial_prompt"])
            self.assertEqual(len(kwargs["hotwords"]), 220)
            clips = kwargs["clip_timestamps"]
            self.assertEqual(
                clips, [{"start": 0.0, "end": 4.0}, {"start": 4.0, "end": 8.0}]
            )
            self.assertTrue(numpy.array_equal(data, numpy.concatenate(audios)))
            # 모델이 생성한 시각이 다른 화자 영역으로 잘못 넘어가도 원본 clip seek로 배정.
            return iter(
                [
                    SimpleNamespace(seek=0, start=4.2, end=9, text="첫 화자"),
                    SimpleNamespace(seek=200, start=0, end=2, text="둘째 화자"),
                ]
            ), None

        pipeline.transcribe.side_effect = infer
        with (
            patch("faster_whisper.BatchedInferencePipeline", return_value=pipeline),
            patch(
                "faster_whisper.vad.get_speech_timestamps",
                side_effect=lambda a, _: [{"start": 0, "end": len(a)}],
            ),
        ):
            self.assertEqual(
                wrapper.transcribe_many(audios, "ko", "용" * 300),
                ["첫 화자", "둘째 화자"],
            )

    def test_oom_discards_partial_generator_and_retries_same_audio(self):
        wrapper = CudaWhisper(Mock(), 8)
        audio = numpy.zeros(16000, numpy.float32)
        seen = []

        def failed():
            yield SimpleNamespace(text="부분 결과는 버림")
            raise RuntimeError("CUDA out of memory")

        def infer(data, **kwargs):
            self.assertIs(data, audio)
            seen.append(kwargs["batch_size"])
            return (
                failed() if len(seen) == 1 else iter([SimpleNamespace(text="완료")])
            ), None

        pipeline = Mock()
        pipeline.transcribe.side_effect = infer
        with patch("faster_whisper.BatchedInferencePipeline", return_value=pipeline):
            parts, _ = wrapper.transcribe(audio, "ko")
            self.assertEqual([p.text for p in parts], ["완료"])
        self.assertEqual(seen, [8, 4])
        self.assertEqual(wrapper.batch_size, 4)

    def test_silence_does_not_inject_keywords_or_call_gpu(self):
        with (
            patch("faster_whisper.vad.get_speech_timestamps", return_value=[]),
            patch("faster_whisper.BatchedInferencePipeline") as pipeline,
        ):
            texts = CudaWhisper(Mock(), 8).transcribe_many(
                [numpy.zeros(16000)], "ko", "API"
            )
            self.assertEqual(texts, [""])
            pipeline.assert_not_called()

    def test_keyword_only_batch_output_is_rechecked_on_the_same_model(self):
        audio = numpy.zeros(10 * 16000)
        model = Mock(frames_per_second=50)
        model.transcribe.return_value = (
            iter([SimpleNamespace(text="설명 문장이 실제로 이어집니다.")]),
            None,
        )
        wrapper = CudaWhisper(model, 8)
        batched = iter([SimpleNamespace(seek=0, text="R-Type, S-Type")])
        with (
            patch(
                "faster_whisper.vad.get_speech_timestamps",
                return_value=[{"start": 0, "end": len(audio)}],
            ),
            patch.object(wrapper, "_batched", return_value=(batched, None)),
        ):
            self.assertEqual(
                wrapper.transcribe_many([audio], "ko", "R-Type, S-Type"),
                ["설명 문장이 실제로 이어집니다."],
            )
        self.assertIs(model.transcribe.call_args.args[0], audio)
        self.assertEqual(wrapper.quality_retries, 1)
        self.assertFalse(wrapper.needs_review("R-Type", 0, "R-Type"))

    def test_long_speech_is_split_without_truncating_after_30_seconds(self):
        wrapper = CudaWhisper(Mock(frames_per_second=50), 4, quality_guard=False)
        with (
            patch(
                "faster_whisper.vad.get_speech_timestamps",
                return_value=[{"start": 0, "end": 65 * 16000}],
            ),
            patch.object(wrapper, "_batched", return_value=(iter([]), None)) as batched,
        ):
            wrapper.transcribe_many([numpy.zeros(65 * 16000)], "ko")
            clips = batched.call_args.kwargs["clips"]
            self.assertEqual(clips[-1]["end"], 65)
            self.assertEqual([c["end"] - c["start"] for c in clips], [30, 30, 5])

    def test_short_vad_fragments_share_one_clip_within_the_same_source_row(self):
        wrapper = CudaWhisper(Mock(frames_per_second=50), 8, quality_guard=False)
        spans = [{"start": i * 16000, "end": i * 16000 + 8000} for i in range(20)]
        with (
            patch("faster_whisper.vad.get_speech_timestamps", return_value=spans),
            patch.object(wrapper, "_batched", return_value=(iter([]), None)) as batched,
        ):
            wrapper.transcribe_many(
                [numpy.zeros(30 * 16000), numpy.zeros(30 * 16000)], "ko"
            )
            self.assertEqual(len(batched.call_args.kwargs["clips"]), 2)
            self.assertEqual(batched.call_args.kwargs["clips"][1]["start"], 30)

    def test_groups_are_bounded_and_keep_metadata(self):
        items = [
            (
                i,
                str(i % 2),
                {"seq": i, "start_ms": i * 20000, "duration_ms": 20000},
                numpy.zeros(20 * 16000),
            )
            for i in range(30)
        ]
        groups = list(link.groups(items))
        self.assertEqual([len(g) for g in groups], [12, 12, 6])
        self.assertEqual(
            [row[:3] for group in groups for row in group], [row[:3] for row in items]
        )

    def test_failed_group_retries_individual_rows_and_retains_partial_failures(self):
        def single(model, audio, *args):
            if len(audio) == 16000:
                raise RuntimeError("bad row")
            return "성공"

        job = link.Job({"id": 1, "model": "test"}, "test-token", run=single)
        items = [
            (
                i,
                str(i),
                {"start_ms": i * 2000, "duration_ms": (i + 1) * 1000},
                numpy.zeros((i + 1) * 16000),
            )
            for i in range(2)
        ]
        with patch.object(
            server, "run_many", side_effect=RuntimeError("batch failure")
        ):
            job.many(items)
        self.assertTrue(job.rows[0]["failed"])
        self.assertEqual(job.rows[1]["user_id"], "1")
        self.assertEqual(job.rows[1]["start_ms"], 2000)
        self.assertEqual(job.rows[1]["text"], "성공")
        self.assertEqual(job.p["segments_failed"], 1)
        self.assertEqual(job.p["segments_done"], 1)

    def test_fast_gpu_can_receive_more_audio_and_capacity_is_actual_slots(self):
        fast, slow = Mock(), Mock()
        fast_pool, slow_pool = Mock(), Mock()
        fast_pool.submit.return_value.result.return_value = ["완료"]
        workers = [
            [fast, 1, fast_pool, {"seconds": 50.0, "rtf": 0.02}],
            [slow, 1, slow_pool, {"seconds": 20.0, "rtf": 0.1}],
        ]
        cur = {"plan": {"gpus": [0, 1], "workers": 1}, "workers": workers}
        with (
            patch.object(server, "current", cur),
            patch.object(server, "load", return_value=cur),
        ):
            result = server.run_many(
                "test", [numpy.zeros(16000)], "ko", None, threading.Event()
            )
            self.assertEqual(result, ["완료"])
            fast_pool.submit.assert_called_once()
            slow_pool.submit.assert_not_called()
            self.assertEqual(server.capacity(), 2)
        self.assertEqual(workers[0][3]["seconds"], 50)


if __name__ == "__main__":
    unittest.main()
