"""Local speech-to-text (faster-whisper) for English + Urdu, with simple silence endpointing."""
from __future__ import annotations

from typing import Any, Iterable

import numpy as np

SAMPLE_RATE = 16000
ALLOWED_LANGS = ("en", "ur")


def frame_rms(samples: np.ndarray, frame: int) -> np.ndarray:
    n = len(samples) // frame
    if n == 0:
        return np.zeros(0)
    f = samples[: n * frame].reshape(n, frame).astype(np.float32)
    return np.sqrt((f ** 2).mean(axis=1))


def endpoint(samples: np.ndarray, sr: int = SAMPLE_RATE, frame_ms: int = 30, silence_ms: int = 900,
             min_speech_ms: int = 200, factor: float = 3.0, floor: float = 0.004) -> tuple[int, bool]:
    """Return (end_index, speech_found): where speech ended (after `silence_ms` of quiet).

    Noise floor is estimated from the first 300ms. end_index == len(samples) if still speaking.
    """
    frame = int(sr * frame_ms / 1000)
    rms = frame_rms(samples, frame)
    if len(rms) == 0:
        return len(samples), False
    cal = max(1, int(300 / frame_ms))
    thresh = max(floor, float(np.median(rms[:cal])) * factor)
    voiced = rms > thresh
    need_speech = int(min_speech_ms / frame_ms)
    need_silence = int(silence_ms / frame_ms)
    run_voiced, seen, quiet = 0, False, 0
    for i, v in enumerate(voiced):
        if v:
            run_voiced += 1
            quiet = 0
            if run_voiced >= need_speech:
                seen = True
        else:
            run_voiced = 0
            if seen:
                quiet += 1
                if quiet >= need_silence:
                    return min(len(samples), (i + 1) * frame), True
    return len(samples), seen


def pick_language(probs: Iterable[tuple[str, float]] | None, detected: str, allowed=ALLOWED_LANGS) -> str:
    """Choose en/ur: keep detected if allowed, else the better allowed language from the probs."""
    if detected in allowed:
        return detected
    best, best_p = allowed[0], -1.0
    for lang, p in (probs or []):
        if lang in allowed and p > best_p:
            best, best_p = lang, p
    return best


class Recorder:
    """Record mic audio until the speaker stops (lazy sounddevice import)."""

    def __init__(self, max_seconds: float = 15.0, sr: int = SAMPLE_RATE):
        self.max_seconds, self.sr = max_seconds, sr

    def record(self) -> np.ndarray:
        import sounddevice as sd
        chunks: list[np.ndarray] = []
        block = int(self.sr * 0.1)
        with sd.InputStream(samplerate=self.sr, channels=1, dtype="float32", blocksize=block) as stream:
            total = 0
            while total < self.max_seconds * self.sr:
                data, _ = stream.read(block)
                chunks.append(data[:, 0].copy())
                total += len(data)
                audio = np.concatenate(chunks)
                end, spoke = endpoint(audio, self.sr)
                if spoke and end < len(audio):
                    return audio[:end]
                if not spoke and total > 5 * self.sr:   # nobody spoke
                    return audio[:0]
        return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)


class Transcriber:
    def __init__(self, model_size: str = "small"):
        self.model_size = model_size
        self._model: Any = None

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
        return self._model

    def transcribe(self, audio: np.ndarray) -> tuple[str, str]:
        if len(audio) < SAMPLE_RATE * 0.25:
            return "", "en"
        model = self._load()
        segs, info = model.transcribe(audio.astype(np.float32), language=None, beam_size=1,
                                      vad_filter=True, condition_on_previous_text=False)
        lang = pick_language(getattr(info, "all_language_probs", None), info.language)
        if lang != info.language:  # detected something else: redo in the best allowed language
            segs, info = model.transcribe(audio.astype(np.float32), language=lang, beam_size=1, vad_filter=True)
        return " ".join(s.text.strip() for s in segs).strip(), lang
