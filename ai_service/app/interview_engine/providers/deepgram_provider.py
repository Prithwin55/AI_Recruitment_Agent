import asyncio
import logging
from collections import Counter
from typing import Awaitable, Callable

from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType
from deepgram.listen.v1.types import (
    ListenV1Results,
    ListenV1SpeechStarted,
    ListenV1UtteranceEnd,
)
from shared.config import get_settings

from .base import (
    AgentSentenceFlushedEvent,
    FinalTranscriptEvent,
    InterimTranscriptEvent,
    SpeechProvider,
    SpeechStartedEvent,
    UtteranceEndEvent,
)

logger = logging.getLogger(__name__)

_LISTEN_MODEL = "nova-3"
_SAMPLE_RATE = 16000
# Upper bound on how long one sentence may take to synthesize + play on the CLIENT before we
# give up waiting for its "done playing" ack. Generous because the very first sentence of a
# session also triggers the one-time ~180MB Pocket TTS model download in the browser; every
# later sentence acks in a few seconds. This is only a stuck-client safety net.
_PLAYBACK_ACK_TIMEOUT_S = 120


class DeepgramProvider(SpeechProvider):
    """English interview provider. Deepgram does the Listen (STT) side, exactly as before — one
    persistent WebSocket fed live mic audio, with diarization on for the "multiple voices"
    integrity signal.

    The Speak (TTS) side no longer runs on the server at all. Text-to-speech now happens in the
    candidate's BROWSER via Pocket TTS (WASM) — see frontend/src/lib/interview/pocketTts.ts and
    the client-driven design in interview_ws.py. This eliminates all server-side TTS work, so N
    concurrent interviews create zero synthesis load/lag on the server (the whole reason for the
    change: ElevenLabs/Deepgram/Azure TTS all competed for server resources per session).

    speak() is therefore a thin bridge: it asks the orchestrator to send the sentence text to
    the client (`on_speak_text`), then blocks until the client reports it has finished PLAYING
    that sentence. Blocking for the real playback duration is deliberate — it keeps the
    turn-taking state machine in AGENT_SPEAKING for exactly as long as the candidate is actually
    hearing the agent, which is what makes barge-in detection correct (unchanged from the paced
    server-TTS design, just with the timing signal coming from the client instead of from our
    own audio pacing). Interruption unblocks the pending speak() immediately; the client is told
    to stop via the orchestrator's existing agent_interrupted message.
    """

    def __init__(self, on_speak_text: Callable[[str, int], Awaitable[None]] | None = None) -> None:
        super().__init__()
        settings = get_settings()
        self._client = AsyncDeepgramClient(api_key=settings.deepgram_api_key)
        # Trailing silence before the candidate's turn is finalized — see config. Deepgram's
        # UtteranceEnd (what we treat as end-of-turn) fires after this much word-gap silence.
        self._utterance_end_ms = settings.interview_end_of_turn_silence_ms

        # Bridge to the client TTS. on_speak_text(text, seq) sends the sentence to the browser;
        # the browser plays it and acks by seq, which resolves the matching event below.
        self._on_speak_text = on_speak_text
        self._speak_seq = 0
        self._playback_acks: dict[int, asyncio.Event] = {}

        self._listen_cm = None
        self._listen_conn = None
        self._listen_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._listen_cm = self._client.listen.v1.connect(
            model=_LISTEN_MODEL,
            encoding="linear16",
            sample_rate=_SAMPLE_RATE,
            channels=1,
            interim_results=True,
            endpointing=300,
            utterance_end_ms=self._utterance_end_ms,
            vad_events=True,
            smart_format=True,
            # Speaker labels per word — used only for the "multiple voices" integrity signal
            # (see _on_listen_message). Does not affect transcription/turn-taking.
            diarize=True,
        )
        self._listen_conn = await self._listen_cm.__aenter__()
        self._listen_conn.on(EventType.MESSAGE, self._on_listen_message)
        self._listen_conn.on(EventType.ERROR, lambda exc: logger.error("Deepgram listen error: %s", exc))
        self._listen_task = asyncio.create_task(self._listen_conn.start_listening())

    async def _on_listen_message(self, message) -> None:
        if isinstance(message, ListenV1Results):
            alt = message.channel.alternatives[0] if message.channel.alternatives else None
            text = alt.transcript if alt else ""
            if not text:
                return
            if message.is_final:
                # Attach diarization info for the "multiple voices" integrity check. The
                # turn-taking engine (which knows if the agent is speaking) decides what counts
                # as a genuine second voice — here we just report who Deepgram heard.
                speaker_counts = Counter(
                    w.speaker for w in (alt.words or []) if w.speaker is not None
                )
                dominant = speaker_counts.most_common(1)[0][0] if speaker_counts else None
                await self.events.put(
                    FinalTranscriptEvent(
                        text=text,
                        speech_final=bool(message.speech_final),
                        speakers=frozenset(speaker_counts),
                        dominant_speaker=dominant,
                    )
                )
            else:
                await self.events.put(InterimTranscriptEvent(text=text))
        elif isinstance(message, ListenV1SpeechStarted):
            await self.events.put(SpeechStartedEvent())
        elif isinstance(message, ListenV1UtteranceEnd):
            await self.events.put(UtteranceEndEvent())

    async def feed_audio(self, chunk: bytes) -> None:
        if self._listen_conn is not None:
            await self._listen_conn.send_media(chunk)

    async def speak(self, text: str, turn_id: int) -> None:
        self._speak_seq += 1
        seq = self._speak_seq
        ack = asyncio.Event()
        self._playback_acks[seq] = ack
        try:
            if self._on_speak_text is not None:
                await self._on_speak_text(text, seq)
            try:
                await asyncio.wait_for(ack.wait(), timeout=_PLAYBACK_ACK_TIMEOUT_S)
            except asyncio.TimeoutError:
                logger.warning("Client TTS playback ack timed out for seq=%s (turn_id=%s)", seq, turn_id)
        finally:
            self._playback_acks.pop(seq, None)
            # Resolves turn_taking.speak_sentence()'s flushed.wait() — must fire whether the
            # sentence was fully played, interrupted, or timed out.
            await self.events.put(AgentSentenceFlushedEvent(turn_id=turn_id))

    def notify_playback_done(self, seq: int) -> None:
        """Called by the orchestrator when the client reports it finished playing sentence `seq`."""
        ack = self._playback_acks.get(seq)
        if ack is not None:
            ack.set()

    async def stop_speaking(self) -> None:
        # Barge-in: unblock any in-flight speak() immediately so speak_sentence() returns and the
        # turn is superseded. The client is told to stop its own playback via the orchestrator's
        # agent_interrupted message (on_agent_interrupted), same as before.
        for ack in list(self._playback_acks.values()):
            ack.set()

    async def close(self) -> None:
        await self.stop_speaking()
        try:
            if self._listen_conn is not None:
                await self._listen_conn.send_close_stream()
            if self._listen_task is not None:
                await asyncio.wait_for(self._listen_task, timeout=5)
        except Exception:  # noqa: BLE001
            logger.exception("Error closing Deepgram listen connection")
        finally:
            if self._listen_cm is not None:
                try:
                    await self._listen_cm.__aexit__(None, None, None)
                except Exception:  # noqa: BLE001
                    pass
