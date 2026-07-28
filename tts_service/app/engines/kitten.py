"""KittenTTS engine — the tiny ~25M nano ONNX model. Lightweight and fast (well above real-time),
but grainier than Pocket. Kept selectable via TTS_ENGINE=kitten for A/B and as a low-cost fallback.

One-shot: renders a whole sentence in a single forward pass, so stream() yields it as one PCM block
(routes.py re-frames for the wire). Output is 24 kHz mono int16 LE PCM."""

import logging
import os
import threading
from typing import Iterator

import numpy as np

logger = logging.getLogger(__name__)

# Optional LOCAL file overrides. Leave unset to let KittenTTS() download nano-0.1 + voices from HF.
# NB: the kittentts package treats a positional/`model_path` arg as a local .onnx FILE, not a repo id.
MODEL_PATH = os.getenv("TTS_MODEL_PATH") or None
VOICES_PATH = os.getenv("TTS_VOICES_PATH") or None
DEFAULT_VOICE = os.getenv("TTS_VOICE", "expr-voice-2-f")
# Tempo multiplier (higher = faster, pitch preserved). Model default 1.0 is draggy (~2.3 wps); 1.3
# lands at a natural ~2.8 wps.
SPEED = float(os.getenv("TTS_SPEED", "1.3"))
SAMPLE_RATE = 24000


class KittenEngine:
    name = "kitten"

    def __init__(self) -> None:
        self._model = None

    def load(self) -> None:
        from kittentts import KittenTTS  # lazy import so tooling/tests don't pay for it

        logger.info("Loading KittenTTS (local=%s) ...", MODEL_PATH or "hf:nano-0.1")
        self._model = KittenTTS(MODEL_PATH, VOICES_PATH) if MODEL_PATH else KittenTTS()
        logger.info("KittenTTS loaded (default voice=%s)", DEFAULT_VOICE)

    @property
    def ready(self) -> bool:
        return self._model is not None

    def stream(self, text: str, voice: str | None = None,
               cancel: "threading.Event | None" = None) -> Iterator[bytes]:
        # Fast one-shot; `cancel` is accepted for interface parity but not checked (synthesis is
        # well above real-time, so there's nothing long-running to interrupt).
        if self._model is None:
            raise RuntimeError("Kitten engine not loaded")
        audio = self._model.generate(text, voice=voice or DEFAULT_VOICE, speed=SPEED)
        audio = _trim_edges(np.asarray(audio, dtype=np.float32).reshape(-1), SAMPLE_RATE)
        yield _pcm16(audio)


def _trim_edges(a: np.ndarray, sr: int) -> np.ndarray:
    """Trim the near-silent lead-in and the low-level noisy TAIL KittenTTS leaves after the last word
    (the audible "grain" between sentences), keeping generous margins so a real word is never clipped."""
    win = int(sr * 0.020)
    hop = int(sr * 0.010)
    if len(a) <= win * 2:
        return a
    frames = np.lib.stride_tricks.sliding_window_view(a, win)[::hop]
    rms = np.sqrt(np.mean(frames.astype(np.float32) ** 2, axis=1))
    thr = max(0.015, 0.10 * float(rms.max()))
    above = np.nonzero(rms > thr)[0]
    if len(above) == 0:
        return a
    start = max(0, int(above[0]) * hop - int(sr * 0.040))
    end = min(len(a), int(above[-1]) * hop + win + int(sr * 0.080))
    return a[start:end]


def _pcm16(audio) -> bytes:
    arr = np.asarray(audio, dtype=np.float32).reshape(-1)
    np.clip(arr, -1.0, 1.0, out=arr)
    return (arr * 32767.0).astype("<i2").tobytes()
