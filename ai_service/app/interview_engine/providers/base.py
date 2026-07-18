import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SpeechStartedEvent:
    """Candidate has started making sound (provider-level VAD, not yet a confirmed interruption)."""


@dataclass
class InterimTranscriptEvent:
    text: str


@dataclass
class FinalTranscriptEvent:
    text: str
    speech_final: bool  # provider is confident the candidate's turn has ended
    # Diarization info for the "multiple voices" integrity check. Only populated by providers
    # whose STT emits per-word speaker labels; left empty otherwise (the current SherpaOnnx and
    # Azure paths), which cleanly disables the check. speakers = distinct speaker ids in this
    # segment; dominant_speaker = the one with the most words. The turn-taking engine, which
    # knows whether the agent is speaking, decides what counts as a genuine second voice.
    speakers: frozenset = frozenset()
    dominant_speaker: int | None = None


@dataclass
class UtteranceEndEvent:
    """Trailing-silence-based end-of-turn signal, independent of speech_final."""


@dataclass
class AgentAudioChunkEvent:
    data: bytes
    turn_id: int


@dataclass
class AgentSentenceFlushedEvent:
    """The TTS provider has finished synthesizing (and we've forwarded) one complete sentence."""

    turn_id: int


ProviderEvent = (
    SpeechStartedEvent
    | InterimTranscriptEvent
    | FinalTranscriptEvent
    | UtteranceEndEvent
    | AgentAudioChunkEvent
    | AgentSentenceFlushedEvent
)


class SpeechProvider(ABC):
    """Common interface over the interview's speech providers — SherpaOnnx STT + browser TTS
    (English) and Azure Speech (Arabic-Omani).

    Normalized events are pushed onto `self.events` so the turn-taking state machine can
    consume every provider identically. One provider instance is used for one interview
    session's whole lifetime.
    """

    def __init__(self) -> None:
        self.events: asyncio.Queue[ProviderEvent] = asyncio.Queue()

    @abstractmethod
    async def start(self) -> None:
        """Open the underlying STT and TTS connections."""

    @abstractmethod
    async def feed_audio(self, chunk: bytes) -> None:
        """Forward one chunk of raw 16kHz/16-bit/mono PCM candidate audio to STT."""

    @abstractmethod
    async def speak(self, text: str, turn_id: int) -> None:
        """Synthesize one sentence/clause of agent speech, tagged with the current turn id."""

    @abstractmethod
    async def stop_speaking(self) -> None:
        """Best-effort halt of in-flight synthesis. Callers must not rely on this alone for
        perceived silence — see turn_taking.py, which stops forwarding audio to the browser
        immediately regardless of how fast this actually takes effect provider-side."""

    async def end_speak_turn(self) -> None:
        """Called once a full agent turn (every sentence of it) has finished being spoken,
        on the normal (non-interrupted) completion path — see turn_taking.end_agent_turn().
        Default no-op: only relevant to a provider that holds a per-turn TTS connection open
        across multiple speak() calls and must not leave it open through the candidate's next
        utterance. The current providers don't (English TTS is client-side; Azure's synthesizer
        is a single long-lived session), so it has nothing to do here."""
        return

    @abstractmethod
    async def close(self) -> None:
        """Tear down both connections."""
