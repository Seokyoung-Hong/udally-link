"""CUDA Whisper 배치 실행. 장치 이름 대신 메모리로 보수적인 상한을 고른다."""

import bisect
import logging
import re
import zlib
from pathlib import Path

import numpy

log = logging.getLogger("udally-link")
SR = 16000


def profile(env):
    value = env("LINK_PROFILE", "balanced") or "balanced"
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
        # 타임스탬프를 켠 배치는 12·16GB 모두 8이 가장 빠르고 16은 크게 느려진다(NoteMaker 11-14 실측).
        else (2 if total <= 8192 else 8)
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


# 누락·반복 판정 기준(NoteMaker 전사_누락_검증과_자동메우기_2026-11-14): 정상 강의 밀도는 하위 1% 2.9,
# 최대 14.3자/초, 확인된 누락은 0.4~1.9자/초. 정상 줄 압축률은 99.9%가 2.11 이하(2.4 = Whisper 반복 기준).
# ponytail: 보고서는 15초 미만 구간을 오탐 때문에 건너뛰지만, 여기선 교체가 "나아졌을 때만"이라 5초를 유지한다.
MIN_SPEECH_S = 5
RATE_RANGE = (2.0, 15.0)
LOOP_RATIO = 2.4


def is_loop(text):
    """같은 꼴 반복(반복 환각). 메우기로도 남은 줄은 지우지 않고 행에 loop 플래그로 알린다(link.py)."""
    data = (text or "").encode()
    return len(data) >= 60 and len(data) / len(zlib.compress(data)) > LOOP_RATIO


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
                    # 기본값(True)은 30초 창 안의 문장을 통째로 빠뜨린다(faster-whisper #1179).
                    without_timestamps=False,
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
        speech = getattr(info, "duration_after_vad", 0)
        old = "".join(s.text for s in segments)
        kind = self.issue(old, speech, initial_prompt)
        if kind:
            new = list(self._resequence(data, language, initial_prompt, vad_filter)[0])
            if self.better(kind, old, "".join(s.text for s in new), speech, initial_prompt):
                segments = new
        return iter(segments), info

    def _resequence(self, data, language, prompt, vad_filter=True):
        """의심 구간 메우기: 같은 GPU에서 순차 전사. initial_prompt는 넣지 않는다
        (짧은 구간에서 프롬프트 문장을 그대로 받아 적었다, NoteMaker 11-14)."""
        log.warning("배치 출력이 의심스러워 같은 GPU에서 순차 재확인합니다.")
        self.quality_retries += 1
        return self.model.transcribe(
            data,
            language=language,
            hotwords=terms(prompt),
            initial_prompt=None,
            vad_filter=vad_filter,
            **self.options,
        )

    def better(self, kind, old, new, speech_seconds, prompt):
        """나아졌을 때만 바꾼다: 새 결과가 정상이거나, 누락 구간에서 글자가 늘었을 때."""
        return self.issue(new, speech_seconds, prompt, guard=False) is None or (
            kind == "drop" and len(new.strip()) > len(old.strip())
        )

    def issue(self, text, speech_seconds, prompt, *, guard=True):
        """"drop"(누락)·"loop"(반복 환각)·None. 기준은 NoteMaker 11-14 실측."""
        if guard and not self.quality_guard:
            return None
        if is_loop(text):
            return "loop"
        if speech_seconds < MIN_SPEECH_S:
            return None

        def normalize(text):
            return re.sub(r"[\W_]+", "", text.lower())

        vocabulary = {
            normalize(t)
            for t in re.split(r"[,，;\n]+", terms(prompt) or "")
            if normalize(t)
        }
        phrases = [normalize(t) for t in re.split(r"[,，;\n]+", text) if normalize(t)]
        if bool(phrases) and all(t in vocabulary for t in phrases):
            return "drop"  # 용어만 나열 = 실제 말이 빠짐
        rate = len(normalize(text)) / speech_seconds
        return "drop" if rate < RATE_RANGE[0] else "loop" if rate > RATE_RANGE[1] else None

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
            kind = self.issue(text, speech_lengths[row], initial_prompt)
            if kind:
                segments, _ = self._resequence(audios[row], language, initial_prompt)
                new = "".join(s.text for s in segments).strip()
                if self.better(kind, text, new, speech_lengths[row], initial_prompt):
                    texts[row] = new
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
    # 반복 억제는 모든 모드에서 켠다. 끄면 "R-Type, R-Type, …" 같은 반복 환각이 재발한다.
    result = CudaWhisper(
        model,
        size,
        quality_guard=(
            env("LINK_QUALITY_GUARD") or ("0" if mode == "throughput" else "1")
        )
        != "0",
    )
    result.profile = mode
    return result
