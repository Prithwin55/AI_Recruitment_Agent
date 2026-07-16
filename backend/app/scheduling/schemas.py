from datetime import datetime

from pydantic import BaseModel, Field


class ScheduledCandidate(BaseModel):
    candidate_id: str
    name: str | None
    email: str | None
    calendar_invited: bool


class FailedCandidate(BaseModel):
    candidate_id: str
    name: str | None
    reason: str


class ScheduleInterviewsResult(BaseModel):
    scheduled: list[ScheduledCandidate]
    failed: list[FailedCandidate]
    skipped_no_email: list[FailedCandidate]


class PublicInterviewSession(BaseModel):
    """What an unauthenticated candidate is allowed to see before joining."""

    token_status: str
    language: str
    role_title: str
    candidate_first_name: str | None
    duration_minutes: int
    expires_at: datetime
    started_at: datetime | None


class LanguageUpdate(BaseModel):
    language: str = Field(pattern="^(en|ar-OM)$")


class StartInterviewResult(BaseModel):
    success: bool
    reason: str | None = None
    started_at: datetime | None = None
