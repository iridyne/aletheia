"""Observation-only metadata for context rendered into model calls.

This module never stores prompts, model responses, or source bodies.  It records
only stable source identities and bounded selection metadata so context can be
audited without becoming a second prompt archive.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from aletheia.economics.schema import ensure_economics_schema

_SCHEMA_VERSION = "observer-context-v1"
_MAX_TEXT = 160
_ALLOWED_KINDS = {"environment", "news", "learning"}
_ALLOWED_FRESHNESS = {"fresh", "stale", "unknown"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:_MAX_TEXT] if text else None


def _metadata(value: dict[str, Any] | None) -> str:
    # The explicit projection prevents an accidental caller from persisting text.
    value = value or {}
    safe = {
        key: _bounded_text(value.get(key))
        for key in ("analysis_state", "freshness_status", "relevance_bucket")
        if _bounded_text(value.get(key)) is not None
    }
    return json.dumps(safe, sort_keys=True, separators=(",", ":"))


def record_context_artifacts(
    *,
    run_id: str | None,
    caller: str,
    symbol: str | None,
    artifacts: list[dict[str, Any]],
    logical_call_id: str | None = None,
    evaluation_pair_id: str | None = None,
    evaluation_arm: str | None = None,
    fixture_id: str | None = None,
    observed_at: str | None = None,
) -> int:
    """Persist rendered-context metadata best-effort; never raise to a caller."""
    if not artifacts:
        return 0
    now = observed_at or _utcnow()
    rows: list[tuple[Any, ...]] = []
    for artifact in artifacts:
        kind = str(artifact.get("kind") or "")
        if kind not in _ALLOWED_KINDS:
            continue
        stable_id = _bounded_text(artifact.get("stable_id"))
        if not stable_id:
            continue
        freshness = str(artifact.get("freshness_status") or "unknown")
        if freshness not in _ALLOWED_FRESHNESS:
            freshness = "unknown"
        try:
            rendered_chars = max(0, int(artifact.get("rendered_chars") or 0))
        except (TypeError, ValueError):
            rendered_chars = 0
        rows.append(
            (
                f"ctx_{uuid.uuid4().hex}",
                _SCHEMA_VERSION,
                run_id,
                caller,
                _bounded_text(logical_call_id),
                _bounded_text(evaluation_pair_id),
                _bounded_text(evaluation_arm),
                _bounded_text(fixture_id),
                _bounded_text(symbol),
                kind,
                stable_id,
                _bounded_text(artifact.get("source")),
                _bounded_text(artifact.get("selected_reason")),
                _bounded_text(artifact.get("trust_class")),
                freshness,
                _bounded_text(artifact.get("published_at")),
                _bounded_text(artifact.get("observed_at")),
                rendered_chars,
                _metadata(artifact.get("metadata")),
                now,
            )
        )
    if not rows:
        return 0
    try:
        db = ensure_economics_schema()
        was_in_tx = db.in_transaction
        db.executemany(
            """INSERT INTO llm_context_artifacts (
                id, schema_version, run_id, caller, logical_call_id,
                evaluation_pair_id, evaluation_arm, fixture_id, symbol, kind, stable_id,
                source, selected_reason, trust_class, freshness_status,
                published_at, observed_at, rendered_chars, metadata_json, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        if not was_in_tx:
            db.commit()
        return len(rows)
    except Exception:
        return 0


def list_context_artifacts(*, days: int = 7, run_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Return bounded prompt-free context records, newest first."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    db = ensure_economics_schema()
    if run_id:
        rows = db.execute(
            """SELECT * FROM llm_context_artifacts
               WHERE created_at >= ? AND created_at <= ? AND run_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (start.isoformat(), end.isoformat(), run_id, limit),
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT * FROM llm_context_artifacts
               WHERE created_at >= ? AND created_at <= ?
               ORDER BY created_at DESC LIMIT ?""",
            (start.isoformat(), end.isoformat(), limit),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            item["metadata"] = {}
        result.append(item)
    return result


def summarize_context_artifacts(*, days: int = 7, run_id: str | None = None) -> dict[str, Any]:
    """Aggregate context usage without exposing rendered text."""
    records = list_context_artifacts(days=days, run_id=run_id, limit=5_000)
    by_kind: dict[str, dict[str, int]] = {}
    freshness: dict[str, int] = {}
    for record in records:
        bucket = by_kind.setdefault(record["kind"], {"artifacts": 0, "rendered_chars": 0})
        bucket["artifacts"] += 1
        try:
            bucket["rendered_chars"] += int(record["rendered_chars"])
        except (KeyError, TypeError, ValueError):
            pass
        status = record["freshness_status"]
        freshness[status] = freshness.get(status, 0) + 1
    return {"schema_version": _SCHEMA_VERSION, "artifacts": len(records), "by_kind": by_kind, "freshness": freshness}
