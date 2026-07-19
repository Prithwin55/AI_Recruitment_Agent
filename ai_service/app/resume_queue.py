import asyncio
import logging

from shared.config import get_settings
from shared.db import session_scope
from shared.models import Candidate, ExtractionMethod, Phase1Decision, ProcessingStatus, Recruitment

from .resume_scoring.claude_scorer import score_resume
from .resume_scoring.extract import UnsupportedResumeFormat, build_resume_content

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 3


def _claim_next_batch(limit: int) -> list[str]:
    """Atomically flip up to `limit` queued candidates to processing and return their ids."""
    claimed: list[str] = []
    with session_scope() as db:
        candidates = (
            db.query(Candidate)
            .filter(Candidate.processing_status == ProcessingStatus.QUEUED)
            .order_by(Candidate.created_at.asc())
            .limit(limit)
            .all()
        )
        for candidate in candidates:
            rows = (
                db.query(Candidate)
                .filter(Candidate.id == candidate.id, Candidate.processing_status == ProcessingStatus.QUEUED)
                .update({"processing_status": ProcessingStatus.PROCESSING})
            )
            if rows == 1:
                claimed.append(candidate.id)
    return claimed


async def _process_candidate(candidate_id: str) -> None:
    with session_scope() as db:
        candidate = db.get(Candidate, candidate_id)
        if candidate is None:
            return
        stored_path = candidate.stored_path
        tenant_id = candidate.tenant_id
        recruitment = db.get(Recruitment, candidate.recruitment_id)
        jd_text = recruitment.jd_text if recruitment else ""

    try:
        resume_content = build_resume_content(stored_path)
        result = await score_resume(
            jd_text, resume_content, context=f"resume:{candidate_id}", tenant_id=tenant_id
        )
    except UnsupportedResumeFormat as exc:
        _mark_failed(candidate_id, str(exc))
        return
    except Exception as exc:  # noqa: BLE001 — any Claude/IO failure must not crash the batch
        logger.exception("Phase 1 scoring failed for candidate %s", candidate_id)
        _mark_failed(candidate_id, f"Scoring failed: {exc}")
        return

    with session_scope() as db:
        candidate = db.get(Candidate, candidate_id)
        if candidate is None:
            return
        candidate.name = result.name
        candidate.email = result.email
        candidate.phone = result.phone
        candidate.parsed_profile = {
            "skills": result.skills,
            "years_experience": result.years_experience,
            "education": result.education,
            "summary": result.summary,
        }
        candidate.extraction_method = (
            ExtractionMethod.VISION if resume_content.extraction_method == "vision" else ExtractionMethod.TEXT
        )
        candidate.phase1_score = result.match_score
        candidate.phase1_rationale = result.rationale
        candidate.phase1_strengths = result.strengths
        candidate.phase1_gaps = result.gaps
        candidate.processing_status = ProcessingStatus.SCORED

        # AI decision (only ever applied to PENDING candidates, so a recruiter's manual
        # hold/reject/advance is never overridden on a re-score):
        #   score >= threshold -> ADVANCE (auto-scheduled from there by the backend sweep)
        #   score <  threshold -> REJECT  (the recruiter can still advance them by hand)
        threshold = get_settings().phase1_shortlist_threshold
        if candidate.phase1_decision == Phase1Decision.PENDING:
            if (result.match_score or 0) >= threshold:
                candidate.phase1_decision = Phase1Decision.ADVANCE
                candidate.auto_advanced = True
                logger.info(
                    "Auto-advanced candidate %s (score %.0f >= threshold %s)",
                    candidate_id, result.match_score, threshold,
                )
            else:
                candidate.phase1_decision = Phase1Decision.REJECT
                logger.info(
                    "Auto-rejected candidate %s (score %.0f < threshold %s)",
                    candidate_id, result.match_score or 0, threshold,
                )


def _mark_failed(candidate_id: str, reason: str) -> None:
    with session_scope() as db:
        candidate = db.get(Candidate, candidate_id)
        if candidate is None:
            return
        candidate.processing_status = ProcessingStatus.FAILED
        candidate.processing_error = reason


async def run_forever() -> None:
    settings = get_settings()
    semaphore = asyncio.Semaphore(settings.phase1_max_concurrency)

    async def _bounded(candidate_id: str) -> None:
        async with semaphore:
            await _process_candidate(candidate_id)

    logger.info("Resume queue processor started (max_concurrency=%s)", settings.phase1_max_concurrency)
    while True:
        try:
            claimed = await asyncio.to_thread(_claim_next_batch, settings.phase1_max_concurrency)
            if claimed:
                await asyncio.gather(*(_bounded(cid) for cid in claimed))
        except Exception:  # noqa: BLE001 — the poll loop must never die
            logger.exception("Resume queue poll iteration failed")
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
