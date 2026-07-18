from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from shared.config import get_settings
from shared.db import session_scope
from shared.models import (
    Candidate,
    FinalDecision,
    InterviewResult,
    InterviewSession,
    Phase1Decision,
    Phase2Status,
    ProcessingStatus,
    Recruitment,
    User,
)

from ..auth.dependencies import get_current_user
from .schemas import (
    PaginatedRecruitments,
    RecruitmentCounts,
    RecruitmentCreate,
    RecruitmentOut,
    RecruitmentStats,
)

router = APIRouter(prefix="/recruitments", tags=["recruitments"])


def _build_counts_batch(db, recruitment_ids: list[str]) -> dict[str, RecruitmentCounts]:
    """Compute per-recruitment counts for a whole set in a constant number of grouped queries
    (never one query per recruitment). Returns a dict keyed by recruitment id."""
    counts = {rid: RecruitmentCounts() for rid in recruitment_ids}
    if not recruitment_ids:
        return counts

    # Processing-status breakdown + total candidates.
    for rid, pstatus, n in (
        db.query(Candidate.recruitment_id, Candidate.processing_status, func.count())
        .filter(Candidate.recruitment_id.in_(recruitment_ids))
        .group_by(Candidate.recruitment_id, Candidate.processing_status)
        .all()
    ):
        c = counts[rid]
        c.total_candidates += n
        if pstatus == ProcessingStatus.QUEUED:
            c.queued += n
        elif pstatus == ProcessingStatus.PROCESSING:
            c.processing += n
        elif pstatus == ProcessingStatus.SCORED:
            c.scored += n
        elif pstatus == ProcessingStatus.FAILED:
            c.failed += n

    # Advanced (Phase-1 shortlisted).
    for rid, n in (
        db.query(Candidate.recruitment_id, func.count())
        .filter(
            Candidate.recruitment_id.in_(recruitment_ids),
            Candidate.phase1_decision == Phase1Decision.ADVANCE,
        )
        .group_by(Candidate.recruitment_id)
        .all()
    ):
        counts[rid].advanced += n

    # AI-advanced but still awaiting the auto-schedule sweep.
    for rid, n in (
        db.query(Candidate.recruitment_id, func.count())
        .filter(
            Candidate.recruitment_id.in_(recruitment_ids),
            Candidate.phase1_decision == Phase1Decision.ADVANCE,
            Candidate.auto_advanced.is_(True),
            Candidate.phase2_status == Phase2Status.NOT_SCHEDULED,
        )
        .group_by(Candidate.recruitment_id)
        .all()
    ):
        counts[rid].awaiting_schedule += n

    # Phase-2 in-progress / completed.
    for rid, p2status, n in (
        db.query(Candidate.recruitment_id, Candidate.phase2_status, func.count())
        .filter(
            Candidate.recruitment_id.in_(recruitment_ids),
            Candidate.phase2_status.in_([Phase2Status.IN_PROGRESS, Phase2Status.COMPLETED]),
        )
        .group_by(Candidate.recruitment_id, Candidate.phase2_status)
        .all()
    ):
        if p2status == Phase2Status.IN_PROGRESS:
            counts[rid].interview_in_progress += n
        elif p2status == Phase2Status.COMPLETED:
            counts[rid].interview_completed += n

    # Final shortlist decision.
    for rid, n in (
        db.query(Candidate.recruitment_id, func.count())
        .select_from(InterviewResult)
        .join(InterviewSession, InterviewResult.interview_session_id == InterviewSession.id)
        .join(Candidate, InterviewSession.candidate_id == Candidate.id)
        .filter(
            Candidate.recruitment_id.in_(recruitment_ids),
            InterviewResult.decision == FinalDecision.SHORTLIST,
        )
        .group_by(Candidate.recruitment_id)
        .all()
    ):
        counts[rid].shortlisted += n

    return counts


def _to_out(db, recruitment: Recruitment) -> RecruitmentOut:
    counts = _build_counts_batch(db, [recruitment.id])[recruitment.id]
    return RecruitmentOut(
        id=recruitment.id,
        title=recruitment.title,
        jd_text=recruitment.jd_text,
        status=recruitment.status.value,
        created_at=recruitment.created_at,
        counts=counts,
    )


@router.post("", response_model=RecruitmentOut, status_code=status.HTTP_201_CREATED)
def create_recruitment(
    payload: RecruitmentCreate,
    current_user: User = Depends(get_current_user),
) -> RecruitmentOut:
    with session_scope() as db:
        recruitment = Recruitment(
            title=payload.title,
            jd_text=payload.jd_text,
            created_by=current_user.id,
        )
        db.add(recruitment)
        db.flush()
        db.refresh(recruitment)
        return _to_out(db, recruitment)


@router.get("", response_model=PaginatedRecruitments)
def list_recruitments(
    page: int = Query(1, ge=1),
    page_size: int | None = Query(None, ge=1, le=100),
    current_user: User = Depends(get_current_user),
) -> PaginatedRecruitments:
    # Page size defaults to the env-configured value; an explicit query param can still override it.
    if page_size is None:
        page_size = max(1, min(get_settings().recruitments_page_size, 100))
    with session_scope() as db:
        total = db.query(func.count(Recruitment.id)).scalar() or 0
        recruitments = (
            db.query(Recruitment)
            .order_by(Recruitment.created_at.desc(), Recruitment.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        # Batch the per-recruitment counts for this page — constant query count, no N+1.
        counts_by_id = _build_counts_batch(db, [r.id for r in recruitments])
        items = [
            RecruitmentOut(
                id=r.id,
                title=r.title,
                jd_text=r.jd_text,
                status=r.status.value,
                created_at=r.created_at,
                counts=counts_by_id[r.id],
            )
            for r in recruitments
        ]
        return PaginatedRecruitments(items=items, total=total, page=page, page_size=page_size)


@router.get("/stats", response_model=RecruitmentStats)
def recruitment_stats(current_user: User = Depends(get_current_user)) -> RecruitmentStats:
    """Aggregate totals across ALL recruitments for the dashboard's stat tiles — a handful of
    bounded COUNT queries, independent of how the list is paginated."""
    with session_scope() as db:
        def _count(*criteria) -> int:
            q = db.query(func.count(Candidate.id))
            for c in criteria:
                q = q.filter(c)
            return q.scalar() or 0

        shortlisted = (
            db.query(func.count(InterviewResult.id))
            .join(InterviewSession, InterviewResult.interview_session_id == InterviewSession.id)
            .filter(InterviewResult.decision == FinalDecision.SHORTLIST)
            .scalar()
            or 0
        )
        return RecruitmentStats(
            recruitments_total=db.query(func.count(Recruitment.id)).scalar() or 0,
            candidates=_count(),
            scored=_count(Candidate.processing_status == ProcessingStatus.SCORED),
            advancing=_count(Candidate.phase1_decision == Phase1Decision.ADVANCE),
            interviewing=_count(Candidate.phase2_status == Phase2Status.IN_PROGRESS),
            completed=_count(Candidate.phase2_status == Phase2Status.COMPLETED),
            shortlisted=shortlisted,
        )


@router.get("/{recruitment_id}", response_model=RecruitmentOut)
def get_recruitment(
    recruitment_id: str,
    current_user: User = Depends(get_current_user),
) -> RecruitmentOut:
    with session_scope() as db:
        recruitment = db.get(Recruitment, recruitment_id)
        if recruitment is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recruitment not found")
        return _to_out(db, recruitment)
