import asyncio
import logging
from datetime import datetime, timedelta

from shared.config import get_settings

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/calendar"]


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
        logger.info("Google Calendar not configured — skipping calendar invite")
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
