"""Tests for GitHub-webhook repository helpers (FEAT-006 / T-111)."""

from __future__ import annotations

from app.modules.ai.repository import compute_github_pr_dedupe_key


class TestComputeGithubPrDedupeKey:
    def test_stable_shape(self) -> None:
        key = compute_github_pr_dedupe_key(42, "abc-123")
        assert key == "github:pr:42:abc-123"

    def test_deterministic(self) -> None:
        assert compute_github_pr_dedupe_key(7, "d") == compute_github_pr_dedupe_key(7, "d")

    def test_different_pr_produces_different_key(self) -> None:
        a = compute_github_pr_dedupe_key(1, "d")
        b = compute_github_pr_dedupe_key(2, "d")
        assert a != b

    def test_different_delivery_produces_different_key(self) -> None:
        a = compute_github_pr_dedupe_key(1, "a")
        b = compute_github_pr_dedupe_key(1, "b")
        assert a != b
