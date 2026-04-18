"""Output renderers for the CLI.

Keep this tiny — no external deps, no fancy escaping.  The goal is legible
fixed-width output for ``orchestrator`` commands; anything richer belongs
behind ``--json``.
"""

from __future__ import annotations

import json
from typing import Any, cast


def render_json(payload: Any) -> str:
    """Pretty-print *payload* as indented JSON; non-JSON types fall back to ``str``."""
    return json.dumps(payload, indent=2, default=str, sort_keys=False)


def render_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    """Fixed-width table from *rows* showing only *columns* in order.

    Missing cells render as empty strings.  Returns an empty string when
    *rows* is empty so the caller can print "no rows" above it.
    """
    if not rows:
        return ""

    cells = [[_cell(r.get(c)) for c in columns] for r in rows]
    widths = [max(len(c), max((len(row[i]) for row in cells), default=0)) for i, c in enumerate(columns)]

    header = "  ".join(c.ljust(widths[i]) for i, c in enumerate(columns))
    sep = "  ".join("-" * w for w in widths)
    body = "\n".join(
        "  ".join(row[i].ljust(widths[i]) for i in range(len(columns))) for row in cells
    )
    return f"{header}\n{sep}\n{body}"


def render_run_summary(data: dict[str, Any]) -> str:
    """Human-formatted single-run block (called for ``runs show``)."""
    keys = [
        "id",
        "agentRef",
        "status",
        "stopReason",
        "startedAt",
        "endedAt",
        "stepCount",
    ]
    width = max(len(k) for k in keys)
    lines = [f"{k.ljust(width)} : {_cell(data.get(k))}" for k in keys if k in data]
    last_raw = data.get("lastStep")
    if isinstance(last_raw, dict):
        last = cast("dict[str, Any]", last_raw)
        lines.append("last step:")
        for k in ("stepNumber", "nodeName", "status"):
            lines.append(f"  {k.ljust(width - 2)} : {_cell(last.get(k))}")
    return "\n".join(lines)


def render_trace_line(record: dict[str, Any]) -> str:
    """Human-format one NDJSON trace record (``{"kind": ..., "data": {...}}``).

    Unknown kinds fall back to the raw JSON so the operator still sees
    the content even if the CLI hasn't been taught a pretty renderer for
    that kind yet.
    """
    kind = record.get("kind", "?")
    data_raw = record.get("data", {})
    data: dict[str, Any] = (
        cast("dict[str, Any]", data_raw) if isinstance(data_raw, dict) else {}
    )
    if kind == "step":
        engine_run = data.get("engineRunId")
        suffix = f"  ({engine_run})" if engine_run else ""
        return (
            f"step #{data.get('stepNumber', '?')} "
            f"{data.get('nodeName', '?')} "
            f"{data.get('status', '?')}"
            f"{suffix}"
        )
    if kind == "policy_call":
        return (
            f"policy → {data.get('selectedTool', '?')}  "
            f"tokens={data.get('inputTokens', 0)}/{data.get('outputTokens', 0)}"
        )
    if kind == "webhook_event":
        return (
            f"webhook {data.get('eventType', '?')} "
            f"engine_run={data.get('engineRunId', '?')}"
        )
    return json.dumps(record, default=str)


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict | list):
        return json.dumps(value, default=str)
    return str(value)
