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
    # which page of the recruitment list is being viewed.
    recruitments_total: int = 0
    candidates: int = 0
    scored: int = 0
    advancing: int = 0
    interviewing: int = 0
    completed: int = 0
    shortlisted: int = 0
