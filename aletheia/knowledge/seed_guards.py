"""Seed active guards — written on first run, never overwrite existing files."""

from pathlib import Path

from aletheia.config.loader import get_data_dir
from aletheia.knowledge.frontmatter import render_frontmatter

_SEED_GUARDS = [
    {
        "meta": {
            "type": "Trading Guard",
            "guard_id": "late_entry_chase_v1",
            "title": "Late Entry Chase Guard",
            "description": "Penalize mature moves that are likely to be chase entries.",
            "status": "active",
            "scope": "paper_entry",
            "enforcement": "scoring",
            "severity": "warning",
            "score_penalty": 20,
            "block_candidate": False,
            "applies_to": {"asset_class": ["crypto"]},
            "predicates": [{"field": "late_entry_penalty", "op": "gte", "value": 20}],
            "evidence_refs": [],
            "tags": ["late-entry", "chase", "paper-entry"],
        },
        "body": (
            "## Rationale\n\n"
            "Late-entry opportunities often look attractive after the easy move has already happened.\n"
            "This guard reduces attention waste and prevents chasing mature moves.\n"
        ),
        "filename": "seed-late-entry-chase-v1.md",
    },
    {
        "meta": {
            "type": "Trading Guard",
            "guard_id": "wide_spread_cost_v1",
            "title": "Wide Spread Cost Guard",
            "description": "Penalize symbols with wide spreads that erode edge.",
            "status": "active",
            "scope": "paper_entry",
            "enforcement": "scoring",
            "severity": "warning",
            "score_penalty": 15,
            "block_candidate": False,
            "applies_to": {"asset_class": ["crypto"]},
            "predicates": [{"field": "risk_flags", "op": "contains", "value": "wide_spread"}],
            "evidence_refs": [],
            "tags": ["wide-spread", "cost", "paper-entry"],
        },
        "body": (
            "## Rationale\n\n"
            "Wide spreads silently eat into edge. A 25bps spread on a 2% target\n"
            "consumes 12.5% of the expected profit on each leg.\n"
        ),
        "filename": "seed-wide-spread-cost-v1.md",
    },
    {
        "meta": {
            "type": "Trading Guard",
            "guard_id": "repeated_losing_factor_v1",
            "title": "Repeated Losing Factor Guard",
            "description": "Block factors already classified as blocked_candidate by factor feedback.",
            "status": "active",
            "scope": "paper_entry",
            "enforcement": "blocking",
            "severity": "critical",
            "score_penalty": 0,
            "block_candidate": True,
            "applies_to": {"asset_class": ["crypto"]},
            "predicates": [{"field": "factor_status", "op": "eq", "value": "blocked_candidate"}],
            "evidence_refs": [],
            "tags": ["factor-feedback", "negative-expectancy", "paper-entry"],
        },
        "body": (
            "## Rationale\n\n"
            "Factors with repeated losses and negative expectancy have already been flagged\n"
            "by the factor feedback system. This guard makes that block explicit and auditable.\n"
        ),
        "filename": "seed-repeated-losing-factor-v1.md",
    },
    {
        "meta": {
            "type": "Trading Guard",
            "guard_id": "no_ruleset_trade_penalty_v1",
            "title": "No RuleSet Trade Penalty",
            "description": "Penalize strategy-linked trades when the strategy has no executable RuleSet.",
            "status": "active",
            "scope": "paper_entry",
            "enforcement": "scoring",
            "severity": "warning",
            "score_penalty": 20,
            "block_candidate": False,
            "applies_to": {"asset_class": ["crypto"]},
            "predicates": [{"field": "strategy_has_ruleset", "op": "eq", "value": False}],
            "evidence_refs": [],
            "tags": ["ruleset", "strategy-quality", "paper-entry"],
        },
        "body": (
            "## Rationale\n\n"
            "A strategy without a compiled RuleSet relies solely on LLM signals,\n"
            "which cannot be backtested or verified deterministically.\n"
        ),
        "filename": "seed-no-ruleset-trade-penalty-v1.md",
    },
    {
        "meta": {
            "type": "Trading Guard",
            "guard_id": "backtest_false_positive_v1",
            "title": "Backtest False Positive Guard",
            "description": "Block strategies whose paper results contradict backtest promise.",
            "status": "active",
            "scope": "paper_entry",
            "enforcement": "blocking",
            "severity": "critical",
            "score_penalty": 0,
            "block_candidate": True,
            "applies_to": {"asset_class": ["crypto"]},
            "predicates": [{"field": "strategy_drift_status", "op": "eq", "value": "backtest_false_positive"}],
            "evidence_refs": [],
            "tags": ["backtest", "false-positive", "drift", "paper-entry"],
        },
        "body": (
            "## Rationale\n\n"
            "When paper trading consistently loses money on a strategy that backtested well,\n"
            "the backtest result is likely overfitted. Block further paper entries until reviewed.\n"
        ),
        "filename": "seed-backtest-false-positive-v1.md",
    },
]


def ensure_seed_guards() -> list[str]:
    """Write seed guard files if they don't exist. Returns list of created filenames."""
    guards_dir = get_data_dir() / "knowledge" / "guards"
    guards_dir.mkdir(parents=True, exist_ok=True)

    created = []
    for seed in _SEED_GUARDS:
        path = guards_dir / seed["filename"]
        if path.exists():
            continue
        content = render_frontmatter(seed["meta"], seed["body"])
        path.write_text(content, encoding="utf-8")
        created.append(seed["filename"])

    return created
