"""Durable, prospective Persona progressive-unlock state.

The resolver is deliberately fail-closed: a configured progressive unlock has no
dynamic authority until a persisted rollout identity, an exact sizing manifest,
and a scoped evidence verdict agree.  The eight-hour scheduler only rechecks
this state machine; elapsed time never counts as evidence.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from typing import Any
from uuid import uuid4

logger = logging.getLogger("aletheia.trading.persona_rollout")

ROLLOUT_SCHEMA_VERSION = "persona-progressive-unlock/v2"
ROLLOUT_POLICY_VERSION = "persona-progressive-unlock-policy/v2"
MIN_MARKET_DAYS = 3
_MIN_MEAN_REWARD_PCT = 0.0
ROLLOUT_STATUSES = frozenset(
    {"frozen", "observing", "eligible", "unlocked", "refrozen", "completed"}
)


def _now(now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat()


def _json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return default
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _model_dump(value: Any) -> dict[str, Any]:
    try:
        return value.model_dump(mode="json")
    except AttributeError:
        if isinstance(value, dict):
            return dict(value)
        return {
            key: getattr(value, key)
            for key in ("name", "description", "params", "operator_decidable", "unlock_ladder")
            if hasattr(value, key)
        }


def _runtime_version() -> str:
    try:
        from aletheia.agent_runtime.build_info import get_boot_commit

        return str(get_boot_commit() or "unknown")
    except Exception:
        return "unknown"


def _identity(cfg) -> dict[str, Any] | None:
    trading = getattr(cfg, "trading", None)
    if trading is None:
        return None
    persona_id = str(getattr(trading, "active_persona", "") or "").strip()
    persona = (getattr(trading, "personas", None) or {}).get(persona_id)
    if not persona_id or persona is None:
        return None
    persona_values = _model_dump(persona)
    persona_fingerprint = hashlib.sha256(
        json.dumps(persona_values, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    outcome_semantics_version = _outcome_semantics_version()
    required_samples = max(1, int(getattr(trading, "persona_unlock_step_samples", 25)))
    policy_values = {
        "contract": ROLLOUT_POLICY_VERSION,
        "persona_id": persona_id,
        "persona_fingerprint": persona_fingerprint,
        "unlock_step_samples": required_samples,
        "persona_learning_enabled": bool(
            getattr(trading, "persona_learning_enabled", False)
        ),
        "persona_learning_min_samples": int(
            getattr(trading, "persona_learning_min_samples", 15)
        ),
        "vol_sizing_enabled": bool(getattr(trading, "vol_sizing_enabled", False)),
        "vol_target_pct": getattr(trading, "vol_target_pct", None),
        "vol_target_risk_usdt": getattr(trading, "vol_target_risk_usdt", None),
        "default_notional_usdt": getattr(trading, "default_notional_usdt", None),
        "vol_kelly_cap_fraction": getattr(trading, "vol_kelly_cap_fraction", None),
        "persona_sizing_enabled": bool(getattr(trading, "persona_sizing_enabled", False)),
        "persona_progressive_unlock_enabled": bool(
            getattr(trading, "persona_progressive_unlock_enabled", False)
        ),
        "outcome_semantics_version": outcome_semantics_version,
        "runtime_version": _runtime_version(),
    }
    policy_epoch = hashlib.sha256(
        json.dumps(policy_values, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    ladder = [
        str(key)
        for key in (getattr(persona, "unlock_ladder", None) or [])
        if str(key) in {"notional_usdt", "kelly_fraction"}
    ]
    return {
        "persona_id": persona_id,
        "persona_fingerprint": persona_fingerprint,
        "policy_epoch": policy_epoch,
        "runtime_version": policy_values["runtime_version"],
        "outcome_semantics_version": outcome_semantics_version,
        "required_samples": required_samples,
        "ladder": ladder,
    }


def _outcome_semantics_version() -> str:
    try:
        from aletheia.analytics.outcome_integrity import OUTCOME_SEMANTICS_VERSION

        return str(OUTCOME_SEMANTICS_VERSION)
    except Exception:
        return "unavailable"


def _db():
    from aletheia.storage.sqlite import get_db

    return get_db()


def _transaction():
    from aletheia.storage.repositories import transaction

    return transaction(immediate=True)


def _state_view(row) -> dict[str, Any]:
    state = dict(row)
    state["baseline_snapshot"] = _json(state.get("baseline_snapshot_json"), {})
    state["current_step"] = int(state.get("current_step") or 0)
    requirements = state["baseline_snapshot"].get("requirements") or {}
    state["required_samples"] = int(
        state.get("required_samples")
        or requirements.get("required_samples")
        or 25
    )
    state["required_market_days"] = int(
        requirements.get("required_market_days") or MIN_MARKET_DAYS
    )
    state["outcome_semantics_version"] = str(
        state.get("outcome_semantics_version")
        or requirements.get("outcome_semantics_version")
        or "unavailable"
    )
    return state


def _audit(event_type: str, state: dict[str, Any], *, reason: str, before: str | None = None) -> None:
    try:
        from aletheia.files.jsonl import write_audit

        payload = {
            "actor": "system",
            "source": "persona_rollout",
            "event_type": event_type,
            "entity_type": "persona_rollout",
            "entity_id": state.get("rollout_id"),
            "message": reason,
            "after": {
                "status": state.get("status"),
                "current_step": state.get("current_step"),
                "current_parameter": state.get("current_parameter"),
                "policy_epoch": state.get("policy_epoch"),
            },
        }
        if before is not None:
            payload["before"] = {"status": before}
        write_audit(payload)
    except Exception:
        logger.debug("persona rollout audit unavailable", exc_info=True)


def _record_event(
    db,
    state: dict[str, Any],
    *,
    event_type: str,
    reason: str,
    evidence: dict[str, Any] | None = None,
    actor: str = "system",
    source: str = "persona_rollout",
) -> None:
    db.execute(
        "INSERT INTO persona_rollout_events "
        "(event_id, rollout_id, step, parameter, status, event_type, authority, reason, "
        "evidence_json, actor, source, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            str(uuid4()),
            state["rollout_id"],
            int(state.get("current_step") or 0),
            state.get("current_parameter"),
            state.get("status") or "unknown",
            event_type,
            state.get("authority") or "",
            reason,
            json.dumps(evidence or {}, sort_keys=True, default=str),
            actor,
            source,
            state.get("updated_at") or _now(),
        ),
    )


def _baseline_snapshot(db, cfg=None, identity: dict[str, Any] | None = None) -> dict[str, Any]:
    row = db.execute(
        "SELECT COUNT(*) AS count, MAX(closed_at) AS max_closed_at, MAX(id) AS max_id "
        "FROM positions WHERE status='closed'"
    ).fetchone()
    snapshot: dict[str, Any] = {
        "outcome_count": int(row["count"] or 0) if row else 0,
        "max_closed_at": row["max_closed_at"] if row else None,
        "max_outcome_id": row["max_id"] if row else None,
        "captured_at": _now(),
    }
    if cfg is not None:
        trading = getattr(cfg, "trading", None)
        persona_id = str(getattr(trading, "active_persona", "") or "")
        persona = (getattr(trading, "personas", None) or {}).get(persona_id)
        if persona is not None:
            values = _model_dump(persona)
            snapshot["safe_baseline"] = {
                "persona_id": persona_id,
                "persona_fingerprint": (identity or {}).get("persona_fingerprint"),
                "params": values.get("params") or {},
                "operator_decidable": values.get("operator_decidable") or {},
                "unlock_ladder": values.get("unlock_ladder") or [],
            }
        snapshot["requirements"] = {
            "required_samples": int(
                (identity or {}).get(
                    "required_samples",
                    getattr(trading, "persona_unlock_step_samples", 25),
                )
            ),
            "required_market_days": MIN_MARKET_DAYS,
            "outcome_semantics_version": (identity or {}).get(
                "outcome_semantics_version", _outcome_semantics_version()
            ),
        }
    return snapshot


def _insert_rollout(db, identity: dict[str, Any], *, now: str, reason: str, cfg=None) -> dict[str, Any]:
    rollout_id = str(uuid4())
    status = "observing" if identity["ladder"] else "completed"
    snapshot = _baseline_snapshot(db, cfg, identity)
    state = {
        "rollout_id": rollout_id,
        "persona_id": identity["persona_id"],
        "persona_fingerprint": identity["persona_fingerprint"],
        "policy_epoch": identity["policy_epoch"],
        "runtime_version": identity["runtime_version"],
        "outcome_semantics_version": identity["outcome_semantics_version"],
        "required_samples": identity["required_samples"],
        "current_step": 0,
        "current_parameter": identity["ladder"][0] if identity["ladder"] else None,
        "step_started_at": now,
        "baseline_snapshot_json": json.dumps(snapshot, sort_keys=True),
        "baseline_snapshot": snapshot,
        # No evidence has been accepted at rollout creation.  The historical
        # max close is retained in the baseline metadata, never as a watermark.
        "evidence_watermark": None,
        "status": status,
        "authority": "static_persona_baseline",
        "reason": reason,
        "created_at": now,
        "updated_at": now,
        "ended_at": None,
    }
    db.execute(
        "INSERT INTO persona_rollouts "
        "(rollout_id, persona_id, persona_fingerprint, policy_epoch, runtime_version, "
        "current_step, current_parameter, step_started_at, baseline_snapshot_json, "
        "evidence_watermark, status, authority, reason, created_at, updated_at, ended_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            state["rollout_id"],
            state["persona_id"],
            state["persona_fingerprint"],
            state["policy_epoch"],
            state["runtime_version"],
            state["current_step"],
            state["current_parameter"],
            state["step_started_at"],
            state["baseline_snapshot_json"],
            state["evidence_watermark"],
            state["status"],
            state["authority"],
            state["reason"],
            state["created_at"],
            state["updated_at"],
            state["ended_at"],
        ),
    )
    _record_event(db, state, event_type="rollout_started", reason=reason, evidence=snapshot)
    return state


def _open_rows(db) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in db.execute(
            "SELECT * FROM persona_rollouts WHERE ended_at IS NULL "
            "ORDER BY created_at DESC, rowid DESC"
        ).fetchall()
    ]


def current_persona_rollout(cfg=None, *, create: bool = False, now: datetime | None = None) -> dict[str, Any] | None:
    """Return the active rollout; create it only when explicitly requested."""
    if cfg is None:
        from aletheia.config.loader import load_config

        cfg = load_config()
    if not bool(getattr(cfg.trading, "persona_progressive_unlock_enabled", False)):
        return None
    if not bool(getattr(cfg.trading, "persona_sizing_enabled", False)):
        return None
    identity = _identity(cfg)
    if identity is None:
        return None
    now_iso = _now(now)
    try:
        with _transaction():
            db = _db()
            rows = _open_rows(db)
            matching = next(
                (
                    row for row in rows
                    if row["persona_id"] == identity["persona_id"]
                    and row["persona_fingerprint"] == identity["persona_fingerprint"]
                    and row["policy_epoch"] == identity["policy_epoch"]
                ),
                None,
            )
            if matching is not None:
                return _state_view(matching)
            if not create:
                return None
            for row in rows:
                db.execute(
                    "UPDATE persona_rollouts SET status='refrozen', authority=?, reason=?, "
                    "updated_at=?, ended_at=? WHERE rollout_id=? AND ended_at IS NULL",
                    (
                        "safe_static_baseline",
                        "persona_or_policy_identity_changed",
                        now_iso,
                        now_iso,
                        row["rollout_id"],
                    ),
                )
                invalidated = _state_view({
                    **row,
                    "status": "refrozen",
                    "authority": "safe_static_baseline",
                    "reason": "persona_or_policy_identity_changed",
                    "updated_at": now_iso,
                    "ended_at": now_iso,
                })
                _record_event(
                    db,
                    invalidated,
                    event_type="rollout_invalidated",
                    reason="persona or policy identity changed; old evidence cannot be reused",
                )
            state = _insert_rollout(
                db,
                identity,
                now=now_iso,
                reason="prospective_rollout_identity_created",
                cfg=cfg,
            )
        if rows:
            _audit(
                "persona_rollout_refrozen",
                {**rows[0], "status": "refrozen", "reason": "persona_or_policy_identity_changed"},
                reason="persona or policy identity changed; old evidence cannot be reused",
                before=str(rows[0].get("status") or "unknown"),
            )
        _audit("persona_rollout_started", state, reason="prospective rollout identity created")
        return _state_view(state)
    except Exception as exc:
        logger.warning("persona rollout state unavailable: %s", exc)
        return None


def start_persona_rollout(cfg=None, *, reason: str = "operator_enabled_progressive_unlock", now: datetime | None = None) -> dict[str, Any] | None:
    """Start a fresh identity after an explicit enable/persona change."""
    if cfg is None:
        from aletheia.config.loader import load_config

        cfg = load_config()
    if not bool(getattr(cfg.trading, "persona_progressive_unlock_enabled", False)):
        return None
    if not bool(getattr(cfg.trading, "persona_sizing_enabled", False)):
        return None
    identity = _identity(cfg)
    if identity is None:
        return None
    now_iso = _now(now)
    try:
        with _transaction():
            db = _db()
            old_rows = _open_rows(db)
            for row in old_rows:
                db.execute(
                    "UPDATE persona_rollouts SET status='refrozen', authority=?, reason=?, "
                    "updated_at=?, ended_at=? WHERE rollout_id=? AND ended_at IS NULL",
                    ("safe_static_baseline", "new_rollout_requested", now_iso, now_iso, row["rollout_id"]),
                )
                invalidated = _state_view({
                    **row,
                    "status": "refrozen",
                    "authority": "safe_static_baseline",
                    "reason": "new_rollout_requested",
                    "updated_at": now_iso,
                    "ended_at": now_iso,
                })
                _record_event(
                    db,
                    invalidated,
                    event_type="rollout_invalidated",
                    reason="new rollout requested; old evidence cannot be reused",
                )
            state = _insert_rollout(db, identity, now=now_iso, reason=reason, cfg=cfg)
        _audit("persona_rollout_started", state, reason=reason)
        return _state_view(state)
    except Exception as exc:
        logger.warning("persona rollout start failed: %s", exc)
        return None


def invalidate_persona_rollouts(
    *, reason: str = "progressive_rollout_disabled", now: datetime | None = None
) -> int:
    """End every open rollout so a later re-enable starts a fresh cohort."""
    now_iso = _now(now)
    try:
        with _transaction():
            db = _db()
            rows = _open_rows(db)
            for row in rows:
                db.execute(
                    "UPDATE persona_rollouts SET status='refrozen', authority=?, reason=?, "
                    "updated_at=?, ended_at=? WHERE rollout_id=? AND ended_at IS NULL",
                    ("safe_static_baseline", reason, now_iso, now_iso, row["rollout_id"]),
                )
                invalidated = _state_view({
                    **row,
                    "status": "refrozen",
                    "authority": "safe_static_baseline",
                    "reason": reason,
                    "updated_at": now_iso,
                    "ended_at": now_iso,
                })
                _record_event(
                    db,
                    invalidated,
                    event_type="rollout_invalidated",
                    reason=reason,
                )
        for row in rows:
            _audit(
                "persona_rollout_refrozen",
                {**row, "status": "refrozen", "reason": reason},
                reason=reason,
                before=str(row.get("status") or "unknown"),
            )
        return len(rows)
    except Exception as exc:
        logger.warning("persona rollout invalidation failed: %s", exc)
        return 0


def _ladder(cfg) -> list[str]:
    identity = _identity(cfg)
    return list(identity["ladder"]) if identity else []


def rollout_runtime_safe(cfg) -> bool:
    """Return whether dynamic Persona authority may be used right now."""
    try:
        agent = getattr(cfg, "agent", None)
        if agent is not None and not bool(getattr(agent, "autonomous_learning", True)):
            return False
        runtime = getattr(cfg, "runtime", None)
        if runtime is not None and bool(getattr(runtime, "paused", False)):
            return False
        from aletheia.learning.control import learning_influence_enabled

        if not learning_influence_enabled():
            return False
        from aletheia.trading.sizing_policy import in_distress

        if in_distress(cfg):
            return False
        from aletheia.portfolio.position_reconciler import detect_reconcile_thrash

        return not detect_reconcile_thrash().get("is_thrashing")
    except Exception:
        return False


def _authorized_params(cfg, state: dict[str, Any] | None) -> list[str]:
    persona = (getattr(cfg.trading, "personas", None) or {}).get(getattr(cfg.trading, "active_persona", ""))
    static = [key for key, value in (getattr(persona, "operator_decidable", None) or {}).items() if value]
    if not state:
        if (
            getattr(cfg.trading, "persona_progressive_unlock_enabled", False)
            and getattr(cfg.trading, "persona_sizing_enabled", False)
            and _ladder(cfg)
        ):
            return []
        return sorted(set(static))
    status = str(state.get("status") or "")
    if status == "refrozen":
        return []
    ladder = _ladder(cfg)
    if ladder and not rollout_runtime_safe(cfg):
        return []
    if status == "completed":
        return sorted(set(static + ladder))
    if status == "unlocked":
        count = max(0, min(len(ladder), int(state.get("current_step") or 0) + 1))
        return sorted(set(static + ladder[:count]))
    if status in {"frozen", "observing", "eligible"}:
        count = max(0, min(len(ladder), int(state.get("current_step") or 0)))
        return sorted(set(static + ladder[:count]))
    return []


def build_sizing_manifest(cfg, *, authorized_params: list[str] | tuple[str, ...] = (), state=None) -> dict[str, Any]:
    """Build the immutable, secret-free evidence identity attached to an entry."""
    if not getattr(cfg.trading, "persona_sizing_enabled", False):
        manifest = {
            "schema_version": ROLLOUT_SCHEMA_VERSION,
            "persona_id": "legacy",
            "parameter_state": "static",
            "assignment": "none",
            "active_params": [],
        }
        manifest["manifest_hash"] = _manifest_hash(manifest)
        return manifest
    identity = _identity(cfg)
    if identity is None:
        manifest = {
            "schema_version": ROLLOUT_SCHEMA_VERSION,
            "persona_id": "legacy",
            "parameter_state": "static",
            "assignment": "none",
            "active_params": [],
        }
        manifest["manifest_hash"] = _manifest_hash(manifest)
        return manifest
    state = state or current_persona_rollout(cfg, create=bool(getattr(cfg.trading, "persona_progressive_unlock_enabled", False)))
    authorized = sorted(set(str(key) for key in authorized_params))
    current_parameter = state.get("current_parameter") if state else None
    dynamic = bool(state and state.get("status") in {"unlocked", "completed"})
    progressive_enabled = bool(getattr(cfg.trading, "persona_progressive_unlock_enabled", False))
    parameter_state = (
        "unlocked"
        if dynamic and (
            current_parameter in authorized or state.get("status") == "completed"
        )
        else "frozen" if progressive_enabled
        else "static"
    )
    manifest: dict[str, Any] = {
        "schema_version": ROLLOUT_SCHEMA_VERSION,
        "persona_id": identity["persona_id"],
        "persona_fingerprint": identity["persona_fingerprint"],
        "policy_epoch": identity["policy_epoch"],
        "runtime_version": identity["runtime_version"],
        "outcome_semantics_version": identity["outcome_semantics_version"],
        "rollout_id": state.get("rollout_id") if state else None,
        "step": int(state.get("current_step") or 0) if state else None,
        "parameter": current_parameter if progressive_enabled else None,
        "parameter_state": parameter_state,
        "assignment": (
            "treatment" if parameter_state == "unlocked"
            else "control" if progressive_enabled
            else "none"
        ),
        "active_params": authorized,
    }
    manifest["manifest_hash"] = _manifest_hash(manifest)
    return manifest


def _manifest_hash(manifest: dict[str, Any]) -> str:
    payload = {key: value for key, value in manifest.items() if key != "manifest_hash"}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _manifest_is_intact(manifest: dict[str, Any]) -> bool:
    stored = str(manifest.get("manifest_hash") or "")
    return bool(stored) and stored == _manifest_hash(manifest)


def _manifest_step(manifest: dict[str, Any]) -> int | None:
    try:
        return int(manifest.get("step"))
    except (TypeError, ValueError):
        return None


def _manifest_active_params(manifest: dict[str, Any]) -> set[str]:
    value = manifest.get("active_params")
    return {str(item) for item in value} if isinstance(value, (list, tuple, set)) else set()


def serialize_sizing_manifest(manifest: Any) -> str | None:
    if not isinstance(manifest, dict) or not manifest.get("schema_version"):
        return None
    try:
        return json.dumps(manifest, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return None


def extract_sizing_manifest(row: dict[str, Any]) -> dict[str, Any] | None:
    """Read the persisted manifest from a Position or Proposal rationale."""
    candidates: list[Any] = [
        row.get("sizing_manifest"),
        row.get("persona_manifest"),
        row.get("attribution_snapshot"),
    ]
    for field in ("decision_reasoning", "agent_rationale"):
        raw = row.get(field)
        if raw:
            text = str(raw)
            candidates.extend([text.rsplit("---\n", 1)[-1], text])
    for candidate in candidates:
        parsed = candidate
        if isinstance(candidate, str):
            try:
                parsed = json.loads(candidate)
            except (TypeError, ValueError):
                continue
        if not isinstance(parsed, dict):
            continue
        nested = parsed.get("sizing_policy") or parsed.get("sizing_learning") or parsed.get("persona_rollout")
        if isinstance(nested, dict):
            nested = nested.get("persona_manifest") or nested.get("sizing_manifest") or nested
        if isinstance(nested, dict) and nested.get("schema_version") == ROLLOUT_SCHEMA_VERSION:
            return nested
        if parsed.get("schema_version") == ROLLOUT_SCHEMA_VERSION:
            return parsed
    if row.get("persona_rollout_id"):
        return {
            "schema_version": ROLLOUT_SCHEMA_VERSION,
            "rollout_id": row.get("persona_rollout_id"),
            "persona_id": row.get("persona_id"),
            "persona_fingerprint": row.get("persona_fingerprint"),
            "policy_epoch": row.get("policy_epoch"),
            "step": row.get("persona_step"),
            "parameter": row.get("persona_parameter"),
            "parameter_state": row.get("persona_parameter_state"),
            "assignment": row.get("persona_assignment"),
        }
    return None


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _market_day(value: Any) -> str | None:
    parsed = _parse_time(value)
    return parsed.date().isoformat() if parsed is not None else None


def _stability_verdict(
    rewards: list[float], *, baseline_mean: float | None,
) -> dict[str, Any]:
    """Small, deterministic contribution verdict for one scoped step."""
    if not rewards:
        return {
            "stable": False,
            "non_degrading": False,
            "mean_reward_pct": None,
            "recent_mean_reward_pct": None,
            "baseline_mean_reward_pct": baseline_mean,
            "reasons": [],
        }
    mean_reward = sum(rewards) / len(rewards)
    split = max(1, len(rewards) // 2)
    recent = rewards[split:] or rewards
    recent_mean = sum(recent) / len(recent)
    reasons: list[str] = []
    if mean_reward < _MIN_MEAN_REWARD_PCT:
        reasons.append("negative_contribution")
    if baseline_mean is not None and mean_reward < float(baseline_mean):
        reasons.append("degrading_contribution")
    # A late-window drop is a stability failure even when the aggregate remains
    # positive. The 0.25pp tolerance avoids treating rounding noise as degradation.
    if len(rewards) >= 2 and recent_mean < (sum(rewards[:split]) / split) - 0.25:
        reasons.append("unstable_contribution")
    return {
        "stable": "unstable_contribution" not in reasons,
        "non_degrading": not any(
            reason in {"negative_contribution", "degrading_contribution"}
            for reason in reasons
        ),
        "mean_reward_pct": round(mean_reward, 8),
        "recent_mean_reward_pct": round(recent_mean, 8),
        "baseline_mean_reward_pct": baseline_mean,
        "reasons": reasons,
    }


def _reward(row: dict[str, Any]) -> float | None:
    """Return the central outcome contract's cost-aware net return."""
    try:
        from aletheia.analytics.outcome_integrity import assess_reportable_outcome

        assessment = assess_reportable_outcome(row)
        if not assessment.eligible or assessment.net_return_pct is None:
            return None
        reward = float(assessment.net_return_pct)
        return reward if reward == reward and abs(reward) != float("inf") else None
    except (TypeError, ValueError, ArithmeticError):
        return None


def _evidence(state: dict[str, Any]) -> dict[str, Any]:
    try:
        from aletheia.analytics.outcome_integrity import eligible_strategy_outcomes

        rows = eligible_strategy_outcomes(closed_after=state["step_started_at"])
    except Exception as exc:
        return {"available": False, "reason": "outcome_read_failed", "error": str(exc)}
    expected_assignment = (
        "control"
        if state.get("status") in {"frozen", "observing", "eligible"}
        else "treatment"
    )
    valid: list[dict[str, Any]] = []
    invalid = 0
    seen: set[str] = set()
    anchor = _parse_time(state.get("step_started_at"))
    expected_semantics = str(
        state.get("outcome_semantics_version") or _outcome_semantics_version()
    )
    for row in rows:
        closed_at = row.get("closed_at")
        closed_time = _parse_time(closed_at)
        # The SQL reader is inclusive for compatibility; the rollout contract is
        # strictly prospective, so an outcome at the exact anchor is excluded.
        if anchor is None or closed_time is None or closed_time <= anchor:
            continue
        manifest = extract_sizing_manifest(row)
        if not manifest:
            invalid += 1
            continue
        if (
            not _manifest_is_intact(manifest)
            or manifest.get("schema_version") != ROLLOUT_SCHEMA_VERSION
            or manifest.get("persona_id") != state.get("persona_id")
            or manifest.get("persona_fingerprint") != state.get("persona_fingerprint")
            or manifest.get("policy_epoch") != state.get("policy_epoch")
            or manifest.get("runtime_version") != state.get("runtime_version")
            or manifest.get("outcome_semantics_version") != expected_semantics
            or manifest.get("rollout_id") != state.get("rollout_id")
            or _manifest_step(manifest) != int(state.get("current_step") or 0)
            or manifest.get("parameter") != state.get("current_parameter")
            or manifest.get("assignment") != expected_assignment
            or manifest.get("parameter_state")
            != ("frozen" if expected_assignment == "control" else "unlocked")
            or (
                expected_assignment == "treatment"
                and state.get("current_parameter") not in _manifest_active_params(manifest)
            )
            or (
                expected_assignment == "control"
                and state.get("current_parameter") in _manifest_active_params(manifest)
            )
        ):
            invalid += 1
            continue
        key = str(row.get("position_id") or row.get("id") or "")
        if not key or key in seen:
            continue
        reward = _reward(row)
        if reward is None:
            invalid += 1
            continue
        seen.add(key)
        valid.append({"id": key, "closed_at": closed_at, "reward_pct": reward})
    rewards = [float(item["reward_pct"]) for item in valid]
    market_days = {
        _market_day(item.get("closed_at"))
        for item in valid
        if _market_day(item.get("closed_at"))
    }
    mean_reward = sum(rewards) / len(rewards) if rewards else None
    baseline = state.get("baseline_snapshot") or {}
    cohort_baseline = baseline.get("cohort") or baseline
    baseline_mean = cohort_baseline.get("mean_reward_pct")
    reasons: list[str] = []
    required = max(1, int(state.get("required_samples") or 25))
    required_market_days = max(
        1,
        int(state.get("required_market_days") or MIN_MARKET_DAYS),
    )
    stability = _stability_verdict(rewards, baseline_mean=baseline_mean)
    if invalid:
        reasons.append("invalid_provenance")
    if len(valid) < required:
        reasons.append("insufficient_scoped_samples")
    if len(market_days) < required_market_days:
        reasons.append("insufficient_market_days")
    reasons.extend(stability["reasons"])
    latest = valid[-1] if valid else None
    snapshot = {
        "rollout_id": state.get("rollout_id"),
        "persona_id": state.get("persona_id"),
        "policy_epoch": state.get("policy_epoch"),
        "step": int(state.get("current_step") or 0),
        "parameter": state.get("current_parameter"),
        "anchor_at": state.get("step_started_at"),
        "assignment": expected_assignment,
        "sample_count": len(valid),
        "market_day_count": len(market_days),
        "mean_reward_pct": round(mean_reward, 8) if mean_reward is not None else None,
        "baseline_mean_reward_pct": baseline_mean,
        "invalid_provenance_count": invalid,
        "required_samples": required,
        "required_market_days": required_market_days,
        "stability": stability,
        "last_outcome_id": latest.get("id") if latest else None,
        "last_closed_at": latest.get("closed_at") if latest else None,
    }
    return {
        "available": True,
        "eligible": not reasons,
        "reason": reasons[0] if reasons else "stable_non_degrading_verdict",
        "reasons": reasons,
        "expected_assignment": expected_assignment,
        "verdict": "pass" if not reasons else "fail",
        **snapshot,
        "snapshot": snapshot,
    }


def _update_state(db, state: dict[str, Any], *, updates: dict[str, Any], event_type: str, reason: str, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    now = _now()
    merged = {**state, **updates, "updated_at": now}
    assignments = {
        "current_step": int(merged.get("current_step") or 0),
        "current_parameter": merged.get("current_parameter"),
        "step_started_at": merged.get("step_started_at"),
        "baseline_snapshot_json": json.dumps(merged.get("baseline_snapshot") or {}, sort_keys=True, default=str),
        "evidence_watermark": merged.get("evidence_watermark"),
        "status": merged.get("status"),
        "authority": merged.get("authority"),
        "reason": reason,
        "updated_at": now,
        "ended_at": merged.get("ended_at"),
        "rollout_id": merged["rollout_id"],
    }
    db.execute(
        "UPDATE persona_rollouts SET current_step=?, current_parameter=?, step_started_at=?, "
        "baseline_snapshot_json=?, evidence_watermark=?, status=?, authority=?, reason=?, "
        "updated_at=?, ended_at=? WHERE rollout_id=?",
        (
            assignments["current_step"], assignments["current_parameter"], assignments["step_started_at"],
            assignments["baseline_snapshot_json"], assignments["evidence_watermark"], assignments["status"],
            assignments["authority"], assignments["reason"], assignments["updated_at"], assignments["ended_at"],
            assignments["rollout_id"],
        ),
    )
    merged.update(assignments)
    _record_event(db, merged, event_type=event_type, reason=reason, evidence=evidence)
    return _state_view(merged)


def _baseline_with_cohort(state: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    baseline = dict(state.get("baseline_snapshot") or {})
    baseline["cohort"] = dict(evidence.get("snapshot") or {})
    baseline["captured_at"] = baseline.get("captured_at") or _now()
    return baseline


def _next_step_started_at(
    requested: datetime | None,
    state: dict[str, Any],
    evidence: dict[str, Any],
) -> str:
    candidate = _parse_time(_now(requested)) or datetime.now(timezone.utc)
    previous = _parse_time(state.get("step_started_at"))
    last_closed = _parse_time(evidence.get("last_closed_at"))
    known_times = [item for item in (previous, last_closed) if item is not None]
    if not known_times:
        return _now(candidate)
    floor = max(known_times)
    if candidate <= floor:
        candidate = floor + timedelta(microseconds=1)
    return _now(candidate)


def refreeze_persona_rollout(
    cfg=None,
    *,
    reason: str,
    state=None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if cfg is None:
        from aletheia.config.loader import load_config

        cfg = load_config()
    state = state or current_persona_rollout(cfg, create=False)
    if not state:
        return None
    before = str(state.get("status") or "unknown")
    try:
        with _transaction():
            updates: dict[str, Any] = {
                "status": "refrozen",
                "authority": "safe_static_baseline",
            }
            if evidence:
                baseline = dict(state.get("baseline_snapshot") or {})
                baseline["last_verdict"] = dict(evidence)
                updates["baseline_snapshot"] = baseline
            updated = _update_state(
                _db(),
                state,
                updates=updates,
                event_type="parameter_refrozen",
                reason=reason,
                evidence=evidence,
            )
        _audit("persona_parameter_refrozen", updated, reason=reason, before=before)
        return updated
    except Exception as exc:
        logger.warning("persona rollout refreeze failed: %s", exc)
        return state


def advance_persona_rollout(now: datetime | None = None) -> dict[str, Any]:
    """Run one evidence-gated transition; never toggles unrelated config flags."""
    from aletheia.config.loader import load_config

    cfg = load_config()
    t = cfg.trading
    if not getattr(t, "persona_auto_advance_enabled", False):
        return {"action": "disabled", "reason": "persona_auto_advance_enabled=False"}
    agent = getattr(cfg, "agent", None)
    if agent is not None and not bool(getattr(agent, "autonomous_learning", True)):
        return {"action": "hold", "reason": "autonomous_learning_disabled"}
    runtime = getattr(cfg, "runtime", None)
    if runtime is not None and bool(getattr(runtime, "paused", False)):
        return {"action": "hold", "reason": "runtime_paused"}
    if not t.persona_sizing_enabled:
        return {"action": "hold", "reason": "persona_sizing not enabled (rollout not started)"}
    if not getattr(t, "persona_progressive_unlock_enabled", False):
        return {"action": "hold", "reason": "persona_progressive_unlock_enabled=False"}

    state = current_persona_rollout(cfg, create=True, now=now)
    if state is None:
        try:
            from aletheia.learning.control import global_learning_refreeze_reason

            health_reason = global_learning_refreeze_reason()
        except Exception:
            health_reason = "learning_integrity_state_unavailable"
        if health_reason:
            return {"action": "hold", "reason": health_reason}
        return {"action": "hold", "reason": "persona_rollout_state_unavailable"}
    if state.get("status") not in ROLLOUT_STATUSES:
        frozen = refreeze_persona_rollout(
            cfg, reason="invalid_rollout_status", state=state
        )
        return {"action": "refrozen", "reason": "invalid_rollout_status", "rollout": frozen}
    from aletheia.trading.sizing_policy import in_distress

    if in_distress(cfg):
        frozen = refreeze_persona_rollout(cfg, reason="account_distress", state=state)
        return {"action": "refrozen", "reason": "account_distress", "rollout": frozen}

    try:
        from aletheia.learning.control import global_learning_refreeze_reason

        health_reason = global_learning_refreeze_reason()
    except Exception:
        health_reason = "learning_integrity_state_unavailable"
    if health_reason:
        frozen = refreeze_persona_rollout(cfg, reason=health_reason, state=state)
        return {"action": "refrozen", "reason": health_reason, "rollout": frozen}
    try:
        from aletheia.portfolio.position_reconciler import detect_reconcile_thrash

        if detect_reconcile_thrash().get("is_thrashing"):
            frozen = refreeze_persona_rollout(cfg, reason="reconcile_thrash", state=state)
            return {"action": "refrozen", "reason": "reconcile_thrash", "rollout": frozen}
    except Exception:
        frozen = refreeze_persona_rollout(
            cfg, reason="reconcile_thrash_state_unavailable", state=state
        )
        return {
            "action": "refrozen",
            "reason": "reconcile_thrash_state_unavailable",
            "rollout": frozen,
        }

    if state.get("status") in {"refrozen", "completed"}:
        return {"action": "hold", "reason": state.get("reason") or state.get("status"), "rollout": state}

    evidence = _evidence(state)
    if not evidence.get("available"):
        return {"action": "hold", "reason": evidence.get("reason"), "evidence": evidence, "rollout": state}
    if any(
        reason in {
            "invalid_provenance",
            "negative_contribution",
            "degrading_contribution",
            "unstable_contribution",
        }
        for reason in evidence.get("reasons", [])
    ):
        frozen = refreeze_persona_rollout(
            cfg,
            reason=evidence["reason"],
            state=state,
            evidence=evidence,
        )
        return {"action": "refrozen", "reason": evidence["reason"], "evidence": evidence, "rollout": frozen}
    if not evidence.get("eligible"):
        return {"action": "hold", "reason": evidence.get("reason"), "evidence": evidence, "rollout": state}

    ladder = _ladder(cfg)
    index = int(state.get("current_step") or 0)
    now_iso = _next_step_started_at(now, state, evidence)
    try:
        with _transaction():
            db = _db()
            eligible_state = _update_state(
                db,
                state,
                updates={
                    "status": "eligible",
                    "authority": "prospective_evidence_eligible",
                },
                event_type="evidence_eligible",
                reason="prospective stable non-degrading evidence verdict",
                evidence=evidence,
            )
            phase = str(state.get("status") or "")
            if phase in {"frozen", "observing", "eligible"}:
                # Control evidence proves the frozen baseline for this exact
                # parameter. The next state unlocks only that one parameter.
                next_index = index
                next_status = "unlocked"
            else:
                # Treatment evidence for the current parameter starts a fresh
                # control arm for the next one; it is not itself evidence for
                # the next parameter.
                next_index = index + 1
                next_status = "observing"
            baseline_snapshot = _baseline_with_cohort(state, evidence)
            if next_index >= len(ladder):
                updates = {
                    "status": "completed",
                    "current_step": len(ladder),
                    "current_parameter": None,
                    "step_started_at": now_iso,
                    "baseline_snapshot": baseline_snapshot,
                    "evidence_watermark": evidence.get("last_closed_at"),
                    "authority": "persona_progressive_unlock_complete",
                }
                event = "rollout_completed"
            else:
                updates = {
                    "status": next_status,
                    "current_step": next_index,
                    "current_parameter": ladder[next_index],
                    "step_started_at": now_iso,
                    "baseline_snapshot": baseline_snapshot,
                    "evidence_watermark": evidence.get("last_closed_at"),
                    "authority": (
                        f"persona_progressive_unlock_step_{next_index}"
                        if next_status == "unlocked"
                        else f"persona_progressive_unlock_observing_step_{next_index}"
                    ),
                }
                event = "parameter_unlocked" if next_status == "unlocked" else "step_started"
            updated = _update_state(
                db,
                eligible_state,
                updates=updates,
                event_type=event,
                reason="prospective stable non-degrading evidence verdict",
                evidence=evidence,
            )
        _audit(event, updated, reason="prospective stable non-degrading evidence verdict", before=str(state.get("status")))
        action = {
            "parameter_unlocked": "unlocked",
            "step_started": "observing",
            "rollout_completed": "completed",
        }[event]
        return {"action": action, "parameter": updated.get("current_parameter"), "evidence": evidence, "rollout": updated}
    except Exception as exc:
        logger.warning("persona rollout transition failed: %s", exc)
        return {"action": "hold", "reason": "persona_rollout_persist_failed", "rollout": state}


def persona_rollout_status(cfg=None, *, create: bool = False) -> dict[str, Any]:
    """Return desired flags separately from actual authority and evidence."""
    if cfg is None:
        from aletheia.config.loader import load_config

        cfg = load_config()
    state = current_persona_rollout(cfg, create=create)
    if state is None:
        return {
            "desired_progressive_unlock": bool(getattr(cfg.trading, "persona_progressive_unlock_enabled", False)),
            "desired_auto_advance": bool(getattr(cfg.trading, "persona_auto_advance_enabled", False)),
            "status": "not_started" if getattr(cfg.trading, "persona_progressive_unlock_enabled", False) else "disabled",
            "actual_authorized_params": _authorized_params(cfg, None),
            "evidence": {"available": False, "reason": "rollout_not_started"},
            "events": [],
        }
    if state.get("status") in {"frozen", "observing", "eligible", "unlocked"}:
        evidence = _evidence(state)
    elif state.get("status") == "refrozen":
        evidence = (state.get("baseline_snapshot") or {}).get("last_verdict") or {
            "available": False,
            "reason": state.get("reason") or "refrozen",
        }
    elif state.get("status") == "completed":
        cohort = (state.get("baseline_snapshot") or {}).get("cohort") or {}
        evidence = {
            "available": bool(cohort),
            "reason": "completed",
            "verdict": "pass" if cohort else "not available",
            **cohort,
        }
    else:
        evidence = {"available": True, "reason": state.get("status")}
    try:
        events = [
            dict(row)
            for row in _db().execute(
                "SELECT event_id, step, parameter, status, event_type, authority, reason, "
                "evidence_json, actor, source, created_at FROM persona_rollout_events "
                "WHERE rollout_id=? ORDER BY created_at DESC, rowid DESC LIMIT 20",
                (state.get("rollout_id"),),
            ).fetchall()
        ]
    except Exception:
        events = []
    return {
        "desired_progressive_unlock": bool(getattr(cfg.trading, "persona_progressive_unlock_enabled", False)),
        "desired_auto_advance": bool(getattr(cfg.trading, "persona_auto_advance_enabled", False)),
        "status": state.get("status"),
        "actual_authorized_params": _authorized_params(cfg, state),
        "rollout": state,
        "evidence": evidence,
        "events": events,
    }
