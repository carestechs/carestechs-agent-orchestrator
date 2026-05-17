"""Unit tests for ``CodeSourceDto`` (IMP-005 / T-311).

Pure schema validation — no DB, no HTTP, no I/O.  Mirrors the surface
coverage pattern from ``test_schemas.py`` for ``RunIntakeWorkItem``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.ai.schemas import CodeSourceDto


class TestCodeSourceDtoRepo:
    def test_minimal_valid_repo(self) -> None:
        dto = CodeSourceDto(repo="org/name", baseBranch="main")
        assert dto.repo == "org/name"
        assert dto.base_branch == "main"
        assert dto.work_branch is None

    @pytest.mark.parametrize(
        "bad_repo",
        [
            "https://github.com/org/name",
            "git@github.com:org/name",
            "org/name.git",
            "",
            "   ",
            "org",
            "/org/name",
            "org//name",
            "org/name/extra",
        ],
    )
    def test_invalid_repo_rejected(self, bad_repo: str) -> None:
        with pytest.raises(ValidationError):
            CodeSourceDto(repo=bad_repo, baseBranch="main")

    def test_repo_with_dots_and_dashes(self) -> None:
        """Hyphens, dots, and underscores are part of the GH name shape."""
        dto = CodeSourceDto(repo="my-org.com_x/my.repo-name", baseBranch="main")
        assert dto.repo == "my-org.com_x/my.repo-name"


class TestCodeSourceDtoBaseBranch:
    @pytest.mark.parametrize(
        "bad_branch",
        [
            "",
            "   ",
            " main ",
            "/main",
            "feat/..escape",
            "feat\tx",
            "feat\nx",
            "feat\x01x",
        ],
    )
    def test_invalid_base_branch_rejected(self, bad_branch: str) -> None:
        with pytest.raises(ValidationError):
            CodeSourceDto(repo="org/name", baseBranch=bad_branch)

    def test_valid_branch_with_slash_segment(self) -> None:
        dto = CodeSourceDto(repo="org/name", baseBranch="feat/imp-005")
        assert dto.base_branch == "feat/imp-005"


class TestCodeSourceDtoWorkBranch:
    def test_work_branch_optional(self) -> None:
        dto = CodeSourceDto(repo="org/name", baseBranch="main")
        assert dto.work_branch is None

    def test_work_branch_alias_roundtrip(self) -> None:
        dto = CodeSourceDto.model_validate(
            {"repo": "org/name", "baseBranch": "main", "workBranch": "feat/x"}
        )
        assert dto.work_branch == "feat/x"
        dumped = dto.model_dump(by_alias=True)
        assert dumped["workBranch"] == "feat/x"

    def test_invalid_work_branch_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CodeSourceDto(
                repo="org/name", baseBranch="main", workBranch="../escape"
            )

    def test_empty_work_branch_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CodeSourceDto(repo="org/name", baseBranch="main", workBranch="")


class TestCodeSourceDtoStructure:
    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CodeSourceDto.model_validate(
                {"repo": "org/name", "baseBranch": "main", "bogus": "x"}
            )

    def test_camel_case_input_accepted(self) -> None:
        dto = CodeSourceDto.model_validate(
            {"repo": "org/name", "baseBranch": "main"}
        )
        assert dto.base_branch == "main"

    def test_snake_case_input_accepted(self) -> None:
        """Pydantic ``populate_by_name=True`` allows snake_case + camelCase."""
        dto = CodeSourceDto.model_validate(
            {"repo": "org/name", "base_branch": "main"}
        )
        assert dto.base_branch == "main"
