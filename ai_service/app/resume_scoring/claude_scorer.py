from anthropic import AsyncAnthropic
from shared.config import get_settings
from shared.schemas import ResumeScoreResult

from .extract import ResumeContent

_SYSTEM_PROMPT = """You are an expert technical recruiter screening a resume against a specific job \
description. Read the resume content provided (as text or as the attached document/image) and the job \
description, then call record_resume_score with your structured assessment.

Be honest and specific. match_score should reflect genuine fit against the job description's actual \
requirements — do not inflate scores. rationale must reference concrete details from the resume (specific \
skills, years of experience, past roles) and explain how they do or don't meet the JD's requirements. If the \
resume is clearly unrelated to the role, score it low and say so plainly."""

_TOOL = {
    "name": "record_resume_score",
    "description": "Record structured extraction and job-description match scoring for a resume.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Candidate full name, empty string if not found"},
            "email": {"type": "string", "description": "Candidate email address, empty string if not found"},
            "phone": {"type": "string", "description": "Candidate phone number, empty string if not found"},
            "skills": {"type": "array", "items": {"type": "string"}, "description": "Key skills/technologies"},
            "years_experience": {
                "type": "number",
                "description": "Total years of relevant professional experience; 0 if entry-level/unclear",
            },
            "education": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Education entries, e.g. 'BSc Computer Science, XYZ University, 2019'",
            },
            "summary": {"type": "string", "description": "2-3 sentence summary of the candidate's background"},
            "match_score": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "How well this resume matches the job description, 0-100",
            },
            "rationale": {
                "type": "string",
                "description": "Specific explanation of the score, referencing concrete resume details against the JD",
            },
            "strengths": {"type": "array", "items": {"type": "string"}},
            "gaps": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["match_score", "rationale"],
    },
}

_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


async def score_resume(jd_text: str, resume: ResumeContent) -> ResumeScoreResult:
    settings = get_settings()
    client = _get_client()

    prompt_text = f"Job description:\n\n{jd_text}\n\nNow assess the resume provided below/attached."

    response = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=2048,
        system=_SYSTEM_PROMPT,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "record_resume_score"},
        messages=[
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt_text}] + resume.content_blocks,
            }
        ],
    )

    tool_use = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use is None:
        raise RuntimeError("Claude did not return a structured score")

    raw = dict(tool_use.input)
    for field in ("name", "email", "phone"):
        if raw.get(field) == "":
            raw[field] = None

    return ResumeScoreResult(**raw)
