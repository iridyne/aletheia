"""Integration: AI Investment Committee → existing paper_trader pipeline.

Two modes:
  1. enrich_candidate() — deterministic committee (fast, no LLM)
  2. enrich_candidate_llm() — LLM-powered committee (full analysis, uses tokens)

The LLM mode uses asyncio.gather for parallel analysis of all candidates.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from aletheia.agents.committee import InvestmentCommittee
from aletheia.agents.committee.base import DecisionReport

logger = logging.getLogger("aletheia.committee.integration")

_committee: InvestmentCommittee | None = None

# Retention for persisted committee reports (advisory audit data, bounded growth).
_COMMITTEE_REPORT_RETENTION_DAYS = 90
_last_prune_ts: float = 0.0
_PRUNE_INTERVAL_SEC = 6 * 3600


def _persist_committee_report(candidate: dict, symbol: str, report: DecisionReport, data: dict) -> None:
    """Append-only persistence of one committee evaluation (fail-soft).

    Decision-level audit record: lets any later decision be traced back to the
    committee opinion that informed it. A persistence failure must never affect
    candidate enrichment or trade flow.
    """
    global _last_prune_ts
    try:
        import time as _time

        from aletheia.storage.models import CommitteeReport
        from aletheia.storage.repositories import insert_committee_report

        meta = getattr(report, "model_meta", None) or {}
        record = CommitteeReport(
            opportunity_id=candidate.get("opportunity_id"),
            symbol=symbol,
            decision_id=getattr(report, "decision_id", "") or "",
            direction=report.final_direction,
            confidence=float(report.final_confidence),
            explainability_score=float(report.explainability_score),
            blocked=1 if report.blocked else 0,
            model_used=meta.get("final_model") or meta.get("requested_model"),
            fallback_used=1 if meta.get("fallback_used") else 0,
            indicators_json=json.dumps(data, default=str, ensure_ascii=False)[:4000],
            agent_opinions_json=json.dumps(
                [o.to_dict() for o in report.agent_opinions],
                default=str, ensure_ascii=False,
            )[:20000],
            risks_json=json.dumps(
                getattr(report, "risk_warnings", []) or [],
                default=str, ensure_ascii=False,
            )[:4000],
        )
        insert_committee_report(record)

        now = _time.time()
        if now - _last_prune_ts > _PRUNE_INTERVAL_SEC:
            _last_prune_ts = now
            try:
                from datetime import timedelta

                from aletheia.config.loader import load_config
                from aletheia.storage.repositories import prune_committee_reports

                # Data-protection gate: pruning disabled means no deletions.
                if load_config().retention.pruning_enabled:
                    cutoff = (
                        datetime.now(timezone.utc)
                        - timedelta(days=_COMMITTEE_REPORT_RETENTION_DAYS)
                    ).isoformat()
                    prune_committee_reports(cutoff)
            except Exception:
                pass
    except Exception as exc:
        logger.warning("Committee report persistence failed for %s: %s", symbol, exc)


def get_committee() -> InvestmentCommittee:
    """Lazy-init the singleton committee."""
    global _committee
    if _committee is None:
        _committee = InvestmentCommittee()
    return _committee


def _extract_features_from_opportunity(opp: dict) -> dict[str, Any]:
    """Extract trend-analyzable features from opportunity metadata."""
    meta = {}
    raw = opp.get("metadata")
    if raw:
        try:
            meta = json.loads(raw) if isinstance(raw, str) else {}
        except (json.JSONDecodeError, TypeError):
            pass

    thesis = opp.get("thesis", "") or ""
    observations_raw = opp.get("key_observations") or "[]"
    try:
        observations = json.loads(observations_raw) if isinstance(observations_raw, str) else []
    except (json.JSONDecodeError, TypeError):
        observations = []

    return {
        "metadata": meta,
        "thesis": thesis,
        "observations": observations,
        "confidence_score": opp.get("confidence_score"),
        "direction_bias": opp.get("direction_bias"),
        "scanner_family": opp.get("scanner_family"),
    }


async def enrich_candidate(
    candidate: dict,
    features: dict[str, Any] | None = None,
    news_data: dict[str, Any] | None = None,
    macro_data: dict[str, Any] | None = None,
    risk_data: dict[str, Any] | None = None,
) -> dict:
    """Evaluate a candidate opportunity through the deterministic committee.

    Fast, no LLM calls. Uses rule-based Trend/Macro/News/Risk agents.
    """
    committee = get_committee()
    symbol = candidate.get("symbol", "")
    market_type = candidate.get("market", "perp")

    if not any([features, news_data, macro_data, risk_data]):
        return candidate

    try:
        report: DecisionReport = await committee.evaluate(
            symbol=symbol,
            market_type=market_type,
            features=features,
            news_data=news_data,
            macro_data=macro_data,
            risk_data=risk_data,
        )

        candidate["committee_direction"] = report.final_direction
        candidate["committee_confidence"] = report.final_confidence
        candidate["committee_position_factor"] = report.position_size_factor
        candidate["committee_explainability"] = report.explainability_score
        candidate["committee_blocked"] = report.blocked
        candidate["committee_report"] = report.to_dict()
        candidate["committee_agent_opinions"] = [o.to_dict() for o in report.agent_opinions]

        existing_score = float(candidate.get("trade_score") or 0)
        committee_signal = report.final_confidence * (1 if report.final_direction == "long" else -1 if report.final_direction == "short" else 0)
        candidate["trade_score"] = existing_score * 0.6 + committee_signal * 40 * 0.4

        logger.debug(
            "Committee %s: dir=%s conf=%.0f%% es=%.2f score=%.1f→%.1f",
            symbol, report.final_direction, report.final_confidence * 100,
            report.explainability_score, existing_score, candidate["trade_score"],
        )

    except Exception as exc:
        logger.warning("Committee evaluation failed for %s: %s", symbol, exc)

    return candidate


async def enrich_candidate_llm(
    candidate: dict,
    provider: str | None = None,
) -> dict:
    """Evaluate a candidate through the LLM-powered committee."""
    committee = get_committee()
    symbol = candidate.get("symbol", "")
    market_type = candidate.get("market", "perp")

    # Build data dict from multiple sources
    data: dict[str, Any] = {}

    # 1. Get feature_snapshot_id from opportunity (DB query, since ranker strips metadata)
    feature_snapshot_id = None
    opp_id = candidate.get("opportunity_id")

    if opp_id:
        try:
            from aletheia.storage import repositories as repo
            opp = repo.get_opportunity(opp_id)
            if opp:
                meta_raw = opp.get("metadata", "{}")
                meta = json.loads(meta_raw) if isinstance(meta_raw, str) else {}
                evidence = meta.get("evidence_manifest", {})
                t0 = evidence.get("t0", {})
                feature_snapshot_id = t0.get("feature_snapshot_id")
                # Also extract direct indicators from t0 if present
                for key in ["atr_pct", "rsi", "funding_rate", "return_1h", "return_4h"]:
                    if t0.get(key) is not None and key not in data:
                        data[key] = t0[key]
        except Exception as exc:
            logger.debug("Opportunity lookup failed for %s: %s", symbol, exc)

    # 2. Query feature_snapshots for detailed indicators

    _SNAPSHOT_KEYS = [
        "atr_pct", "rsi_14", "return_1h", "return_4h", "return_24h",
        "funding_rate", "open_interest", "oi_delta_1h", "taker_buy_ratio",
        "vol_ratio", "relative_strength_24h", "oi_price_divergence",
        "rsi_4h", "bb_squeeze",
    ]
    _SNAPSHOT_SELECT = (
        "SELECT atr_pct, rsi_14, return_1h, return_4h, return_24h, "
        "funding_rate, open_interest, oi_delta_1h, taker_buy_ratio, "
        "vol_ratio, relative_strength_24h, oi_price_divergence, "
        "rsi_4h, bb_squeeze FROM universe_feature_snapshots"
    )
    # Only use fallback snapshots when fresh — stale indicators would mislead the LLM.
    _SNAPSHOT_MAX_AGE_SEC = 1800

    def _apply_snapshot_row(row) -> None:
        if not row:
            return
        for k, v in zip(_SNAPSHOT_KEYS, row):
            if v is not None:
                data[k] = v

    if feature_snapshot_id:
        try:
            from aletheia.storage.sqlite import get_db
            db = get_db()
            row = db.execute(
                _SNAPSHOT_SELECT + " WHERE id = ?",
                (feature_snapshot_id,),
            ).fetchone()
            _apply_snapshot_row(row)
        except Exception as exc:
            logger.debug("Feature snapshot lookup failed for %s: %s", symbol, exc)

    # Fallback: many scanners do not record feature_snapshot_id in the evidence
    # manifest, which left the LLM with only ATR + macro and it refused to call
    # a direction ("INSUFFICIENT_DATA").  Resolve the latest fresh snapshot for
    # this symbol via universe_members instead.
    if len(data) < 5:
        try:
            from aletheia.storage.sqlite import get_db
            db = get_db()
            # ts is stored as ISO-8601 UTC text; string comparison is safe.
            cutoff = (
                datetime.now(timezone.utc) - timedelta(seconds=_SNAPSHOT_MAX_AGE_SEC)
            ).isoformat()
            member_id = candidate.get("universe_member_id")
            row = None
            if member_id:
                row = db.execute(
                    _SNAPSHOT_SELECT
                    + " WHERE asset_id = (SELECT asset_id FROM universe_members WHERE id = ?)"
                      " AND ts >= ? ORDER BY ts DESC LIMIT 1",
                    (member_id, cutoff),
                ).fetchone()
            if row is None:
                row = db.execute(
                    _SNAPSHOT_SELECT
                    + " WHERE asset_id IN (SELECT asset_id FROM universe_members WHERE symbol = ?)"
                      " AND ts >= ? ORDER BY ts DESC LIMIT 1",
                    (symbol, cutoff),
                ).fetchone()
            if row:
                _apply_snapshot_row(row)
                logger.debug("LLM enrich used latest snapshot fallback for %s", symbol)
        except Exception as exc:
            logger.debug("Latest snapshot fallback failed for %s: %s", symbol, exc)

    # 3. Get macro data from latest macro_snapshots
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        macro_row = db.execute(
            """SELECT fear_greed, etf_flow_24h, dxy_change, mvrv_z_score,
                      stablecoin_supply_change
               FROM macro_snapshots ORDER BY ts DESC LIMIT 1"""
        ).fetchone()
        if macro_row:
            macro_keys = ["fear_greed", "etf_flow_24h", "dxy_change", "onchain_mvrv", "stablecoin_supply_change"]
            for k, v in zip(macro_keys, macro_row):
                if v is not None:
                    data[k] = v
    except Exception as exc:
        logger.debug("Macro snapshot lookup failed: %s", exc)

    # 4. If no data, skip
    if not data:
        return candidate

    try:
        report: DecisionReport = await committee.evaluate_with_llm(
            symbol=symbol,
            market_type=market_type,
            features=data,
            provider=provider,
        )

        # DEBUG: Log report confidence right after evaluate_with_llm returns
        logger.info(
            "LLM enrich got report for %s: conf=%.3f dir=%s es=%.2f",
            symbol, report.final_confidence, report.final_direction, report.explainability_score,
        )

        candidate["committee_direction"] = report.final_direction
        candidate["committee_confidence"] = report.final_confidence
        candidate["committee_position_factor"] = report.position_size_factor
        candidate["committee_explainability"] = report.explainability_score
        candidate["committee_blocked"] = report.blocked
        candidate["committee_report"] = report.to_dict()
        candidate["committee_agent_opinions"] = [o.to_dict() for o in report.agent_opinions]
        candidate["committee_llm_used"] = True

        # Persist the evaluation for later audit/review (fail-soft, advisory).
        _persist_committee_report(candidate, symbol, report, data)

        # Combined score: 60% ranker + 40% LLM committee
        existing_score = float(candidate.get("trade_score") or 0)
        committee_signal = report.final_confidence * (
            1 if report.final_direction == "long" else -1 if report.final_direction == "short" else 0
        )
        candidate["trade_score"] = existing_score * 0.6 + committee_signal * 40 * 0.4

        # DEBUG: Log right before the main committee log
        logger.info(
            "LLM Committee PRE-LOG %s: report.final_confidence=%.3f (%.0f%%)",
            symbol, report.final_confidence, report.final_confidence * 100,
        )

        logger.info(
            "LLM Committee %s: dir=%s conf=%.0f%% es=%.2f (LLM-powered, %d indicators)",
            symbol, report.final_direction, report.final_confidence * 100,
            report.explainability_score, len(data),
        )

    except Exception as exc:
        logger.warning("LLM Committee failed for %s: %s", symbol, exc)
        return candidate

    return candidate


async def enrich_candidates_parallel(
    candidates: list[dict],
    provider: str | None = None,
    max_concurrency: int = 10,
    total_timeout: float = 240.0,
) -> list[dict]:
    """Enrich all candidates in parallel using LLM.

    Uses asyncio.gather with a semaphore to limit concurrency
    (default 10 parallel LLM calls).  A *total_timeout* bounds the
    entire batch so a slow/straggling LLM provider cannot hang the
    caller indefinitely.  On timeout, candidates that have not yet
    completed are returned un-enriched (the caller degrades gracefully).

    Args:
        candidates: list of candidate dicts.
        provider: optional LLM provider name.
        max_concurrency: max parallel LLM calls.
        total_timeout: wall-clock seconds for the whole batch.  Must be
            strictly less than the caller's own job timeout so the
            scheduler's single-threaded async worker is never pinned by
            a coroutine that the waiter has already given up on.

    Returns:
        List of enriched candidates (un-enriched ones preserved on timeout).
    """
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _enrich_one(c: dict) -> dict:
        async with semaphore:
            return await enrich_candidate_llm(c, provider=provider)

    tasks = [_enrich_one(c) for c in candidates]

    # --- Bound the batch so a slow provider cannot hang the scheduler ---
    # asyncio.gather waits for ALL tasks; without a ceiling the caller's
    # job timeout fires on the waiter side but the inner coroutine keeps
    # running on the single-threaded async worker, permanently starving it.
    done: set[asyncio.Task] = set()
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=total_timeout,
        )
    except asyncio.TimeoutError:
        # Return whatever finished + un-enriched stragglers.  Cancel the
        # pending tasks so they don't keep the event loop thread busy.
        results = []
        for t in tasks:
            if t.done():
                results.append(t.result())
            else:
                results.append(TimeoutError(
                    f"LLM enrichment exceeded {total_timeout}s budget"
                ))
            if not t.done():
                t.cancel()
        # Let cancelled tasks settle so their finally blocks run.
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.warning(
            "LLM enrichment timed out after %.0f)s — %d/%d candidates processed",
            total_timeout,
            sum(1 for r in results if not isinstance(r, Exception)),
            len(candidates),
        )

    enriched = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.warning("Candidate %s failed: %s", candidates[i].get("symbol", "?"), result)
            enriched.append(candidates[i])
        else:
            enriched.append(result)

    return enriched


def committee_score_display(candidate: dict) -> str:
    """Format committee assessment for display in logs/reports."""
    report = candidate.get("committee_report")
    if not report:
        return "committee:n/a"
    direction = report.get("final_direction", "?")
    conf = report.get("final_confidence", 0)
    es = report.get("explainability_score", 0)
    llm = "🤖" if candidate.get("committee_llm_used") else ""
    blocked = "⛔" if report.get("blocked") else ""
    return f"committee:{direction}({conf:.0%}) ES={es:.2f}{llm}{blocked}"
