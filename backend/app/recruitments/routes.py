from fastapi import APIRouter, Depends, HTTPException, status
from shared.db import session_scope
from shared.models import Candidate, Phase1Decision, Phase2Status, ProcessingStatus, Recruitment, User

from ..auth.dependencies import get_current_user
from .schemas import RecruitmentCounts, RecruitmentCreate, RecruitmentOut

router = APIRouter(prefix="/recruitments", tags=["recruitments"])


def _build_counts(db, recruitment_id: str) -> RecruitmentCounts:
    candidates = db.query(Candidate).filter(Candidate.recruitment_id == recruitment_id).all()
    counts = RecruitmentCounts(total_candidates=len(candidates))
    for c in candidates:
        if c.processing_status == ProcessingStatus.QUEUED:
            counts.queued += 1
        elif c.processing_status == ProcessingStatus.PROCESSING:
            counts.processing += 1
        elif c.processing_status == ProcessingStatus.SCORED:
            counts.scored += 1
        elif c.processing_status == ProcessingStatus.FAILED:
            counts.failed += 1

        if c.phase1_decision == Phase1Decision.ADVANCE:
            counts.advanced += 1

        if c.phase2_status == Phase2Status.IN_PROGRESS:
            counts.interview_in_progress += 1
        elif c.phase2_status == Phase2Status.COMPLETED:
            counts.interview_completed += 1

    return counts


def _to_out(db, recruitment: Recruitment) -> RecruitmentOut:
    return RecruitmentOut(
        id=recruitment.id,
        title=recruitment.title,
        jd_text=recruitment.jd_text,
        status=recruitment.status.value,
        created_at=recruitment.created_at,
        counts=_build_counts(db, recruitment.id),
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


@router.get("", response_model=list[RecruitmentOut])
def list_recruitments(current_user: User = Depends(get_current_user)) -> list[RecruitmentOut]:
    with session_scope() as db:
        recruitments = db.query(Recruitment).order_by(Recruitment.created_at.desc()).all()
        return [_to_out(db, r) for r in recruitments]


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
