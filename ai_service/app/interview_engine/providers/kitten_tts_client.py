"""Thin async HTTP client to the standalone KittenTTS microservice (tts_service).

Streams one sentence of 24 kHz mono int16 PCM back so SherpaOnnxProvider.speak() can forward it to
the browser chunk-by-chunk (the same server-audio path the Arabic/Azure provider uses). The service
is reached by its DNS name, so when tts_service is scaled to multiple replicas Docker round-robins
across them with no change here."""

import logging
from typing import AsyncIterator

import httpx
from shared.config import get_settings

logger = logging.getLogger(__name__)


class KittenTtsClient:
    def __init__(self) -> None:
        settings = get_settings()
        self._url = settings.tts_service_url.rstrip("/") + "/synthesize"
        self._voice = settings.tts_voice or None
        # A whole sentence is synthesized before the first byte, but that's sub-second for the nano
        # model; keep connect short and read generous. One client per provider reuses connections.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)
        )

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        """Yield raw int16 PCM frames for `text`. Raises on a non-2xx / transport error so the
        caller can fall back to its retry-nudge path, exactly like the Azure synth raising."""
        payload = {"text": text, "voice": self._voice}
        async with self._client.stream("POST", self._url, json=payload) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes():
                if chunk:
                    yield chunk

    async def aclose(self) -> None:
        await self._client.aclose()
