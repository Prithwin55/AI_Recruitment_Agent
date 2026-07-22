import asyncio
import logging
from datetime import datetime, timedelta, timezone

from shared.config import get_settings
from shared.db import session_scope
from shared.models import Candidate, InterviewSession, Phase2Status, TokenStatus

from ..scheduling.service import schedule_candidate

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

        # Abandoned mid-interview: started but never reached completion. An in-progress interview
        # must be allowed its FULL scheduled duration before a still-ACTIVE session is treated as
        # abandoned — plus the reconnect grace (for the candidate to hang up, or briefly drop and
        # rejoin). Bounding by the grace ALONE expired live interviews mid-call: started_at is set
        # once when the candidate clicks Start and never refreshed, so every interview older than
        # the grace (default 2 min) got flipped to EXPIRED while it was still running.
        grace = timedelta(seconds=settings.reconnect_grace_seconds)
        active_sessions = (
            db.query(InterviewSession).filter(InterviewSession.token_status == TokenStatus.ACTIVE).all()
        )
        for session_row in active_sessions:
            started = session_row.started_at
            if started is None:
                continue
            max_active = timedelta(minutes=session_row.duration_minutes) + grace
            if now > _aware(started) + max_active:
                session_row.token_status = TokenStatus.EXPIRED
                session_row.ended_at = now
                candidate = db.get(Candidate, session_row.candidate_id)
                if candidate is not None:
                    candidate.phase2_status = Phase2Status.EXPIRED


async def _auto_schedule_pass() -> None:
    """Send interview invites to candidates the AI auto-advanced (score >= threshold) that haven't
    been scheduled yet. This is the automatic half of scheduling — the recruiter never has to
    click for high scorers. Manually-advanced candidates keep auto_advanced == False and are left
    for the recruiter to schedule explicitly."""
    with session_scope() as db:
        candidate_ids = [
            row[0]
            for row in db.query(Candidate.id)
            .filter(
                Candidate.auto_advanced.is_(True),
                Candidate.phase2_status == Phase2Status.NOT_SCHEDULED,
            )
            .all()
        ]

    for candidate_id in candidate_ids:
        ok, reason = await schedule_candidate(candidate_id)
        if ok:
            logger.info("Auto-scheduled interview for candidate %s", candidate_id)
        else:
            # Hard failure (no email on file, SMTP not configured, …). Clear the flag so we don't
            # retry every cycle — the candidate stays advanced and the recruiter can schedule
            # them manually once the underlying issue is fixed.
            logger.warning(
                "Auto-schedule failed for candidate %s: %s — leaving for manual scheduling",
                candidate_id, reason,
            )
            with session_scope() as db:
                candidate = db.get(Candidate, candidate_id)
                if candidate is not None:
                    candidate.auto_advanced = False


async def run_forever() -> None:
    logger.info("Interview link sweep worker started (interval=%ss)", _SWEEP_INTERVAL_SECONDS)
    while True:
        try:
            await asyncio.to_thread(_sweep_once)
            await _auto_schedule_pass()
        except Exception:  # noqa: BLE001 — the sweep loop must never die
            logger.exception("Sweep iteration failed")
        await asyncio.sleep(_SWEEP_INTERVAL_SECONDS)
