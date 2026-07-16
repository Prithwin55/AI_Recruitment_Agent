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

    model_config = {"from_attributes": True}


class RejectedUpload(BaseModel):
    filename: str
    reason: str


class BulkUploadResult(BaseModel):
    created: list[CandidateOut]
    rejected: list[RejectedUpload]


class DecisionUpdate(BaseModel):
    decision: str = Field(pattern="^(advance|hold|reject)$")
