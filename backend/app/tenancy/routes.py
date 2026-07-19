from fastapi import APIRouter, Depends
from pydantic import BaseModel
from shared.models import Tenant

from .dependencies import resolve_tenant

router = APIRouter(prefix="/tenant", tags=["tenant"])


class TenantPublic(BaseModel):
    slug: str
    name: str
    status: str
    display_name: str | None = None
    logo_url: str | None = None
    primary_color: str | None = None


@router.get("/current", response_model=TenantPublic)
def current_tenant(tenant: Tenant = Depends(resolve_tenant)) -> TenantPublic:
    """Public, unauthenticated: the branding the frontend needs to render the login screen for
    whichever tenant subdomain was hit. Never returns anything sensitive."""
    return TenantPublic(
        slug=tenant.slug,
        name=tenant.name,
        status=tenant.status.value,
        display_name=tenant.display_name,
        logo_url=tenant.logo_url,
        primary_color=tenant.primary_color,
    )
