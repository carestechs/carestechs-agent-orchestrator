"""Tests for the GitHub PR webhook (FEAT-006 / T-120)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.modules.ai.enums import TaskStatus, WebhookSource
from app.modules.ai.models import Task, WebhookEvent, WorkItem
from app.modules.ai.webhooks.github import (
    extract_task_reference,
    verify_github_signature,
)

_ASYNCIO_MARK = pytest.mark.asyncio(loop_scope="function")


_SECRET = "github-test-secret"


@pytest.fixture(autouse=True)
def _github_secret_env() -> None:
    prior = os.environ.get("GITHUB_WEBHOOK_SECRET")
    os.environ["GITHUB_WEBHOOK_SECRET"] = _SECRET
    get_settings.cache_clear()
    yield
    if prior is None:
        os.environ.pop("GITHUB_WEBHOOK_SECRET", None)
    else:
        os.environ["GITHUB_WEBHOOK_SECRET"] = prior
    get_settings.cache_clear()


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(_SECRET.encode(), body, hashlib.sha256).hexdigest()


class TestSignatureHelper:
    def test_valid(self) -> None:
        body = b'{"x":1}'
        assert verify_github_signature(body, _sign(body), _SECRET) is True

    def test_missing(self) -> None:
        assert verify_github_signature(b"x", None, _SECRET) is False

    def test_wrong_prefix(self) -> None:
        assert verify_github_signature(b"x", "md5=abc", _SECRET) is False

    def test_wrong_digest(self) -> None:
        assert verify_github_signature(b"x", "sha256=deadbeef", _SECRET) is False


class TestExtractTaskReference:
    def test_closes_match(self) -> None:
        assert extract_task_reference(None, "fixes things, closes T-042") == "T-042"

    def test_orchestrator_match(self) -> None:
        assert extract_task_reference("orchestrator: T-7", None) == "T-7"

    def test_case_insensitive(self) -> None:
        assert extract_task_reference(None, "CLOSES t-1") == "T-1"

    def test_no_match(self) -> None:
        assert extract_task_reference("no refs", "body text") is None


async def _seed_task(db: AsyncSession, *, ref: str, status: TaskStatus) -> Task:
    wi = WorkItem(
        external_ref=f"FEAT-{ref}",
        type="FEAT",
        title="t",
        status="in_progress",
        opened_by="admin",
    )
    db.add(wi)
    await db.flush()
    task = Task(
        work_item_id=wi.id,
        external_ref=ref,
        title="do",
        status=status.value,
        proposer_type="admin",
        proposer_id="admin",
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


@_ASYNCIO_MARK
class TestEndpoint:
    async def test_bad_signature_returns_401_and_persists(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        body = json.dumps({"action": "opened"}).encode()
        r = await client.post(
            "/hooks/github/pr",
            content=body,
            headers={
                "X-Hub-Signature-256": "sha256=wrong",
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "d-1",
                "Content-Type": "application/json",
            },
        )
        assert r.status_code == 401
        events = (
            await db_session.scalars(
                select(WebhookEvent).where(
                    WebhookEvent.source == WebhookSource.GITHUB.value
                )
            )
        ).all()
        assert any(e.signature_ok is False for e in events)

    async def test_non_pull_request_event_ignored(
        self, client: AsyncClient
    ) -> None:
        body = b'{"zen":"hi"}'
        r = await client.post(
            "/hooks/github/pr",
            content=body,
            headers={
                "X-Hub-Signature-256": _sign(body),
                "X-GitHub-Event": "ping",
                "X-GitHub-Delivery": "d-ping",
                "Content-Type": "application/json",
            },
        )
        assert r.status_code == 202
        assert r.json()["data"]["ignored"] is True

    async def test_unmatched_pr_returns_null_task(
        self, client: AsyncClient
    ) -> None:
        body = json.dumps(
            {
                "action": "opened",
                "pull_request": {
                    "number": 100,
                    "title": "no ref here",
                    "body": "nothing",
                    "head": {"sha": "abc"},
                    "merged": False,
                },
            }
        ).encode()
        r = await client.post(
            "/hooks/github/pr",
            content=body,
            headers={
                "X-Hub-Signature-256": _sign(body),
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "d-unmatched",
                "Content-Type": "application/json",
            },
        )
        assert r.status_code == 202
        assert r.json()["data"]["matchedTaskId"] is None

    async def test_matched_pr_invokes_s11(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        task = await _seed_task(db_session, ref="T-201", status=TaskStatus.IMPLEMENTING)
        body = json.dumps(
            {
                "action": "opened",
                "pull_request": {
                    "number": 200,
                    "title": "closes T-201",
                    "body": "implementation done",
                    "head": {"sha": "ff"},
                    "merged": False,
                },
            }
        ).encode()
        r = await client.post(
            "/hooks/github/pr",
            content=body,
            headers={
                "X-Hub-Signature-256": _sign(body),
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "d-201",
                "Content-Type": "application/json",
            },
        )
        assert r.status_code == 202, r.text
        assert r.json()["data"]["matchedTaskId"] == str(task.id)

        await db_session.refresh(task)
        assert task.status == TaskStatus.IMPL_REVIEW.value

    async def test_dedupe_on_replay(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        body = json.dumps(
            {
                "action": "opened",
                "pull_request": {
                    "number": 300,
                    "title": "no ref",
                    "body": "",
                    "head": {"sha": "aa"},
                    "merged": False,
                },
            }
        ).encode()
        headers = {
            "X-Hub-Signature-256": _sign(body),
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "d-dup",
            "Content-Type": "application/json",
        }
        r1 = await client.post("/hooks/github/pr", content=body, headers=headers)
        r2 = await client.post("/hooks/github/pr", content=body, headers=headers)
        assert r1.status_code == 202
        assert r2.status_code == 202

        events = (
            await db_session.scalars(
                select(WebhookEvent).where(
                    WebhookEvent.dedupe_key == "github:pr:300:d-dup"
                )
            )
        ).all()
        assert len(list(events)) == 1
