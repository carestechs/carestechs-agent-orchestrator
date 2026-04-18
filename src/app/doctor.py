"""Doctor check registry: diagnose local setup viability."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class CheckResult:
    """Outcome of a single doctor check."""

    name: str
    status: str  # "ok" | "fail" | "warn"
    detail: str


def run_checks() -> list[CheckResult]:
    """Execute all checks in order; never short-circuit."""
    results: list[CheckResult] = []

    # -- 1. Config loads ---------------------------------------------------
    results.append(_check_config())

    # -- 2. Required secrets -----------------------------------------------
    results.append(_check_env("ORCHESTRATOR_API_KEY", "Control-plane bearer token"))
    results.append(_check_env("ENGINE_WEBHOOK_SECRET", "Webhook HMAC secret"))

    # -- 3. LLM provider ---------------------------------------------------
    results.append(_check_llm_config())

    # -- 4. Database URL ---------------------------------------------------
    results.append(_check_env("DATABASE_URL", "PostgreSQL connection string"))

    # -- 5. Engine base URL ------------------------------------------------
    results.append(_check_env("ENGINE_BASE_URL", "Flow engine HTTP base URL"))

    # -- 6. Agents dir -----------------------------------------------------
    results.append(_check_agents_dir())

    return results


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_config() -> CheckResult:
    try:
        from app.config import Settings

        Settings()  # type: ignore[call-arg]
        return CheckResult("config", "ok", "Settings loaded successfully")
    except Exception as exc:
        return CheckResult("config", "fail", str(exc))


def _check_env(var_name: str, hint: str) -> CheckResult:
    import os

    value = os.environ.get(var_name)
    if value:
        return CheckResult(var_name, "ok", f"{hint} is set")
    return CheckResult(var_name, "fail", f"{hint} — set {var_name} in env or .env")


def _check_llm_config() -> CheckResult:
    import os

    provider = os.environ.get("LLM_PROVIDER", "stub")
    if provider == "stub":
        return CheckResult("llm_provider", "ok", "Using stub provider (no API key needed)")
    if provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            return CheckResult(
                "llm_provider",
                "fail",
                "Provider anthropic but ANTHROPIC_API_KEY is not set",
            )
        if not key.startswith("sk-ant-") or len(key) < 20:
            return CheckResult(
                "llm_provider",
                "fail",
                (
                    "ANTHROPIC_API_KEY does not look like an Anthropic key "
                    "(expected 'sk-ant-…' with length >= 20). "
                    "A live check would require a network call and is skipped; "
                    "run `orchestrator run` to catch 401s."
                ),
            )
        return CheckResult(
            "llm_provider",
            "ok",
            f"Provider anthropic; key looks well-formed ({len(key)} chars)",
        )
    return CheckResult("llm_provider", "warn", f"Unknown provider: {provider}")


def _check_agents_dir() -> CheckResult:
    import os

    agents_dir = Path(os.environ.get("AGENTS_DIR", "agents"))
    if not agents_dir.is_dir():
        return CheckResult(
            "agents_dir",
            "warn",
            f"{agents_dir} not found (place agent YAMLs there or set AGENTS_DIR)",
        )

    try:
        from app.modules.ai.agents import list_agent_records

        records = list_agent_records(agents_dir)
    except Exception as exc:  # pragma: no cover — listing swallows per-file errors.
        return CheckResult("agents_dir", "fail", f"failed to list agents: {exc}")

    # Any ``.yaml`` present in the dir that didn't parse counts as a hard fail:
    # malformed definitions should not silently hide from operators.
    present = {p.name for p in agents_dir.glob("*.yaml")}
    parsed = {r.path.name for r in records}
    skipped = sorted(present - parsed)
    if skipped:
        return CheckResult(
            "agents_dir",
            "fail",
            f"{len(skipped)} unreadable agent file(s): {', '.join(skipped)}",
        )

    return CheckResult(
        "agents_dir",
        "ok",
        f"{agents_dir} loaded {len(records)} agent definition(s)",
    )


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def format_human(results: list[CheckResult]) -> str:
    """Render a ✓/✗ checklist."""
    lines: list[str] = []
    for r in results:
        icon = "✓" if r.status == "ok" else ("⚠" if r.status == "warn" else "✗")
        lines.append(f"  {icon} {r.name}: {r.detail}")
    return "\n".join(lines)


def format_json(results: list[CheckResult]) -> str:
    """Render structured JSON."""
    data: list[dict[str, Any]] = [{"name": r.name, "status": r.status, "detail": r.detail} for r in results]
    return json.dumps(data, indent=2)
