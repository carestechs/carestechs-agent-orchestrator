"""GitHub PR webhook helpers (FEAT-006 / T-120).

- :func:`verify_github_signature` checks ``X-Hub-Signature-256`` against the
  configured secret with ``hmac.compare_digest``.
- :func:`extract_task_reference` parses ``closes T-NNN`` or
  ``orchestrator: T-NNN`` references out of the PR title/body.
- :class:`GitHubPrEvent` is the subset of the PR payload we care about.
"""

from __future__ import annotations

import hashlib
import hmac
import re

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

_TASK_REF_RE = re.compile(
    r"(?:closes|orchestrator:)\s+(T-\d+)",
    re.IGNORECASE,
)


def verify_github_signature(
    raw_body: bytes, signature_header: str | None, secret: str
) -> bool:
    """Constant-time compare against ``sha256=<hex>`` header."""
    if not signature_header:
        return False
    prefix = "sha256="
    if not signature_header.startswith(prefix):
        return False
    provided = signature_header[len(prefix) :]
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, expected)


def extract_task_reference(title: str | None, body: str | None) -> str | None:
    """Return the first matching ``T-NNN`` from title or body, or None."""
    for text in (title, body):
        if not text:
            continue
        m = _TASK_REF_RE.search(text)
        if m:
            return m.group(1).upper()
    return None


_CAMEL = ConfigDict(populate_by_name=True, alias_generator=to_camel, extra="allow")


class GitHubPrHead(BaseModel):
    model_config = _CAMEL
    sha: str


class GitHubPrPullRequest(BaseModel):
    model_config = _CAMEL

    number: int
    title: str | None = None
    body: str | None = None
    head: GitHubPrHead
    merged: bool | None = None


class GitHubPrEvent(BaseModel):
    """Parsed minimal subset of a GitHub ``pull_request`` webhook payload."""

    model_config = _CAMEL

    action: str
    pull_request: GitHubPrPullRequest
