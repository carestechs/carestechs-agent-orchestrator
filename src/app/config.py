"""Pydantic-settings configuration: env vars, pyproject.toml, defaults."""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import AnyHttpUrl, Field, PostgresDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class PyprojectTomlSource(PydanticBaseSettingsSource):
    """Read ``[tool.orchestrator]`` from the nearest ``pyproject.toml``."""

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        self._data: dict[str, Any] = self._load()

    # ------------------------------------------------------------------
    # Lookup: walk from CWD upward until we find pyproject.toml
    # ------------------------------------------------------------------
    @staticmethod
    def _load() -> dict[str, Any]:
        current = Path.cwd().resolve()
        for directory in (current, *current.parents):
            candidate = directory / "pyproject.toml"
            if candidate.is_file():
                with candidate.open("rb") as f:
                    data = tomllib.load(f)
                return data.get("tool", {}).get("orchestrator", {})
        return {}

    # ------------------------------------------------------------------
    # PydanticBaseSettingsSource protocol
    # ------------------------------------------------------------------
    def get_field_value(
        self,
        field: Any,
        field_name: str,
    ) -> tuple[Any, str, bool]:
        value = self._data.get(field_name)
        return value, field_name, False

    def __call__(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for field_name in self.settings_cls.model_fields:
            value, _, _ = self.get_field_value(None, field_name)
            if value is not None:
                result[field_name] = value
        return result


class Settings(BaseSettings):
    """Application settings with env → pyproject.toml → defaults precedence."""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    # -- Database ----------------------------------------------------------
    database_url: PostgresDsn

    # -- Auth --------------------------------------------------------------
    orchestrator_api_key: SecretStr
    engine_webhook_secret: SecretStr

    # -- Flow engine -------------------------------------------------------
    engine_base_url: AnyHttpUrl
    engine_api_key: SecretStr | None = None
    engine_dispatch_timeout_seconds: int = 10
    public_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8000")

    # -- LLM ---------------------------------------------------------------
    llm_provider: Literal["stub", "anthropic"] = "stub"
    llm_model: str | None = None
    anthropic_api_key: SecretStr | None = None
    anthropic_max_tokens: int = Field(default=4096, gt=0)
    anthropic_timeout_seconds: int = Field(default=60, gt=0)

    # -- Paths -------------------------------------------------------------
    agents_dir: Path = Path("agents")
    trace_dir: Path = Path(".trace")
    repo_root: Path = Path(".")

    # -- Lifecycle agent (FEAT-005) ---------------------------------------
    lifecycle_max_corrections: int = Field(default=2, ge=1)

    # -- Deterministic lifecycle flow (FEAT-006) --------------------------
    # When True (v1 default) the impl-review approver collapses to `admin`;
    # when False a `dev` other than the implementer is expected.
    solo_dev_mode: bool = True

    # -- GitHub integration (FEAT-006) ------------------------------------
    github_webhook_secret: SecretStr | None = None

    # -- Flow-engine lifecycle surface (FEAT-006 rc2) ---------------------
    # Points at the same flow engine as ``engine_base_url``; separate field
    # because the lifecycle surface (workflows / items / transitions) is
    # accessed with a tenant API key + JWT, whereas ``engine_base_url`` is
    # the node/dispatch surface used by FEAT-005.
    flow_engine_lifecycle_base_url: AnyHttpUrl | None = None
    flow_engine_tenant_api_key: SecretStr | None = None

    # -- Observability -----------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    trace_backend: Literal["noop", "jsonl"] = "jsonl"

    @model_validator(mode="after")
    def _validate_llm_provider(self) -> Settings:
        """Enforce provider-specific invariants at ``Settings()`` construction.

        For ``llm_provider='anthropic'``: require a non-empty API key and
        default ``llm_model`` to ``claude-opus-4-7`` when left blank.  Fail
        at construction so a misconfigured process never starts.
        """
        if self.llm_provider != "anthropic":
            return self

        key = self.anthropic_api_key
        if key is None or not key.get_secret_value().strip():
            raise ValueError(
                "anthropic_api_key is required when llm_provider='anthropic'"
            )
        if self.llm_model is None or not self.llm_model.strip():
            self.llm_model = "claude-opus-4-7"
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            PyprojectTomlSource(settings_cls),
            file_secret_settings,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton ``Settings`` instance (cached)."""
    return Settings()  # type: ignore[call-arg]  # pydantic-settings fills required fields from env
