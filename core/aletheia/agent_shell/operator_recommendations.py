"""Operator recommendation synthesis from authoritative backend state."""

from datetime import datetime, timedelta, timezone
import json
import logging
import math

logger = logging.getLogger("aletheia.agent_shell.operator_recommendations")

from aletheia.utils.dt import parse_utc
from aletheia.agent_shell.operator_store import (
    get_latest_heartbeat,
    list_artifacts,
    list_commands,
    list_extensions,
    list_extension_runs,
    list_events,
    list_sessions,
    list_tasks,
)
from aletheia.config.loader import load_config
from aletheia.data.symbols import to_canonical
from aletheia.storage import repositories as repo


def _archived_recommendation_ids(session_id: str, *, limit: int = 200) -> set[str]:
    archived: set[str] = set()
    for artifact in list_artifacts(session_id=session_id, limit=limit):
        if artifact.get("artifact_type") != "notification_archive":
            continue
        content = artifact.get("content") or {}
        if not isinstance(content, dict):
            continue
        recommendation_id = content.get("recommendation_id")
        if recommendation_id:
            archived.add(str(recommendation_id))
    return archived


def _truthy_flag(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "none", "null"}
    return bool(value)




def _parse_json_list(value) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        parsed = json.loads(value)
    except Exception:
        return [str(value)]
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return [str(parsed)]


def _has_fresh_report_artifact(
    artifacts: list[dict],
    *,
    artifact_type: str,
    report_type: str,
    within_minutes: int,
) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=within_minutes)
    for artifact in artifacts:
        if artifact.get("artifact_type") != artifact_type:
            continue
        created_at = parse_utc(artifact.get("created_at"))
        if created_at is None or created_at <= cutoff:
            continue
        content = artifact.get("content") or {}
        if not isinstance(content, dict):
            continue
        if str(content.get("report_type") or "") == report_type:
            return True
    return False


def _is_stuck_task(task: dict, *, threshold_minutes: int = 30) -> bool:
    if str(task.get("status") or "") != "running":
        return False
    started_at = task.get("started_at") or task.get("created_at")
    parsed = parse_utc(started_at)
    if parsed is None:
        return False
    return (datetime.now(timezone.utc) - parsed) >= timedelta(minutes=threshold_minutes)


def _task_age_minutes(task: dict) -> float | None:
    parsed = parse_utc(task.get("created_at"))
    if parsed is None:
        return None
    return (datetime.now(timezone.utc) - parsed).total_seconds() / 60.0


def _run_age_minutes(run: dict) -> float | None:
    parsed = parse_utc(run.get("ended_at") or run.get("started_at"))
    if parsed is None:
        return None
    return (datetime.now(timezone.utc) - parsed).total_seconds() / 60.0


def _queue_review_policy(queued_tasks: list[dict]) -> dict | None:
    if not queued_tasks:
        return None

    queued_ages = [
        age for age in (_task_age_minutes(task) for task in queued_tasks) if age is not None
    ]
    oldest_queued_age_minutes = max(queued_ages) if queued_ages else None

    high_priority_queued = [
        task for task in queued_tasks if str(task.get("priority") or "") == "high"
    ]
    high_priority_ages = [
        age
        for age in (_task_age_minutes(task) for task in high_priority_queued)
        if age is not None
    ]
    oldest_high_priority_age_minutes = (
        max(high_priority_ages) if high_priority_ages else None
    )

    attention = len(queued_tasks) >= 3 or (
        oldest_queued_age_minutes is not None and oldest_queued_age_minutes >= 15
    )
    if not attention:
        return None

    urgent = (
        len(queued_tasks) >= 5
        or (
            oldest_queued_age_minutes is not None and oldest_queued_age_minutes >= 30
        )
        or (
            oldest_high_priority_age_minutes is not None
            and oldest_high_priority_age_minutes >= 10
        )
    )

    reasons: list[str] = []
    if oldest_queued_age_minutes is not None and oldest_queued_age_minutes >= 15:
        reasons.append(
            f"oldest queued task has waited about {int(oldest_queued_age_minutes)} minutes"
        )
    if len(queued_tasks) >= 3:
        reasons.append(f"{len(queued_tasks)} queued tasks are accumulating")
    if (
        oldest_high_priority_age_minutes is not None
        and oldest_high_priority_age_minutes >= 10
    ):
        label = (
            "1 high-priority queued task is aging"
            if len(high_priority_queued) == 1
            else f"{len(high_priority_queued)} high-priority queued tasks are aging"
        )
        reasons.append(
            f"{label} (oldest about {int(oldest_high_priority_age_minutes)} minutes)"
        )

    return {
        "review_stage": "urgent" if urgent else "attention",
        "severity": "high" if urgent else "medium",
        "suggested_review_cadence_sec": 60 if urgent else 300,
        "reasons": reasons,
        "oldest_age_minutes": (
            int(oldest_queued_age_minutes)
            if oldest_queued_age_minutes is not None
            else None
        ),
        "high_priority_queued_count": len(high_priority_queued),
        "oldest_high_priority_age_minutes": (
            int(oldest_high_priority_age_minutes)
            if oldest_high_priority_age_minutes is not None
            else None
        ),
    }


def _default_schedule_for(agent_name: str) -> str | None:
    schedules = {
        "market_data": "every 5 minutes",
        "position_manager": "every 5 minutes",
        "exchange_sync": "every 5 minutes",
        "portfolio_snapshot": "every 10 minutes",
        "post_trade_reviewer": "every 4 hours",
        "risk_critic": "every 6 hours",
        "dreaming_agent": "daily at 02:00",
        "strategy_designer": "daily at 01:00",
    }
    return schedules.get(agent_name)


def _suggested_schedule_for(agent_name: str) -> str | None:
    current = _default_schedule_for(agent_name)
    if current == "every 15 minutes":
        return "every 10 minutes"
    if current == "every 10 minutes":
        return "every 5 minutes"
    if current == "every 5 minutes":
        return "every 3 minutes"
    if current == "every 4 hours":
        return "every 2 hours"
    if current == "every 6 hours":
        return "every 4 hours"
    if current in {"daily at 02:00", "daily at 01:00"}:
        return "every 12 hours"
    return None


def _parse_numeric_threshold_violation(violation: str, prefix: str) -> tuple[float, float] | None:
    if not violation.startswith(prefix):
        return None
    payload = violation[len(prefix):]
    if ">" not in payload:
        return None
    actual_text, limit_text = payload.split(">", 1)
    try:
        return float(actual_text), float(limit_text)
    except ValueError:
        return None


def _parse_daily_loss_violation(violation: str) -> tuple[float, float] | None:
    prefix = "max_daily_loss_exceeded:"
    if not violation.startswith(prefix):
        return None
    payload = violation[len(prefix):]
    if "<=-" not in payload:
        return None
    realized_text, limit_text = payload.split("<=-", 1)
    try:
        return float(realized_text), float(limit_text)
    except ValueError:
        return None


def _round_up(value: float, digits: int = 2) -> float:
    factor = 10**digits
    return math.ceil(value * factor) / factor


def _risk_boundary_label(boundary_key: str) -> str:
    return {
        "max_demo_position_notional_usdt": "demo position-notional cap",
        "max_live_position_notional_usdt": "live position-notional cap",
        "max_open_positions_demo": "demo open-position cap",
        "max_open_positions_live": "live open-position cap",
        "allow_short_demo": "demo short-selling guard",
        "allow_short_live": "live short-selling guard",
        "max_daily_loss_pct": "daily-loss cap",
        "max_total_live_exposure_pct": "total live exposure cap",
        "max_single_trade_risk_pct": "live single-trade risk cap",
        "max_demo_single_trade_risk_pct": "demo single-trade risk cap",
        "max_futures_leverage_live": "live futures leverage cap",
        "max_futures_leverage_demo": "demo futures leverage cap",
    }.get(boundary_key, boundary_key)


def _format_boundary_value(value):
    if isinstance(value, bool):
        return "enabled" if value else "disabled"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def _risk_policy_identity(candidate: dict) -> tuple | None:
    """Return an immutable mode-policy identity or None for legacy/partial evidence."""
    risk_mode = str(candidate.get("risk_mode") or "")
    policy_id = str(candidate.get("policy_id") or "")
    policy_hash = str(candidate.get("policy_hash") or "")
    try:
        policy_revision = int(candidate.get("policy_revision"))
    except (TypeError, ValueError):
        return None
    if risk_mode not in {"demo", "live"} or not policy_id or not policy_hash:
        return None
    return risk_mode, policy_id, policy_revision, policy_hash


def _risk_boundary_scope(candidate: dict, boundary_key: str | None = None) -> tuple | None:
    """Return the exact mode-policy boundary represented by a proposal candidate."""
    identity = _risk_policy_identity(candidate)
    key = str(boundary_key or candidate.get("boundary_key") or "")
    if identity is None or not key:
        return None
    return (*identity, key)


def _finalize_risk_boundary_summary(candidate: dict) -> dict:
    label = _risk_boundary_label(str(candidate.get("boundary_key") or "risk boundary"))
    risk_mode = str(candidate.get("risk_mode") or "")
    mode_label = f"{risk_mode} " if risk_mode else ""
    current_value = candidate.get("current_value")
    suggested_value = candidate.get("suggested_value")
    count = int(candidate.get("rejection_count") or 0)
    if isinstance(suggested_value, bool):
        action = "enable" if suggested_value else "change"
        candidate["summary"] = (
            f"Recent {mode_label}risk rejections hit the {label} {count} times and should create a human-reviewed proposal to "
            f"{action} {candidate.get('boundary_key')}."
        )
    else:
        candidate["summary"] = (
            f"Recent {mode_label}risk rejections hit the {label} {count} times and should create a human-reviewed proposal to "
            f"raise {candidate.get('boundary_key')} from {_format_boundary_value(current_value)} to "
            f"{_format_boundary_value(suggested_value)}."
        )
    return candidate


def _risk_boundary_change_from_violation(violation: str, proposal: dict | None, cfg) -> dict | None:
    proposal_mode = str((proposal or {}).get("execution_mode") or "")
    if not proposal_mode:
        return None

    try:
        from aletheia.risk.policy import resolve_risk_policy

        resolved = resolve_risk_policy(cfg, proposal_mode)
    except Exception:
        # A recommendation without an authoritative mode policy would point at
        # an ambiguous legacy field.  Keep this proposal-only surface fail closed.
        return None

    risk = resolved.legacy_view(cfg.risk)

    def candidate(
        *,
        boundary_key: str,
        violation_kind: str,
        current_value,
        suggested_value,
    ) -> dict:
        return {
            "boundary_key": boundary_key,
            "violation_kind": violation_kind,
            "risk_mode": resolved.mode,
            "policy_id": resolved.policy.policy_id,
            "policy_revision": resolved.policy.revision,
            "policy_hash": resolved.policy_hash,
            "current_value": current_value,
            "suggested_value": suggested_value,
            "changes": {boundary_key: suggested_value},
        }

    def effective_capital_base() -> float:
        try:
            from aletheia.risk.engine import _resolve_capital_base

            value, _ = _resolve_capital_base(proposal_mode, resolved.policy, cfg)
            return float(value or 0.0)
        except Exception as exc:
            logger.warning("operator_recommendations: effective capital base unavailable (%s)", type(exc).__name__)
            return 0.0

    parsed = _parse_numeric_threshold_violation(
        violation,
        "exceeds_demo_position_margin:",
    )
    if parsed is not None:
        if resolved.mode != "demo":
            return None
        actual, _ = parsed
        current = float(risk.max_demo_position_margin_usdt or 0.0)
        suggested = _round_up(max(actual, current), 2)
        if suggested <= current:
            return None
        return candidate(
            boundary_key="max_demo_position_margin_usdt",
            violation_kind="exceeds_demo_position_margin",
            current_value=current,
            suggested_value=suggested,
        )

    parsed = _parse_numeric_threshold_violation(
        violation,
        "exceeds_max_position_margin:",
    )
    if parsed is not None:
        if resolved.mode != "live":
            return None
        actual, _ = parsed
        current = float(risk.max_live_position_margin_usdt or 0.0)
        suggested = _round_up(max(actual, current), 2)
        if suggested <= current:
            return None
        return candidate(
            boundary_key="max_live_position_margin_usdt",
            violation_kind="exceeds_max_position_margin",
            current_value=current,
            suggested_value=suggested,
        )

    parsed = _parse_numeric_threshold_violation(
        violation,
        "max_total_live_exposure_exceeded:",
    )
    if parsed is not None:
        if resolved.mode != "live":
            return None
        projected, _ = parsed
        capital_base = effective_capital_base()
        current = float(risk.max_total_live_exposure_pct or 0.0)
        if capital_base <= 0:
            return None
        suggested = _round_up(max(current, projected / capital_base * 100.0), 2)
        if suggested <= current:
            return None
        return candidate(
            boundary_key="max_total_live_exposure_pct",
            violation_kind="max_total_live_exposure_exceeded",
            current_value=current,
            suggested_value=suggested,
        )

    parsed = _parse_numeric_threshold_violation(
        violation,
        "exceeds_single_trade_risk:",
    )
    if parsed is not None:
        if resolved.mode != "live":
            return None
        risk_amount, _ = parsed
        capital_base = effective_capital_base()
        current = float(risk.max_single_trade_risk_pct or 0.0)
        if capital_base <= 0:
            return None
        suggested = _round_up(max(current, risk_amount / capital_base * 100.0), 2)
        if suggested <= current:
            return None
        return candidate(
            boundary_key="max_single_trade_risk_pct",
            violation_kind="exceeds_single_trade_risk",
            current_value=current,
            suggested_value=suggested,
        )

    parsed = _parse_numeric_threshold_violation(
        violation,
        "exceeds_demo_single_trade_risk:",
    )
    if parsed is not None:
        if resolved.mode != "demo":
            return None
        risk_amount, _ = parsed
        capital_base = effective_capital_base()
        current = float(risk.max_demo_single_trade_risk_pct or 0.0)
        if capital_base <= 0:
            return None
        suggested = _round_up(max(current, risk_amount / capital_base * 100.0), 2)
        if suggested <= current:
            return None
        return candidate(
            boundary_key="max_demo_single_trade_risk_pct",
            violation_kind="exceeds_demo_single_trade_risk",
            current_value=current,
            suggested_value=suggested,
        )

    parsed = _parse_daily_loss_violation(violation)
    if parsed is not None:
        realized, _ = parsed
        capital_base = effective_capital_base()
        current = float(risk.max_daily_loss_pct or 0.0)
        if capital_base <= 0:
            return None
        suggested = _round_up(max(current, abs(realized) / capital_base * 100.0), 2)
        if suggested <= current:
            return None
        return candidate(
            boundary_key="max_daily_loss_pct",
            violation_kind="max_daily_loss_exceeded",
            current_value=current,
            suggested_value=suggested,
        )

    parsed = _parse_numeric_threshold_violation(
        violation,
        "exceeds_max_leverage:",
    )
    if parsed is not None:
        actual, _ = parsed
        is_live = resolved.mode == "live"
        boundary_key = "max_futures_leverage_live" if is_live else "max_futures_leverage_demo"
        current = int(
            risk.max_futures_leverage_live if is_live else risk.max_futures_leverage_demo
        )
        suggested = max(current, math.ceil(actual))
        if suggested <= current:
            return None
        return candidate(
            boundary_key=boundary_key,
            violation_kind="exceeds_max_leverage",
            current_value=current,
            suggested_value=suggested,
        )

    if violation.startswith("max_live_positions_reached:"):
        if resolved.mode != "live":
            return None
        try:
            reached = int(violation.split(":", 1)[1])
        except ValueError:
            return None
        current = int(risk.max_open_positions_live or 0)
        suggested = max(current, reached + 1)
        if suggested <= current:
            return None
        return candidate(
            boundary_key="max_open_positions_live",
            violation_kind="max_live_positions_reached",
            current_value=current,
            suggested_value=suggested,
        )

    if violation.startswith("max_demo_positions_reached:"):
        if resolved.mode != "demo":
            return None
        try:
            reached = int(violation.split(":", 1)[1])
        except ValueError:
            return None
        current = int(risk.max_open_positions_demo or 0)
        suggested = max(current, reached + 1)
        if suggested <= current:
            return None
        return candidate(
            boundary_key="max_open_positions_demo",
            violation_kind="max_demo_positions_reached",
            current_value=current,
            suggested_value=suggested,
        )

    if violation == "short_not_allowed_live":
        if resolved.mode != "live":
            return None
        current = bool(risk.allow_short_live)
        if current:
            return None
        return candidate(
            boundary_key="allow_short_live",
            violation_kind="short_not_allowed_live",
            current_value=current,
            suggested_value=True,
        )

    if violation == "short_not_allowed_demo":
        if resolved.mode != "demo":
            return None
        current = bool(risk.allow_short_demo)
        if current:
            return None
        return candidate(
            boundary_key="allow_short_demo",
            violation_kind="short_not_allowed_demo",
            current_value=current,
            suggested_value=True,
        )

    return None


def build_risk_summary(
    limit: int = 20,
    *,
    cfg=None,
    recent_artifacts: list[dict] | None = None,
) -> dict:
    cfg = cfg or load_config()
    cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    recent_rejected_decisions: list[dict] = []
    for decision in repo.list_risk_decisions(limit=200):
        if bool(decision.get("approved")):
            continue
        created_at = parse_utc(decision.get("created_at"))
        if created_at is None or created_at <= cutoff_24h:
            continue
        proposal = repo.get_trade_proposal(str(decision.get("proposal_id") or ""))
        recent_rejected_decisions.append(
            {
                "id": decision.get("id"),
                "proposal_id": decision.get("proposal_id"),
                "created_at": decision.get("created_at"),
                "violations": _parse_json_list(decision.get("violations")),
                "message": decision.get("message"),
                "symbol": proposal.get("symbol") if proposal else None,
                "execution_mode": proposal.get("execution_mode") if proposal else None,
                "strategy_id": proposal.get("strategy_id") if proposal else None,
                "risk_mode": decision.get("risk_mode"),
                "policy_id": decision.get("policy_id"),
                "policy_revision": decision.get("policy_revision"),
                "policy_hash": decision.get("policy_hash"),
            }
        )

    recent_risk_boundary_proposals: list[dict] = []
    recent_proposed_boundary_scopes: set[tuple] = set()
    artifacts = recent_artifacts if recent_artifacts is not None else list_artifacts(limit=500)
    for artifact in artifacts:
        if artifact.get("artifact_type") != "risk_boundary_change_proposal":
            continue
        created_at = parse_utc(artifact.get("created_at"))
        if created_at is None or created_at <= cutoff_24h:
            continue
        content = artifact.get("content") or {}
        if not isinstance(content, dict):
            continue
        changes = content.get("changes")
        changes = changes if isinstance(changes, dict) else {}
        boundary_keys = sorted(str(key) for key in changes.keys())
        for boundary_key in boundary_keys:
            scope = _risk_boundary_scope(content, boundary_key)
            if scope is not None:
                recent_proposed_boundary_scopes.add(scope)
        recent_risk_boundary_proposals.append(
            {
                "id": artifact.get("id"),
                "title": artifact.get("title"),
                "created_at": artifact.get("created_at"),
                "risk_mode": content.get("risk_mode"),
                "policy_id": content.get("policy_id"),
                "policy_revision": content.get("policy_revision"),
                "policy_hash": content.get("policy_hash"),
                "changes": changes,
                "boundary_keys": boundary_keys,
            }
        )

    aggregated: dict[tuple, dict] = {}
    for decision in recent_rejected_decisions:
        proposal = repo.get_trade_proposal(str(decision.get("proposal_id") or ""))
        created_at = parse_utc(decision.get("created_at"))
        for violation in decision.get("violations") or []:
            violation_text = str(violation)
            candidate = _risk_boundary_change_from_violation(violation_text, proposal, cfg)
            if candidate is None:
                continue
            # Boundary proposals may only aggregate rejections evaluated under
            # the exact current policy.  Historical/legacy decisions stay visible
            # above but cannot be reinterpreted after a policy revision.
            if _risk_policy_identity(decision) != _risk_policy_identity(candidate):
                continue
            boundary_key = str(candidate["boundary_key"])
            scope = _risk_boundary_scope(candidate)
            if scope is None:
                continue
            bucket = aggregated.get(scope)
            if bucket is None:
                bucket = {
                    **candidate,
                    "rejection_count": 0,
                    "sample_violations": [],
                    "decision_ids": [],
                    "affected_symbols": [],
                    "execution_modes": [],
                    "latest_created_at": decision.get("created_at"),
                }
                aggregated[scope] = bucket
            bucket["rejection_count"] += 1
            violations: list[str] = bucket["sample_violations"]
            if violation_text not in violations and len(violations) < 3:
                violations.append(violation_text)
            decision_ids: list[str] = bucket["decision_ids"]
            decision_id = decision.get("id")
            if decision_id and decision_id not in decision_ids:
                decision_ids.append(decision_id)
            symbols: list[str] = bucket["affected_symbols"]
            symbol = decision.get("symbol")
            if symbol and symbol not in symbols:
                symbols.append(symbol)
            modes: list[str] = bucket["execution_modes"]
            execution_mode = decision.get("execution_mode")
            if execution_mode and execution_mode not in modes:
                modes.append(execution_mode)
            if (
                isinstance(candidate.get("suggested_value"), (int, float))
                and isinstance(bucket.get("suggested_value"), (int, float))
                and float(candidate["suggested_value"]) > float(bucket["suggested_value"])
            ):
                bucket["suggested_value"] = candidate["suggested_value"]
                bucket["changes"] = candidate["changes"]
            if created_at and (
                parse_utc(bucket.get("latest_created_at")) is None
                or created_at > parse_utc(bucket.get("latest_created_at"))
            ):
                bucket["latest_created_at"] = decision.get("created_at")

    due_boundary_changes = [
        _finalize_risk_boundary_summary(candidate)
        for candidate in aggregated.values()
        if int(candidate.get("rejection_count") or 0) >= 2
        and _risk_boundary_scope(candidate) not in recent_proposed_boundary_scopes
    ]
    due_boundary_changes.sort(
        key=lambda item: (
            int(item.get("rejection_count") or 0),
            str(item.get("latest_created_at") or ""),
        ),
        reverse=True,
    )

    from aletheia.uta.manager import DEFAULT_UNIFIED_EXECUTION_MODES

    symbol_list_canonical = {to_canonical(symbol) for symbol in cfg.risk.symbol_list}
    symbol_rejections: dict[str, dict] = {}
    for decision in recent_rejected_decisions:
        symbol = to_canonical(str(decision.get("symbol") or ""))
        execution_mode = str(decision.get("execution_mode") or "").strip().lower()
        if not symbol or execution_mode not in DEFAULT_UNIFIED_EXECUTION_MODES:
            continue
        # In whitelist mode, only consider symbols currently in the list.
        # In blacklist mode, only consider symbols NOT in the blocked list.
        if cfg.risk.symbol_filter_mode == "whitelist":
            if symbol not in symbol_list_canonical:
                continue
            if len(symbol_list_canonical) <= 1:
                continue
        else:
            if symbol in symbol_list_canonical:
                continue
        bucket = symbol_rejections.get(symbol)
        if bucket is None:
            bucket = {
                "symbol": symbol,
                "rejection_count": 0,
                "decision_ids": [],
                "execution_modes": [],
                "latest_created_at": decision.get("created_at"),
            }
            symbol_rejections[symbol] = bucket
        bucket["rejection_count"] += 1
        decision_ids: list[str] = bucket["decision_ids"]
        decision_id = decision.get("id")
        if decision_id and decision_id not in decision_ids:
            decision_ids.append(decision_id)
        modes: list[str] = bucket["execution_modes"]
        if execution_mode and execution_mode not in modes:
            modes.append(execution_mode)
        created_at = parse_utc(decision.get("created_at"))
        if created_at and (
            parse_utc(bucket.get("latest_created_at")) is None
            or created_at > parse_utc(bucket.get("latest_created_at"))
        ):
            bucket["latest_created_at"] = decision.get("created_at")

    due_observe_only_symbols = [
        {
            "symbol": candidate["symbol"],
            "rejection_count": candidate["rejection_count"],
            "decision_ids": candidate["decision_ids"][:limit],
            "execution_modes": candidate["execution_modes"][:limit],
            "summary": (
                f"{candidate['symbol']} triggered {candidate['rejection_count']} recent external Demo risk rejections "
                "and should be left observe-only until setup quality improves."
            ),
            "latest_created_at": candidate["latest_created_at"],
        }
        for candidate in symbol_rejections.values()
        if int(candidate.get("rejection_count") or 0) >= 2
    ]
    due_observe_only_symbols.sort(
        key=lambda item: (
            int(item.get("rejection_count") or 0),
            str(item.get("latest_created_at") or ""),
        ),
        reverse=True,
    )

    return {
        "recent_rejected_decisions": recent_rejected_decisions[:limit],
        "recent_risk_boundary_proposals": recent_risk_boundary_proposals[:limit],
        "needs_operator_risk_boundary_proposals": due_boundary_changes[:limit],
        "needs_operator_observe_only_symbols": due_observe_only_symbols[:limit],
    }


def _command_center_requested_behavior(command_name: str, sample_response: str | None) -> str:
    label = command_name.replace("_", " ").strip() or "this flow"
    response = (sample_response or "").lower()
    if (
        "requires operator permission mode" in response
        or "requires worker permission mode" in response
        or "worker/global safety" in response
        or "permission denied" in response
    ):
        return (
            f"Add an inline permission gate and clearer next-action affordance for `{command_name}` in Command Center, "
            f"so repeated denied attempts to {label} surface the required mode before the user retries."
        )
    if "unknown command" in response:
        return (
            f"Add a more discoverable affordance or alias around `{command_name}` in Command Center, "
            f"so repeated attempts to {label} do not fail as unknown commands."
        )
    return (
        f"Improve Command Center behavior around `{command_name}` so repeated failed attempts to {label} "
        f"surface clearer guidance and a better next action."
    )


def build_command_center_behavior_summary(
    limit: int = 20,
    *,
    cfg=None,
    recent_artifacts: list[dict] | None = None,
) -> dict:
    cfg = cfg or load_config()
    cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    command_session_ids = {
        str(session.get("id"))
        for session in list_sessions(limit=500)
        if str(session.get("kind") or "command_session") == "command_session"
    }
    recent_denied_commands: list[dict] = []
    rejections_by_command: dict[str, list[dict]] = {}

    for command in list_commands(limit=200):
        if bool(command.get("permitted")):
            continue
        created_at = parse_utc(command.get("created_at"))
        if created_at is None or created_at <= cutoff_24h:
            continue
        session_id = str(command.get("session_id") or "")
        if session_id not in command_session_ids:
            continue
        recent_denied_commands.append(command)
        command_name = str(command.get("command") or "").strip()
        if not command_name:
            continue
        rejections_by_command.setdefault(command_name, []).append(command)

    recent_behavior_proposals: list[dict] = []
    proposed_commands: set[str] = set()
    artifacts = recent_artifacts if recent_artifacts is not None else list_artifacts(limit=500)
    for artifact in artifacts:
        if artifact.get("artifact_type") != "command_center_behavior_proposal":
            continue
        created_at = parse_utc(artifact.get("created_at"))
        if created_at is None or created_at <= cutoff_24h:
            continue
        content = artifact.get("content") or {}
        if not isinstance(content, dict):
            continue
        target_command = str(content.get("target_command") or "").strip()
        if target_command:
            proposed_commands.add(target_command)
        recent_behavior_proposals.append(
            {
                "id": artifact.get("id"),
                "title": artifact.get("title"),
                "created_at": artifact.get("created_at"),
                "target_command": target_command or None,
                "requested_behavior": content.get("requested_behavior"),
            }
        )

    due_behavior_changes: list[dict] = []
    for command_name, rows in rejections_by_command.items():
        if len(rows) < 2 or command_name in proposed_commands:
            continue
        sample_responses = []
        sample_inputs = []
        session_ids = []
        for row in rows[:5]:
            response = str(row.get("result_summary") or "").strip()
            user_input = str(row.get("session_user_input") or "").strip()
            session_id = str(row.get("session_id") or "").strip()
            if response and response not in sample_responses and len(sample_responses) < 3:
                sample_responses.append(response)
            if user_input and user_input not in sample_inputs and len(sample_inputs) < 3:
                sample_inputs.append(user_input)
            if session_id and session_id not in session_ids:
                session_ids.append(session_id)
        requested_behavior = _command_center_requested_behavior(
            command_name,
            sample_responses[0] if sample_responses else None,
        )
        due_behavior_changes.append(
            {
                "target_command": command_name,
                "rejection_count": len(rows),
                "summary": (
                    f"Command Center rejected `{command_name}` {len(rows)} recent times and should get a "
                    "human-reviewed behavior proposal instead of making the operator rediscover the same failure."
                ),
                "requested_behavior": requested_behavior,
                "sample_responses": sample_responses,
                "sample_inputs": sample_inputs,
                "session_ids": session_ids[:5],
                "command_ids": [str(row.get("id")) for row in rows[:5] if row.get("id")],
            }
        )

    due_behavior_changes.sort(
        key=lambda item: (
            int(item.get("rejection_count") or 0),
            str(item.get("target_command") or ""),
        ),
        reverse=True,
    )
    return {
        "recent_denied_commands": recent_denied_commands[:limit],
        "recent_command_center_behavior_proposals": recent_behavior_proposals[:limit],
        "needs_command_center_behavior_proposals": due_behavior_changes[:limit],
    }


def _make_task_recommendation(
    id_: str,
    kind: str,
    severity: str,
    summary: str,
    task_type: str,
    metadata: dict,
) -> dict:
    """Canonical shape for actionable operator recommendations."""
    return {
        "id": id_,
        "kind": kind,
        "severity": severity,
        "summary": summary,
        "taskType": task_type,
        "metadata": dict(metadata),
    }


def _missing_review_strategy_ids(
    recent_post_trade_reviews: list[dict],
    recent_closed_positions: list[dict],
) -> list[str]:
    # Position-level coverage: a strategy is covered only if the eligible
    # Position's exact ID appears in an active review's tags (#269).
    reviewed_position_ids: set[str] = set()
    for item in recent_post_trade_reviews:
        try:
            tags = json.loads(item.get("tags") or "[]")
        except (TypeError, ValueError):
            continue
        if not isinstance(tags, list):
            continue
        reviewed_position_ids.update(
            str(tag).split(":", 1)[1]
            for tag in tags
            if str(tag).startswith("position:")
        )
    return sorted(
        {
            str(position.get("strategy_id"))
            for position in recent_closed_positions
            if position.get("strategy_id")
            and str(position.get("id")) not in reviewed_position_ids
        }
    )


def _group_agent_runs(
    recent_agent_runs: list[dict],
    failed_run_statuses: set[str],
) -> tuple[dict[str, list[dict]], dict[str, list[dict]], dict[str, list[dict]]]:
    recent_runs_by_agent: dict[str, list[dict]] = {}
    failed_runs_by_agent: dict[str, list[dict]] = {}
    for run in recent_agent_runs:
        agent_name = str(run.get("agent_name") or "").strip()
        if agent_name:
            recent_runs_by_agent.setdefault(agent_name, []).append(run)
        if run.get("status") not in failed_run_statuses:
            continue
        started_at = parse_utc(run.get("started_at"))
        if started_at is None or started_at <= datetime.now(timezone.utc) - timedelta(hours=24):
            continue
        if not agent_name:
            continue
        failed_runs_by_agent.setdefault(agent_name, []).append(run)

    manual_runs_by_agent: dict[str, list[dict]] = {}
    for run in recent_agent_runs:
        if str(run.get("trigger_type") or "") != "manual":
            continue
        agent_name = str(run.get("agent_name") or "").strip()
        if not agent_name:
            continue
        manual_runs_by_agent.setdefault(agent_name, []).append(run)

    return recent_runs_by_agent, failed_runs_by_agent, manual_runs_by_agent


def _build_portfolio_snapshot_recommendation(
    cfg,
    open_positions: list[dict],
    latest_portfolio_snapshot: dict | None,
) -> dict | None:
    portfolio_snapshot_age_minutes = (
        _run_age_minutes({"started_at": latest_portfolio_snapshot.get("snapshot_at"), "ended_at": None})
        if latest_portfolio_snapshot is not None
        else None
    )
    if (
        not cfg.runtime.paused
        and open_positions
        and (
            latest_portfolio_snapshot is None
            or (
                portfolio_snapshot_age_minutes is not None
                and portfolio_snapshot_age_minutes >= 15
            )
        )
    ):
        return _make_task_recommendation(
            id_="portfolio-snapshot-refresh-due",
            kind="portfolio_snapshot_refresh_due",
            severity="medium",
            summary=(
                "Open positions exist but no recent portfolio snapshot is visible. Refresh portfolio state for operator review."
                if latest_portfolio_snapshot is None
                else (
                    "Open positions exist and the latest portfolio snapshot is about "
                    f"{int(portfolio_snapshot_age_minutes)} minutes old. Refresh portfolio state for operator review."
                )
            ),
            task_type="run_portfolio_snapshot",
            metadata={
                "task_type": "run_portfolio_snapshot",
                "open_positions_count": len(open_positions),
                "last_snapshot_id": (
                    latest_portfolio_snapshot.get("id")
                    if latest_portfolio_snapshot
                    else None
                ),
                "stale_minutes": (
                    int(portfolio_snapshot_age_minutes)
                    if portfolio_snapshot_age_minutes is not None
                    else None
                ),
            },
        )
    return None


def _build_command_center_recommendation(
    cfg,
    command_center_summary: dict,
) -> dict | None:
    due_command_center_behavior_changes = command_center_summary[
        "needs_command_center_behavior_proposals"
    ]
    if not cfg.runtime.paused and due_command_center_behavior_changes:
        first_candidate = due_command_center_behavior_changes[0]
        return _make_task_recommendation(
            id_=f"command-center-behavior:{first_candidate['target_command']}",
            kind="command_center_behavior_change_proposal_due",
            severity="medium",
            summary=first_candidate["summary"],
            task_type="create_repair_proposal",
            metadata={
                "target_command": first_candidate["target_command"],
                "rejection_count": first_candidate["rejection_count"],
                "requested_behavior": first_candidate["requested_behavior"],
                "sample_responses": first_candidate["sample_responses"],
                "sample_inputs": first_candidate["sample_inputs"],
                "session_ids": first_candidate["session_ids"],
                "command_ids": first_candidate["command_ids"],
                "proposal_command": "propose_command_center_behavior_change",
            },
        )
    return None


def _build_extension_enablement_recommendation(cfg) -> dict | None:
    passed_extensions = list_extensions(status="sandbox_passed", limit=20)
    if not cfg.runtime.paused and passed_extensions:
        ready_extensions = []
        for extension in passed_extensions:
            if extension.get("proposal_artifact_id"):
                continue
            latest_run = next(
                iter(list_extension_runs(extension_id=str(extension["id"]), limit=1)),
                None,
            )
            if latest_run is None or str(latest_run.get("status") or "") != "passed":
                continue
            ready_extensions.append((extension, latest_run))

        if ready_extensions:
            extension, latest_run = ready_extensions[0]
            slash_command = str(extension.get("slash_command") or extension["id"])
            return _make_task_recommendation(
                id_=f"extension-enable:{extension['id']}",
                kind="extension_enablement_proposal_due",
                severity="medium",
                summary=(
                    f"Extension {slash_command} passed sandbox validation and is ready "
                    "for a human-reviewed enablement proposal."
                ),
                task_type="create_repair_proposal",
                metadata={
                    "extension_id": extension["id"],
                    "extension_name": extension.get("extension_name"),
                    "slash_command": extension.get("slash_command"),
                    "target_command": extension.get("target_command"),
                    "latest_sandbox_run_id": latest_run.get("id"),
                    "latest_sandbox_status": latest_run.get("status"),
                    "latest_sandbox_summary": latest_run.get("summary"),
                    "proposal_command": "propose_extension_enablement",
                },
            )
    return None


def _build_daily_review_recommendation(
    cfg,
    recent_agent_runs: list[dict],
) -> dict | None:
    target_date = datetime.now(timezone.utc).date().isoformat()
    start_of_target_date = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()
    recent_daily_reviews = repo._select_all(
        "knowledge_items",
        where="item_type = ? AND created_at >= ?",
        params=("daily_review", start_of_target_date),
        limit=10,
    )
    recent_activity_run_count = sum(
        1
        for run in recent_agent_runs
        if (
            (parse_utc(run.get("ended_at") or run.get("started_at"))
             or datetime.min.replace(tzinfo=timezone.utc))
            > datetime.now(timezone.utc) - timedelta(hours=12)
        )
    )
    if (
        not cfg.runtime.paused
        and not recent_daily_reviews
        and recent_activity_run_count > 0
    ):
        return _make_task_recommendation(
            id_=f"daily-review-refresh:{target_date}",
            kind="daily_review_refresh_due",
            severity="medium",
            summary=f"No daily review has been written for {target_date} UTC. Refresh the non-critical operator summary report.",
            task_type="create_daily_review",
            metadata={
                "task_type": "create_daily_review",
                "target_date": target_date,
                "recent_activity_run_count": recent_activity_run_count,
                "title": f"Operator Daily Review {target_date}",
            },
        )
    return None


def _build_agent_performance_recommendation(
    cfg,
    recent_agent_runs: list[dict],
    failed_run_statuses: set[str],
    recent_artifacts: list[dict],
) -> dict | None:
    recent_activity_run_count = sum(
        1
        for run in recent_agent_runs
        if (
            (parse_utc(run.get("ended_at") or run.get("started_at"))
             or datetime.min.replace(tzinfo=timezone.utc))
            > datetime.now(timezone.utc) - timedelta(hours=12)
        )
    )
    recent_failed_run_count = sum(
        1
        for run in recent_agent_runs
        if str(run.get("status") or "") in failed_run_statuses
        and (
            (parse_utc(run.get("ended_at") or run.get("started_at"))
             or datetime.min.replace(tzinfo=timezone.utc))
            > datetime.now(timezone.utc) - timedelta(hours=12)
        )
    )
    if (
        not cfg.runtime.paused
        and (recent_failed_run_count > 0 or recent_activity_run_count >= 5)
        and not _has_fresh_report_artifact(
            recent_artifacts,
            artifact_type="review_report",
            report_type="agent_performance",
            within_minutes=360,
        )
    ):
        return _make_task_recommendation(
            id_="agent-performance-report-refresh",
            kind="agent_performance_report_refresh_due",
            severity="medium",
            summary=(
                "Recent agent activity warrants a fresh agent-performance report for operator review."
            ),
            task_type="create_review_report",
            metadata={
                "task_type": "create_review_report",
                "report_type": "agent_performance",
                "title": "Agent Performance Report",
                "recent_activity_run_count": recent_activity_run_count,
                "failed_run_count": recent_failed_run_count,
                "refresh_window_minutes": 360,
            },
        )
    return None


def _build_task_backlog_recommendations(
    failed_tasks: list[dict],
    running_tasks: list[dict],
    queued_tasks: list[dict],
) -> list[dict]:
    recommendations: list[dict] = []
    if failed_tasks:
        recommendations.append(
            {
                "id": "failed-tasks",
                "kind": "failed_tasks_present",
                "severity": "high",
                "summary": f"{len(failed_tasks)} failed operator task(s) require review.",
                "task_ids": [task["id"] for task in failed_tasks[:5]],
            }
        )

    stuck_tasks = [task for task in running_tasks if _is_stuck_task(task)]
    if stuck_tasks:
        recommendations.append(
            {
                "id": "stuck-tasks",
                "kind": "stuck_tasks_present",
                "severity": "high",
                "summary": (
                    "1 running operator task appears stuck and should be reviewed before trusting shell progress."
                    if len(stuck_tasks) == 1
                    else f"{len(stuck_tasks)} running operator tasks appear stuck and should be reviewed before trusting shell progress."
                ),
                "task_ids": [task["id"] for task in stuck_tasks[:5]],
            }
        )

    if queued_tasks:
        recommendations.append(
            {
                "id": "queued-tasks",
                "kind": "queued_tasks_present",
                "severity": "medium",
                "summary": (
                    "1 queued operator task is waiting in backlog review."
                    if len(queued_tasks) == 1
                    else f"{len(queued_tasks)} queued operator tasks are waiting in backlog review."
                ),
                "task_ids": [task["id"] for task in queued_tasks[:5]],
            }
        )

        queue_review = _queue_review_policy(queued_tasks)
        if queue_review:
            recommendations.append(
                {
                    "id": "queue-review-needed",
                    "kind": "queue_review_needed",
                    "severity": queue_review["severity"],
                    "summary": (
                        "Queued operator backlog needs queue review "
                        f"({queue_review['review_stage']}): {' and '.join(queue_review['reasons'])}."
                    ),
                    "task_ids": [task["id"] for task in queued_tasks[:5]],
                    "queued_count": len(queued_tasks),
                    "oldest_age_minutes": queue_review["oldest_age_minutes"],
                    "review_stage": queue_review["review_stage"],
                    "suggested_review_cadence_sec": queue_review[
                        "suggested_review_cadence_sec"
                    ],
                    "high_priority_queued_count": queue_review[
                        "high_priority_queued_count"
                    ],
                    "oldest_high_priority_age_minutes": queue_review[
                        "oldest_high_priority_age_minutes"
                    ],
                }
            )

    return recommendations


def _build_health_report_recommendations(
    health_snapshot: dict,
    recent_artifacts: list[dict],
) -> list[dict]:
    health_issues = [
        str(issue)
        for issue in (health_snapshot.get("issues") or [])
        if str(issue) != "trading_paused"
    ]
    recommendations: list[dict] = []

    if health_issues and not _has_fresh_report_artifact(
        recent_artifacts,
        artifact_type="diagnostic_report",
        report_type="system_health",
        within_minutes=60,
    ):
        recommendations.append(
            _make_task_recommendation(
                id_="system-health-report-refresh",
                kind="system_health_report_refresh_due",
                severity="medium",
                summary=(
                    "Gateway health is degraded and no recent system-health diagnostic report exists. "
                    "Refresh a diagnostic report for operator follow-up."
                ),
                task_type="create_diagnostic_report",
                metadata={
                    "task_type": "create_diagnostic_report",
                    "report_type": "system_health",
                    "title": "System Health Diagnostic Report",
                    "health_issues": health_issues,
                    "refresh_window_minutes": 60,
                },
            )
        )

    worker_health_issues = [
        issue for issue in health_issues if issue.startswith("agent_failure_ratio:")
    ]
    if worker_health_issues and not _has_fresh_report_artifact(
        recent_artifacts,
        artifact_type="diagnostic_report",
        report_type="worker_health",
        within_minutes=60,
    ):
        recommendations.append(
            _make_task_recommendation(
                id_="worker-health-review",
                kind="worker_health_review_due",
                severity="medium",
                summary=(
                    "Worker health is degraded and no recent worker-health diagnostic report exists. "
                    "Refresh a diagnostic report for operator follow-up."
                ),
                task_type="create_diagnostic_report",
                metadata={
                    "task_type": "create_diagnostic_report",
                    "report_type": "worker_health",
                    "title": "Worker Health Diagnostic Report",
                    "health_issues": worker_health_issues,
                    "refresh_window_minutes": 60,
                },
            )
        )

    model_runtime_health_issues = [
        issue
        for issue in health_issues
        if issue.startswith("llm_unavailable:")
        or issue.startswith("code_updated_pending_restart:")
    ]
    if model_runtime_health_issues and not _has_fresh_report_artifact(
        recent_artifacts,
        artifact_type="diagnostic_report",
        report_type="model_runtime_health",
        within_minutes=60,
    ):
        recommendations.append(
            _make_task_recommendation(
                id_="model-runtime-health-review",
                kind="model_runtime_health_review_due",
                severity="medium",
                summary=(
                    "Model/runtime health is degraded and no recent model-runtime diagnostic report exists. "
                    "Refresh a diagnostic report for operator follow-up."
                ),
                task_type="create_diagnostic_report",
                metadata={
                    "task_type": "create_diagnostic_report",
                    "report_type": "model_runtime_health",
                    "title": "Model Runtime Health Diagnostic Report",
                    "health_issues": model_runtime_health_issues,
                    "refresh_window_minutes": 60,
                },
            )
        )

    data_source_health_issues = [
        issue for issue in health_issues if issue.startswith("market_data_stale:")
    ]
    if data_source_health_issues and not _has_fresh_report_artifact(
        recent_artifacts,
        artifact_type="diagnostic_report",
        report_type="data_source_health",
        within_minutes=60,
    ):
        recommendations.append(
            _make_task_recommendation(
                id_="data-source-health-review",
                kind="data_source_health_review_due",
                severity="medium",
                summary=(
                    "Data-source health is degraded and no recent data-source diagnostic report exists. "
                    "Refresh a diagnostic report for operator follow-up."
                ),
                task_type="create_diagnostic_report",
                metadata={
                    "task_type": "create_diagnostic_report",
                    "report_type": "data_source_health",
                    "title": "Data Source Health Diagnostic Report",
                    "health_issues": data_source_health_issues,
                    "refresh_window_minutes": 60,
                },
            )
        )

    return recommendations


def _build_risk_recommendations(
    cfg,
    risk_summary: dict,
    recent_risk_critic_runs: list[dict],
    recent_artifacts: list[dict],
) -> list[dict]:
    recommendations: list[dict] = []

    if (
        not cfg.runtime.paused
        and risk_summary["recent_rejected_decisions"]
        and not recent_risk_critic_runs
    ):
        recommendations.append(
            _make_task_recommendation(
                id_="risk-critique-due",
                kind="risk_critique_due",
                severity="medium",
                summary=(
                    "Recent risk rejections exist without a fresh risk-critic pass. "
                    "Delegate risk_critic to record a new risk lesson note."
                ),
                task_type="run_risk_critique",
                metadata={
                    "task_type": "run_risk_critique",
                    "rejected_decision_count": len(risk_summary["recent_rejected_decisions"]),
                },
            )
        )

    if (
        not cfg.runtime.paused
        and risk_summary["recent_rejected_decisions"]
        and not _has_fresh_report_artifact(
            recent_artifacts,
            artifact_type="review_report",
            report_type="risk_review",
            within_minutes=360,
        )
    ):
        recommendations.append(
            _make_task_recommendation(
                id_="risk-review-report-refresh",
                kind="risk_review_report_refresh_due",
                severity="medium",
                summary=(
                    "Recent risk rejections exist and no fresh risk review report is visible. "
                    "Refresh a risk-focused operator summary."
                ),
                task_type="create_review_report",
                metadata={
                    "task_type": "create_review_report",
                    "report_type": "risk_review",
                    "title": "Risk Review Report",
                    "rejected_decision_count": len(risk_summary["recent_rejected_decisions"]),
                    "proposal_candidate_count": len(
                        risk_summary["needs_operator_risk_boundary_proposals"]
                    ),
                    "refresh_window_minutes": 360,
                },
            )
        )

    due_risk_boundary_changes = risk_summary["needs_operator_risk_boundary_proposals"]
    if not cfg.runtime.paused and due_risk_boundary_changes:
        first_candidate = due_risk_boundary_changes[0]
        recommendations.append(
            _make_task_recommendation(
                id_=(
                    "risk-boundary-proposal:"
                    f"{first_candidate['risk_mode']}:"
                    f"{first_candidate['policy_hash']}:"
                    f"{first_candidate['boundary_key']}"
                ),
                kind="risk_boundary_change_proposal_due",
                severity="medium",
                summary=first_candidate["summary"],
                task_type="create_repair_proposal",
                metadata={
                    "boundary_key": first_candidate["boundary_key"],
                    "violation_kind": first_candidate["violation_kind"],
                    "risk_mode": first_candidate["risk_mode"],
                    "policy_id": first_candidate["policy_id"],
                    "policy_revision": first_candidate["policy_revision"],
                    "policy_hash": first_candidate["policy_hash"],
                    "rejection_count": first_candidate["rejection_count"],
                    "changes": first_candidate["changes"],
                    "sample_violations": first_candidate["sample_violations"],
                    "decision_ids": first_candidate["decision_ids"][:5],
                    "affected_symbols": first_candidate["affected_symbols"][:5],
                    "execution_modes": first_candidate["execution_modes"],
                    "proposal_command": "propose_risk_boundary_change",
                },
            )
        )

    due_observe_only_symbols = risk_summary.get("needs_operator_observe_only_symbols") or []
    if not cfg.runtime.paused and due_observe_only_symbols:
        first_candidate = due_observe_only_symbols[0]
        recommendations.append(
            _make_task_recommendation(
                id_=f"symbol-observe-only:{first_candidate['symbol']}",
                kind="symbol_observe_only_due",
                severity="medium",
                summary=first_candidate["summary"],
                task_type="set_symbol_observe_only",
                metadata={
                    "task_type": "set_symbol_observe_only",
                    "symbol": first_candidate["symbol"],
                    "rejection_count": first_candidate["rejection_count"],
                    "decision_ids": first_candidate["decision_ids"][:5],
                    "execution_modes": first_candidate.get("execution_modes", [])[:5],
                },
            )
        )

    return recommendations


def _build_failed_agent_rerun_recommendation(
    cfg,
    recent_runs_by_agent: dict,
    failed_run_statuses: set[str],
) -> dict | None:
    failed_rerun_candidate = None
    for agent_name, runs in recent_runs_by_agent.items():
        sorted_runs = sorted(
            runs,
            key=lambda run: (
                parse_utc(run.get("ended_at") or run.get("started_at"))
                or datetime.min.replace(tzinfo=timezone.utc)
            ),
            reverse=True,
        )
        latest_run = sorted_runs[0] if sorted_runs else None
        if latest_run is None:
            continue
        if str(latest_run.get("status") or "") not in failed_run_statuses:
            continue
        if str(latest_run.get("trigger_type") or "") == "manual":
            continue
        latest_failure_age_minutes = _run_age_minutes(latest_run)
        if latest_failure_age_minutes is None or latest_failure_age_minutes < 15:
            continue
        failed_run_count = sum(
            1 for run in sorted_runs if str(run.get("status") or "") in failed_run_statuses
        )
        if failed_run_count >= 2:
            continue
        failed_rerun_candidate = (agent_name, latest_run, int(latest_failure_age_minutes))
        break

    if not cfg.runtime.paused and failed_rerun_candidate:
        agent_name, latest_failed_run, stale_minutes = failed_rerun_candidate
        return _make_task_recommendation(
            id_=f"failed-agent-rerun:{latest_failed_run['id']}",
            kind="failed_agent_rerun_due",
            severity="medium",
            summary=f"{agent_name} has a recent failed run with no newer recovery. Rerun the failed task to restore scheduled coverage.",
            task_type="rerun_failed_agent_task",
            metadata={
                "task_type": "rerun_failed_agent_task",
                "agent_name": agent_name,
                "failed_run_id": latest_failed_run["id"],
                "failed_run_status": latest_failed_run.get("status"),
                "stale_minutes": stale_minutes,
            },
        )
    return None


def build_recommendations(limit: int = 10) -> list[dict]:
    from aletheia.agent_runtime.health import compute_health

    cfg = load_config()
    recommendations: list[dict] = []
    heartbeat = get_latest_heartbeat()
    if heartbeat is not None:
        from aletheia.agent_shell.operator_store import apply_heartbeat_staleness

        heartbeat = apply_heartbeat_staleness(heartbeat)
    failed_tasks = list_tasks(limit=10, status="failed")
    running_tasks = list_tasks(limit=20, status="running")
    queued_tasks = list_tasks(limit=20, status="queued")
    recent_events = list_events(limit=100)
    recent_artifacts = list_artifacts(limit=500)

    if heartbeat is None or heartbeat.get("status") == "offline":
        recommendations.append(
            {
                "id": "operator-offline",
                "kind": "operator_offline",
                "severity": "high",
                "summary": "Operator shell is offline. Wake or restart it before relying on background supervision.",
            }
        )
    elif heartbeat.get("stale"):
        recommendations.append(
            {
                "id": "operator-stale",
                "kind": "heartbeat_stale",
                "severity": "high",
                "summary": "Operator heartbeat is stale. Inspect shell logs or let the supervisor restart it.",
            }
        )

    if cfg.runtime.paused:
        recommendations.append(
            {
                "id": "gateway-paused",
                "kind": "operator_paused",
                "severity": "medium",
                "summary": "Gateway is paused. Resume it when background monitoring and scheduled work should continue.",
            }
        )

    health_snapshot = compute_health()
    recommendations.extend(
        _build_health_report_recommendations(health_snapshot, recent_artifacts)
    )

    recommendations.extend(
        _build_task_backlog_recommendations(
            failed_tasks,
            running_tasks,
            queued_tasks,
        )
    )

    rejected_commands = [
        event for event in recent_events if event["event_type"] == "command_rejected"
    ]
    if rejected_commands:
        recommendations.append(
            {
                "id": "command-rejections",
                "kind": "command_rejections_present",
                "severity": "medium",
                "summary": "Recent operator commands were rejected. Review permission mode or command intent.",
                "count": len(rejected_commands),
            }
        )

    cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    cutoff_6h = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
    cutoff_15m = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()

    recent_dreaming_logs = repo._select_all(
        "knowledge_items",
        where="item_type = ? AND created_at > ?",
        params=("dreaming_log", cutoff_24h),
        limit=10,
    )
    recent_dreaming_runs = repo._select_all(
        "agent_runs",
        where="agent_name = ? AND started_at > ?",
        params=("dreaming_agent", cutoff_24h),
        limit=5,
    )
    recent_risk_critic_runs = repo._select_all(
        "agent_runs",
        where="agent_name = ? AND started_at > ?",
        params=("risk_critic", cutoff_6h),
        limit=5,
    )
    if not cfg.runtime.paused and not recent_dreaming_logs and not recent_dreaming_runs:
        recommendations.append(
            _make_task_recommendation(
                id_="dreaming-due",
                kind="dreaming_due",
                severity="medium",
                summary="No recent dreaming consolidation is visible. Queue a dreaming run to refresh operator memory.",
                task_type="run_dreaming",
                metadata={
                    "reason": "no_recent_dreaming_activity",
                },
            )
        )

    from aletheia.analytics.outcome_integrity import eligible_strategy_outcomes
    recent_closed_positions = list(reversed(
        eligible_strategy_outcomes(closed_after=cutoff_24h)
    ))[:20]
    # Exclude archived/deprecated reviews so they do not suppress regeneration
    # after an owner archives an invalid review (#269).
    recent_post_trade_reviews = repo._select_all(
        "knowledge_items",
        where="item_type = ? AND status NOT IN ('archived', 'deprecated') AND created_at > ?",
        params=("post_trade_review", cutoff_24h),
        limit=20,
    )
    recent_reviewer_runs = repo._select_all(
        "agent_runs",
        where="agent_name = ? AND started_at > ?",
        params=("post_trade_reviewer", cutoff_15m),
        limit=5,
    )
    missing_review_strategy_ids = _missing_review_strategy_ids(
        recent_post_trade_reviews,
        recent_closed_positions,
    )
    if not cfg.runtime.paused and missing_review_strategy_ids and not recent_reviewer_runs:
        recommendations.append(
            _make_task_recommendation(
                id_="review-due",
                kind="review_due",
                severity="medium",
                summary=(
                    f"Closed strategy {missing_review_strategy_ids[0]} still lacks a recent post-trade review."
                    if len(missing_review_strategy_ids) == 1
                    else f"{len(missing_review_strategy_ids)} closed strategies still lack recent post-trade reviews."
                ),
                task_type="generate_review",
                metadata={
                    "missing_review_strategy_ids": missing_review_strategy_ids,
                    "closed_position_count": len(recent_closed_positions),
                },
            )
        )

    if (
        not cfg.runtime.paused
        and recent_closed_positions
        and not _has_fresh_report_artifact(
            recent_artifacts,
            artifact_type="review_report",
            report_type="trade_review",
            within_minutes=360,
        )
    ):
        recommendations.append(
            _make_task_recommendation(
                id_="trade-review-report-refresh",
                kind="trade_review_report_refresh_due",
                severity="medium",
                summary=(
                    "Recent closed positions exist and no fresh trade review report is visible. "
                    "Refresh a non-trading trade review summary."
                ),
                task_type="create_review_report",
                metadata={
                    "task_type": "create_review_report",
                    "report_type": "trade_review",
                    "title": "Trade Review Report",
                    "closed_position_count": len(recent_closed_positions),
                    "missing_review_strategy_ids": missing_review_strategy_ids,
                    "refresh_window_minutes": 360,
                },
            )
        )

    recent_agent_runs = repo.list_agent_runs_window(hours=24)
    recent_run_ids = {str(run.get("id")) for run in recent_agent_runs}
    recent_agent_runs.extend(
        run
        for run in repo.list_latest_agent_runs()
        if str(run.get("id")) not in recent_run_ids
    )
    open_positions = repo.list_positions(status="open", limit=200)
    latest_portfolio_snapshot = repo.get_latest_portfolio_snapshot()
    result = _build_portfolio_snapshot_recommendation(
        cfg,
        open_positions,
        latest_portfolio_snapshot,
    )
    if result:
        recommendations.append(result)

    failed_run_statuses = {"failed", "error", "completed_with_errors"}
    (
        recent_runs_by_agent,
        failed_runs_by_agent,
        manual_runs_by_agent,
    ) = _group_agent_runs(recent_agent_runs, failed_run_statuses)

    result = _build_failed_agent_rerun_recommendation(
        cfg,
        recent_runs_by_agent,
        failed_run_statuses,
    )
    if result:
        recommendations.append(result)

    recent_prompt_change_proposals = []
    for artifact in recent_artifacts:
        if artifact.get("artifact_type") != "prompt_change_proposal":
            continue
        created_at = parse_utc(artifact.get("created_at"))
        if created_at is None or created_at <= datetime.now(timezone.utc) - timedelta(hours=24):
            continue
        content = artifact.get("content") or {}
        if not isinstance(content, dict):
            continue
        target = content.get("target")
        if target:
            recent_prompt_change_proposals.append(str(target))

    repeated_failed_agent = next(
        (
            (agent_name, runs)
            for agent_name, runs in failed_runs_by_agent.items()
            if len(runs) >= 2 and agent_name not in recent_prompt_change_proposals
        ),
        None,
    )
    if not cfg.runtime.paused and repeated_failed_agent:
        agent_name, runs = repeated_failed_agent
        error_samples: list[str] = []
        for run in runs:
            error_samples.extend(_parse_json_list(run.get("errors")))
        recommendations.append(
            _make_task_recommendation(
                id_=f"agent-prompt-proposal:{agent_name}",
                kind="agent_prompt_change_proposal_due",
                severity="medium",
                summary=f"{agent_name} failed {len(runs)} recent runs and should get a prompt/playbook change proposal for human review.",
                task_type="create_repair_proposal",
                metadata={
                    "agent_name": agent_name,
                    "target": agent_name,
                    "failed_run_ids": [run["id"] for run in runs[:5]],
                    "error_samples": error_samples[:3],
                    "proposal_command": "propose_prompt_change",
                },
            )
        )

    recent_schedule_change_proposals = []
    for artifact in recent_artifacts:
        if artifact.get("artifact_type") != "schedule_change_proposal":
            continue
        created_at = parse_utc(artifact.get("created_at"))
        if created_at is None or created_at <= datetime.now(timezone.utc) - timedelta(hours=24):
            continue
        content = artifact.get("content") or {}
        if not isinstance(content, dict):
            continue
        agent_name = content.get("agent_name")
        if agent_name:
            recent_schedule_change_proposals.append(str(agent_name))

    repeated_manual_agent = next(
        (
            (agent_name, runs)
            for agent_name, runs in manual_runs_by_agent.items()
            if len(runs) >= 2
            and agent_name not in recent_schedule_change_proposals
            and _default_schedule_for(agent_name)
            and _suggested_schedule_for(agent_name)
        ),
        None,
    )
    # Only propose schedule changes when the Operator can actually execute them.
    # In ReadOnly mode a create_repair_proposal task can never complete, so
    # generating this recommendation locks the Operator in Repairing indefinitely.
    _hb_permission = (heartbeat or {}).get("permission_mode", "")
    if not cfg.runtime.paused and repeated_manual_agent and _hb_permission != "ReadOnly":
        agent_name, runs = repeated_manual_agent
        recommendations.append(
            _make_task_recommendation(
                id_=f"agent-schedule-proposal:{agent_name}",
                kind="agent_schedule_change_proposal_due",
                severity="medium",
                summary=f"{agent_name} needed {len(runs)} recent manual runs on top of its scheduled cadence and should get a schedule-change proposal for human review.",
                task_type="create_repair_proposal",
                metadata={
                    "agent_name": agent_name,
                    "current_schedule": _default_schedule_for(agent_name),
                    "requested_schedule": _suggested_schedule_for(agent_name),
                    "manual_run_ids": [run["id"] for run in runs[:5]],
                    "proposal_command": "propose_schedule_change",
                },
            )
        )

    result = _build_agent_performance_recommendation(
        cfg,
        recent_agent_runs,
        failed_run_statuses,
        recent_artifacts,
    )
    if result:
        recommendations.append(result)

    result = _build_daily_review_recommendation(cfg, recent_agent_runs)
    if result:
        recommendations.append(result)

    risk_summary = build_risk_summary(cfg=cfg, recent_artifacts=recent_artifacts)
    recommendations.extend(
        _build_risk_recommendations(
            cfg,
            risk_summary,
            recent_risk_critic_runs,
            recent_artifacts,
        )
    )

    command_center_summary = build_command_center_behavior_summary(
        cfg=cfg,
        recent_artifacts=recent_artifacts,
    )
    result = _build_command_center_recommendation(cfg, command_center_summary)
    if result:
        recommendations.append(result)

    result = _build_extension_enablement_recommendation(cfg)
    if result:
        recommendations.append(result)

    archived_ids = _archived_recommendation_ids(cfg.operator.main_session_id)
    if archived_ids:
        recommendations = [
            item for item in recommendations if str(item.get("id")) not in archived_ids
        ]

    return recommendations[:limit]
