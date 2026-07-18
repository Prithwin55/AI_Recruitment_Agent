import logging
from datetime import datetime, timedelta, timezone

from shared.config import get_settings
from shared.db import session_scope
from shared.models import (
    Candidate,
    InterviewLanguage,
    InterviewSession,
    Phase2Status,
    Recruitment,
    TokenStatus,
)
from shared.security import generate_interview_token

from .calendar_service import create_interview_calendar_event
from .email_service import EmailNotConfigured, send_interview_email

logger = logging.getLogger(__name__)


async def schedule_candidate(candidate_id: str) -> tuple[bool, str]:
    """Schedule one candidate's interview: create the one-time session, email them the join
    link, and (best-effort) add a calendar invite, then mark them SCHEDULED. Returns
    (ok, reason) — reason is a human-readable failure message when ok is False.

    Shared by the manual "schedule interviews" endpoint and the auto-scheduler (sweep worker),
    so both paths behave identically. DB sessions are opened and closed around each network call
    rather than held across awaits (SQLite/WAL locking rule)."""
    settings = get_settings()

    with session_scope() as db:
        candidate = db.get(Candidate, candidate_id)
        if candidate is None:
            return False, "Candidate not found"
        if candidate.phase2_status != Phase2Status.NOT_SCHEDULED:
            return False, "Already scheduled"
        name = candidate.name
        email = candidate.email
        recruitment = db.get(Recruitment, candidate.recruitment_id)
        role_title = recruitment.title if recruitment else ""

    if not email:
        return False, "No email address on file"

    now = datetime.now(timezone.utc)
    token = generate_interview_token()
    expires_at = now + timedelta(days=settings.interview_link_validity_days)

    with session_scope() as db:
        interview_session = InterviewSession(
            candidate_id=candidate_id,
            token=token,
            token_status=TokenStatus.PENDING,
            language=InterviewLanguage.EN,
            scheduled_start=now,
            expires_at=expires_at,
            duration_minutes=settings.interview_duration_minutes,
        )
        db.add(interview_session)
        db.flush()
        session_id = interview_session.id

    join_url = f"{settings.frontend_base_url}/interview/{token}"

    try:
        await send_interview_email(email, name, role_title, join_url, settings.interview_link_validity_days)
    except EmailNotConfigured as exc:
        with session_scope() as db:
            db.query(InterviewSession).filter(InterviewSession.id == session_id).delete()
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to send interview email to candidate %s", candidate_id)
        with session_scope() as db:
            db.query(InterviewSession).filter(InterviewSession.id == session_id).delete()
        return False, f"Email send failed: {exc}"

    calendar_event_id = await create_interview_calendar_event(
        email, name, role_title, join_url, now, settings.interview_duration_minutes
    )

    with session_scope() as db:
        interview_session = db.get(InterviewSession, session_id)
        if interview_session is not None:
            interview_session.calendar_event_id = calendar_event_id
        candidate = db.get(Candidate, candidate_id)
        if candidate is not None:
            candidate.phase2_status = Phase2Status.SCHEDULED

    return True, ""
