from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from shared.db import session_scope
from shared.models import (
    Candidate,
    CheatingFlag,
    InterviewResult,
    InterviewSession,
    Phase1Decision,
    ProcessingStatus,
    Recruitment,
    User,
)
from shared.storage import save_upload
from shared.storage import resume_path as build_resume_path

from ..auth.dependencies import get_current_user
from .schemas import (
    BulkUploadResult,
    CandidateDetailOut,
    CandidateOut,
    CheatingFlagOut,
    DecisionUpdate,
    InterviewSessionOut,
    RejectedUpload,
    TranscriptTurnOut,
)

router = APIRouter(tags=["candidates"])

_ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".png", ".jpg", ".jpeg"}
_MAX_FILE_SIZE_BYTES = 15 * 1024 * 1024  # 15 MB
_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


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

        results_by_candidate: dict[str, InterviewResult] = {}
        flags_by_candidate: dict[str, list[CheatingFlag]] = {}
        candidate_ids = [c.id for c in candidates]
        if candidate_ids:
            rows = (
                db.query(InterviewSession.candidate_id, InterviewResult)
                .join(InterviewResult, InterviewResult.interview_session_id == InterviewSession.id)
                .filter(InterviewSession.candidate_id.in_(candidate_ids))
                .all()
            )
            for candidate_id, result in rows:
                results_by_candidate[candidate_id] = result

            flag_rows = (
                db.query(InterviewSession.candidate_id, CheatingFlag)
                .join(CheatingFlag, CheatingFlag.interview_session_id == InterviewSession.id)
                .filter(InterviewSession.candidate_id.in_(candidate_ids))
                .order_by(CheatingFlag.created_at.asc())
                .all()
            )
            for candidate_id, flag in flag_rows:
                flags_by_candidate.setdefault(candidate_id, []).append(flag)

        out = []
        for c in candidates:
            item = CandidateOut.model_validate(c)
            result = results_by_candidate.get(c.id)
            if result is not None:
                item.interview_score = result.final_score
                item.interview_decision = result.decision.value if result.decision else None
                item.interview_rationale = result.rationale
                item.interview_summary = result.summary
                item.interview_strengths = result.strengths
                item.interview_weaknesses = result.weaknesses
                item.interview_ability_score = result.ability_score
                item.interview_confidence_score = result.confidence_score
            if c.id in flags_by_candidate:
                item.interview_cheating_flags = [
                    CheatingFlagOut.model_validate(f) for f in flags_by_candidate[c.id]
                ]
            out.append(item)
        return out


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


@router.get("/candidates/{candidate_id}", response_model=CandidateDetailOut)
def get_candidate(
    candidate_id: str,
    current_user: User = Depends(get_current_user),
) -> CandidateDetailOut:
    with session_scope() as db:
        candidate = db.get(Candidate, candidate_id)
        if candidate is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found")

        sessions = (
            db.query(InterviewSession)
            .filter(InterviewSession.candidate_id == candidate_id)
            .order_by(InterviewSession.created_at.desc())
            .all()
        )

        session_ids = [s.id for s in sessions]
        flags_by_session: dict[str, list[CheatingFlag]] = {}
        if session_ids:
            flag_rows = (
                db.query(CheatingFlag)
                .filter(CheatingFlag.interview_session_id.in_(session_ids))
                .order_by(CheatingFlag.created_at.asc())
                .all()
            )
            for flag in flag_rows:
                flags_by_session.setdefault(flag.interview_session_id, []).append(flag)

        session_rows = []
        for s in sessions:
            turns = sorted(s.transcript_turns, key=lambda t: t.sequence_index)
            session_rows.append(
                InterviewSessionOut(
                    id=s.id,
                    language=s.language.value,
                    token_status=s.token_status.value,
                    scheduled_start=s.scheduled_start,
                    started_at=s.started_at,
                    ended_at=s.ended_at,
                    duration_minutes=s.duration_minutes,
                    summary=s.result.summary if s.result else None,
                    strengths=s.result.strengths if s.result else None,
                    weaknesses=s.result.weaknesses if s.result else None,
                    ability_score=s.result.ability_score if s.result else None,
                    confidence_score=s.result.confidence_score if s.result else None,
                    final_score=s.result.final_score if s.result else None,
                    rationale=s.result.rationale if s.result else None,
                    decision=s.result.decision.value if s.result and s.result.decision else None,
                    sentiment_summary=s.result.sentiment_summary if s.result else None,
                    transcript_turns=[TranscriptTurnOut.model_validate(t) for t in turns],
                    cheating_flags=[CheatingFlagOut.model_validate(f) for f in flags_by_session.get(s.id, [])],
                )
            )

        return CandidateDetailOut(
            **CandidateOut.model_validate(candidate).model_dump(),
            recruitment_id=candidate.recruitment_id,
            parsed_profile=candidate.parsed_profile,
            interview_sessions=session_rows,
        )


@router.get("/candidates/{candidate_id}/resume")
def download_resume(
    candidate_id: str,
    current_user: User = Depends(get_current_user),
) -> FileResponse:
    with session_scope() as db:
        candidate = db.get(Candidate, candidate_id)
        if candidate is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found")
        stored_path = candidate.stored_path
        original_filename = candidate.original_filename

    path = Path(stored_path)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume file not found on server")

    media_type = _MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=original_filename)
