"""Parse a GitHub PR URL into ``owner``, ``repo``, and ``pull_number``.

FEAT-007 routes each merge-gating check to whichever repo the task's PR
lives in — we do **not** pin a single ``GITHUB_REPO``.  The canonical
input is the PR's browser URL (``https://github.com/{owner}/{repo}/pull/{n}``)
as submitted in the implementation-signal payload; this module extracts
the three fields or fails fast with a typed ``ValidationError`` so the
service layer never fans out to the wrong repo on a malformed URL.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from app.core.exceptions import ValidationError

_ALLOWED_HOSTS = {"github.com", "www.github.com"}


@dataclass(frozen=True, slots=True)
class PullRequestRef:
    """A resolved reference to one GitHub pull request."""

    owner: str
    repo: str
    pull_number: int

    @property
    def slug(self) -> str:
        """Return ``owner/repo`` — the form the GitHub REST API expects."""
        return f"{self.owner}/{self.repo}"


def parse_pr_url(url: str) -> PullRequestRef:
    """Return the ``PullRequestRef`` for *url*.

    Accepts any ``https://github.com/{owner}/{repo}/pull/{n}`` URL, with or
    without trailing slash, query string, or fragment.  Raises
    ``ValidationError`` on anything else — non-HTTPS, non-github.com host,
    wrong path shape, non-integer pull number, or empty segments.
    """
    if not url:
        raise ValidationError("invalid PR URL: must be a non-empty string")

    parsed = urlparse(url.strip())
    if parsed.scheme != "https":
        raise ValidationError(
            f"invalid PR URL: scheme must be https, got {parsed.scheme!r}"
        )
    if parsed.netloc.lower() not in _ALLOWED_HOSTS:
        raise ValidationError(
            f"invalid PR URL: host must be github.com, got {parsed.netloc!r}"
        )

    segments = [p for p in parsed.path.split("/") if p]
    if len(segments) != 4 or segments[2] != "pull":
        raise ValidationError(
            "invalid PR URL: expected path /{owner}/{repo}/pull/{n}"
        )

    owner, repo, _, raw_n = segments
    if not owner or not repo:
        raise ValidationError("invalid PR URL: owner and repo must be non-empty")

    try:
        pull_number = int(raw_n)
    except ValueError as exc:
        raise ValidationError(
            f"invalid PR URL: pull number must be an integer, got {raw_n!r}"
        ) from exc
    if pull_number <= 0:
        raise ValidationError(
            f"invalid PR URL: pull number must be positive, got {pull_number}"
        )

    return PullRequestRef(owner=owner, repo=repo, pull_number=pull_number)


__all__ = ["PullRequestRef", "parse_pr_url"]
