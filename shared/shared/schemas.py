"""Cross-service data contracts — primarily the shape of structured Claude
outputs, shared between ai_service (producing them) and backend (rendering
them in the recruiter portal API).
"""

from pydantic import BaseModel, Field


class ResumeScoreResult(BaseModel):
    """Structured output of the single Phase-1 Claude call: extraction + JD scoring."""

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    skills: list[str] = Field(default_factory=list)
    years_experience: float | None = None
    education: list[str] = Field(default_factory=list)
    summary: str = ""
    match_score: int = Field(ge=0, le=100)
    rationale: str
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class SentimentResult(BaseModel):
    label: str  # positive | neutral | negative
    score: float = Field(ge=-1.0, le=1.0)


class SentimentSummary(BaseModel):
    average_score: float
    positive_count: int
    neutral_count: int
    negative_count: int


class FinalInterviewScore(BaseModel):
    """Structured output of the post-interview Claude scoring call."""

    score: int = Field(ge=0, le=100)
    rationale: str
    decision: str  # "shortlist" | "reject"
    key_moments: list[str] = Field(default_factory=list)
