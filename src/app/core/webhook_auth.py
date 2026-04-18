"""HMAC-SHA256 webhook signature verification."""

from __future__ import annotations

import hashlib
import hmac
from typing import Annotated

from fastapi import Depends, Request

from app.config import Settings
from app.core.dependencies import get_settings_dep


def sign_body(body: bytes, secret: str) -> str:
    """Compute ``sha256=<hex>`` signature for *body* keyed by *secret*.

    Exported for test use — conftest builds valid payloads with this.
    """
    mac = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def verify_signature(body: bytes, header: str | None, secret: str) -> bool:
    """Return ``True`` if *header* matches the expected HMAC-SHA256 of *body*.

    Returns ``False`` (never raises) on missing header, wrong prefix, or
    wrong digest.  Uses constant-time comparison.
    """
    if not header or not header.startswith("sha256="):
        return False
    expected = sign_body(body, secret)
    return hmac.compare_digest(header, expected)


async def require_engine_signature(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> bool:
    """FastAPI dependency that verifies the webhook HMAC signature.

    Does **not** raise on failure — sets ``request.state.signature_ok`` and
    returns the bool.  The route handler decides how to respond (401 + persist
    for ``False``; continue for ``True``).
    """
    body: bytes = request.state.raw_body  # stashed by RawBodyMiddleware
    header = request.headers.get("x-engine-signature")
    ok = verify_signature(body, header, settings.engine_webhook_secret.get_secret_value())
    request.state.signature_ok = ok
    return ok
