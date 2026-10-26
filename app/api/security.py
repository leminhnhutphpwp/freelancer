"""Admin API key protection."""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, status

from app.config import Settings


async def require_admin_key(request: Request) -> None:
    """Constant-time check of the X-API-Key header against the app's settings.

    If ADMIN_API_KEY is unset the check is skipped (local dev only); in production
    the app refuses to start with an empty key (enforced in create_app).
    """
    settings: Settings = request.app.state.settings
    key = (request.headers.get("x-api-key") or "").strip()
    if not settings.admin_api_key:
        if settings.is_production:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "server misconfigured: admin api key")
        return
    if not hmac.compare_digest(key, settings.admin_api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing X-API-Key")
