"""Synthesizer facade: loads the engine selected by TTS_ENGINE (see engines/__init__.py) and turns
text into 24 kHz mono int16 LE PCM — the exact format the browser's AgentAudioPlayer plays for the
Arabic/Azure path (see frontend audioPlayback.ts).

Both engines are blocking + CPU-bound; routes.py runs synthesis in a thread pool under a concurrency
semaphore. Pocket is ~real-time (heavier), Kitten is well above real-time (lighter) — see each engine.
The service is stateless, so scale by adding replicas."""

import logging
import threading

from .engines import get_engine

logger = logging.getLogger(__name__)

# Both engines render at 24 kHz — MUST match the browser player's PLAYBACK_SAMPLE_RATE.
SAMPLE_RATE = 24000


class Synthesizer:
    def __init__(self) -> None:
        self._engine = None

    def load(self) -> None:
        """Construct + load the configured engine. Called once at startup (and at Docker build to bake
        weights in), so a bad model/dep surfaces up front, not on the first interview sentence."""
        engine = get_engine()
        engine.load()
        self._engine = engine
        logger.info("Synthesizer ready (engine=%s)", engine.name)

    @property
    def ready(self) -> bool:
        return self._engine is not None and self._engine.ready

    def stream(self, text: str, voice: str | None = None,
               cancel: "threading.Event | None" = None):
        """Yield int16 PCM blocks as the engine produces them (Pocket: progressively; Kitten: one)."""
        if self._engine is None:
            raise RuntimeError("Synthesizer not loaded")
        yield from self._engine.stream(text, voice, cancel)

    def synthesize_pcm(self, text: str, voice: str | None = None,
                       cancel: "threading.Event | None" = None) -> bytes:
        """Blocking one-shot: synthesize the whole sentence, then return its PCM. routes.py streams
        the result in frames — synthesizing fully first guarantees the client never underruns, which
        matters because Pocket runs near real-time."""
        return b"".join(self.stream(text, voice, cancel))


# One shared instance per process; loaded at startup (app/main.py) or Docker build (Dockerfile).
synthesizer = Synthesizer()
