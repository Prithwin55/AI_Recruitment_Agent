"""Re-run post-interview scoring for COMPLETED interviews that were never scored.

An interview whose one-shot scoring failed (e.g. a Claude formatting quirk that the strict schema
rejected) keeps its InterviewResult row with the transcript saved but score/decision/summary NULL.
There's no automatic retry, so this maintenance command finds those rows and re-scores them. It's
idempotent — already-scored interviews are skipped, and it's safe to run more than once.

Run it in the ai_service environment (it needs ANTHROPIC_API_KEY and the storage volume with the
transcript files). Add --dry-run to just list what WOULD be re-scored without calling the API.

    # bare metal / venv, from the ai_service dir:
    python -m app.post_interview.reprocess            # re-score the backlog
    python -m app.post_interview.reprocess --dry-run  # list only, no API calls / no writes

    # docker compose:
    docker compose exec ai_service python -m app.post_interview.reprocess
"""
import argparse
import asyncio
import logging

from shared.db import session_scope
from shared.models import InterviewResult

from .analyzer import run_post_interview_analysis

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reprocess")


def _unscored_session_ids() -> list[str]:
    # An InterviewResult row exists only after finalize() (interview COMPLETED). final_score is NULL
    # until scoring succeeds, so "row exists but final_score IS NULL" == completed-but-unscored.
    with session_scope() as db:
        return [
            r.interview_session_id
            for r in db.query(InterviewResult).filter(InterviewResult.final_score.is_(None)).all()
        ]


async def rescore_pending() -> int:
    """Re-score every completed-but-unscored interview, sequentially (one Claude call at a time, to
    stay gentle on rate limits). Returns how many were newly scored. Safe to call repeatedly and on
    startup — a cheap no-op (one empty query) when there's no backlog."""
    session_ids = _unscored_session_ids()
    if not session_ids:
        return 0

    logger.info("Scoring %d interview(s) left unscored by a previous run/failure", len(session_ids))
    scored = 0
    for sid in session_ids:
        try:
            # settle=False: these interviews ended long ago, no sentiment is still in flight.
            await run_post_interview_analysis(sid, settle=False)
        except Exception:  # noqa: BLE001 — one bad session must not stop the batch
            logger.exception("Reprocess crashed for %s", sid)
            continue
        # Read the outcome INSIDE the session — the row detaches once the block exits (attributes
        # expire on commit), so pull the plain values out here.
        with session_scope() as db:
            row = (
                db.query(InterviewResult)
                .filter(InterviewResult.interview_session_id == sid)
                .first()
            )
            final_score = row.final_score if row is not None else None
            decision = row.decision.value if (row is not None and row.decision) else None
        if final_score is not None:
            scored += 1
            logger.info("Scored %s -> %s (%s)", sid, final_score, decision)
        else:
            logger.warning("Still unscored after retry: %s (transcript missing, or scoring failed)", sid)

    logger.info("Backlog scoring done. Scored %d/%d.", scored, len(session_ids))
    return scored


async def main(dry_run: bool = False) -> None:
    if dry_run:
        session_ids = _unscored_session_ids()
        if not session_ids:
            logger.info("No unscored completed interviews found — nothing to do.")
            return
        logger.info("%d completed interview(s) need scoring:", len(session_ids))
        for sid in session_ids:
            logger.info("  - %s", sid)
        logger.info("--dry-run: no scoring performed.")
        return

    await rescore_pending()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Re-score completed-but-unscored interviews.")
    parser.add_argument("--dry-run", action="store_true", help="list what would be re-scored; no API calls")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
