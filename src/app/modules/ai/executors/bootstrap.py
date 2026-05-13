"""Lifespan-time executor wiring (FEAT-009 / T-214 + T-218).

For every agent loaded from ``agents/`` and every node it declares,
register a concrete :class:`Executor` under
``(agent_ref, node_name)``.  ``v0.1.0`` nodes wrap the existing
``modules/ai/tools/lifecycle/*`` handlers via :class:`LocalExecutor`;
``v0.2.0`` (when it lands in T-222/T-223) registers fresh handlers.

The registration in PR 3 is intentionally minimal: it stands up the
binding so :func:`validate_executor_coverage` can succeed at boot, but
the runtime loop does **not** consume the registry yet — that's the
T-220 loop swap in PR 5.  The local-executor handlers below therefore
raise :class:`NotImplementedError` if invoked, which makes a premature
runtime-loop wiring fail loud rather than silently returning a stub
envelope.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.ai.executors.base import DispatchContext
from app.modules.ai.executors.binding import ExecutorBinding, no_executor
from app.modules.ai.executors.coverage import (
    ExecutorCoverageError,
    validate_executor_coverage,
)
from app.modules.ai.executors.local import LocalExecutor
from app.modules.ai.executors.registry import ExecutorRegistry

if TYPE_CHECKING:
    # FEAT-010 import quarantine — only the helper signature pulls
    # ``FlowEngineLifecycleClient`` for typing; never at module scope so
    # importing ``runtime_deterministic`` (which imports the registry's
    # bootstrap surface transitively) does not pull the engine HTTP
    # client into ``sys.modules``.
    from app.core.llm import LLMProvider
    from app.modules.ai.lifecycle.engine_client import FlowEngineLifecycleClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# v0.3.0 collaborator bundle
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LifecycleV03Collaborators:
    """Bundle of collaborators ``register_lifecycle_v03`` needs.

    The lifespan composition root constructs these once and hands them
    in.  Tests that don't load v0.3.0 omit them; the bootstrap then
    falls back to no_executor exemptions so the coverage validator
    still boots.
    """

    lifecycle_client: FlowEngineLifecycleClient
    llm_provider: LLMProvider
    session_factory: async_sessionmaker[AsyncSession]
    workflow_ids: Mapping[str, uuid.UUID]
    actor: str | None = "lifecycle-agent"
    max_corrections: int = 2


def register_all_executors(
    registry: ExecutorRegistry,
    agents_dir: Path,
    *,
    v03_collaborators: LifecycleV03Collaborators | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Register an executor for every node of every loaded agent.

    The function is the single source of truth for the executor wiring;
    lifespan calls it once at boot and then runs the coverage validator.

    ``v03_collaborators`` is required to wire the production
    ``lifecycle-agent@0.3.0`` bindings (FEAT-011 / T-254).  When absent,
    every v0.3.0 node is declared as an explicit no_executor exemption
    so the coverage validator still boots — useful for tests that don't
    exercise v0.3.0 directly.  A run against the v0.3.0 agent without
    real bindings would fail at dispatch time naming the unbound node.
    """
    from app.modules.ai.agents import list_agents

    agents = list_agents(agents_dir)
    for agent in agents:
        if agent.ref.startswith("lifecycle-agent@0.1"):
            _register_lifecycle_v01(registry, agent.ref, [n.name for n in agent.nodes])
        elif agent.ref.startswith("lifecycle-agent@0.2"):
            # FEAT-014 / T-286: v0.2's ``request_work_item_load`` reads
            # the body from DB.  Prefer the explicit kwarg; fall back to
            # the v0.3 collaborators' session_factory if available; else
            # the handler will surface ``loaded=False`` at dispatch time.
            v02_session_factory = session_factory or (
                v03_collaborators.session_factory if v03_collaborators else None
            )
            _register_lifecycle_v02(registry, agent.ref, session_factory=v02_session_factory)
        elif agent.ref.startswith("lifecycle-agent@0.3"):
            if v03_collaborators is None:
                _exempt_lifecycle_v03(agent.ref, [n.name for n in agent.nodes])
            else:
                register_lifecycle_v03(
                    registry,
                    agent.ref,
                    lifecycle_client=v03_collaborators.lifecycle_client,
                    llm_provider=v03_collaborators.llm_provider,
                    session_factory=v03_collaborators.session_factory,
                    workflow_ids=v03_collaborators.workflow_ids,
                    actor=v03_collaborators.actor,
                    max_corrections=v03_collaborators.max_corrections,
                )

    logger.info(
        "executor registry: %d binding(s) across %d agent(s)",
        len(registry.registered_keys()),
        len(agents),
    )


def run_coverage_validation(registry: ExecutorRegistry, agents_dir: Path) -> None:
    """Refuse to return when any loaded agent's node is unbound.

    Raises :class:`ExecutorCoverageError` listing every offending
    ``(agent_ref, node_name)`` so an operator can resolve all bootstrap
    gaps in one pass.
    """
    from app.modules.ai.agents import list_agents

    agents = list_agents(agents_dir)
    decls: list[Mapping[str, Any]] = [{"ref": a.ref, "nodes": [{"name": n.name} for n in a.nodes]} for a in agents]
    validate_executor_coverage(registry, decls)


# ---------------------------------------------------------------------------
# v0.1.0 — placeholder handlers
# ---------------------------------------------------------------------------


def _register_lifecycle_v01(registry: ExecutorRegistry, agent_ref: str, node_names: list[str]) -> None:
    """Register a ``LocalExecutor`` for every v0.1.0 lifecycle node.

    The handler raises :class:`NotImplementedError` if invoked — the
    real bridge from ``DispatchContext`` to the existing
    ``modules/ai/tools/lifecycle/*`` ``handle(args, *, memory=...)``
    callables lands with the runtime-loop swap in T-220 (PR 5), which
    is the first caller that actually dispatches through the registry.
    """
    for node_name in node_names:
        executor = LocalExecutor(
            ref=f"local:{node_name}",
            handler=_make_v01_placeholder(agent_ref, node_name),
        )
        registry.register(agent_ref, node_name, executor)


def _make_v01_placeholder(agent_ref: str, node_name: str):  # type: ignore[no-untyped-def]
    """Return a handler that fails loud if invoked before T-220 lands."""

    async def _handler(_ctx: DispatchContext) -> Mapping[str, Any]:
        raise NotImplementedError(
            f"v0.1.0 executor invocation not wired yet "
            f"(agent={agent_ref!r}, node={node_name!r}); "
            "real bridging lands with the T-220 runtime-loop swap (FEAT-009 PR 5)"
        )

    return _handler


# ---------------------------------------------------------------------------
# v0.2.0 — real handlers (FEAT-009 / T-223)
# ---------------------------------------------------------------------------


def _register_lifecycle_v02(
    registry: ExecutorRegistry,
    agent_ref: str,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Register the v0.2.0 demo agent's local executors.

    v0.2.0 is a minimal demo proving the new shape end-to-end (dispatch
    verbs + deterministic policy + executor seam).  It is **not** a
    drop-in replacement for v0.1.0 — migrating the full lifecycle (with
    its eight original tools, LifecycleMemory semantics, and the
    wait_for_implementation pause) is tracked as a separate future FEAT.

    FEAT-014 / T-286: ``request_work_item_load`` reads the brief body
    from the ``work_items`` row instead of opening a file under
    ``docs/work-items/``.  When *session_factory* is absent (tests that
    don't load the v0.3 collaborators), the handler returns ``loaded=False``
    rather than touching disk.
    """
    registry.register(
        agent_ref,
        "request_work_item_load",
        LocalExecutor(
            ref="local:request_work_item_load",
            handler=_make_work_item_load_handler(session_factory),
        ),
    )
    registry.register(
        agent_ref,
        "request_closure",
        LocalExecutor(ref="local:request_closure", handler=_handle_request_closure),
    )


async def _load_work_item_body(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    external_ref: str,
) -> tuple[str | None, str | None]:
    """Return ``(body_md, source_path)`` for the work item identified by
    *external_ref*.  Either component may be ``None``.

    Short-lived session per call — matches the runtime loop's
    one-session-per-iteration discipline and never holds a session across
    yields.  Imports SQLAlchemy + the model lazily to keep import-time
    surface small.
    """
    from sqlalchemy import select as _select

    from app.modules.ai.models import WorkItem as _WorkItem

    async with session_factory() as session:
        row = await session.scalar(
            _select(_WorkItem).where(_WorkItem.external_ref == external_ref)
        )
        if row is None:
            return None, None
        return row.body_md, row.source_path


def _make_work_item_load_handler(
    session_factory: async_sessionmaker[AsyncSession] | None,
):  # type: ignore[no-untyped-def]
    """Build the ``request_work_item_load`` handler bound to *session_factory*.

    FEAT-014 / T-286: returns a coroutine that reads ``intake.workItem.id``
    (or legacy ``intake.workItemId``), looks up the row, and emits a
    memory patch carrying the body.  The legacy ``intake.workItemPath``
    is preserved on the memory patch for downstream consumers that still
    expect it during the deprecation window (T-288).
    """

    async def _handler(ctx: DispatchContext) -> Mapping[str, Any]:
        work_item_raw: Any = ctx.intake.get("workItem")
        external_ref_raw: Any
        if isinstance(work_item_raw, Mapping):
            external_ref_raw = cast("Mapping[str, Any]", work_item_raw).get("id")
        else:
            external_ref_raw = ctx.intake.get("workItemId")
        external_ref: str | None = (
            str(external_ref_raw)  # external_ref_raw: Any
            if external_ref_raw is not None
            else None
        )

        if external_ref is None or session_factory is None:
            # Either no work-item is wired (non-lifecycle agent) or
            # session_factory wasn't provided (test wiring); return a
            # safe ``loaded=False`` rather than touching disk.
            return {
                "loaded": False,
                "externalRef": external_ref,
                "__memory_patch": {
                    "work_item_body": None,
                    "work_item_id": external_ref,
                    "work_item_path": ctx.intake.get("workItemPath"),
                },
            }

        body_md, source_path = await _load_work_item_body(
            session_factory, external_ref=external_ref
        )
        return {
            "loaded": body_md is not None,
            "externalRef": external_ref,
            "__memory_patch": {
                "work_item_body": body_md,
                "work_item_id": external_ref,
                # Legacy compat: some prompts / downstream consumers still
                # read ``work_item_path`` during the FEAT-014 deprecation
                # window.  Removed in the follow-up cut.
                "work_item_path": source_path or ctx.intake.get("workItemPath"),
            },
        }

    return _handler


async def _handle_request_closure(_ctx: DispatchContext) -> Mapping[str, Any]:
    """Mark closure (terminal). Pure code; no LLM."""
    return {"closed": True}


# ---------------------------------------------------------------------------
# v0.3.0 — PR 2 placeholder exemptions (real wiring in PR 3 / T-254)
# ---------------------------------------------------------------------------


def _exempt_lifecycle_v03(agent_ref: str, node_names: list[str]) -> None:
    """Declare every v0.3.0 node as an explicit no_executor exemption.

    Used when ``register_all_executors`` is called without
    ``v03_collaborators`` (e.g. test contexts where the LLM provider /
    engine client / session factory aren't built).  Coverage validation
    boots cleanly; a run started against this agent would still fail at
    dispatch time naming the unbound node.
    """
    reason = "v0.3.0 collaborators not provided to register_all_executors"
    for node_name in node_names:
        no_executor(agent_ref, node_name, reason)


# ---------------------------------------------------------------------------
# v0.3.0 — production bindings (FEAT-011 / T-254)
# ---------------------------------------------------------------------------


def _patch_review(result: Mapping[str, Any], current_memory: Mapping[str, Any]) -> dict[str, Any]:
    """Canonical writer for ``lifecycle.v1.reviewHistory`` (BUG-010).

    Lifted to module scope (IMP-003 / T-270) so the stub-pass reviewer
    can reuse the exact same shape contract — drift between the LLM
    path and the stub is impossible by construction when both run
    through this single builder.

    The runtime's ``__memory_patch`` applier replaces top-level keys
    verbatim, so we read the existing namespace, append the new entry,
    and return the full merged blob.
    """
    from app.modules.ai.tools.lifecycle.memory import (
        LIFECYCLE_MEMORY_NS,
        LifecycleMemory,
        LifecycleReview,
        read_lifecycle_memory,
        to_run_memory,
    )

    memory_model: LifecycleMemory = read_lifecycle_memory(current_memory)
    attempt = sum(1 for r in memory_model.review_history if r.task_id == str(result.get("task_id", ""))) + 1
    verdict_raw = str(result.get("verdict", ""))
    verdict: Literal["pass", "fail"] = "pass" if verdict_raw == "pass" else "fail"
    new_review = LifecycleReview(
        task_id=str(result.get("task_id", "")),
        attempt=attempt,
        verdict=verdict,
        feedback=str(result.get("feedback", "")),
        written_to="memory",
    )
    memory_model.review_history.append(new_review)
    return {LIFECYCLE_MEMORY_NS: to_run_memory(memory_model)}


def register_lifecycle_v03(
    registry: ExecutorRegistry,
    agent_ref: str,
    *,
    lifecycle_client: FlowEngineLifecycleClient | None,
    llm_provider: LLMProvider,
    session_factory: async_sessionmaker[AsyncSession],
    workflow_ids: Mapping[str, uuid.UUID],
    max_corrections: int = 2,
    actor: str | None = "lifecycle-agent",
    skip_review_implementation: bool = False,
) -> None:
    """Register the eight production bindings for ``lifecycle-agent@0.3.0``.

    When ``skip_review_implementation=True``, the LLM ``review_implementation``
    binding is omitted so a sibling variant (e.g. FEAT-015's
    ``lifecycle-agent@0.4.0-manual``) can register a human reviewer in
    that slot.  The companion ``approve_review`` engine binding is
    unaffected and registers as usual — both reviewer variants route
    through it on a ``pass`` verdict.

    The mapping table lives in ``docs/design/feat-011-lifecycle-deterministic-port.md``
    (section "Node-to-executor mapping table (v0.3.0)").  Composite nodes
    chain LLM-content + engine via :class:`CompositeLLMEngineExecutor`
    (Option 1 from the design doc).

    PR 3 simplifications (documented in the FEAT-011 PR 3 body):

    * ``generate_tasks`` collapses the T1xN fanout into a single engine
      call — the production T1 fanout lands in a follow-up PR.
    * ``request_implementation`` registers as a plain
      :class:`HumanExecutor`; the post-resume T9 engine call is deferred
      to PR 4 (T-256 may extend this with a composite human+engine
      adapter).
    * ``correct_implementation`` is a placeholder
      :class:`LocalExecutor` that increments
      ``correction_attempts[task_id]`` via ``__memory_patch`` and
      returns ``outcome="rejected"`` — enough for the resolver's
      ``correction_attempts_under_bound`` predicate.  T-258 (PR 4)
      polishes this with the Approval-row write.
    * ``terminate_correction_budget`` declares a ``no_executor``
      exemption — it's a terminal failure node the runtime maps to
      ``RunStatus.FAILED`` via the existing stop-condition pipeline.
    """
    if lifecycle_client is None:
        raise RuntimeError("register_lifecycle_v03: lifecycle_client is required (engine-bound nodes need it)")
    work_item_workflow_id = workflow_ids.get("work_item_workflow")
    if work_item_workflow_id is None:
        raise RuntimeError(
            "register_lifecycle_v03: workflow_ids missing 'work_item_workflow' — "
            "register_work_item (BUG-003) needs the engine workflow id at boot. "
            "Ensure lifespan.ensure_workflows() ran before register_all_executors."
        )
    task_workflow_id = workflow_ids.get("task_workflow")
    if task_workflow_id is None:
        raise RuntimeError(
            "register_lifecycle_v03: workflow_ids missing 'task_workflow' — "
            "propose_tasks (BUG-004) needs the engine task workflow id at boot."
        )

    # Synthetic ``start`` entry node — the flow resolver treats
    # ``entryNode`` as the previous-node marker, so the YAML declares
    # ``start`` solely so its outgoing transition selects
    # ``load_work_item`` as the first dispatched node.  ``start`` itself
    # is never dispatched; satisfy coverage with an explicit exemption.
    no_executor(
        agent_ref,
        "start",
        "synthetic entry marker; never dispatched (resolver-level only)",
    )

    # Local imports to keep the bootstrap module's import graph small —
    # mirrors the ``register_engine_executor`` pattern.
    from app.modules.ai.executors.composite import CompositeLLMEngineExecutor
    from app.modules.ai.executors.human import HumanExecutor
    from app.modules.ai.executors.lifecycle_schemas import (
        GeneratePlanResult,
        GenerateTasksResult,
        LoadWorkItemResult,
        ReviewImplementationResult,
    )
    from app.modules.ai.executors.llm_content import LLMContentExecutor
    from app.modules.ai.tools.lifecycle.memory import (
        LifecycleMemory,
        LifecycleTask,
        WorkItemRef,
        find_current_task,
        read_lifecycle_memory,
        write_lifecycle_memory,
    )

    prompts_dir = Path(__file__).parent / "prompts" / "lifecycle"

    def _load_prompt(name: str) -> str:
        path = prompts_dir / f"{name}.md"
        return path.read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # BUG-003: split former composite ``load_work_item`` into two nodes —
    #
    #   load_work_item       LLMContentExecutor only — synthesises the
    #                        brief and persists it into LifecycleMemory.
    #   register_work_item   EngineCreateExecutor — calls W1 create_item
    #                        with the brief's title + external ref,
    #                        captures the new engine item id into Run.intake
    #                        and inserts the local WorkItem row.
    #
    # The composite invariant ("transition existing item") stays clean;
    # creation is its own seam alongside :class:`EngineExecutor`.
    # ------------------------------------------------------------------

    def _patch_load_work_item(result: Mapping[str, Any], _current_memory: Mapping[str, Any]) -> dict[str, Any]:
        try:
            wi_type = str(result.get("work_item_id", "FEAT")).split("-", 1)[0] or "FEAT"
        except Exception:
            wi_type = "FEAT"
        if wi_type not in ("FEAT", "BUG", "IMP"):
            wi_type = "FEAT"
        memory = LifecycleMemory(
            work_item=WorkItemRef(
                id=str(result.get("work_item_id", "")),
                type=wi_type,  # type: ignore[arg-type]
                title=str(result.get("title", "")),
                path="",
            )
        )
        return write_lifecycle_memory(memory)

    async def _load_work_item_brief_db(ctx: DispatchContext) -> Mapping[str, Any]:
        # FEAT-014 / T-287: read the brief body from ``WorkItem.body_md``
        # keyed on the run's ``intake.workItem.id`` (or legacy
        # ``workItemId``).  Replaces BUG-005's disk read of
        # ``intake.workItemPath`` — the orchestrator no longer needs
        # filesystem access to the caller's brief.  Falls back to a
        # placeholder when the row is missing or body is NULL so the
        # LLM still has *something* to synthesise from.
        external_ref_raw: Any = ctx.intake.get("workItemId")
        work_item_raw: Any = ctx.intake.get("workItem")
        if isinstance(work_item_raw, Mapping):
            external_ref_raw = (
                cast("Mapping[str, Any]", work_item_raw).get("id") or external_ref_raw
            )
        external_ref = (
            str(external_ref_raw)  # external_ref_raw: Any
            if external_ref_raw is not None
            else None
        )

        # ``workItemId`` is also looked up in the prompt template; expose
        # the resolved external ref so the template binding works either
        # way (new shape via ``intake.workItem.id`` OR legacy intake).
        bindings: dict[str, Any] = {
            "workItemId": external_ref or "",
            "workItemBrief": "",
        }

        if external_ref is None:
            bindings["workItemBrief"] = (
                "<!-- no work_item in intake; synthesize from the run's intake alone -->"
            )
            return bindings

        body_md, _ = await _load_work_item_body(
            session_factory, external_ref=external_ref
        )
        if body_md is None:
            bindings["workItemBrief"] = (
                f"<!-- work_item {external_ref} has no body stored; "
                "synthesize from the external ref alone -->"
            )
        else:
            bindings["workItemBrief"] = body_md
        return bindings

    registry.register(
        agent_ref,
        "load_work_item",
        LLMContentExecutor(
            ref="llm:load_work_item",
            system_prompt=_load_prompt("load_work_item"),
            user_prompt_template=(
                "Synthesize a brief for the work item with id `{workItemId}`. "
                "The full body follows.\n\n"
                "----- BEGIN WORK ITEM BRIEF -----\n"
                "{workItemBrief}\n"
                "----- END WORK ITEM BRIEF -----\n"
            ),
            result_schema=LoadWorkItemResult,
            llm_provider=llm_provider,
            memory_patch_builder=_patch_load_work_item,
            prompt_context_loader=_load_work_item_brief_db,
            session_factory=session_factory,
        ),
    )

    # ------------------------------------------------------------------
    # Engine create: register_work_item — W1 (BUG-003)
    # ------------------------------------------------------------------

    from app.modules.ai.executors.engine_create import EngineCreateExecutor

    registry.register(
        agent_ref,
        "register_work_item",
        EngineCreateExecutor(
            ref="engine:work_item.W1",
            workflow_id=work_item_workflow_id,
            lifecycle_client=lifecycle_client,
            session_factory=session_factory,
            opened_by=actor or "lifecycle-agent",
        ),
    )

    # ------------------------------------------------------------------
    # generate_tasks — LLM task list only (BUG-004: was a misconfigured
    # composite; the engine fanout moves to ``propose_tasks``).
    # ------------------------------------------------------------------

    def _patch_generate_tasks(result: Mapping[str, Any], _current_memory: Mapping[str, Any]) -> dict[str, Any]:
        tasks_in: list[Mapping[str, Any]] = list(result.get("tasks") or [])

        def _task(t: Mapping[str, Any]) -> LifecycleTask:
            complexity_raw = str(t.get("complexity", "medium") or "medium")
            complexity = complexity_raw if complexity_raw in ("small", "medium", "large") else "medium"
            return LifecycleTask(
                id=str(t.get("id", "")),
                title=str(t.get("title", "")),
                executor=str(t.get("executor", "")) or None,
                description=str(t.get("description", "") or ""),
                acceptance_criteria=[str(c) for c in cast(list[Any], t.get("acceptance_criteria") or [])],
                complexity=complexity,  # type: ignore[arg-type]
                depends_on=[str(d) for d in cast(list[Any], t.get("depends_on") or [])],
                files_hint=[str(f) for f in cast(list[Any], t.get("files_hint") or [])],
            )

        memory = LifecycleMemory(
            tasks=[_task(t) for t in tasks_in],
            current_task_id=(str(tasks_in[0].get("id")) if tasks_in else None),
        )
        return write_lifecycle_memory(memory)

    registry.register(
        agent_ref,
        "generate_tasks",
        LLMContentExecutor(
            ref="llm:generate_tasks",
            system_prompt=_load_prompt("generate_tasks"),
            user_prompt_template=(
                "Generate the task breakdown for work item id: {workItemId}.\n"
                "The full work-item brief follows; ground your task list in "
                "its acceptance criteria, scope, and constraints. Do not "
                "invent requirements that are not anchored in the brief.\n\n"
                "----- BEGIN WORK ITEM BRIEF -----\n"
                "{workItemBrief}\n"
                "----- END WORK ITEM BRIEF -----\n"
            ),
            result_schema=GenerateTasksResult,
            llm_provider=llm_provider,
            memory_patch_builder=_patch_generate_tasks,
            prompt_context_loader=_load_work_item_brief_db,
            session_factory=session_factory,
        ),
    )

    # ------------------------------------------------------------------
    # propose_tasks — T1xN create_item + T2+T4 + W2 (BUG-004 / new node).
    # ------------------------------------------------------------------

    from app.modules.ai.executors.propose_tasks import ProposeTasksExecutor

    registry.register(
        agent_ref,
        "propose_tasks",
        ProposeTasksExecutor(
            ref="propose_tasks",
            task_workflow_id=task_workflow_id,
            lifecycle_client=lifecycle_client,
            session_factory=session_factory,
            actor=actor,
        ),
    )

    # ------------------------------------------------------------------
    # Resolver shared by every per-task engine binding (BUG-004).
    # Reads ``LifecycleMemory.current_task_id`` and returns the matching
    # task's engine_item_id from memory.
    # ------------------------------------------------------------------

    from sqlalchemy import select as _select

    from app.modules.ai.models import RunMemory as _RunMemoryModel

    async def _resolve_current_task_engine_id(
        ctx: DispatchContext,
    ) -> uuid.UUID | None:
        async with session_factory() as session:
            mem = await session.scalar(_select(_RunMemoryModel).where(_RunMemoryModel.run_id == ctx.run_id))
        memory = read_lifecycle_memory((mem.data if mem is not None else {}) or {})
        task = find_current_task(memory)
        if task is None or task.engine_item_id is None:
            return None
        try:
            return uuid.UUID(task.engine_item_id)
        except ValueError:
            return None

    # ------------------------------------------------------------------
    # assign_task — T5: assigning → planning (current task).
    # ------------------------------------------------------------------

    register_engine_executor(
        registry,
        agent_ref,
        "assign_task",
        transition_key="task.T5",
        to_status="planning",
        lifecycle_client=lifecycle_client,
        session_factory=session_factory,
        actor=actor,
        target_id_resolver=_resolve_current_task_engine_id,
    )

    # ------------------------------------------------------------------
    # generate_plan — LLM plan + T6 only (current task).  T7 is a
    # separate ``approve_plan`` node so the unplanned-tasks loop can
    # branch on completion.
    # ------------------------------------------------------------------

    def _patch_generate_plan(result: Mapping[str, Any], current_memory: Mapping[str, Any]) -> dict[str, Any]:
        task_id = str(result.get("task_id", ""))
        # BUG-013: ``plans`` is a top-level sidecar; the runtime's memory
        # applier replaces top-level keys outright, so we must merge in
        # the existing entries before writing or every per-task iteration
        # of ``generate_plan`` overwrites prior task plans.
        merged_plans = dict(cast(Mapping[str, Any], current_memory.get("plans") or {}))
        merged_plans[task_id] = {"plan_markdown": result.get("plan_markdown")}
        return {"plans": merged_plans}

    async def _load_current_task_body(ctx: DispatchContext) -> Mapping[str, Any]:
        """Read the current task's structured body from memory for the planner.

        ``generate_tasks`` writes ``description`` + ``acceptance_criteria``
        + ``complexity`` + ``depends_on`` + ``files_hint`` per task.
        Without this loader the planner only sees ``taskId`` from intake
        and re-derives intent from the title — this loader makes the
        body visible as ``{taskTitle}`` / ``{taskDescription}`` /
        ``{acceptanceCriteria}`` / ``{complexity}`` / ``{dependsOn}`` /
        ``{filesHint}`` template variables.
        """
        from sqlalchemy import select as _select

        from app.modules.ai.models import RunMemory as _RunMemoryModel

        async with session_factory() as session:
            mem = await session.scalar(_select(_RunMemoryModel).where(_RunMemoryModel.run_id == ctx.run_id))
        memory = read_lifecycle_memory((mem.data if mem is not None else {}) or {})
        task = find_current_task(memory)
        if task is None:
            # Fall back to a placeholder so format_map still succeeds —
            # the planner will produce a thin plan from taskId alone.
            return {
                "taskTitle": "<task body not available in memory>",
                "taskDescription": "",
                "acceptanceCriteria": "(none recorded)",
                "complexity": "medium",
                "dependsOn": "(none)",
                "filesHint": "(none)",
            }
        criteria = "\n".join(f"- {c}" for c in task.acceptance_criteria) or "(none recorded)"
        deps = ", ".join(task.depends_on) or "(none)"
        files = "\n".join(f"- `{f}`" for f in task.files_hint) or "(none)"
        return {
            "taskTitle": task.title,
            "taskDescription": task.description or "(no description provided)",
            "acceptanceCriteria": criteria,
            "complexity": task.complexity,
            "dependsOn": deps,
            "filesHint": files,
        }

    registry.register(
        agent_ref,
        "generate_plan",
        CompositeLLMEngineExecutor(
            ref="composite:generate_plan",
            llm_executor=LLMContentExecutor(
                ref="llm:generate_plan",
                system_prompt=_load_prompt("generate_plan"),
                user_prompt_template=(
                    "Author an implementation plan for the task below.\n\n"
                    "Task id: {taskId}\n"
                    "Title: {taskTitle}\n"
                    "Complexity: {complexity}\n"
                    "Depends on: {dependsOn}\n\n"
                    "Description:\n{taskDescription}\n\n"
                    "Acceptance criteria:\n{acceptanceCriteria}\n\n"
                    "Files hint:\n{filesHint}\n"
                ),
                result_schema=GeneratePlanResult,
                llm_provider=llm_provider,
                prompt_context_loader=_load_current_task_body,
            ),
            transition_key="task.T6",
            to_status="plan_review",
            lifecycle_client=lifecycle_client,
            session_factory=session_factory,
            memory_patch_builder=_patch_generate_plan,
            actor=actor,
            target_id_resolver=_resolve_current_task_engine_id,
        ),
    )

    # ------------------------------------------------------------------
    # approve_plan — T7: plan_review → implementing (current task).
    # ------------------------------------------------------------------

    register_engine_executor(
        registry,
        agent_ref,
        "approve_plan",
        transition_key="task.T7",
        to_status="implementing",
        lifecycle_client=lifecycle_client,
        session_factory=session_factory,
        actor=actor,
        target_id_resolver=_resolve_current_task_engine_id,
    )

    # ------------------------------------------------------------------
    # Human: request_implementation — pause for operator signal.
    # ------------------------------------------------------------------

    registry.register(
        agent_ref,
        "request_implementation",
        HumanExecutor(
            ref="human:request_implementation",
            expected_signal_name="implementation-complete",
        ),
    )

    # ------------------------------------------------------------------
    # submit_implementation — T9: implementing → impl_review (idempotent).
    # ------------------------------------------------------------------

    from app.modules.ai.executors.submit_implementation import (
        SubmitImplementationExecutor,
    )

    registry.register(
        agent_ref,
        "submit_implementation",
        SubmitImplementationExecutor(
            ref="submit_implementation",
            lifecycle_client=lifecycle_client,
            session_factory=session_factory,
            actor=actor,
        ),
    )

    # ------------------------------------------------------------------
    # review_implementation — LLM-only verdict (BUG-004: was a composite
    # firing T10 unconditionally; now T10 lives in ``approve_review`` and
    # only fires on the pass branch).
    # ------------------------------------------------------------------

    async def _load_review_context(ctx: DispatchContext) -> Mapping[str, Any]:
        """Surface the task body, the plan markdown, and any operator-supplied
        implementation evidence as template variables.

        Without this loader the reviewer LLM only sees ``{taskId}`` from
        intake and has no basis to judge a pass/fail — the live run on
        2026-05-02 had the model legitimately return ``verdict=fail``
        with the message "task spec, implementation plan, and git diff
        for T-001 are all missing from this request" because they were.

        Sources:

        * Task body (title, description, acceptance criteria) — read from
          ``lifecycle.v1.tasks[current_task_id]``.
        * Plan markdown — read from top-level ``plans[task_id].plan_markdown``
          (the canonical write site of ``_patch_generate_plan``).
        * Implementation evidence — read from
          ``__feat009.last_dispatch_result.payload`` which carries the
          operator's signal payload (``{commit_sha, pr_url, diff, ...}``).
          Out-of-scope to read git directly in v1; what the operator
          chooses to put in the signal is what the reviewer sees.
        """
        from sqlalchemy import select as _select

        from app.modules.ai.models import RunMemory as _RunMemoryModel

        async with session_factory() as session:
            mem = await session.scalar(_select(_RunMemoryModel).where(_RunMemoryModel.run_id == ctx.run_id))
        memory_data: dict[str, Any] = (mem.data if mem is not None else {}) or {}
        memory = read_lifecycle_memory(memory_data)
        task = find_current_task(memory)
        plans_top: Mapping[str, Any] = cast(Mapping[str, Any], memory_data.get("plans") or {})
        plan_for_task: Mapping[str, Any] = cast(
            Mapping[str, Any],
            plans_top.get(task.id if task is not None else "", {}) or {},
        )
        plan_markdown = str(plan_for_task.get("plan_markdown") or "(plan not in memory)")

        # Operator's signal payload, if any.  Lives in the runtime's
        # bookkeeping namespace under ``last_dispatch_result.payload``.
        bookkeeping = cast(Mapping[str, Any], memory_data.get("__feat009") or {})
        last_result = cast(Mapping[str, Any], bookkeeping.get("last_dispatch_result") or {})
        evidence_payload = last_result.get("payload") or {}
        if isinstance(evidence_payload, Mapping):
            evidence_lines: list[str] = []
            for key, value in evidence_payload.items():  # type: ignore[union-attr]
                evidence_lines.append(f"- **{key}**: {value}")
            evidence = "\n".join(evidence_lines) if evidence_lines else "(none provided)"
        else:
            evidence = str(evidence_payload) or "(none provided)"

        if task is None:
            return {
                "taskTitle": "<task body not available in memory>",
                "taskDescription": "",
                "acceptanceCriteria": "(none recorded)",
                "complexity": "medium",
                "planMarkdown": plan_markdown,
                "implementationEvidence": evidence,
            }
        criteria = "\n".join(f"- {c}" for c in task.acceptance_criteria) or "(none recorded)"
        return {
            "taskTitle": task.title,
            "taskDescription": task.description or "(no description provided)",
            "acceptanceCriteria": criteria,
            "complexity": task.complexity,
            "planMarkdown": plan_markdown,
            "implementationEvidence": evidence,
        }

    # FEAT-015: sibling variants register their own reviewer (e.g.
    # ``human_review_implementation`` for the manual flow).  When
    # ``skip_review_implementation`` is set, omit the LLM reviewer
    # binding entirely so the variant's bootstrap can register a
    # replacement under the same ``agent_ref``.  Every other binding
    # (approve_review / T10, mark_task_done, correct_implementation,
    # close_work_item, terminate_correction_budget) registers
    # unconditionally — those are shared infrastructure.
    if not skip_review_implementation:
        # IMP-003: select the reviewer binding from settings.  Default
        # ``llm-content`` preserves today's behaviour; ``stub-pass`` is the
        # smoke / CI binding that auto-approves so runs reach
        # ``close_work_item`` without real PR evidence.  A future ``remote``
        # value slots in here when the external reviewer service ships.
        from app.config import get_settings as _get_settings

        reviewer_choice = _get_settings().lifecycle_reviewer
        reviewer_executor: Any
        if reviewer_choice == "llm-content":
            reviewer_executor = LLMContentExecutor(
                ref="llm:review_implementation",
                system_prompt=_load_prompt("review_implementation"),
                user_prompt_template=(
                    "Review the implementation for the task below against the "
                    "approved plan and the operator-supplied evidence.\n\n"
                    "Task id: {taskId}\n"
                    "Title: {taskTitle}\n"
                    "Complexity: {complexity}\n\n"
                    "Description:\n{taskDescription}\n\n"
                    "Acceptance criteria:\n{acceptanceCriteria}\n\n"
                    "----- BEGIN APPROVED PLAN -----\n"
                    "{planMarkdown}\n"
                    "----- END APPROVED PLAN -----\n\n"
                    "----- BEGIN IMPLEMENTATION EVIDENCE -----\n"
                    "{implementationEvidence}\n"
                    "----- END IMPLEMENTATION EVIDENCE -----\n"
                ),
                result_schema=ReviewImplementationResult,
                llm_provider=llm_provider,
                memory_patch_builder=_patch_review,
                prompt_context_loader=_load_review_context,
                session_factory=session_factory,
            )
        elif reviewer_choice == "stub-pass":
            from app.modules.ai.executors.stub_reviewer import make_stub_pass_reviewer

            logger.warning(
                "register_lifecycle_v03: LIFECYCLE_REVIEWER=stub-pass — every "
                "implementation will be auto-approved.  Smoke / CI only."
            )
            reviewer_executor = make_stub_pass_reviewer(
                session_factory=session_factory,
                patch_review_builder=_patch_review,
            )
        else:
            # Pydantic ``Literal`` validation prevents this branch in
            # practice; the explicit raise documents the closed enumeration.
            raise ValueError(f"unknown LIFECYCLE_REVIEWER={reviewer_choice!r}")

        logger.info(
            "register_lifecycle_v03: reviewer binding=%s (LIFECYCLE_REVIEWER)",
            reviewer_choice,
        )
        registry.register(agent_ref, "review_implementation", reviewer_executor)
    else:
        logger.info(
            "register_lifecycle_v03: agent_ref=%s skipping review_implementation "
            "(sibling variant will register its own reviewer)",
            agent_ref,
        )

    # ------------------------------------------------------------------
    # approve_review — T10: impl_review → done (current task).  Only
    # selected on the verdict=pass branch.
    # ------------------------------------------------------------------

    register_engine_executor(
        registry,
        agent_ref,
        "approve_review",
        transition_key="task.T10",
        to_status="done",
        lifecycle_client=lifecycle_client,
        session_factory=session_factory,
        actor=actor,
        target_id_resolver=_resolve_current_task_engine_id,
    )

    # ------------------------------------------------------------------
    # mark_task_done — append current_task_id to completed_task_ids and
    # advance current_task_id to the next un-completed task (or None when
    # every task is done).  Pure memory write; no engine call.  BUG-013.
    # ------------------------------------------------------------------

    async def _mark_task_done_handler(ctx: DispatchContext) -> Mapping[str, Any]:
        from sqlalchemy import select as _select

        from app.modules.ai.models import RunMemory as _RunMemoryModel

        async with session_factory() as session:
            row = await session.scalar(
                _select(_RunMemoryModel).where(_RunMemoryModel.run_id == ctx.run_id)
            )
        memory = read_lifecycle_memory((row.data if row is not None else {}) or {})
        completed_id = memory.current_task_id
        if completed_id is not None and completed_id not in memory.completed_task_ids:
            memory.completed_task_ids.append(completed_id)
        # Pick the next task in declaration order whose id is not yet in
        # the completed list.  None when every task is done — the
        # tasks_remaining predicate then routes to close_work_item.
        next_id: str | None = next(
            (t.id for t in memory.tasks if t.id not in memory.completed_task_ids),
            None,
        )
        memory.current_task_id = next_id
        return {
            "completedTaskId": completed_id,
            "nextTaskId": next_id,
            "__memory_patch": write_lifecycle_memory(memory),
        }

    registry.register(
        agent_ref,
        "mark_task_done",
        LocalExecutor(
            ref="local:mark_task_done",
            handler=_mark_task_done_handler,
        ),
    )

    # ------------------------------------------------------------------
    # correct_implementation — Approval inline; no engine call.
    # ------------------------------------------------------------------

    registry.register(
        agent_ref,
        "correct_implementation",
        LocalExecutor(
            ref="local:correct_implementation",
            handler=_make_correct_implementation_handler(session_factory),
        ),
    )

    # ------------------------------------------------------------------
    # close_work_item — W4 (in_progress → ready) then W6 (ready → closed).
    # SequenceEngineExecutor fires both hops on the work-item id supplied
    # by Run.intake.engineItemId (default resolver).
    # ------------------------------------------------------------------

    from app.modules.ai.executors.sequence import SequenceEngineExecutor

    registry.register(
        agent_ref,
        "close_work_item",
        SequenceEngineExecutor(
            ref="sequence:close_work_item",
            hops=[("work_item.W4", "ready"), ("work_item.W6", "closed")],
            lifecycle_client=lifecycle_client,
            session_factory=session_factory,
            actor=actor,
        ),
    )

    # ------------------------------------------------------------------
    # Terminal failure: terminate_correction_budget — emits an error
    # envelope so the runtime's _ExecutorFailure handler maps the run to
    # RunStatus.FAILED with stop_reason=ERROR.  Carries
    # ``final_state.reason=correction_budget_exceeded`` via the
    # envelope's ``detail`` for operator forensics.
    # ------------------------------------------------------------------

    async def _terminate_correction_budget_handler(_ctx: DispatchContext) -> Mapping[str, Any]:
        # Returning outcome=error via a raised exception is the simplest
        # path through LocalExecutor: it catches the exception and emits
        # a failed envelope, which the runtime treats as terminal-with-error.
        raise _CorrectionBudgetExceeded("correction_budget_exceeded")

    registry.register(
        agent_ref,
        "terminate_correction_budget",
        LocalExecutor(
            ref="local:terminate_correction_budget",
            handler=_terminate_correction_budget_handler,
        ),
    )


class _CorrectionBudgetExceeded(RuntimeError):
    """Sentinel: ``terminate_correction_budget`` reached.

    Surfaced as a failed local-executor envelope; the runtime maps it to
    ``RunStatus.FAILED`` via the ``_ExecutorFailure`` path.
    """


def _make_correct_implementation_handler(  # type: ignore[no-untyped-def]
    session_factory: async_sessionmaker[AsyncSession],
):
    """Build the ``correct_implementation`` LocalExecutor handler.

    The handler runs after ``review_implementation`` produces a ``fail``
    verdict.  It does three things, all inside a single transaction:

    1. Read the current correction-attempt count from ``RunMemory.data``
       and bump it for the active symbolic ``task_id`` (e.g. ``"T-1"``).
    2. **FEAT-008 rejection contract**: write an ``Approval`` row with
       ``stage="implementation"``, ``decision="reject"``, ``decided_by``
       set to the lifecycle-agent actor.  No engine call — rejection
       does not advance engine state (the FEAT-008 anti-pattern this
       FEAT preserves).
    3. Return a dispatch envelope with ``outcome="rejected"`` so the
       ``correction_attempts_under_bound`` predicate routes on the
       next iteration.

    The Approval row links to the persisted ``Task.id`` UUID, looked
    up by ``(work_item_id, external_ref)`` via ``ctx.intake``.  When
    the lookup fails (e.g. tests that don't seed ``tasks`` rows), the
    Approval write is skipped with a warning and the bookkeeping path
    still runs — keeps the unit-test path working without forcing every
    test to seed task rows.
    """
    from sqlalchemy import select as _select

    from app.modules.ai.models import Approval as _Approval
    from app.modules.ai.models import RunMemory as _RunMemory
    from app.modules.ai.models import Task as _Task
    from app.modules.ai.models import WorkItem as _WorkItem

    async def _handler(ctx: DispatchContext) -> Mapping[str, Any]:
        task_external_ref = str(ctx.intake.get("taskId") or ctx.intake.get("task_id") or "") or "unknown"
        engine_item_id_raw = ctx.intake.get("engineItemId") or ctx.intake.get("workItemEngineId")

        # 1. Bookkeeping — read existing attempts from
        # ``lifecycle.v1.correctionAttempts`` (BUG-010), bump for this
        # task, and produce a merged ``lifecycle.v1`` patch so the
        # rest of the namespace (tasks, work_item, review_history,
        # etc.) is preserved.  Previously this read/wrote
        # ``correction_attempts`` (snake_case) at the top level — a slot
        # the predicate ``correction_attempts_under_bound`` had also
        # been pointing at, so attempts always read 0 and the budget
        # never tripped → infinite reject/resubmit loop.
        from app.modules.ai.tools.lifecycle.memory import (
            LIFECYCLE_MEMORY_NS,
            read_lifecycle_memory,
            to_run_memory,
        )

        async with session_factory() as session:
            row = await session.scalar(_select(_RunMemory).where(_RunMemory.run_id == ctx.run_id))
            existing: dict[str, Any] = ((row.data if row is not None else {}) or {}).copy()
        memory_model = read_lifecycle_memory(existing)
        current = int(memory_model.correction_attempts.get(task_external_ref, 0))
        memory_model.correction_attempts[task_external_ref] = current + 1

        # 2. FEAT-008 rejection contract — write Approval row inline.
        # Separate session/transaction from the runtime's __memory_patch
        # write so a missing Task FK degrades gracefully (the run can
        # still bump the counter even when the test harness skips the
        # tasks table).
        approval_written = False
        if engine_item_id_raw is not None:
            try:
                engine_item_id = uuid.UUID(str(engine_item_id_raw))
                async with session_factory() as session, session.begin():
                    work_item = await session.scalar(
                        _select(_WorkItem).where(_WorkItem.engine_item_id == engine_item_id)
                    )
                    if work_item is not None:
                        task = await session.scalar(
                            _select(_Task).where(
                                _Task.work_item_id == work_item.id,
                                _Task.external_ref == task_external_ref,
                            )
                        )
                        if task is not None:
                            session.add(
                                _Approval(
                                    task_id=task.id,
                                    stage="impl",
                                    decision="reject",
                                    decided_by="lifecycle-agent",
                                    # v0.3.0 self-approves under admin
                                    # auspices; the FEAT-008 / ActorRole
                                    # enum constrains approvers to
                                    # human roles (admin / dev).  When
                                    # PR 4 grows multi-actor support, a
                                    # follow-on adds an "agent" role.
                                    decided_by_role="admin",
                                    feedback=None,
                                )
                            )
                            approval_written = True
            except Exception:
                logger.warning(
                    "correct_implementation: Approval row not written "
                    "(task ref=%r engineItemId=%r); proceeding without it",
                    task_external_ref,
                    engine_item_id_raw,
                    exc_info=True,
                )

        return {
            "outcome": "rejected",
            "task_id": task_external_ref,
            "approval_written": approval_written,
            "__memory_patch": {LIFECYCLE_MEMORY_NS: to_run_memory(memory_model)},
        }

    return _handler


# ---------------------------------------------------------------------------
# FEAT-010 — engine executor registration helper
# ---------------------------------------------------------------------------


def register_engine_executor(
    registry: ExecutorRegistry,
    agent_ref: str,
    node_name: str,
    *,
    transition_key: str,
    to_status: str,
    lifecycle_client: FlowEngineLifecycleClient | None,
    session_factory: async_sessionmaker[AsyncSession],
    actor: str | None = None,
    timeout_seconds: float | None = None,
    target_id_resolver: Any | None = None,
) -> ExecutorBinding:
    """Register an :class:`EngineExecutor` for ``(agent_ref, node_name)``.

    Mirrors how local/remote/human bindings are wired today: bootstrap
    is the single source of truth for executor wiring; agents declare
    nodes, bootstrap binds executors.

    Raises ``RuntimeError`` if ``lifecycle_client`` is ``None`` (engine-
    absent dev mode).  Surfacing the misconfiguration at boot — naming
    the offending binding — is preferable to a stack trace at first
    dispatch.  The fallback for engine-absent dev mode is an explicit
    ``no_executor("≥10-char reason")`` exemption on the binding.
    """
    if lifecycle_client is None:
        raise RuntimeError(
            f"register_engine_executor: lifecycle_client is None for "
            f"({agent_ref!r}, {node_name!r}); engine-bound nodes require a "
            "configured FlowEngineLifecycleClient.  In engine-absent dev "
            'mode, declare a no_executor("reason") exemption for this '
            "binding instead."
        )

    # Local import — keeps ``executors.engine`` off the module-level
    # import graph until this helper is actually called (so static
    # imports of ``executors.bootstrap`` in tests / runtime don't pull
    # the engine adapter for free).
    from app.modules.ai.executors.engine import EngineExecutor

    executor = EngineExecutor(
        ref=f"engine:{transition_key}",
        transition_key=transition_key,
        to_status=to_status,
        lifecycle_client=lifecycle_client,
        session_factory=session_factory,
        actor=actor,
        target_id_resolver=target_id_resolver,
    )
    return registry.register(
        agent_ref,
        node_name,
        executor,
        timeout_seconds=timeout_seconds,
    )


__all__ = [
    "ExecutorCoverageError",
    "LifecycleV03Collaborators",
    "register_all_executors",
    "register_engine_executor",
    "register_lifecycle_v03",
    "run_coverage_validation",
]
