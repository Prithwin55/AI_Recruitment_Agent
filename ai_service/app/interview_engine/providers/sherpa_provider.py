import asyncio
import json
import logging

import websockets
from shared.config import get_settings

from ..vad import TurnVad
from .base import (
    AgentAudioChunkEvent,
    AgentSentenceFlushedEvent,
    FinalTranscriptEvent,
    InterimTranscriptEvent,
    SpeechProvider,
    SpeechStartedEvent,
    UtteranceEndEvent,
)
from .kitten_tts_client import KittenTtsClient

logger = logging.getLogger(__name__)

# The TTS service streams a whole sentence's PCM to us faster than real time (one-shot synth), and the
# browser buffers it and plays it back gaplessly — so the server finishes SENDING well before the
# client finishes PLAYING. These let end_speak_turn() hold the agent-speaking window open until the
# buffered audio would actually drain, so a barge-in during that trailing playback is still caught.
_TTS_SAMPLE_RATE = 24000
_CLIENT_START_CUSHION_S = 0.08   # matches AgentAudioPlayer's SCHEDULE_AHEAD_S (first-chunk lead)
_DRAIN_MARGIN_S = 0.2            # small safety so we never flip to listening a hair before playout ends


class SherpaOnnxProvider(SpeechProvider):
    """English interview provider backed by an external SherpaOnnx streaming ASR WebSocket server
    for speech-to-text, plus a local server-side VAD for turn signals, plus server-side TTS via the
    standalone KittenTTS microservice. It emits the exact same normalized events as the old Deepgram
    provider, so the turn-taking engine is unchanged.

    STT: raw 16 kHz / 16-bit mono PCM is streamed to the SherpaOnnx server; its JSON messages
    ({text, is_final, segment, ...}) become InterimTranscript / FinalTranscript events.

    VAD: SherpaOnnx does NOT emit VAD/endpointing, so a local TurnVad (webrtcvad) runs over the
    same audio to produce SpeechStarted (arms barge-in) and UtteranceEnd (ends the candidate's
    turn after `interview_end_of_turn_silence_ms` of silence). This is what replaces Deepgram's
    `vad_events` + `utterance_end_ms`.

    Diarization: the SherpaOnnx model returns no speaker labels, so FinalTranscript carries an
    empty speaker set and the engine's "multiple voices" integrity check is inactive on this
    path (it degrades cleanly — same as the Azure/Arabic path). See the module note in the PR.

    TTS: server-side. speak() streams 24 kHz PCM from the KittenTTS microservice and emits
    AgentAudioChunkEvents — the SAME event contract AzureProvider uses — so the gateway forwards
    the audio to the browser over the interview WebSocket and the browser just plays it (no more
    in-browser synthesis or per-sentence client ack). Barge-in cancels the in-flight stream.
    """

    def __init__(self) -> None:
        super().__init__()
        settings = get_settings()
        scheme = "wss" if settings.sherpa_stt_use_wss else "ws"
        # NB: the server's URL puts the language path before the port, e.g.
        # wss://transcription-dev.vconsol.com/en:443
        self._uri = f"{scheme}://{settings.sherpa_stt_host}{settings.sherpa_stt_path}:{settings.sherpa_stt_port}"
        self._headers = [("X-Secret-Key", settings.sherpa_stt_secret_key)]

        self._vad = TurnVad(
            sample_rate=16000,
            aggressiveness=settings.vad_aggressiveness,
            onset_ms=settings.vad_onset_ms,
            utterance_end_ms=settings.interview_end_of_turn_silence_ms,
        )

        self._ws = None
        self._recv_task: asyncio.Task | None = None

        # Server-side TTS: HTTP client to the TTS microservice, plus a cancel flag that
        # stop_speaking() sets so an in-flight sentence stream aborts promptly on barge-in.
        self._tts = KittenTtsClient()
        self._cancel_speak = asyncio.Event()
        # Monotonic wall-clock time at which the audio streamed so far will have finished playing on
        # the client (tracks the browser's own gapless scheduling). end_speak_turn() waits for it.
        self._playback_end = 0.0

    # ------------------------------------------------------------------ STT (uplink) ---

    async def start(self) -> None:
        self._ws = await websockets.connect(
            self._uri,
            additional_headers=self._headers,
            open_timeout=10,
            ping_interval=20,
            max_size=None,
        )
        self._recv_task = asyncio.create_task(self._receive_loop())
        logger.info("SherpaOnnx STT connected: %s", self._uri)

    async def _receive_loop(self) -> None:
        try:
            async for message in self._ws:
                if isinstance(message, (bytes, bytearray)):
                    continue
                if message == "Done!":
                    continue
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    continue
                text = (data.get("text") or "").strip()
                if not text:
                    # Empty periodic segment/silence markers — nothing to transcribe.
                    continue
                if data.get("is_final"):
                    # No speaker labels from this model -> empty speaker set (diarization off).
                    await self.events.put(
                        FinalTranscriptEvent(
                            text=text,
                            speech_final=True,
                            speakers=frozenset(),
                            dominant_speaker=None,
                        )
                    )
                else:
                    await self.events.put(InterimTranscriptEvent(text=text))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("SherpaOnnx receive loop ended")

    async def feed_audio(self, chunk: bytes) -> None:
        # 1) Local VAD -> the turn signals SherpaOnnx doesn't provide.
        for event in self._vad.feed(chunk):
            if event == TurnVad.SPEECH_STARTED:
                await self.events.put(SpeechStartedEvent())
            elif event == TurnVad.UTTERANCE_END:
                await self.events.put(UtteranceEndEvent())
        # 2) Forward the same PCM to the ASR server for transcription.
        if self._ws is not None:
            try:
                await self._ws.send(chunk)
            except Exception:  # noqa: BLE001 — a transient send failure must not kill the turn loop
                pass

    # --------------------------------------------------------- TTS (server-side, downlink) ---

    async def speak(self, text: str, turn_id: int) -> None:
        """Stream one sentence of PCM from the KittenTTS service and emit it as AgentAudioChunkEvents
        tagged with turn_id. The pump forwards chunks to the browser and drops any whose turn_id is
        stale after a barge-in. Always ends with an AgentSentenceFlushedEvent so speak_sentence()
        unblocks — on normal completion, on barge-in cancel, or on a synth error (which also
        propagates so the orchestrator can fall back to its retry nudge, mirroring the Azure path)."""
        self._cancel_speak.clear()
        loop = asyncio.get_event_loop()
        try:
            async for chunk in self._tts.stream(text):
                if self._cancel_speak.is_set():
                    break  # barge-in: stop forwarding; the flush below releases speak_sentence()
                await self.events.put(AgentAudioChunkEvent(data=chunk, turn_id=turn_id))
                # Advance the projected playback-end the same way the browser schedules chunks: if the
                # queue has already drained, playback restarts after a small cushion; otherwise this
                # chunk stacks onto the tail.
                now = loop.time()
                if self._playback_end < now:
                    self._playback_end = now + _CLIENT_START_CUSHION_S
                self._playback_end += (len(chunk) // 2) / _TTS_SAMPLE_RATE
        except Exception:  # noqa: BLE001 — surface synth/transport failure to the orchestrator
            logger.exception("TTS synthesis failed for turn_id=%s", turn_id)
            raise
        finally:
            await self.events.put(AgentSentenceFlushedEvent(turn_id=turn_id))

    async def end_speak_turn(self) -> None:
        # The sentence loop finished SENDING, but the client is likely still PLAYING buffered audio.
        # Hold here (keeping the engine in AGENT_SPEAKING) until that audio would drain, so a late
        # barge-in is still detected and stops playback. Wakes immediately on a barge-in, which sets
        # _cancel_speak — without this the turn would flip to LISTENING mid-playout and interruptions
        # over the tail (or over any short single-sentence reply) would be silently ignored.
        remaining = self._playback_end - asyncio.get_event_loop().time() + _DRAIN_MARGIN_S
        if remaining > 0:
            try:
                await asyncio.wait_for(self._cancel_speak.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                pass  # played all the way through with no interruption

    async def stop_speaking(self) -> None:
        # Barge-in: signal the in-flight speak()/end_speak_turn() to stop, and drop the projected
        # playback tail — the client flushes its queue on agent_interrupted, so nothing is left playing.
        self._cancel_speak.set()
        self._playback_end = 0.0

    # ---------------------------------------------------------------------- lifecycle ---

    async def close(self) -> None:
        await self.stop_speaking()
        if self._recv_task is not None:
            self._recv_task.cancel()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
        await self._tts.aclose()
