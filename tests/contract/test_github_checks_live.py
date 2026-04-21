"""Opt-in live-network contract test for the GitHub Checks client.

Runs only under ``--run-live`` AND when ``GITHUB_PAT`` + ``GITHUB_SMOKE_PR_URL``
are set.  Creates + updates a check on the configured scratch PR so
operators can verify their PAT scope and GitHub's current API shape.

Uses a distinct check name (``orchestrator/smoke-test``) so it cannot
accidentally resolve the production ``orchestrator/impl-review`` gate.
"""

from __future__ import annotations

import os

import httpx
import pytest

from app.modules.ai.github.auth import PatAuthStrategy
from app.modules.ai.github.checks import HttpxGitHubChecksClient
from app.modules.ai.github.pr_urls import parse_pr_url

pytestmark = [pytest.mark.live, pytest.mark.asyncio(loop_scope="function")]


async def test_pat_create_and_update_check() -> None:
    pat = os.getenv("GITHUB_PAT")
    pr_url = os.getenv("GITHUB_SMOKE_PR_URL")
    if not pat or not pr_url:
        pytest.skip(
            "set GITHUB_PAT and GITHUB_SMOKE_PR_URL to enable the live smoke"
        )

    ref = parse_pr_url(pr_url)
    auth_headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=30.0) as http:
        # Look up the PR's head sha — check-runs need a commit, not a PR id.
        pr_resp = await http.get(
            f"https://api.github.com/repos/{ref.owner}/{ref.repo}/pulls/{ref.pull_number}",
            headers=auth_headers,
        )
        pr_resp.raise_for_status()
        head_sha = pr_resp.json()["head"]["sha"]

        client = HttpxGitHubChecksClient(auth=PatAuthStrategy(pat), http=http)
        check_id = await client.create_check(
            owner=ref.owner,
            repo=ref.repo,
            head_sha=head_sha,
            name="orchestrator/smoke-test",
        )
        assert check_id

        await client.update_check(
            owner=ref.owner,
            repo=ref.repo,
            check_id=check_id,
            conclusion="success",
        )
