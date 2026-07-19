import logging

from shared.config import get_settings
from shared.db import DEFAULT_TENANT_ID, session_scope
from shared.models import Tenant, TenantStatus, User, UserRole
from shared.security import hash_password

logger = logging.getLogger(__name__)


def seed_default_user() -> None:
    settings = get_settings()
    with session_scope() as db:
        existing = db.query(User).first()
        if existing is not None:
            return

        # Guarantee the default tenant exists (init_db()'s backfill normally creates it, but seed
        # must not depend on ordering) and make the first account its default recruiter.
        tenant = db.get(Tenant, DEFAULT_TENANT_ID)
        if tenant is None:
            tenant = Tenant(
                id=DEFAULT_TENANT_ID,
                slug=settings.default_tenant_slug,
                name="Default",
                status=TenantStatus.ACTIVE,
            )
            db.add(tenant)
            db.flush()

        user = User(
            tenant_id=DEFAULT_TENANT_ID,
            email=settings.default_admin_email,
            password_hash=hash_password(settings.default_admin_password),
            role=UserRole.RECRUITER,
            must_change_password=True,
        )
        db.add(user)

    logger.warning(
        "Seeded default recruiter account: %s / %s (change on first login)",
        settings.default_admin_email,
        settings.default_admin_password,
    )
    print(
        f"\n[seed] Default recruiter account created:\n"
        f"       email:    {settings.default_admin_email}\n"
        f"       password: {settings.default_admin_password}\n"
        f"       (you will be required to change this password on first login)\n"
    )
