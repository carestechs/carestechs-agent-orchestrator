"""AI-specific FastAPI dependencies: policy factory, engine client."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.config import Settings
from app.core.dependencies import get_settings_dep
from app.modules.ai.engine_client import FlowEngineClient


def get_engine_client(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> FlowEngineClient:
    """FastAPI dependency returning a :class:`FlowEngineClient`.

    Override in tests via ``app.dependency_overrides[get_engine_client]``.
    """
    return FlowEngineClient(settings)
