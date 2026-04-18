"""Tests for the shared atomic-write helpers (FEAT-005 / T-091)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.exceptions import PolicyError
from app.modules.ai.tools.lifecycle.atomic_write import overwrite_atomic, write_atomic


class TestWriteAtomic:
    def test_happy(self, tmp_path: Path) -> None:
        target = tmp_path / "sub" / "file.md"
        write_atomic(target, "hello\n", repo_root=tmp_path)
        assert target.read_text() == "hello\n"
        # No leftover temp files.
        assert list(target.parent.glob(".file.md.tmp.*")) == []

    def test_refuses_to_overwrite(self, tmp_path: Path) -> None:
        target = tmp_path / "file.md"
        target.write_text("existing\n")
        with pytest.raises(PolicyError, match="file already exists"):
            write_atomic(target, "new\n", repo_root=tmp_path)
        assert target.read_text() == "existing\n"

    def test_rejects_escape_root(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / f"outside-{tmp_path.name}.md"
        with pytest.raises(PolicyError, match="escapes repo root"):
            write_atomic(outside, "x", repo_root=tmp_path)

    def test_cleans_tmp_on_write_failure(self, tmp_path: Path) -> None:
        target = tmp_path / "file.md"
        original_fsync = os.fsync

        def boom(_fd: int) -> None:
            raise OSError("disk full (simulated)")

        with patch("app.modules.ai.tools.lifecycle.atomic_write.os.fsync", boom):
            with pytest.raises(OSError, match="disk full"):
                write_atomic(target, "data", repo_root=tmp_path)
        assert not target.exists()
        assert list(tmp_path.glob(".file.md.tmp.*")) == []
        # Sanity: real fsync untouched.
        assert os.fsync is original_fsync


class TestOverwriteAtomic:
    def test_overwrites_existing(self, tmp_path: Path) -> None:
        target = tmp_path / "file.md"
        target.write_text("old\n")
        overwrite_atomic(target, "new\n", repo_root=tmp_path)
        assert target.read_text() == "new\n"

    def test_creates_if_missing(self, tmp_path: Path) -> None:
        target = tmp_path / "fresh.md"
        overwrite_atomic(target, "content\n", repo_root=tmp_path)
        assert target.read_text() == "content\n"

    def test_rejects_escape_root(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / f"outside-{tmp_path.name}.md"
        with pytest.raises(PolicyError, match="escapes repo root"):
            overwrite_atomic(outside, "x", repo_root=tmp_path)
