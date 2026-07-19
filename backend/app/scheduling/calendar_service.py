import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from shared.config import get_settings

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Config problems are logged once per process, not on every schedule — otherwise the sweep worker
# spams the same warning (and full traceback) repeatedly. Reset only on restart.
_config_warned = False


def _warn_config_once(message: str) -> None:
    global _config_warned
    if not _config_warned:
        logger.warning("Google Calendar disabled: %s", message)
        _config_warned = True


def _validate_service_account_file(path_str: str) -> str | None:
    """Return an error message if the configured file isn't a usable service-account key, else None.
    Catches the common mistake of pointing this at an OAuth client-secret JSON instead."""
    path = Path(path_str)
    if not path.exists():
        return f"file not found at '{path_str}'"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return f"'{path_str}' is not valid JSON"
    if not isinstance(data, dict):
        return f"'{path_str}' is not a valid credentials file"
    # OAuth client-secret files nest everything under "web"/"installed" — a common wrong-file mistake.
    if "web" in data or "installed" in data:
        return (
            f"'{path_str}' looks like an OAuth client-secret, not a service-account key. Create a "
            "SERVICE ACCOUNT in Google Cloud Console and download its JSON key (it has "
            '"type": "service_account" with client_email + private_key).'
        )
    if data.get("type") != "service_account" or not data.get("client_email"):
        return f"'{path_str}' is missing service-account fields (need type=service_account, client_email, private_key)"
    return None


def _create_event_sync(
    candidate_email: str | None,
    candidate_name: str | None,
    role_title: str,
    join_url: str,
    scheduled_start: datetime,
    duration_minutes: int,
) -> str | None:
    """Best-effort — returns the created event id, or None if not configured / it fails.

    Must never raise: a Calendar failure should never block the interview email, which is the
    load-bearing notification. See docs/SETUP.md for the service-account-vs-OAuth-client gotcha.
    """
    settings = get_settings()
    if not settings.google_service_account_file or not settings.google_calendar_id:
        _warn_config_once("GOOGLE_SERVICE_ACCOUNT_FILE / GOOGLE_CALENDAR_ID not set")
        return None

    # Validate the credential file up front so a wrong/malformed file is a single clean warning
    # rather than a full traceback on every schedule.
    config_error = _validate_service_account_file(settings.google_service_account_file)
    if config_error is not None:
        _warn_config_once(config_error)
        return None

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        credentials = service_account.Credentials.from_service_account_file(
            settings.google_service_account_file, scopes=_SCOPES
        )
        service = build("calendar", "v3", credentials=credentials, cache_discovery=False)

        end = scheduled_start + timedelta(minutes=duration_minutes)
        event = {
            "summary": f"AI Interview — {role_title}",
            "description": f"Automated AI interview for the {role_title} role.\n\nJoin link: {join_url}",
            "start": {"dateTime": scheduled_start.isoformat()},
            "end": {"dateTime": end.isoformat()},
            "attendees": [{"email": candidate_email, "displayName": candidate_name or ""}]
            if candidate_email
            else [],
        }
        created = (
            service.events()
            .insert(calendarId=settings.google_calendar_id, body=event, sendUpdates="all")
            .execute()
        )
        return created.get("id")
    except Exception:
        logger.exception("Google Calendar invite failed — continuing without it (email still sends)")
        return None


async def create_interview_calendar_event(
    candidate_email: str | None,
    candidate_name: str | None,
    role_title: str,
    join_url: str,
    scheduled_start: datetime,
    duration_minutes: int,
) -> str | None:
    return await asyncio.to_thread(
        _create_event_sync, candidate_email, candidate_name, role_title, join_url, scheduled_start, duration_minutes
    )
