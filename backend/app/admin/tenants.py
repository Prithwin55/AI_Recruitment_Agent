"""Super-admin tenant provisioning. Cross-tenant by design (env-cred `admin` JWT), so these do
NOT go through resolve_tenant. Creating a tenant also creates its default recruiter account in one
transaction and returns a one-time temp password for a clean workspace setup."""

import re
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from shared.config import get_settings
from shared.db import session_scope
from shared.models import Candidate, Recruitment, Tenant, TenantStatus, User, UserRole
from shared.security import hash_password

from .routes import require_admin

router = APIRouter(prefix="/admin/tenants", tags=["admin-tenants"])

_SLUG_RE = re.compile(r"^[a-z0-9](-?[a-z0-9])*$")
_RESERVED_SLUGS = {"www", "app", "api", "admin", "auth", "default"}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class TenantCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=63)
    name: str = Field(min_length=1, max_length=255)
    recruiter_email: str
    display_name: str | None = None
    logo_url: str | None = None
    primary_color: str | None = None

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        v = v.strip().lower()
        if not _SLUG_RE.match(v):
            raise ValueError("slug must be lowercase letters, digits and hyphens (DNS-label form)")
        if v in _RESERVED_SLUGS:
            raise ValueError(f"'{v}' is reserved")
        return v

    @field_validator("recruiter_email")
    @classmethod
    def _email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("value is not a valid email address")
        return v.lower()


class TenantUpdate(BaseModel):
    name: str | None = None
    status: str | None = Field(default=None, pattern="^(active|suspended)$")
    display_name: str | None = None
    logo_url: str | None = None
    primary_color: str | None = None


class TenantOut(BaseModel):
    id: str
    slug: str
    name: str
    status: str
    display_name: str | None = None
    logo_url: str | None = None
    primary_color: str | None = None
    users: int = 0
    recruitments: int = 0
    candidates: int = 0


class TenantCreated(BaseModel):
    tenant: TenantOut
    recruiter_email: str
    temp_password: str  # shown once
    login_url: str  # built server-side from ROOT_DOMAIN (authoritative), e.g. https://acme.yourco.com


def _serialize(db, tenant: Tenant, counts: dict[str, tuple[int, int, int]] | None = None) -> TenantOut:
    u, r, c = (counts or {}).get(tenant.id, (0, 0, 0))
    return TenantOut(
        id=tenant.id,
        slug=tenant.slug,
        name=tenant.name,
        status=tenant.status.value,
        display_name=tenant.display_name,
        logo_url=tenant.logo_url,
        primary_color=tenant.primary_color,
        users=u,
        recruitments=r,
        candidates=c,
    )


@router.get("", response_model=list[TenantOut], dependencies=[Depends(require_admin)])
def list_tenants() -> list[TenantOut]:
    with session_scope() as db:
        tenants = db.query(Tenant).order_by(Tenant.created_at.asc()).all()
        # Counts in three grouped queries (not per-tenant).
        users = dict(db.query(User.tenant_id, func.count()).group_by(User.tenant_id).all())
        recrs = dict(db.query(Recruitment.tenant_id, func.count()).group_by(Recruitment.tenant_id).all())
        cands = dict(db.query(Candidate.tenant_id, func.count()).group_by(Candidate.tenant_id).all())
        counts = {
            t.id: (users.get(t.id, 0), recrs.get(t.id, 0), cands.get(t.id, 0)) for t in tenants
        }
        return [_serialize(db, t, counts) for t in tenants]


@router.post("", response_model=TenantCreated, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_admin)])
def create_tenant(payload: TenantCreate) -> TenantCreated:
    temp_password = secrets.token_urlsafe(9)
    with session_scope() as db:
        if db.query(Tenant).filter(Tenant.slug == payload.slug).first() is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="That subdomain is already taken")
        tenant = Tenant(
            slug=payload.slug,
            name=payload.name,
            status=TenantStatus.ACTIVE,
            display_name=payload.display_name,
            logo_url=payload.logo_url,
            primary_color=payload.primary_color,
        )
        db.add(tenant)
        db.flush()
        db.add(
            User(
                tenant_id=tenant.id,
                email=payload.recruiter_email,
                password_hash=hash_password(temp_password),
                role=UserRole.RECRUITER,
                must_change_password=True,
            )
        )
        db.flush()
        out = _serialize(db, tenant, {tenant.id: (1, 0, 0)})
    return TenantCreated(
        tenant=out,
        recruiter_email=payload.recruiter_email,
        temp_password=temp_password,
        # Built from the backend's ROOT_DOMAIN env (authoritative) — not the frontend build-time
        # VITE_ROOT_DOMAIN — so it always reflects the deployed domain.
        login_url=get_settings().tenant_frontend_base_url(payload.slug),
    )



class TenantUserOut(BaseModel):
    id: str
    email: str
    role: str
    must_change_password: bool
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("role", mode="before")
    @classmethod
    def _role(cls, v: object) -> str:
        return getattr(v, "value", v)


class TenantUserCreate(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("value is not a valid email address")
        return v.lower()


class TenantUserCreated(BaseModel):
    user: TenantUserOut
    temp_password: str


@router.get("/{tenant_id}/users", response_model=list[TenantUserOut],
            dependencies=[Depends(require_admin)])
def list_tenant_users(tenant_id: str) -> list[TenantUserOut]:
    with session_scope() as db:
        if db.get(Tenant, tenant_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        users = (
            db.query(User).filter(User.tenant_id == tenant_id).order_by(User.created_at.asc()).all()
        )
        return [TenantUserOut.model_validate(u) for u in users]


@router.post("/{tenant_id}/users", response_model=TenantUserCreated,
             status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
def add_tenant_user(tenant_id: str, payload: TenantUserCreate) -> TenantUserCreated:
    """Add a recruiter account to an existing organization (the recruiter portal has no account
    management — the super-admin owns provisioning)."""
    temp_password = secrets.token_urlsafe(9)
    with session_scope() as db:
        if db.get(Tenant, tenant_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        clash = (
            db.query(User).filter(User.tenant_id == tenant_id, User.email == payload.email).first()
        )
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="That email already exists in this workspace"
            )
        user = User(
            tenant_id=tenant_id,
            email=payload.email,
            password_hash=hash_password(temp_password),
            role=UserRole.RECRUITER,
            must_change_password=True,
        )
        db.add(user)
        db.flush()
        db.refresh(user)
        out = TenantUserOut.model_validate(user)
    return TenantUserCreated(user=out, temp_password=temp_password)


@router.patch("/{tenant_id}", response_model=TenantOut, dependencies=[Depends(require_admin)])
def update_tenant(tenant_id: str, payload: TenantUpdate) -> TenantOut:
    with session_scope() as db:
        tenant = db.get(Tenant, tenant_id)
        if tenant is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        if payload.name is not None:
            tenant.name = payload.name
        if payload.status is not None:
            tenant.status = TenantStatus(payload.status)
        if payload.display_name is not None:
            tenant.display_name = payload.display_name
        if payload.logo_url is not None:
            tenant.logo_url = payload.logo_url
        if payload.primary_color is not None:
            tenant.primary_color = payload.primary_color
        db.flush()
        db.refresh(tenant)
        return _serialize(db, tenant)
