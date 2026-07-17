from datetime import datetime

from pydantic import BaseModel, Field


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
    phase2_status: str
    created_at: datetime

    # Final interview outcome, populated once Phase 2 scoring completes — None until then.
    interview_score: float | None = None
    interview_decision: str | None = None
    interview_rationale: str | None = None
    interview_summary: str | None = None
    interview_strengths: list[str] | None = None
    interview_weaknesses: list[str] | None = None
    interview_ability_score: float | None = None
    interview_confidence_score: float | None = None

    model_config = {"from_attributes": True}


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


class CandidateDetailOut(CandidateOut):
    recruitment_id: str
    parsed_profile: dict | None
    interview_sessions: list[InterviewSessionOut]
