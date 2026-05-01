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
from typing import TYPE_CHECKING, Any

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
            _register_lifecycle_v02(registry, agent.ref)
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


def _register_lifecycle_v02(registry: ExecutorRegistry, agent_ref: str) -> None:
    """Register the v0.2.0 demo agent's local executors.

    v0.2.0 is a minimal demo proving the new shape end-to-end (dispatch
    verbs + deterministic policy + executor seam).  It is **not** a
    drop-in replacement for v0.1.0 — migrating the full lifecycle (with
    its eight original tools, LifecycleMemory semantics, and the
    wait_for_implementation pause) is tracked as a separate future FEAT.
    """
    registry.register(
        agent_ref,
        "request_work_item_load",
        LocalExecutor(
            ref="local:request_work_item_load",
            handler=_handle_request_work_item_load,
        ),
    )
    registry.register(
        agent_ref,
        "request_closure",
        LocalExecutor(ref="local:request_closure", handler=_handle_request_closure),
    )


async def _handle_request_work_item_load(ctx: DispatchContext) -> Mapping[str, Any]:
    """Load a work-item brief path into the run's memory.

    Pure code; no LLM. The path comes from the run's ``intake.workItemPath``
    forwarded by the runtime via memory bookkeeping (or a future
    enhancement that threads intake into ``DispatchContext.intake``).
    """
    path = ctx.intake.get("workItemPath") or ctx.intake.get("path")
    return {
        "loaded": True,
        "path": str(path) if path is not None else None,
        "__memory_patch": {"work_item_path": str(path) if path is not None else None},
    }


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
) -> None:
    """Register the eight production bindings for ``lifecycle-agent@0.3.0``.

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
        raise RuntimeError(
            "register_lifecycle_v03: lifecycle_client is required (engine-bound nodes need it)"
        )
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

    def _patch_load_work_item(result: Mapping[str, Any]) -> dict[str, Any]:
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

    registry.register(
        agent_ref,
        "load_work_item",
        LLMContentExecutor(
            ref="llm:load_work_item",
            system_prompt=_load_prompt("load_work_item"),
            user_prompt_template="Synthesize the work-item brief at: {workItemPath}",
            result_schema=LoadWorkItemResult,
            llm_provider=llm_provider,
            memory_patch_builder=_patch_load_work_item,
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

    def _patch_generate_tasks(result: Mapping[str, Any]) -> dict[str, Any]:
        tasks_in: list[Mapping[str, Any]] = list(result.get("tasks") or [])
        memory = LifecycleMemory(
            tasks=[
                LifecycleTask(
                    id=str(t.get("id", "")),
                    title=str(t.get("title", "")),
                    executor=str(t.get("executor", "")) or None,
                )
                for t in tasks_in
            ],
            current_task_id=(str(tasks_in[0].get("id")) if tasks_in else None),
        )
        return write_lifecycle_memory(memory)

    registry.register(
        agent_ref,
        "generate_tasks",
        LLMContentExecutor(
            ref="llm:generate_tasks",
            system_prompt=_load_prompt("generate_tasks"),
            user_prompt_template="Generate the task breakdown for work item id: {workItemId}",
            result_schema=GenerateTasksResult,
            llm_provider=llm_provider,
            memory_patch_builder=_patch_generate_tasks,
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
            mem = await session.scalar(
                _select(_RunMemoryModel).where(_RunMemoryModel.run_id == ctx.run_id)
            )
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

    def _patch_generate_plan(result: Mapping[str, Any]) -> dict[str, Any]:
        task_id = str(result.get("task_id", ""))
        return {
            "plans": {task_id: {"plan_markdown": result.get("plan_markdown")}},
            "tasks": {task_id: {}},
        }

    registry.register(
        agent_ref,
        "generate_plan",
        CompositeLLMEngineExecutor(
            ref="composite:generate_plan",
            llm_executor=LLMContentExecutor(
                ref="llm:generate_plan",
                system_prompt=_load_prompt("generate_plan"),
                user_prompt_template="Author an implementation plan for task: {taskId}",
                result_schema=GeneratePlanResult,
                llm_provider=llm_provider,
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

    def _patch_review(result: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "review_history": [
                {
                    "task_id": str(result.get("task_id", "")),
                    "verdict": str(result.get("verdict", "")),
                    "feedback": str(result.get("feedback", "")),
                }
            ]
        }

    registry.register(
        agent_ref,
        "review_implementation",
        LLMContentExecutor(
            ref="llm:review_implementation",
            system_prompt=_load_prompt("review_implementation"),
            user_prompt_template="Review the implementation for task: {taskId}",
            result_schema=ReviewImplementationResult,
            llm_provider=llm_provider,
            memory_patch_builder=_patch_review,
        ),
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
        task_external_ref = (
            str(ctx.intake.get("taskId") or ctx.intake.get("task_id") or "")
            or "unknown"
        )
        engine_item_id_raw = ctx.intake.get("engineItemId") or ctx.intake.get("workItemEngineId")

        # 1. Bookkeeping — read existing attempts, bump for this task.
        async with session_factory() as session:
            row = await session.scalar(
                _select(_RunMemory).where(_RunMemory.run_id == ctx.run_id)
            )
            existing: dict[str, Any] = ((row.data if row is not None else {}) or {}).copy()
        attempts: dict[str, Any] = dict(existing.get("correction_attempts") or {})
        current = int(attempts.get(task_external_ref, 0))
        attempts[task_external_ref] = current + 1

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
            "__memory_patch": {"correction_attempts": attempts},
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
