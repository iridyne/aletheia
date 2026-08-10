"""Market Observer agent — observes market conditions and creates opportunities.

Per-symbol loop: each symbol gets its own LLM MarketRead call and its own
Opportunity row. This fixes the original "only BTC" bug where all symbol data
was sent to the LLM together but only symbols[0] was used for the resulting
opportunity.
"""

import asyncio
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field

from aletheia.agents import register_agent
from aletheia.agents.helpers import elapsed_ms, llm_structured, log_tool, record_step
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.data.freshness import fresh_row
from aletheia.model_provider.router import get_provider_for_tier
from aletheia.storage.models import Opportunity
from aletheia.storage.repositories import (
    insert_opportunity,
    insert_opportunity_once_by_snapshot,
    list_strategies,
    find_opportunity_by_symbol_snapshot,
    update_open_opportunity,
)
from aletheia.trading.instruments import (
    observed_instruments_from_config,
    instrument_market_to_db_market,
)
from aletheia.trading.strategy_lifecycle import is_active_paper_strategy
from aletheia.config.loader import load_config

logger = logging.getLogger("aletheia.agents.market_observer")

_VALID_DIRECTIONS = {"long", "short", "neutral"}
_VALID_CONFIDENCE = {"high", "medium", "speculative"}

# Wall-clock budget for the complete observer run. Must stay BELOW the scheduler's
# market_observer job timeout (_ASYNC_JOB_TIMEOUT_SEC["market_observer"] = 500s in
# agent_runtime/scheduler.py) with margin, so the coroutine returns normally before
# asyncio.wait_for cancels it — preventing the abandoned-thread leak. Keep in sync if
# the job timeout changes.
_MARKET_OBSERVER_JOB_TIMEOUT_S = 500.0
_MARKET_OBSERVER_TEARDOWN_MARGIN_SEC = 40.0
_MARKET_OBSERVER_LOOP_BUDGET_SEC = _MARKET_OBSERVER_JOB_TIMEOUT_S - _MARKET_OBSERVER_TEARDOWN_MARGIN_SEC
_MARKET_OBSERVER_PREFETCH_CONCURRENCY = 4
# Bounded concurrency for the Phase 4 LLM analysis loop.  Two concurrent calls
# match the LLM broker's default max_concurrency=2 worker pool (ProviderPolicy),
# so requests queue inside the broker rather than stacking up as coroutines.
# Raising this without also raising inference_max_concurrency in config would
# only add asyncio overhead with no throughput gain.
_MARKET_OBSERVER_LLM_CONCURRENCY = 2
# Maximum wall-clock a single symbol's LLM call (all retry attempts combined)
# may consume.  Caps the per-symbol deadline so that one slow provider call
# cannot consume the entire remaining run budget and starve later symbols.
# Must be < _MARKET_OBSERVER_LOOP_BUDGET_SEC and > a single attempt's p99.
_MARKET_OBSERVER_PER_SYMBOL_LLM_CAP_SEC = 45.0
# Pressure-skip: when estimated remaining LLM work exceeds remaining run budget
# AND a symbol's signal score is below this threshold, the symbol is skipped
# immediately rather than queued.  High-signal symbols (score >= threshold)
# are always attempted regardless of budget pressure.
# Set to 0.5 — symbols that did not fully pass the pre-filter (score < 1.0)
# are candidates for pressure-skip; pre-filter passers are always protected.
_MARKET_OBSERVER_SIGNAL_PRESSURE_THRESHOLD = 0.5
# EWMA smoothing factor for the run-local LLM latency estimate.
# alpha=0.4 weights recent calls heavier; lower values give more stable estimates.
_MARKET_OBSERVER_LLM_EWMA_ALPHA = 0.4
_PREDICTION_CONTEXT_MAX_AGE_HOURS = 6
_ROUND_ROBIN_STATE_KEY = "agent.market_observer.round_robin.v1"
_LLM_LATENCY_SETTINGS_KEY = "agent.market_observer.llm_latency.v1"
_LLM_LATENCY_ROLLING_N = 100  # keep the most recent N successful call durations
_RETRYABLE_EVIDENCE_ERROR_PREFIX = "observer_pre_filter_evidence_failed:"
_RETRYABLE_EMISSION_ERROR_PREFIX = "observer_opportunity_emission_evidence_failed:"


def _fair_llm_budget_seconds(remaining_run_s: float, outstanding_count: int) -> float:
    """Split remaining time across parallel LLM waves, with a useful floor."""
    concurrency = max(1, _MARKET_OBSERVER_LLM_CONCURRENCY)
    outstanding = max(1, outstanding_count)
    remaining_waves = max(1, math.ceil(outstanding / concurrency))
    return max(10.0, remaining_run_s / remaining_waves)


@dataclass
class _DbCache:
    """Batch-prefetched DB rows for all instruments in a run."""

    perp_structures: dict[str, dict]  # symbol → latest perp_structure_snapshots row
    funding_hl: dict[str, dict]  # symbol → latest HL funding row
    funding_hl_pred: dict[str, dict]  # symbol → latest HL-predicted funding row
    news: dict[str, list[dict]]  # symbol → up-to-3 recent news_items rows
    onchain_rows: list[dict]  # global on-chain metrics (all symbols share)
    open_interest_hl: dict[str, dict] = field(default_factory=dict)


@dataclass
class _SymbolPrefetch:
    """Per-symbol context built in the parallel prefetch phase."""

    context_str: str
    debug: dict
    pre_filter: dict  # from _evaluate_pre_filter()
    signal_score: float  # 0.0 = no signal; ≥1.0 = pre-filter passed
    funding_rate_val: float | None
    open_interest_val: float | None
    oi_delta_val: float | None
    rendered_sources: dict[str, list[dict]] = field(default_factory=dict)
    failed: bool = False  # True if exchange context fetch raised
    context_duration_ms: int | None = None


def _observer_learning_candidate_key(
    *,
    run_id: str | None,
    venue: str | None,
    market_type: str,
    symbol: str,
    universe_snapshot_id: str | None,
    universe_member_id: str | None,
) -> str:
    """Keep one Universe member in one arm across Observer retries."""
    if universe_member_id:
        return f"observer_member:{universe_snapshot_id or 'unknown'}:{universe_member_id}:{market_type}"
    return f"observer_run:{run_id or 'scheduled'}:{venue or 'unknown'}:{market_type}:{symbol}"


def _monotonic() -> float:
    """Single injectable monotonic clock for the observer run budget."""
    import time

    return time.monotonic()


def _fair_signal_order(items, *, score, key) -> list:
    """Keep the durable next item first, then prioritize the remaining signals.

    ``items`` is already rotated to the durable round-robin cursor.  Sorting the
    whole list would let the same high-score subset permanently displace that
    cursor whenever the wall-clock budget truncates a run.
    """
    if not items:
        return []
    first = items[0]
    first_key = key(first)
    remaining = [item for item in items[1:] if key(item) != first_key]
    return [first, *sorted(remaining, key=score, reverse=True)]


def _advance_completed_round_robin_prefix(
    round_robin,
    ordered_items,
    *,
    completed_keys: set[str],
    next_unprocessed_index: int,
    key,
) -> int:
    """Advance through completed canonical items, stopping at the first gap."""
    ordered_keys = [key(item) for item in ordered_items]
    if not ordered_keys:
        return 0
    for _ in ordered_keys:
        expected_key = ordered_keys[next_unprocessed_index]
        if expected_key not in completed_keys:
            break
        round_robin.advance_after(
            ordered_items,
            completed_key=expected_key,
            key=key,
        )
        # Consume this attempt.  Keeping it in the set would let a later call
        # wrap around the cycle and advance over the same item a second time.
        completed_keys.discard(expected_key)
        next_unprocessed_index = (next_unprocessed_index + 1) % len(ordered_keys)
    return next_unprocessed_index


def _record_observer_llm_latency(duration_s: float) -> None:
    """Persist one LLM call duration to the rolling latency history in settings.

    Keeps at most _LLM_LATENCY_ROLLING_N samples (FIFO).  Best-effort: any
    storage error is swallowed so a DB hiccup never breaks the observer run.
    """
    try:
        import json as _json
        from aletheia.storage.sqlite import get_db

        db = get_db()
        row = db.execute("SELECT value FROM settings WHERE key = ?", (_LLM_LATENCY_SETTINGS_KEY,)).fetchone()
        existing: list[float] = []
        if row is not None:
            try:
                existing = _json.loads(row["value"])
                if not isinstance(existing, list):
                    existing = []
            except (ValueError, TypeError):
                existing = []
        existing.append(float(duration_s))
        if len(existing) > _LLM_LATENCY_ROLLING_N:
            existing = existing[-_LLM_LATENCY_ROLLING_N:]
        payload = _json.dumps(existing)
        db.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES(?, ?, datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (_LLM_LATENCY_SETTINGS_KEY, payload),
        )
    except Exception:
        pass  # latency persistence is purely advisory


def _read_observer_llm_latency_p50() -> float | None:
    """Return the p50 of the persisted LLM latency samples, or None if none exist."""
    try:
        import json as _json
        from aletheia.storage.sqlite import get_db

        row = get_db().execute("SELECT value FROM settings WHERE key = ?", (_LLM_LATENCY_SETTINGS_KEY,)).fetchone()
        if row is None:
            return None
        samples: list[float] = _json.loads(row["value"])
        if not isinstance(samples, list) or not samples:
            return None
        sorted_samples = sorted(samples)
        mid = len(sorted_samples) // 2
        return float(sorted_samples[mid])
    except Exception:
        return None


def _consume_detached_prefetch(task: asyncio.Future) -> None:
    """Consume a deadline-detached prefetch result without leaking warnings."""
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass


async def _gather_prefetch_with_deadline(awaitables, *, deadline: float) -> list:
    """Gather prefetches without draining cancellation-resistant provider I/O."""
    remaining = deadline - _monotonic()
    if remaining <= 0:
        # Coroutines were constructed by the caller but never scheduled.  Close
        # them explicitly so a budget-expired run does not emit unawaited warnings.
        for awaitable in awaitables:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
        raise TimeoutError("market_observer prefetch has no remaining run budget")
    task = asyncio.ensure_future(
        asyncio.gather(*awaitables, return_exceptions=True),
    )
    try:
        done, _pending = await asyncio.wait({task}, timeout=remaining)
    except asyncio.CancelledError:
        task.add_done_callback(_consume_detached_prefetch)
        task.cancel()
        raise
    if task not in done:
        task.add_done_callback(_consume_detached_prefetch)
        task.cancel()
        raise TimeoutError
    return task.result()


class MarketRead(BaseModel):
    """Structured market read for a single symbol.

    Two design choices vs. the previous schema:
      1. `confidence_score` (0-100) sits alongside the categorical `confidence`.
         This makes "neutral / speculative" no longer a free hideout: the model
         must commit a number on the spectrum and explain why.
      2. `key_observations` forces ≥1 specific data citation from the snapshot.
         Empty list → the model is implicitly admitting "I had nothing concrete".
    """

    direction_bias: Literal["long", "short", "neutral"] = "neutral"
    confidence: Literal["high", "medium", "speculative"] = "speculative"
    confidence_score: int = Field(50, ge=0, le=100, description="0=no idea, 50=balanced, 100=overwhelming evidence")
    rationale: str = ""
    key_observations: list[str] = Field(
        default_factory=list, description="2-4 bullets citing specific numbers from the snapshot"
    )


def _is_perp_structure_fresh(row: dict | None) -> bool:
    return (
        fresh_row(
            row,
            "perp_structure",
            timestamp_field="as_of_time" if row and row.get("as_of_time") else "observed_at",
        )
        is not None
    )


# The static prompt names the technical-context-v1 categories but does not add
# field-specific trading priors. Numeric definitions remain in the versioned
# renderer, while Risk and Execution keep their independent authority.
_SYSTEM_PROMPT = """\
You are a crypto market observer for Aletheia paper-trading.

You receive one verified one-symbol snapshot. When technical-context-v1 is
present, it uses closed bars: treat correlated fields as one evidence bundle,
and unavailable TFs as having no usable facts. The snapshot is the source of
truth — do not invent numbers or levels.

Output a structured market read. Calibration:

- direction_bias:
  - "long" if distinct trend, momentum, location and participation facts align.
  - "short" mirror.
  - "neutral" only when signals genuinely conflict OR the data is too thin
    (mention which in rationale). Do not default to neutral; commit if you have evidence.

- confidence:
  - "high" — multi-timeframe agreement and clear directional signal.
  - "medium" — 1-2 timeframe agreement, or mild signal.
  - "speculative" — noisy / conflicting / thin data. Speculative is honest
    when warranted, not a default.

- confidence_score: 0-100. 50 = genuinely balanced. Choose a non-50 value
  if you have any actionable read at all.

- key_observations: 2-4 short bullets, each citing a specific number from
  the snapshot (e.g. "BTC 1h RSI 68 + price > SMA_20: short-term overbought").
  Empty list signals "no concrete evidence to point at".

CRITICAL: Output ONLY the raw JSON object. No markdown fences, no explanation
before or after, no "Here is..." preamble. Just the JSON starting with { and
ending with }.

Indicator meanings (single source of truth — do not invent rules):
- SMA20/SMA50: simple moving averages. Price > SMA50 = uptrend; < SMA50 = downtrend.
- RSI14: momentum 0-100. >70 overbought, <30 oversold, 50 = neutral. Can stay extreme in strong trends.
- ATR14: average true range (price units). Bigger = wider swings; use for stops, not direction.
- BB%: position in Bollinger band (0=lower, 1=upper). >1 or <0 = outside band.
- Mom10: % change over last 10 bars. Positive = trending up.
- Vol z: volume z-score vs 20 bars. >+1 = unusually high, <-1 = unusually low.

Not financial advice."""


_SCANNER_KEYWORD_MAP: list[tuple[str, str]] = [
    # trend-family — must come before reversion checks (more specific)
    ("breakout", "breakout"),
    ("breakdown", "breakout"),
    ("momentum", "momentum"),
    ("ma_cross", "ma_crossover"),
    ("sma cross", "ma_crossover"),
    ("trend", "trend"),
    # reversion-family
    ("oversold", "reversion"),
    ("overbought", "reversion"),
    ("mean reversion", "reversion"),
    ("divergence", "reversion"),
    # volatility/special
    ("squeeze", "volatility"),
    ("funding", "funding_arb"),
    ("arb", "arb"),
]


def _infer_scanner_family(rationale: str, observations: list[str]) -> str | None:
    """Infer a scanner_family from LLM rationale + observations when not set by universe.

    Used so that the sideways regime tax (P0-2) can fire on non-universe opportunities.
    Returns the first matching family, or None if no keyword matches.
    """
    text = (rationale + " " + " ".join(observations)).lower()
    for keyword, family in _SCANNER_KEYWORD_MAP:
        if keyword in text:
            return family
    return None


_PREFILTER_RSI_DEVIATION = 12.0
_PREFILTER_ATR_PCT = 0.8


def _evaluate_pre_filter(context_debug: dict) -> dict:
    """Evaluate only structured 1h indicators returned by build_market_context."""
    rsi_value: float | None = None
    atr_pct: float | None = None
    matched_rules: list[str] = []

    symbols = context_debug.get("symbols") if isinstance(context_debug, dict) else None
    symbol_debug = next(iter(symbols.values()), {}) if isinstance(symbols, dict) else {}
    one_hour = symbol_debug.get("1h") if isinstance(symbol_debug, dict) else None
    if isinstance(one_hour, dict):
        try:
            raw_rsi = one_hour.get("rsi_14")
            rsi_value = float(raw_rsi) if raw_rsi is not None else None
            if rsi_value is not None and not math.isfinite(rsi_value):
                rsi_value = None
            if rsi_value is not None and abs(rsi_value - 50) > _PREFILTER_RSI_DEVIATION:
                matched_rules.append("rsi_deviation")
        except (TypeError, ValueError):
            rsi_value = None
        try:
            raw_atr_pct = one_hour.get("atr_pct")
            atr_pct = float(raw_atr_pct) if raw_atr_pct is not None else None
            if atr_pct is not None and not math.isfinite(atr_pct):
                atr_pct = None
            if atr_pct is not None and atr_pct > _PREFILTER_ATR_PCT:
                matched_rules.append("atr_pct")
        except (TypeError, ValueError):
            atr_pct = None

    return {
        "passed": bool(matched_rules),
        "rsi": rsi_value,
        "atr_pct": atr_pct,
        "keyword_hits": [],
        "matched_rules": matched_rules,
    }


def _has_sufficient_signal(context_debug: dict) -> bool:
    """Return whether structured 1h RSI or ATR% crosses its signal threshold.

    Used to skip the LLM call for calm markets (P1-7 — saves wall-clock budget).
    """
    return bool(_evaluate_pre_filter(context_debug)["passed"])


def _signal_score(pf: dict) -> float:
    """Normalize pre-filter result to a sortable float. ≥1.0 means pre-filter passed."""
    scores: list[float] = []
    if pf.get("rsi") is not None:
        scores.append(abs(pf["rsi"] - 50) / _PREFILTER_RSI_DEVIATION)
    if pf.get("atr_pct") is not None:
        scores.append(pf["atr_pct"] / _PREFILTER_ATR_PCT)
    return max(scores, default=0.0)


def _prediction_cutoff_iso(max_age_hours: int = _PREDICTION_CONTEXT_MAX_AGE_HOURS) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()


def _prediction_market_signal_lines(
    symbol: str,
    cutoff_iso: str,
    limit: int = 3,
) -> list[str]:
    lines, _sources = _prediction_market_signal_context(symbol, cutoff_iso, limit)
    return lines


def _prediction_market_signal_context(
    symbol: str,
    cutoff_iso: str,
    limit: int = 3,
) -> tuple[list[str], list[dict]]:
    """Return fresh prediction-market lines for a symbol.

    Prediction markets are observation-only catalysts. Stale rows are worse than
    missing rows because their 24h probability change reads like a current fact.
    """
    from aletheia.storage.repositories import _select_all

    base = symbol.split("/")[0].lower()
    keyword_variants = [base]
    coin_names = {
        "btc": "bitcoin",
        "eth": "ethereum",
        "sol": "solana",
        "xrp": "ripple",
        "doge": "dogecoin",
        "ada": "cardano",
        "avax": "avalanche",
        "link": "chainlink",
    }
    if base in coin_names:
        keyword_variants.append(coin_names[base])

    where_clauses = " OR ".join(["LOWER(question) LIKE ?" for _ in keyword_variants])
    params = [f"%{kw}%" for kw in keyword_variants]
    params.append(cutoff_iso)
    pred_rows = _select_all(
        "prediction_markets",
        where=(
            f"({where_clauses}) AND status = 'active' "
            "AND COALESCE(volume_24h, 0) > 0 "
            "AND datetime(updated_at) >= datetime(?)"
        ),
        params=tuple(params),
        order="abs(probability_change_24h) DESC",
        limit=limit,
    )

    lines: list[str] = []
    sources: list[dict] = []
    for r in pred_rows:
        q = (r.get("question") or "")[:80]
        yes = (r.get("yes_probability") or 0) * 100
        delta = (r.get("probability_change_24h") or 0) * 100
        vol = r.get("volume_24h") or 0
        updated = (r.get("updated_at") or "")[:16]
        lines.append(f"- {q}: YES={yes:.0f}% (Delta24h {delta:+.1f}pp, vol=${vol:,.0f}, updated={updated})")
        sources.append(
            {
                "id": r.get("id"),
                "updated_at": r.get("updated_at"),
                "event_id": r.get("event_id"),
            }
        )
    return lines, sources


_T1_SOURCE_KINDS = (
    "news",
    "macro",
    "onchain",
    "prediction",
    "hyperliquid",
    "position_x",
)


def _empty_rendered_sources() -> dict[str, list[dict]]:
    return {kind: [] for kind in _T1_SOURCE_KINDS}


def _news_rendered_source(row: dict) -> dict:
    """Prompt-free provenance for one rendered News line."""
    line = f"- [{row.get('published_at', '')[:10]}] {row.get('title', '')} ({row.get('source', '')})"
    return {
        "id": row.get("id"), "published_at": row.get("published_at"),
        "source": row.get("source"), "analyzed_at": row.get("analyzed_at"),
        "analysis_state": "analyzed_metadata", "freshness_status": "fresh",
        "selection_reason": "exact_entity_match", "rendered_chars": len(line),
    }


def _record_observer_context_artifacts(
    *, run_id, symbol, context, learning_text, learning_manifest, rendered_sources,
    technical_context,
) -> list[dict]:
    """Build prompt-free metadata; the helper links it to the exact LLM call."""
    news = rendered_sources.get("news") or []
    technical = (technical_context or {}).get("artifacts") or []
    artifacts = [{
        "kind": "environment", "stable_id": f"market_context:{symbol}",
        "source": "market_observer", "selected_reason": "required_environment",
        "trust_class": "first_party_observed", "freshness_status": "unknown",
        "rendered_chars": max(
            0,
            len(context)
            - sum(int(item.get("rendered_chars") or 0) for item in news)
            - sum(int(item.get("rendered_chars") or 0) for item in technical),
        ),
    }]
    artifacts.extend({
        "kind": "environment", "stable_id": item.get("stable_id"),
        "source": item.get("source"),
        "selected_reason": f"closed_bar_bundle:{item.get('quality_state', 'unknown')}",
        "trust_class": "deterministic_derived_fact", "freshness_status": "unknown",
        "observed_at": item.get("observed_at"),
        "rendered_chars": item.get("rendered_chars"),
    } for item in technical if item.get("stable_id"))
    artifacts.extend({
        "kind": "news", "stable_id": f"news_item:{item['id']}",
        "source": item.get("source"), "selected_reason": item.get("selection_reason"),
        "trust_class": "external_untrusted", "freshness_status": item.get("freshness_status"),
        "published_at": item.get("published_at"), "observed_at": item.get("analyzed_at"),
        "rendered_chars": item.get("rendered_chars"),
        "metadata": {"analysis_state": item.get("analysis_state")},
    } for item in news if item.get("id"))
    if learning_text:
        artifacts.append({
            "kind": "learning",
            "stable_id": learning_manifest.get("bundle_id") or "observer_learning_bundle",
            "source": "paper_authority_adapter",
            "selected_reason": "authorized_learning_bundle",
            "trust_class": "learned_advisory", "freshness_status": "unknown",
            "rendered_chars": len(learning_text),
        })
    return artifacts


def _bounded_rendered_sources(sources: dict[str, list[dict]]) -> dict[str, list[dict]]:
    limits = {
        "news": 3,
        "macro": 1,
        "onchain": 3,
        "prediction": 3,
        "hyperliquid": 3,
        "position_x": 1,
    }
    bounded: dict[str, list[dict]] = {}
    for kind in _T1_SOURCE_KINDS:
        bounded[kind] = []
        for item in (sources.get(kind) or [])[: limits[kind]]:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            bounded[kind].append(
                {
                    key: value[:200] if isinstance(value, str) else value
                    for key, value in item.items()
                    if value is not None
                }
            )
    return bounded


def _bounded_t0(value: dict) -> dict:
    allowed = (
        "status",
        "trigger_type",
        "run_id",
        "universe_snapshot_id",
        "universe_member_id",
        "feature_snapshot_id",
        "pre_filter_evidence_id",
        "symbol",
        "market_type",
        "scanner_family",
        "direction",
        "valid_until",
        "total_score",
        "entry_price_usd",
        "rsi",
        "atr_pct",
    )
    bounded = {
        key: item[:160] if isinstance(item, str) else item for key in allowed if (item := value.get(key)) is not None
    }
    for key in ("reasons", "matched_rules"):
        if isinstance(value.get(key), list):
            bounded[key] = [str(item)[:160] for item in value[key][:5]]
    return bounded


def _macro_source_identity(macro: dict | None) -> dict | None:
    observed_at = (macro or {}).get("observed_at")
    if not observed_at:
        return None
    try:
        from aletheia.storage.sqlite import get_db

        row = (
            get_db()
            .execute(
                "SELECT id, observed_at FROM macro_snapshots WHERE observed_at = ? ORDER BY rowid DESC LIMIT 1",
                (observed_at,),
            )
            .fetchone()
        )
        return dict(row) if row else None
    except Exception:
        return None


def _rendered_onchain_sources(rows: list[dict]) -> list[dict]:
    fields = {
        "stablecoin_mcap": "total_usd",
        "chain_tvl": "total_usd",
        "dex_volume": "total_usd",
    }
    newest: dict[str, dict] = {}
    for row in rows:
        metric_type = row.get("metric_type")
        if metric_type in fields:
            newest.setdefault(metric_type, row)
    return [
        {
            "id": row.get("id"),
            "metric_type": metric_type,
            "observed_at": row.get("observed_at"),
            "source": row.get("source"),
        }
        for metric_type, row in newest.items()
        if row.get(fields[metric_type]) is not None
    ]


def _opportunity_evidence_manifest(
    *,
    metadata: dict,
    rendered_sources: dict[str, list[dict]],
    run_id: str | None,
    universe_snapshot_id: str | None,
    universe_member_id: str | None,
    scanner_family: str | None,
    pre_filter: dict,
    pre_filter_evidence_id: str | None = None,
) -> dict:
    from aletheia.learning.opportunity_information_policy import POLICY_EPOCH_LABEL

    existing = metadata.get("evidence_manifest")
    existing_t0 = existing.get("t0") if isinstance(existing, dict) else None
    t0 = (
        _bounded_t0(existing_t0)
        if (isinstance(existing_t0, dict) and existing.get("version") == "opportunity-information-integrity-v1")
        else {
            "status": "available",
            "trigger_type": "observer_pre_filter",
            "run_id": run_id,
            "pre_filter_evidence_id": pre_filter_evidence_id,
            "universe_snapshot_id": universe_snapshot_id,
            "universe_member_id": universe_member_id,
            "scanner_family": scanner_family,
            "matched_rules": [str(rule)[:80] for rule in (pre_filter.get("matched_rules") or [])[:4]],
            "rsi": pre_filter.get("rsi"),
            "atr_pct": pre_filter.get("atr_pct"),
        }
    )
    return {
        "version": "opportunity-information-integrity-v1",
        "policy_epoch": POLICY_EPOCH_LABEL,
        "t0": t0,
        "t1": {
            "status": "available",
            "sources": _bounded_rendered_sources(rendered_sources),
        },
    }


def _batch_prefetch_db(symbols: list[str]) -> _DbCache:
    """Batch-fetch all per-symbol DB data at run start. Sync, single transaction window."""
    from aletheia.storage import repositories as repo
    from aletheia.data.freshness import fresh_rows

    news_since = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    return _DbCache(
        perp_structures=repo.batch_perp_structures(symbols),
        funding_hl=repo.batch_funding_rates(symbols, "hyperliquid"),
        funding_hl_pred=repo.batch_funding_rates(symbols, "hyperliquid_predicted"),
        news=repo.batch_news(symbols, news_since),
        onchain_rows=fresh_rows(repo.latest_onchain_snapshots(limit=3), "onchain"),
        open_interest_hl=repo.batch_open_interest(symbols, "hyperliquid"),
    )


async def _prefetch_symbol(
    instrument,  # InstrumentId
    *,
    venue: str,
    provider,
    prediction_context_enabled: bool,
    prediction_context_cutoff: str | None,
    db_cache: _DbCache,
) -> _SymbolPrefetch:
    """Build market context for one symbol — the parallelizable phase.

    Called via asyncio.gather() for all instruments before the serial LLM loop.
    Exchange OHLCV calls (the wall-clock bottleneck) run concurrently across symbols.
    """
    import time as _time
    from aletheia.trading.market_context import build_market_context

    symbol = instrument.symbol
    market_type = instrument.market_type
    _t_start = _time.monotonic()
    _empty_pf: dict = {
        "passed": False,
        "rsi": None,
        "atr_pct": None,
        "keyword_hits": [],
        "matched_rules": [],
    }

    # Phase A: async exchange OHLCV (the expensive wall-clock part)
    try:
        context_started_at = _time.monotonic()
        context, ctx_debug = await build_market_context(
            [symbol],
            market_type=market_type,
            exchange=venue,
            provider=provider,
            include_glossary=False,
        )
        context_duration_ms = elapsed_ms(context_started_at)
    except Exception as e:
        logger.warning("[MO prefetch] %s: build_market_context failed: %s", symbol, e)
        return _SymbolPrefetch(
            context_str="",
            debug={},
            pre_filter=_empty_pf,
            signal_score=0.0,
            funding_rate_val=None,
            open_interest_val=None,
            oi_delta_val=None,
            failed=True,
        )

    # Phase B: DB enrichment — uses batch-prefetched data where possible
    funding_rate_val: float | None = None
    open_interest_val: float | None = None
    oi_delta_val: float | None = None
    rendered_sources = _empty_rendered_sources()
    try:
        enrichment_sources = _empty_rendered_sources()
        from aletheia.data.context_builder import (
            build_market_context_from_db,
            render_enriched_context_md,
        )
        from aletheia.data.freshness import fresh_row as _fresh_row
        from aletheia.data.models import TradabilityScore, LeverageState, MacroSnapshot

        enriched_snapshot = build_market_context_from_db(
            [symbol],
            exchange=venue or "",
            market_type=market_type,
        )
        tradability = {sym: TradabilityScore(**row) for sym, row in enriched_snapshot.tradability.items()}
        leverage = {sym: LeverageState(**row) for sym, row in enriched_snapshot.leverage.items()}
        macro = MacroSnapshot(**enriched_snapshot.macro) if enriched_snapshot.macro else None
        macro_source = _macro_source_identity(enriched_snapshot.macro)
        if macro is not None and macro_source:
            enrichment_sources["macro"].append(macro_source)
        enrichment_sources["onchain"] = _rendered_onchain_sources(db_cache.onchain_rows)
        lv = leverage.get(symbol)
        if lv:
            funding_rate_val = lv.funding_rate
            open_interest_val = lv.open_interest_usd
            oi_delta_val = lv.oi_delta_1h_pct

        is_derivatives_market = market_type in {"perp", "swap", "futures", "usdt_futures"}
        # Cross-venue perp evidence is never attached to a spot instrument.
        hl_fr = _fresh_row(db_cache.funding_hl.get(symbol), "cross_venue_funding") if is_derivatives_market else None
        hl_pred = (
            _fresh_row(db_cache.funding_hl_pred.get(symbol), "cross_venue_funding") if is_derivatives_market else None
        )
        hl_oi = (
            _fresh_row(db_cache.open_interest_hl.get(symbol), "cross_venue_open_interest")
            if is_derivatives_market
            else None
        )
        xvenue_funding: dict = {}
        if hl_fr or hl_pred or hl_oi:
            xvenue_funding[symbol] = {
                "hl_funding_id": hl_fr.get("id") if hl_fr else None,
                "hl_ann": hl_fr.get("funding_rate_annualized") if hl_fr else None,
                "hl_predicted_ann": hl_pred.get("funding_rate_annualized") if hl_pred else None,
                "primary_ann": lv.funding_annualized if lv else None,
                "hl_open_interest_id": hl_oi.get("id") if hl_oi else None,
                "hl_open_interest_usd": hl_oi.get("open_interest_usd") if hl_oi else None,
                "hl_oi_change_1h_pct": hl_oi.get("oi_change_1h_pct") if hl_oi else None,
                "hl_observed_at": hl_oi.get("observed_at") if hl_oi else (hl_fr.get("observed_at") if hl_fr else None),
            }
        if hl_fr and hl_fr.get("funding_rate_annualized") is not None:
            enrichment_sources["hyperliquid"].append(
                {
                    "id": hl_fr.get("id"),
                    "kind": "funding",
                    "observed_at": hl_fr.get("observed_at"),
                    "exchange": hl_fr.get("exchange"),
                }
            )
        if (
            hl_pred
            and hl_pred.get("funding_rate_annualized") is not None
            and hl_fr
            and hl_fr.get("funding_rate_annualized") is not None
        ):
            enrichment_sources["hyperliquid"].append(
                {
                    "id": hl_pred.get("id"),
                    "kind": "predicted_funding",
                    "observed_at": hl_pred.get("observed_at"),
                    "exchange": hl_pred.get("exchange"),
                }
            )
        if hl_oi and hl_oi.get("open_interest_usd") is not None:
            enrichment_sources["hyperliquid"].append(
                {
                    "id": hl_oi.get("id"),
                    "kind": "open_interest",
                    "observed_at": hl_oi.get("observed_at"),
                    "exchange": hl_oi.get("exchange"),
                }
            )

        # Perp structure from batch cache
        ps = db_cache.perp_structures.get(symbol) if is_derivatives_market else None
        perp_structure = {symbol: ps} if _is_perp_structure_fresh(ps) else {}
        if perp_structure:
            enrichment_sources["position_x"].append(
                {
                    "id": ps.get("id"),
                    "source": ps.get("source"),
                    "source_ref": ps.get("source_ref"),
                    "source_hash": ps.get("source_hash"),
                    "as_of_time": ps.get("as_of_time"),
                }
            )

        enriched = render_enriched_context_md(
            [symbol],
            tradability,
            leverage,
            macro,
            onchain=db_cache.onchain_rows,
            xvenue_funding=xvenue_funding,
            perp_structure=perp_structure,
        )
        if enriched:
            context = context + "\n\n" + enriched
            for kind in ("macro", "onchain", "hyperliquid", "position_x"):
                rendered_sources[kind] = enrichment_sources[kind]
    except Exception as _e:
        logger.debug("[MO prefetch] %s: enriched context skipped: %s", symbol, _e)

    # Phase C: prediction market signals (per-symbol query, fast)
    if prediction_context_enabled and prediction_context_cutoff:
        try:
            pred_lines, prediction_sources = _prediction_market_signal_context(symbol, prediction_context_cutoff)
            if pred_lines:
                context += "\n\n## Prediction Market Signals\n" + "\n".join(pred_lines)
                rendered_sources["prediction"] = prediction_sources
        except Exception:
            pass

    # Phase D: news headlines from batch cache
    news_rows = db_cache.news.get(symbol, [])
    if news_rows:
        news_lines = [
            f"- [{r.get('published_at', '')[:10]}] {r.get('title', '')} ({r.get('source', '')})" for r in news_rows
        ]
        context += "\n\n## Recent News\n" + "\n".join(news_lines)
        rendered_sources["news"] = [_news_rendered_source(row) for row in news_rows]

    # Phase E: pre-filter scoring
    pf = _evaluate_pre_filter(ctx_debug)
    score = _signal_score(pf)
    logger.debug(
        "[MO prefetch] %s: done in %.1fs, signal_score=%.2f, passed=%s",
        symbol,
        _time.monotonic() - _t_start,
        score,
        pf["passed"],
    )

    return _SymbolPrefetch(
        context_str=context,
        debug=ctx_debug,
        pre_filter=pf,
        signal_score=score,
        funding_rate_val=funding_rate_val,
        open_interest_val=open_interest_val,
        oi_delta_val=oi_delta_val,
        rendered_sources=_bounded_rendered_sources(rendered_sources),
        failed=False,
        context_duration_ms=context_duration_ms,
    )


async def _prefetch_instruments(
    instruments,
    *,
    venue: str,
    prediction_context_enabled: bool,
    prediction_context_cutoff: str | None,
    db_cache: _DbCache,
    concurrency: int = _MARKET_OBSERVER_PREFETCH_CONCURRENCY,
) -> list:
    """Build all symbol contexts with one run-scoped provider and bounded fan-out."""
    from aletheia.data.ccxt_provider import get_market_data_provider

    provider = get_market_data_provider(exchange=venue)
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _bounded(instrument):
        async with semaphore:
            return await _prefetch_symbol(
                instrument,
                venue=venue,
                provider=provider,
                prediction_context_enabled=prediction_context_enabled,
                prediction_context_cutoff=prediction_context_cutoff,
                db_cache=db_cache,
            )

    try:
        return await asyncio.gather(
            *(_bounded(instrument) for instrument in instruments),
            return_exceptions=True,
        )
    finally:
        await provider.close()


async def _analyze_symbol(
    symbol: str,
    market_type: str,
    permission_mode: str,
    run_id: str | None,
    linked_strategy_id: str | None,
    memory_block: str,
    asset_class: str = "crypto",
    provider=None,
    universe_snapshot_id: str | None = None,
    universe_member_id: str | None = None,
    scanner_family: str | None = None,
    scanner_direction: str | None = None,
    member_entry_price_usd: float | None = None,
    member_spread_bps: float | None = None,
    venue: str | None = None,
    member_expires_at: str | None = None,
    force_full: bool = False,
    prediction_context_enabled: bool = False,
    prediction_context_cutoff: str | None = None,
    learning_memory_block: str | None = None,
    learning_outcome_candidate_key: str | None = None,
    non_learning_context_block: str = "",
    _prefetched: "_SymbolPrefetch | None" = None,
    llm_deadline: float | None = None,
) -> tuple[str | None, str | None]:
    """Run market context build + LLM read for a single symbol.

    Returns (opportunity_id, error_str). Both may be None if the symbol is
    simply skipped (no permission, neutral signal, etc.).

    When _prefetched is provided (parallel-prefetch path), context building is
    skipped and the pre-built context is used directly.

    llm_deadline: absolute asyncio loop.time() deadline for the LLM call
        (all retry attempts combined). When None, llm_structured() uses its
        own global _LLM_STRUCTURED_WALLCLOCK_BUDGET_SEC ceiling.
    """
    import time as _time

    _t_start = _time.monotonic()
    _step_times: dict[str, float] = {}

    # Derivatives state for the Opportunity row (perp only; stays None for spot).
    funding_rate_val: float | None = None
    open_interest_val: float | None = None
    oi_delta_val: float | None = None
    rendered_sources = _empty_rendered_sources()

    if _prefetched is not None:
        # Fast path: context already built by _prefetch_symbol.
        if _prefetched.failed:
            return None, f"market_context_fallback:{symbol}:prefetch_failed"
        context = _prefetched.context_str
        ctx_debug = _prefetched.debug
        funding_rate_val = _prefetched.funding_rate_val
        open_interest_val = _prefetched.open_interest_val
        oi_delta_val = _prefetched.oi_delta_val
        rendered_sources = _prefetched.rendered_sources
        log_tool(
            run_id,
            "build_market_context",
            "observe",
            True,
            output=f"{symbol} prefetched=True fallback={_prefetched.debug.get('fallback')}",
            duration_ms=_prefetched.context_duration_ms,
        )
    else:
        from aletheia.trading.market_context import build_market_context

        try:
            _t0 = _time.monotonic()
            context, ctx_debug = await build_market_context(
                [symbol],
                market_type=market_type,
                exchange=venue,
                provider=provider,
                include_glossary=False,
            )
            _step_times["build_market_context"] = _time.monotonic() - _t0
            log_tool(
                run_id,
                "build_market_context",
                "observe",
                True,
                output=f"{symbol} fallback={ctx_debug.get('fallback')} errs={len(ctx_debug.get('errors', []))}",
                duration_ms=elapsed_ms(_t0),
            )
            logger.debug(
                "[MO] %s (%s): build_market_context done in %.1fs, fallback=%s",
                symbol,
                market_type,
                _step_times["build_market_context"],
                ctx_debug.get("fallback"),
            )
        except Exception as e:
            from aletheia.trading.market_data import latest_snapshot

            _total_duration = _time.monotonic() - _t_start
            logger.warning(
                "[MO] %s (%s): market_context build FAILED after %.1fs — %s", symbol, market_type, _total_duration, e
            )
            snapshot_started_at = _time.monotonic()
            snap = latest_snapshot(symbol)
            log_tool(
                run_id,
                "get_market_snapshot",
                "observe",
                True,
                output=symbol,
                duration_ms=elapsed_ms(snapshot_started_at),
            )
            if snap:
                context = f"{symbol}: price={snap['price']} 24h_change={snap.get('change_pct_24h')}%"
            else:
                context = f"{symbol}: no snapshot yet"
            return None, f"market_context_fallback:{symbol}:{e}"

        # Append enriched microstructure/derivatives/macro context if available.
        _t0 = _time.monotonic()
        try:
            enrichment_sources = _empty_rendered_sources()
            from aletheia.data.context_builder import (
                build_market_context_from_db,
                render_enriched_context_md,
            )
            from aletheia.data.freshness import fresh_rows
            from aletheia.storage import repositories as repo
            from aletheia.data.models import TradabilityScore, LeverageState, MacroSnapshot

            # The shared DB builder applies exact-instrument identity and the same
            # fail-closed freshness policy used by Universe feature collection.
            enriched_snapshot = build_market_context_from_db(
                [symbol],
                exchange=venue or "",
                market_type=market_type,
            )
            is_derivatives_market = market_type in {
                "perp",
                "swap",
                "futures",
                "usdt_futures",
            }
            ps = repo.latest_perp_structure(symbol) if is_derivatives_market else None
            tradability = {sym: TradabilityScore(**row) for sym, row in enriched_snapshot.tradability.items()}
            leverage = {sym: LeverageState(**row) for sym, row in enriched_snapshot.leverage.items()}
            macro = MacroSnapshot(**enriched_snapshot.macro) if enriched_snapshot.macro else None
            macro_source = _macro_source_identity(enriched_snapshot.macro)
            if macro is not None and macro_source:
                enrichment_sources["macro"].append(macro_source)

            # Only freshness-qualified derivatives are copied into Opportunity rows.
            lv = leverage.get(symbol)
            if lv:
                funding_rate_val = lv.funding_rate
                open_interest_val = lv.open_interest_usd
                oi_delta_val = lv.oi_delta_1h_pct

            # On-chain regime (DefiLlama stablecoin/TVL/DEX) — global, not per-symbol.
            onchain_rows = fresh_rows(
                repo.latest_onchain_snapshots(limit=3),
                "onchain",
            )
            enrichment_sources["onchain"] = _rendered_onchain_sources(onchain_rows)

            # Cross-venue funding: route the Hyperliquid feed (otherwise orphan) into
            # the decision context as a basis/sentiment-divergence signal vs the
            # primary venue. Compared annualized (HL 1h vs primary 8h interval).
            xvenue_funding: dict = {}
            hl_fr = (
                fresh_row(
                    repo.latest_funding_rate(symbol, exchange="hyperliquid"),
                    "cross_venue_funding",
                )
                if is_derivatives_market
                else None
            )
            hl_pred = (
                fresh_row(
                    repo.latest_funding_rate(symbol, exchange="hyperliquid_predicted"),
                    "cross_venue_funding",
                )
                if is_derivatives_market
                else None
            )
            hl_oi = (
                fresh_row(
                    repo.latest_open_interest(symbol, exchange="hyperliquid"),
                    "cross_venue_open_interest",
                )
                if is_derivatives_market
                else None
            )
            if hl_fr or hl_pred or hl_oi:
                xvenue_funding[symbol] = {
                    "hl_funding_id": hl_fr.get("id") if hl_fr else None,
                    "hl_ann": hl_fr.get("funding_rate_annualized") if hl_fr else None,
                    "hl_predicted_ann": hl_pred.get("funding_rate_annualized") if hl_pred else None,
                    "primary_ann": lv.funding_annualized if lv else None,
                    "hl_open_interest_id": hl_oi.get("id") if hl_oi else None,
                    "hl_open_interest_usd": hl_oi.get("open_interest_usd") if hl_oi else None,
                    "hl_oi_change_1h_pct": hl_oi.get("oi_change_1h_pct") if hl_oi else None,
                    "hl_observed_at": hl_oi.get("observed_at")
                    if hl_oi
                    else (hl_fr.get("observed_at") if hl_fr else None),
                }
            if hl_fr and hl_fr.get("funding_rate_annualized") is not None:
                enrichment_sources["hyperliquid"].append(
                    {
                        "id": hl_fr.get("id"),
                        "kind": "funding",
                        "observed_at": hl_fr.get("observed_at"),
                        "exchange": hl_fr.get("exchange"),
                    }
                )
            if (
                hl_pred
                and hl_pred.get("funding_rate_annualized") is not None
                and hl_fr
                and hl_fr.get("funding_rate_annualized") is not None
            ):
                enrichment_sources["hyperliquid"].append(
                    {
                        "id": hl_pred.get("id"),
                        "kind": "predicted_funding",
                        "observed_at": hl_pred.get("observed_at"),
                        "exchange": hl_pred.get("exchange"),
                    }
                )
            if hl_oi and hl_oi.get("open_interest_usd") is not None:
                enrichment_sources["hyperliquid"].append(
                    {
                        "id": hl_oi.get("id"),
                        "kind": "open_interest",
                        "observed_at": hl_oi.get("observed_at"),
                        "exchange": hl_oi.get("exchange"),
                    }
                )

            # Position X / perp-structure evidence stays advisory-only: we pass the
            # freshest compact row into render_enriched_context_md(), which handles
            # old-style perp structure plus the newer Position X compression.
            perp_structure = {symbol: ps} if _is_perp_structure_fresh(ps) else {}
            if perp_structure:
                enrichment_sources["position_x"].append(
                    {
                        "id": ps.get("id"),
                        "source": ps.get("source"),
                        "source_ref": ps.get("source_ref"),
                        "source_hash": ps.get("source_hash"),
                        "as_of_time": ps.get("as_of_time"),
                    }
                )

            enriched = render_enriched_context_md(
                [symbol],
                tradability,
                leverage,
                macro,
                onchain=onchain_rows,
                xvenue_funding=xvenue_funding,
                perp_structure=perp_structure,
            )
            if enriched:
                context = context + "\n\n" + enriched
                for kind in ("macro", "onchain", "hyperliquid", "position_x"):
                    rendered_sources[kind] = enrichment_sources[kind]
            _step_times["enriched_context"] = _time.monotonic() - _t0
            logger.debug(
                "[MO] %s (%s): enriched context done in %.1fs", symbol, market_type, _step_times["enriched_context"]
            )
        except Exception as _e:
            _step_times["enriched_context"] = _time.monotonic() - _t0
            logger.debug(
                "[MO] %s (%s): enriched context skipped (%.1fs): %s",
                symbol,
                market_type,
                _step_times["enriched_context"],
                _e,
            )
            pass  # enriched context is optional; never break the main path

        # Append fresh prediction market signals relevant to this symbol.
        if prediction_context_enabled and prediction_context_cutoff:
            try:
                pred_lines, prediction_sources = _prediction_market_signal_context(symbol, prediction_context_cutoff)
                if pred_lines:
                    context += "\n\n## Prediction Market Signals\n" + "\n".join(pred_lines)
                    rendered_sources["prediction"] = prediction_sources
            except Exception:
                pass

        # Append recent news headlines for this symbol
        try:
            from aletheia.storage import repositories as repo

            # MO-12: only headlines from the last 48h. Without this, week-old news
            # surfaced as the "latest 3" and the LLM treated it as current context.
            # Use module-level datetime (not a local re-import) so simulation
            # harnesses can patch it via patch.object and enforce virtual clock time.
            news_cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
            news_rows = repo.batch_news([symbol], news_cutoff).get(symbol, [])
            if news_rows:
                news_lines = [
                    f"- [{r.get('published_at', '')[:10]}] {r.get('title', '')} ({r.get('source', '')})"
                    for r in news_rows
                ]
                context += "\n\n## Recent News\n" + "\n".join(news_lines)
                rendered_sources["news"] = [_news_rendered_source(row) for row in news_rows]
        except Exception:
            pass

    # P1-7: deterministic pre-filter — skip LLM for symbols with no actionable signal.
    # MO-14: force_full is passed in (computed once per run). load_config() has no
    # cache, so calling it per-symbol was a file read + YAML parse every iteration.
    pre_filter = _evaluate_pre_filter(ctx_debug)
    pre_filter_passed = _has_sufficient_signal(ctx_debug)
    try:
        from aletheia.analytics.filter_evidence import record_observer_pre_filter_evidence

        pre_filter_decision = "not_applicable" if force_full else "passed" if pre_filter_passed else "rejected"
        pre_filter_reason = (
            "force_full_bypass"
            if force_full
            else "+".join(pre_filter["matched_rules"])
            if pre_filter["matched_rules"]
            else "sufficient_signal"
            if pre_filter_passed
            else "no_sufficient_signal"
        )
        evidence_id = record_observer_pre_filter_evidence(
            run_id=run_id,
            symbol=symbol,
            venue=venue,
            market_type=market_type,
            scanner_direction=scanner_direction,
            scanner_family=scanner_family,
            universe_snapshot_id=universe_snapshot_id,
            universe_member_id=universe_member_id,
            decision=pre_filter_decision,
            reason_code=pre_filter_reason,
            inputs={
                "rsi": pre_filter["rsi"],
                "atr_pct": pre_filter["atr_pct"],
                "keyword_hits": pre_filter["keyword_hits"],
                "matched_rules": pre_filter["matched_rules"],
                "force_full": force_full,
            },
            parameters={
                "rsi_deviation": _PREFILTER_RSI_DEVIATION,
                "atr_pct": _PREFILTER_ATR_PCT,
            },
            entry_price_usd=member_entry_price_usd,
            entry_spread_bps=member_spread_bps,
            funding_rate_at_entry=funding_rate_val,
        )
        # record_filter_decision is observational by default and intentionally
        # swallows storage exceptions. A pre-filter rejection is different: once
        # the LLM is skipped, this row is the only durable explanation for why the
        # real instrument disappeared. Verify the returned identity exists before
        # treating the input as handled.
        from aletheia.storage.sqlite import get_db

        evidence_row = (
            get_db()
            .execute(
                "SELECT 1 FROM filter_decisions WHERE id = ?",
                (evidence_id,),
            )
            .fetchone()
        )
        if evidence_row is None:
            raise RuntimeError("observer_pre_filter_evidence_not_persisted")
    except Exception as exc:
        error_code = f"{_RETRYABLE_EVIDENCE_ERROR_PREFIX}{symbol}"
        logger.error("%s (%s)", error_code, type(exc).__name__)
        return None, error_code

    if not force_full and not pre_filter_passed:
        logger.debug("pre-filter: no sufficient signal for %s — skipping LLM call", symbol)
        return None, None

    # MO-2 (断口F fix): per-symbol guard advisory. Guards match on symbol +
    # scanner_family, which only exist per-symbol — the run-level memory_block was
    # built from a symbol LIST with no family, so no guard ever matched. Build it
    # here where scanner_family is known.
    # ``memory_block`` is retained only for call compatibility.  Historical
    # callers used it for learned memo/case text, so rendering it directly would
    # bypass the paper authority adapter.  Explicit non-Learning observations use
    # the separately named field below.
    sym_memory = non_learning_context_block
    guard_block = ""
    if scanner_family and learning_memory_block is not None:
        try:
            from aletheia.knowledge.router import build_guard_advisory

            guard_block = build_guard_advisory(
                {
                    "symbol": symbol,
                    "asset_class": asset_class,
                    "scanner_family": scanner_family,
                }
            )
        except Exception:
            pass
    from aletheia.learning.prompt_bundles import authorize_observer_learning_bundle

    learning_candidate_key = _observer_learning_candidate_key(
        run_id=run_id,
        venue=venue,
        market_type=market_type,
        symbol=symbol,
        universe_snapshot_id=universe_snapshot_id,
        universe_member_id=universe_member_id,
    )
    authorized_learning, learning_manifest = authorize_observer_learning_bundle(
        learning_memory_block or "",
        guard_block,
        candidate_key=learning_candidate_key,
        scope={
            "symbol": symbol,
            "market_type": market_type,
            "venue": venue,
            "scanner_family": scanner_family,
        },
        outcome_candidate_key=(f"{learning_outcome_candidate_key}:4h" if learning_outcome_candidate_key else None),
    )
    sym_memory = f"{non_learning_context_block}{authorized_learning}"

    # MO-4: tell the LLM which scanner family surfaced this symbol so it VALIDATES
    # that signal family rather than doing a generic technical read.
    scanner_hint = (
        f"\n\nThis symbol was surfaced by the '{scanner_family}' scanner — "
        f"focus your read on validating that signal family."
        if scanner_family
        else ""
    )

    context_artifacts = _record_observer_context_artifacts(
        run_id=run_id,
        symbol=symbol,
        context=context,
        learning_text=authorized_learning,
        learning_manifest=learning_manifest,
        rendered_sources=rendered_sources,
        technical_context=ctx_debug.get("technical_context"),
    )
    technical_adapter = (
        "technical-context-v1"
        if (ctx_debug.get("technical_context") or {}).get("artifacts")
        else "none"
    )
    record_step(
        run_id,
        f"market_read_input:{symbol}",
        input_data=(f"symbol={symbol} ctx_len={len(context)} "
                    f"memory_chars={len(sym_memory)} scanner={scanner_family or 'none'}"),
        output_data="context_metadata_recorded",
    )

    _t0 = _time.monotonic()
    logger.info(
        "[MO] %s (%s): starting LLM structured call, ctx_len=%d, memory=%d chars",
        symbol,
        market_type,
        len(context),
        len(sym_memory),
    )
    logger.debug(
        "[MO DIAG] %s (%s): LLM call start — ctx_len=%d, scanner=%s",
        symbol,
        market_type,
        len(context),
        scanner_family or "none",
    )
    read, err = await llm_structured(
        system=_SYSTEM_PROMPT,
        user=f"/no_think\nMarket data for {symbol}:\n{context}{sym_memory}{scanner_hint}\n\nReturn the market read JSON.",
        schema=MarketRead,
        max_tokens=2048,
        provider=get_provider_for_tier("deep"),
        caller="market_observer.market_read",
        context_adapter=technical_adapter,
        context_artifacts=context_artifacts,
        context_symbol=symbol,
        deadline=llm_deadline,
        reasoning_effort="none",
    )
    _llm_duration = _time.monotonic() - _t0
    _step_times["llm_structured"] = _llm_duration
    log_tool(run_id, "llm_structured", "observe", True)

    direction = "neutral"
    confidence = "speculative"
    confidence_score: int | None = None
    rationale = ""
    observations: list[str] = []

    if err or not read:
        # LLM unavailable — skip opportunity creation entirely.
        # Neutral/NULL signals pollute the DB and UI without adding signal value.
        llm_error = err or "no_structured_read"
        record_step(run_id, f"market_read_skipped:{symbol}", input_data=None, output_data=llm_error)
        logger.warning(
            "[MO] %s (%s): LLM call FAILED after %.1fs — error=%s", symbol, market_type, _llm_duration, llm_error
        )
        logger.debug(
            "[MO DIAG] %s (%s): LLM FAILED — dur=%.1fs, err=%s", symbol, market_type, _llm_duration, llm_error
        )
        return None, f"{market_type}:{symbol}:llm_unavailable: {llm_error}"

    logger.info(
        "[MO] %s (%s): LLM call SUCCESS in %.1fs — direction=%s, confidence=%s, score=%s",
        symbol,
        market_type,
        _llm_duration,
        read.get("direction_bias"),
        read.get("confidence"),
        read.get("confidence_score"),
    )
    logger.debug(
        "[MO DIAG] %s (%s): LLM OK — dur=%.1fs, dir=%s, conf=%s, score=%s",
        symbol,
        market_type,
        _llm_duration,
        read.get("direction_bias"),
        read.get("confidence"),
        read.get("confidence_score"),
    )

    llm_error = None
    d = str(read.get("direction_bias", "")).lower()
    c = str(read.get("confidence", "")).lower()
    if d in _VALID_DIRECTIONS:
        direction = d
    if c in _VALID_CONFIDENCE:
        confidence = c
    rationale = str(read.get("rationale", ""))
    cs = read.get("confidence_score")
    if isinstance(cs, (int, float)) and 0 <= cs <= 100:
        confidence_score = int(cs)
    obs = read.get("key_observations") or []
    if isinstance(obs, list):
        observations = [str(o)[:200] for o in obs[:4] if o]

    record_step(
        run_id,
        f"market_read_output:{symbol}",
        input_data=None,
        output_data=(f"direction={direction} confidence={confidence} score={confidence_score} obs={len(observations)}"),
    )

    if not is_tool_permitted("create_opportunity", permission_mode):
        return None, llm_error

    # Build thesis
    thesis_parts = []
    if rationale:
        thesis_parts.append(rationale)
    if observations:
        thesis_parts.append("Observations: " + " | ".join(observations))
    if confidence_score is not None:
        thesis_parts.append(f"score={confidence_score}/100")
    thesis = (
        " ".join(thesis_parts)
        if thesis_parts
        else f"No structured rationale — {symbol} market read (direction={direction})"
    )

    def _reuse_existing_opportunity(existing: dict) -> tuple[str, str | None]:
        """Return the durable owner, enriching it only while it remains open."""
        if existing.get("status") != "open":
            record_step(
                run_id,
                f"opportunity_already_terminal:{symbol}",
                input_data=None,
                output_data=(f"opportunity_id={existing['id']} status={existing.get('status')}"),
            )
            log_tool(
                run_id,
                "reuse_terminal_opportunity",
                "observe",
                True,
                output=existing["id"],
            )
            return existing["id"], llm_error
        try:
            existing_meta = json.loads(existing.get("metadata") or "{}")
            if not isinstance(existing_meta, dict):
                existing_meta = {}
        except (TypeError, ValueError):
            existing_meta = {}
        source_manifest = existing_meta.get("evidence_manifest")
        source_t0 = (
            source_manifest.get("t0")
            if isinstance(source_manifest, dict)
            and isinstance(source_manifest.get("t0"), dict)
            else {}
        )
        scanner_owned = (
            existing.get("source_agent") == "universe_scanner"
            or source_t0.get("trigger_type") == "universe_scanner"
        )
        source_direction = str(
            source_t0.get("direction")
            or scanner_direction
            or existing.get("direction_bias")
            or ""
        ).lower()
        direction_conflict = (
            scanner_owned
            and source_direction in {"long", "short"}
            and direction in {"long", "short"}
            and source_direction != direction
        )
        updates: dict = {
            "thesis": (
                f"[observer_direction_conflict scanner={source_direction} "
                f"observer={direction}] {thesis}"
                if direction_conflict
                else thesis
            )
        }
        if scanner_owned:
            # The deterministic signal owns direction and scanner-score semantics.
            # Observer output is a later assessment, not permission to reinterpret
            # the same lifecycle identity as the opposite trade or mix an LLM
            # confidence score into the scanner's composite ranking score.
            existing_meta["observer_assessment"] = {
                "version": "market-observer-assessment/v1",
                "direction_bias": direction,
                "confidence": confidence,
                "confidence_score": confidence_score,
                "scanner_direction": source_direction or None,
                "direction_conflict": direction_conflict,
            }
            if direction_conflict:
                record_step(
                    run_id,
                    f"observer_direction_conflict:{symbol}",
                    input_data=(
                        f"scanner_direction={source_direction} "
                        f"observer_direction={direction}"
                    ),
                    output_data="scanner_identity_preserved",
                )
                logger.info(
                    "observer direction conflict for %s: scanner=%s observer=%s; "
                    "preserving scanner-owned Opportunity identity",
                    symbol,
                    source_direction,
                    direction,
                )
        if learning_manifest:
            existing_meta["learning_manifest"] = learning_manifest
        existing_meta["evidence_manifest"] = _opportunity_evidence_manifest(
            metadata=existing_meta,
            rendered_sources=rendered_sources,
            run_id=run_id,
            universe_snapshot_id=universe_snapshot_id,
            universe_member_id=universe_member_id,
            scanner_family=scanner_family,
            pre_filter=pre_filter,
            pre_filter_evidence_id=evidence_id,
        )
        updates["metadata"] = json.dumps(existing_meta, sort_keys=True)
        if funding_rate_val is not None:
            updates["funding_rate"] = funding_rate_val
        if open_interest_val is not None:
            updates["open_interest"] = open_interest_val
        if oi_delta_val is not None:
            updates["oi_delta_1h"] = oi_delta_val
        if observations:
            updates["key_observations"] = json.dumps(observations)
        if confidence_score is not None and not scanner_owned:
            old_score = existing.get("confidence_score") or 0
            updates["confidence_score"] = max(old_score, confidence_score)
            updates["confidence"] = confidence
        if direction != "neutral" and not scanner_owned:
            updates["direction_bias"] = direction
        started_at = _time.monotonic()
        enriched = update_open_opportunity(existing["id"], **updates)
        duration_ms = elapsed_ms(started_at)
        if not enriched:
            log_tool(
                run_id,
                "reuse_non_open_opportunity",
                "observe",
                True,
                output=existing["id"],
                duration_ms=duration_ms,
            )
            return existing["id"], llm_error
        log_tool(
            run_id,
            "annotate_opportunity",
            "observe",
            True,
            output=existing["id"],
            duration_ms=duration_ms,
        )
        return existing["id"], llm_error

    # If a deterministic opportunity already exists for this symbol+snapshot+market,
    # never create a second lifecycle row.  An open opportunity may be annotated
    # with the LLM read; a terminal opportunity is immutable decision history and
    # must not be rewritten by a delayed observer pass.  Pass market so a
    # same-symbol opp from another market is not mistaken for this one's opp.
    # OD-S02 locks.
    if universe_snapshot_id:
        existing = find_opportunity_by_symbol_snapshot(
            symbol,
            universe_snapshot_id,
            market=instrument_market_to_db_market(market_type),
        )
        if existing:
            return _reuse_existing_opportunity(existing)

    # IB-24-12: standalone neutral opportunities add no signal value and get
    # filtered immediately by the ranker's neutral_bias gate. Skip creation.
    if direction == "neutral":
        record_step(
            run_id,
            f"neutral_skipped:{symbol}",
            input_data=None,
            output_data="direction=neutral standalone opp suppressed",
        )
        return None, llm_error

    # P0-2: fill scanner_family from LLM text when not set by universe member,
    # so the sideways regime tax can fire on non-universe opportunities.
    effective_scanner_family = scanner_family or _infer_scanner_family(rationale, observations)

    opportunity_metadata = {}
    if learning_outcome_candidate_key:
        opportunity_metadata["learning_candidate_key"] = learning_outcome_candidate_key
    if learning_manifest:
        opportunity_metadata["learning_manifest"] = learning_manifest
    opportunity_metadata["evidence_manifest"] = _opportunity_evidence_manifest(
        metadata=opportunity_metadata,
        rendered_sources=rendered_sources,
        run_id=run_id,
        universe_snapshot_id=universe_snapshot_id,
        universe_member_id=universe_member_id,
        scanner_family=effective_scanner_family,
        pre_filter=pre_filter,
        pre_filter_evidence_id=evidence_id,
    )
    # Resolve valid_until: prefer exact member expiry; fall back to family-TTL clock.
    if member_expires_at is not None:
        valid_until = member_expires_at
    else:
        from aletheia.universe.allocator import _FAMILY_TTL

        ttl_sec = _FAMILY_TTL.get(effective_scanner_family or "", 3600)
        valid_until = (datetime.now(timezone.utc) + timedelta(seconds=ttl_sec)).isoformat()

    opp = Opportunity(
        symbol=symbol,
        market=instrument_market_to_db_market(market_type),
        direction_bias=direction,
        thesis=thesis,
        confidence=confidence,
        confidence_score=confidence_score,
        source_agent="market_observer",
        linked_strategy_id=linked_strategy_id,
        asset_class=asset_class,
        metadata=json.dumps(opportunity_metadata, sort_keys=True),
        universe_snapshot_id=universe_snapshot_id,
        universe_member_id=universe_member_id,
        scanner_family=effective_scanner_family,
        valid_until=valid_until,
        key_observations=json.dumps(observations) if observations else "",
        funding_rate=funding_rate_val,
        open_interest=open_interest_val,
        oi_delta_1h=oi_delta_val,
    )
    try:
        from aletheia.analytics.filter_evidence import (
            atomic_opportunity_evidence,
            record_observer_opportunity_emitted_evidence,
        )

        persisted = None
        created = True
        started_at = _time.monotonic()
        with atomic_opportunity_evidence():
            if universe_snapshot_id:
                persisted, created = insert_opportunity_once_by_snapshot(opp)
            else:
                insert_opportunity(opp)
            if created:
                record_observer_opportunity_emitted_evidence(
                    run_id=run_id,
                    opportunity_id=opp.id,
                    symbol=symbol,
                    venue=venue,
                    market_type=market_type,
                    direction=direction,
                    scanner_family=effective_scanner_family,
                    universe_snapshot_id=universe_snapshot_id,
                    universe_member_id=universe_member_id,
                    confidence=confidence,
                    confidence_score=confidence_score,
                )
        duration_ms = elapsed_ms(started_at)
        if not created:
            return _reuse_existing_opportunity(persisted)
    except Exception as exc:
        error_code = f"{_RETRYABLE_EMISSION_ERROR_PREFIX}{symbol}"
        logger.error("%s (%s)", error_code, type(exc).__name__)
        return None, error_code
    try:
        from aletheia.agent_runtime.scheduler import notify_paper_trader

        notify_paper_trader(source="market_observer_opportunity")
    except Exception:
        logger.debug("Failed to notify Ranker after Opportunity commit", exc_info=True)
    log_tool(
        run_id,
        "create_opportunity",
        "observe",
        True,
        output=opp.id,
        duration_ms=duration_ms,
    )
    _total_duration = _time.monotonic() - _t_start
    _step_times["total"] = _total_duration
    logger.info(
        "[MO] %s (%s): DONE — total=%.1fs, steps=%s, opp_id=%s",
        symbol,
        market_type,
        _total_duration,
        {k: f"{v:.1f}s" for k, v in _step_times.items()},
        opp.id,
    )
    return opp.id, llm_error


@register_agent("market_observer")
async def run_market_observer(permission_mode: str = "Observe", run_id: str | None = None, **kwargs) -> dict:
    """Observe market state and create one Opportunity per symbol."""
    import time as _time

    from aletheia.learning.opportunity_information_policy import (
        ensure_opportunity_information_policy_epoch,
    )

    ensure_opportunity_information_policy_epoch()
    _run_start = _monotonic()
    # One run-level deadline covers setup, batch reads, parallel prefetch, and the
    # serial LLM loop.  The 40-second difference to the scheduler timeout is kept
    # for cancellation/DB cleanup and event-loop teardown.
    _run_deadline = _run_start + _MARKET_OBSERVER_LOOP_BUDGET_SEC
    outputs: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []
    artifact_refs: list[dict[str, str]] = []
    _success_count = 0
    _fail_count = 0
    _skip_count = 0

    logger.info("[MO] run started: permission=%s, run_id=%s", permission_mode, run_id)

    if not is_tool_permitted("get_market_snapshot", permission_mode):
        errors.append("get_market_snapshot not permitted in current mode")
        logger.warning("[MO] run aborted: no permission for get_market_snapshot")
        return {"outputs": outputs, "errors": errors}

    cfg = load_config()

    # Universe-driven path: if enabled, get symbols from the dynamic pool
    universe_members: list[dict] = []
    universe_snapshot_id: str | None = None
    if cfg.universe.enabled:
        from aletheia.universe.service import UniverseService

        svc = UniverseService()
        universe_members = svc.get_current_universe(limit=cfg.universe.max_candidates)
        if universe_members and universe_members[0].get("snapshot_id"):
            universe_snapshot_id = universe_members[0]["snapshot_id"]

    if universe_members and universe_snapshot_id:
        from aletheia.data.symbols import InstrumentId, MARKET_SPOT, to_canonical

        instruments = []
        for m in universe_members:
            sym = m.get("symbol", "")
            if not sym:
                continue
            mt = m.get("market_type", "spot") or MARKET_SPOT
            instruments.append(InstrumentId.from_parts(cfg.market_data.ccxt_exchange, sym, mt))
    else:
        from aletheia.data.symbols import to_canonical

        instruments = observed_instruments_from_config(cfg)
        universe_members = []

    from aletheia.agents.round_robin import DurableRoundRobin, rotate_from_next

    instrument_key = str
    round_robin = DurableRoundRobin(_ROUND_ROBIN_STATE_KEY)
    instruments = rotate_from_next(
        instruments,
        next_key=round_robin.next_key(),
        key=instrument_key,
    )
    symbols = [i.symbol for i in instruments]  # canonical list for memory query

    # Build symbol→strategy map so each opportunity links to a matching strategy.
    _strategy_by_symbol: dict[str, str] = {}
    for s in list_strategies(limit=50):
        if is_active_paper_strategy(s):
            params_raw = s.get("parameters") or "{}"
            try:
                import json as _json_mod

                params = _json_mod.loads(params_raw) if isinstance(params_raw, str) else params_raw
            except (ValueError, TypeError):
                params = {}
            strat_sym = params.get("symbol")
            if strat_sym:
                _strategy_by_symbol[to_canonical(strat_sym)] = s["id"]
            else:
                _strategy_by_symbol.setdefault("__generic__", s["id"])

    # Experience-loop advisory context (lessons + case studies + guard advisories)
    memory_block = ""
    try:
        from aletheia.knowledge.router import build_advisory_context

        # Guard advisory is now built PER-SYMBOL inside _analyze_symbol (guards match
        # on symbol + scanner_family, absent here where we only have a symbol LIST —
        # this is why the old guard_context={"symbols": [...]} never matched, 断口F).
        # The run-level block carries only symbol-agnostic lessons / case studies.
        started_at = _time.monotonic()
        memory_block = build_advisory_context(
            role="market_observer",
            query=f"{' '.join(symbols)} market regime risk funding volatility",
            k=3,
        )
        log_tool(
            run_id,
            "retrieve_lessons",
            "observe",
            True,
            output=f"lessons:{1 if memory_block else 0}",
            duration_ms=elapsed_ms(started_at),
        )
    except Exception:
        pass

    # Trading Memo — historical performance insights (soft guidance for direction calibration)
    try:
        from aletheia.analytics.insight_synthesizer import get_latest_memo, render_memo_for_observer

        memo = get_latest_memo()
        if memo:
            memory_block += render_memo_for_observer(memo, compact=True)
    except Exception:
        pass

    # This is only a proposed Learning bundle. Per-instrument paper authority,
    # treatment/control assignment, and exposure recording happen immediately
    # before the LLM prompt is rendered in _analyze_symbol().
    learning_memory_block = memory_block
    memory_block = ""
    non_learning_context_block = ""

    prediction_context_enabled = bool(cfg.market_data.prediction_enabled)
    prediction_context_cutoff = _prediction_cutoff_iso()
    if prediction_context_enabled:
        try:
            from aletheia.storage.repositories import list_prediction_claims

            pred_claims = list_prediction_claims(
                claim_type="probability_momentum",
                since=prediction_context_cutoff,
                limit=5,
            )
            if pred_claims:
                pred_lines = ["\n\n## Prediction Market Context (observation-only, fresh)"]
                for pc in pred_claims[:5]:
                    direction = pc.get("direction", "")
                    pred_lines.append(f"- {pc['claim_text']} [{direction}]")
                non_learning_context_block += "\n".join(pred_lines)
        except Exception:
            pass

    # Build a lookup for universe member metadata (symbol → member dict)
    _universe_lookup = {m["symbol"]: m for m in universe_members} if universe_members else {}

    # Round 2: the observer emits opportunities for BOTH spot and perp instruments.
    # Full futures support is now in place, so the prior spot-only guard is lifted:
    #   - leverage + liquidation persisted on the Position (F1);
    #   - PI chooses market_type + leverage, clamped to the per-tier cap (F2), with a
    #     futures context block (F3) and funding/OI on the Opportunity (F4);
    #   - the exchange leverage is set fail-closed before a leveraged open (set_leverage);
    #   - the risk engine enforces max-leverage / account-leverage-ratio / liquidation
    #     buffer (#10/#17/#18) and per-symbol cumulative notional (#19).
    # A perp instrument's Opportunity.market is "usdt_futures" (instrument_market_to_db_market).
    # Per-instrument loop: each instrument gets its own LLM call + Opportunity row.
    # instrument.market_type is the authoritative type; no more config-derived guessing.
    #
    # WALL-CLOCK BUDGET (gateway-stability fix): each instrument is one structured LLM call
    # capped below this loop budget even when its provider permits a longer research timeout. With a large
    # universe this can blow the 500s job timeout → _run_async_with_timeout cancels the
    # coroutine mid-flight → SelectorEventLoop teardown stalls → the daemon thread is
    # abandoned and LEAKS (the "abandoned threads 3→5" cascade that starves the frontdoor).
    # Capping the loop wall-clock < the job timeout makes the coroutine ALWAYS return
    # normally before cancellation → clean teardown, no leak. Skipped instruments are
    # picked up next 10-minute cycle (logged, not silent).
    # MO-14: compute force_full ONCE per run (load_config has no cache; per-symbol
    # calls were a file read + YAML parse each iteration).
    _force_full = getattr(getattr(cfg, "observer", None), "force_full_scan", False)
    _venue = cfg.market_data.ccxt_exchange

    # ── Phase 1: batch DB prefetch (sync, all symbols at once) ──────────────────────
    # Replaces 16× individual DB round-trips for perp_structure, HL funding rates,
    # and news queries with 4 batched queries at run start.
    _all_symbols = [i.symbol for i in instruments]
    try:
        _db_cache = _batch_prefetch_db(_all_symbols)
        logger.info(
            "[MO] batch DB prefetch done: %d perp, %d hl_fr, %d hl_pred",
            len(_db_cache.perp_structures),
            len(_db_cache.funding_hl),
            len(_db_cache.funding_hl_pred),
        )
    except Exception as _e:
        logger.warning("[MO] batch DB prefetch failed, using empty cache: %s", _e)
        _db_cache = _DbCache(
            perp_structures={},
            funding_hl={},
            funding_hl_pred={},
            news={},
            onchain_rows=[],
        )

    # ── Phase 2: bounded async context build + pre-filter ────────────────────────────
    # One fresh run-scoped provider coordinates exchange rate limiting/session reuse.
    # Bounded symbol fan-out avoids the former 24-26-provider / 72-78-request stampede.
    _t_prefetch_start = _time.monotonic()
    try:
        _prefetch_batch = await _gather_prefetch_with_deadline(
            [
                _prefetch_instruments(
                    instruments,
                    venue=_venue,
                    prediction_context_enabled=prediction_context_enabled,
                    prediction_context_cutoff=prediction_context_cutoff,
                    db_cache=_db_cache,
                )
            ],
            deadline=_run_deadline,
        )
        if isinstance(_prefetch_batch[0], Exception):
            raise _prefetch_batch[0]
        _prefetch_raw = _prefetch_batch[0]
    except TimeoutError:
        warnings.append(
            "prefetch_budget_exceeded: parallel context build consumed the "
            f"{_MARKET_OBSERVER_LOOP_BUDGET_SEC:.0f}s run budget"
        )
        logger.warning(
            "market_observer prefetch exhausted the %.0fs run budget; returning before the %.0fs scheduler timeout",
            _MARKET_OBSERVER_LOOP_BUDGET_SEC,
            _MARKET_OBSERVER_JOB_TIMEOUT_S,
        )
        return {"outputs": outputs, "errors": errors, "warnings": warnings}
    except Exception as exc:
        errors.append(f"prefetch_failed:{type(exc).__name__}")
        logger.warning("market_observer prefetch batch failed: %s", exc)
        return {"outputs": outputs, "errors": errors, "warnings": warnings}
    _prefetch_wall = _time.monotonic() - _t_prefetch_start
    logger.debug("[MO DIAG] parallel prefetch done: %.1fs for %d symbols", _prefetch_wall, len(instruments))

    _prefetch_map: dict[str, _SymbolPrefetch] = {}
    _empty_pf: dict = {
        "passed": False,
        "rsi": None,
        "atr_pct": None,
        "keyword_hits": [],
        "matched_rules": [],
    }
    for _instr, _result in zip(instruments, _prefetch_raw):
        _ikey = instrument_key(_instr)
        if isinstance(_result, Exception):
            logger.warning("[MO] prefetch error for %s: %s", _ikey, _result)
            _prefetch_map[_ikey] = _SymbolPrefetch(
                context_str="",
                debug={},
                pre_filter=_empty_pf,
                signal_score=0.0,
                funding_rate_val=None,
                open_interest_val=None,
                oi_delta_val=None,
                failed=True,
            )
        else:
            _prefetch_map[_ikey] = _result

    # ── Phase 3: signal-strength sort ────────────────────────────────────────────────
    # Keep the durable next instrument as a mandatory prefix, then sort the rest by
    # signal score.  This preserves signal priority without permanently starving a
    # low-score instrument when every run is truncated by the wall-clock budget.
    # force_full bypasses the pre-filter gate → keep original RR order for consistency.
    if _force_full:
        processing_order = list(instruments)
    else:
        processing_order = _fair_signal_order(
            instruments,
            score=lambda _i: _prefetch_map[instrument_key(_i)].signal_score,
            key=instrument_key,
        )
    logger.debug(
        "[MO DIAG] run start: %d instruments, force_full=%s, budget=%.0fs",
        len(instruments),
        _force_full,
        _MARKET_OBSERVER_LOOP_BUDGET_SEC,
    )

    # ── Phase 4: bounded-concurrent LLM loop ────────────────────────────────────────
    # WALL-CLOCK BUDGET: reuse the deadline created before prefetch; never grant
    # the loop a second 460-second window.  Tasks launched after the deadline
    # are counted as skipped (same semantics as the former serial loop).
    #
    # CONCURRENCY: _MARKET_OBSERVER_LLM_CONCURRENCY (default 2) tasks run in
    # parallel, matching the LLM broker's max_concurrency so extra coroutines
    # queue inside the broker rather than at this layer.  For a 22-symbol run
    # with ~20s average LLM latency, this roughly halves total elapsed time
    # (22 × 20s serial → ~11 × 20s = 220s with concurrency 2).
    #
    # ROUND-ROBIN: _completed_attempt_keys and _next_unprocessed_index are now
    # shared across concurrent tasks; guarded by _rr_lock so the durable cursor
    # advances correctly even when tasks complete out of order.
    #
    # SQLITE: all DB writes inside _analyze_symbol go through _SerializedConnection
    # which acquires _WRITE_LOCK per-statement — safe for concurrent coroutines on
    # the same event-loop thread (asyncio switches only at await points).
    _completed_attempt_keys: set[str] = set()
    _next_unprocessed_index = 0
    _rr_lock = asyncio.Lock()
    _llm_sem = asyncio.Semaphore(_MARKET_OBSERVER_LLM_CONCURRENCY)
    _skipped_count = 0
    # Tracks how many symbols have not yet entered the semaphore slot (i.e., not
    # yet started their LLM call).  Used to compute a fair per-symbol budget:
    # fair_budget = remaining_run_time / remaining_parallel_waves so that early
    # slow calls shorten later budgets without pretending the two slots are serial.
    _pending_count = len(processing_order)
    _active_count = 0
    # Run-local EWMA of observed LLM call duration (seconds).  Updated after each
    # completed call; used to detect budget pressure and skip low-signal symbols
    # when the remaining budget is likely insufficient for the full queue.
    # Seed from persisted p50 so the first run after a restart has a meaningful
    # initial estimate rather than starting blind.  The run-local EWMA then
    # updates this with fresh observations as calls complete.
    _llm_ewma_s: float | None = _read_observer_llm_latency_p50()

    async def _run_one(instrument) -> None:
        nonlocal _success_count, _fail_count, _skip_count, _skipped_count
        nonlocal _next_unprocessed_index, _pending_count, _active_count, _llm_ewma_s
        # Each slot first checks the budget before acquiring the semaphore.
        # This prevents a task from queuing inside the semaphore past the deadline.
        if _monotonic() >= _run_deadline:
            async with _rr_lock:
                _skipped_count += 1
            return
        async with _llm_sem:
            # Re-check after acquiring the slot — a previous task may have consumed
            # the remaining budget while this one waited.
            if _monotonic() >= _run_deadline:
                async with _rr_lock:
                    _skipped_count += 1
                return
            # Per-symbol LLM deadline uses a three-way min:
            #   (a) _MARKET_OBSERVER_PER_SYMBOL_LLM_CAP_SEC — absolute hard cap
            #       so one slow provider call never starves the whole queue.
            #   (b) fair_budget = remaining_run_time / parallel waves — distributes
            #       the remaining budget across the bounded concurrent slots so an
            #       early slow wave shortens later symbols without halving every
            #       deadline merely because two calls run at once.
            #       Clamped to a 10s floor so the very last symbol always gets a
            #       meaningful attempt.
            #   (c) remaining run budget — never exceeds the job-level deadline.
            _loop_now = asyncio.get_event_loop().time()
            _remaining_run_s = max(1.0, _run_deadline - _monotonic())
            # Count queued + already-active work so the budget represents actual
            # remaining waves. Then remove this item from the not-yet-started set.
            async with _rr_lock:
                _local_outstanding = max(1, _pending_count + _active_count)
                _pending_count -= 1
            _fair_budget_s = _fair_llm_budget_seconds(
                _remaining_run_s,
                _local_outstanding,
            )
            _per_symbol_llm_deadline = _loop_now + min(
                _MARKET_OBSERVER_PER_SYMBOL_LLM_CAP_SEC,
                _fair_budget_s,
                _remaining_run_s,
            )
            # Pressure-skip: if we have a latency estimate AND the expected
            # remaining work (ewma * pending) exceeds the remaining budget by
            # more than a safety factor, skip low-signal symbols immediately
            # so high-signal ones are not crowded out.
            _prefetched = _prefetch_map.get(instrument_key(instrument))
            _sym_signal = _prefetched.signal_score if _prefetched is not None and not _prefetched.failed else 0.0
            async with _rr_lock:
                _ewma_snapshot = _llm_ewma_s
                _pending_snapshot = max(1, _pending_count)
            if _ewma_snapshot is not None and _sym_signal < _MARKET_OBSERVER_SIGNAL_PRESSURE_THRESHOLD:
                _remaining_s = _run_deadline - _monotonic()
                # Estimate work outstanding: ewma * (concurrent slots already in
                # flight are not counted — they'll finish regardless).  Use a 1.5x
                # safety factor to absorb estimation error.
                _estimated_work_s = _ewma_snapshot * _pending_snapshot * 1.5
                if _estimated_work_s > _remaining_s:
                    logger.info(
                        "[MO] %s: pressure-skip (signal=%.2f < %.2f, est_work=%.0fs > remaining=%.0fs)",
                        instrument.symbol,
                        _sym_signal,
                        _MARKET_OBSERVER_SIGNAL_PRESSURE_THRESHOLD,
                        _estimated_work_s,
                        _remaining_s,
                    )
                    async with _rr_lock:
                        _skip_count += 1
                    return
            completed_attempt = False
            async with _rr_lock:
                _active_count += 1
            try:
                u_member = _universe_lookup.get(instrument.symbol)
                _sym_canonical = to_canonical(instrument.symbol)
                _linked_sid = _strategy_by_symbol.get(_sym_canonical) or _strategy_by_symbol.get("__generic__")
                _t_llm_start = _monotonic()
                opp_id, err = await _analyze_symbol(
                    symbol=instrument.symbol,
                    market_type=instrument.market_type,
                    permission_mode=permission_mode,
                    run_id=run_id,
                    linked_strategy_id=_linked_sid,
                    memory_block=memory_block,
                    asset_class="crypto",
                    universe_snapshot_id=universe_snapshot_id,
                    universe_member_id=u_member.get("id") if u_member else None,
                    scanner_family=u_member.get("scanner_family") if u_member else None,
                    scanner_direction=u_member.get("scanner_direction") if u_member else None,
                    member_entry_price_usd=u_member.get("entry_price_usd") if u_member else None,
                    member_spread_bps=u_member.get("spread_bps") if u_member else None,
                    member_expires_at=u_member.get("expires_at") if u_member else None,
                    venue=_venue,
                    force_full=_force_full,
                    prediction_context_enabled=prediction_context_enabled,
                    prediction_context_cutoff=prediction_context_cutoff,
                    learning_memory_block=learning_memory_block,
                    learning_outcome_candidate_key=(u_member.get("learning_candidate_key") if u_member else None),
                    non_learning_context_block=non_learning_context_block,
                    _prefetched=_prefetched,
                    llm_deadline=_per_symbol_llm_deadline,
                )
                # Update run-local EWMA with the observed LLM call duration.
                # Only count completed calls (not pre-filter skips) so the
                # estimate reflects real LLM latency, not short-circuit paths.
                _call_duration_s = _monotonic() - _t_llm_start
                if err is None or (err and "pre_filter_skip" not in err):
                    # Persist to rolling history (T5) and update run-local EWMA (T2).
                    # _record_observer_llm_latency is best-effort and swallows exceptions.
                    _record_observer_llm_latency(_call_duration_s)
                    async with _rr_lock:
                        if _llm_ewma_s is None:
                            _llm_ewma_s = _call_duration_s
                        else:
                            _llm_ewma_s = (
                                _MARKET_OBSERVER_LLM_EWMA_ALPHA * _call_duration_s
                                + (1 - _MARKET_OBSERVER_LLM_EWMA_ALPHA) * _llm_ewma_s
                            )
                if opp_id:
                    async with _rr_lock:
                        outputs.append(f"opportunity:{opp_id}")
                        artifact_refs.append({"artifact_type": "opportunity", "artifact_id": opp_id})
                        _success_count += 1
                elif err and "pre_filter_skip" in err:
                    async with _rr_lock:
                        _skip_count += 1
                elif err:
                    async with _rr_lock:
                        _fail_count += 1
                        errors.append(f"{instrument}:{err}")
                completed_attempt = not bool(
                    err
                    and err.startswith(
                        (
                            _RETRYABLE_EVIDENCE_ERROR_PREFIX,
                            _RETRYABLE_EMISSION_ERROR_PREFIX,
                        )
                    )
                )
            except Exception as e:
                async with _rr_lock:
                    _fail_count += 1
                    errors.append(f"{instrument}:unexpected:{e}")
                completed_attempt = True
            finally:
                # Cancellation or retryable evidence failure must not advance past an
                # in-flight item.  Successful out-of-order attempts are remembered,
                # but the durable cursor moves only through the completed canonical
                # prefix — guarded by _rr_lock for concurrent access.
                async with _rr_lock:
                    _active_count = max(0, _active_count - 1)
                    if completed_attempt:
                        _completed_attempt_keys.add(instrument_key(instrument))
                        _next_unprocessed_index = _advance_completed_round_robin_prefix(
                            round_robin,
                            instruments,
                            completed_keys=_completed_attempt_keys,
                            next_unprocessed_index=_next_unprocessed_index,
                            key=instrument_key,
                        )

    await asyncio.gather(
        *(_run_one(instrument) for instrument in processing_order),
        return_exceptions=True,
    )

    if _skipped_count > 0:
        warnings.append(
            f"loop_budget_exceeded: processed {len(processing_order) - _skipped_count}/"
            f"{len(processing_order)}, skipped {_skipped_count} "
            f"(wall-clock cap {_MARKET_OBSERVER_LOOP_BUDGET_SEC:.0f}s "
            f"< {_MARKET_OBSERVER_JOB_TIMEOUT_S:.0f}s job timeout — avoids thread leak)"
        )
        logger.warning(
            "market_observer loop budget hit; skipped %d/%d instruments (%.0fs cap)",
            _skipped_count,
            len(processing_order),
            _MARKET_OBSERVER_LOOP_BUDGET_SEC,
        )

    return {
        "outputs": outputs,
        "errors": errors,
        "warnings": warnings,
        "output_artifacts": artifact_refs,
    }
