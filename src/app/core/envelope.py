"""Response envelope: ``Envelope[T]`` generic + ``Meta`` DTO + ``envelope()`` helper."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Meta(BaseModel):
    """Pagination metadata for collection responses."""

    total_count: int = Field(serialization_alias="totalCount")
    page: int
    page_size: int = Field(serialization_alias="pageSize")


class Envelope(BaseModel, Generic[T]):
    """Standard ``{ data, meta? }`` response wrapper."""

    data: T
    meta: Meta | None = None


def envelope(data: T, meta: Meta | None = None) -> Envelope[T]:
    """Wrap *data* in an ``Envelope``, optionally attaching *meta*."""
    return Envelope(data=data, meta=meta)
