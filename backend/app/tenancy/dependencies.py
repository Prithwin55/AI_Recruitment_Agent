"""Resolve the current tenant from the request's subdomain.

Tenants live at {slug}.<root_domain>. The Host (or X-Forwarded-Host behind the proxy) is parsed
against the configured root_domain to extract the slug, then looked up. Unknown -> 404; suspended
-> 403. A request with no subdomain (bare host / IP / localhost) falls back to the default tenant
so single-host dev keeps working.
"""

from fastapi import Depends, HTTPException, Request, status
from shared.config import get_settings
from shared.db import session_scope
from shared.models import Tenant, TenantStatus

# Subdomain labels that are never tenants (reserved for the apex app / admin / api surfaces).
RESERVED_SLUGS = {"www", "app", "api", "admin", "auth"}


def _tenant_slug_from_host(host: str, root_domain: str) -> str | None:
    """Return the tenant slug for a Host header, or None when there's no tenant subdomain."""
    host = (host or "").split(",")[0].strip().lower()
    if not host:
        return None
    host = host.split(":", 1)[0]  # strip port
    root = root_domain.split(":", 1)[0].lower()
    if host == root or not host.endswith("." + root):
        return None  # bare apex / unrelated host / IP -> no tenant
    label = host[: -(len(root) + 1)]  # strip ".<root>"
    # Only the left-most label is the tenant (ignore any deeper nesting).
    label = label.rsplit(".", 1)[-1]
    if not label or label in RESERVED_SLUGS:
        return None
    return label


def resolve_tenant(request: Request) -> Tenant:
    settings = get_settings()
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    slug = _tenant_slug_from_host(host, settings.root_domain)
    if slug is None:
        # Dev / single-host: serve the default tenant.
        slug = settings.default_tenant_slug

    with session_scope() as db:
        tenant = db.query(Tenant).filter(Tenant.slug == slug).first()
        if tenant is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown workspace")
        if tenant.status == TenantStatus.SUSPENDED:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This workspace is suspended")
        db.expunge(tenant)
        return tenant


# Convenience alias for route signatures.
CurrentTenant = Depends(resolve_tenant)
