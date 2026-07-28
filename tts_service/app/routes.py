"""HTTP API for the standalone TTS microservice.

Concurrency model (the whole reason this is its own scalable service): ONNX inference is CPU-bound
and blocking, so `async` alone buys nothing — we run each synthesis in a thread (`asyncio.to_thread`)
guarded by a process-wide `Semaphore(TTS_MAX_CONCURRENCY)`. Requests beyond the limit wait briefly
for a slot (natural backpressure); if none frees within TTS_ACQUIRE_TIMEOUT_S we return 503 so a
burst sheds load instead of thrashing every live interview. Thread usage depends on the engine
(Kitten pins ONNX threads low for many-tiny-syntheses; Pocket lets ONNX use the cores so each
near-real-time synthesis finishes fast — see the engines). The service holds no per-interview state,
so capacity scales by running more replicas."""

import asyncio
import logging
import os
import threading

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .synth import SAMPLE_RATE, synthesizer

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_CONCURRENCY = int(os.getenv("TTS_MAX_CONCURRENCY", "3"))
ACQUIRE_TIMEOUT_S = float(os.getenv("TTS_ACQUIRE_TIMEOUT_S", "10"))
# ~40 ms frames (24000 * 0.04 * 2 bytes) — synthesis produces the whole sentence at once, but we
# hand it back in small frames so the gateway/browser can start playing before it's fully drained.
_FRAME_BYTES = int(SAMPLE_RATE * 0.04) * 2

_semaphore = asyncio.Semaphore(MAX_CONCURRENCY)


class SynthesizeRequest(BaseModel):
    text: str
    voice: str | None = None


@router.get("/health")
def health() -> dict:
    # Reports "ok" only once the model is warm, so orchestration / load balancers don't route
    # synthesis at a replica that's still loading weights.
    return {"status": "ok" if synthesizer.ready else "loading"}


@router.post("/synthesize")
async def synthesize(req: SynthesizeRequest, request: Request):
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    if not synthesizer.ready:
        raise HTTPException(status_code=503, detail="model still loading")

    try:
        await asyncio.wait_for(_semaphore.acquire(), timeout=ACQUIRE_TIMEOUT_S)
    except asyncio.TimeoutError:
        # Overloaded: shed this request rather than pile onto a saturated CPU. ai_service handles
        # a failed sentence via its existing retry-nudge path (interview_ws._respond).
        raise HTTPException(status_code=503, detail="tts overloaded")

    # Barge-in: when the candidate interrupts, ai_service closes this HTTP stream. Watch for the
    # disconnect and set a cancel flag so the (near-real-time) Pocket generation stops early instead
    # of burning CPU synthesizing a sentence no one will hear.
    cancel = threading.Event()

    async def _watch_disconnect() -> None:
        try:
            while not await request.is_disconnected():
                await asyncio.sleep(0.2)
            cancel.set()
        except asyncio.CancelledError:
            return

    watcher = asyncio.create_task(_watch_disconnect())
    try:
        # The CPU-bound synthesis is bounded by the semaphore; once we have the PCM the semaphore is
        # released and only the cheap frame-slicing happens while streaming out.
        pcm = await asyncio.to_thread(synthesizer.synthesize_pcm, text, req.voice, cancel)
    except Exception:
        logger.exception("Synthesis failed")
        raise HTTPException(status_code=500, detail="synthesis failed")
    finally:
        watcher.cancel()
        _semaphore.release()

    def _frames():
        for i in range(0, len(pcm), _FRAME_BYTES):
            yield pcm[i : i + _FRAME_BYTES]

    return StreamingResponse(_frames(), media_type="audio/pcm")
