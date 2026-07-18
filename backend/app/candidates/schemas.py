from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class CheatingFlagOut(BaseModel):
    kind: str
    detail: str | None
    at_seconds: float | None
    created_at: datetime

    model_config = {"from_attributes": True}


class CandidateOut(BaseModel):
    id: str
    original_filename: str
    name: str | None
    email: str | None
    phone: str | None
    processing_status: str
    processing_error: str | None
    phase1_score: float | None
    phase1_rationale: str | None
    phase1_strengths: list[str] | None
    phase1_gaps: list[str] | None
    phase1_decision: str
    auto_advanced: bool = False
    phase2_status: str
    created_at: datetime

    @field_validator("auto_advanced", mode="before")
    @classmethod
    def _coerce_auto_advanced(cls, v: object) -> bool:
        # Candidates that existed before this column was added carry NULL for it (SQLite's
        # ADD COLUMN doesn't backfill). Treat that as False rather than failing serialization.
        return bool(v)

    # Final interview outcome, populated once Phase 2 scoring completes — None until then.
    interview_score: float | None = None
    interview_decision: str | None = None
    interview_rationale: str | None = None
    interview_summary: str | None = None
    interview_strengths: list[str] | None = None
    interview_weaknesses: list[str] | None = None
    interview_ability_score: float | None = None
    interview_confidence_score: float | None = None
    # Integrity/proctoring flags raised during the interview — informational only, never part
    # of scoring. None until an interview has run; an empty list means none were raised.
    interview_cheating_flags: list[CheatingFlagOut] | None = None

    model_config = {"from_attributes": True}


class PaginatedCandidates(BaseModel):
    # Both groups are paginated independently at the same env-configured page size and filtered by
    # the same search. `interviews` is the shortlisted (advanced) set, ordered ongoing/scheduled
    # first; `pool` is the not-shortlisted set, ordered newest-activity first. `ready_to_schedule_total`
    # is the count of shortlisted candidates still awaiting a scheduled interview across ALL pages
    # (so the "Schedule N pending" button reflects the true total, not just the visible page).
    interviews: list[CandidateOut]
    interviews_total: int
    interviews_page: int
    ready_to_schedule_total: int
    pool: list[CandidateOut]
    pool_total: int
    pool_page: int
    page_size: int


class RejectedUpload(BaseModel):
    filename: str
    reason: str


class BulkUploadResult(BaseModel):
    created: list[CandidateOut]
    rejected: list[RejectedUpload]


class DecisionUpdate(BaseModel):
    decision: str = Field(pattern="^(advance|hold|reject)$")


class TranscriptTurnOut(BaseModel):
    speaker: str
    text: str
    cut_off: bool
    sentiment_label: str | None
    sentiment_score: float | None
    created_at: datetime

    model_config = {"from_attributes": True}


class InterviewSessionOut(BaseModel):
    id: str
    language: str
    token_status: str
    scheduled_start: datetime | None
    started_at: datetime | None
    ended_at: datetime | None
    duration_minutes: int
    summary: str | None
    strengths: list[str] | None
    weaknesses: list[str] | None
    ability_score: float | None
    confidence_score: float | None
    final_score: float | None
    rationale: str | None
    decision: str | None
    sentiment_summary: dict | None
    transcript_turns: list[TranscriptTurnOut]
    cheating_flags: list[CheatingFlagOut] = []


class CandidateDetailOut(CandidateOut):
    recruitment_id: str
    parsed_profile: dict | None
    interview_sessions: list[InterviewSessionOut]
