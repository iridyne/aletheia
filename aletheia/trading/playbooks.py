"""Seed starter playbooks on first run (guide §22.3 / §8)."""

from pathlib import Path

# Guide §22.3 — 5 starter playbooks (not fixed strategies; the agent turns these
# into paper candidates).
SEED_PLAYBOOKS: dict[str, str] = {
    "core_crypto_regime_observation": """# Core Crypto Regime Observation

Observe BTC/ETH/SOL across 5m–1d. Classify regime: trending up, trending down,
ranging, or high-volatility. Note funding, volume, and relative strength.
Output: a neutral market read, not a trade signal.
""",
    "trend_following_hypothesis": """# Trend Following Hypothesis

Hypothesis: in a confirmed trend, pullbacks to a moving average offer continuation
entries. Define: trend filter (e.g. EMA slope), entry trigger, invalidation, and
position sizing. Validate in paper before promotion.
""",
    "volatility_regime_hypothesis": """# Volatility Regime Hypothesis

Hypothesis: volatility clusters; expansion follows contraction. Detect squeezes
(e.g. narrowing range/ATR) and trade the breakout with a volatility-scaled stop.
Counterargument: false breakouts in chop. Validate in paper.
""",
    "funding_pressure_hypothesis": """# Funding Pressure Hypothesis

Hypothesis: extreme perp funding marks crowded positioning and mean-reversion risk.
Use funding extremes + price divergence to fade or reduce exposure. Spot vs perp
basis as confirmation. Validate in paper.
""",
    "breakout_or_reversal_hypothesis": """# Breakout or Reversal Hypothesis

Hypothesis: key levels (prior high/low, range edges) either break with momentum or
reject. Define how to distinguish, entry/exit per case, and risk. Validate in paper.
""",
}


def seed_playbooks(data_dir: Path) -> list[str]:
    """Write seed playbook .md files if absent. Returns names written."""
    pb_dir = data_dir / "playbooks"
    pb_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, content in SEED_PLAYBOOKS.items():
        path = pb_dir / f"{name}.md"
        if not path.exists():
            path.write_text(content, encoding="utf-8")
            written.append(name)
    return written
