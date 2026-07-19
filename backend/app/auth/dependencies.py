from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from shared.db import session_scope
from shared.models import Tenant, User
from shared.security import decode_access_token_claims

from ..tenancy.dependencies import resolve_tenant

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    tenant: Tenant = Depends(resolve_tenant),
) -> User:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    claims = decode_access_token_claims(credentials.credentials)
    if claims is None or not claims.get("sub"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    user_id = claims["sub"]
    token_tid = claims.get("tid")

    with session_scope() as db:
        user = db.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
        # Three-way tenant check (defense-in-depth): the token's tid, the user's tenant, and the
        # subdomain the request arrived on must all agree — a token for tenant A can't be replayed
        # against tenant B's subdomain, and stale pre-tenancy tokens (no tid) are rejected.
        if token_tid != user.tenant_id or user.tenant_id != tenant.id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Wrong workspace for this session"
            )
        db.expunge(user)
        return user

