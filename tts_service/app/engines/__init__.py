"""TTS engine selection. `TTS_ENGINE` picks which one the service runs:
  - pocket (default): kyutai flow-matching, natural voice, ~real-time on CPU (heavier).
  - kitten:           tiny nano model, fast but grainier.
Both expose the same interface: load(), .ready, stream(text, voice, cancel) -> Iterator[bytes]
(24 kHz mono int16 LE PCM)."""

import os


def get_engine():
    name = os.getenv("TTS_ENGINE", "pocket").strip().lower()
    if name == "kitten":
        from .kitten import KittenEngine
        return KittenEngine()
    if name == "pocket":
        from .pocket import PocketEngine
        return PocketEngine()
    raise ValueError(f"Unknown TTS_ENGINE {name!r} (expected 'pocket' or 'kitten')")
