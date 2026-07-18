import threading
import time

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
    FunnelBar,
    PageAnalytics,
    PaginatedRecruitments,
    PipelineBreakdown,
    RecruitmentCounts,
    RecruitmentCreate,
    RecruitmentOut,
    RecruitmentStats,
)

router = APIRouter(prefix="/recruitments", tags=["recruitments"])


def _build_counts_batch(db, recruitment_ids: list[str]) -> dict[str, RecruitmentCounts]:
    """Compute per-recruitment counts for a whole set in exactly TWO queries total (never one per
    recruitment): one grouped scan over the candidate dimensions, plus one join for the final
    shortlist decision. Returns a dict keyed by recruitment id."""
    counts = {rid: RecruitmentCounts() for rid in recruitment_ids}
    if not recruitment_ids:
        return counts

    # Single grouped scan over every candidate dimension we need. Grouping by all four columns at
    # once yields a small, bounded number of rows (enum-cardinality) that we fold in Python — one
    # index-only pass instead of a separate COUNT per metric.
    for rid, pstatus, decision, p2status, auto, n in (
        db.query(
            Candidate.recruitment_id,
            Candidate.processing_status,
            Candidate.phase1_decision,
            Candidate.phase2_status,
            Candidate.auto_advanced,
            func.count(),
        )
        .filter(Candidate.recruitment_id.in_(recruitment_ids))
        .group_by(
            Candidate.recruitment_id,
            Candidate.processing_status,
            Candidate.phase1_decision,
            Candidate.phase2_status,
            Candidate.auto_advanced,
        )
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

        if decision == Phase1Decision.ADVANCE:
            c.advanced += n
            if auto and p2status == Phase2Status.NOT_SCHEDULED:
                c.awaiting_schedule += n

        if p2status == Phase2Status.IN_PROGRESS:
            c.interview_in_progress += n
        elif p2status == Phase2Status.COMPLETED:
            c.interview_completed += n

    # Final shortlist decision — the one metric that lives on InterviewResult, so it needs a join.
    for rid, n in (
        db.query(Candidate.recruitment_id, func.count(func.distinct(Candidate.id)))
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


# Cache for the global dashboard stats. Two layers keep it cheap at any scale:
#   1. A short coalescing window (`analytics_cache_seconds`) — within it, repeated polls (many
#      tabs/users) are served from memory with zero DB work.
#   2. A change fingerprint — after that window, a couple of near-free indexed lookups decide
#      whether anything actually changed. The expensive full aggregate re-runs ONLY when it did,
#      so an idle dashboard never re-scans a million-row table on a timer.
_stats_cache: dict[str, object] = {"checked_at": 0.0, "fingerprint": None, "value": None}
_stats_lock = threading.Lock()


def _stats_fingerprint(db) -> str:
    """A cheap signal that moves whenever the stats could change, without scanning the table:
    - MAX(candidates.updated_at): moves on every candidate insert/update (updated_at is indexed,
      so this is an index-tail seek — O(log n), not a scan).
    - COUNT of interview results that have a final decision: moves when post-interview scoring
      finalizes a shortlist/reject (which updates InterviewResult, not the candidate row)."""
    max_updated = db.query(func.max(Candidate.updated_at)).scalar()
    decided = (
        db.query(func.count(InterviewResult.id)).filter(InterviewResult.decision.isnot(None)).scalar() or 0
    )
    return f"{max_updated}|{decided}"


def _compute_stats() -> RecruitmentStats:
    """TWO grouped scans (+ one tiny recruitment count) derive every tile and every mutually-
    exclusive pipeline bucket. No per-metric COUNT fan-out, no whole-table walk per number."""
    with session_scope() as db:
        recruitments_total = db.query(func.count(Recruitment.id)).scalar() or 0

        # One grouped pass over the candidate dimensions derives every tile.
        candidates = scored = interviewing = completed = advance_total = 0
        for pstatus, p2status, n in (
            db.query(
                Candidate.processing_status,
                Candidate.phase2_status,
                func.count(),
            )
            .group_by(Candidate.processing_status, Candidate.phase2_status)
            .all()
        ):
            candidates += n
            if pstatus == ProcessingStatus.SCORED:
                scored += n
            if p2status == Phase2Status.IN_PROGRESS:
                interviewing += n
            elif p2status == Phase2Status.COMPLETED:
                completed += n

        advance_total = (
            db.query(func.count(Candidate.id))
            .filter(Candidate.phase1_decision == Phase1Decision.ADVANCE)
            .scalar()
            or 0
        )
        shortlisted = (
            db.query(func.count(func.distinct(Candidate.id)))
            .select_from(InterviewResult)
            .join(InterviewSession, InterviewResult.interview_session_id == InterviewSession.id)
            .join(Candidate, InterviewSession.candidate_id == Candidate.id)
            .filter(
                Candidate.phase1_decision == Phase1Decision.ADVANCE,
                InterviewResult.decision == FinalDecision.SHORTLIST,
            )
            .scalar()
            or 0
        )

        return RecruitmentStats(
            recruitments_total=recruitments_total,
            candidates=candidates,
            scored=scored,
            advancing=advance_total,
            interviewing=interviewing,
            completed=completed,
            shortlisted=shortlisted,
        )


@router.get("/stats", response_model=RecruitmentStats)
def recruitment_stats(current_user: User = Depends(get_current_user)) -> RecruitmentStats:
    """Aggregate totals across ALL recruitments for the dashboard. The full aggregate is recomputed
    only when the candidate data actually changes (detected by a cheap fingerprint); between changes
    every poll is served from cache, so an idle dashboard never re-scans the table on a timer."""
    ttl = get_settings().analytics_cache_seconds
    now = time.monotonic()

    # Layer 1 — coalescing window: within `ttl` of the last check, serve from memory, no DB at all.
    with _stats_lock:
        cached = _stats_cache["value"]
        if cached is not None and (now - float(_stats_cache["checked_at"])) < ttl:  # type: ignore[arg-type]
            return cached  # type: ignore[return-value]

    # Layer 2 — change detection: a couple of near-free indexed lookups. If nothing changed since
    # the cached value, reuse it WITHOUT the expensive grouped scans.
    with session_scope() as db:
        fingerprint = _stats_fingerprint(db)
    with _stats_lock:
        if _stats_cache["value"] is not None and _stats_cache["fingerprint"] == fingerprint:
            _stats_cache["checked_at"] = time.monotonic()
            return _stats_cache["value"]  # type: ignore[return-value]

    # Data changed (or cold cache) — recompute the full aggregate and remember its fingerprint.
    value = _compute_stats()
    with _stats_lock:
        _stats_cache["checked_at"] = time.monotonic()
        _stats_cache["fingerprint"] = fingerprint
        _stats_cache["value"] = value
    return value


# Page-scoped chart analytics (funnel bar + pipeline pie), cached per (page, page_size) for a long,
# env-configurable window. Unlike the global stats, these are computed only for the recruitments on
# the current page and simply reused until the window lapses — the next request then recomputes.
_page_analytics_cache: dict[tuple[int, int], tuple[float, PageAnalytics]] = {}
_page_analytics_lock = threading.Lock()


def _compute_page_analytics(page: int, page_size: int) -> PageAnalytics:
    """Two grouped scans, scoped to just this page's recruitments, build the per-recruitment funnel
    bars and the mutually-exclusive pipeline breakdown for the pie."""
    with session_scope() as db:
        total = db.query(func.count(Recruitment.id)).scalar() or 0
        page_recruitments = (
            db.query(Recruitment.id, Recruitment.title)
            .order_by(Recruitment.created_at.desc(), Recruitment.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        ids = [r.id for r in page_recruitments]

        # Per-recruitment tallies (for the funnel) + aggregate pipeline buckets (for the pie).
        per: dict[str, dict[str, int]] = {
            rid: {"total": 0, "advanced": 0, "completed": 0, "shortlisted": 0} for rid in ids
        }
        advance_total = rejected = screening = failed = 0
        if ids:
            for rid, pstatus, decision, p2status, n in (
                db.query(
                    Candidate.recruitment_id,
                    Candidate.processing_status,
                    Candidate.phase1_decision,
                    Candidate.phase2_status,
                    func.count(),
                )
                .filter(Candidate.recruitment_id.in_(ids))
                .group_by(
                    Candidate.recruitment_id,
                    Candidate.processing_status,
                    Candidate.phase1_decision,
                    Candidate.phase2_status,
                )
                .all()
            ):
                row = per[rid]
                row["total"] += n
                if decision == Phase1Decision.ADVANCE:
                    row["advanced"] += n
                if p2status == Phase2Status.COMPLETED:
                    row["completed"] += n

                # Mutually-exclusive pipeline bucket by precedence (aggregate across the page).
                if pstatus == ProcessingStatus.FAILED:
                    failed += n
                elif pstatus in (ProcessingStatus.QUEUED, ProcessingStatus.PROCESSING):
                    screening += n
                elif decision == Phase1Decision.ADVANCE:
                    advance_total += n
                elif decision == Phase1Decision.REJECT:
                    rejected += n
                else:  # scored & (pending | hold)
                    screening += n

        # Interview outcomes among advanced candidates in these recruitments.
        shortlisted_total = interviewed_total = 0
        if ids:
            for rid, decision, n in (
                db.query(
                    Candidate.recruitment_id,
                    InterviewResult.decision,
                    func.count(func.distinct(Candidate.id)),
                )
                .select_from(InterviewResult)
                .join(InterviewSession, InterviewResult.interview_session_id == InterviewSession.id)
                .join(Candidate, InterviewSession.candidate_id == Candidate.id)
                .filter(
                    Candidate.recruitment_id.in_(ids),
                    Candidate.phase1_decision == Phase1Decision.ADVANCE,
                    InterviewResult.decision.isnot(None),
                )
                .group_by(Candidate.recruitment_id, InterviewResult.decision)
                .all()
            ):
                if decision == FinalDecision.SHORTLIST:
                    shortlisted_total += n
                    per[rid]["shortlisted"] += n
                elif decision == FinalDecision.REJECT:
                    interviewed_total += n

        funnel = [
            FunnelBar(
                title=r.title,
                advancing=per[r.id]["advanced"],
                completed=per[r.id]["completed"],
                shortlisted=per[r.id]["shortlisted"],
            )
            for r in page_recruitments
            if per[r.id]["total"] > 0
        ]
        pipeline = PipelineBreakdown(
            shortlisted=shortlisted_total,
            interviewed=interviewed_total,
            advancing=max(0, advance_total - shortlisted_total - interviewed_total),
            rejected=rejected,
            screening=screening,
            failed=failed,
        )
        candidates = sum(row["total"] for row in per.values())
        return PageAnalytics(
            page=page,
            page_size=page_size,
            total=total,
            candidates=candidates,
            funnel=funnel,
            pipeline=pipeline,
        )


@router.get("/analytics", response_model=PageAnalytics)
def recruitment_analytics(
    page: int = Query(1, ge=1),
    page_size: int | None = Query(None, ge=1, le=100),
    current_user: User = Depends(get_current_user),
) -> PageAnalytics:
    """Chart data for the current page of recruitments, cached for analytics_charts_cache_seconds.
    Within the window the cached result is returned as-is; the first request after it expires
    recomputes (and re-caches) it."""
    settings = get_settings()
    if page_size is None:
        page_size = max(1, min(settings.recruitments_page_size, 100))
    ttl = settings.analytics_charts_cache_seconds
    key = (page, page_size)
    now = time.monotonic()

    if ttl > 0:
        with _page_analytics_lock:
            hit = _page_analytics_cache.get(key)
            if hit is not None and (now - hit[0]) < ttl:
                return hit[1]

    value = _compute_page_analytics(page, page_size)

    if ttl > 0:
        with _page_analytics_lock:
            _page_analytics_cache[key] = (time.monotonic(), value)
    return value


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
