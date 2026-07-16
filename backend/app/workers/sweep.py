import asyncio
import logging
from datetime import datetime, timedelta, timezone

from shared.config import get_settings
from shared.db import session_scope
from shared.models import Candidate, InterviewSession, Phase2Status, TokenStatus

logger = logging.getLogger(__name__)

_SWEEP_INTERVAL_SECONDS = 60


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _sweep_once() -> None:
    settings = get_settings()
    now = datetime.now(timezone.utc)

    with session_scope() as db:
        # No-shows: link never used, deadline passed.
        no_shows = (
            db.query(InterviewSession)
            .filter(InterviewSession.token_status == TokenStatus.PENDING)
            .all()
        )
        for session_row in no_shows:
            if now > _aware(session_row.expires_at):
                session_row.token_status = TokenStatus.EXPIRED
                candidate = db.get(Candidate, session_row.candidate_id)
                if candidate is not None:
                    candidate.phase2_status = Phase2Status.NO_SHOW

        # Abandoned mid-interview: started but never reached completion within the grace window.
        grace = timedelta(seconds=settings.reconnect_grace_seconds)
        active_sessions = (
            db.query(InterviewSession).filter(InterviewSession.token_status == TokenStatus.ACTIVE).all()
        )
        for session_row in active_sessions:
            started = session_row.started_at
            if started is not None and now > _aware(started) + grace:
                session_row.token_status = TokenStatus.EXPIRED
                session_row.ended_at = now
                candidate = db.get(Candidate, session_row.candidate_id)
                if candidate is not None:
                    candidate.phase2_status = Phase2Status.EXPIRED


async def run_forever() -> None:
    logger.info("Interview link sweep worker started (interval=%ss)", _SWEEP_INTERVAL_SECONDS)
    while True:
        try:
            await asyncio.to_thread(_sweep_once)
        except Exception:  # noqa: BLE001 — the sweep loop must never die
            logger.exception("Sweep iteration failed")
        await asyncio.sleep(_SWEEP_INTERVAL_SECONDS)
