from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from shared.db import session_scope
from shared.models import Candidate, Phase1Decision, ProcessingStatus, Recruitment, User
from shared.storage import save_upload
from shared.storage import resume_path as build_resume_path

from ..auth.dependencies import get_current_user
from .schemas import BulkUploadResult, CandidateOut, DecisionUpdate, RejectedUpload

router = APIRouter(tags=["candidates"])

_ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".png", ".jpg", ".jpeg"}
_MAX_FILE_SIZE_BYTES = 15 * 1024 * 1024  # 15 MB


@router.post(
    "/recruitments/{recruitment_id}/candidates/bulk-upload",
    response_model=BulkUploadResult,
    status_code=status.HTTP_201_CREATED,
)
async def bulk_upload_resumes(
    recruitment_id: str,
    files: list[UploadFile],
    current_user: User = Depends(get_current_user),
) -> BulkUploadResult:
    with session_scope() as db:
        recruitment = db.get(Recruitment, recruitment_id)
        if recruitment is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recruitment not found")

    created: list[CandidateOut] = []
    rejected: list[RejectedUpload] = []

    for upload in files:
        filename = upload.filename or "unnamed"
        extension = Path(filename).suffix.lower()
        content = await upload.read()

        if extension not in _ALLOWED_EXTENSIONS:
            rejected.append(RejectedUpload(filename=filename, reason=f"Unsupported file type '{extension}'"))
            continue
        if not content:
            rejected.append(RejectedUpload(filename=filename, reason="File is empty"))
            continue
        if len(content) > _MAX_FILE_SIZE_BYTES:
            rejected.append(RejectedUpload(filename=filename, reason="File exceeds 15 MB limit"))
            continue

        with session_scope() as db:
            candidate = Candidate(
                recruitment_id=recruitment_id,
                original_filename=filename,
                stored_path="",
                processing_status=ProcessingStatus.QUEUED,
            )
            db.add(candidate)
            db.flush()

            destination = build_resume_path(recruitment_id, candidate.id, filename)
            save_upload(destination, content)
            candidate.stored_path = str(destination)

            db.flush()
            db.refresh(candidate)
            created.append(CandidateOut.model_validate(candidate))

    return BulkUploadResult(created=created, rejected=rejected)


@router.get("/recruitments/{recruitment_id}/candidates", response_model=list[CandidateOut])
def list_candidates(
    recruitment_id: str,
    current_user: User = Depends(get_current_user),
) -> list[CandidateOut]:
    with session_scope() as db:
        recruitment = db.get(Recruitment, recruitment_id)
        if recruitment is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recruitment not found")

        candidates = (
            db.query(Candidate)
            .filter(Candidate.recruitment_id == recruitment_id)
            .order_by(Candidate.phase1_score.desc().nullslast(), Candidate.created_at.asc())
            .all()
        )
        return [CandidateOut.model_validate(c) for c in candidates]


@router.patch("/candidates/{candidate_id}/decision", response_model=CandidateOut)
def update_candidate_decision(
    candidate_id: str,
    payload: DecisionUpdate,
    current_user: User = Depends(get_current_user),
) -> CandidateOut:
    with session_scope() as db:
        candidate = db.get(Candidate, candidate_id)
        if candidate is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found")

        candidate.phase1_decision = Phase1Decision(payload.decision)
        db.flush()
        db.refresh(candidate)
        return CandidateOut.model_validate(candidate)
