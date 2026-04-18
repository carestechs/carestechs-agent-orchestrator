"""Tests for the git subprocess boundary (FEAT-005 / T-094)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.exceptions import PolicyError
from app.modules.ai.tools.lifecycle import git as git_mod
from app.modules.ai.tools.lifecycle.git import get_diff


def _requires_git() -> None:
    if shutil.which("git") is None:
        pytest.skip("git binary not available in this environment")


def _init_repo(tmp_path: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "t@t.local"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Tests"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )


class TestGetDiff:
    def test_happy(self, tmp_path: Path) -> None:
        _requires_git()
        _init_repo(tmp_path)
        (tmp_path / "file.txt").write_text("first\n")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "checkout", "-b", "feature"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )
        (tmp_path / "file.txt").write_text("first\nsecond\n")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add second line"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

        diff = get_diff(["file.txt"], base="main", cwd=tmp_path)
        assert "+second" in diff

    def test_no_repo_or_no_base(self, tmp_path: Path) -> None:
        """In a dir with no .git and no 'main' branch anywhere reachable, the
        diff cannot resolve — surfaces as PolicyError.  Which specific git
        error string fires depends on the git version + path — we only care
        that a PolicyError is raised, not the exact sub-message.
        """
        _requires_git()
        with pytest.raises(PolicyError):
            get_diff(["x"], base="main", cwd=tmp_path)

    def test_missing_binary(self, tmp_path: Path) -> None:
        with patch.object(git_mod.shutil, "which", return_value=None):
            with pytest.raises(PolicyError, match="git not available"):
                get_diff(["x"], base="main", cwd=tmp_path)

    def test_timeout(self, tmp_path: Path) -> None:
        _requires_git()
        _init_repo(tmp_path)

        def _raise_timeout(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(cmd="git", timeout=10)

        with patch.object(git_mod.subprocess, "run", _raise_timeout):
            with pytest.raises(PolicyError, match="timed out"):
                get_diff(["x"], base="main", cwd=tmp_path)

    def test_unknown_base(self, tmp_path: Path) -> None:
        _requires_git()
        _init_repo(tmp_path)
        (tmp_path / "x.txt").write_text("x")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "i"], cwd=tmp_path, check=True, capture_output=True
        )
        with pytest.raises(PolicyError, match="unknown base"):
            get_diff(["x.txt"], base="does-not-exist", cwd=tmp_path)

    def test_truncates_large_diff(self, tmp_path: Path) -> None:
        _requires_git()
        _init_repo(tmp_path)
        (tmp_path / "big.txt").write_text("x\n")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "i"], cwd=tmp_path, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "checkout", "-b", "big"], cwd=tmp_path, check=True, capture_output=True
        )
        (tmp_path / "big.txt").write_text("line\n" * 20000)  # > 64 KB of changes
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "big"], cwd=tmp_path, check=True, capture_output=True
        )

        diff = get_diff(["big.txt"], base="main", cwd=tmp_path)
        assert "<truncated" in diff
