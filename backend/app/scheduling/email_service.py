import asyncio
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from shared.config import get_settings

logger = logging.getLogger(__name__)


class EmailNotConfigured(Exception):
    pass


def _render_html(candidate_name: str, role_title: str, join_url: str, validity_days: int) -> str:
    greeting = f"Hi {candidate_name}," if candidate_name else "Hi,"
    return f"""\
<div style="font-family: -apple-system, Segoe UI, Roboto, sans-serif; max-width: 560px; margin: 0 auto; color: #1f2430;">
  <h2 style="color: #1f2430;">You're invited to interview for {role_title}</h2>
  <p>{greeting}</p>
  <p>
    Thanks for applying — we'd like to invite you to a short AI-guided interview for the
    <strong>{role_title}</strong> role. It takes about 20–30 minutes and you can complete it from your
    computer whenever suits you, any time in the next {validity_days} day{"s" if validity_days != 1 else ""}.
  </p>
  <p style="margin: 28px 0;">
    <a href="{join_url}"
       style="background: #4f5fd6; color: #fff; padding: 12px 22px; border-radius: 8px; text-decoration: none; font-weight: 600;">
      Start your interview
    </a>
  </p>
  <p style="color: #6b7280; font-size: 13px;">
    This link is unique to you and can only be used once — it becomes invalid as soon as the interview
    starts, and expires automatically if unused. Please make sure you have a working camera and
    microphone before you begin.
  </p>
  <p style="color: #6b7280; font-size: 13px;">If the button doesn't work, copy and paste this link: {join_url}</p>
</div>
"""


def _send_sync(to_email: str, subject: str, html_body: str) -> None:
    settings = get_settings()
    if not settings.smtp_host or not settings.smtp_user:
        raise EmailNotConfigured("SMTP is not configured (SMTP_HOST/SMTP_USER missing in .env)")

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
    message["To"] = to_email
    message.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
        if settings.smtp_use_tls:
            server.starttls()
        server.login(settings.smtp_user, settings.smtp_password)
        server.sendmail(settings.smtp_from_email, [to_email], message.as_string())


async def send_interview_email(
    to_email: str, candidate_name: str | None, role_title: str, join_url: str, validity_days: int
) -> None:
    subject = f"Interview invitation — {role_title}"
    html_body = _render_html(candidate_name or "", role_title, join_url, validity_days)
    await asyncio.to_thread(_send_sync, to_email, subject, html_body)
