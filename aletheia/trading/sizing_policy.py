"""Persona learning (P3): the Operator's sizing decision space as a learnable policy.

Design: docs/design/operator/dynamic_sizing_operator_persona_design.md §7/§8/§10-P3.

A deliberately SIMPLE, SAFE first learner (owner's "早期实验性阶段…温和策略"): per
CONTEXT BUCKET (the market regime the position was opened in, `positions.regime_tag`)
it measures the historical CLEAN-WINDOW risk-adjusted reward and nudges the sizing
`risk_appetite` toward what has worked — up in buckets that paid, down in buckets
that bled. This is a contextual-bandit reward signal at coarse (regime) granularity.

Hard safety invariants (all four keep the autonomy bounded — VISION trust bet):
  1. DORMANT by default: returns 0.0 (→ P2 rule-based fallback) unless
     `persona_learning_enabled`.
  2. CLEAN DATA ONLY: reward is computed from genuine closes — synthetic/administrative
     closes (reconciled_gone etc.) are excluded (the reconciler-thrash lesson: dirty
     data poisons learning). Positions with NULL realized_pnl are skipped.
  3. SAMPLE GATE: no nudge until a bucket has >= persona_learning_min_samples clean
     closes (self-refreeze on thin data).
  4. BOUNDED: the nudge is clamped to [-MAX_ADJ, +MAX_ADJ]; the risk-engine hard caps
     remain the outer envelope regardless of what the learner returns.

NOTE (data dependency): bucketing is by `positions.regime_tag`, which is currently
sparsely populated (many NULL). Until regime_tag propagation improves, most buckets
stay under the sample gate and the learner correctly no-ops. That is the intended
fail-safe, not a bug — the learner activates only where it has enough clean evidence.
"""
from __future__ import annotations

import logging

from aletheia.analytics.outcome_integrity import (
    eligible_strategy_outcomes,
)

logger = logging.getLogger("aletheia.trading.sizing_policy")

MAX_ADJ = 0.30           # max |appetite| nudge the learner may apply
_REWARD_SCALE_PCT = 2.0  # a bucket mean of +2% realized → full +MAX_ADJ (tanh-like via clamp)
_WINDOW = 200            # look back at most this many recent clean closes per bucket


def _clean_bucket_rewards(regime_bucket: str) -> list[float]:
    """Realized-pnl-% of GENUINE closed positions opened in this regime bucket.

    Excludes synthetic/administrative closes and NULL-pnl rows. Reward is per-trade
    realized_pnl as a fraction of entry notional (risk-normalized across sizes)."""
    try:
        rows = [
            row for row in reversed(eligible_strategy_outcomes())
            if row.get("regime_tag") == regime_bucket
        ][:_WINDOW]
    except Exception as e:
        logger.debug("sizing_policy reward query failed: %s", e)
        return []
    out: list[float] = []
    for r in rows:
        notional = float(r.get("entry_price") or 0) * float(r.get("quantity") or 0)
        if notional <= 0:
            continue
        out.append(float(r.get("realized_pnl") or 0) / notional * 100.0)  # pct
    return out


def clean_close_count() -> int:
    """Total count of GENUINE closed positions (synthetic/administrative excluded).

    Drives the progressive-unlock gate (P4): params unfreeze as clean evidence
    accrues. Read-only; 0 on error."""
    try:
        return len(eligible_strategy_outcomes())
    except Exception:
        return 0


def in_distress(cfg=None) -> bool:
    """True when the account is in drawdown or the kill switch is active — the
    progressive-unlock gate refreezes ALL params in distress."""
    from aletheia.config.loader import load_config
    cfg = cfg or load_config()
    try:
        if getattr(cfg.runtime, "kill_switch_active", False):
            return True
        from aletheia.risk.engine import _portfolio_drawdown_scale
        mode = cfg.exchange.paper_execution_mode or "paper_internal"
        return _portfolio_drawdown_scale(mode) < 0.8
    except Exception:
        return False


def learned_appetite_adjustment(regime_bucket: str | None, cfg=None) -> float:
    """Reward-driven appetite nudge for the current context bucket, in [-MAX_ADJ, MAX_ADJ].

    Returns 0.0 (→ P2 rule-based fallback) when learning is disabled, the bucket is
    unknown, or clean samples are below the gate (soft self-refreeze). Positive bucket
    reward → nudge appetite up; negative → down (defensive)."""
    from aletheia.config.loader import load_config
    cfg = cfg or load_config()
    try:
        from aletheia.learning.control import learning_influence_enabled
        if not learning_influence_enabled():
            return 0.0
    except Exception:
        return 0.0
    if not getattr(cfg.trading, "persona_learning_enabled", False):
        return 0.0
    if not regime_bucket:
        return 0.0
    rewards = _clean_bucket_rewards(regime_bucket)
    min_n = int(getattr(cfg.trading, "persona_learning_min_samples", 15))
    if len(rewards) < min_n:
        return 0.0
    mean_r = sum(rewards) / len(rewards)
    adj = mean_r / _REWARD_SCALE_PCT * MAX_ADJ
    return max(-MAX_ADJ, min(MAX_ADJ, adj))


def resolve_learned_notional(
    *,
    baseline_notional: float,
    learned_notional: float,
    symbol: str,
    direction: str | None,
    regime_bucket: str | None,
    candidate_key: str | None,
    outcome_candidate_key: str | None,
    opportunity_id: str | None,
) -> dict:
    """Prospectively expose a learned size and return an auditable decision.

    Historical persona rewards only propose ``learned_notional``. The actual size
    stays at ``baseline_notional`` unless the common Learning experiment adapter
    authorizes this paper treatment. Missing causal keys therefore fail closed.
    """
    from aletheia.learning.experiments import apply_numeric_treatment

    baseline = float(baseline_notional)
    learned = float(learned_notional)
    if abs(learned - baseline) < 1e-9:
        from aletheia.learning.experiments import InfluenceDecision
        decision = InfluenceDecision(baseline, False, "baseline", "no_learned_delta")
    else:
        try:
            decision = apply_numeric_treatment(
                path_id="persona_sizing",
                scope={
                    "symbol": symbol,
                    "direction": direction,
                    "regime": regime_bucket,
                },
                action={"component": "base_notional", "method": "persona_reward_nudge"},
                source_cohort=f"persona_regime:{regime_bucket or 'unknown'}",
                target_horizon="4h",
                candidate_key=candidate_key if outcome_candidate_key else None,
                baseline=baseline,
                treatment=learned,
                consumer="paper_sizing_policy",
                consumer_stage="notional",
                outcome_candidate_key=outcome_candidate_key,
                opportunity_id=opportunity_id,
                manifest={
                    "baseline_notional": baseline,
                    "learned_notional": learned,
                    "regime_bucket": regime_bucket,
                },
            )
        except Exception as exc:
            logger.warning("Sizing Learning adapter failed closed: %s", exc)
            from aletheia.learning.experiments import InfluenceDecision
            decision = InfluenceDecision(baseline, False, "unavailable", "adapter_error")
    return {
        "baseline_notional": baseline,
        "learned_notional": learned,
        "actual_notional": float(decision.value),
        "authority_applied": bool(decision.applied),
        "assignment": decision.assignment,
        "reason": decision.reason,
        "hypothesis_id": decision.hypothesis_id,
        "experiment_id": decision.experiment_id,
        "exposure_id": decision.exposure_id,
    }


def pyramid_plan(position: dict, adds_done: int = 0, cfg=None) -> dict:
    """Decide whether/how to scale into a WINNING position (P4 pyramid).

    Pure DECISION only (no order placement). Returns
      {"add": bool, "add_notional": float, "reason": str}.

    Gates (all must hold, else add=False): persona_pyramid_enabled; active persona has
    max_adds>0 and add_fraction>0; adds_done < max_adds; the position is a winner past
    persona_pyramid_add_trigger_pct of unrealized profit. The add is then HARD-CAPPED
    so base_notional + all adds stays within the risk engine's per-symbol exposure
    limit (roll uses high leverage → exposure discipline is the whole safety story)."""
    from aletheia.config.loader import load_config
    cfg = cfg or load_config()
    t = cfg.trading
    if not getattr(t, "persona_pyramid_enabled", False):
        return {"add": False, "add_notional": 0.0, "reason": "pyramid disabled"}
    if not getattr(t, "persona_sizing_enabled", False):
        return {"add": False, "add_notional": 0.0, "reason": "persona sizing disabled"}
    persona = (t.personas or {}).get(t.active_persona)
    if persona is None:
        return {"add": False, "add_notional": 0.0, "reason": "no active persona"}
    max_adds = int(persona.params.get("max_adds").init) if "max_adds" in persona.params else 0
    add_frac = float(persona.params.get("add_fraction").init) if "add_fraction" in persona.params else 0.0
    if max_adds <= 0 or add_frac <= 0:
        return {"add": False, "add_notional": 0.0, "reason": "persona has no pyramiding"}
    if adds_done >= max_adds:
        return {"add": False, "add_notional": 0.0, "reason": f"max_adds reached ({max_adds})"}

    entry = float(position.get("entry_price") or 0)
    qty = float(position.get("quantity") or 0)
    base_notional = entry * qty
    if base_notional <= 0:
        return {"add": False, "add_notional": 0.0, "reason": "no base notional"}
    upnl = float(position.get("unrealized_pnl") or 0)
    profit_pct = (upnl / base_notional * 100.0) if base_notional else 0.0
    if profit_pct < float(getattr(t, "persona_pyramid_add_trigger_pct", 1.0)):
        return {"add": False, "add_notional": 0.0,
                "reason": f"not enough profit ({profit_pct:.2f}%) to add"}

    desired = base_notional * add_frac
    already_added = base_notional * add_frac * adds_done

    # CAP 1 (owner 2026-07-02): TOTAL pyramided ≤ 1× base_notional (so total exposure
    # ≤ ~2× base). This is the primary pyramid discipline — winners scale, but bounded.
    total_room = max(0.0, base_notional - already_added)
    add_notional = min(desired, total_room)

    # CAP 2 (risk envelope): base + adds must fit the per-symbol exposure limit —
    # the immutable outer bound regardless of persona/pyramid state.
    cap = float(cfg.risk.demo.capital.max_per_symbol_notional_usdt or 0)
    if cap > 0:
        room = max(0.0, cap - (base_notional + already_added))
        add_notional = min(add_notional, room)

    if add_notional <= 0:
        return {"add": False, "add_notional": 0.0,
                "reason": "pyramid cap reached (total≤1x base and per-symbol exposure)"}
    return {"add": True, "add_notional": round(add_notional, 4),
            "reason": f"winner +{profit_pct:.2f}%, add {adds_done + 1}/{max_adds} (total≤1x base)"}


def sizing_policy_health(cfg=None) -> dict:
    """Observability for the doctor probe: per-regime clean sample counts + mean
    reward + the resulting appetite nudge. Read-only, never raises."""
    from aletheia.config.loader import load_config
    cfg = cfg or load_config()
    enabled = bool(getattr(cfg.trading, "persona_learning_enabled", False))
    min_n = int(getattr(cfg.trading, "persona_learning_min_samples", 15))
    buckets: dict[str, dict] = {}
    for b in ("bull", "bear", "sideways", "volatile"):
        rewards = _clean_bucket_rewards(b)
        n = len(rewards)
        mean_r = (sum(rewards) / n) if n else None
        buckets[b] = {
            "clean_samples": n,
            "mean_reward_pct": round(mean_r, 4) if mean_r is not None else None,
            "active": enabled and n >= min_n,
            "appetite_adj": round(learned_appetite_adjustment(b, cfg), 4) if enabled else 0.0,
        }
    return {"enabled": enabled, "min_samples": min_n, "buckets": buckets}
