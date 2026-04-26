"""RemoteExecutor unit tests (FEAT-009 / T-215)."""

from __future__ import annotations

import uuid

import httpx
import pytest
import respx

from app.modules.ai.executors.base import DispatchContext
from app.modules.ai.executors.remote import RemoteExecutor

pytestmark = pytest.mark.asyncio(loop_scope="function")


_URL = "https://exec.test/dispatch"
_CALLBACK = "https://orch.test/hooks/executors/claude-code"
_SECRET = "test-secret"


def _ctx() -> DispatchContext:
    return DispatchContext(
        dispatch_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        step_id=uuid.uuid4(),
        agent_ref="agent@1",
        node_name="node_a",
        intake={"x": 1},
    )


async def _executor() -> RemoteExecutor:
    client = httpx.AsyncClient()
    return RemoteExecutor(
        ref="remote:claude-code",
        url=_URL,
        secret=_SECRET,
        callback_url=_CALLBACK,
        client=client,
    )


class TestSuccessPath:
    @respx.mock
    async def test_202_returns_dispatched(self) -> None:
        respx.post(_URL).respond(status_code=202)
        executor = await _executor()
        env = await executor.dispatch(_ctx())
        assert env.state.value == "dispatched"
        assert env.dispatched_at is not None
        assert env.outcome is None
        assert env.executor_ref == "remote:claude-code"
        assert env.mode.value == "remote"

    @respx.mock
    async def test_signature_header_sent(self) -> None:
        route = respx.post(_URL).respond(status_code=202)
        executor = await _executor()
        await executor.dispatch(_ctx())
        sig = route.calls.last.request.headers.get("x-executor-signature")
        assert sig is not None
        assert sig.startswith("sha256=")


class TestFailurePaths:
    @respx.mock
    async def test_4xx_failed_no_retry(self) -> None:
        route = respx.post(_URL).respond(status_code=400, text="bad shape")
        executor = await _executor()
        env = await executor.dispatch(_ctx())
        assert env.state.value == "failed"
        assert env.detail is not None
        assert "400" in env.detail
        assert route.call_count == 1  # no retry on 4xx

    @respx.mock
    async def test_5xx_retries_then_fails(self) -> None:
        route = respx.post(_URL).respond(status_code=502)
        executor = await _executor()
        env = await executor.dispatch(_ctx())
        assert env.state.value == "failed"
        assert "502" in (env.detail or "")
        assert route.call_count == 3

    @respx.mock
    async def test_5xx_then_202_succeeds(self) -> None:
        responses = [
            httpx.Response(503),
            httpx.Response(202),
        ]
        route = respx.post(_URL).mock(side_effect=responses)
        executor = await _executor()
        env = await executor.dispatch(_ctx())
        assert env.state.value == "dispatched"
        assert route.call_count == 2

    @respx.mock
    async def test_connection_error_retries_then_fails(self) -> None:
        route = respx.post(_URL).mock(side_effect=httpx.ConnectError("nope"))
        executor = await _executor()
        env = await executor.dispatch(_ctx())
        assert env.state.value == "failed"
        assert "connection" in (env.detail or "")
        assert route.call_count == 3
