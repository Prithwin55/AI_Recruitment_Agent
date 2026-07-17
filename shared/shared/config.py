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

    # --- Deepgram (English STT) ---
    deepgram_api_key: str = ""

    # --- ElevenLabs (English TTS) ---
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    elevenlabs_model_id: str = "eleven_flash_v2_5"

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

    # --- Interview behavior ---
    interview_duration_minutes: int = 25
    interview_link_validity_days: int = 3
    reconnect_grace_seconds: int = 120
    idle_ws_timeout_seconds: int = 45
    phase1_shortlist_threshold: int = 70
    phase1_max_concurrency: int = 4

    # --- Networking ---
    frontend_url: str = "http://localhost:5173"
    backend_port: int = 8000
    ai_service_port: int = 8100
    backend_url: str = "http://localhost:8000"
    ai_service_url: str = "http://localhost:8100"

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
