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

from aletheia.analytics.execution_mode import resolve_selected_execution_mode
from aletheia.analytics.outcome_integrity import (
    is_current_outcome_artifact,
    read_complete_outcome_cohort,
)
from aletheia.storage.sqlite import iso_instant_us

logger = logging.getLogger("aletheia.analytics.memo_effectiveness")

# A rule needs at least this many in-window trades before we judge its prediction.
MIN_EVAL_SAMPLE_PER_RULE = 3


def _norm_regime(value) -> str:
    return value or "unknown"


def evaluate_prior_memo(
    *, for_rotation: bool = False, execution_mode: str | None = None,
) -> dict | None:
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
        max_age = timedelta(days=30) if for_rotation else timedelta(hours=72)
        if not is_current_outcome_artifact(memo, max_age=max_age):
            return None
        if memo.get("cohort_complete") is not True or not memo.get(
            "ordered_source_digest"
        ):
            return None
        generated_at = memo.get("generated_at")
        if not memo_id or not generated_at:
            return None
        generated_at_us = iso_instant_us(generated_at)
        if generated_at_us is None:
            return None

        now_iso = datetime.now(timezone.utc).isoformat()

        # Trades the memo could have influenced: opened at/after it was formed.
        # Scope the effectiveness join to the memo's own ``execution_mode`` so a
        # memo formed under the selected cohort can never be overturned or
        # confirmed by another cohort's outcomes (#1939).  Fallback to the
        # currently-resolved selected mode when the caller passes nothing and
        # the memo record itself has none (legacy artifact).
        memo_execution_mode = (
            execution_mode
            or memo.get("execution_mode")
            or resolve_selected_execution_mode()
        )
        if not memo_execution_mode:
            return None
        cohort = read_complete_outcome_cohort(execution_mode=memo_execution_mode)
        if not cohort.cohort_complete:
            return None
        rows = []
        for row, assessment in zip(
            cohort.rows, cohort.assessments, strict=True
        ):
            if not assessment.eligible:
                continue
            opened_at_us = iso_instant_us(row.get("opened_at"))
            if opened_at_us is None or opened_at_us < generated_at_us:
                continue
            # If a learning_manifest_json is available, verify memo exposure
            manifest_raw = row.get("learning_manifest_json") or row.get("decision_learning_manifest")
            if manifest_raw:
                try:
                    manifest = json.loads(manifest_raw) if isinstance(manifest_raw, str) else manifest_raw
                    if isinstance(manifest, dict) and manifest.get("trading_memo_id"):
                        if manifest["trading_memo_id"] != memo_id:
                            continue  # trade saw a different memo
                except (json.JSONDecodeError, TypeError):
                    pass  # no memo filtering if manifest unparseable
            rows.append(row)

        verdict = {
            "memo_id": memo_id,
            "accuracy_score": None,
            "overturned_rules": [],
            "n_trades": len(rows),
            "evaluated_at": now_iso,
            "source_raw_event_count": cohort.source_raw_event_count,
            "loaded_raw_event_count": cohort.loaded_raw_event_count,
            "cohort_complete": cohort.cohort_complete,
            "ordered_source_digest": cohort.ordered_source_digest,
            "cohort_identity": cohort.identity,
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
        tags["eval_cohort_digest"] = cohort.ordered_source_digest
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
