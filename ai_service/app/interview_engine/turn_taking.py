import asyncio
import enum
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .providers.base import (
    AgentAudioChunkEvent,
    AgentSentenceFlushedEvent,
    FinalTranscriptEvent,
    InterimTranscriptEvent,
    SpeechProvider,
    SpeechStartedEvent,
    UtteranceEndEvent,
)

logger = logging.getLogger(__name__)

# A candidate barge-in only counts once speech is sustained past both of these — filters out
# coughs/noise/blips AND, critically, the agent's own voice leaking back through the
# candidate's speakers into their mic (Deepgram's VAD/endpointing has no notion of "that's
# our own TTS audio" — it fires SpeechStarted on any sustained sound, echoCancellation is
# best-effort and imperfect especially on speaker+mic setups, and feed_audio() forwards
# continuously through agent speech by design — see feed_audio()). A loose threshold here
# means leaked echo regularly crosses the bar, which doesn't just misfire once: it triggers a
# real stop_speaking()+clear() mid-utterance, the agent treats the echoed fragment as
# candidate speech and starts responding to it, and the same thing happens again seconds
# later — audibly a repeating stop/start "cut cut" pattern, not a one-off glitch. Was
# tightened to 0.6s/3 words for this exact reason, then loosened back to 0.35s/2 (below even
# the original 0.4s/2) in the same change that also fixed a real main-thread-jank bug delaying
# outbound mic audio — the jank was mistakenly assumed to be the sole cause of "interruption
# not detected." Restored to 0.6s/3: with the jank fix in place, genuine interruptions reach
# the server promptly and clear this bar easily, while intermittent/choppy echo mostly can't.
MIN_INTERRUPT_DURATION_S = 0.6
MIN_INTERRUPT_WORDS = 3


class TurnState(enum.Enum):
    IDLE = "idle"
    AGENT_SPEAKING = "agent_speaking"
    LISTENING = "listening"
    THINKING = "thinking"


@dataclass
class TurnTakingCallbacks:
    on_agent_speaking_start: Callable[[], Awaitable[None]] | None = None
    on_agent_speaking_end: Callable[[], Awaitable[None]] | None = None
    on_agent_interrupted: Callable[[], Awaitable[None]] | None = None
    on_agent_audio_chunk: Callable[[bytes], Awaitable[None]] | None = None
    on_candidate_speaking_start: Callable[[], Awaitable[None]] | None = None
    on_candidate_partial: Callable[[str], Awaitable[None]] | None = None
    on_candidate_final: Callable[[str], Awaitable[None]] | None = None


@dataclass
class _PendingInterruption:
    started_at: float
    latest_text: str = ""


class TurnTakingEngine:
    """Owns one provider for the session lifetime and interprets its normalized event
    stream into turn-taking decisions: genuine barge-in vs. noise, trailing-pause tolerance
    (delegated to the provider's own endpointing — see DeepgramProvider/AzureProvider),
    mute-gating, and sentence-level tracking of what the agent actually said."""

    def __init__(self, provider: SpeechProvider, callbacks: TurnTakingCallbacks) -> None:
        self._provider = provider
        self._cb = callbacks

        self.state = TurnState.IDLE
        self.turn_id = 0
        self._muted = False
        self._interruptible = True

        self._pending_interruption: _PendingInterruption | None = None
        self._candidate_final_segments: list[str] = []

        self._sentence_flushed_events: dict[int, asyncio.Event] = {}
        self._pump_task: asyncio.Task | None = None

    async def start(self) -> None:
        await self._provider.start()
        self._pump_task = asyncio.create_task(self._pump_events())

    async def stop(self) -> None:
        if self._pump_task is not None:
            self._pump_task.cancel()
        await self._provider.close()

    def set_muted(self, muted: bool) -> None:
        """Mute gates whether audio is forwarded to the provider at all — this is how the
        endpointing clock 'pauses' during mute rather than treating muted silence as
        end-of-turn: the provider simply receives no audio to time out on."""
        self._muted = muted

    def set_interruptible(self, interruptible: bool) -> None:
        """Used to make the forced time's-up closing remark immune to barge-in (including
        false positives from mic echo of the agent's own voice on speaker playback): once
        we've committed to ending the interview, nothing the candidate says should be able
        to cut off the goodbye."""
        self._interruptible = interruptible
        if not interruptible:
            self._pending_interruption = None

    async def feed_audio(self, chunk: bytes) -> None:
        # No state gate beyond mute: audio must reach the provider from the moment the
        # connection opens, before the welcome (or anything else) has started speaking —
        # gating on state used to silently drop everything received while still IDLE, which
        # defeated forwarding audio early precisely when that mattered most (see start()).
        if not self._muted:
            await self._provider.feed_audio(chunk)

    async def speak_sentence(self, text: str) -> bool:
        """Speak one sentence/clause. Returns True if it was fully delivered, False if the
        turn was superseded (interrupted) before or during delivery — callers must stop
        speaking further sentences of the same response when this returns False."""
        turn_id = self.turn_id
        if self.state != TurnState.AGENT_SPEAKING:
            self.state = TurnState.AGENT_SPEAKING
            if self._cb.on_agent_speaking_start:
                await self._cb.on_agent_speaking_start()

        flushed = asyncio.Event()
        self._sentence_flushed_events[turn_id] = flushed
        await self._provider.speak(text, turn_id)

        try:
            await flushed.wait()
        finally:
            self._sentence_flushed_events.pop(turn_id, None)

        return turn_id == self.turn_id

    async def end_agent_turn(self) -> None:
        if self.state == TurnState.AGENT_SPEAKING:
            self.state = TurnState.LISTENING
            await self._provider.end_speak_turn()
            if self._cb.on_agent_speaking_end:
                await self._cb.on_agent_speaking_end()

    async def begin_thinking(self) -> None:
        self.state = TurnState.THINKING

    async def _confirm_interruption(self) -> None:
        self.turn_id += 1
        self._pending_interruption = None
        await self._provider.stop_speaking()
        self.state = TurnState.LISTENING
        if self._cb.on_agent_interrupted:
            await self._cb.on_agent_interrupted()

    async def _pump_events(self) -> None:
        while True:
            event = await self._provider.events.get()

            if isinstance(event, AgentAudioChunkEvent):
                if event.turn_id == self.turn_id and self._cb.on_agent_audio_chunk:
                    await self._cb.on_agent_audio_chunk(event.data)
                continue

            if isinstance(event, AgentSentenceFlushedEvent):
                flushed = self._sentence_flushed_events.get(event.turn_id)
                if flushed is not None:
                    flushed.set()
                continue

            if isinstance(event, SpeechStartedEvent):
                if self.state == TurnState.AGENT_SPEAKING and self._interruptible:
                    self._pending_interruption = _PendingInterruption(started_at=time.monotonic())
                elif self.state in (TurnState.LISTENING, TurnState.THINKING):
                    if self._cb.on_candidate_speaking_start:
                        await self._cb.on_candidate_speaking_start()
                continue

            if isinstance(event, InterimTranscriptEvent):
                if self.state == TurnState.AGENT_SPEAKING:
                    if self._pending_interruption is not None:
                        self._pending_interruption.latest_text = event.text
                        elapsed = time.monotonic() - self._pending_interruption.started_at
                        word_count = len(event.text.split())
                        if elapsed >= MIN_INTERRUPT_DURATION_S and word_count >= MIN_INTERRUPT_WORDS:
                            await self._confirm_interruption()
                            preview = " ".join([*self._candidate_final_segments, event.text]).strip()
                            if self._cb.on_candidate_partial:
                                await self._cb.on_candidate_partial(preview)
                    # else: interim growth with no SpeechStarted seen yet — shouldn't happen
                    # given provider semantics, but stay silent rather than guess.
                    continue

                preview = " ".join([*self._candidate_final_segments, event.text]).strip()
                if self._cb.on_candidate_partial:
                    await self._cb.on_candidate_partial(preview)
                continue

            if isinstance(event, FinalTranscriptEvent):
                # A brief noise blip that never crossed the interruption threshold and never
                # produced real transcript growth — nothing to do, stay AGENT_SPEAKING.
                if self.state == TurnState.AGENT_SPEAKING:
                    continue
                if event.text:
                    self._candidate_final_segments.append(event.text)
                continue

            if isinstance(event, UtteranceEndEvent):
                if self.state == TurnState.AGENT_SPEAKING:
                    self._pending_interruption = None
                    continue
                final_text = " ".join(self._candidate_final_segments).strip()
                self._candidate_final_segments = []
                if final_text and self._cb.on_candidate_final:
                    await self.begin_thinking()
                    # Dispatched as an independent task, NOT awaited here: on_candidate_final
                    # drives the whole Claude response + speak_sentence flow, which can run for
                    # the length of an entire agent turn. Awaiting it inline would block this
                    # loop from processing any further provider events — including the very
                    # SpeechStarted/Interim events a genuine barge-in depends on — for that
                    # entire duration.
                    asyncio.create_task(self._cb.on_candidate_final(final_text))
                continue
