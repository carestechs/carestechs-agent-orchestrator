"""Bearer API-key authentication for the control plane."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Header

from app.config import Settings
from app.core.dependencies import get_settings_dep
from app.core.exceptions import AuthError


async def require_api_key(
    authorization: Annotated[str | None, Header()] = None,
    *,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> None:
    """FastAPI dependency that validates ``Authorization: Bearer <token>``.

    Raises :class:`AuthError` on missing or invalid tokens — the global
    handler converts this to a 401 Problem Details response.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthError("missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    expected = settings.orchestrator_api_key.get_secret_value()
    if not hmac.compare_digest(token, expected):
        raise AuthError("invalid api key")
