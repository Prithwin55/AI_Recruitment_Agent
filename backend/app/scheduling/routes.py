import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from shared.config import get_settings
from shared.db import session_scope
from shared.models import (
    Candidate,
    InterviewLanguage,
    InterviewSession,
    Phase1Decision,
    Phase2Status,
    Recruitment,
    TokenStatus,
    User,
)
from shared.security import generate_interview_token

from ..auth.dependencies import get_current_user
from .service import schedule_candidate
from .schemas import (
    FailedCandidate,
    LanguageUpdate,
    PublicInterviewSession,
    ScheduledCandidate,
    ScheduleInterviewsResult,
    StartInterviewResult,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/recruitments", tags=["scheduling"])
public_router = APIRouter(prefix="/interview", tags=["interview-link"])


@router.post("/{recruitment_id}/schedule-interviews", response_model=ScheduleInterviewsResult)
async def schedule_interviews(
    recruitment_id: str,
    current_user: User = Depends(get_current_user),
) -> ScheduleInterviewsResult:
    with session_scope() as db:
        recruitment = db.get(Recruitment, recruitment_id)
        if recruitment is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recruitment not found")

        # Only candidates a recruiter advanced by hand are scheduled here — AI-advanced ones
        # (auto_advanced) are scheduled automatically by the sweep worker. `isnot(True)` also
        # covers legacy rows where the flag is NULL.
        candidates = (
            db.query(Candidate)
            .filter(
                Candidate.recruitment_id == recruitment_id,
                Candidate.phase1_decision == Phase1Decision.ADVANCE,
                Candidate.phase2_status == Phase2Status.NOT_SCHEDULED,
                Candidate.auto_advanced.isnot(True),
            )
            .all()
        )
        candidate_snapshots = [(c.id, c.name, c.email) for c in candidates]

    scheduled: list[ScheduledCandidate] = []
    failed: list[FailedCandidate] = []
    skipped_no_email: list[FailedCandidate] = []

    for candidate_id, name, email in candidate_snapshots:
        if not email:
            skipped_no_email.append(
                FailedCandidate(candidate_id=candidate_id, name=name, reason="No email address on file")
            )
            continue

        ok, reason = await schedule_candidate(candidate_id)
        if ok:
            scheduled.append(ScheduledCandidate(candidate_id=candidate_id, name=name, email=email, calendar_invited=True))
        else:
            failed.append(FailedCandidate(candidate_id=candidate_id, name=name, reason=reason))

    return ScheduleInterviewsResult(scheduled=scheduled, failed=failed, skipped_no_email=skipped_no_email)


def _effective_status(session: InterviewSession) -> str:
    """Re-derive status at read time rather than trusting only the periodic sweep."""
    if session.token_status == TokenStatus.PENDING and datetime.now(timezone.utc) > _aware(session.expires_at):
        return TokenStatus.EXPIRED.value
    return session.token_status.value


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


@public_router.get("/{token}", response_model=PublicInterviewSession)
def get_public_session(token: str) -> PublicInterviewSession:
    with session_scope() as db:
        session_row = db.query(InterviewSession).filter(InterviewSession.token == token).first()
        if session_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid interview link")

        candidate = db.get(Candidate, session_row.candidate_id)
        recruitment = db.get(Recruitment, candidate.recruitment_id) if candidate else None
        first_name = (candidate.name or "").split(" ")[0] if candidate and candidate.name else None

        return PublicInterviewSession(
            token_status=_effective_status(session_row),
            language=session_row.language.value,
            role_title=recruitment.title if recruitment else "",
            candidate_first_name=first_name,
            duration_minutes=session_row.duration_minutes,
            expires_at=session_row.expires_at,
            started_at=session_row.started_at,
        )


@public_router.patch("/{token}/language", response_model=PublicInterviewSession)
def set_language(token: str, payload: LanguageUpdate) -> PublicInterviewSession:
    with session_scope() as db:
        session_row = db.query(InterviewSession).filter(InterviewSession.token == token).first()
        if session_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid interview link")

        if _effective_status(session_row) != TokenStatus.PENDING.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Language can only be changed before the interview starts",
            )

        session_row.language = InterviewLanguage(payload.language)
        db.flush()
        db.refresh(session_row)

        candidate = db.get(Candidate, session_row.candidate_id)
        recruitment = db.get(Recruitment, candidate.recruitment_id) if candidate else None
        first_name = (candidate.name or "").split(" ")[0] if candidate and candidate.name else None

        return PublicInterviewSession(
            token_status=_effective_status(session_row),
            language=session_row.language.value,
            role_title=recruitment.title if recruitment else "",
            candidate_first_name=first_name,
            duration_minutes=session_row.duration_minutes,
            expires_at=session_row.expires_at,
            started_at=session_row.started_at,
        )


@public_router.post("/{token}/start", response_model=StartInterviewResult)
def start_interview(token: str) -> StartInterviewResult:
    """The link only becomes invalid here — not on page load. This is the 'validated by
    starting the meeting, not just clicking the link' boundary."""
    now = datetime.now(timezone.utc)

    with session_scope() as db:
        session_row = db.query(InterviewSession).filter(InterviewSession.token == token).first()
        if session_row is None:
            return StartInterviewResult(success=False, reason="Invalid interview link")

        if session_row.token_status != TokenStatus.PENDING:
            return StartInterviewResult(success=False, reason="This interview has already been started or has ended")

        if now > _aware(session_row.expires_at):
            db.query(InterviewSession).filter(
                InterviewSession.id == session_row.id, InterviewSession.token_status == TokenStatus.PENDING
            ).update({"token_status": TokenStatus.EXPIRED})
            return StartInterviewResult(success=False, reason="This interview link has expired")

        rows = (
            db.query(InterviewSession)
            .filter(InterviewSession.id == session_row.id, InterviewSession.token_status == TokenStatus.PENDING)
            .update({"token_status": TokenStatus.ACTIVE, "started_at": now})
        )
        if rows != 1:
            return StartInterviewResult(success=False, reason="This interview has already been started")

        candidate = db.get(Candidate, session_row.candidate_id)
        if candidate is not None:
            candidate.phase2_status = Phase2Status.IN_PROGRESS

        return StartInterviewResult(success=True, started_at=now)
