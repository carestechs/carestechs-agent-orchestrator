"""Tests for app.core.envelope: Envelope shape + camelCase meta."""

from __future__ import annotations

from app.core.envelope import Envelope, Meta, envelope


class TestEnvelopeWithoutMeta:
    def test_no_meta_omitted(self) -> None:
        result = envelope({"id": "x"})
        dumped = result.model_dump(by_alias=True, exclude_none=True)
        assert dumped == {"data": {"id": "x"}}
        assert "meta" not in dumped


class TestEnvelopeWithMeta:
    def test_meta_camel_case(self) -> None:
        meta = Meta(total_count=42, page=2, page_size=10)
        result = envelope({"id": "x"}, meta=meta)
        dumped = result.model_dump(by_alias=True)
        assert dumped == {
            "data": {"id": "x"},
            "meta": {"totalCount": 42, "page": 2, "pageSize": 10},
        }


class TestEnvelopeGeneric:
    def test_typed_data(self) -> None:
        env: Envelope[list[int]] = envelope([1, 2, 3])
        assert env.data == [1, 2, 3]
        assert env.meta is None
