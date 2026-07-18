from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Claude (Anthropic) ---
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"

    # --- SherpaOnnx (English STT — external streaming WebSocket ASR server) ---
    sherpa_stt_host: str = "transcription-dev.vconsol.com"
    sherpa_stt_path: str = "/en"  # per-language path on the ASR server
    sherpa_stt_port: int = 443
    sherpa_stt_use_wss: bool = True
    sherpa_stt_secret_key: str = ""

    # --- Server-side VAD (supplies the turn signals SherpaOnnx does not emit) ---
    # webrtcvad aggressiveness 0-3 (higher = more aggressively classifies frames as speech).
    vad_aggressiveness: int = 2
    # Sustained speech required before a "speech started" (barge-in arm) signal — debounces blips.
    vad_onset_ms: int = 150
    # (End-of-turn silence reuses interview_end_of_turn_silence_ms below — the same pause-tolerance knob.)

    # --- Azure Speech (Arabic-Omani STT/TTS) ---
    azure_speech_key: str = ""
    azure_speech_region: str = ""

    # --- SMTP (candidate emails) ---
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = "no-reply@recruitment.local"
    smtp_from_name: str = "AI Recruitment"
    smtp_use_tls: bool = True

    # --- Google Calendar ---
    google_service_account_file: str = ""
    google_calendar_id: str = ""

    # --- Auth ---
    jwt_secret: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 12
    default_admin_email: str = "admin@company.local"
    default_admin_password: str = "ChangeMe123!"

    # --- Recruiter portal ---
    candidates_page_size: int = 20  # not-shortlisted candidates listed per page
    recruitments_page_size: int = 12  # recruitments listed per page (list + dashboard)

    # --- Interview behavior ---
    interview_duration_minutes: int = 25
    interview_link_validity_days: int = 3
    reconnect_grace_seconds: int = 120
    idle_ws_timeout_seconds: int = 45
    phase1_shortlist_threshold: int = 70
    phase1_max_concurrency: int = 4
    # How long the candidate must stay silent before their turn is considered finished and the
    # agent starts responding. Higher = more tolerant of mid-thought pauses (the candidate can
    # breathe / collect their thoughts without the agent jumping in), at the cost of a slightly
    # longer beat before the agent replies once they're genuinely done. Applied to both the
    # Deepgram (English) and Azure (Arabic) recognizers so both languages feel the same.
    interview_end_of_turn_silence_ms: int = 2000

    # --- Networking ---
    # URL and port are kept SEPARATE for each service: the URL holds only scheme + host (no port
    # baked in), and the port is its own value. Use the *_base_url properties below when you need
    # the combined "url:port" address (e.g. CORS origins, the candidate interview link).
    frontend_url: str = "http://localhost"
    frontend_port: int = 5173
    backend_url: str = "http://localhost"
    backend_port: int = 8000
    ai_service_url: str = "http://localhost"
    ai_service_port: int = 8100

    @property
    def frontend_base_url(self) -> str:
        return f"{self.frontend_url}:{self.frontend_port}"

    @property
    def backend_base_url(self) -> str:
        return f"{self.backend_url}:{self.backend_port}"

    @property
    def ai_service_base_url(self) -> str:
        return f"{self.ai_service_url}:{self.ai_service_port}"

    # --- Storage ---
    storage_dir: str = str(REPO_ROOT / "storage")

    @property
    def db_path(self) -> Path:
        return Path(self.storage_dir) / "app.db"

    @property
    def resumes_dir(self) -> Path:
        return Path(self.storage_dir) / "resumes"

    @property
    def recordings_dir(self) -> Path:
        return Path(self.storage_dir) / "recordings"

    @property
    def transcripts_dir(self) -> Path:
        return Path(self.storage_dir) / "transcripts"


@lru_cache
def get_settings() -> Settings:
    return Settings()
