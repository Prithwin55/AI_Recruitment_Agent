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

        # Server-side TTS: HTTP client to the KittenTTS microservice, plus a cancel flag that
        # stop_speaking() sets so an in-flight sentence stream aborts promptly on barge-in.
        self._tts = KittenTtsClient()
        self._cancel_speak = asyncio.Event()

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
        try:
            async for chunk in self._tts.stream(text):
                if self._cancel_speak.is_set():
                    break  # barge-in: stop forwarding; the flush below releases speak_sentence()
                await self.events.put(AgentAudioChunkEvent(data=chunk, turn_id=turn_id))
        except Exception:  # noqa: BLE001 — surface synth/transport failure to the orchestrator
            logger.exception("KittenTTS synthesis failed for turn_id=%s", turn_id)
            raise
        finally:
            await self.events.put(AgentSentenceFlushedEvent(turn_id=turn_id))

    async def stop_speaking(self) -> None:
        # Barge-in: signal the in-flight speak() loop to stop emitting audio. Perceived silence also
        # comes from the pump no longer forwarding once turn_id advances (same as the Azure path).
        self._cancel_speak.set()

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
