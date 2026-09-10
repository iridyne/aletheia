"""Declarative Exit Intent (ExitPlan) — the System Operator's per-position exit intent.

Central abstraction of the exit-intent plan: the AI authorizes stop / target /
horizon / trailing / thesis-invalidation as declarative parameters; the deterministic
executor enforces them (exchange bracket + local loop); the AI revises them on its
own cadence.

M1 = data layer only: read/write helpers + config fallback. It does NOT change any
executor behavior — ``effective_exit_plan`` reproduces the exact numbers that
``position_manager._effective_stop`` / ``_effective_take_profit`` return today, so the
M2 swap is behavior-preserving. M2 wires the executor to consume this; M3 adds the
``adjust_position`` action that bumps ``version`` and re-authors the plan.

Persistence — flat columns on ``positions`` (design decision D1 = B):
  stop_price               -> ExitPlan.stop_price            (hard level, reused)
  take_profit_price        -> ExitPlan.target_price          (hard level, reused)
  expected_horizon_hours   -> ExitPlan.expected_horizon_hours
  trailing_json            -> ExitPlan.trailing              (dict)
  thesis_invalidation_json -> ExitPlan.thesis_invalidation   (dict)
  stop_source              -> ExitPlan.provenance            ('operator' | 'default')
  exit_plan_version        -> ExitPlan.version               (int)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from aletheia.storage.models import EXIT_INTENT_STOP_SOURCES, exit_intent_violations

Provenance = Literal["operator", "default", "invalid"]


@dataclass
class ExitPlan:
    """One position's declarative exit intent. ``provenance='operator'`` means the AI
    authored it; ``'default'`` means config fallback is standing in and the position
    should be surfaced back to the Operator for a real decision (M2)."""

    stop_price: float | None = None
    target_price: float | None = None
    expected_horizon_hours: float | None = None
    trailing: dict | None = None
    thesis_invalidation: dict | None = None
    provenance: Provenance = "default"
    version: int = 0
    invalid_reasons: tuple[str, ...] = ()

    def to_columns(self) -> dict[str, Any]:
        """Serialize to the flat columns owned by the Position ExitPlan writer."""
        return {
            "stop_price": self.stop_price,
            "take_profit_price": self.target_price,
            "expected_horizon_hours": self.expected_horizon_hours,
            "trailing_json": json.dumps(self.trailing) if self.trailing is not None else None,
            "thesis_invalidation_json": (
                json.dumps(self.thesis_invalidation)
                if self.thesis_invalidation is not None
                else None
            ),
            "stop_source": self.provenance,
            "exit_plan_version": int(self.version),
        }


def _loads(value: Any) -> dict | None:
    """Parse a JSON column back to a dict; tolerate NULL / '' / bad JSON → None."""
    if not value:
        return None
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def load_exit_plan(position: dict) -> ExitPlan:
    """Build an ExitPlan from a raw position row as persisted (NO config fallback).

    Legacy rows (new columns absent/NULL) read back as ``provenance='default'``,
    ``version=0`` — never fabricated. Use :func:`effective_exit_plan` when the
    executor needs resolved stop/target with config defaults filled in.
    """
    raw_provenance = position.get("stop_source")
    violations = exit_intent_violations(
        expected_horizon_hours=position.get("expected_horizon_hours"),
        stop_source=raw_provenance if raw_provenance not in (None, "") else None,
    )
    provenance = raw_provenance or "default"
    version_raw = position.get("exit_plan_version")
    try:
        version = int(version_raw) if version_raw is not None else 0
    except (ValueError, TypeError):
        version = 0
    return ExitPlan(
        stop_price=_as_float(position.get("stop_price")) or None,
        target_price=_as_float(position.get("take_profit_price")) or None,
        expected_horizon_hours=(
            None
            if "invalid_expected_horizon_hours" in violations
            else _as_float(position.get("expected_horizon_hours"))
        ),
        trailing=_loads(position.get("trailing_json")),
        thesis_invalidation=_loads(position.get("thesis_invalidation_json")),
        provenance=(
            provenance
            if provenance in EXIT_INTENT_STOP_SOURCES
            else "invalid"
        ),
        version=version,
        invalid_reasons=tuple(violations),
    )


def save_exit_plan(
    position_id: str,
    plan: ExitPlan,
    *,
    exit_plan_updates: dict | None = None,
    observation_updates: dict | None = None,
) -> ExitPlan:
    """Atomically persist exit intent and related fields for an open exchange Position."""
    from aletheia.storage import repositories as repo
    from aletheia.uta.manager import UTA_EXECUTION_MODES

    position = repo.get_position(position_id)
    if position is None or position.get("status") != "open":
        raise ValueError("ExitPlan update requires an open Position")
    if position.get("execution_mode") not in UTA_EXECUTION_MODES:
        raise ValueError("unsupported position execution mode")
    with repo.transaction(immediate=True):
        repo.update_open_position_exit_plan(
            position_id, **{**plan.to_columns(), **(exit_plan_updates or {})}
        )
        if observation_updates:
            repo.update_open_position_observation(position_id, **observation_updates)
    return plan


def _resolve_stop(position: dict, cfg) -> tuple[float | None, bool]:
    """Return (effective stop, operator_authored). Mirrors ``_effective_stop`` exactly,
    including the native-proposal-protection check that prevents mutable config from
    inventing a stop that Risk never approved for a proposal-originated position."""
    stop = position.get("stop_price")
    if stop:
        return float(stop), True
    from aletheia.portfolio.position_manager import _native_proposal_protection
    governed, proposal_stop = _native_proposal_protection(position, "stop_price")
    if governed:
        return proposal_stop, proposal_stop is not None
    pct = cfg.trading.default_stop_loss_pct
    entry = position.get("entry_price")
    if not pct or not entry or float(entry) <= 0:
        return None, False
    entry = float(entry)
    if position.get("direction") in ("long", "buy"):
        return round(entry * (1 - pct / 100.0), 8), False
    return round(entry * (1 + pct / 100.0), 8), False


def _resolve_target(position: dict, cfg) -> tuple[float | None, bool]:
    """Return (effective target, operator_authored). Mirrors ``_effective_take_profit``
    exactly, including native-proposal-protection."""
    tp = position.get("take_profit_price")
    if tp:
        return float(tp), True
    from aletheia.portfolio.position_manager import _native_proposal_protection
    governed, proposal_tp = _native_proposal_protection(position, "take_profit")
    if governed:
        return proposal_tp, proposal_tp is not None
    pct = cfg.trading.default_take_profit_pct
    entry = position.get("entry_price")
    if not pct or not entry or float(entry) <= 0:
        return None, False
    entry = float(entry)
    if position.get("direction") in ("long", "buy"):
        return round(entry * (1 + pct / 100.0), 8), False
    return round(entry * (1 - pct / 100.0), 8), False


def effective_exit_plan(position: dict, cfg) -> ExitPlan:
    """Resolved ExitPlan for the executor: operator-authored levels where present,
    config defaults where absent. ``provenance='operator'`` iff the stored plan was
    operator-authored OR either hard level was explicitly set on the position.

    ``stop_price`` / ``target_price`` here equal ``_effective_stop`` /
    ``_effective_take_profit`` exactly, so M2 can swap the executor onto this without
    changing behavior.
    """
    base = load_exit_plan(position)
    stop, stop_op = _resolve_stop(position, cfg)
    target, target_op = _resolve_target(position, cfg)
    if base.invalid_reasons:
        return ExitPlan(
            stop_price=stop,
            target_price=target,
            expected_horizon_hours=base.expected_horizon_hours,
            trailing=base.trailing,
            thesis_invalidation=base.thesis_invalidation,
            provenance="invalid",
            version=base.version,
            invalid_reasons=base.invalid_reasons,
        )
    # Provenance: trust the stored stop_source when present (M2+ positions stamp it at
    # open, so a config-default stop persisted at open correctly stays 'default'); for
    # legacy rows with no stored marker, infer 'operator' from an explicit stop/target.
    stored = position.get("stop_source")
    if stored in ("operator", "default"):
        provenance: Provenance = "operator" if stored == "operator" else "default"
    else:
        provenance = "operator" if (stop_op or target_op) else "default"
    return ExitPlan(
        stop_price=stop,
        target_price=target,
        expected_horizon_hours=base.expected_horizon_hours,
        trailing=base.trailing,
        thesis_invalidation=base.thesis_invalidation,
        provenance=provenance,
        version=base.version,
    )
