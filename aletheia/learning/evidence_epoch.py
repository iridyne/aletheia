"""Evidence epoch anchoring for deployment-bounded observation windows.

An evidence epoch pins the deployed commit SHA and wall-clock boundary so
that PF, MFE→realized capture, tail, and regime slices are attributable to a
specific build and comparable across epochs.

Usage
-----
Call :func:`ensure_evidence_epochs` once at Gateway startup (after migrations)
or from ``aletheia evidence epoch register`` to anchor the current epoch.  The
function is idempotent: it returns without mutation if an identical epoch is
already open for each path.

Evidence epochs are *observation-only*; they carry no trading authority.  The
gateway and operator remain the sole entry/exit decision paths.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("aletheia.learning.evidence_epoch")

# The two paths whose epochs are managed here.
_EVIDENCE_PATH_IDS: tuple[str, ...] = (
    "exit_trailing_evidence",
    "entry_correction_v2",
)


def _current_commit() -> str:
    """Return the current source commit SHA, empty string if unavailable."""
    try:
        from aletheia.update.git_ops import current_commit as _git_commit
        sha = _git_commit()
        return sha if isinstance(sha, str) else ""
    except Exception:
        pass
    # Packaged runtime: read from release manifest via update state
    try:
        from aletheia.update.packaged import current_selection, _paths, _read_json
        selection = current_selection()
        current = selection.get("current")
        if isinstance(current, str) and current:
            manifest_path = _paths()["releases"] / current / "release-manifest.json"
            manifest = _read_json(manifest_path, required=False)
            sha = manifest.get("git_sha")
            return sha if isinstance(sha, str) and sha else ""
    except Exception:
        pass
    return ""


def _effective_values_for_path(path_id: str, *, commit_sha: str) -> dict[str, Any]:
    """Build the effective-values dict for one evidence-epoch policy epoch."""
    return {
        "path_id": path_id,
        "behavior_contract": _behavior_contract_version(path_id),
        "deploy_commit": commit_sha,
        "epoch_kind": "evidence_anchor",
    }


def _behavior_contract_version(path_id: str) -> str:
    try:
        from aletheia.learning.registry import get_learning_path
        path = get_learning_path(path_id)
        return path.behavior_contract_version or ""
    except (KeyError, Exception):
        return ""


def ensure_evidence_epochs(*, commit_sha: str | None = None) -> dict[str, str]:
    """Ensure one open policy epoch per evidence path, keyed to the deployed build.

    Returns a mapping ``{path_id: epoch_id}`` for the registered/existing epochs.
    Raises no exception on partial failure; each path is registered independently.

    The epoch *rotates* only when the deployed commit SHA changes, so the same
    build creates at most one epoch per path across multiple Gateway restarts.
    """
    from aletheia.learning.policy_epochs import build_policy_epoch, persist_policy_epoch

    sha = commit_sha if commit_sha is not None else _current_commit()
    result: dict[str, str] = {}

    for path_id in _EVIDENCE_PATH_IDS:
        try:
            epoch = build_policy_epoch(
                epoch_kind=f"evidence_anchor:{path_id}",
                effective_values=_effective_values_for_path(path_id, commit_sha=sha),
                affected_path_ids=(path_id,),
                safety_required=False,
            )
            epoch_id = persist_policy_epoch(epoch)
            result[path_id] = epoch_id
            logger.debug(
                "evidence epoch %s: path=%s commit=%s",
                epoch_id[:12] if epoch_id else "?",
                path_id,
                sha[:12] if sha else "none",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "evidence epoch registration failed for %s (non-fatal): %s",
                path_id,
                exc,
            )

    return result


def current_evidence_epochs() -> dict[str, dict[str, Any]]:
    """Return the currently open evidence epoch record for each evidence path.

    Returns ``{path_id: {"epoch_id": ..., "started_at": ..., "deploy_commit": ...}}``
    for each open epoch.  Missing or closed epochs are excluded.
    """
    try:
        from aletheia.storage.sqlite import get_db
        import json as _json
    except Exception:
        return {}

    result: dict[str, dict[str, Any]] = {}
    try:
        db = get_db()
        for path_id in _EVIDENCE_PATH_IDS:
            row = db.execute(
                "SELECT epoch_id, started_at, effective_values_json "
                "FROM learning_policy_epochs "
                "WHERE epoch_kind=? AND ended_at IS NULL "
                "ORDER BY started_at DESC, rowid DESC LIMIT 1",
                (f"evidence_anchor:{path_id}",),
            ).fetchone()
            if row:
                values = {}
                try:
                    values = _json.loads(row["effective_values_json"] or "{}")
                except Exception:
                    pass
                result[path_id] = {
                    "epoch_id": row["epoch_id"],
                    "started_at": row["started_at"],
                    "deploy_commit": values.get("deploy_commit", ""),
                    "behavior_contract": values.get("behavior_contract", ""),
                }
    except Exception as exc:  # noqa: BLE001
        logger.warning("evidence epoch query failed: %s", exc)

    return result


def evidence_epoch_summary() -> dict[str, Any]:
    """Human-readable summary for the control-plane status command."""
    sha = _current_commit()
    epochs = current_evidence_epochs()
    now = datetime.now(timezone.utc).isoformat()

    paths: list[dict[str, Any]] = []
    for path_id in _EVIDENCE_PATH_IDS:
        entry = epochs.get(path_id)
        if entry:
            epoch_commit = entry.get("deploy_commit", "")
            consistent = epoch_commit[:12] == sha[:12] if sha and epoch_commit else None
            paths.append({
                "path_id": path_id,
                "epoch_id": entry["epoch_id"],
                "started_at": entry["started_at"],
                "deploy_commit": epoch_commit,
                "sha_consistent": consistent,
                "status": "open",
            })
        else:
            paths.append({
                "path_id": path_id,
                "epoch_id": None,
                "started_at": None,
                "deploy_commit": None,
                "sha_consistent": None,
                "status": "not_registered",
            })

    return {
        "current_commit": sha,
        "checked_at": now,
        "paths": paths,
        "all_anchored": all(p["status"] == "open" for p in paths),
        "all_sha_consistent": all(
            p.get("sha_consistent") is True
            for p in paths
            if p["status"] == "open"
        ),
    }
