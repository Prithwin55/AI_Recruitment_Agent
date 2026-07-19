import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket
from shared.config import get_settings
from shared.db import session_scope
from shared.models import (
    Candidate,
    CheatingFlag,
    CheatingKind,
    InterviewResult,
    InterviewSession,
    Phase2Status,
    Recruitment,
    Speaker,
    TokenStatus,
    UsageService,
)
from shared.usage import record_usage_async

from ..post_interview.analyzer import run_post_interview_analysis
from .conversation import ConversationEngine, build_candidate_summary, build_system_prompt, scripted_welcome
from .providers.azure_provider import AzureProvider
from .providers.sherpa_provider import SherpaOnnxProvider
from .transcript import TranscriptRecorder
from .turn_taking import TurnTakingCallbacks, TurnTakingEngine

logger = logging.getLogger(__name__)

router = APIRouter()

_TIMER_UPDATE_INTERVAL_S = 5
_TIME_UP_NOTE = "[The allotted interview time has now ended.]"
_FALLBACK_GOODBYE = "That's all the time we have for today — thank you so much for interviewing with us."
_RETRY_NUDGE = "Sorry, I had a brief hiccup there — could you say that again?"

# Safety cap so a buggy/hostile client can't flood the DB with integrity flags. Real sessions
# produce a handful; the client already debounces sustained conditions into single events.
_MAX_CHEATING_FLAGS = 200
# Minimum gap between two logged multiple-voices flags — Deepgram can report a second speaker
# on several consecutive finals for one real occurrence; we only want to log it once.
_VOICE_FLAG_DEBOUNCE_S = 15.0


class InterviewOrchestrator:
    def __init__(
        self,
        websocket: WebSocket,
        session_id: str,
        language: str,
        duration_minutes: int,
        role_title: str,
        jd_text: str,
        candidate_first_name: str | None,
        candidate_summary: str,
        tenant_id: str | None = None,
    ) -> None:
        self.websocket = websocket
        self.session_id = session_id
        self.tenant_id = tenant_id
        self.language = language
        self.duration_minutes = duration_minutes
        self.role_title = role_title
        self.candidate_first_name = candidate_first_name
        self.started_at = time.monotonic()
        # Two distinct end-of-interview states:
        #  - concluded: the AGENT has decided the conversation is over (natural wrap-up or the
        #    time limit was reached) and has finished delivering its goodbye. This does NOT tear
        #    anything down — it just tells the client to enable its "End call" button. There is
        #    deliberately no server-side auto-close: the candidate ends the call themselves.
        #  - ended: the session is actually being finalized (scored + DB written + socket closed),
        #    triggered by the candidate hanging up or the socket dropping — never on a timer.
        self.concluded = False
        self.ended = False

        # English: SherpaOnnx STT (external WS ASR) + a local VAD for turn signals + client-side
        # Pocket TTS (the provider's speak() bridges to the browser via _send_agent_say). Arabic:
        # Azure for both STT and server-side TTS (SherpaOnnx here / Pocket TTS have no Arabic
        # voice), so it keeps the original server-audio path unchanged.
        self.provider = (
            SherpaOnnxProvider(on_speak_text=self._send_agent_say) if language == "en" else AzureProvider()
        )
        language_label = "English" if language == "en" else "Arabic (Omani)"
        system_prompt = build_system_prompt(role_title, jd_text, candidate_summary, language_label, duration_minutes)
        self.conversation = ConversationEngine(system_prompt, tenant_id=tenant_id)
        self.transcript = TranscriptRecorder(session_id, tenant_id=tenant_id)

        self.engine = TurnTakingEngine(
            self.provider,
            TurnTakingCallbacks(
                on_agent_speaking_start=self._on_agent_speaking_start,
                on_agent_speaking_end=self._on_agent_speaking_end,
                on_agent_interrupted=self._on_agent_interrupted,
                on_agent_audio_chunk=self._on_agent_audio_chunk,
                on_candidate_speaking_start=self._on_candidate_speaking_start,
                on_candidate_partial=self._on_candidate_partial,
                on_candidate_final=self._on_candidate_final,
                on_multiple_voices=self._on_multiple_voices,
            ),
        )

        self._responding_lock = asyncio.Lock()
        self._background_tasks: list[asyncio.Task] = []
        self._cheating_flag_count = 0
        self._last_voice_flag_at = -_VOICE_FLAG_DEBOUNCE_S

        # Usage metering, accumulated in-memory per session and flushed as two UsageEvent rows at
        # finalize() — a per-chunk DB write in the hot audio path would be absurd. STT audio is
        # 16 kHz / 16-bit mono PCM, so seconds = bytes / 32000.
        self._stt_audio_bytes = 0
        self._tts_characters = 0

    async def _send_json(self, payload: dict) -> None:
        try:
            await self.websocket.send_json(payload)
        except Exception:  # noqa: BLE001 — socket may already be closing
            pass

    async def _on_agent_speaking_start(self) -> None:
        await self._send_json({"type": "agent_speaking_start"})

    async def _on_agent_speaking_end(self) -> None:
        await self._send_json({"type": "agent_speaking_end"})

    async def _on_agent_interrupted(self) -> None:
        await self._send_json({"type": "agent_interrupted"})

    async def _on_agent_audio_chunk(self, data: bytes) -> None:
        # Only used by the Arabic/Azure server-TTS path — English audio is synthesized in the
        # browser and never touches the server (see SherpaOnnxProvider / _send_agent_say).
        try:
            await self.websocket.send_bytes(data)
        except Exception:  # noqa: BLE001
            pass

    async def _send_agent_say(self, text: str, seq: int) -> None:
        """Bridge for client-side (Pocket TTS) speech: hand the sentence text to the browser to
        synthesize + play. The browser acks with {type: agent_sentence_done, id: seq} once it
        has finished playing, which unblocks the provider's speak() — see notify_playback_done."""
        await self._send_json({"type": "agent_say", "text": text, "id": seq})

    def notify_playback_done(self, seq) -> None:
        notify = getattr(self.provider, "notify_playback_done", None)
        if notify is None or seq is None:
            return
        try:
            notify(int(seq))
        except (ValueError, TypeError):
            pass

    async def _on_candidate_speaking_start(self) -> None:
        await self._send_json({"type": "candidate_speaking_start"})

    async def _on_candidate_partial(self, text: str) -> None:
        await self._send_json({"type": "candidate_transcript_partial", "text": text})

    async def _on_candidate_final(self, text: str) -> None:
        await self._send_json({"type": "candidate_transcript_final", "text": text})
        self.transcript.record_candidate_turn(text, self.engine.turn_id)
        await self._respond(candidate_text=text)

    async def start(self) -> None:
        # Only opens the provider connections — must return fast. The caller's WS receive
        # loop starts immediately after this, so client audio starts flowing to the Deepgram/
        # Azure Listen connection right away. Speaking the welcome takes a few real seconds
        # (Claude/TTS latency) and used to be awaited here too, which meant the receive loop
        # — the ONLY thing that reads the browser's already-arriving audio and forwards it —
        # didn't start until the welcome finished. Deepgram's Listen connection times out
        # after ~10-12s of receiving nothing (https://dpgr.am/net0001); combined with real
        # browser startup latency (mic permission grant, AudioWorklet module load) on top of
        # the welcome's speaking time, that window could be blown before the server ever read
        # a single byte the client had already sent. The welcome now runs concurrently instead.
        await self.engine.start()
        self._background_tasks.append(asyncio.create_task(self._timer_loop()))
        self._background_tasks.append(asyncio.create_task(self._hard_timeout()))
        self._background_tasks.append(asyncio.create_task(self._speak_welcome()))

    async def _speak_welcome(self) -> None:
        await self._send_json({"type": "welcome"})
        welcome_text = scripted_welcome(self.candidate_first_name, self.role_title, self.language)
        self.conversation.history.append({"role": "assistant", "content": welcome_text})
        # Same lock as _respond()/_force_conclude(): guarantees the welcome can never overlap
        # with a forced time's-up conclusion, even with a pathologically short duration.
        async with self._responding_lock:
            await self._deliver_sentences([welcome_text])

    async def _deliver_sentences(self, sentences: list[str]) -> bool:
        """Speak a pre-built list of sentences (used for the scripted welcome and the
        time's-up fallback goodbye). Returns True if fully delivered without interruption."""
        delivered_ok = True
        for sentence in sentences:
            await self._send_json({"type": "agent_transcript", "text": sentence})
            self._tts_characters += len(sentence)
            ok = await self.engine.speak_sentence(sentence)
            self.transcript.record_agent_turn(sentence, self.engine.turn_id, cut_off=not ok)
            if not ok:
                delivered_ok = False
                break
        await self.engine.end_agent_turn()
        return delivered_ok

    async def _generate_and_speak(self, elapsed: float, remaining: float) -> bool:
        """Streams one Claude response and speaks it sentence by sentence. Returns True if
        delivered in full (not cut off by a genuine candidate barge-in partway through)."""
        delivered: list[str] = []
        cut_off = False
        async for sentence in self.conversation.stream_response(elapsed, remaining):
            if self.conversation.should_conclude:
                # Claude has decided this is its goodbye — same reasoning as the forced
                # time's-up wrap-up: once we're saying goodbye, nothing should cut it off.
                self.engine.set_interruptible(False)
            await self._send_json({"type": "agent_transcript", "text": sentence})
            self._tts_characters += len(sentence)
            ok = await self.engine.speak_sentence(sentence)
            # Even when interrupted mid-sentence, the sentence still belongs in what the agent
            # "said": dropping it entirely (as opposed to just marking it cut off) would leave
            # the next Claude turn with zero memory of what it had even started asking — just a
            # bare "[cut off]" with no content — which defeats the system prompt's "respond
            # naturally to what they actually said, don't try to awkwardly resume" instruction;
            # Claude needs to know what "the old sentence" was to not awkwardly resume it.
            delivered.append(sentence)
            self.transcript.record_agent_turn(sentence, self.engine.turn_id, cut_off=not ok)
            if not ok:
                cut_off = True
                break

        self.conversation.commit_agent_turn(delivered, cut_off)
        if not cut_off:
            await self.engine.end_agent_turn()
        return not cut_off

    async def _respond(self, candidate_text: str) -> None:
        async with self._responding_lock:
            # Once the agent has concluded (said its goodbye), it no longer takes new turns —
            # the interview is over on the agent's side; we're just waiting for the candidate
            # to hang up. Anything they say now is ignored rather than reopening the interview.
            if self.ended or self.concluded:
                return
            # Appending the candidate's turn and generating the reply must be one atomic
            # unit under this lock: if two utterances finalize back to back, appending
            # candidate_text outside the lock let both user-turns land before either
            # response was generated, so the second _respond() call would find the first
            # one's assistant reply already at the tail — Claude then rejects the request
            # ("conversation must end with a user message"). Keeping both steps under the
            # same lock guarantees strict user/assistant alternation no matter how fast
            # consecutive utterances finalize.
            self.conversation.add_candidate_turn(candidate_text)

            elapsed = time.monotonic() - self.started_at
            remaining = max(0.0, self.duration_minutes * 60 - elapsed)

            try:
                delivered_ok = await self._generate_and_speak(elapsed, remaining)
            except Exception:  # noqa: BLE001 — Claude/provider failures (rate limits, transient
                # "overloaded" errors, network blips) must never leave the interview silently
                # stuck: nothing was committed to history yet (the exception happened before
                # commit_agent_turn), so the candidate's turn is still an unanswered "user"
                # message at the tail. Recover audibly with a short nudge — spoken through the
                # normal engine path so the client's orb/state transitions correctly — and
                # commit IT as the assistant reply so history stays correctly alternating for
                # whatever the candidate says next.
                logger.exception(
                    "Claude generation failed for session %s — recovering with a nudge", self.session_id
                )
                try:
                    await self._deliver_sentences([_RETRY_NUDGE])
                    self.conversation.history.append({"role": "assistant", "content": _RETRY_NUDGE})
                except Exception:  # noqa: BLE001
                    logger.exception("Retry nudge also failed for session %s", self.session_id)
                return

            # The agent naturally reached a close (delivered its goodbye + END marker). Don't
            # tear anything down — just signal the client to enable its "End call" button and
            # let the candidate leave when they're ready. The hard timeout is the backstop that
            # forces this same conclusion if the conversation never wraps up on its own.
            if delivered_ok and self.conversation.should_conclude:
                await self.conclude()

    async def feed_audio(self, chunk: bytes) -> None:
        self._stt_audio_bytes += len(chunk)
        await self.engine.feed_audio(chunk)

    def set_muted(self, muted: bool) -> None:
        self.engine.set_muted(muted)

    async def record_cheating_flag(self, kind: str, detail: str | None = None, notify_client: bool = False) -> None:
        """Persist one integrity/proctoring flag. Called both for client-reported video events
        (multiple faces, looking away, head turned) and server-detected audio events (multiple
        voices). Purely informational — never touches interview scoring."""
        try:
            kind_enum = CheatingKind(kind)
        except ValueError:
            logger.warning("Ignoring unknown cheating flag kind %r for session %s", kind, self.session_id)
            return
        if self._cheating_flag_count >= _MAX_CHEATING_FLAGS:
            return
        self._cheating_flag_count += 1
        at_seconds = round(time.monotonic() - self.started_at, 1)
        detail = (detail or "")[:500] or None
        try:
            with session_scope() as db:
                db.add(
                    CheatingFlag(
                        interview_session_id=self.session_id,
                        kind=kind_enum,
                        detail=detail,
                        at_seconds=at_seconds,
                    )
                )
        except Exception:  # noqa: BLE001 — a proctoring log write must never break the interview
            logger.exception("Failed to record cheating flag for session %s", self.session_id)
            return
        if notify_client:
            await self._send_json(
                {"type": "cheating_flag", "kind": kind_enum.value, "detail": detail, "at_seconds": at_seconds}
            )

    async def _on_multiple_voices(self) -> None:
        # Debounced: one real "second person spoke" occurrence can span several Deepgram finals.
        now = time.monotonic()
        if now - self._last_voice_flag_at < _VOICE_FLAG_DEBOUNCE_S:
            return
        self._last_voice_flag_at = now
        await self.record_cheating_flag(
            CheatingKind.MULTIPLE_VOICES.value,
            detail="A second voice was detected on the microphone",
            notify_client=True,
        )

    async def _timer_loop(self) -> None:
        while not self.ended:
            elapsed = time.monotonic() - self.started_at
            remaining = max(0.0, self.duration_minutes * 60 - elapsed)
            await self._send_json(
                {"type": "timer_update", "elapsed_seconds": int(elapsed), "remaining_seconds": int(remaining)}
            )
            await asyncio.sleep(_TIMER_UPDATE_INTERVAL_S)

    async def _hard_timeout(self) -> None:
        await asyncio.sleep(self.duration_minutes * 60)
        if self.ended or self.concluded:
            return
        logger.info("Interview %s reached its time limit — waiting for a natural pause to wrap up", self.session_id)
        await self._force_conclude()

    async def _force_conclude(self) -> None:
        """Time's genuinely up. Never cut the agent off mid-sentence: this waits for
        whatever is currently in flight (an in-progress _respond() holds the same lock) to
        finish naturally, then forces one final goodbye turn and delivers it in full. This
        closing turn is made non-interruptible — once we've committed to wrapping up, nothing
        the candidate says (including false-positive mic echo of the agent's own voice, a real
        risk without a headset) should be able to cut the goodbye off. It does NOT close the
        session: like a natural conclusion, it just marks the interview concluded and lets the
        candidate hang up when ready."""
        async with self._responding_lock:
            if self.ended or self.concluded:
                return
            self.engine.set_interruptible(False)
            self.conversation.history.append({"role": "user", "content": _TIME_UP_NOTE})
            try:
                await self._generate_and_speak(self.duration_minutes * 60, 0)
            except Exception:  # noqa: BLE001 — Claude/provider failure must never hang the session
                logger.exception("Failed to generate closing remarks for session %s — using fallback", self.session_id)
                try:
                    await self._deliver_sentences([_FALLBACK_GOODBYE])
                except Exception:  # noqa: BLE001
                    logger.exception("Fallback goodbye also failed for session %s", self.session_id)
        await self.conclude()

    async def conclude(self) -> None:
        """The agent has decided the interview is over and finished its goodbye. Signal the
        client to enable its "End call" button. Deliberately does not close the socket or write
        the final result — the candidate ends the call themselves (or the socket drops), which
        is what triggers finalize()."""
        if self.concluded or self.ended:
            return
        self.concluded = True
        await self._send_json({"type": "interview_concluded"})

    async def finalize(self, reason: str) -> None:
        """Actually end the session: mark it completed in the DB, kick off post-interview
        scoring, tell the client, and close the socket. Idempotent — safe to call from both the
        explicit end_call handler and the disconnect cleanup path."""
        if self.ended:
            return
        self.ended = True
        await self._send_json({"type": "interview_ended", "reason": reason})

        now = datetime.now(timezone.utc)
        with session_scope() as db:
            session_row = db.get(InterviewSession, self.session_id)
            if session_row is not None:
                session_row.token_status = TokenStatus.COMPLETED
                session_row.ended_at = now
                candidate = db.get(Candidate, session_row.candidate_id)
                if candidate is not None:
                    candidate.phase2_status = Phase2Status.COMPLETED
            existing_result = (
                db.query(InterviewResult).filter(InterviewResult.interview_session_id == self.session_id).first()
            )
            if existing_result is None:
                db.add(
                    InterviewResult(
                        interview_session_id=self.session_id,
                        transcript_file_path=str(self.transcript.path),
                    )
                )

        # Flush this session's accumulated STT/TTS usage (16 kHz 16-bit mono -> bytes/32000 s).
        context = f"interview:{self.session_id}"
        await record_usage_async(
            UsageService.STT, tenant_id=self.tenant_id, seconds=self._stt_audio_bytes / 32000.0, context=context
        )
        await record_usage_async(
            UsageService.TTS, tenant_id=self.tenant_id, characters=self._tts_characters, context=context
        )

        asyncio.create_task(run_post_interview_analysis(self.session_id))

        try:
            await self.websocket.close()
        except Exception:  # noqa: BLE001
            pass

    async def close(self) -> None:
        for task in self._background_tasks:
            task.cancel()
        await self.engine.stop()


@router.websocket("/interview/{token}")
async def interview_websocket(websocket: WebSocket, token: str) -> None:
    await websocket.accept()

    with session_scope() as db:
        session_row = db.query(InterviewSession).filter(InterviewSession.token == token).first()
        if session_row is None or session_row.token_status != TokenStatus.ACTIVE:
            await websocket.send_json({"type": "error", "message": "Invalid or inactive interview session"})
            await websocket.close(code=4404)
            return

        session_id = session_row.id
        tenant_id = session_row.tenant_id
        language = session_row.language.value
        duration_minutes = session_row.duration_minutes

        candidate = db.get(Candidate, session_row.candidate_id)
        recruitment = db.get(Recruitment, candidate.recruitment_id) if candidate else None
        role_title = recruitment.title if recruitment else ""
        jd_text = recruitment.jd_text if recruitment else ""
        candidate_first_name = (candidate.name or "").split(" ")[0] if candidate and candidate.name else None
        candidate_summary = build_candidate_summary(candidate.parsed_profile if candidate else None)

    settings = get_settings()
    if language == "ar-OM" and (not settings.azure_speech_key or not settings.azure_speech_region):
        await websocket.send_json(
            {"type": "error", "message": "Arabic-Omani interview mode is not configured on this server yet"}
        )
        await websocket.close(code=4500)
        return
    if language == "en" and not settings.sherpa_stt_secret_key:
        await websocket.send_json({"type": "error", "message": "Interview voice engine is not configured"})
        await websocket.close(code=4500)
        return

    orchestrator = InterviewOrchestrator(
        websocket=websocket,
        session_id=session_id,
        language=language,
        duration_minutes=duration_minutes,
        role_title=role_title,
        jd_text=jd_text,
        candidate_first_name=candidate_first_name,
        candidate_summary=candidate_summary,
        tenant_id=tenant_id,
    )

    try:
        await orchestrator.start()
        while not orchestrator.ended:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                await orchestrator.feed_audio(message["bytes"])
            elif message.get("text") is not None:
                try:
                    data = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue
                if data.get("type") == "mic_muted":
                    orchestrator.set_muted(True)
                elif data.get("type") == "mic_unmuted":
                    orchestrator.set_muted(False)
                elif data.get("type") == "agent_sentence_done":
                    # Client (Pocket TTS) finished playing the sentence with this id.
                    orchestrator.notify_playback_done(data.get("id"))
                elif data.get("type") == "cheating_event":
                    # Client-side proctoring (MediaPipe) reported a video-based integrity event.
                    # It already displays it locally; we just persist it for the recruiter review.
                    await orchestrator.record_cheating_flag(
                        str(data.get("kind", "")), detail=data.get("detail")
                    )
                elif data.get("type") == "end_call":
                    # Candidate clicked "End call" (only possible once the agent concluded and
                    # enabled the button). Finalize and break out of the receive loop.
                    await orchestrator.finalize(reason="ended_by_candidate")
                    break
    except Exception:  # noqa: BLE001
        logger.exception("Interview session %s crashed", session_id)
    finally:
        # Covers the socket simply dropping (candidate closed the tab, network died) without an
        # explicit end_call. Idempotent: a no-op if finalize() already ran above.
        await orchestrator.finalize(reason="disconnected")
        await orchestrator.close()
