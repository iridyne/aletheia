"""Persona learning (P3): the Operator's sizing decision space as a learnable policy.

Design: docs/design/dynamic_sizing_operator_persona_design.md §7/§8/§10-P3.

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

logger = logging.getLogger("aletheia.trading.sizing_policy")

MAX_ADJ = 0.30           # max |appetite| nudge the learner may apply
_REWARD_SCALE_PCT = 2.0  # a bucket mean of +2% realized → full +MAX_ADJ (tanh-like via clamp)
_WINDOW = 200            # look back at most this many recent clean closes per bucket


def _clean_bucket_rewards(regime_bucket: str) -> list[float]:
    """Realized-pnl-% of GENUINE closed positions opened in this regime bucket.

    Excludes synthetic/administrative closes and NULL-pnl rows. Reward is per-trade
    realized_pnl as a fraction of entry notional (risk-normalized across sizes)."""
    from aletheia.storage.sqlite import get_db
    from aletheia.analytics.close_reasons import is_synthetic_close
    try:
        db = get_db()
        rows = db.execute(
            "SELECT realized_pnl, entry_price, quantity, close_reason "
            "FROM positions WHERE status='closed' AND regime_tag=? "
            "AND realized_pnl IS NOT NULL ORDER BY closed_at DESC LIMIT ?",
            (regime_bucket, _WINDOW),
        ).fetchall()
    except Exception as e:
        logger.debug("sizing_policy reward query failed: %s", e)
        return []
    out: list[float] = []
    for r in rows:
        if is_synthetic_close(r["close_reason"]):
            continue
        notional = float(r["entry_price"] or 0) * float(r["quantity"] or 0)
        if notional <= 0:
            continue
        out.append(float(r["realized_pnl"] or 0) / notional * 100.0)  # pct
    return out


def learned_appetite_adjustment(regime_bucket: str | None, cfg=None) -> float:
    """Reward-driven appetite nudge for the current context bucket, in [-MAX_ADJ, MAX_ADJ].

    Returns 0.0 (→ P2 rule-based fallback) when learning is disabled, the bucket is
    unknown, or clean samples are below the gate (soft self-refreeze). Positive bucket
    reward → nudge appetite up; negative → down (defensive)."""
    from aletheia.config.loader import load_config
    cfg = cfg or load_config()
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
