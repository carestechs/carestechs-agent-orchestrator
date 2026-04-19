"""AI-specific FastAPI dependencies: policy factory, engine client, actor-role guard."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, Header

from app.config import Settings
from app.core.dependencies import get_settings_dep
from app.core.exceptions import AuthError, ValidationError
from app.modules.ai.engine_client import FlowEngineClient
from app.modules.ai.enums import ActorRole


def get_engine_client(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> FlowEngineClient:
    """FastAPI dependency returning a :class:`FlowEngineClient`.

    Override in tests via ``app.dependency_overrides[get_engine_client]``.
    """
    return FlowEngineClient(settings)


# ---------------------------------------------------------------------------
# Actor-role dependency (FEAT-006)
# ---------------------------------------------------------------------------


class _AuthForbidden(AuthError):
    """403 variant of :class:`AuthError` — wrong role for an endpoint."""

    http_status = 403
    title = "Forbidden"
    code = "actor-role-forbidden"


def require_actor_role(
    *allowed: ActorRole,
) -> Callable[[str | None], Coroutine[Any, Any, ActorRole]]:
    """Return a FastAPI dependency that validates ``X-Actor-Role``.

    Raises ``400`` when the header is missing or not a known role; raises
    ``403`` when the role is not in *allowed*.
    """

    async def _dep(
        x_actor_role: Annotated[
            str | None, Header(alias="X-Actor-Role")
        ] = None,
    ) -> ActorRole:
        if x_actor_role is None:
            raise ValidationError("X-Actor-Role header is required")
        try:
            role = ActorRole(x_actor_role)
        except ValueError as exc:
            raise ValidationError(f"Unknown role: {x_actor_role}") from exc
        if role not in allowed:
            allowed_s = ", ".join(r.value for r in allowed)
            raise _AuthForbidden(
                f"Role {role.value} not allowed; required one of: {allowed_s}"
            )
        return role

    return _dep
