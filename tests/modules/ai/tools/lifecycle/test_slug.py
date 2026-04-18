"""Tests for the slugify helper (FEAT-005 / T-093)."""

from __future__ import annotations

import pytest

from app.modules.ai.tools.lifecycle.slug import slugify


class TestSlugify:
    def test_happy_path(self) -> None:
        assert slugify("Add Delivery Fee Service") == "add-delivery-fee-service"

    def test_unicode_diacritics(self) -> None:
        assert slugify("Aí, beleza") == "ai-beleza"

    def test_all_punctuation_raises(self) -> None:
        with pytest.raises(ValueError, match="cannot derive slug"):
            slugify("!!!")

    def test_exceeds_max_len_trims_cleanly(self) -> None:
        long_title = "a" * 50 + " b"
        result = slugify(long_title, max_len=20)
        assert len(result) <= 20
        assert not result.endswith("-")

    def test_collapses_repeated_dashes(self) -> None:
        assert slugify("foo   ---   bar") == "foo-bar"

    def test_strips_trailing_dash(self) -> None:
        assert slugify("hello world -") == "hello-world"
