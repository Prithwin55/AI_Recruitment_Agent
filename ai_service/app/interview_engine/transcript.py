import asyncio
import logging
from datetime import datetime

from shared.db import session_scope
from shared.models import Speaker, TranscriptTurn
from shared.storage import transcript_path

from .sentiment import analyze_sentiment

logger = logging.getLogger(__name__)


class TranscriptRecorder:
    """Writes a human-readable .txt transcript turn-by-turn and mirrors each turn into the
    TranscriptTurn table. Candidate turns get fire-and-forget sentiment analysis that never
    blocks the live conversation loop."""

    def __init__(self, session_id: str, tenant_id: str | None = None) -> None:
        self.session_id = session_id
        self.tenant_id = tenant_id
        self.path = transcript_path(session_id)
        self._sequence = 0

    def _append_line(self, label: str, text: str, cut_off: bool) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        suffix = " [cut off]" if cut_off else ""
        with self.path.open("a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {label}: {text}{suffix}\n")

    def record_agent_turn(self, text: str, turn_id: int, cut_off: bool) -> None:
        if not text.strip():
            return
        self._append_line("Agent", text, cut_off)
        self._sequence += 1
        with session_scope() as db:
            db.add(
                TranscriptTurn(
                    interview_session_id=self.session_id,
                    speaker=Speaker.AGENT,
                    text=text,
                    turn_id=turn_id,
                    sequence_index=self._sequence,
                    cut_off=cut_off,
                )
            )

    def record_candidate_turn(self, text: str, turn_id: int) -> None:
        if not text.strip():
            return
        self._append_line("Candidate", text, False)
        self._sequence += 1
        with session_scope() as db:
            row = TranscriptTurn(
                interview_session_id=self.session_id,
                speaker=Speaker.CANDIDATE,
                text=text,
                turn_id=turn_id,
                sequence_index=self._sequence,
                cut_off=False,
            )
            db.add(row)
            db.flush()
            turn_row_id = row.id

        asyncio.create_task(self._tag_sentiment(turn_row_id, text))

    async def _tag_sentiment(self, turn_row_id: str, text: str) -> None:
        try:
            result = await analyze_sentiment(text, tenant_id=self.tenant_id)
        except Exception:  # noqa: BLE001 — sentiment is best-effort, never fatal
            logger.exception("Sentiment analysis failed for transcript turn %s", turn_row_id)
            return

        with session_scope() as db:
            row = db.get(TranscriptTurn, turn_row_id)
            if row is not None:
                row.sentiment_label = result.label
                row.sentiment_score = result.score
