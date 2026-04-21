"""Tests for app.modules.ai.models: field presence, types, constraints, table names."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import inspect

from app.modules.ai.enums import (
    ActorRole,
    ActorType,
    ApprovalDecision,
    ApprovalStage,
    AssigneeType,
    RunStatus,
    StepStatus,
    StopReason,
    TaskStatus,
    WebhookEventType,
    WebhookSource,
    WorkItemStatus,
    WorkItemType,
)
from app.modules.ai.models import (
    Approval,
    PendingAuxWrite,
    PolicyCall,
    Run,
    RunMemory,
    Step,
    Task,
    TaskAssignment,
    WebhookEvent,
    WorkItem,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _column_names(model: type) -> set[str]:
    mapper = inspect(model)
    return {c.key for c in mapper.columns}


def _table(model: type) -> str:
    return model.__tablename__  # type: ignore[attr-defined]


def _unique_constraint_columns(model: type) -> list[frozenset[str]]:
    """Return a list of column-name sets for each unique constraint on the table."""
    from sqlalchemy import UniqueConstraint as UC

    table = model.__table__  # type: ignore[attr-defined]
    result: list[frozenset[str]] = []
    for constraint in table.constraints:
        if isinstance(constraint, UC):
            result.append(frozenset(c.name for c in constraint.columns))
    # Also check unique=True on individual columns
    for col in table.columns:
        if col.unique:
            result.append(frozenset([col.name]))
    return result


def _check_constraint_texts(model: type) -> list[str]:
    table = model.__table__  # type: ignore[attr-defined]
    return [str(c.sqltext) for c in table.constraints if hasattr(c, "sqltext")]


# ---------------------------------------------------------------------------
# Table names
# ---------------------------------------------------------------------------


class TestTableNames:
    def test_runs(self) -> None:
        assert _table(Run) == "runs"

    def test_steps(self) -> None:
        assert _table(Step) == "steps"

    def test_policy_calls(self) -> None:
        assert _table(PolicyCall) == "policy_calls"

    def test_webhook_events(self) -> None:
        assert _table(WebhookEvent) == "webhook_events"

    def test_run_memory(self) -> None:
        assert _table(RunMemory) == "run_memory"


# ---------------------------------------------------------------------------
# Field presence
# ---------------------------------------------------------------------------

_RUN_FIELDS = {
    "id",
    "agent_ref",
    "agent_definition_hash",
    "intake",
    "status",
    "stop_reason",
    "final_state",
    "started_at",
    "ended_at",
    "trace_uri",
    "created_at",
    "updated_at",
}

_STEP_FIELDS = {
    "id",
    "run_id",
    "step_number",
    "node_name",
    "node_inputs",
    "engine_run_id",
    "status",
    "node_result",
    "error",
    "dispatched_at",
    "completed_at",
    "created_at",
}

_POLICY_CALL_FIELDS = {
    "id",
    "run_id",
    "step_id",
    "prompt_context",
    "available_tools",
    "provider",
    "model",
    "selected_tool",
    "tool_arguments",
    "input_tokens",
    "output_tokens",
    "latency_ms",
    "raw_response",
    "created_at",
}

_WEBHOOK_EVENT_FIELDS = {
    "id",
    "run_id",
    "step_id",
    "event_type",
    "engine_run_id",
    "payload",
    "signature_ok",
    "source",
    "received_at",
    "processed_at",
    "dedupe_key",
}

_RUN_MEMORY_FIELDS = {"run_id", "data", "updated_at"}


class TestFieldPresence:
    @pytest.mark.parametrize(
        ("model", "expected"),
        [
            (Run, _RUN_FIELDS),
            (Step, _STEP_FIELDS),
            (PolicyCall, _POLICY_CALL_FIELDS),
            (WebhookEvent, _WEBHOOK_EVENT_FIELDS),
            (RunMemory, _RUN_MEMORY_FIELDS),
        ],
        ids=["Run", "Step", "PolicyCall", "WebhookEvent", "RunMemory"],
    )
    def test_fields_match_data_model(self, model: type, expected: set[str]) -> None:
        actual = _column_names(model)
        assert actual == expected, f"Missing: {expected - actual}, Extra: {actual - expected}"


# ---------------------------------------------------------------------------
# Primary keys
# ---------------------------------------------------------------------------


class TestPrimaryKeys:
    def test_run_pk(self) -> None:
        mapper = inspect(Run)
        pk_cols = [c.key for c in mapper.primary_key]
        assert pk_cols == ["id"]

    def test_run_memory_pk_is_run_id(self) -> None:
        mapper = inspect(RunMemory)
        pk_cols = [c.key for c in mapper.primary_key]
        assert pk_cols == ["run_id"]


# ---------------------------------------------------------------------------
# CHECK constraints (enum values)
# ---------------------------------------------------------------------------


class TestCheckConstraints:
    def test_run_status_check(self) -> None:
        checks = _check_constraint_texts(Run)
        status_check = [c for c in checks if "status" in c and "stop_reason" not in c]
        assert len(status_check) == 1
        for v in RunStatus:
            assert f"'{v.value}'" in status_check[0]

    def test_run_stop_reason_check(self) -> None:
        checks = _check_constraint_texts(Run)
        sr_check = [c for c in checks if "stop_reason" in c]
        assert len(sr_check) == 1
        for v in StopReason:
            assert f"'{v.value}'" in sr_check[0]

    def test_step_status_check(self) -> None:
        checks = _check_constraint_texts(Step)
        assert len(checks) == 1
        for v in StepStatus:
            assert f"'{v.value}'" in checks[0]

    def test_webhook_event_type_check(self) -> None:
        checks = _check_constraint_texts(WebhookEvent)
        et_checks = [c for c in checks if c.strip().startswith("event_type IN")]
        assert len(et_checks) == 1
        for v in WebhookEventType:
            assert f"'{v.value}'" in et_checks[0]

    def test_webhook_event_source_check(self) -> None:
        checks = _check_constraint_texts(WebhookEvent)
        src_checks = [c for c in checks if c.strip().startswith("source IN")]
        assert len(src_checks) == 1
        for v in WebhookSource:
            assert f"'{v.value}'" in src_checks[0]


# ---------------------------------------------------------------------------
# Unique constraints
# ---------------------------------------------------------------------------


class TestUniqueConstraints:
    def test_step_run_id_step_number(self) -> None:
        uqs = _unique_constraint_columns(Step)
        assert frozenset(["run_id", "step_number"]) in uqs

    def test_policy_call_step_id(self) -> None:
        uqs = _unique_constraint_columns(PolicyCall)
        assert frozenset(["step_id"]) in uqs

    def test_webhook_event_dedupe_key(self) -> None:
        uqs = _unique_constraint_columns(WebhookEvent)
        assert frozenset(["dedupe_key"]) in uqs


# ---------------------------------------------------------------------------
# UUIDv7 default
# ---------------------------------------------------------------------------


class TestUuidDefault:
    def test_generate_uuid7(self) -> None:
        from app.modules.ai.models import generate_uuid7

        v = generate_uuid7()
        assert isinstance(v, uuid.UUID)
        # UUIDv7 has version 7
        assert v.version == 7


# ---------------------------------------------------------------------------
# FEAT-006 — WorkItem
# ---------------------------------------------------------------------------


class TestWorkItem:
    def test_table_name(self) -> None:
        assert _table(WorkItem) == "work_items"

    def test_columns(self) -> None:
        expected = {
            "id",
            "external_ref",
            "type",
            "title",
            "source_path",
            "status",
            "locked_from",
            "engine_item_id",
            "opened_by",
            "closed_at",
            "closed_by",
            "created_at",
            "updated_at",
        }
        assert _column_names(WorkItem) == expected

    def test_status_check_includes_all_values(self) -> None:
        checks = _check_constraint_texts(WorkItem)
        status_check = [c for c in checks if "status" in c and "locked_from" not in c]
        assert len(status_check) == 1
        for v in WorkItemStatus:
            assert f"'{v.value}'" in status_check[0]

    def test_type_check_includes_all_values(self) -> None:
        checks = _check_constraint_texts(WorkItem)
        type_check = [c for c in checks if c.strip().startswith("type IN")]
        assert len(type_check) == 1
        for v in WorkItemType:
            assert f"'{v.value}'" in type_check[0]

    def test_locked_from_check_allows_null(self) -> None:
        checks = _check_constraint_texts(WorkItem)
        lf_check = [c for c in checks if "locked_from" in c]
        assert len(lf_check) == 1
        assert "locked_from IS NULL" in lf_check[0]

    def test_unique_external_ref(self) -> None:
        uqs = _unique_constraint_columns(WorkItem)
        assert frozenset(["external_ref"]) in uqs


# ---------------------------------------------------------------------------
# FEAT-006 — Task
# ---------------------------------------------------------------------------


class TestTask:
    def test_table_name(self) -> None:
        assert _table(Task) == "tasks"

    def test_columns(self) -> None:
        expected = {
            "id",
            "work_item_id",
            "external_ref",
            "title",
            "status",
            "engine_item_id",
            "proposer_type",
            "proposer_id",
            "deferred_from",
            "created_at",
            "updated_at",
        }
        assert _column_names(Task) == expected

    def test_status_check_has_all_nine_values(self) -> None:
        checks = _check_constraint_texts(Task)
        status_checks = [
            c
            for c in checks
            if c.strip().startswith("status IN")
        ]
        assert len(status_checks) == 1
        for v in TaskStatus:
            assert f"'{v.value}'" in status_checks[0]

    def test_proposer_type_check(self) -> None:
        checks = _check_constraint_texts(Task)
        pt_checks = [c for c in checks if "proposer_type" in c]
        assert len(pt_checks) == 1
        for v in ActorType:
            assert f"'{v.value}'" in pt_checks[0]

    def test_deferred_from_allows_null(self) -> None:
        checks = _check_constraint_texts(Task)
        df_checks = [c for c in checks if "deferred_from" in c]
        assert len(df_checks) == 1
        assert "deferred_from IS NULL" in df_checks[0]

    def test_unique_work_item_and_external_ref(self) -> None:
        uqs = _unique_constraint_columns(Task)
        assert frozenset(["work_item_id", "external_ref"]) in uqs

    def test_work_item_fk_on_delete_restrict(self) -> None:
        fk = next(iter(Task.__table__.foreign_keys))
        assert fk.column.table.name == "work_items"
        assert fk.ondelete == "RESTRICT"


# ---------------------------------------------------------------------------
# FEAT-006 — TaskAssignment
# ---------------------------------------------------------------------------


class TestTaskAssignment:
    def test_table_name(self) -> None:
        assert _table(TaskAssignment) == "task_assignments"

    def test_columns(self) -> None:
        expected = {
            "id",
            "task_id",
            "assignee_type",
            "assignee_id",
            "assigned_by",
            "assigned_at",
            "superseded_at",
        }
        assert _column_names(TaskAssignment) == expected

    def test_assignee_type_check(self) -> None:
        checks = _check_constraint_texts(TaskAssignment)
        assert len(checks) == 1
        for v in AssigneeType:
            assert f"'{v.value}'" in checks[0]

    def test_partial_unique_active_index(self) -> None:
        table = TaskAssignment.__table__
        active = next(i for i in table.indexes if i.name == "ix_task_assignments_active")
        assert active.unique
        # partial-where expression should reference superseded_at
        where = active.dialect_options["postgresql"]["where"]
        assert "superseded_at IS NULL" in str(where)

    def test_task_fk_on_delete_restrict(self) -> None:
        fk = next(iter(TaskAssignment.__table__.foreign_keys))
        assert fk.column.table.name == "tasks"
        assert fk.ondelete == "RESTRICT"


# ---------------------------------------------------------------------------
# FEAT-006 — Approval
# ---------------------------------------------------------------------------


class TestApproval:
    def test_table_name(self) -> None:
        assert _table(Approval) == "approvals"

    def test_columns(self) -> None:
        expected = {
            "id",
            "task_id",
            "stage",
            "decision",
            "decided_by",
            "decided_by_role",
            "feedback",
            "decided_at",
        }
        assert _column_names(Approval) == expected

    def test_stage_check(self) -> None:
        checks = _check_constraint_texts(Approval)
        stage_checks = [c for c in checks if c.strip().startswith("stage IN")]
        assert len(stage_checks) == 1
        for v in ApprovalStage:
            assert f"'{v.value}'" in stage_checks[0]

    def test_decision_check(self) -> None:
        checks = _check_constraint_texts(Approval)
        dec_checks = [c for c in checks if c.strip().startswith("decision IN")]
        assert len(dec_checks) == 1
        for v in ApprovalDecision:
            assert f"'{v.value}'" in dec_checks[0]

    def test_decided_by_role_check(self) -> None:
        checks = _check_constraint_texts(Approval)
        role_checks = [c for c in checks if "decided_by_role" in c]
        assert len(role_checks) == 1
        for v in ActorRole:
            assert f"'{v.value}'" in role_checks[0]

    def test_task_fk_on_delete_restrict(self) -> None:
        fk = next(iter(Approval.__table__.foreign_keys))
        assert fk.column.table.name == "tasks"
        assert fk.ondelete == "RESTRICT"


# ---------------------------------------------------------------------------
# FEAT-008 — PendingAuxWrite (outbox)
# ---------------------------------------------------------------------------


class TestPendingAuxWrite:
    def test_table_name(self) -> None:
        assert _table(PendingAuxWrite) == "pending_aux_writes"

    def test_columns(self) -> None:
        expected = {
            "id",
            "correlation_id",
            "signal_name",
            "entity_type",
            "entity_id",
            "payload",
            "enqueued_at",
        }
        assert _column_names(PendingAuxWrite) == expected

    def test_correlation_id_is_unique(self) -> None:
        uniques = [
            c
            for c in PendingAuxWrite.__table__.constraints
            if c.__class__.__name__ == "UniqueConstraint"
        ]
        # Engine may coalesce the declared unique into a UNIQUE INDEX.
        unique_by_constraint = any(
            {col.name for col in c.columns} == {"correlation_id"}  # type: ignore[attr-defined]
            for c in uniques
        )
        unique_by_index = any(
            idx.unique and {col.name for col in idx.columns} == {"correlation_id"}
            for idx in PendingAuxWrite.__table__.indexes
        )
        assert unique_by_constraint or unique_by_index

    def test_has_entity_and_enqueued_indexes(self) -> None:
        names = {idx.name for idx in PendingAuxWrite.__table__.indexes}
        assert "ix_pending_aux_writes_entity_id" in names
        assert "ix_pending_aux_writes_enqueued_at" in names
