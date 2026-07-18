from datetime import datetime

from pydantic import BaseModel, Field


class RecruitmentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    jd_text: str = Field(min_length=1)


class RecruitmentCounts(BaseModel):
    total_candidates: int = 0
    queued: int = 0
    processing: int = 0
    scored: int = 0
    failed: int = 0
    advanced: int = 0
    # AI-advanced candidates still waiting for the sweep worker to auto-schedule them. Drives the
    # UI's "keep polling" signal so the not_scheduled -> scheduled flip shows up in near real time.
    awaiting_schedule: int = 0
    interview_in_progress: int = 0
    interview_completed: int = 0
    shortlisted: int = 0


class RecruitmentOut(BaseModel):
    id: str
    title: str
    jd_text: str
    status: str
    created_at: datetime
    counts: RecruitmentCounts

    model_config = {"from_attributes": True}


class PaginatedRecruitments(BaseModel):
    items: list[RecruitmentOut]  # one page, newest first
    total: int
    page: int
    page_size: int


class RecruitmentStats(BaseModel):
    # Aggregate totals across ALL recruitments — drives the dashboard's stat tiles independently of
    # which page of the recruitment list is being viewed. NOTE: these are overlapping funnel stages
    # (a shortlisted candidate is also scored), so they are NOT suitable for a pie chart.
    recruitments_total: int = 0
    candidates: int = 0
    scored: int = 0
    advancing: int = 0
    interviewing: int = 0
    completed: int = 0
    shortlisted: int = 0


class FunnelBar(BaseModel):
    # One recruitment's bar-chart datum (overlapping funnel stages).
    title: str
    advancing: int
    completed: int
    shortlisted: int


class PipelineBreakdown(BaseModel):
    # Mutually-exclusive candidate pipeline buckets — partition the page's candidates, so they're
    # safe to render as a pie/donut. The advanced group is split by interview outcome so shortlisted
    # is its own slice (not folded into "advancing").
    shortlisted: int = 0
    interviewed: int = 0  # advanced, interviewed but not shortlisted
    advancing: int = 0  # advanced, interview not yet finalized
    rejected: int = 0  # screened out at Phase 1
    screening: int = 0  # processing, or scored & awaiting a decision / on hold
    failed: int = 0


class PageAnalytics(BaseModel):
    # Chart data scoped to ONE page of recruitments (not the whole DB). Cached server-side; see
    # analytics_charts_cache_seconds. `funnel` drives the bar chart, `pipeline` drives the pie.
    page: int
    page_size: int
    total: int  # total recruitments (for reference / paging)
    candidates: int  # total candidates across this page's recruitments
    funnel: list[FunnelBar]
    pipeline: PipelineBreakdown
