"""Server-side voice-activity detection for turn-taking.

The SherpaOnnx ASR server returns transcripts but no VAD/endpointing events, so the two signals
the turn-taking engine needs — speech ONSET (to arm a barge-in) and end-of-turn SILENCE (to end
the candidate's turn) — have to be produced here. This is the moral equivalent of Deepgram's
`vad_events` + `utterance_end_ms`, reimplemented locally.

Design goals from the request: lightweight and concurrency-friendly. It's built on `webrtcvad`
(the WebRTC VAD as a ~20KB C extension): classifying a frame is a few microseconds with no model
to load and no allocation, so each interview session owns its own `TurnVad` and thousands of them
cost almost nothing and never contend (no shared state, no locks, no GIL-heavy inference). Frame
classification is stateless; only the tiny onset/offset debounce is per-session state.
"""

import logging

import webrtcvad

logger = logging.getLogger(__name__)

# webrtcvad only accepts 10, 20, or 30 ms frames of 16-bit mono PCM at 8/16/32/48 kHz.
_VALID_FRAME_MS = (10, 20, 30)


class TurnVad:
    """Per-session VAD state machine over webrtcvad. Feed it raw 16-bit mono PCM (any chunk
    size); it buffers into fixed frames, classifies each, and returns the turn-taking transitions
    that crossed during this feed."""

    SPEECH_STARTED = "speech_started"
    UTTERANCE_END = "utterance_end"

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_ms: int = 20,
        aggressiveness: int = 2,
        onset_ms: int = 150,
        utterance_end_ms: int = 2000,
    ) -> None:
        if frame_ms not in _VALID_FRAME_MS:
            frame_ms = 20
        self._vad = webrtcvad.Vad(max(0, min(3, aggressiveness)))
        self._sample_rate = sample_rate
        self._frame_ms = frame_ms
        self._frame_bytes = int(sample_rate * frame_ms / 1000) * 2  # 16-bit mono
        # Sustained speech before we call it a real onset (debounces coughs/clicks); and
        # sustained silence (following speech) before we call the turn over. These directly
        # mirror the vad_events debounce and utterance_end_ms of the old STT-native path.
        self._onset_frames = max(1, round(onset_ms / frame_ms))
        self._silence_frames = max(1, round(utterance_end_ms / frame_ms))

        self._buf = bytearray()
        self._in_speech = False
        self._speech_run = 0
        self._silence_run = 0

    def feed(self, pcm16: bytes) -> list[str]:
        """Feed a chunk of 16-bit mono PCM at the configured sample rate. Returns the ordered
        list of transitions ('speech_started' / 'utterance_end') that occurred, usually empty."""
        events: list[str] = []
        self._buf.extend(pcm16)
        fb = self._frame_bytes
        while len(self._buf) >= fb:
            frame = bytes(self._buf[:fb])
            del self._buf[:fb]
            try:
                is_speech = self._vad.is_speech(frame, self._sample_rate)
            except Exception:  # noqa: BLE001 — a malformed frame must never break the session
                continue

            if is_speech:
                self._speech_run += 1
                self._silence_run = 0
                if not self._in_speech and self._speech_run >= self._onset_frames:
                    self._in_speech = True
                    events.append(self.SPEECH_STARTED)
            else:
                self._silence_run += 1
                self._speech_run = 0
                # Only end a turn we were actually in — silence during silence does nothing,
                # so this fires exactly once per speech→silence transition.
                if self._in_speech and self._silence_run >= self._silence_frames:
                    self._in_speech = False
                    events.append(self.UTTERANCE_END)
        return events

    def reset(self) -> None:
        self._buf.clear()
        self._in_speech = False
        self._speech_run = 0
        self._silence_run = 0
