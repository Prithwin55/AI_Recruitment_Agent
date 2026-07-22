import asyncio
import logging
from pathlib import Path

from anthropic import AsyncAnthropic
from shared.config import get_settings
from shared.db import session_scope
from shared.models import (
    Candidate,
    FinalDecision,
    InterviewResult,
    InterviewSession,
    Recruitment,
    TranscriptTurn,
    UsageService,
)
from shared.schemas import FinalInterviewScore
from shared.usage import record_usage_async

from ..interview_engine.conversation import build_candidate_summary

logger = logging.getLogger(__name__)

# Best-effort wait for any still-in-flight fire-and-forget sentiment analysis on the final
# candidate turn(s) to land before we aggregate — sentiment calls are cheap/fast but async
# and not otherwise synchronized with the end-of-interview trigger.
_SENTIMENT_SETTLE_DELAY_S = 4

_TOOL = {
    "name": "record_interview_score",
    "description": "Record a final hiring recommendation after reviewing a completed interview.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "2-4 sentence overall summary of who this candidate is and how the interview went",
            },
            "strengths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Concrete strengths demonstrated in the interview and/or resume, specific to this role",
            },
            "weaknesses": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Concrete weaknesses, gaps, or concerns — specific, not generic",
            },
            "ability_score": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "Technical/role competence demonstrated — how well their skills and experience match what this job needs",
            },
            "confidence_score": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "How confident, composed, and articulate the candidate came across in their spoken answers — informed by the sentiment data and how they handled the conversation, not just word choice",
            },
            "score": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "Overall hiring score combining ability, confidence, and resume/JD fit",
            },
            "rationale": {
                "type": "string",
                "description": (
                    "The reason for the decision, required either way: if shortlisting, explain specifically "
                    "why this candidate should be hired; if rejecting, explain specifically why not. Reference "
                    "concrete moments from the transcript and resume/JD fit, not generic statements."
                ),
            },
            "decision": {"type": "string", "enum": ["shortlist", "reject"]},
            "key_moments": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Short quotes or close paraphrases of pivotal moments in the interview",
            },
        },
        "required": [
            "summary",
            "strengths",
            "weaknesses",
            "ability_score",
            "confidence_score",
            "score",
            "rationale",
            "decision",
        ],
    },
}

_SYSTEM_PROMPT = """You are a senior recruiter making the final hiring recommendation after a completed \
AI-conducted interview. You're given the job description, the candidate's resume summary, the full \
interview transcript, and a sentiment aggregate from their spoken answers.

Score genuinely. It is completely fine — and expected when warranted — to recommend "reject" if the \
candidate doesn't clearly meet the bar. Do not feel obligated to shortlist anyone; only recommend \
"shortlist" for candidates who are a genuinely strong fit for this specific role.

Assess ability (role/technical competence) and confidence (composure, articulateness, how they carried \
themselves — informed by the sentiment data) as separate signals, not one blended guess.

A clear reason is REQUIRED regardless of the outcome — if you shortlist, say specifically why this \
candidate should be hired; if you reject, say specifically why not. Ground everything in SPECIFIC moments \
from the transcript (quote or closely paraphrase what the candidate actually said) and how well their \
background matches the job description — never generic filler."""


def _aggregate_sentiment(turns: list[TranscriptTurn]) -> dict:
    scored = [t for t in turns if t.sentiment_score is not None]
    if not scored:
        return {"average_score": 0.0, "positive_count": 0, "neutral_count": 0, "negative_count": 0}
    return {
        "average_score": sum(t.sentiment_score for t in scored) / len(scored),
        "positive_count": sum(1 for t in scored if t.sentiment_label == "positive"),
        "neutral_count": sum(1 for t in scored if t.sentiment_label == "neutral"),
        "negative_count": sum(1 for t in scored if t.sentiment_label == "negative"),
    }


async def _score_interview(
    transcript_text: str,
    jd_text: str,
    candidate_summary: str,
    sentiment_summary: dict,
    context: str | None = None,
) -> FinalInterviewScore:
    settings = get_settings()
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    user_prompt = f"""Job description:
{jd_text}

Candidate resume summary:
{candidate_summary or "Not available."}

Sentiment aggregate across the candidate's spoken answers (score range -1 to 1):
{sentiment_summary}

Full interview transcript:
{transcript_text}

Now call record_interview_score with your final assessment."""

    response = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=2048,
        system=_SYSTEM_PROMPT,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "record_interview_score"},
        messages=[{"role": "user", "content": user_prompt}],
    )

    await record_usage_async(
        UsageService.LLM,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        context=context,
    )

    tool_use = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use is None:
        raise RuntimeError("Claude did not return a structured interview score")
    return FinalInterviewScore(**dict(tool_use.input))


async def run_post_interview_analysis(session_id: str, settle: bool = True) -> None:
    # `settle` waits for the final turns' still-in-flight fire-and-forget sentiment to land before
    # aggregating — needed right after a LIVE interview, but pointless when re-scoring an old one
    # whose sentiment settled long ago (the startup/backlog rescorer passes settle=False).
    if settle:
        await asyncio.sleep(_SENTIMENT_SETTLE_DELAY_S)

    with session_scope() as db:
        session_row = db.get(InterviewSession, session_id)
        if session_row is None:
            return
        candidate = db.get(Candidate, session_row.candidate_id)
        recruitment = db.get(Recruitment, candidate.recruitment_id) if candidate else None
        turns = (
            db.query(TranscriptTurn)
            .filter(TranscriptTurn.interview_session_id == session_id)
            .order_by(TranscriptTurn.sequence_index)
            .all()
        )
        jd_text = recruitment.jd_text if recruitment else ""
        candidate_summary = build_candidate_summary(candidate.parsed_profile if candidate else None)
        sentiment_summary = _aggregate_sentiment(turns)
        result_row = (
            db.query(InterviewResult).filter(InterviewResult.interview_session_id == session_id).first()
        )
        transcript_path = Path(result_row.transcript_file_path) if result_row else None

    transcript_text = ""
    if transcript_path is not None and transcript_path.exists():
        transcript_text = transcript_path.read_text(encoding="utf-8")
    if not transcript_text.strip():
        logger.warning("Interview %s ended with no transcript — skipping scoring", session_id)
        return

    try:
        score = await _score_interview(
            transcript_text, jd_text, candidate_summary, sentiment_summary,
            context=f"interview:{session_id}",
        )
    except Exception:  # noqa: BLE001
        logger.exception("Post-interview scoring failed for session %s", session_id)
        return

    with session_scope() as db:
        result_row = (
            db.query(InterviewResult).filter(InterviewResult.interview_session_id == session_id).first()
        )
        if result_row is not None:
            result_row.summary = score.summary
            result_row.strengths = score.strengths
            result_row.weaknesses = score.weaknesses
            result_row.ability_score = score.ability_score
            result_row.confidence_score = score.confidence_score
            result_row.final_score = score.score
            result_row.rationale = score.rationale
            result_row.sentiment_summary = sentiment_summary
            result_row.decision = FinalDecision.SHORTLIST if score.decision == "shortlist" else FinalDecision.REJECT
