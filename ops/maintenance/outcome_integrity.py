"""Explicit outcome-history repair; no automatic runtime writer."""

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

import typer

from aletheia.knowledge.document import canonicalize_inline_document
from aletheia.maintenance.outcome_integrity import (
    CLEAN_WINDOW_ANCHOR_ID, OUTCOME_SEMANTICS_VERSION, REMEDIATION_BATCH_ID,
    _plan_outcome_history,
)


def remediate_outcome_history(*, dry_run: bool = True) -> dict[str, Any]:
    """Plan or apply one deterministic remediation batch.

    ``compensation_candidates`` are report output only. There is intentionally no
    owner-approval parameter and no capital/paper-account insertion path here.
    """
    from aletheia.storage.sqlite import get_db

    db = get_db()
    savepoint = "outcome_integrity_remediation"
    owns_transaction = False
    uses_savepoint = False
    was_in_tx = db.in_transaction
    if dry_run:
        if not was_in_tx:
            db.execute("BEGIN")
            owns_transaction = True
    else:
        if was_in_tx:
            db.execute(f"SAVEPOINT {savepoint}")
            uses_savepoint = True
        else:
            db.execute("BEGIN IMMEDIATE")
            owns_transaction = True
    try:
        result, classifications, archives = _plan_outcome_history(db)
        result["dry_run"] = dry_run
        if dry_run:
            if owns_transaction:
                db.commit()
            return result

        now = datetime.now(timezone.utc).isoformat()
        for item in classifications:
            db.execute(
                "UPDATE positions SET close_category=?, exit_price_source='unavailable', "
                "exit_fill_verified=0, outcome_eligible=0, outcome_exclusion_reason=?, "
                "outcome_semantics_version=? WHERE id=? AND outcome_semantics_version IS NULL",
                (
                    item["close_category"], item["outcome_exclusion_reason"],
                    OUTCOME_SEMANTICS_VERSION, item["id"],
                ),
            )
        for item in archives:
            marker_id = _marker_id(item["id"])
            marker = {
                "batch_id": REMEDIATION_BATCH_ID,
                "semantics_version": OUTCOME_SEMANTICS_VERSION,
                "supersedes_id": item["id"],
                "superseded_by": f"clean-window:{CLEAN_WINDOW_ANCHOR_ID}",
                "reason": item["reason"],
                "archive": True,
            }
            content = json.dumps(marker, sort_keys=True)
            canonical = canonicalize_inline_document(content)
            db.execute(
                "UPDATE knowledge_items SET status='archived', updated_at=? "
                "WHERE id=? AND status='active'",
                (now, item["id"]),
            )
            db.execute(
                "INSERT OR IGNORE INTO knowledge_items "
                "(id,item_type,title,confidence,status,tags,content,content_hash,"
                "content_format,content_sha256,metadata_json,symbol,direction,"
                "scanner_family,outcome,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    marker_id,
                    "outcome_remediation_supersession",
                    f"Supersession: {item['id']}",
                    "medium_confidence",
                    "active",
                    json.dumps(["outcome_integrity", "supersession"]),
                    canonical["content"],
                    marker_id,
                    canonical["content_format"],
                    canonical["content_sha256"],
                    canonical["metadata_json"],
                    canonical["symbol"],
                    canonical["direction"],
                    canonical["scanner_family"],
                    canonical["outcome"],
                    now,
                    now,
                ),
            )
        if uses_savepoint:
            db.execute(f"RELEASE SAVEPOINT {savepoint}")
        elif owns_transaction:
            db.commit()
    except Exception:
        if uses_savepoint:
            db.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            db.execute(f"RELEASE SAVEPOINT {savepoint}")
        elif owns_transaction:
            db.rollback()
        raise

    result["classification"]["applied"] = len(classifications)
    result["archive"]["applied"] = len(archives)
    try:
        from aletheia.knowledge.retrieval import reindex_all
        result["reindexed"] = reindex_all()
    except Exception:
        result["reindexed"] = None
    result["rebuild"] = _rebuild_allowed_reports()
    return result


def _rebuild_allowed_reports() -> dict[str, str]:
    """Rebuild deterministic reports only; no LLM/network derived content."""
    builders = {}
    from aletheia.analytics.confidence_calibration import _calibration_builder
    from aletheia.analytics.reasoning_feedback import _feedback_builder

    for name, builder in (
        ("calibration_report", _calibration_builder),
        ("reasoning_feedback", _feedback_builder),
    ):
        try:
            if builder is None:
                builders[name] = "unavailable"
                continue
            builders[name] = "generated" if builder() is not None else "insufficient"
        except Exception:
            builders[name] = "failed"
    return builders


def remediate_outcomes(
    apply: bool = typer.Option(
        False, "--apply",
        help="Apply additive classification/archive changes. Default is dry-run.",
    ),
):
    """Plan non-destructive outcome-history remediation; apply only when requested.

    Paper-cash compensation is always report-only and still requires a separate owner
    decision. This command cannot apply it.
    """
    from aletheia.storage.migrations import assert_migrations_current

    assert_migrations_current()
    result = remediate_outcome_history(dry_run=not apply)
    typer.echo(json.dumps(result, indent=2, ensure_ascii=False))


def _marker_id(item_id: str) -> str:
    return "oi-sup-" + hashlib.sha256(
        f"{REMEDIATION_BATCH_ID}:{item_id}".encode()
    ).hexdigest()[:24]
