"""``ExecutorCallDto`` engine-mode trace-shape tests (FEAT-010 / T-234).

Asserts:
- engine-mode entries accept ``correlation_id`` / ``transition_key`` /
  ``engine_run_id``;
- non-engine modes reject those fields (defensive — prevents schema drift);
- the JSONL writer round-trips an engine-mode entry through
  ``record_executor_call`` and the resulting line carries every field.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.modules.ai.enums import DispatchMode, DispatchOutcome
from app.modules.ai.schemas import ExecutorCallDto
from app.modules.ai.trace_jsonl import JsonlTraceStore


def _engine_call(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "dispatch_id": uuid.uuid4(),
        "run_id": uuid.uuid4(),
        "executor_ref": "engine:work_item.W2",
        "mode": DispatchMode.ENGINE,
        "started_at": datetime.now(UTC),
        "finished_at": datetime.now(UTC),
        "outcome": DispatchOutcome.OK,
        "correlation_id": uuid.uuid4(),
        "transition_key": "work_item.W2",
        "engine_run_id": "run-abc-123",
    }
    base.update(overrides)
    return base


class TestEngineModeAccepted:
    def test_engine_call_accepts_all_fields(self) -> None:
        dto = ExecutorCallDto(**_engine_call())
        assert dto.mode == DispatchMode.ENGINE
        assert dto.transition_key == "work_item.W2"
        assert dto.correlation_id is not None
        assert dto.engine_run_id == "run-abc-123"

    def test_engine_call_engine_run_id_optional(self) -> None:
        # Pre-engine-call failure path: engine_run_id can be absent.
        dto = ExecutorCallDto(**_engine_call(engine_run_id=None))
        assert dto.engine_run_id is None
        assert dto.transition_key == "work_item.W2"


class TestNonEngineModeRejected:
    def test_local_mode_with_engine_fields_rejected(self) -> None:
        with pytest.raises(ValueError, match="engine-mode fields"):
            ExecutorCallDto(
                dispatch_id=uuid.uuid4(),
                run_id=uuid.uuid4(),
                executor_ref="local:foo",
                mode=DispatchMode.LOCAL,
                started_at=datetime.now(UTC),
                transition_key="work_item.W2",
            )

    def test_remote_mode_with_correlation_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="engine-mode fields"):
            ExecutorCallDto(
                dispatch_id=uuid.uuid4(),
                run_id=uuid.uuid4(),
                executor_ref="remote:claude-code",
                mode=DispatchMode.REMOTE,
                started_at=datetime.now(UTC),
                correlation_id=uuid.uuid4(),
            )

    def test_local_mode_without_engine_fields_accepted(self) -> None:
        dto = ExecutorCallDto(
            dispatch_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            executor_ref="local:foo",
            mode=DispatchMode.LOCAL,
            started_at=datetime.now(UTC),
        )
        assert dto.transition_key is None
        assert dto.correlation_id is None
        assert dto.engine_run_id is None


class TestJsonlWriterRoundTrip:
    @pytest.mark.asyncio
    async def test_engine_entry_writes_all_fields(self, tmp_path: Path) -> None:
        store = JsonlTraceStore(trace_dir=tmp_path)
        run_id = uuid.uuid4()
        call = ExecutorCallDto(**_engine_call())
        await store.record_executor_call(run_id, call)

        # Locate the executor trace file.
        executor_files = list(tmp_path.rglob(f"{run_id}.jsonl"))
        assert executor_files, f"no executor trace file written under {tmp_path}"
        # Find the file under the executors/ subdir.
        candidates = [p for p in executor_files if p.parent.name == "executors"]
        assert candidates, f"no executor trace file in executors/ subdir: {executor_files}"
        line = candidates[0].read_text(encoding="utf-8").strip()
        assert line, "trace file empty"
        record = json.loads(line)
        assert record["kind"] == "executor_call"
        data = record["data"]
        assert data["mode"] == "engine"
        assert data["transitionKey"] == "work_item.W2"
        assert data["engineRunId"] == "run-abc-123"
        assert data["correlationId"] == str(call.correlation_id)
