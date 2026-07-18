import asyncio
import json
import logging
from typing import Awaitable, Callable

import websockets
from shared.config import get_settings

from ..vad import TurnVad
from .base import (
    AgentSentenceFlushedEvent,
    FinalTranscriptEvent,
    InterimTranscriptEvent,
    SpeechProvider,
    SpeechStartedEvent,
    UtteranceEndEvent,
)

logger = logging.getLogger(__name__)

# Upper bound on how long one sentence may take to synthesize + play on the CLIENT before we
# stop waiting for its "done playing" ack (generous — the first sentence also triggers the
# one-time in-browser TTS model download). Only a stuck-client safety net.
_PLAYBACK_ACK_TIMEOUT_S = 120


class SherpaOnnxProvider(SpeechProvider):
    """English interview provider backed by an external SherpaOnnx streaming ASR WebSocket server
    for speech-to-text, plus a local server-side VAD for turn signals, plus the client-side TTS
    bridge. It emits the exact same normalized events as the old Deepgram provider, so the
    turn-taking engine is unchanged.

    STT: raw 16 kHz / 16-bit mono PCM is streamed to the SherpaOnnx server; its JSON messages
    ({text, is_final, segment, ...}) become InterimTranscript / FinalTranscript events.

    VAD: SherpaOnnx does NOT emit VAD/endpointing, so a local TurnVad (webrtcvad) runs over the
    same audio to produce SpeechStarted (arms barge-in) and UtteranceEnd (ends the candidate's
    turn after `interview_end_of_turn_silence_ms` of silence). This is what replaces Deepgram's
    `vad_events` + `utterance_end_ms`.

    Diarization: the SherpaOnnx model returns no speaker labels, so FinalTranscript carries an
    empty speaker set and the engine's "multiple voices" integrity check is inactive on this
    path (it degrades cleanly — same as the Azure/Arabic path). See the module note in the PR.

    TTS: unchanged from the Deepgram provider — speak() bridges each sentence to the browser
    (Pocket TTS) via `on_speak_text` and blocks until the client acks it has finished playing.
    """

    def __init__(self, on_speak_text: Callable[[str, int], Awaitable[None]] | None = None) -> None:
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

        # Client-TTS bridge (identical contract to the former DeepgramProvider).
        self._on_speak_text = on_speak_text
        self._speak_seq = 0
        self._playback_acks: dict[int, asyncio.Event] = {}

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

    # -------------------------------------------------------------- TTS bridge (downlink) ---

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
            await self.events.put(AgentSentenceFlushedEvent(turn_id=turn_id))

    def notify_playback_done(self, seq: int) -> None:
        ack = self._playback_acks.get(seq)
        if ack is not None:
            ack.set()

    async def stop_speaking(self) -> None:
        # Barge-in: unblock any in-flight speak() so speak_sentence() returns and the turn is
        # superseded. The client is told to stop playback via the orchestrator's agent_interrupted.
        for ack in list(self._playback_acks.values()):
            ack.set()

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
