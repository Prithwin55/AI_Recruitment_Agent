import re
from pathlib import Path

from .config import get_settings

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(original_filename: str) -> str:
    name = Path(original_filename).name
    return _UNSAFE_CHARS.sub("_", name) or "file"


def resume_path(recruitment_id: str, candidate_id: str, original_filename: str) -> Path:
    settings = get_settings()
    directory = settings.resumes_dir / recruitment_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{candidate_id}_{safe_filename(original_filename)}"


def transcript_path(interview_session_id: str) -> Path:
    settings = get_settings()
    settings.transcripts_dir.mkdir(parents=True, exist_ok=True)
    return settings.transcripts_dir / f"{interview_session_id}.txt"


def recording_path(interview_session_id: str, extension: str = "webm") -> Path:
    settings = get_settings()
    settings.recordings_dir.mkdir(parents=True, exist_ok=True)
    return settings.recordings_dir / f"{interview_session_id}.{extension}"


def save_upload(destination: Path, content: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
