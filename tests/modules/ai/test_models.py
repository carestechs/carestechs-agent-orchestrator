"""Tests for app.modules.ai.models: field presence, types, constraints, table names."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import inspect

from app.modules.ai.enums import RunStatus, StepStatus, StopReason, WebhookEventType
from app.modules.ai.models import PolicyCall, Run, RunMemory, Step, WebhookEvent

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
        assert len(checks) == 1
        for v in WebhookEventType:
            assert f"'{v.value}'" in checks[0]


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
