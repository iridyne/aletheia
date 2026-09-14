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

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("aletheia.learning.evidence_epoch")

_EVIDENCE_PATH_IDS: tuple[str, ...] = (
    "exit_trailing_evidence",
    "entry_correction_v2",
    "profit_loop_observability",
)
EXPECTED_EVIDENCE_PATH_IDS = frozenset(_EVIDENCE_PATH_IDS)


def _current_commit() -> str:
    """Return the running Gateway commit, empty when deployment is unavailable."""
    try:
        from aletheia.agent_runtime.build_info import get_boot_commit

        sha = get_boot_commit()
        if sha:
            return sha
    except Exception:
        pass
    try:
        from aletheia.storage.sqlite import get_db

        row = get_db().execute(
            "SELECT boot_commit FROM runtime_sessions "
            "WHERE ended_at IS NULL AND boot_commit IS NOT NULL "
            "ORDER BY started_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        if row and row["boot_commit"]:
            return str(row["boot_commit"])
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


def _profit_loop_effective_values() -> dict[str, Any]:
    from aletheia.agent_runtime.runtime_session import current_session
    from aletheia.analytics.profit_loop_counterfactual import (
        COUNTERFACTUAL_CONTRACT_VERSION,
        profit_loop_registry_hash,
    )

    session = current_session()
    if not session or session.get("durable") is not True:
        raise ValueError("durable current runtime session is unavailable")
    commit = str(session.get("boot_commit") or "").strip()
    fingerprint = str(session.get("behavior_fingerprint") or "").strip()
    if not commit or not fingerprint:
        raise ValueError("current runtime commit or behavior fingerprint is unavailable")
    return {
        "path_id": "profit_loop_observability",
        "behavior_contract": COUNTERFACTUAL_CONTRACT_VERSION,
        "deploy_commit": commit,
        "behavior_fingerprint": fingerprint,
        "registry_hash": profit_loop_registry_hash(),
        "duration_days": 30,
        "assignment_unit": "position_id",
        "eligibility": "opened_on_or_after_registration_closed_by_cutoff_complete_position_fact_with_frozen_hypothesis_and_1m_prefix",
        "stopping_rule": "single_locked_retrospective_evaluation_after_fixed_cutoff_no_optional_stopping",
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
    Registration fails closed when the running deployment SHA is unavailable.

    The epoch *rotates* only when the deployed commit SHA changes, so the same
    build creates at most one epoch per path across multiple Gateway restarts.
    """
    from aletheia.learning.policy_epochs import build_policy_epoch, persist_policy_epoch
    from aletheia.storage.repositories import transaction

    sha = commit_sha if commit_sha is not None else _current_commit()
    if not sha:
        raise ValueError("running deployment commit is unavailable")
    result: dict[str, str] = {}

    with transaction(immediate=True):
        for path_id in _EVIDENCE_PATH_IDS:
            try:
                values = (
                    _profit_loop_effective_values()
                    if path_id == "profit_loop_observability"
                    else _effective_values_for_path(path_id, commit_sha=sha)
                )
                epoch = build_policy_epoch(
                    epoch_kind=f"evidence_anchor:{path_id}",
                    effective_values=values,
                    affected_path_ids=(path_id,),
                    safety_required=False,
                )
                epoch_id = persist_policy_epoch(epoch)
                result[path_id] = epoch_id
                logger.debug(
                    "evidence epoch %s: path=%s commit=%s",
                    epoch_id[:12] if epoch_id else "?",
                    path_id,
                    str(values.get("deploy_commit") or "none")[:12],
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
                    values = json.loads(row["effective_values_json"] or "{}")
                except Exception:
                    pass
                result[path_id] = {
                    "epoch_id": row["epoch_id"],
                    "started_at": row["started_at"],
                    "deploy_commit": values.get("deploy_commit", ""),
                    "behavior_contract": values.get("behavior_contract", ""),
                    "behavior_fingerprint": values.get("behavior_fingerprint"),
                    "registry_hash": values.get("registry_hash"),
                    "duration_days": values.get("duration_days"),
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
            consistent = epoch_commit == sha if sha and epoch_commit else None
            path = {
                "path_id": path_id,
                "epoch_id": entry["epoch_id"],
                "started_at": entry["started_at"],
                "deploy_commit": epoch_commit,
                "sha_consistent": consistent,
                "status": "open",
            }
            if path_id == "profit_loop_observability":
                try:
                    started_at = datetime.fromisoformat(
                        str(entry["started_at"]).replace("Z", "+00:00")
                    )
                    if started_at.tzinfo is None:
                        raise ValueError
                except (TypeError, ValueError):
                    path.update({"status": "malformed", "sha_consistent": False, "cutoff_at": None})
                else:
                    path["cutoff_at"] = (started_at + timedelta(days=30)).isoformat()
                path.update(
                    {
                        "behavior_fingerprint": entry.get("behavior_fingerprint"),
                        "registry_hash": entry.get("registry_hash"),
                    }
                )
            paths.append(path)
        else:
            path = {
                "path_id": path_id,
                "epoch_id": None,
                "started_at": None,
                "deploy_commit": None,
                "sha_consistent": None,
                "status": "not_registered",
            }
            if path_id == "profit_loop_observability":
                path.update(
                    {
                        "behavior_fingerprint": None,
                        "registry_hash": None,
                        "cutoff_at": None,
                    }
                )
            paths.append(path)

    return {
        "current_commit": sha,
        "checked_at": now,
        "paths": paths,
        "all_anchored": all(p["status"] == "open" for p in paths),
        "all_sha_consistent": (
            all(p["status"] == "open" for p in paths)
            and all(p.get("sha_consistent") is True for p in paths)
        ),
    }
