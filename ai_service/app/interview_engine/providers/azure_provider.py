import asyncio
import logging

import azure.cognitiveservices.speech as speechsdk
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

_SAMPLE_RATE = 16000
_DEFAULT_VOICE = "ar-OM-AyshaNeural"
_LOCALE = "ar-OM"
# Matched to Deepgram's utterance_end_ms=1000 so English/Arabic candidates aren't cut off
# at different sensitivities (see plan).
_SEGMENTATION_SILENCE_TIMEOUT_MS = "1000"


class AzureProvider(SpeechProvider):
    """Arabic-Omani (ar-OM) STT/TTS via Azure AI Speech. Deepgram has zero Arabic TTS and no
    Arabic Flux turn-taking, so this hand-rolls the same normalized-event contract as
    DeepgramProvider — Azure's SDK is thread/callback-based, not asyncio-native, so every
    callback is bridged onto the event loop via call_soon_threadsafe."""

    def __init__(self, voice: str = _DEFAULT_VOICE) -> None:
        super().__init__()
        settings = get_settings()
        self._key = settings.azure_speech_key
        self._region = settings.azure_speech_region
        self._voice = voice
        self._current_turn_id = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._in_utterance = False

        self._push_stream: speechsdk.audio.PushAudioInputStream | None = None
        self._recognizer: speechsdk.SpeechRecognizer | None = None
        self._synthesizer: speechsdk.SpeechSynthesizer | None = None

    async def start(self) -> None:
        if not self._key or not self._region:
            raise RuntimeError("AZURE_SPEECH_KEY/AZURE_SPEECH_REGION is not configured")
        self._loop = asyncio.get_running_loop()

        recognition_config = speechsdk.SpeechConfig(subscription=self._key, region=self._region)
        recognition_config.speech_recognition_language = _LOCALE
        recognition_config.set_property(
            speechsdk.PropertyId.Speech_SegmentationSilenceTimeoutMs, _SEGMENTATION_SILENCE_TIMEOUT_MS
        )

        stream_format = speechsdk.audio.AudioStreamFormat(
            samples_per_second=_SAMPLE_RATE, bits_per_sample=16, channels=1
        )
        self._push_stream = speechsdk.audio.PushAudioInputStream(stream_format=stream_format)
        audio_config = speechsdk.audio.AudioConfig(stream=self._push_stream)
        self._recognizer = speechsdk.SpeechRecognizer(speech_config=recognition_config, audio_config=audio_config)

        self._recognizer.recognizing.connect(self._on_recognizing)
        self._recognizer.recognized.connect(self._on_recognized)
        self._recognizer.canceled.connect(lambda evt: logger.warning("Azure STT canceled: %s", evt))

        await self._loop.run_in_executor(None, lambda: self._recognizer.start_continuous_recognition_async().get())

        synth_config = speechsdk.SpeechConfig(subscription=self._key, region=self._region)
        synth_config.speech_synthesis_voice_name = self._voice
        synth_config.set_speech_synthesis_output_format(speechsdk.SpeechSynthesisOutputFormat.Raw24Khz16BitMonoPcm)
        # audio_config=None: no default speaker output — we only want audio via the
        # `synthesizing` event's incremental audio_data chunks.
        self._synthesizer = speechsdk.SpeechSynthesizer(speech_config=synth_config, audio_config=None)
        self._synthesizer.synthesizing.connect(self._on_synthesizing)
        self._synthesizer.synthesis_completed.connect(self._on_synthesis_completed)
        self._synthesizer.synthesis_canceled.connect(lambda evt: logger.warning("Azure TTS canceled: %s", evt))

    def _emit_threadsafe(self, event) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self.events.put_nowait, event)

    def _on_recognizing(self, evt) -> None:
        if evt.result.reason != speechsdk.ResultReason.RecognizingSpeech or not evt.result.text:
            return
        if not self._in_utterance:
            self._in_utterance = True
            self._emit_threadsafe(SpeechStartedEvent())
        self._emit_threadsafe(InterimTranscriptEvent(text=evt.result.text))

    def _on_recognized(self, evt) -> None:
        self._in_utterance = False
        if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech and evt.result.text:
            self._emit_threadsafe(FinalTranscriptEvent(text=evt.result.text, speech_final=True))
            self._emit_threadsafe(UtteranceEndEvent())

    def _on_synthesizing(self, evt) -> None:
        if evt.result.audio_data:
            self._emit_threadsafe(AgentAudioChunkEvent(data=evt.result.audio_data, turn_id=self._current_turn_id))

    def _on_synthesis_completed(self, evt) -> None:
        self._emit_threadsafe(AgentSentenceFlushedEvent(turn_id=self._current_turn_id))

    async def feed_audio(self, chunk: bytes) -> None:
        if self._push_stream is not None:
            await asyncio.get_running_loop().run_in_executor(None, self._push_stream.write, chunk)

    async def speak(self, text: str, turn_id: int) -> None:
        if self._synthesizer is None:
            return
        self._current_turn_id = turn_id
        await asyncio.get_running_loop().run_in_executor(None, lambda: self._synthesizer.speak_text_async(text).get())

    async def stop_speaking(self) -> None:
        """Fire-and-forget: `stop_speaking_async()` is documented to lag up to ~30s on Azure's
        side, so we never await it — the WS gateway achieves perceived silence by simply
        no longer forwarding AgentAudioChunkEvents once the turn id advances, regardless of
        how long Azure actually takes to stop generating them underneath."""
        if self._synthesizer is not None:
            asyncio.create_task(self._stop_speaking_background())

    async def _stop_speaking_background(self) -> None:
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._synthesizer.stop_speaking_async().get()
            )
        except Exception:  # noqa: BLE001
            logger.exception("Azure stop_speaking_async failed")

    async def close(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            if self._recognizer is not None:
                await loop.run_in_executor(None, lambda: self._recognizer.stop_continuous_recognition_async().get())
        except Exception:  # noqa: BLE001
            logger.exception("Error stopping Azure recognizer")
        try:
            if self._push_stream is not None:
                self._push_stream.close()
        except Exception:  # noqa: BLE001
            pass
