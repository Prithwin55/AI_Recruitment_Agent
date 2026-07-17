import asyncio
import logging
import time

from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType
from deepgram.listen.v1.types import (
    ListenV1Results,
    ListenV1SpeechStarted,
    ListenV1UtteranceEnd,
)
from elevenlabs import AsyncElevenLabs
from shared.config import get_settings

from .base import (
    AgentAudioChunkEvent,
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
_SPEAK_OUTPUT_FORMAT = "pcm_24000"  # raw linear16/24kHz/mono — matches what the client expects
_SPEAK_CHUNK_TIMEOUT_S = 15  # a real stall, not a legitimately long sentence — see speak()
_SPEAK_COALESCE_BYTES = 4800  # ~100ms @ 24kHz/16-bit/mono — see speak()
_PLAYBACK_BYTES_PER_SEC = 48000  # 24000 samples/s * 2 bytes/sample (linear16 mono) — pacing math
_PACING_LEAD_S = 0.5  # keep at most this much audio buffered ahead of real playback — see speak()


class DeepgramProvider(SpeechProvider):
    """English STT/TTS for the live interview: Deepgram for Listen (STT), ElevenLabs for Speak
    (TTS) — split across two vendors deliberately, not a naming leftover.

    Listen (STT) is unchanged from before: one persistent Deepgram WebSocket connection for
    the whole session, continuously fed live mic audio so it never goes idle.

    Speak (TTS) was Deepgram Aura until it was replaced here with ElevenLabs — audible
    stuttering/lag persisted even after fixing the turn-taking and connection-reuse issues on
    the Deepgram Speak side (see turn_taking.py's interruption-threshold history and this
    file's own prior persistent-connection-per-turn rewrite). ElevenLabs' text_to_speech.stream()
    is a one-shot HTTP-streaming call per sentence rather than a persistent WebSocket — verified
    directly against the real API before writing this: ~0.2-0.7s to first byte per call, back
    to back, with no explicit connection-reuse bookkeeping needed (httpx pools the underlying
    connection itself). output_format="pcm_24000" returns raw PCM directly — no container to
    decode. Interruption aborts the in-flight HTTP stream via aclose() rather than a
    provider-side "clear" message; perceived silence still comes primarily from the client
    dropping stale-turn_id audio and flushing its playback queue on agent_interrupted.

    speak() PACES emission to ~real playback rate rather than dumping a whole sentence's audio
    at once. This matters because ElevenLabs returns a full sentence in ~0.3s but that audio
    plays over several seconds: if the server forwards it all immediately, speak_sentence()
    returns almost instantly and the turn-taking state machine flips to LISTENING while the
    candidate is still hearing the agent — so a genuine barge-in during that window isn't
    treated as an interruption (state is no longer AGENT_SPEAKING), and the agent's next turn
    stacks on top of audio still buffered in the browser. Pacing keeps the server's notion of
    "still speaking" aligned with what the candidate actually hears, so barge-in works
    throughout the utterance and the client never holds more than ~_PACING_LEAD_S of backlog
    (making the flush-on-interrupt near-instant). The pacing sleeps also yield the event loop
    to the interruption-detecting pump task, which the old burst behavior starved.
    """

    def __init__(self) -> None:
        super().__init__()
        settings = get_settings()
        self._client = AsyncDeepgramClient(api_key=settings.deepgram_api_key)

        self._eleven_client = AsyncElevenLabs(api_key=settings.elevenlabs_api_key)
        self._eleven_voice_id = settings.elevenlabs_voice_id
        self._eleven_model_id = settings.elevenlabs_model_id
        # Checked once per audio chunk while streaming a sentence — set by stop_speaking() to
        # abort the in-flight speak() call promptly on a genuine interruption.
        self._stop_requested = False

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
            utterance_end_ms=1000,
            vad_events=True,
            smart_format=True,
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
                await self.events.put(FinalTranscriptEvent(text=text, speech_final=bool(message.speech_final)))
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
        self._stop_requested = False
        stream_iter = self._eleven_client.text_to_speech.stream(
            voice_id=self._eleven_voice_id,
            text=text,
            model_id=self._eleven_model_id,
            output_format=_SPEAK_OUTPUT_FORMAT,
        )

        # ElevenLabs streams in very fine-grained pieces (~1KB every ~20ms). We coalesce into
        # ~100ms frames (fewer, larger websocket sends — the old one-send-per-tiny-chunk firehose
        # was ~150 sends/sentence) AND pace emission to real playback rate — see the class
        # docstring for why pacing is what actually makes barge-in work.
        emitted_bytes = 0
        t_start = None

        async def emit(frame: bytes) -> None:
            nonlocal emitted_bytes, t_start
            if t_start is None:
                t_start = time.monotonic()
            await self.events.put(AgentAudioChunkEvent(data=frame, turn_id=turn_id))
            emitted_bytes += len(frame)
            ahead = emitted_bytes / _PLAYBACK_BYTES_PER_SEC - (time.monotonic() - t_start)
            if ahead > _PACING_LEAD_S:
                await asyncio.sleep(ahead - _PACING_LEAD_S)

        buffer = bytearray()
        try:
            while not self._stop_requested:
                try:
                    chunk = await asyncio.wait_for(stream_iter.__anext__(), timeout=_SPEAK_CHUNK_TIMEOUT_S)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    logger.error("ElevenLabs speak() stalled waiting for audio for turn_id=%s", turn_id)
                    break
                if not chunk:
                    continue
                buffer.extend(chunk)
                while len(buffer) >= _SPEAK_COALESCE_BYTES and not self._stop_requested:
                    frame = bytes(buffer[:_SPEAK_COALESCE_BYTES])
                    del buffer[:_SPEAK_COALESCE_BYTES]
                    await emit(frame)
        finally:
            if buffer and not self._stop_requested:
                await emit(bytes(buffer))
            try:
                await stream_iter.aclose()
            except Exception:  # noqa: BLE001
                pass
            await self.events.put(AgentSentenceFlushedEvent(turn_id=turn_id))

    async def stop_speaking(self) -> None:
        # No provider-side "clear" round trip to wait on (unlike the old Deepgram Speak
        # design) — this just tells the in-flight speak() loop to stop consuming further
        # chunks on its very next iteration and let its own finally: block close the stream.
        self._stop_requested = True

    async def close(self) -> None:
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
