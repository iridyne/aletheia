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
from typing import Callable, Literal

from aletheia.analytics.outcome_integrity import (
    eligible_strategy_outcomes,
)

logger = logging.getLogger("aletheia.trading.sizing_policy")

_learned_notional_resolver: Callable[..., dict] | None = None


def configure_sizing_evidence(resolver: Callable[..., dict] | None) -> None:
    global _learned_notional_resolver
    _learned_notional_resolver = resolver

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


def clean_close_count(
    *,
    anchor_at: str | None = None,
    rollout_id: str | None = None,
    persona_id: str | None = None,
    policy_epoch: str | None = None,
    parameter: str | None = None,
    parameter_state: str | None = None,
) -> int:
    """Count only genuine outcomes in a complete prospective evidence scope.

    The durable Persona state machine is the authority for unlocks.  This helper
    remains a read-only compatibility seam for diagnostics and older callers; an
    incomplete scope returns zero rather than turning a lifetime count into
    dynamic authority.
    """
    if not anchor_at or not rollout_id or not persona_id or not policy_epoch:
        return 0
    try:
        kwargs: dict[str, object] = {}
        kwargs["closed_after"] = anchor_at
        rows = eligible_strategy_outcomes(**kwargs)
        from aletheia.trading.persona_rollout import extract_sizing_manifest

        count = 0
        for row in rows:
            manifest = extract_sizing_manifest(row)
            if not manifest:
                continue
            if manifest.get("rollout_id") != rollout_id:
                continue
            if manifest.get("persona_id") != persona_id:
                continue
            if manifest.get("policy_epoch") != policy_epoch:
                continue
            if parameter is not None and manifest.get("parameter") != parameter:
                continue
            if parameter_state is not None and manifest.get("parameter_state") != parameter_state:
                continue
            count += 1
        return count
    except Exception:
        return 0


DistressAssessment = Literal["healthy", "distressed", "unavailable"]


def assess_persona_distress(cfg=None) -> DistressAssessment:
    """Assess whether drawdown evidence permits dynamic Persona authority."""
    try:
        from aletheia.config.loader import load_config

        cfg = cfg or load_config()
        if getattr(cfg.runtime, "kill_switch_active", False):
            return "distressed"
        mode = cfg.exchange.paper_execution_mode or ""
        from aletheia.risk.engine import _portfolio_drawdown_assessment

        scale, available = _portfolio_drawdown_assessment(mode, cfg)
        if not available:
            return "unavailable"
        return "distressed" if scale < 0.8 else "healthy"
    except Exception:
        return "unavailable"


def in_distress(cfg=None) -> bool:
    """Fail closed unless Persona has verified healthy drawdown evidence."""
    return assess_persona_distress(cfg) != "healthy"


def learned_appetite_adjustment(regime_bucket: str | None, cfg=None) -> float:
    """Reward-driven appetite nudge for the current context bucket, in [-MAX_ADJ, MAX_ADJ].

    Returns 0.0 (→ P2 rule-based fallback) when learning is disabled, the bucket is
    unknown, or clean samples are below the gate (soft self-refreeze). Positive bucket
    reward → nudge appetite up; negative → down (defensive)."""
    from aletheia.config.loader import load_config
    cfg = cfg or load_config()
    try:
        from aletheia.trust.learning_safety import learning_influence_allowed
        if not learning_influence_allowed():
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
    """Keep static Core sizing usable when optional evidence is unavailable."""
    baseline = float(baseline_notional)
    learned = float(learned_notional)
    no_delta = abs(learned - baseline) < 1e-9
    result = {
        "baseline_notional": baseline,
        "learned_notional": learned,
        "actual_notional": baseline,
        "authority_applied": False,
        "assignment": "baseline" if no_delta else "unavailable",
        "reason": "no_learned_delta" if no_delta else "sizing_learning_extension_unavailable",
        "hypothesis_id": None,
        "experiment_id": None,
        "exposure_id": None,
    }
    if _learned_notional_resolver is None:
        return result
    try:
        evidence = _learned_notional_resolver(
            baseline_notional=baseline, learned_notional=learned, symbol=symbol,
            direction=direction, regime_bucket=regime_bucket,
            candidate_key=candidate_key, outcome_candidate_key=outcome_candidate_key,
            opportunity_id=opportunity_id,
        )
        # Persona sizing is measurement-unsupported: evidence never grants size authority.
        if (
            evidence["baseline_notional"] != baseline
            or evidence["learned_notional"] != learned
            or evidence["actual_notional"] != baseline
            or evidence["authority_applied"] is not False
        ):
            raise ValueError("persona_sizing_authority_unsupported")
        return {key: evidence[key] for key in result}
    except Exception as exc:
        logger.warning("Sizing Learning adapter failed closed: %s", exc)
        return {**result, "assignment": "unavailable", "reason": "adapter_error"}


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
    # the immutable outer bound regardless of persona/pyramid state. Read through
    # the Risk Engine's own seam so this can never disagree with check #19, and
    # so the position's actual pool is used: this previously hardcoded the demo
    # policy, silently applying demo limits to a live position.
    from aletheia.risk.engine import per_symbol_notional_ceiling
    cap = per_symbol_notional_ceiling(
        str(position.get("execution_mode") or ""),
        leverage=float(position.get("leverage") or 1),
        cfg=cfg,
    )
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
