"""Static check: router and CLI modules must not call SQL/LLM directly.

Per AC-9: route handlers and CLI commands are thin adapters.
- ``router.py`` must not touch the DB, HTTP, or LLM layers directly —
  queries and outbound calls go through the service layer.
- ``cli.py`` must not touch the DB or LLM layers directly — it is an HTTP
  client of the orchestrator service (CLAUDE.md: "not a DB back door").
  ``httpx`` is *how* the CLI talks to the service, so it is permitted.

This test fails loudly if someone deliberately adds a forbidden import.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent.resolve()

# Per-target forbidden roots: the CLI is allowed to use httpx (it's an HTTP
# client of the service), but router.py is not (which would imply one
# handler calling another via HTTP rather than via the service layer).
_FORBIDDEN_BY_TARGET: dict[str, tuple[str, ...]] = {
    "router.py": ("sqlalchemy", "httpx", "anthropic", "openai"),
    "cli.py": ("sqlalchemy", "anthropic", "openai"),
}

# (module, symbol) pairs that ARE allowed — type-only imports needed for
# FastAPI dependency injection and similar.  Adding a new allowance requires
# a comment explaining why.
_ALLOWED_IMPORTS: set[tuple[str, str]] = {
    # AsyncSession is used only as a type annotation on Depends() parameters;
    # queries go through the service layer.
    ("sqlalchemy.ext.asyncio", "AsyncSession"),
    # async_sessionmaker is used only as a type annotation on the
    # Depends(get_session_factory) parameter passed through to the service.
    ("sqlalchemy.ext.asyncio", "async_sessionmaker"),
}

_TARGETS = [
    _REPO_ROOT / "src" / "app" / "modules" / "ai" / "router.py",
    _REPO_ROOT / "src" / "app" / "cli.py",
]


def _module_is_forbidden(module: str, roots: tuple[str, ...]) -> bool:
    return any(module == root or module.startswith(root + ".") for root in roots)


def _collect_violations(path: Path) -> list[str]:
    roots = _FORBIDDEN_BY_TARGET.get(path.name, ())
    tree = ast.parse(path.read_text(), filename=str(path))
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if not _module_is_forbidden(module, roots):
                continue
            for alias in node.names:
                if (module, alias.name) in _ALLOWED_IMPORTS:
                    continue
                violations.append(
                    f"{path.name}:{node.lineno} — forbidden import "
                    f"`from {module} import {alias.name}`"
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _module_is_forbidden(alias.name, roots):
                    violations.append(
                        f"{path.name}:{node.lineno} — forbidden import `import {alias.name}`"
                    )

    return violations


class TestThinAdapters:
    @pytest.mark.parametrize(
        "target",
        _TARGETS,
        ids=[t.name for t in _TARGETS],
    )
    def test_no_forbidden_imports(self, target: Path) -> None:
        violations = _collect_violations(target)
        assert violations == [], "\n".join(violations)


class TestSanityCheck:
    def test_walker_detects_injected_violation(self) -> None:
        """Smoke check on the walker itself — inject a sqlalchemy import and confirm the check would fail."""
        src = "from sqlalchemy import select\n"
        tree = ast.parse(src)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if _module_is_forbidden(module, _FORBIDDEN_BY_TARGET["router.py"]):
                    for alias in node.names:
                        if (module, alias.name) not in _ALLOWED_IMPORTS:
                            found = True
        assert found, "walker failed to flag an obvious sqlalchemy import"


# ---------------------------------------------------------------------------
# Anthropic import quarantine (T-064)
# ---------------------------------------------------------------------------
#
# The ``anthropic`` SDK is permitted in exactly two files: ``core/llm.py``
# (factory branch — deferred import inside a ``case``) and ``core/llm_anthropic.py``
# (the provider implementation itself).  Any other file importing ``anthropic``
# breaks the thin-adapter seam declared by AD-3 and violates CLAUDE.md's
# "LLM through the abstraction" rule.

_SRC_APP_ROOT = _REPO_ROOT / "src" / "app"

_ANTHROPIC_ALLOWED = frozenset(
    {
        _SRC_APP_ROOT / "core" / "llm.py",
        _SRC_APP_ROOT / "core" / "llm_anthropic.py",
    }
)


def _walk_py_files(root: Path) -> list[Path]:
    """Every ``.py`` file under *root*, excluding auto-generated migrations."""
    results: list[Path] = []
    for path in root.rglob("*.py"):
        if "migrations" in path.parts:
            continue
        results.append(path)
    return results


def _file_imports_anthropic(path: Path) -> bool:
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "anthropic" or module.startswith("anthropic."):
                return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "anthropic" or alias.name.startswith("anthropic."):
                    return True
    return False


class TestAnthropicImportQuarantine:
    def test_anthropic_only_imported_by_llm_seam(self) -> None:
        violations: list[str] = []
        for path in _walk_py_files(_SRC_APP_ROOT):
            if path in _ANTHROPIC_ALLOWED:
                continue
            if _file_imports_anthropic(path):
                violations.append(str(path.relative_to(_REPO_ROOT)))
        assert violations == [], (
            "The `anthropic` SDK may only be imported from core/llm.py + "
            "core/llm_anthropic.py. Violators:\n  " + "\n  ".join(violations)
        )

    def test_walker_flags_injected_anthropic_import(self, tmp_path: Path) -> None:
        """Sanity: the scanner reliably catches an offending import."""
        offender = tmp_path / "leak.py"
        offender.write_text("import anthropic\n")
        assert _file_imports_anthropic(offender) is True


# ---------------------------------------------------------------------------
# subprocess + yaml quarantines (FEAT-005 / T-106)
# ---------------------------------------------------------------------------
#
# ``subprocess`` may only be imported by the lifecycle ``git`` helper
# (``tools/lifecycle/git.py``) — every other file that shells out is an
# anti-pattern per CLAUDE.md's "async all the way" rule.  ``yaml`` may only
# be imported by the agent loader (``modules/ai/agents.py``) since every
# other YAML read would imply agent-definition authority moving out of the
# loader's seam.


_SUBPROCESS_ALLOWED = frozenset(
    {
        _SRC_APP_ROOT / "modules" / "ai" / "tools" / "lifecycle" / "git.py",
    }
)

_YAML_ALLOWED = frozenset(
    {
        _SRC_APP_ROOT / "modules" / "ai" / "agents.py",
        # The CLI deferred-imports yaml to parse operator-supplied intake
        # files (``--intake-file path/to/input.yaml``).  Not the agent
        # loader's job.
        _SRC_APP_ROOT / "cli.py",
    }
)


def _file_imports(path: Path, *, module_prefix: str) -> bool:
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == module_prefix or module.startswith(module_prefix + "."):
                return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module_prefix or alias.name.startswith(
                    module_prefix + "."
                ):
                    return True
    return False


class TestSubprocessQuarantine:
    def test_subprocess_confined_to_lifecycle_git(self) -> None:
        violations: list[str] = []
        for path in _walk_py_files(_SRC_APP_ROOT):
            if path in _SUBPROCESS_ALLOWED:
                continue
            if _file_imports(path, module_prefix="subprocess"):
                violations.append(str(path.relative_to(_REPO_ROOT)))
        assert violations == [], (
            "The `subprocess` module may only be imported from "
            "tools/lifecycle/git.py (FEAT-005). Violators:\n  "
            + "\n  ".join(violations)
        )


class TestYamlQuarantine:
    def test_yaml_confined_to_agent_loader(self) -> None:
        violations: list[str] = []
        for path in _walk_py_files(_SRC_APP_ROOT):
            if path in _YAML_ALLOWED:
                continue
            if _file_imports(path, module_prefix="yaml"):
                violations.append(str(path.relative_to(_REPO_ROOT)))
        assert violations == [], (
            "The `yaml` module may only be imported from modules/ai/agents.py. "
            "Violators:\n  " + "\n  ".join(violations)
        )
