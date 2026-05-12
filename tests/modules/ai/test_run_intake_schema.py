"""FEAT-014 / T-283 — RunIntakeWorkItem DTO boundary tests.

The DTO is the typed view the route uses to extract ``intake.workItem``
from the (still-dict) ``CreateRunRequest.intake``.  These tests pin the
boundary: id regex, kind enum, content nullability, camelCase aliases.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.ai.enums import WorkItemType
from app.modules.ai.schemas import RunIntakeWorkItem


class TestIdValidation:
    def test_valid_canonical(self) -> None:
        m = RunIntakeWorkItem(id="FEAT-100", kind=WorkItemType.FEAT, content="x")
        assert m.id == "FEAT-100"

    def test_valid_with_slug(self) -> None:
        m = RunIntakeWorkItem(id="BUG-7-some-slug", kind=WorkItemType.BUG)
        assert m.id == "BUG-7-some-slug"

    @pytest.mark.parametrize(
        "bad_id",
        [
            "feat-100",  # lowercase
            "FEAT-abc",  # no number
            "FEAT_100",  # underscore
            "FEAT",  # no number at all
            "FEAT-100-Slug",  # uppercase slug
            "",  # empty (also caught by min_length)
        ],
    )
    def test_invalid_ids_rejected(self, bad_id: str) -> None:
        with pytest.raises(ValidationError):
            RunIntakeWorkItem(id=bad_id, kind=WorkItemType.FEAT)


class TestKindAndContent:
    def test_kind_must_be_enum_value(self) -> None:
        with pytest.raises(ValidationError):
            RunIntakeWorkItem.model_validate({"id": "FEAT-1", "kind": "WIDGET"})

    def test_content_optional(self) -> None:
        m = RunIntakeWorkItem(id="FEAT-1", kind=WorkItemType.FEAT)
        assert m.content is None

    def test_content_oversized_still_parses_at_model_level(self) -> None:
        # The hard cap is enforced at the route by ``INTAKE_WORK_ITEM_MAX_BYTES``;
        # the model itself accepts any non-None string so the route can produce
        # a clean 413 instead of a generic 422.
        big = "x" * (5 * 1024 * 1024)
        m = RunIntakeWorkItem(id="FEAT-1", kind=WorkItemType.FEAT, content=big)
        assert m.content is not None
        assert len(m.content) == 5 * 1024 * 1024


class TestCamelCaseAliases:
    def test_parses_camel_case_input(self) -> None:
        m = RunIntakeWorkItem.model_validate(
            {"id": "FEAT-1", "kind": "FEAT", "content": "body"}
        )
        assert m.content == "body"

    def test_serializes_camel_case_by_alias(self) -> None:
        m = RunIntakeWorkItem(id="FEAT-1", kind=WorkItemType.FEAT, content="body")
        dumped = m.model_dump(by_alias=True)
        assert set(dumped.keys()) == {"id", "kind", "content"}
        assert dumped["kind"] == "FEAT"
