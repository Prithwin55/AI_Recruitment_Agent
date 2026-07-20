import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

from .config import get_settings

_BCRYPT_MAX_BYTES = 72  # hard limit of the bcrypt algorithm itself


def _to_bcrypt_bytes(plain_password: str) -> bytes:
    return plain_password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(plain_password: str) -> str:
    hashed = bcrypt.hashpw(_to_bcrypt_bytes(plain_password), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(_to_bcrypt_bytes(plain_password), password_hash.encode("utf-8"))


def create_access_token(subject: str, tenant_id: str | None = None) -> str:
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload: dict = {"sub": subject, "exp": expire}
    if tenant_id is not None:
        payload["tid"] = tenant_id  # tenant the token is scoped to
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> str | None:
    """Backwards-compatible: returns just the subject (or None)."""
    claims = decode_access_token_claims(token)
    return claims.get("sub") if claims else None


def decode_access_token_claims(token: str) -> dict | None:
    """Full claims (sub, tid, exp, …) or None if invalid/expired."""
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None


def generate_interview_token() -> str:
    return secrets.token_urlsafe(32)
