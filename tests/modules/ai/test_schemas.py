"""Tests for app.modules.ai.schemas: round-trip serialization, validation, enums."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.modules.ai.enums import RunStatus, StepStatus, WebhookEventType
from app.modules.ai.schemas import (
    CreateRunRequest,
    PolicyCallDto,
    RunSummaryDto,
    StepDto,
    WebhookEventDto,
    WebhookEventRequest,
)

_NOW = datetime(2026, 4, 16, 12, 0, 0, tzinfo=UTC)
_UUID = uuid.UUID("019d9869-7692-71e7-a25f-f72283fef5e6")


# ---------------------------------------------------------------------------
# Serialization round-trip (snake → camelCase JSON)
# ---------------------------------------------------------------------------


class TestCamelCaseSerialization:
    def test_run_summary_dto(self) -> None:
        dto = RunSummaryDto(
            id=_UUID,
            agent_ref="lifecycle-agent@0.3.0",
            status=RunStatus.RUNNING,
            started_at=_NOW,
        )
        dumped = dto.model_dump(by_alias=True, mode="json")
        assert "agentRef" in dumped
        assert "startedAt" in dumped
        assert "stopReason" in dumped  # None but key present
        assert dumped["status"] == "running"

    def test_step_dto(self) -> None:
        dto = StepDto(
            id=_UUID,
            step_number=1,
            node_name="generate_tasks",
            status=StepStatus.COMPLETED,
            node_inputs={"k": 1},
            node_result={"out": "ok"},
            dispatched_at=_NOW,
            completed_at=_NOW,
        )
        dumped = dto.model_dump(by_alias=True, mode="json")
        assert "stepNumber" in dumped
        assert "nodeName" in dumped
        assert "nodeInputs" in dumped
        assert "nodeResult" in dumped
        assert "dispatchedAt" in dumped

    def test_policy_call_dto(self) -> None:
        dto = PolicyCallDto(
            id=_UUID,
            step_id=_UUID,
            provider="stub",
            model="stub-v1",
            selected_tool="do_x",
            tool_arguments={"a": 1},
            available_tools=[{"name": "do_x"}],
            input_tokens=100,
            output_tokens=50,
            latency_ms=200,
            created_at=_NOW,
        )
        dumped = dto.model_dump(by_alias=True, mode="json")
        assert "stepId" in dumped
        assert "selectedTool" in dumped
        assert "inputTokens" in dumped
        assert "latencyMs" in dumped

    def test_webhook_event_dto(self) -> None:
        dto = WebhookEventDto(
            id=_UUID,
            event_type=WebhookEventType.NODE_FINISHED,
            engine_run_id="eng-123",
            payload={"result": "ok"},
            signature_ok=True,
            received_at=_NOW,
        )
        dumped = dto.model_dump(by_alias=True, mode="json")
        assert "eventType" in dumped
        assert "engineRunId" in dumped
        assert "signatureOk" in dumped
        assert dumped["eventType"] == "node_finished"


# ---------------------------------------------------------------------------
# Deserialization (camelCase JSON → snake_case Python)
# ---------------------------------------------------------------------------


class TestCamelCaseDeserialization:
    def test_create_run_request(self) -> None:
        data = {
            "agentRef": "lifecycle-agent@0.3.0",
            "intake": {"featureBriefPath": "docs/FEAT-042.md"},
            "budget": {"maxSteps": 50},
        }
        req = CreateRunRequest.model_validate(data)
        assert req.agent_ref == "lifecycle-agent@0.3.0"
        assert req.budget is not None
        assert req.budget.max_steps == 50

    def test_webhook_event_request(self) -> None:
        data = {
            "eventType": "node_finished",
            "engineRunId": "eng-123",
            "engineEventId": "evt-456",
            "occurredAt": "2026-04-16T12:00:00Z",
            "payload": {"result": "ok"},
        }
        req = WebhookEventRequest.model_validate(data)
        assert req.event_type == WebhookEventType.NODE_FINISHED
        assert req.engine_run_id == "eng-123"

    def test_run_summary_from_camel(self) -> None:
        data = {
            "id": str(_UUID),
            "agentRef": "test-agent@1.0",
            "status": "pending",
            "startedAt": "2026-04-16T12:00:00Z",
        }
        dto = RunSummaryDto.model_validate(data)
        assert dto.agent_ref == "test-agent@1.0"
        assert dto.status == RunStatus.PENDING


# ---------------------------------------------------------------------------
# Enum validation
# ---------------------------------------------------------------------------


class TestEnumValidation:
    def test_unknown_event_type_rejected(self) -> None:
        data = {
            "eventType": "unknown_event",
            "engineRunId": "eng-123",
            "engineEventId": "evt-456",
            "occurredAt": "2026-04-16T12:00:00Z",
            "payload": {},
        }
        with pytest.raises(ValidationError) as exc_info:
            WebhookEventRequest.model_validate(data)
        assert "event_type" in str(exc_info.value).lower() or "eventType" in str(exc_info.value)

    def test_unknown_run_status_rejected(self) -> None:
        data = {
            "id": str(_UUID),
            "agentRef": "test",
            "status": "bogus",
            "startedAt": "2026-04-16T12:00:00Z",
        }
        with pytest.raises(ValidationError):
            RunSummaryDto.model_validate(data)

    def test_valid_event_types_accepted(self) -> None:
        for evt in WebhookEventType:
            data = {
                "eventType": evt.value,
                "engineRunId": "eng",
                "engineEventId": "evt",
                "occurredAt": "2026-04-16T12:00:00Z",
                "payload": {},
            }
            req = WebhookEventRequest.model_validate(data)
            assert req.event_type == evt
