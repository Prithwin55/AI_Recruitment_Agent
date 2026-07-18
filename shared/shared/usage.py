"""Append-only usage metering for external services (Claude/LLM, STT, TTS, OCR).

Call sites record raw quantities the moment they're known; pricing happens at read time in the
admin API from env-configured rates. Recording is deliberately fail-soft: a metering failure must
never break the user-facing operation it measures.
"""

import asyncio
import logging

from .db import session_scope
from .models import UsageEvent, UsageService

logger = logging.getLogger(__name__)


def record_usage(
    service: UsageService,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    seconds: float = 0.0,
    characters: int = 0,
    pages: int = 0,
    context: str | None = None,
) -> None:
    """Synchronous insert of one usage row. Swallows (but logs) every failure."""
    if not (input_tokens or output_tokens or seconds or characters or pages):
        return  # nothing consumed — don't write empty rows
    try:
        with session_scope() as db:
            db.add(
                UsageEvent(
                    service=service,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    seconds=seconds,
                    characters=characters,
                    pages=pages,
                    context=context,
                )
            )
    except Exception:  # noqa: BLE001 — metering must never break the metered operation
        logger.exception("Failed to record %s usage", service.value)


async def record_usage_async(service: UsageService, **kwargs) -> None:
    """record_usage without blocking the event loop (used from async AI/interview paths)."""
    await asyncio.to_thread(record_usage, service, **kwargs)
