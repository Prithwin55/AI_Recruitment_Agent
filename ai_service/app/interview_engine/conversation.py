import re
from typing import AsyncIterator

from anthropic import AsyncAnthropic
from shared.config import get_settings
from shared.models import UsageService
from shared.usage import record_usage_async

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?؟])\s+")
_CONCLUDE_MARKER = "[[END_INTERVIEW]]"

_BASE_SYSTEM_PROMPT = """You are an AI interviewer conducting a live, SPOKEN job interview over voice. \
This is not a chat — the candidate hears your words spoken aloud, and you hear theirs.

Role being interviewed for: {role_title}

Job description:
{jd_text}

Candidate's resume summary (from their application):
{candidate_summary}

Interview language: {language_label}. Speak and respond only in this language.
Target interview length: {duration_minutes} minutes.

How to behave:
- Be warm, conversational, and natural — like a skilled human recruiter, not a rigid Q&A script.
- Ask about specific things from the candidate's resume and probe skills/experience relevant to the \
  job description above. Let their answers steer your follow-ups.
- Keep every turn SHORT: 1-3 sentences, one question at a time — this is spoken conversation, not an essay.
- Respond with ONLY what you would actually say out loud. No stage directions, no markdown, no lists, \
  no headers — plain spoken sentences only.
- You may sometimes be interrupted mid-sentence by the candidate. When that happens you'll see your own \
  previous turn end with "[cut off]" in the conversation so far — respond naturally to what they actually \
  said; don't try to awkwardly resume or finish the old sentence.
- Never fabricate claims about the candidate. Only reference what they've actually told you or what's in \
  their resume summary above.

Ending the interview:
- If — and only if — you judge the conversation has reached a natural conclusion (you've asked what you \
  need to for this role, the candidate has nothing more to add, and you've just delivered a warm closing \
  remark/goodbye), append the exact literal marker {conclude_marker} immediately after your closing words, \
  with no other text after it. Only do this on the turn where you are actually saying goodbye.
- Do not end the interview prematurely — a real conversation needs at least a few substantive exchanges \
  about the candidate's background before it's appropriate to close.
"""


def build_candidate_summary(parsed_profile: dict | None) -> str:
    if not parsed_profile:
        return ""
    parts = []
    if parsed_profile.get("summary"):
        parts.append(parsed_profile["summary"])
    if parsed_profile.get("skills"):
        parts.append("Skills: " + ", ".join(parsed_profile["skills"]))
    if parsed_profile.get("years_experience"):
        parts.append(f"Years of experience: {parsed_profile['years_experience']}")
    if parsed_profile.get("education"):
        parts.append("Education: " + "; ".join(parsed_profile["education"]))
    return "\n".join(parts)


def build_system_prompt(
    role_title: str, jd_text: str, candidate_summary: str, language_label: str, duration_minutes: int
) -> str:
    return _BASE_SYSTEM_PROMPT.format(
        role_title=role_title,
        jd_text=jd_text,
        candidate_summary=candidate_summary or "No resume summary available.",
        language_label=language_label,
        duration_minutes=duration_minutes,
        conclude_marker=_CONCLUDE_MARKER,
    )


def scripted_welcome(candidate_first_name: str | None, role_title: str, language: str) -> str:
    name = f"{candidate_first_name}, " if candidate_first_name else ""
    if language == "ar-OM":
        greeting_name = candidate_first_name or ""
        return (
            f"مرحباً {greeting_name}، شكراً لانضمامك اليوم. أنا المساعد الذكي الذي سيجري معك مقابلة "
            f"لوظيفة {role_title}. لنبدأ — هل يمكنك أن تخبرني قليلاً عن خلفيتك المهنية؟"
        )
    return (
        f"Hi {name}thanks so much for joining today. I'm the AI interviewer for the {role_title} role. "
        f"Let's get started — could you tell me a little about your background?"
    )


_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        settings = get_settings()
        # Higher than the SDK default (2): transient "overloaded_error" (529) responses from
        # Anthropic are meant to be retried, and a live conversation stalling on one is a much
        # worse outcome than a few extra seconds of retry/backoff latency.
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key, max_retries=5)
    return _client


async def stream_claude_sentences(
    system_prompt: str, messages: list[dict], max_tokens: int = 400, tenant_id: str | None = None
) -> AsyncIterator[str]:
    settings = get_settings()
    client = _get_client()

    buffer = ""
    async with client.messages.stream(
        model=settings.anthropic_model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=messages,
    ) as stream:
        async for delta in stream.text_stream:
            buffer += delta
            match = _SENTENCE_BOUNDARY.search(buffer)
            while match:
                sentence = buffer[: match.start()].strip()
                if sentence:
                    yield sentence
                buffer = buffer[match.end() :]
                match = _SENTENCE_BOUNDARY.search(buffer)

        # Meter the finished stream (usage totals only exist once the stream completes).
        try:
            final = await stream.get_final_message()
            await record_usage_async(
                UsageService.LLM,
                tenant_id=tenant_id,
                input_tokens=final.usage.input_tokens,
                output_tokens=final.usage.output_tokens,
                context="conversation",
            )
        except Exception:  # noqa: BLE001 — metering must never break the live conversation
            pass

    remainder = buffer.strip()
    if remainder:
        yield remainder


class ConversationEngine:
    """Holds one interview session's Claude conversation history and produces sentence-streamed
    responses. Doesn't speak anything itself — the orchestrator (interview_ws.py) drives actual
    TTS delivery via turn_taking.py and reports back what was truly said via commit_agent_turn."""

    def __init__(self, base_system_prompt: str, tenant_id: str | None = None) -> None:
        self._base_system_prompt = base_system_prompt
        self._tenant_id = tenant_id
        self.history: list[dict] = []
        self.should_conclude = False

    def add_candidate_turn(self, text: str) -> None:
        self.history.append({"role": "user", "content": text})

    def commit_agent_turn(self, delivered_sentences: list[str], cut_off: bool) -> None:
        text = " ".join(s for s in delivered_sentences if s.strip())
        if cut_off:
            text = f"{text} [cut off]".strip()
        if text:
            self.history.append({"role": "assistant", "content": text})

    def stream_response(self, elapsed_seconds: float, remaining_seconds: float) -> AsyncIterator[str]:
        time_note = (
            f"\n\n[Elapsed: {int(elapsed_seconds // 60)}m{int(elapsed_seconds % 60)}s, "
            f"remaining: {int(remaining_seconds // 60)}m{int(remaining_seconds % 60)}s.]"
        )
        if remaining_seconds <= 120:
            time_note += (
                " Time is nearly up — wrap up warmly now: thank the candidate for their time and let "
                "them know the interview is complete. Do not ask another question. Remember to append "
                f"{_CONCLUDE_MARKER} after your closing remark."
            )
        system_prompt = self._base_system_prompt + time_note
        return self._stream_and_detect_conclusion(system_prompt)

    async def _stream_and_detect_conclusion(self, system_prompt: str) -> AsyncIterator[str]:
        async for sentence in stream_claude_sentences(system_prompt, self.history, tenant_id=self._tenant_id):
            if _CONCLUDE_MARKER in sentence:
                self.should_conclude = True
                sentence = sentence.replace(_CONCLUDE_MARKER, "").strip()
            if sentence:
                yield sentence
