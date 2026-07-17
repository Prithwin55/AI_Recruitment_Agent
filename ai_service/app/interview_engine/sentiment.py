from anthropic import AsyncAnthropic
from shared.config import get_settings
from shared.schemas import SentimentResult

_TOOL = {
    "name": "record_sentiment",
    "description": "Record the sentiment of a single spoken utterance from a job candidate.",
    "input_schema": {
        "type": "object",
        "properties": {
            "label": {"type": "string", "enum": ["positive", "neutral", "negative"]},
            "score": {
                "type": "number",
                "minimum": -1,
                "maximum": 1,
                "description": "-1 very negative, 0 neutral, 1 very positive",
            },
        },
        "required": ["label", "score"],
    },
}

_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=get_settings().anthropic_api_key)
    return _client


async def analyze_sentiment(text: str) -> SentimentResult:
    settings = get_settings()
    client = _get_client()

    response = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=200,
        system=(
            "Classify the sentiment/confidence/tone of a job candidate's spoken interview answer. "
            "Consider tone, confidence, and enthusiasm, not just literal word choice."
        ),
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "record_sentiment"},
        messages=[{"role": "user", "content": f'Candidate said: "{text}"'}],
    )

    tool_use = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use is None:
        return SentimentResult(label="neutral", score=0.0)
    return SentimentResult(**dict(tool_use.input))
