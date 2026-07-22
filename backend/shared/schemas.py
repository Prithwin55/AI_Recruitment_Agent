"""Cross-service data contracts — primarily the shape of structured Claude
outputs, shared between ai_service (producing them) and backend (rendering
them in the recruiter portal API).
"""

import re

from pydantic import BaseModel, Field, field_validator

# Claude usually returns array fields as real JSON arrays, but occasionally emits them as a single
# string instead — most often `<item>...</item>` markup, sometimes a newline/bullet list. A strict
# `list[str]` then rejects the whole tool call and the scoring/parse fails. Coerce leniently.
_ITEM_RE = re.compile(r"<item>\s*(.*?)\s*</item>", re.IGNORECASE | re.DOTALL)
_BULLET_PREFIX_RE = re.compile(r"^[\s\-\*••\d.)]+")


def _coerce_str_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return []
        items = _ITEM_RE.findall(s)  # <item>…</item> blocks, if any
        if not items:
            # Fall back to newline splitting, stripping leading bullets / numbering.
            items = [_BULLET_PREFIX_RE.sub("", line).strip() for line in s.splitlines()]
        cleaned = [i.strip() for i in items if i and i.strip()]
        return cleaned or [s]
    if isinstance(value, (list, tuple)):
        return [str(x).strip() for x in value if x is not None and str(x).strip()]
    s = str(value).strip()
    return [s] if s else []


class _LenientListModel(BaseModel):
    """Base for Claude tool-output models: any of these array fields that arrives as a string is
    coerced into a list of strings (see _coerce_str_list). check_fields=False so the one validator
    covers whichever of these fields a given subclass actually declares."""

    @field_validator(
        "strengths", "weaknesses", "key_moments", "skills", "education", "gaps",
        mode="before", check_fields=False,
    )
    @classmethod
    def _coerce_lists(cls, v: object) -> list[str]:
        return _coerce_str_list(v)


class ResumeScoreResult(_LenientListModel):
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


class FinalInterviewScore(_LenientListModel):
    """Structured output of the post-interview Claude scoring call."""

    summary: str
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    ability_score: int = Field(ge=0, le=100)
    confidence_score: int = Field(ge=0, le=100)
    score: int = Field(ge=0, le=100)
    rationale: str
    decision: str  # "shortlist" | "reject"
    key_moments: list[str] = Field(default_factory=list)
