"""Tests for ``app.modules.ai.github.pr_urls``."""

from __future__ import annotations

import pytest

from app.core.exceptions import ValidationError
from app.modules.ai.github.pr_urls import PullRequestRef, parse_pr_url


class TestHappyPath:
    def test_canonical(self) -> None:
        ref = parse_pr_url("https://github.com/foo/bar/pull/42")
        assert ref == PullRequestRef(owner="foo", repo="bar", pull_number=42)
        assert ref.slug == "foo/bar"

    def test_trailing_slash(self) -> None:
        ref = parse_pr_url("https://github.com/foo/bar/pull/42/")
        assert ref.pull_number == 42

    def test_with_query_and_fragment(self) -> None:
        ref = parse_pr_url("https://github.com/foo/bar/pull/42?diff=unified#comment-7")
        assert ref == PullRequestRef(owner="foo", repo="bar", pull_number=42)

    def test_www_prefix_accepted(self) -> None:
        ref = parse_pr_url("https://www.github.com/foo/bar/pull/1")
        assert ref.owner == "foo"
        assert ref.repo == "bar"
        assert ref.pull_number == 1

    def test_surrounding_whitespace_stripped(self) -> None:
        ref = parse_pr_url("   https://github.com/foo/bar/pull/42  \n")
        assert ref.pull_number == 42

    def test_case_preserved(self) -> None:
        ref = parse_pr_url("https://github.com/Foo-Org/Bar.Repo/pull/1")
        assert ref.owner == "Foo-Org"
        assert ref.repo == "Bar.Repo"


class TestInvalid:
    @pytest.mark.parametrize(
        "url",
        [
            "http://github.com/foo/bar/pull/1",  # not https
            "ftp://github.com/foo/bar/pull/1",
            "https://gitlab.com/foo/bar/pull/1",  # wrong host
            "https://github.enterprise.com/foo/bar/pull/1",
            "https://github.com/foo/bar/issues/1",  # wrong path
            "https://github.com/foo/bar/pull",  # missing number
            "https://github.com/foo/bar/pull/abc",  # non-integer
            "https://github.com/foo/bar/pull/-5",  # negative
            "https://github.com/foo/bar/pull/0",  # zero
            "https://github.com//bar/pull/1",  # empty owner
            "not a url",
            "",
        ],
    )
    def test_rejects_bad_input(self, url: str) -> None:
        with pytest.raises(ValidationError):
            parse_pr_url(url)

    def test_rejects_none_via_runtime(self) -> None:
        # Defensive: runtime callers (YAML, JSON payloads) can produce None.
        with pytest.raises((ValidationError, AttributeError, TypeError)):
            parse_pr_url(None)  # type: ignore[arg-type]
