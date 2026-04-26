"""Agent definition schema.

The YAML agent contract: typed Pydantic models that the loader (T-032) will
populate from files on disk.  Pure schema — no I/O here.

Reaching any node in :attr:`AgentDefinition.terminal_nodes` ends the run with
``stop_reason=done_node``.  A reserved ``terminate`` tool (built by T-034)
ends the run with ``stop_reason=policy_terminated`` — so node names must not
collide with that reserved token.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from app.core.exceptions import NotFoundError
from app.modules.ai.tools import TERMINATE_TOOL_NAME

logger = logging.getLogger(__name__)

_CAMEL_CONFIG = ConfigDict(populate_by_name=True, alias_generator=to_camel)


class AgentNode(BaseModel):
    """One engine-dispatchable node the policy may select as a tool."""

    model_config = _CAMEL_CONFIG

    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = 300


class AgentFlow(BaseModel):
    """Declared entry point + allowed transitions.

    Transitions are advisory in v1 — the runtime loop trusts the policy's
    tool selection and records the choice.  Explicit transition enforcement
    is a FEAT-003+ concern.

    FEAT-009 / T-220: ``policy`` selects how the runtime loop picks the
    next node.

    * ``llm`` (default for backward compatibility) — LLM-as-policy. The
      model is asked which tool to call; transitions in the YAML are
      advisory. This is the path every pre-FEAT-009 agent runs on.
    * ``deterministic`` — node selection is a pure function of the YAML
      transitions + run state via the FlowResolver (FEAT-009 T-211).
      Multi-target transitions must be expressed as ``branch:`` blocks;
      no LLM call participates in node selection. The path artefacts are
      produced by registered executors, not in-process tools.
    """

    model_config = _CAMEL_CONFIG

    entry_node: str
    transitions: dict[str, Any] = Field(default_factory=dict)
    policy: Literal["llm", "deterministic"] = "llm"


class BudgetDefaults(BaseModel):
    """Optional defaults the CLI / API can override per-run."""

    model_config = _CAMEL_CONFIG

    max_steps: int | None = None
    max_tokens: int | None = None


class AgentPolicy(BaseModel):
    """LLM-facing policy configuration (v1: per-node system prompts).

    ``system_prompts`` maps node names to filesystem paths holding the system
    prompt the policy must use when that node is selected.  Path contents are
    resolved and existence-checked by the loader (:func:`_parse_file`), not
    by this schema — tests that build :class:`AgentDefinition` directly may
    reference paths that do not exist on disk.
    """

    model_config = _CAMEL_CONFIG

    system_prompts: dict[str, Path] = Field(default_factory=dict)


class AgentDefinition(BaseModel):
    """The full agent contract as authored in YAML.

    ``agent_definition_hash`` is populated by the loader (T-032) over the
    canonical byte form of the parsed document; it is ``None`` when the
    model is constructed by hand (e.g. in tests).
    """

    model_config = _CAMEL_CONFIG

    ref: str
    version: str
    description: str
    nodes: list[AgentNode]
    flow: AgentFlow
    intake_schema: dict[str, Any] = Field(default_factory=dict)
    terminal_nodes: set[str]
    default_budget: BudgetDefaults = Field(default_factory=BudgetDefaults)
    policy: AgentPolicy = Field(default_factory=AgentPolicy)
    agent_definition_hash: str | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> AgentDefinition:
        node_names = {n.name for n in self.nodes}

        if len(node_names) != len(self.nodes):
            raise ValueError("agent node names must be unique")

        if TERMINATE_TOOL_NAME in node_names:
            raise ValueError(f"node name {TERMINATE_TOOL_NAME!r} is reserved for the built-in terminate tool")

        if not self.terminal_nodes:
            raise ValueError("terminal_nodes must be non-empty")

        missing = self.terminal_nodes - node_names
        if missing:
            raise ValueError(f"terminal_nodes references unknown nodes: {sorted(missing)}")

        if self.flow.entry_node not in node_names:
            raise ValueError(f"flow.entry_node {self.flow.entry_node!r} is not among declared nodes")

        unknown_prompt_nodes = set(self.policy.system_prompts.keys()) - node_names
        if unknown_prompt_nodes:
            raise ValueError(f"system_prompts references unknown nodes: {sorted(unknown_prompt_nodes)}")

        return self


# ---------------------------------------------------------------------------
# Filesystem loader (T-032)
# ---------------------------------------------------------------------------


def _canonicalize(raw: Any) -> bytes:
    """Return a byte form of *raw* that's stable across Python/OS/YAML versions.

    We parse YAML, then re-serialize via ``json.dumps(..., sort_keys=True)``
    so whitespace / key-ordering differences in the source file do not change
    the hash.
    """
    return json.dumps(raw, sort_keys=True, default=str).encode("utf-8")


def _validate_prompt_path(prompt_path: Path, repo_root: Path) -> None:
    """Resolve *prompt_path* under *repo_root* and confirm it exists.

    Relative paths resolve against *repo_root*; absolute paths resolve
    as-is.  Both must land inside *repo_root* (no ``../`` escapes) and
    the target file must exist.
    """
    candidate = prompt_path if prompt_path.is_absolute() else repo_root / prompt_path
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError(f"prompt file not found: {prompt_path}") from exc
    root_resolved = repo_root.resolve()
    if root_resolved != resolved and root_resolved not in resolved.parents:
        raise ValueError(f"prompt path escapes repo root: {prompt_path}")


def _parse_file(path: Path, *, repo_root: Path | None = None) -> AgentDefinition:
    """Load *path*, validate, and return an :class:`AgentDefinition` with hash set.

    Prompt-path references in ``policy.system_prompts`` are resolved relative
    to *repo_root* (defaults to :func:`Path.cwd`) and existence-checked.
    """
    raw = yaml.safe_load(path.read_text())
    digest = hashlib.sha256(_canonicalize(raw)).hexdigest()
    agent = AgentDefinition.model_validate(raw)
    root = (repo_root or Path.cwd()).resolve()
    for prompt_path in agent.policy.system_prompts.values():
        _validate_prompt_path(prompt_path, root)
    return agent.model_copy(update={"agent_definition_hash": digest})


def _resolve_path(agents_dir: Path, ref: str) -> Path | None:
    """Return the YAML path for *ref* under *agents_dir*, or ``None`` if absent.

    *ref* may be ``"name"`` or ``"name@version"``; the versioned file wins
    when both a bare and a versioned YAML exist.
    """
    if "@" in ref:
        candidate = agents_dir / f"{ref}.yaml"
        return candidate if candidate.is_file() else None

    versioned = sorted(agents_dir.glob(f"{ref}@*.yaml"))
    if versioned:
        return versioned[-1]  # highest version by string sort
    bare = agents_dir / f"{ref}.yaml"
    return bare if bare.is_file() else None


def load_agent(ref: str, agents_dir: Path) -> AgentDefinition:
    """Load a single agent definition by ref.

    Raises :class:`NotFoundError` if the ref does not resolve to a file.
    """
    path = _resolve_path(agents_dir, ref) if agents_dir.is_dir() else None
    if path is None:
        raise NotFoundError(f"agent not found: {ref}")
    return _parse_file(path)


def list_agents(agents_dir: Path) -> list[AgentDefinition]:
    """Return every agent definition in *agents_dir*, sorted by (ref, version).

    Missing directory → empty list (expected on first-time setup).
    Unreadable files are skipped with a WARNING log — one bad YAML must not
    take out the whole listing.  Invalid YAML / schema surfaces as a warning
    here (per-file), but ``load_agent`` on a specific broken ref still
    raises (so operators see the error).
    """
    return [rec.definition for rec in list_agent_records(agents_dir)]


@dataclass(frozen=True, slots=True)
class AgentRecord:
    """An :class:`AgentDefinition` paired with the file it was loaded from."""

    definition: AgentDefinition
    path: Path


def list_agent_records(agents_dir: Path) -> list[AgentRecord]:
    """Like :func:`list_agents` but returns ``(definition, path)`` pairs.

    Useful when the caller needs to surface the file path (e.g. the
    ``/api/v1/agents`` endpoint).
    """
    if not agents_dir.is_dir():
        return []

    records: list[AgentRecord] = []
    for path in sorted(agents_dir.glob("*.yaml")):
        try:
            definition = _parse_file(path)
        except Exception as exc:
            logger.warning("skipping unreadable agent file %s: %s", path, exc)
            continue
        records.append(AgentRecord(definition=definition, path=path))

    records.sort(key=lambda r: (r.definition.ref, r.definition.version))
    return records
