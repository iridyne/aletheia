"""Error summary helpers -- used by doctor + optional CLI surface.

Provides:
  - recent_errors(limit, min_level, since_hours) -> list[dict]
  - error_counts_by_source(since_hours) -> dict[str, int]
  - format_error_summary(lines=15) -> str   (human-readable)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aletheia.config.loader import get_data_dir

_LEVEL_RANK = {"debug": 0, "info": 1, "warning": 2, "error": 3, "critical": 4}


def _errors_jsonl_path() -> Path:
    return get_data_dir() / "logs" / "errors.jsonl"


def recent_errors(
    limit: int = 50,
    min_level: str = "warning",
    since_hours: int | None = None,
) -> list[dict]:
    """Read most-recent errors from errors.jsonl (tail-first for speed)."""
    path = _errors_jsonl_path()
    if not path.exists():
        return []
    min_rank = _LEVEL_RANK.get(min_level.lower(), 2)
    rows: list[dict] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                lvl = _LEVEL_RANK.get(str(rec.get("level", "")).lower(), 2)
                if lvl < min_rank:
                    continue
                rows.append(rec)
    except OSError:
        return []
    rows.reverse()  # newest first
    return rows[:limit]


def error_counts_by_source(since_hours: int = 24) -> dict[str, int]:
    counts: dict[str, int] = {}
    for rec in recent_errors(
        limit=1000, min_level="warning", since_hours=since_hours
    ):
        src = rec.get("source", "unknown")
        counts[src] = counts.get(src, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def format_error_summary(lines: int = 15) -> str:
    """Human-friendly error summary for operator surfaces."""
    recent = recent_errors(limit=lines, min_level="warning")
    if not recent:
        return "No warnings/errors recorded."
    out = []
    for r in recent[:lines]:
        ts = str(r.get("timestamp", ""))[:19]
        lvl = str(r.get("level", "?")).upper()[:8]
        src = r.get("source", "?")
        msg = r.get("message", "")[:100]
        out.append(f"{ts} {lvl:8s} [{src}] {msg}")
    counts = error_counts_by_source(since_hours=24)
    if counts:
        top = ", ".join(f"{s}: {c}" for s, c in list(counts.items())[:5])
        out.append(f"\nTop sources (24h): {top}")
    return "\n".join(out)
