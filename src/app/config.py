"""Pydantic-settings configuration: env vars, pyproject.toml, defaults."""

from __future__ import annotations

import tomllib
import uuid
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
    # IMP-006: maximum operator rejections per checkpoint before the run fails.
    lifecycle_max_checkpoint_rejections: int = Field(default=3, ge=1)

    # -- Run-intake code source (IMP-005) ---------------------------------
    # When True, ``POST /api/v1/runs`` rejects intakes missing the
    # ``codeSource`` block (RFC-7807 ``intake-validation-failed``).
    # When False (deprecation window default), missing ``codeSource`` logs
    # a one-shot WARNING and accepts the run.  Flip to True once all
    # callers supply ``codeSource``; the agent YAML's ``required:`` array
    # follows in lockstep.
    lifecycle_code_source_required: bool = False

    # -- Lifecycle reviewer binding (IMP-003) -----------------------------
    # Selects which executor binding registers for ``review_implementation``.
    #   * ``llm-content`` (default) — production binding; in-process LLM
    #     call via ``LLMContentExecutor`` (FEAT-011 baseline).
    #   * ``stub-pass`` — smoke / CI binding; ``StubPassReviewerExecutor``
    #     always returns ``verdict=pass``.  NEVER use in production.
    # A future ``remote`` value is reserved for the external review service;
    # intentionally not in the literal yet to avoid premature contract freeze.
    lifecycle_reviewer: Literal["llm-content", "stub-pass"] = "llm-content"

    # -- Agent platform (carestechs-agent-platform) -----------------------
    # When set, generate_tasks / generate_plan / review_implementation are
    # routed to the platform via AgentPlatformExecutor instead of in-process
    # LLMContentExecutor.  Set to the platform's base URL, e.g.:
    #   AGENT_PLATFORM_URL=http://agent-platform-api:8000   (umbrella)
    #   AGENT_PLATFORM_URL=http://localhost:8001             (local dev)
    # Leave unset to keep the default in-process LLM path.
    agent_platform_url: AnyHttpUrl | None = None

    # -- Deterministic lifecycle flow (FEAT-006) --------------------------
    # When True (v1 default) the impl-review approver collapses to `admin`;
    # when False a `dev` other than the implementer is expected.
    solo_dev_mode: bool = True

    # -- GitHub integration (FEAT-006 webhook ingress + FEAT-007 merge gating) --
    github_webhook_secret: SecretStr | None = None

    # FEAT-007: GitHub Checks API credentials.  Configure exactly one of
    # ``github_pat`` (classic PAT with ``repo`` scope) OR the App pair
    # (``github_app_id`` + ``github_private_key``).  When neither is set the
    # Checks client resolves to a no-op — FEAT-006 endpoints keep working,
    # they just don't flip a GitHub merge gate.
    github_pat: SecretStr | None = None
    github_app_id: str | None = None
    github_private_key: SecretStr | None = None

    # FEAT-019: artifact commit target branch.  When set, @0.6.0-human commit
    # nodes push artefacts (brief, task list, plans, reviews) to this branch
    # via the GitHub Contents API using the same ``github_pat`` credential.
    # Must be a valid git ref (e.g. ``main``).  When absent the commit nodes
    # are no-ops (a warning is logged and the step completes without writing).
    github_artifact_branch: str | None = None

    # FEAT-020: validator integration.
    # Path to carestechs-ia-framework/tools/ on the orchestrator host.
    # When absent, run_validate_tasks and run_validate_specs_strict skip
    # non-fatally.
    ia_framework_tools_path: str | None = None
    # Path to the project repo checkout on the orchestrator host; required by
    # run_validate_specs_strict (validate-specs.py reads the docs/ tree).
    # When absent, run_validate_specs_strict skips non-fatally.
    lifecycle_project_repo_path: str | None = None
    # Wall-clock cap for pytest runs dispatched by run_tests (seconds).
    lifecycle_test_timeout_seconds: int = Field(default=300, ge=30)

    # -- Flow-engine lifecycle surface (FEAT-006 rc2) ---------------------
    # Points at the same flow engine as ``engine_base_url``; separate field
    # because the lifecycle surface (workflows / items / transitions) is
    # accessed with a tenant API key + JWT, whereas ``engine_base_url`` is
    # the node/dispatch surface used by FEAT-005.
    flow_engine_lifecycle_base_url: AnyHttpUrl | None = None
    flow_engine_tenant_api_key: SecretStr | None = None
    # BUG-002: required when ``flow_engine_lifecycle_base_url`` is set so
    # the orchestrator can key its ``engine_workflows`` cache by tenant.
    # Get from your engine admin / JWT subject. Validated below.
    flow_engine_tenant_id: uuid.UUID | None = None

    # -- Executor seam (FEAT-009) -----------------------------------------
    # Shared HMAC secret remote executors sign their callback POST with.
    # Single secret for all remote executors initially; per-executor rotation
    # is a future FEAT and should not pre-bake assumptions here.
    executor_dispatch_secret: SecretStr | None = None
    # Per-dispatch upper bound the runtime loop applies when awaiting a
    # remote/human executor's webhook reply. Local executors are bounded by
    # their own callable; they ignore this setting.
    executor_dispatch_timeout_seconds: int = 600

    # -- Observability -----------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # ``postgres`` (FEAT-013) is the production default — trace rows
    # land in the Postgres tables alongside the rest of run state.
    # ``jsonl`` remains as an opt-in local-dev backend.  ``noop`` is the
    # test default and discards all writes.
    trace_backend: Literal["noop", "jsonl", "postgres"] = "postgres"

    # FEAT-013 — retention sweep target.  ``None`` (default) disables
    # retention; otherwise ``orchestrator trace-retention-sweep`` deletes
    # ``effector_calls`` / ``executor_calls`` rows older than this many
    # days.  Operators wire the CLI to their scheduler (cron / systemd /
    # CronJob); the orchestrator runs no in-process retention thread.
    trace_retention_days: int | None = None

    # FEAT-014 — server-side cap on ``intake.workItem.content`` size at the
    # POST /api/v1/runs and POST /api/v1/work-items boundaries.  Bytes,
    # measured as ``len(content.encode("utf-8"))``.  Default 1 MB.
    intake_work_item_max_bytes: int = Field(default=1_048_576, ge=1)

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
            raise ValueError("anthropic_api_key is required when llm_provider='anthropic'")
        if self.llm_model is None or not self.llm_model.strip():
            self.llm_model = "claude-opus-4-7"
        return self

    @model_validator(mode="after")
    def _validate_github_credentials(self) -> Settings:
        """Reject half-set App creds and "both PAT and App" configurations.

        FEAT-007's factory resolves the Checks client in strict priority
        order (App > PAT > Noop).  A misconfiguration here would silently
        pick the wrong strategy, so we fail at construction instead.
        """
        has_pat = self.github_pat is not None
        app_id = self.github_app_id
        has_app_id = app_id is not None and app_id.strip() != ""
        has_app_key = self.github_private_key is not None
        has_app = has_app_id and has_app_key

        if has_app_id != has_app_key:
            raise ValueError("github_app_id and github_private_key must be configured together")
        if has_pat and has_app:
            raise ValueError("configure GITHUB_PAT or GITHUB_APP_ID+GITHUB_PRIVATE_KEY, not both")
        return self

    @model_validator(mode="after")
    def _validate_engine_lifecycle_settings(self) -> Settings:
        """Pair ``flow_engine_lifecycle_base_url`` with both auth + tenant id.

        BUG-002: ``engine_workflows`` is keyed by ``(tenant_id, name)``;
        without an explicit tenant id the bootstrap cannot key the cache
        and would silently fall back to the FEAT-006 single-tenant
        assumption. Refuse at construction so a misconfigured process
        never starts.
        """
        if self.flow_engine_lifecycle_base_url is None:
            return self
        missing: list[str] = []
        if self.flow_engine_tenant_api_key is None:
            missing.append("FLOW_ENGINE_TENANT_API_KEY")
        if self.flow_engine_tenant_id is None:
            missing.append("FLOW_ENGINE_TENANT_ID")
        if missing:
            raise ValueError(
                f"FLOW_ENGINE_LIFECYCLE_BASE_URL is set but {', '.join(missing)} "
                "is not. See docs/work-items/BUG-002-engine-workflows-tenant-scope.md."
            )
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
