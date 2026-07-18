import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return uuid.uuid4().hex


class RecruitmentStatus(str, enum.Enum):
    DRAFT = "draft"
    PROCESSING = "processing"
    PHASE1_COMPLETE = "phase1_complete"
    INTERVIEWING = "interviewing"
    COMPLETED = "completed"


class ExtractionMethod(str, enum.Enum):
    TEXT = "text"
    VISION = "vision"


class ProcessingStatus(str, enum.Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    SCORED = "scored"
    FAILED = "failed"


class Phase1Decision(str, enum.Enum):
    PENDING = "pending"
    ADVANCE = "advance"
    HOLD = "hold"
    REJECT = "reject"


class Phase2Status(str, enum.Enum):
    NOT_SCHEDULED = "not_scheduled"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    EXPIRED = "expired"
    NO_SHOW = "no_show"


class TokenStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    EXPIRED = "expired"


class InterviewLanguage(str, enum.Enum):
    EN = "en"
    AR_OM = "ar-OM"


class Speaker(str, enum.Enum):
    AGENT = "agent"
    CANDIDATE = "candidate"


class FinalDecision(str, enum.Enum):
    SHORTLIST = "shortlist"
    REJECT = "reject"


class CheatingKind(str, enum.Enum):
    """Integrity/proctoring signals raised during a live interview. These are informational
    only — they are logged and surfaced to the recruiter but deliberately never fed into the
    interview scoring (see post_interview/analyzer.py)."""

    MULTIPLE_FACES = "multiple_faces"  # more than one person visible on camera
    NO_FACE = "no_face"  # candidate not visible / left the frame
    LOOKING_AWAY = "looking_away"  # gaze consistently off-screen (reading elsewhere)
    HEAD_TURNED = "head_turned"  # head turned away from the screen
    MULTIPLE_VOICES = "multiple_voices"  # a second speaker heard on the mic


def _str_enum(python_enum, **kw):
    return Enum(python_enum, native_enum=False, validate_strings=True, **kw)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    recruitments: Mapped[list["Recruitment"]] = relationship(back_populates="created_by_user")


class Recruitment(Base):
    __tablename__ = "recruitments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(255))
    jd_text: Mapped[str] = mapped_column(Text)
    status: Mapped[RecruitmentStatus] = mapped_column(
        _str_enum(RecruitmentStatus), default=RecruitmentStatus.DRAFT
    )
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    created_by_user: Mapped["User"] = relationship(back_populates="recruitments")
    candidates: Mapped[list["Candidate"]] = relationship(
        back_populates="recruitment", cascade="all, delete-orphan"
    )


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    recruitment_id: Mapped[str] = mapped_column(ForeignKey("recruitments.id"), index=True)

    original_filename: Mapped[str] = mapped_column(String(500))
    stored_path: Mapped[str] = mapped_column(String(1000))
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_method: Mapped[ExtractionMethod | None] = mapped_column(
        _str_enum(ExtractionMethod), nullable=True
    )

    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parsed_profile: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    processing_status: Mapped[ProcessingStatus] = mapped_column(
        _str_enum(ProcessingStatus), default=ProcessingStatus.QUEUED, index=True
    )
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    phase1_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    phase1_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    phase1_strengths: Mapped[list | None] = mapped_column(JSON, nullable=True)
    phase1_gaps: Mapped[list | None] = mapped_column(JSON, nullable=True)
    phase1_decision: Mapped[Phase1Decision] = mapped_column(
        _str_enum(Phase1Decision), default=Phase1Decision.PENDING
    )
    # True when the AI auto-advanced this candidate (Phase-1 score >= the shortlist threshold),
    # as opposed to a recruiter advancing them by hand. Drives the auto-scheduler (which only
    # emails auto-advanced candidates) and an "AI shortlisted" badge in the UI.
    auto_advanced: Mapped[bool] = mapped_column(Boolean, default=False)

    phase2_status: Mapped[Phase2Status] = mapped_column(
        _str_enum(Phase2Status), default=Phase2Status.NOT_SCHEDULED, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # Bumped on every change (scoring, decision, phase2 status, …) so the recruiter view can sort
    # "latest activity first". Indexed because it's the default sort key for the paginated list.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, index=True
    )

    recruitment: Mapped["Recruitment"] = relationship(back_populates="candidates")
    interview_sessions: Mapped[list["InterviewSession"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )


class InterviewSession(Base):
    __tablename__ = "interview_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id"), index=True)

    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    token_status: Mapped[TokenStatus] = mapped_column(
        _str_enum(TokenStatus), default=TokenStatus.PENDING, index=True
    )
    language: Mapped[InterviewLanguage] = mapped_column(
        _str_enum(InterviewLanguage), default=InterviewLanguage.EN
    )

    scheduled_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    calendar_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=25)
    recording_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    candidate: Mapped["Candidate"] = relationship(back_populates="interview_sessions")
    transcript_turns: Mapped[list["TranscriptTurn"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="TranscriptTurn.sequence_index"
    )
    result: Mapped["InterviewResult | None"] = relationship(
        back_populates="session", cascade="all, delete-orphan", uselist=False
    )


class TranscriptTurn(Base):
    __tablename__ = "transcript_turns"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    interview_session_id: Mapped[str] = mapped_column(
        ForeignKey("interview_sessions.id"), index=True
    )

    speaker: Mapped[Speaker] = mapped_column(_str_enum(Speaker))
    text: Mapped[str] = mapped_column(Text)
    turn_id: Mapped[int] = mapped_column(Integer)
    sequence_index: Mapped[int] = mapped_column(Integer)
    cut_off: Mapped[bool] = mapped_column(Boolean, default=False)

    sentiment_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    session: Mapped["InterviewSession"] = relationship(back_populates="transcript_turns")


class InterviewResult(Base):
    __tablename__ = "interview_results"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    interview_session_id: Mapped[str] = mapped_column(
        ForeignKey("interview_sessions.id"), unique=True, index=True
    )

    transcript_file_path: Mapped[str] = mapped_column(String(1000))
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    strengths: Mapped[list | None] = mapped_column(JSON, nullable=True)
    weaknesses: Mapped[list | None] = mapped_column(JSON, nullable=True)
    ability_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    sentiment_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    decision: Mapped[FinalDecision | None] = mapped_column(_str_enum(FinalDecision), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    session: Mapped["InterviewSession"] = relationship(back_populates="result")


class CheatingFlag(Base):
    """One integrity event raised during an interview. Multiple flags per session are expected
    (each distinct occurrence is its own row); the recruiter review lists them all."""

    __tablename__ = "cheating_flags"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    interview_session_id: Mapped[str] = mapped_column(
        ForeignKey("interview_sessions.id"), index=True
    )
    kind: Mapped[CheatingKind] = mapped_column(_str_enum(CheatingKind))
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Seconds since the interview started, so the review can show "at 4:12" without needing
    # wall-clock reconciliation across the two services.
    at_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    session: Mapped["InterviewSession"] = relationship()
