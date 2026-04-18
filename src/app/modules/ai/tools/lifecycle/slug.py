"""Pure slug-derivation helper for lifecycle file names (FEAT-005 / T-093)."""

from __future__ import annotations

import re
import unicodedata

_WHITESPACE_RE = re.compile(r"\s+")
_INVALID_CHARS_RE = re.compile(r"[^a-z0-9-]+")
_MULTI_DASH_RE = re.compile(r"-+")


def slugify(title: str, *, max_len: int = 40) -> str:
    """Derive a filesystem-safe slug from *title*.

    Normalizes Unicode via NFKD + ASCII-ignore (Portuguese / Spanish /
    French diacritics drop to their base letters), lowercases, replaces
    whitespace runs with hyphens, strips everything not in ``[a-z0-9-]``,
    collapses repeated hyphens, and trims to *max_len* on a hyphen boundary.

    Raises :class:`ValueError` when the title has no slug-worthy characters.
    """
    normalized = unicodedata.normalize("NFKD", title)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower().strip()
    hyphened = _WHITESPACE_RE.sub("-", lowered)
    cleaned = _INVALID_CHARS_RE.sub("", hyphened)
    collapsed = _MULTI_DASH_RE.sub("-", cleaned).strip("-")
    trimmed = collapsed[:max_len].rstrip("-")
    if not trimmed:
        raise ValueError(f"cannot derive slug from {title!r}")
    return trimmed
