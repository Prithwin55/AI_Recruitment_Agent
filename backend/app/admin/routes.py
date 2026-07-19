"""Admin usage & cost dashboard API.

Separate from recruiter auth: credentials come straight from the env (ADMIN_USERNAME /
ADMIN_PASSWORD), and a successful login issues a short-lived JWT carrying an `admin: true`
claim — recruiter tokens are NOT accepted here, and admin tokens are not accepted by the
recruiter endpoints (their `sub` is not a user id, so get_current_user rejects them).

Costs are derived at read time from the env-configured ₹ rates, so editing a price in .env
re-prices all history on the next request — nothing is stored pre-priced.
"""

import secrets
from datetime import date, datetime, time as dtime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import func
from shared.config import get_settings
from shared.db import session_scope
from shared.models import UsageEvent, UsageService

from .schemas import (
    AdminLoginRequest,
    AdminLoginResponse,
    PricingOut,
    ServiceUsage,
    UsageReport,
)

router = APIRouter(prefix="/admin", tags=["admin"])

_bearer = HTTPBearer(auto_error=False)


def _create_admin_token() -> str:
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": "__admin__", "admin": True, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def require_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    settings = get_settings()
    try:
        payload = jwt.decode(
            credentials.credentials, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    if payload.get("admin") is not True:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")


@router.post("/login", response_model=AdminLoginResponse)
def admin_login(payload: AdminLoginRequest) -> AdminLoginResponse:
    settings = get_settings()
    if not settings.admin_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin panel is disabled — set ADMIN_PASSWORD in the environment to enable it",
        )
    # compare_digest on both fields: same code path for wrong user and wrong password.
    user_ok = secrets.compare_digest(payload.username, settings.admin_username)
    pass_ok = secrets.compare_digest(payload.password, settings.admin_password)
    if not (user_ok and pass_ok):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin credentials")
    return AdminLoginResponse(access_token=_create_admin_token())


def _parse_day(value: str, *, end_of_range: bool) -> datetime:
    """Parse a YYYY-MM-DD (or full ISO) filter value into a UTC bound. A bare date used as the
    range end is made inclusive by advancing to the next midnight (the query uses `<`)."""
    try:
        parsed_date = date.fromisoformat(value)
        if end_of_range:
            parsed_date = parsed_date + timedelta(days=1)
        return datetime.combine(parsed_date, dtime.min, tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid date '{value}' — use YYYY-MM-DD or an ISO datetime",
        )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _as_int(value: object) -> int:
    return int(value or 0)


def _as_float(value: object) -> float:
    return float(value or 0.0)


@router.get("/usage", response_model=UsageReport, dependencies=[Depends(require_admin)])
def usage_report(
    start: str | None = Query(None, description="Inclusive start date (YYYY-MM-DD or ISO datetime)"),
    end: str | None = Query(None, description="Inclusive end date (YYYY-MM-DD) / exclusive ISO datetime"),
    tenant_id: str | None = Query(None, description="Restrict to one tenant; omit for all tenants"),
) -> UsageReport:
    """Aggregate metered usage for the date window and derive ₹ costs from env rates.

    Cost formulas (rates from PRICE_INR_* env vars; defaults = USD rates × ₹86/$):
      AI agent (Claude) = (input_tokens / 1e6) * PRICE_INR_LLM_PER_MTOK_INPUT
                        + (output_tokens / 1e6) * PRICE_INR_LLM_PER_MTOK_OUTPUT
                        (USD ref: $3 / $15 per million input/output tokens)
      STT               = (seconds / 60) * PRICE_INR_STT_PER_MINUTE
                        (USD ref: $0.0154 per minute)
      TTS               = (characters / 1000) * PRICE_INR_TTS_PER_1K_CHARS
                        (USD ref: $0.10 per 1,000 characters)
      OCR               = (pages / 1000) * PRICE_INR_OCR_PER_1K_PAGES
                        (USD ref: $1.50 per 1,000 pages)
    """
    settings = get_settings()
    start_dt = _parse_day(start, end_of_range=False) if start else None
    end_dt = _parse_day(end, end_of_range=True) if end else None

    # One grouped scan over the (indexed) window aggregates every service at once — no per-service
    # queries, bounded output (max 4 rows).
    with session_scope() as db:
        query = db.query(
            UsageEvent.service,
            func.coalesce(func.sum(UsageEvent.input_tokens), 0),
            func.coalesce(func.sum(UsageEvent.output_tokens), 0),
            func.coalesce(func.sum(UsageEvent.seconds), 0.0),
            func.coalesce(func.sum(UsageEvent.characters), 0),
            func.coalesce(func.sum(UsageEvent.pages), 0),
            func.count(),
        )
        if start_dt is not None:
            query = query.filter(UsageEvent.created_at >= start_dt)
        if end_dt is not None:
            query = query.filter(UsageEvent.created_at < end_dt)
        if tenant_id is not None:
            query = query.filter(UsageEvent.tenant_id == tenant_id)
        # Key by the enum value string so we never miss a row if the driver returns plain strings.
        rows: dict[str, tuple] = {}
        for r in query.group_by(UsageEvent.service).all():
            key = r[0].value if isinstance(r[0], UsageService) else str(r[0])
            rows[key] = r[1:]

    def _sums(service: UsageService) -> tuple[int, int, float, int, int, int]:
        raw = rows.get(service.value, (0, 0, 0.0, 0, 0, 0))
        return (
            _as_int(raw[0]),
            _as_int(raw[1]),
            _as_float(raw[2]),
            _as_int(raw[3]),
            _as_int(raw[4]),
            _as_int(raw[5]),
        )

    in_tok, out_tok, _, _, _, llm_events = _sums(UsageService.LLM)
    _, _, stt_seconds, _, _, stt_events = _sums(UsageService.STT)
    _, _, _, tts_chars, _, tts_events = _sums(UsageService.TTS)
    _, _, _, _, ocr_pages, ocr_events = _sums(UsageService.OCR)

    llm_cost = (in_tok / 1_000_000) * settings.price_inr_llm_per_mtok_input + (
        out_tok / 1_000_000
    ) * settings.price_inr_llm_per_mtok_output
    stt_minutes = stt_seconds / 60.0
    stt_cost = stt_minutes * settings.price_inr_stt_per_minute
    tts_cost = (tts_chars / 1000) * settings.price_inr_tts_per_1k_chars
    ocr_cost = (ocr_pages / 1000) * settings.price_inr_ocr_per_1k_pages

    services = [
        ServiceUsage(
            service="llm",
            input_tokens=in_tok,
            output_tokens=out_tok,
            events=llm_events,
            cost_inr=round(llm_cost, 2),
        ),
        ServiceUsage(
            service="stt",
            minutes=round(stt_minutes, 2),
            events=stt_events,
            cost_inr=round(stt_cost, 2),
        ),
        ServiceUsage(
            service="tts",
            characters=tts_chars,
            events=tts_events,
            cost_inr=round(tts_cost, 2),
        ),
        ServiceUsage(
            service="ocr",
            pages=ocr_pages,
            events=ocr_events,
            cost_inr=round(ocr_cost, 2),
        ),
    ]
    return UsageReport(
        start=start_dt.isoformat() if start_dt else None,
        end=end_dt.isoformat() if end_dt else None,
        services=services,
        total_cost_inr=round(llm_cost + stt_cost + tts_cost + ocr_cost, 2),
        pricing=PricingOut(
            llm_per_mtok_input=settings.price_inr_llm_per_mtok_input,
            llm_per_mtok_output=settings.price_inr_llm_per_mtok_output,
            stt_per_minute=settings.price_inr_stt_per_minute,
            tts_per_1k_chars=settings.price_inr_tts_per_1k_chars,
            ocr_per_1k_pages=settings.price_inr_ocr_per_1k_pages,
        ),
    )
