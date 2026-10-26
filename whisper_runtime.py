"""CUDA Whisper 배치 실행. 장치 이름 대신 메모리로 보수적인 상한을 고른다."""

import bisect
import logging
import re
from pathlib import Path

import numpy

log = logging.getLogger("udally-link")
SR = 16000


def profile(env):
    value = env("LINK_PROFILE", "accuracy") or "accuracy"
    if value not in ("accuracy", "balanced", "throughput"):
        raise ValueError(
            "LINK_PROFILE는 accuracy, balanced, throughput 중 하나여야 합니다."
        )
    return value


def batch_limit(value):
    if value in (None, "", "auto"):
        return None
    try:
        size = int(value)
    except (TypeError, ValueError):
        raise ValueError("LINK_BATCH_SIZE는 auto 또는 0~32 정수여야 합니다.") from None
    if not 0 <= size <= 32:
        raise ValueError("LINK_BATCH_SIZE는 auto 또는 0~32 정수여야 합니다.")
    return size


def choose_batch(weights_mb, gpu, compute_type, maximum=None):
    """실측에서 관찰한 VRAM 압박을 피하는 추정치이며 하드웨어별 최적값 보장은 아니다."""
    if maximum == 0:
        return 0
    total, free = gpu.get("total_mb", 0), gpu.get("free_mb", 0)
    cap = (
        maximum
        if maximum is not None
        else (2 if total <= 8192 else 4 if total <= 12288 else 8)
    )
    factor = (
        2
        if compute_type == "float32"
        else 1
        if compute_type in ("float16", "bfloat16")
        else 0.5
    )
    resident = weights_mb * factor * 1.25 + 300
    reserve = max(768, total * 0.08)
    per_item = max(96, 512 * weights_mb / 2950)
    affordable = max(1, int((free - resident - reserve) / per_item))
    candidates = [b for b in (1, 2, 4, 8, 16, 32) if b <= min(cap, affordable)]
    return max(candidates, default=1)


def terms(text):
    """문장형 initial_prompt 대신 제한된 전문 용어만 전달한다."""
    return (text or "").strip()[:220] or None


class CudaWhisper:
    def __init__(
        self,
        model,
        batch_size,
        *,
        beam_size=5,
        no_repeat_ngram_size=4,
        repetition_penalty=1.1,
        quality_guard=True,
    ):
        self.model = model
        self.batch_size = batch_size
        self.reductions = 0
        self.quality_guard = quality_guard
        self.quality_retries = 0
        self.options = {
            "beam_size": beam_size,
            "no_repeat_ngram_size": no_repeat_ngram_size,
            "repetition_penalty": repetition_penalty,
        }

    def _batched(self, data, language, prompt, *, clips=None, vad_filter=True):
        from faster_whisper import BatchedInferencePipeline

        while True:
            try:
                segments, info = BatchedInferencePipeline(self.model).transcribe(
                    data,
                    language=language,
                    hotwords=terms(prompt),
                    initial_prompt=None,
                    batch_size=self.batch_size,
                    vad_filter=vad_filter,
                    clip_timestamps=clips,
                    **self.options,
                )
                # generator 내부에서 OOM이 나더라도 부분 결과를 내보내지 않고 같은 입력을 재시도한다.
                return iter(list(segments)), info
            except Exception as exc:
                message = str(exc).lower()
                if self.batch_size <= 1 or not (
                    "out of memory" in message or "out of gpu memory" in message
                ):
                    raise
                previous = self.batch_size
                self.batch_size = max(1, previous // 2)
                self.reductions += 1
                log.warning(
                    "GPU 배치 메모리 부족: %s → %s, 같은 오디오를 다시 처리합니다.",
                    previous,
                    self.batch_size,
                )

    def transcribe(self, data, language=None, initial_prompt=None, vad_filter=True):
        if not self.batch_size:
            return self.model.transcribe(
                data,
                language=language,
                initial_prompt=initial_prompt,
                vad_filter=vad_filter,
                **self.options,
            )
        segments, info = self._batched(
            data, language, initial_prompt, vad_filter=vad_filter
        )
        segments = list(segments)
        if self.needs_review(
            "".join(s.text for s in segments),
            getattr(info, "duration_after_vad", 0),
            initial_prompt,
        ):
            log.warning("배치 출력을 같은 GPU에서 순차 재확인합니다.")
            self.quality_retries += 1
            return self.model.transcribe(
                data,
                language=language,
                initial_prompt=initial_prompt,
                vad_filter=vad_filter,
                **self.options,
            )
        return iter(segments), info

    def needs_review(self, text, speech_seconds, prompt):
        if not self.quality_guard or speech_seconds < 5:
            return False

        def normalize(text):
            return re.sub(r"[\W_]+", "", text.lower())

        vocabulary = {
            normalize(t)
            for t in re.split(r"[,，;\n]+", terms(prompt) or "")
            if normalize(t)
        }
        phrases = [normalize(t) for t in re.split(r"[,，;\n]+", text) if normalize(t)]
        keywords_only = bool(phrases) and all(t in vocabulary for t in phrases)
        return len(normalize(text)) < speech_seconds * 2 or keywords_only

    def transcribe_many(self, audios, language=None, initial_prompt=None, cancel=None):
        """원본 발화 경계는 clip으로 분리한다. 출력의 화자·순서·시각을 합치지 않는다."""
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        if not self.batch_size:
            return [
                "".join(
                    s.text for s in self.transcribe(a, language, initial_prompt)[0]
                ).strip()
                for a in audios
            ]
        clips, owners, speech_lengths, offset = [], [], [], 0
        for row, audio in enumerate(audios):
            if cancel and cancel.is_set():
                return [""] * len(audios)
            speech = (
                get_speech_timestamps(
                    audio,
                    VadOptions(max_speech_duration_s=29, min_silence_duration_ms=160),
                )
                if len(audio)
                else []
            )
            speech_lengths.append(sum(s["end"] - s["start"] for s in speech) / SR)
            # 명시 clip은 각자 30초로 패딩된다. 짧은 VAD 조각을 그대로 배치하면
            # 실제 음성보다 패딩 연산이 커지므로 같은 원본 발화 안에서만 30초까지 합친다.
            merged = []
            for span in speech:
                if merged and span["end"] - merged[-1]["start"] <= 30 * SR:
                    merged[-1]["end"] = span["end"]
                else:
                    merged.append(dict(span))
            for span in merged:
                for start in range(span["start"], span["end"], 30 * SR):
                    clips.append(
                        {
                            "start": (offset + start) / SR,
                            "end": (offset + min(start + 30 * SR, span["end"])) / SR,
                        }
                    )
                    owners.append(row)
            offset += len(audio)
        parts = [[] for _ in audios]
        if not clips:
            return [""] * len(audios)
        segments, _ = self._batched(
            numpy.concatenate(audios),
            language,
            initial_prompt,
            clips=clips,
            vad_filter=False,
        )
        # seek는 원본 clip offset에서 생성된다. 모델이 생성한 start/end는 경계를 넘을 수 있어
        # 화자 배정에 쓰지 않는다(faster-whisper BatchedInferencePipeline.forward).
        seeks = [
            int(int(c["start"] * SR) / SR * self.model.frames_per_second) for c in clips
        ]
        for segment in segments:
            clip = bisect.bisect_right(seeks, segment.seek) - 1
            if clip < 0:
                raise RuntimeError("배치 출력의 원본 발화 위치를 확인할 수 없습니다.")
            parts[owners[clip]].append(segment.text)
        texts = ["".join(p).strip() for p in parts]
        for row, text in enumerate(texts):
            if cancel and cancel.is_set():
                break
            if self.needs_review(text, speech_lengths[row], initial_prompt):
                log.warning(
                    "배치 출력이 지나치게 짧거나 용어 나열입니다. 원본 발화 %s를 같은 GPU에서 순차 재확인합니다.",
                    row,
                )
                segments, _ = self.model.transcribe(
                    audios[row],
                    language=language,
                    initial_prompt=initial_prompt,
                    vad_filter=True,
                    **self.options,
                )
                texts[row] = "".join(s.text for s in segments).strip()
                self.quality_retries += 1
        return texts


def wrap(model, path, gpu, compute_type, env):
    weights = (Path(path) / "model.bin").stat().st_size / 2**20
    mode = profile(env)
    maximum = batch_limit(env("LINK_BATCH_SIZE", "auto"))
    size = (
        0 if mode == "accuracy" else choose_batch(weights, gpu, compute_type, maximum)
    )
    log.info(
        "GPU %s Whisper 배치=%s (0=순차), 여유 VRAM=%sMB",
        gpu["index"],
        size,
        gpu.get("free_mb"),
    )
    result = CudaWhisper(
        model,
        size,
        no_repeat_ngram_size=0 if mode == "accuracy" else 4,
        repetition_penalty=1.0 if mode == "accuracy" else 1.1,
        quality_guard=(
            env("LINK_QUALITY_GUARD") or ("0" if mode == "throughput" else "1")
        )
        != "0",
    )
    result.profile = mode
    return result
