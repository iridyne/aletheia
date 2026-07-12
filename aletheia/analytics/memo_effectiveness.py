"""Module J — Memo Effectiveness (refutable self-evolution).

The owner's core requirement: a later result must be able to OVERTURN an earlier
judgment. Without this, a confidently-wrong TradingMemo runs its full 72h, steers
a batch of trades the wrong way, then expires leaving no trace — the next memo
re-learns from scratch and can repeat the same mistake.

`evaluate_prior_memo()` closes that loop: before a new memo is synthesized, it
scores the PRIOR memo against what actually happened while it was active. For
each regime_playbook rule the prior memo asserted, it measures the ACTUAL win
rate of trades opened during the memo's window and compares it to the edge the
memo claimed. Rules whose prediction was contradicted become `overturned_rules`,
and an `accuracy_score` is written back onto the prior memo's tags — so a wrong
judgment leaves a durable trace the next memo (and the PI prompt) can learn from.

Pure analytics: reads positions + knowledge_items, writes back only the prior
memo's tags. No LLM, no imports from agents/risk/agent_shell (architecture
fitness: data flows knowledge → prompt, never the reverse).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from math import exp, log

from aletheia.analytics.outcome_integrity import (
    eligible_strategy_outcomes,
    is_current_outcome_artifact,
)

logger = logging.getLogger("aletheia.analytics.memo_effectiveness")

# A rule needs at least this many in-window trades before we judge its prediction.
MIN_EVAL_SAMPLE_PER_RULE = 3
MEMO_HALF_LIFE_DAYS = 7.0


def _norm_regime(value) -> str:
    return value or "unknown"


def memo_half_life_weight(generated_at: str | None, *, now: datetime | None = None, half_life_days: float = MEMO_HALF_LIFE_DAYS) -> float:
    """Return a 0..1 influence weight for a trading memo by age.

    A fresh memo has weight 1.0. Every half_life_days the influence halves, so
    stale regime guidance fades even when no explicit overturn signal exists.
    """
    if not generated_at:
        return 1.0
    try:
        ts = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
    except ValueError:
        return 1.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    age_days = max(0.0, (now - ts).total_seconds() / 86400.0)
    if half_life_days <= 0:
        return 0.0
    return round(exp(-log(2) * age_days / half_life_days), 4)


def evaluate_prior_memo() -> dict | None:
    """Score the most-recent persisted TradingMemo against subsequent outcomes.

    Returns a verdict dict ``{memo_id, accuracy_score, overturned_rules,
    n_trades, evaluated_at}`` (accuracy_score is None when no rule had enough
    in-window samples) or None when there is no prior memo to evaluate.

    Side effect: writes accuracy_score / overturned_count / evaluated_at back
    onto the prior memo's knowledge_items.tags.
    """
    try:
        from aletheia.storage.repositories import list_knowledge_items, update_knowledge_item

        items = list_knowledge_items(item_type="trading_memo", limit=1)
        if not items:
            return None
        prior = items[0]
        memo_id = prior.get("id")
        try:
            memo = json.loads(prior.get("content") or "{}")
        except (json.JSONDecodeError, TypeError):
            return None
        if not is_current_outcome_artifact(memo, max_age=timedelta(hours=72)):
            return None
        generated_at = memo.get("generated_at")
        if not memo_id or not generated_at:
            return None

        now_iso = datetime.now(timezone.utc).isoformat()

        # Trades the memo could have influenced: opened at/after it was formed.
        rows = [
            row for row in eligible_strategy_outcomes()
            if str(row.get("opened_at") or "") >= str(generated_at)
        ]

        verdict = {
            "memo_id": memo_id,
            "accuracy_score": None,
            "overturned_rules": [],
            "n_trades": len(rows),
            "evaluated_at": now_iso,
        }

        if rows:
            # Group in-window outcomes by (regime, direction).
            from collections import defaultdict
            grouped: dict[tuple, list[bool]] = defaultdict(list)
            for r in rows:
                key = (_norm_regime(r.get("regime_tag")), r.get("direction"))
                grouped[key].append((r.get("realized_pnl") or 0) > 0)

            correct = 0
            judged = 0
            overturned: list[dict] = []
            for rule in memo.get("regime_playbook", []):
                regime = _norm_regime(rule.get("regime"))
                direction = rule.get("direction")
                edge = rule.get("edge")
                wins = grouped.get((regime, direction), [])
                if len(wins) < MIN_EVAL_SAMPLE_PER_RULE:
                    continue
                actual_wr = sum(wins) / len(wins)

                if edge == "strong":
                    judged += 1
                    if actual_wr >= 0.5:
                        correct += 1
                    elif actual_wr < 0.4:
                        overturned.append({
                            "regime": regime, "direction": direction,
                            "claimed_edge": "strong",
                            "actual_win_rate": round(actual_wr * 100, 1),
                            "sample": len(wins),
                            "reason": "claimed strong edge, subsequent trades show weak",
                        })
                elif edge == "contra":
                    judged += 1
                    if actual_wr <= 0.45:
                        correct += 1
                    elif actual_wr > 0.55:
                        overturned.append({
                            "regime": regime, "direction": direction,
                            "claimed_edge": "contra",
                            "actual_win_rate": round(actual_wr * 100, 1),
                            "sample": len(wins),
                            "reason": "claimed contra, subsequent trades were favorable",
                        })
                # edge == "weak": neutral claim, not scored.

            verdict["overturned_rules"] = overturned
            verdict["accuracy_score"] = round(correct / judged, 3) if judged else None

        # Write the verdict back onto the prior memo's tags (durable trace).
        try:
            tags = json.loads(prior.get("tags") or "{}")
            if not isinstance(tags, dict):
                tags = {}
        except (json.JSONDecodeError, TypeError):
            tags = {}
        tags["accuracy_score"] = verdict["accuracy_score"]
        tags["overturned_count"] = len(verdict["overturned_rules"])
        tags["evaluated_at"] = now_iso
        tags["eval_trades"] = verdict["n_trades"]
        try:
            update_knowledge_item(memo_id, tags=json.dumps(tags, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001 — write-back is best-effort
            logger.warning("memo_effectiveness tag write-back failed: %s", e)

        logger.info(
            "Evaluated prior memo %s: accuracy=%s, %d overturned rule(s), %d in-window trades",
            memo_id, verdict["accuracy_score"], len(verdict["overturned_rules"]), verdict["n_trades"],
        )
        return verdict
    except Exception as e:  # noqa: BLE001 — evaluation must never break synthesis
        logger.warning("evaluate_prior_memo failed: %s", e)
        return None
