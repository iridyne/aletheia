"""Minimal machine control plane: `aletheia control status`.

Surfaces deployment/runtime identity, UFC and profit-loop evidence anchors,
Position Path recovery, pause ownership, authority, and window continuity in
one command — so issues like #470/#657/#985 never need manually maintained
dynamic values in governance documents.

All reads are offline-safe: the command works without a running Gateway.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer

control_app = typer.Typer(no_args_is_help=True, help="Machine governance control plane.")

CONTROL_STATUS_SCHEMA_VERSION = "control_status/v1"
_UFC_PATH_ID = "universe_family_calibration"
_PROFIT_LOOP_PATH_ID = "profit_loop_observability"


def _disk_commit() -> str:
    """Return on-disk source/release SHA; empty on error."""
    try:
        from aletheia.update.git_ops import current_commit as _git_sha, is_repo
        if is_repo():
            return _git_sha() or ""
    except Exception:
        pass
    try:
        from aletheia.update.packaged import current_selection, _paths, _read_json
        sel = current_selection()
        current = sel.get("current")
        if isinstance(current, str) and current:
            mf = _read_json(_paths()["releases"] / current / "release-manifest.json", required=False)
            sha = mf.get("git_sha")
            return sha if isinstance(sha, str) and sha else ""
    except Exception:
        pass
    return ""


def _deployed_commit() -> str:
    """Return the running Gateway boot SHA; empty when unavailable."""
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
        return str(row["boot_commit"]) if row and row["boot_commit"] else ""
    except Exception:
        return ""


def _cached_master_commit() -> str:
    """Return the cached origin/master tracking SHA; empty if unavailable."""
    try:
        from aletheia.update.git_ops import remote_commit
        return remote_commit("master") or ""
    except Exception:
        return ""


def _cfg_commit() -> str:
    """Return the last recorded commit SHA from config."""
    try:
        from aletheia.config.loader import load_config
        return load_config(persist_migrations=False).update.current_commit or ""
    except Exception:
        return ""


def _auto_deploy_commit() -> str:
    """Return the last successfully activated stable Tag SHA."""
    try:
        from aletheia.update.auto import status

        last = status().get("last_successful") or {}
        sha = last.get("sha") if isinstance(last, dict) else None
        return sha if isinstance(sha, str) else ""
    except Exception:
        return ""


def _runtime_session_status() -> dict[str, Any]:
    """Project the active Runtime Session without manufacturing identity."""

    try:
        from aletheia.agent_runtime.runtime_session import current_session

        session = current_session()
        source = "in_process"
        if session is None:
            from aletheia.storage.sqlite import get_db

            row = get_db().execute(
                "SELECT * FROM runtime_sessions WHERE ended_at IS NULL "
                "ORDER BY started_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
            session = dict(row) if row is not None else None
            source = "durable_db"
        if not session:
            return {
                "available": False,
                "unavailable_reason": "no_active_runtime_session",
                "session_id": None,
                "runtime_id": None,
                "boot_sha": None,
                "behavior_fingerprint": None,
            }
        return {
            "available": True,
            "unavailable_reason": None,
            "source": source,
            "session_id": session.get("session_id"),
            "runtime_id": session.get("runtime_id"),
            "boot_sha": session.get("boot_commit"),
            "behavior_fingerprint": session.get("behavior_fingerprint"),
            "schema_version": session.get("schema_version"),
            "execution_mode": session.get("execution_mode"),
            "started_at": session.get("started_at"),
            "durable": bool(session.get("durable", True)),
        }
    except Exception as exc:
        return {
            "available": False,
            "unavailable_reason": "runtime_session_read_failed",
            "error": type(exc).__name__,
            "session_id": None,
            "runtime_id": None,
            "boot_sha": None,
            "behavior_fingerprint": None,
        }


def _data_root_identity() -> dict[str, Any]:
    """Expose only a stable, non-secret identity for the selected data root."""

    try:
        from aletheia.config.loader import get_data_dir, is_preview_mode

        root = Path(get_data_dir()).expanduser().resolve()
        digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()
        return {
            "available": True,
            "identity": f"sha256:{digest}",
            "mode": "preview" if is_preview_mode() else "primary",
            "unavailable_reason": None,
        }
    except Exception as exc:
        return {
            "available": False,
            "identity": None,
            "mode": None,
            "unavailable_reason": "data_root_read_failed",
            "error": type(exc).__name__,
        }


def _iso_from_epoch(value: object) -> str | None:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _observer_health_snapshot(session_start: str | None) -> dict[str, Any]:
    """Read Observer run completion timestamps; Opportunity creation is not a heartbeat."""

    try:
        from aletheia.storage.sqlite import get_db

        db = get_db()
        params: tuple[object, ...] = (session_start,) if session_start else ()
        where = " AND started_at >= ?" if session_start else ""
        latest = db.execute(
            "SELECT status, started_at, ended_at FROM agent_runs "
            "WHERE agent_name='market_observer'" + where
            + " ORDER BY started_at DESC, rowid DESC LIMIT 1",
            params,
        ).fetchone()
        successful = db.execute(
            "SELECT started_at, ended_at FROM agent_runs "
            "WHERE agent_name='market_observer'" + where
            + " AND status='completed' AND ended_at IS NOT NULL "
            "ORDER BY ended_at DESC, rowid DESC LIMIT 1",
            params,
        ).fetchone()
        return {
            "available": True,
            "latest_status": latest["status"] if latest else None,
            "latest_started_at": latest["started_at"] if latest else None,
            "latest_ended_at": latest["ended_at"] if latest else None,
            "last_successful_at": successful["ended_at"] if successful else None,
            "unavailable_reason": None,
        }
    except Exception as exc:
        return {
            "available": False,
            "latest_status": None,
            "latest_started_at": None,
            "latest_ended_at": None,
            "last_successful_at": None,
            "unavailable_reason": "observer_health_read_failed",
            "error": type(exc).__name__,
        }


def _connectivity_snapshot() -> dict[str, Any]:
    """Project cached connectivity only; status must never probe an exchange."""

    try:
        from aletheia.agent_runtime.connectivity import get_status

        state = get_status()
        probe = state.get("last_probe") or {}
        reachable = probe.get("reachable") if probe else None
        return {
            "available": bool(probe),
            "reachable": reachable,
            "state": (
                "exchange_restored" if reachable is True
                else "exchange_unreachable" if reachable is False
                else "unknown"
            ),
            "exchange": probe.get("exchange"),
            "last_probe_at": _iso_from_epoch(probe.get("ts")),
            "consecutive_failures": state.get("consecutive_failures"),
            "consecutive_successes": state.get("consecutive_successes"),
            "unavailable_reason": None if probe else "no_cached_exchange_probe",
        }
    except Exception as exc:
        return {
            "available": False,
            "reachable": None,
            "state": "unknown",
            "exchange": None,
            "last_probe_at": None,
            "consecutive_failures": None,
            "consecutive_successes": None,
            "unavailable_reason": "connectivity_read_failed",
            "error": type(exc).__name__,
        }


def _open_experiments() -> dict[str, Any]:
    try:
        from aletheia.learning.ledger import list_open_experiments

        return {"available": True, "items": list_open_experiments()}
    except Exception as exc:
        return {"available": False, "items": [], "error": type(exc).__name__}


def _open_policy_epochs(limit: int = 10) -> list[dict[str, Any]] | dict[str, Any]:
    try:
        from aletheia.storage.sqlite import get_db
        rows = get_db().execute(
            "SELECT epoch_id, epoch_kind, effective_values_json, started_at "
            "FROM learning_policy_epochs WHERE ended_at IS NULL "
            "ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    except Exception as exc:
        return {
            "available": False,
            "items": [],
            "error": type(exc).__name__,
        }


def _evidence_epochs() -> dict[str, Any]:
    try:
        from aletheia.learning.evidence_epoch import evidence_epoch_summary
        return evidence_epoch_summary()
    except Exception:
        return {"error": "unavailable"}


def _learning_authority() -> dict[str, Any]:
    try:
        from aletheia.learning.control import (
            learning_influence_gate_reason,
            global_learning_refreeze_reason,
        )
        gate_reason = learning_influence_gate_reason()
        refreeze_reason = global_learning_refreeze_reason()
        return {
            "influence_gate": gate_reason or "open",
            "global_refreeze": refreeze_reason or "none",
            "influence_enabled": gate_reason is None,
        }
    except Exception:
        return {"error": "unavailable"}


def _runtime_continuity() -> dict[str, Any]:
    try:
        from aletheia.config.loader import load_config
        from aletheia.agent_runtime import scheduler
        from aletheia.agent_runtime.pause_ownership import (
            active_pause_blocker_names,
            active_pause_owners,
            pause_blocked_reason,
        )
        cfg = load_config(persist_migrations=False)
        owners = active_pause_owners(cfg)
        blockers = active_pause_blocker_names(cfg)
        session = _runtime_session_status()
        scheduler_state = scheduler.status_snapshot()
        connectivity = _connectivity_snapshot()
        observer = _observer_health_snapshot(session.get("started_at"))
        gateway = _gateway_running()
        jobs_resumed = (
            bool(
                scheduler_state.get("running")
                and not blockers
                and not cfg.runtime.paused
            )
            if scheduler_state.get("state") not in {None, "unknown"}
            else None
        )
        return {
            "enabled": cfg.runtime.runtime_continuity_enabled,
            "paused": cfg.runtime.paused,
            "pause_owners": owners,
            "pause_blockers": list(blockers),
            "resume_blocked_reason": pause_blocked_reason(cfg),
            "scheduler_resume_authority": {
                "allowed": not blockers,
                "blocked_by": list(blockers),
                "dependency_recovery_is_resume": False,
            },
            "kill_switch_active": cfg.runtime.kill_switch_active,
            "demo_brake_active": cfg.runtime.demo_brake_active,
            "live_brake_active": cfg.runtime.live_brake_active,
            "runtime_session": session,
            "runtime_session_id": session.get("session_id"),
            "runtime_id": session.get("runtime_id"),
            "boot_sha": session.get("boot_sha"),
            "data_root": _data_root_identity(),
            "gateway": {
                "running": gateway,
                "unavailable_reason": "gateway_status_read_failed" if gateway is None else None,
            },
            "scheduler": scheduler_state,
            "exchange": connectivity,
            "behavioral_jobs_resumed": jobs_resumed,
            "last_healthy_at": {
                "exchange": connectivity.get("last_probe_at")
                if connectivity.get("reachable") is True else None,
                "observer": observer.get("last_successful_at"),
                "scheduler": scheduler_state.get("last_tick")
                if scheduler_state.get("running") else None,
            },
            "health": {
                "availability": "available" if observer.get("available") else "partial",
                "observer": observer,
                "unavailable_reason": observer.get("unavailable_reason"),
            },
        }
    except Exception:
        return {
            "availability": "unavailable",
            "unavailable_reason": "runtime_continuity_read_failed",
            "error": "unavailable",
        }


def _gateway_running() -> bool | None:
    try:
        from aletheia.gateway.pidfile import is_running
        return is_running()
    except Exception:
        return None


def _position_path_observer_status() -> dict[str, Any]:
    """Return durable Position-path recovery facts without inventing runtime state."""

    try:
        from aletheia.portfolio.position_path_observer import (
            build_position_path_observer_status,
        )

        return {"available": True, **build_position_path_observer_status()}
    except Exception as exc:
        return {
            "available": False,
            "error": type(exc).__name__,
            "oldest_missing_bucket": None,
            "oldest_deferred_bucket": None,
            "deferred_bucket_count": None,
            "observer_lag": None,
            "last_24h": None,
            "recent_counts": None,
            "ordered_path_coverage": None,
            "unavailable_reason": "position_path_observer_read_failed",
        }


def _ufc_status() -> dict[str, Any]:
    """Expose the one UFC measured path without merging it with profit-loop state."""

    base = {
        "schema_version": "ufc_control_status/v1",
        "path_id": _UFC_PATH_ID,
        "status": "not_registered",
        "availability": "available",
        "unavailable_reason": "no_active_experiment",
        "experiment": None,
        "identity": None,
        "assignment": None,
        "coverage": None,
        "fixed_look": None,
        "verdict": None,
        "integrity": None,
        "evaluator": None,
        "freshness": None,
        "authority": None,
        "projection_schema_version": None,
    }
    try:
        from aletheia.learning.read_models import build_learning_contribution_projection

        projection = build_learning_contribution_projection(limit=20)
    except Exception as exc:
        return {
            **base,
            "status": "unavailable",
            "availability": "unavailable",
            "unavailable_reason": "learning_projection_read_failed",
            "error": type(exc).__name__,
        }

    if not isinstance(projection, dict):
        return {
            **base,
            "status": "unavailable",
            "availability": "unavailable",
            "unavailable_reason": "learning_projection_malformed",
        }
    base["projection_schema_version"] = projection.get("schema_version")
    raw_items = projection.get("items", [])
    raw_path_states = projection.get("path_states", [])
    if not isinstance(raw_items, list) or not isinstance(raw_path_states, list):
        return {
            **base,
            "status": "unavailable",
            "availability": "unavailable",
            "unavailable_reason": "learning_projection_malformed",
        }
    items = [
        item for item in raw_items
        if isinstance(item, dict) and item.get("path_id") == _UFC_PATH_ID
    ]
    if len(items) > 1:
        return {
            **base,
            "status": "unavailable",
            "availability": "unavailable",
            "unavailable_reason": "multiple_active_ufc_paths",
        }
    if not items:
        state = next(
            (
                state for state in raw_path_states
                if isinstance(state, dict) and state.get("path_id") == _UFC_PATH_ID
            ),
            {},
        )
        if state.get("availability") == "unavailable":
            base.update(
                {
                    "status": "unavailable",
                    "availability": "unavailable",
                    "unavailable_reason": state.get("unavailable_reason")
                    or "ufc_path_unavailable",
                }
            )
        return base

    item = items[0]

    def _mapping(value: object) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    measurement = _mapping(item.get("measurement"))
    identity = _mapping(item.get("current_identity"))
    inference = _mapping(item.get("inference"))
    samples = _mapping(item.get("samples"))
    coverage = dict(_mapping(measurement.get("coverage")))
    coverage["current_consecutive_eligible_utc_days"] = coverage.get(
        "current_consecutive_market_days"
    )
    lifecycle = _mapping(item.get("lifecycle"))
    item_availability = item.get("availability") or "unavailable"
    if item_availability not in {"available", "partial"}:
        status = "unavailable"
    elif any(
        not identity.get(field)
        for field in (
            "policy_epoch", "target_horizon", "outcome_contract_version",
            "metric_version", "statistical_unit", "behavior_contract_hash", "code_commit",
        )
    ):
        status = "unavailable"
    elif measurement.get("evaluated_at") and measurement.get("state"):
        status = "evaluated"
    else:
        status = "collecting"
    operations = _mapping(projection.get("operations"))
    evaluator = operations.get("evaluator")
    return {
        **base,
        "status": status,
        "availability": item_availability,
        "unavailable_reason": (
            "ufc_projection_partial" if item_availability == "partial"
            else "ufc_identity_unavailable" if status == "unavailable"
            else None
        ),
        "experiment": {
            "experiment_id": item.get("experiment_id"),
            "hypothesis_id": item.get("hypothesis_id"),
            "stage": item.get("stage"),
            "status": item.get("experiment_status"),
        },
        "identity": identity,
        "assignment": {
            "horizon": identity.get("target_horizon"),
            "statistical_unit": identity.get("statistical_unit"),
            "allocation_pct": item.get("allocation_pct"),
            "complete_treatment_units": samples.get("treatment_assignment_units"),
            "complete_control_units": samples.get("control_assignment_units"),
            "complete_units": samples.get("complete_assignment_units"),
            "actual_intervention_units": samples.get(
                "actual_intervention_treatment_units"
            ),
            "exposed_actual_intervention_units": samples.get(
                "exposed_actual_intervention_treatment_units"
            ),
            "required_per_arm": samples.get("required_per_arm"),
        },
        "coverage": coverage,
        "fixed_look": {
            **inference,
            "measurement_state": measurement.get("state"),
            "evaluated_at": measurement.get("evaluated_at"),
        },
        "verdict": {
            key: measurement.get(key)
            for key in (
                "state", "reason", "baseline", "observed", "delta",
                "confidence_low", "confidence_high", "evaluated_at",
                "window_start", "window_end", "inference_role", "authority_eligible",
            )
        },
        "integrity": item.get("integrity"),
        "evaluator": evaluator,
        "freshness": item.get("data_freshness"),
        "authority": {
            "eligible": lifecycle.get("authority_enabled"),
            "block_reason": lifecycle.get("authority_block_reason"),
            "mutation_available": lifecycle.get("mutation_available"),
        },
        # Keep the already versioned read model available for exact consumers;
        # the surrounding fields are the stable control-status projection.
        "projection": item,
    }


def _profit_loop_coverage() -> dict[str, Any]:
    """Read bounded candidate/Position/path/economics counts without evaluation."""

    try:
        from aletheia.analytics.candidate_facts import build_candidate_facts
        from aletheia.storage.sqlite import readonly_db

        with readonly_db() as db:
            candidates = build_candidate_facts(horizon="24h", db=db)
            table_exists = {
                table: db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                is not None
                for table in ("positions", "position_path_observations")
            }
            if not all(table_exists.values()):
                missing = next(table for table, exists in table_exists.items() if not exists)
                raise RuntimeError(f"missing_{missing}_table")
            position_counts = db.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) AS closed, "
                "SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) AS open, "
                "SUM(CASE WHEN status='closed' AND realized_pnl IS NOT NULL THEN 1 ELSE 0 END) AS economics "
                "FROM positions WHERE status IN ('open','closed')"
            ).fetchone()
            path_counts = db.execute(
                "SELECT COUNT(*) AS observations, COUNT(DISTINCT position_id) AS positions, "
                "SUM(CASE WHEN evidence_status='available' THEN 1 ELSE 0 END) AS available, "
                "SUM(CASE WHEN evidence_status='unavailable' THEN 1 ELSE 0 END) AS unavailable "
                "FROM position_path_observations"
            ).fetchone()
        position_counts = dict(position_counts or {})
        path_counts = dict(path_counts or {})
        candidate_count = int(candidates.get("candidate_count") or 0)
        settled_count = int(candidates.get("settled_outcome_count") or 0)
        return {
            "availability": "available",
            "unavailable_reason": None,
            "candidate": {
                "availability": "available",
                "units": candidate_count,
                "settled_outcome_units": settled_count,
                "outcome_coverage": candidates.get("outcome_coverage"),
                "unavailable_units": candidates.get("unavailable_count", 0),
            },
            "position": {
                "availability": "available",
                "units": int(position_counts.get("total") or 0),
                "open_units": int(position_counts.get("open") or 0),
                "closed_units": int(position_counts.get("closed") or 0),
            },
            "path": {
                "availability": "available",
                "observations": int(path_counts.get("observations") or 0),
                "positions": int(path_counts.get("positions") or 0),
                "available_observations": int(path_counts.get("available") or 0),
                "unavailable_observations": int(path_counts.get("unavailable") or 0),
            },
            "economics": {
                "availability": "available",
                "closed_positions": int(position_counts.get("closed") or 0),
                "cost_aware_closed_positions": int(position_counts.get("economics") or 0),
            },
        }
    except Exception as exc:
        return {
            "availability": "unavailable",
            "unavailable_reason": "profit_loop_coverage_read_failed",
            "error": type(exc).__name__,
            "candidate": None,
            "position": None,
            "path": None,
            "economics": None,
        }


def _profit_loop_status(evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    """Project the registered profit-loop epoch; never compile or select a winner."""

    base = {
        "schema_version": "profit_loop_control_status/v1",
        "path_id": _PROFIT_LOOP_PATH_ID,
        "status": "not_registered",
        "availability": "available",
        "unavailable_reason": None,
        "registration": None,
        "identity": None,
        "coverage": None,
        "compiler": None,
        "artifact": None,
        "fixed_result_hash": None,
    }
    evidence = evidence if isinstance(evidence, dict) else _evidence_epochs()
    if "error" in evidence:
        return {
            **base,
            "status": "unavailable",
            "availability": "unavailable",
            "unavailable_reason": "evidence_epoch_read_failed",
        }
    raw_paths = evidence.get("paths", [])
    if not isinstance(raw_paths, list):
        return {
            **base,
            "status": "unavailable",
            "availability": "unavailable",
            "unavailable_reason": "malformed_evidence_epoch_paths",
        }
    entry = next(
        (
            path for path in raw_paths
            if isinstance(path, dict) and path.get("path_id") == _PROFIT_LOOP_PATH_ID
        ),
        None,
    )
    if entry is None or entry.get("status") == "not_registered":
        base["unavailable_reason"] = "profit_loop_not_registered"
        return base
    if entry.get("status") != "open":
        return {
            **base,
            "status": "unavailable",
            "availability": "unavailable",
            "unavailable_reason": "malformed_profit_loop_epoch",
        }
    required_identity = (
        "epoch_id", "started_at", "deploy_commit", "behavior_fingerprint", "registry_hash"
    )
    if any(not entry.get(field) for field in required_identity):
        return {
            **base,
            "status": "unavailable",
            "availability": "unavailable",
            "unavailable_reason": "malformed_profit_loop_identity",
        }
    cutoff_at = entry.get("cutoff_at")
    try:
        cutoff = datetime.fromisoformat(str(cutoff_at).replace("Z", "+00:00"))
        if cutoff.tzinfo is None:
            raise ValueError
    except (TypeError, ValueError):
        return {
            **base,
            "status": "unavailable",
            "availability": "unavailable",
            "unavailable_reason": "malformed_profit_loop_cutoff",
        }
    now = datetime.now(timezone.utc)
    lifecycle_status = "collecting" if now < cutoff else "cutoff_reached"
    coverage = _profit_loop_coverage()
    if not isinstance(coverage, dict):
        coverage = {
            "availability": "unavailable",
            "unavailable_reason": "malformed_profit_loop_coverage",
        }
    try:
        from aletheia.analytics.profit_gradient_evidence import (
            PROFIT_GRADIENT_EVIDENCE_CONTRACT_VERSION,
        )
        import aletheia.analytics.profit_gradient_evidence as compiler_module

        compiler_source_hash = hashlib.sha256(
            Path(compiler_module.__file__).read_bytes()
        ).hexdigest()
        compiler = {
            "contract_version": PROFIT_GRADIENT_EVIDENCE_CONTRACT_VERSION,
            "source_hash": f"sha256:{compiler_source_hash}",
            "available": True,
        }
    except Exception as exc:
        compiler = {
            "contract_version": None,
            "source_hash": None,
            "available": False,
            "unavailable_reason": "compiler_identity_unavailable",
            "error": type(exc).__name__,
        }
    return {
        **base,
        "status": lifecycle_status,
        "availability": "partial" if coverage.get("availability") != "available" else "available",
        "unavailable_reason": coverage.get("unavailable_reason"),
        "registration": {
            "epoch_id": entry.get("epoch_id"),
            "registered_at": entry.get("started_at"),
            "cutoff_at": cutoff.isoformat(),
            "duration_days": 30,
            "assignment_unit": "position_id",
            "stopping_rule": "single_locked_retrospective_evaluation_after_fixed_cutoff_no_optional_stopping",
        },
        "identity": {
            "runtime_boot_sha": entry.get("deploy_commit"),
            "behavior_fingerprint": entry.get("behavior_fingerprint"),
            "registry_hash": entry.get("registry_hash"),
            "behavior_contract": "profit_loop_counterfactual/v1",
        },
        "coverage": coverage,
        "compiler": compiler,
        "artifact": {
            "status": "unavailable",
            "contract_version": compiler.get("contract_version"),
            "content_hash": None,
            "unavailable_reason": "artifact_not_persisted_by_contract",
        },
        # Result hashes and layer winners are intentionally absent until an
        # operator supplies a cutoff-verified artifact to the inspect CLI.
        "fixed_result_hash": None,
    }


def build_control_status() -> dict[str, Any]:
    """Collect all governance-relevant state into one serializable dict."""
    deployed = _deployed_commit()
    disk = _disk_commit()
    master = _cached_master_commit()
    cfg_sha = _cfg_commit()
    auto_sha = _auto_deploy_commit()
    if deployed and auto_sha == deployed:
        recorded_sha, recorded_source = auto_sha, "auto_release_tag"
    elif deployed and cfg_sha == deployed:
        recorded_sha, recorded_source = cfg_sha, "config"
    elif auto_sha:
        recorded_sha, recorded_source = auto_sha, "auto_release_tag"
    else:
        recorded_sha, recorded_source = cfg_sha, "config" if cfg_sha else "not_available"

    sha_section = {
        "deployed_head": deployed,
        "deployed_head_short": deployed[:12] if deployed else "not_available",
        "disk_head": disk,
        "disk_head_short": disk[:12] if disk else "not_available",
        "cached_master_head": master,
        "cached_master_head_short": master[:12] if master else "not_available",
        "config_recorded": cfg_sha,
        "config_recorded_short": cfg_sha[:12] if cfg_sha else "not_available",
        "auto_recorded": auto_sha,
        "auto_recorded_short": auto_sha[:12] if auto_sha else "not_available",
        "runtime_recorded": recorded_sha,
        "runtime_recorded_short": recorded_sha[:12] if recorded_sha else "not_available",
        "runtime_recorded_source": recorded_source,
        "deployed_matches_recorded": (
            bool(deployed and recorded_sha and deployed[:12] == recorded_sha[:12])
            if deployed and recorded_sha else None
        ),
        "deployed_matches_config": (
            bool(deployed and cfg_sha and deployed[:12] == cfg_sha[:12])
            if deployed and cfg_sha else None
        ),
        "deployed_matches_master": (
            bool(deployed and master and deployed[:12] == master[:12])
            if deployed and master else None
        ),
        "disk_matches_deployed": (
            bool(disk and deployed and disk[:12] == deployed[:12])
            if disk and deployed else None
        ),
    }

    # Active hypotheses / open experiments
    experiment_result = _open_experiments()
    if not isinstance(experiment_result, dict):
        experiment_result = {
            "available": False,
            "items": [],
            "error": "malformed_experiment_projection",
        }
    if not isinstance(experiment_result.get("items"), list):
        experiment_result = {
            **experiment_result,
            "available": False,
            "items": [],
            "error": "malformed_experiment_projection",
        }
    active_hypotheses: list[dict[str, Any]] = []
    for exp in experiment_result["items"]:
        if not isinstance(exp, dict):
            experiment_result["available"] = False
            experiment_result["error"] = "malformed_experiment_row"
            continue
        active_hypotheses.append({
            "experiment_id": exp.get("experiment_id"),
            "hypothesis_id": exp.get("hypothesis_id"),
            "stage": exp.get("stage"),
            "status": exp.get("status"),
            "started_at": exp.get("started_at"),
            "policy_epoch": exp.get("concurrent_policy_epoch"),
        })

    # Policy epochs (most recent open)
    raw_epochs = _open_policy_epochs(limit=8)
    if isinstance(raw_epochs, dict):
        policy_epochs_available = bool(raw_epochs.get("available"))
        policy_epochs_error = raw_epochs.get("error")
        epoch_rows = raw_epochs.get("items")
        if not isinstance(epoch_rows, list):
            policy_epochs_available = False
            policy_epochs_error = "malformed_policy_epoch_projection"
            epoch_rows = []
    elif isinstance(raw_epochs, list):
        policy_epochs_available = True
        policy_epochs_error = None
        epoch_rows = raw_epochs
    else:
        policy_epochs_available = False
        policy_epochs_error = "malformed_policy_epoch_projection"
        epoch_rows = []
    open_epochs: list[dict[str, Any]] = []
    for row in epoch_rows:
        if not isinstance(row, dict):
            policy_epochs_available = False
            policy_epochs_error = "malformed_policy_epoch_row"
            continue
        open_epochs.append({
            "epoch_id": row.get("epoch_id"),
            "epoch_kind": row.get("epoch_kind"),
            "started_at": row.get("started_at"),
        })

    # Evidence epochs (trailing + entry)
    evidence = _evidence_epochs()
    if not isinstance(evidence, dict):
        evidence = {"error": "malformed_evidence_projection"}
    authority = _learning_authority()
    if not isinstance(authority, dict):
        authority = {"error": "malformed_authority_projection"}
    continuity = _runtime_continuity()
    if not isinstance(continuity, dict):
        continuity = {
            "availability": "unavailable",
            "unavailable_reason": "malformed_runtime_projection",
        }
    gateway = _gateway_running()
    position_path = _position_path_observer_status()
    if not isinstance(position_path, dict):
        position_path = {
            "available": False,
            "unavailable_reason": "malformed_position_path_projection",
        }
    ufc = _ufc_status()
    if not isinstance(ufc, dict):
        ufc = {
            "status": "unavailable",
            "availability": "unavailable",
            "unavailable_reason": "malformed_ufc_projection",
        }
    profit_loop = _profit_loop_status(evidence)
    if not isinstance(profit_loop, dict):
        profit_loop = {
            "status": "unavailable",
            "availability": "unavailable",
            "unavailable_reason": "malformed_profit_loop_projection",
        }

    return {
        "schema_version": CONTROL_STATUS_SCHEMA_VERSION,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "sha": sha_section,
        "deployment": {
            "running_sha": deployed or None,
            "disk_sha": disk or None,
            "cached_master_sha": master or None,
            "recorded_sha": recorded_sha or None,
            "recorded_source": recorded_source,
            "running_matches_recorded": sha_section["deployed_matches_recorded"],
            "running_matches_master": sha_section["deployed_matches_master"],
        },
        "gateway_running": gateway,
        "runtime": continuity,
        "safety": {
            "kill_switch_active": continuity.get("kill_switch_active"),
            "pause_owners": continuity.get("pause_owners"),
            "pause_blockers": continuity.get("pause_blockers"),
            "blocked_reason": continuity.get("resume_blocked_reason"),
            "exchange_restored": (
                True
                if (continuity.get("exchange") or {}).get("state") == "exchange_restored"
                else False
                if (continuity.get("exchange") or {}).get("state") == "exchange_unreachable"
                else None
            ),
            "behavioral_jobs_resumed": continuity.get("behavioral_jobs_resumed"),
        },
        "position_path_observer": position_path,
        "ufc": ufc,
        "profit_loop": profit_loop,
        "authority": authority,
        "active_experiments": active_hypotheses,
        "active_experiments_available": experiment_result["available"],
        "active_experiments_error": experiment_result.get("error"),
        "open_policy_epochs": open_epochs,
        "open_policy_epochs_available": policy_epochs_available,
        "open_policy_epochs_error": policy_epochs_error,
        "evidence_epochs": evidence,
        "window_continuity": {
            "evidence_anchored": evidence.get("all_anchored", False),
            "evidence_sha_consistent": evidence.get("all_sha_consistent", False),
            "deployed_head_consistent": sha_section["deployed_matches_recorded"],
        },
    }


@control_app.command("status")
def status(
    json_output: bool = typer.Option(False, "--json", help="Print raw JSON."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Include open policy epochs."),
):
    """Show the machine governance control-plane snapshot.

    Reports the versioned machine snapshot used by Agents and Issues. ``--json``
    is authoritative; human output is a concise rendering of the same fields.
    """
    data = build_control_status()

    if json_output:
        typer.echo(json.dumps(data, indent=2, ensure_ascii=False))
        return

    sha = data["sha"]
    continuity = data["window_continuity"]
    authority = data.get("authority", {})
    evidence = data.get("evidence_epochs", {})

    # ─── Header ────────────────────────────────────────────────────────────
    typer.echo("╔═ Aletheia Control Status ════════════════════════════╗")
    typer.echo(f"  As of     : {data['as_of'][:19]}Z")
    typer.echo(f"  Gateway   : {'running' if data.get('gateway_running') else 'stopped' if data.get('gateway_running') is False else 'unknown'}")
    paused = data.get("runtime", {}).get("paused")
    typer.echo(f"  Paused    : {paused}")
    runtime = data.get("runtime", {})
    blockers = runtime.get("pause_blockers") or []
    typer.echo(f"  Resume    : {'blocked by ' + ', '.join(blockers) if blockers else 'permitted'}")
    owners = runtime.get("pause_owners") or []
    if owners:
        for owner in owners:
            typer.echo(
                f"  Owner     : {owner.get('owner')} ({owner.get('reason')}) "
                f"generation={owner.get('generation') or 'unknown'} "
                f"age={owner.get('age_seconds') if owner.get('age_seconds') is not None else 'unknown'}s"
            )
    typer.echo("╠═ SHA ════════════════════════════════════════════════╣")
    typer.echo(f"  Running   : {sha['deployed_head_short']}{'  ✓ matches recorded deployment' if sha.get('deployed_matches_recorded') else '  ⚠ recorded deployment disagrees' if sha.get('deployed_matches_recorded') is False else ''}")
    typer.echo(f"  Disk      : {sha['disk_head_short']}{'  (same as running)' if sha.get('disk_matches_deployed') else '  (different from running)' if sha.get('disk_matches_deployed') is False else ''}")
    typer.echo(f"  Cached master: {sha['cached_master_head_short']}{'  (same as running)' if sha.get('deployed_matches_master') else '  (different)' if sha.get('deployed_matches_master') is False else ''}")
    typer.echo(f"  Config    : {sha['config_recorded_short']}")
    typer.echo(
        f"  Recorded  : {sha['runtime_recorded_short']} "
        f"({sha['runtime_recorded_source']})"
    )

    # ─── Position path observer ────────────────────────────────────────────
    typer.echo("╠═ Position Path Observer ══════════════════════════════╣")
    position_path = data.get("position_path_observer", {})
    if not position_path.get("available"):
        typer.echo("  (not available — durable observer state query failed)")
    else:
        oldest = position_path.get("oldest_missing_bucket") or {}
        typer.echo(
            "  Oldest missing: "
            + (
                f"{oldest.get('position_id')} @ {oldest.get('bucket_open_at')}"
                if oldest
                else "none"
            )
        )
        typer.echo(f"  Deferred buckets: {position_path.get('deferred_bucket_count')}")
        lag = position_path.get("observer_lag") or {}
        typer.echo(
            f"  Observer lag: {lag.get('seconds', 'not_available')}s "
            f"({lag.get('buckets', 'not_available')} buckets)"
        )
        counts = position_path.get("last_24h") or {}
        typer.echo(
            "  Last 24h: "
            f"available={counts.get('available', 'not_available')} "
            f"unavailable={counts.get('unavailable', 'not_available')} "
            f"skipped={counts.get('skipped', 'not_available')}"
        )

    # ─── Measured UFC path ──────────────────────────────────────────────────
    typer.echo("╠═ UFC (measured Learning path) ════════════════════════╣")
    ufc = data.get("ufc", {})
    typer.echo(
        f"  Status: {ufc.get('status', 'unknown')}  "
        f"availability={ufc.get('availability', 'unknown')}"
    )
    assignment = ufc.get("assignment") or {}
    typer.echo(
        "  Units: treatment="
        f"{assignment.get('complete_treatment_units', 'not_available')} "
        "control="
        f"{assignment.get('complete_control_units', 'not_available')} "
        "actual_intervention="
        f"{assignment.get('actual_intervention_units', 'not_available')}"
    )
    coverage = ufc.get("coverage") or {}
    typer.echo(
        "  Eligible UTC days: "
        f"{coverage.get('current_consecutive_eligible_utc_days', 'not_available')}"
    )

    # ─── Profit-loop evidence anchor ────────────────────────────────────────
    typer.echo("╠═ Profit Loop (separate evidence anchor) ═════════════╣")
    profit_loop = data.get("profit_loop", {})
    registration = profit_loop.get("registration") or {}
    profit_coverage = profit_loop.get("coverage") or {}
    typer.echo(
        f"  Status: {profit_loop.get('status', 'unknown')}  "
        f"epoch={str(registration.get('epoch_id') or 'not_registered')[:16]}"
    )
    typer.echo(
        "  Cutoff: "
        f"{registration.get('cutoff_at', 'not_available')}  "
        f"artifact={((profit_loop.get('artifact') or {}).get('status') or 'not_available')}"
    )
    typer.echo(
        "  Coverage: "
        f"candidate={((profit_coverage.get('candidate') or {}).get('units', 'not_available'))} "
        f"position={((profit_coverage.get('position') or {}).get('units', 'not_available'))} "
        f"path={((profit_coverage.get('path') or {}).get('observations', 'not_available'))}"
    )

    # ─── Authority ─────────────────────────────────────────────────────────
    typer.echo("╠═ Authority ══════════════════════════════════════════╣")
    if "error" in authority:
        typer.echo("  (unavailable — gateway may be stopped)")
    else:
        gate = authority.get("influence_gate", "unknown")
        refreeze = authority.get("global_refreeze", "none")
        typer.echo(f"  Influence gate : {gate}")
        typer.echo(f"  Global refreeze: {refreeze}")

    # ─── Active experiments / hypotheses ───────────────────────────────────
    typer.echo("╠═ Active Hypotheses ══════════════════════════════════╣")
    exps = data.get("active_experiments", [])
    if not data.get("active_experiments_available"):
        typer.echo("  (not available — experiment ledger query failed)")
    elif not exps:
        typer.echo("  (none open)")
    else:
        for exp in exps:
            typer.echo(
                f"  [{exp.get('stage', '?')}] {exp.get('experiment_id', '?')[:16]}  "
                f"status={exp.get('status', '?')}  "
                f"started={str(exp.get('started_at') or '')[:19]}"
            )
            if exp.get("policy_epoch"):
                typer.echo(f"      epoch: {exp['policy_epoch'][:12]}...")

    # ─── Evidence epoch window ─────────────────────────────────────────────
    typer.echo("╠═ Evidence Epochs ════════════════════════════════════╣")
    typer.echo(f"  Anchored : {evidence.get('all_anchored', False)}   SHA consistent: {evidence.get('all_sha_consistent', False)}")
    for path in evidence.get("paths", []):
        flag = "✓" if path.get("sha_consistent") else ("!" if path.get("sha_consistent") is False else "?")
        typer.echo(
            f"  {flag} {path['path_id']}: {path.get('status', '?')}  "
            f"started={str(path.get('started_at') or 'none')[:19]}"
        )

    # ─── Window continuity ─────────────────────────────────────────────────
    typer.echo("╠═ Window Continuity ══════════════════════════════════╣")
    for key, val in continuity.items():
        flag = "✓" if val is True else ("⚠" if val is False else "?")
        typer.echo(f"  {flag} {key}: {val}")

    # ─── Open policy epochs (verbose) ──────────────────────────────────────
    if verbose:
        typer.echo("╠═ Open Policy Epochs ═════════════════════════════════╣")
        epochs = data.get("open_policy_epochs", [])
        if not data.get("open_policy_epochs_available", True):
            typer.echo(
                "  (not available — "
                f"{data.get('open_policy_epochs_error') or 'query failed'})"
            )
        elif not epochs:
            typer.echo("  (none)")
        else:
            for ep in epochs:
                typer.echo(
                    f"  [{ep.get('epoch_kind', '?')[:40]}]  "
                    f"id={str(ep.get('epoch_id') or '')[:12]}  "
                    f"started={str(ep.get('started_at') or '')[:19]}"
                )

    typer.echo("╚══════════════════════════════════════════════════════╝")
