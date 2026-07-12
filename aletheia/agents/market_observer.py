"""Market Observer agent — observes market conditions and creates opportunities.

Per-symbol loop: each symbol gets its own LLM MarketRead call and its own
Opportunity row. This fixes the original "only BTC" bug where all symbol data
was sent to the LLM together but only symbols[0] was used for the resulting
opportunity.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field

from aletheia.agents import register_agent
from aletheia.agents.helpers import llm_structured, log_tool, record_step
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.data.freshness import fresh_row
from aletheia.model_provider.router import get_provider_for_tier
from aletheia.storage.models import Opportunity
from aletheia.storage.repositories import (
    insert_opportunity, list_strategies,
    find_open_opportunity_by_symbol_snapshot, update_opportunity,
)
from aletheia.trading.instruments import (
    observed_instruments_from_config, instrument_market_to_db_market,
)
from aletheia.trading.strategy_lifecycle import is_active_paper_strategy
from aletheia.config.loader import load_config

logger = logging.getLogger("aletheia.agents.market_observer")

_VALID_DIRECTIONS = {"long", "short", "neutral"}
_VALID_CONFIDENCE = {"high", "medium", "speculative"}

# Wall-clock budget for the per-instrument loop. Must stay BELOW the scheduler's
# market_observer job timeout (_ASYNC_JOB_TIMEOUT_SEC["market_observer"] = 240s in
# agent_runtime/scheduler.py) with margin, so the coroutine returns normally before
# asyncio.wait_for cancels it — preventing the abandoned-thread leak. Keep in sync if
# the job timeout changes.
_MARKET_OBSERVER_JOB_TIMEOUT_S = 240.0
_MARKET_OBSERVER_LOOP_BUDGET_SEC = 200.0
_PREDICTION_CONTEXT_MAX_AGE_HOURS = 6
_ROUND_ROBIN_STATE_KEY = "agent.market_observer.round_robin.v1"


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
    confidence_score: int = Field(
        50, ge=0, le=100,
        description="0=no idea, 50=balanced, 100=overwhelming evidence"
    )
    rationale: str = ""
    key_observations: list[str] = Field(
        default_factory=list,
        description="2-4 bullets citing specific numbers from the snapshot"
    )


def _is_perp_structure_fresh(row: dict | None) -> bool:
    return fresh_row(
        row,
        "perp_structure",
        timestamp_field="as_of_time" if row and row.get("as_of_time") else "observed_at",
    ) is not None


# ROUND 2 SIGNAL-INTRODUCTION DECISION (per plan §8.6.2, spec MS-S15):
# Round 2 introduced new enriched_context signals — LSR (long_short_ratio) via the
# derivatives table's "| LSR |" column, F&G via the macro block's "Fear & Greed:" line,
# and a "24h movers:" line from CoinGecko top movers. _SYSTEM_PROMPT BELOW IS DELIBERATELY
# NOT UPDATED to brief the LLM on these field semantics. Injecting field-specific priors
# (e.g. "LSR>1 means retail futures crowded long — typically a contra-signal") would be
# the exact kind of intervention Round 1 was terminated for (hallucinated learning loop,
# see [[aletheia-round1-terminated-fix-batch]]). The LLM reads the markdown table headers
# and lines directly; the "Use this snapshot as your single source of truth" rule below
# already constrains it. Re-evaluate this decision after one Round 2 experiment window.
_SYSTEM_PROMPT = """\
You are a crypto market observer for Aletheia paper-trading.

You will receive a verified market snapshot for ONE symbol containing:
latest price, multi-timeframe technical indicators (SMA/RSI/ATR/Bollinger/
momentum), recent close series. Use this snapshot as your single source of
truth — do not invent numbers or claim levels not in the data.

Output a structured market read. Calibration:

- direction_bias:
  - "long" if multiple indicators across timeframes point up — e.g. price > SMA_50,
    RSI 50-70 with positive slope, positive 4h momentum, leading relative-strength rank.
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
_PREFILTER_KEYWORDS = (
    "breakout", "breakdown", "oversold", "overbought", "squeeze",
    "divergence", "spike",
)


def _evaluate_pre_filter(context_str: str) -> dict:
    """Return the exact point-in-time evidence behind the Observer pre-filter."""
    import re

    rsi_value: float | None = None
    atr_pct: float | None = None
    matched_rules: list[str] = []

    rsi_match = re.search(r'RSI[^|]*\|[^|]*\|[^|]*?(\d+\.?\d*)', context_str, re.IGNORECASE)
    if rsi_match:
        try:
            rsi_value = float(rsi_match.group(1))
            if abs(rsi_value - 50) > _PREFILTER_RSI_DEVIATION:
                matched_rules.append("rsi_deviation")
        except ValueError:
            rsi_value = None

    atr_match = re.search(r'ATR[^|]*\|[^|]*?(\d+\.?\d+)%', context_str, re.IGNORECASE)
    if atr_match:
        try:
            atr_pct = float(atr_match.group(1))
            if atr_pct > _PREFILTER_ATR_PCT:
                matched_rules.append("atr_pct")
        except ValueError:
            atr_pct = None

    context_lower = context_str.lower()
    keyword_hits = [kw for kw in _PREFILTER_KEYWORDS if kw in context_lower]
    if keyword_hits:
        matched_rules.append("keyword")

    return {
        "passed": bool(matched_rules),
        "rsi": rsi_value,
        "atr_pct": atr_pct,
        "keyword_hits": keyword_hits,
        "matched_rules": matched_rules,
    }


def _has_sufficient_signal(context_str: str) -> bool:
    """Deterministic pre-filter: returns True if the context contains actionable signals.

    Checks RSI deviation from 50, ATR%, and momentum keywords.
    Used to skip the LLM call for calm markets (P1-7 — saves wall-clock budget).
    """
    return bool(_evaluate_pre_filter(context_str)["passed"])


def _prediction_cutoff_iso(max_age_hours: int = _PREDICTION_CONTEXT_MAX_AGE_HOURS) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()


def _prediction_market_signal_lines(
    symbol: str,
    cutoff_iso: str,
    limit: int = 3,
) -> list[str]:
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
    for r in pred_rows:
        q = (r.get("question") or "")[:80]
        yes = (r.get("yes_probability") or 0) * 100
        delta = (r.get("probability_change_24h") or 0) * 100
        vol = r.get("volume_24h") or 0
        updated = (r.get("updated_at") or "")[:16]
        lines.append(
            f"- {q}: YES={yes:.0f}% (Delta24h {delta:+.1f}pp, "
            f"vol=${vol:,.0f}, updated={updated})"
        )
    return lines


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
    force_full: bool = False,
    prediction_context_enabled: bool = False,
    prediction_context_cutoff: str | None = None,
    learning_memory_block: str | None = None,
) -> tuple[str | None, str | None]:
    """Run market context build + LLM read for a single symbol.

    Returns (opportunity_id, error_str). Both may be None if the symbol is
    simply skipped (no permission, neutral signal, etc.).
    """
    from aletheia.trading.market_context import build_market_context

    # Derivatives state for the Opportunity row (perp only; stays None for spot).
    funding_rate_val: float | None = None
    open_interest_val: float | None = None
    oi_delta_val: float | None = None

    try:
        context, ctx_debug = await build_market_context(
            [symbol], market_type=market_type, exchange=venue, provider=provider
        )
        log_tool(run_id, "build_market_context", "observe", True,
                 output=f"{symbol} fallback={ctx_debug.get('fallback')} "
                        f"errs={len(ctx_debug.get('errors', []))}")

        # Append enriched microstructure/derivatives/macro context if available.
        try:
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
                [symbol], exchange=venue or "", market_type=market_type,
            )
            ps = repo.latest_perp_structure(symbol)
            tradability = {
                sym: TradabilityScore(**row)
                for sym, row in enriched_snapshot.tradability.items()
            }
            leverage = {
                sym: LeverageState(**row)
                for sym, row in enriched_snapshot.leverage.items()
            }
            macro = (
                MacroSnapshot(**enriched_snapshot.macro)
                if enriched_snapshot.macro else None
            )

            # Only freshness-qualified derivatives are copied into Opportunity rows.
            lv = leverage.get(symbol)
            if lv:
                funding_rate_val = lv.funding_rate
                open_interest_val = lv.open_interest_usd
                oi_delta_val = lv.oi_delta_1h_pct

            # On-chain regime (DefiLlama stablecoin/TVL/DEX) — global, not per-symbol.
            onchain_rows = fresh_rows(
                repo.latest_onchain_snapshots(limit=3), "onchain",
            )

            # Cross-venue funding: route the Hyperliquid feed (otherwise orphan) into
            # the decision context as a basis/sentiment-divergence signal vs the
            # primary venue. Compared annualized (HL 1h vs primary 8h interval).
            xvenue_funding: dict = {}
            hl_fr = fresh_row(
                repo.latest_funding_rate(symbol, exchange="hyperliquid"),
                "cross_venue_funding",
            )
            hl_pred = fresh_row(
                repo.latest_funding_rate(symbol, exchange="hyperliquid_predicted"),
                "cross_venue_funding",
            )
            if hl_fr or hl_pred:
                xvenue_funding[symbol] = {
                    "hl_ann": hl_fr.get("funding_rate_annualized") if hl_fr else None,
                    "hl_predicted_ann": hl_pred.get("funding_rate_annualized") if hl_pred else None,
                    "primary_ann": lv.funding_annualized if lv else None,
                }

            # Position X / perp-structure evidence stays advisory-only: we pass the
            # freshest compact row into render_enriched_context_md(), which handles
            # old-style perp structure plus the newer Position X compression.
            perp_structure = {symbol: ps} if _is_perp_structure_fresh(ps) else {}

            enriched = render_enriched_context_md(
                [symbol], tradability, leverage, macro,
                onchain=onchain_rows,
                xvenue_funding=xvenue_funding,
                perp_structure=perp_structure,
            )
            if enriched:
                context = context + "\n\n" + enriched
        except Exception:
            pass  # enriched context is optional; never break the main path

        # Append fresh prediction market signals relevant to this symbol.
        if prediction_context_enabled and prediction_context_cutoff:
            try:
                pred_lines = _prediction_market_signal_lines(symbol, prediction_context_cutoff)
                if pred_lines:
                    context += "\n\n## Prediction Market Signals\n" + "\n".join(pred_lines)
            except Exception:
                pass

        # Append recent news headlines for this symbol
        try:
            from aletheia.storage.sqlite import get_db
            from datetime import datetime as _dt, timedelta as _td, timezone as _tz
            news_db = get_db()
            # MO-12: only headlines from the last 48h. Without this, week-old news
            # surfaced as the "latest 3" and the LLM treated it as current context.
            news_cutoff = (_dt.now(_tz.utc) - _td(hours=48)).isoformat()
            news_rows = news_db.execute(
                "SELECT title, source, created_at FROM news_items "
                "WHERE symbols_mentioned LIKE ? AND created_at > ? "
                "ORDER BY created_at DESC LIMIT 3",
                (f"%{symbol.split('/')[0]}%", news_cutoff),
            ).fetchall()
            if news_rows:
                news_lines = [f"- [{r[2][:10]}] {r[0]} ({r[1]})" for r in news_rows]
                context += "\n\n## Recent News\n" + "\n".join(news_lines)
        except Exception:
            pass

    except Exception as e:
        from aletheia.trading.market_data import latest_snapshot
        snap = latest_snapshot(symbol)
        log_tool(run_id, "get_market_snapshot", "observe", True, output=symbol)
        if snap:
            context = f"{symbol}: price={snap['price']} 24h_change={snap.get('change_pct_24h')}%"
        else:
            context = f"{symbol}: no snapshot yet"
        return None, f"market_context_fallback:{symbol}:{e}"

    # P1-7: deterministic pre-filter — skip LLM for symbols with no actionable signal.
    # MO-14: force_full is passed in (computed once per run). load_config() has no
    # cache, so calling it per-symbol was a file read + YAML parse every iteration.
    pre_filter = _evaluate_pre_filter(context)
    pre_filter_passed = _has_sufficient_signal(context)
    try:
        from aletheia.analytics.filter_evidence import record_observer_pre_filter_evidence

        pre_filter_decision = (
            "not_applicable" if force_full
            else "passed" if pre_filter_passed
            else "rejected"
        )
        pre_filter_reason = (
            "force_full_bypass" if force_full
            else "+".join(pre_filter["matched_rules"])
            if pre_filter["matched_rules"]
            else "sufficient_signal" if pre_filter_passed
            else "no_sufficient_signal"
        )
        record_observer_pre_filter_evidence(
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
                "keywords": list(_PREFILTER_KEYWORDS),
            },
            entry_price_usd=member_entry_price_usd,
            entry_spread_bps=member_spread_bps,
            funding_rate_at_entry=funding_rate_val,
        )
    except Exception as exc:
        logger.warning("observer pre-filter evidence unavailable for %s: %s", symbol, exc)

    if not force_full and not pre_filter_passed:
        logger.debug("pre-filter: no sufficient signal for %s — skipping LLM call", symbol)
        return None, None

    # MO-2 (断口F fix): per-symbol guard advisory. Guards match on symbol +
    # scanner_family, which only exist per-symbol — the run-level memory_block was
    # built from a symbol LIST with no family, so no guard ever matched. Build it
    # here where scanner_family is known.
    sym_memory = memory_block
    guard_block = ""
    if scanner_family and learning_memory_block is not None:
        try:
            from aletheia.knowledge.router import build_guard_advisory
            guard_block = build_guard_advisory({
                "symbol": symbol,
                "asset_class": asset_class,
                "scanner_family": scanner_family,
            })
            if guard_block:
                sym_memory = f"{memory_block}{guard_block}"
        except Exception:
            pass
    from aletheia.learning.prompt_bundles import build_observer_learning_manifest
    learning_manifest = build_observer_learning_manifest(
        learning_memory_block or "", guard_block,
    )

    # MO-4: tell the LLM which scanner family surfaced this symbol so it VALIDATES
    # that signal family rather than doing a generic technical read.
    scanner_hint = (
        f"\n\nThis symbol was surfaced by the '{scanner_family}' scanner — "
        f"focus your read on validating that signal family."
        if scanner_family else ""
    )

    record_step(
        run_id, f"market_read_input:{symbol}",
        input_data=f"symbol={symbol} ctx_len={len(context)} memory={len(sym_memory) > 0}",
        output_data=context[:1000],
    )

    read, err = await llm_structured(
        system=_SYSTEM_PROMPT,
        user=f"/no_think\nMarket data for {symbol}:\n{context}{sym_memory}{scanner_hint}\n\nReturn the market read JSON.",
        schema=MarketRead,
        max_tokens=2048,
        provider=get_provider_for_tier("deep"),
    )
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
        record_step(run_id, f"market_read_skipped:{symbol}",
                    input_data=None, output_data=llm_error)
        return None, f"{market_type}:{symbol}:llm_unavailable: {llm_error}"

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
        run_id, f"market_read_output:{symbol}",
        input_data=None,
        output_data=(
            f"direction={direction} confidence={confidence} "
            f"score={confidence_score} obs={len(observations)}"
        ),
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
    thesis = " ".join(thesis_parts) if thesis_parts else f"No structured rationale — {symbol} market read (direction={direction})"

    # If a deterministic opportunity already exists for this symbol+snapshot+market,
    # annotate it with LLM insights instead of creating a duplicate. Pass market
    # so a same-symbol opp from a market is not mistaken for this one's opp.
    # OD-S02 locks.
    if universe_snapshot_id:
        existing = find_open_opportunity_by_symbol_snapshot(
            symbol, universe_snapshot_id,
            market=instrument_market_to_db_market(market_type),
        )
        if existing:
            updates: dict = {"thesis": thesis}
            if learning_manifest:
                try:
                    existing_meta = json.loads(existing.get("metadata") or "{}")
                except (TypeError, ValueError):
                    existing_meta = {}
                existing_meta["learning_manifest"] = learning_manifest
                updates["metadata"] = json.dumps(existing_meta, sort_keys=True)
            if funding_rate_val is not None:
                updates["funding_rate"] = funding_rate_val
            if open_interest_val is not None:
                updates["open_interest"] = open_interest_val
            if oi_delta_val is not None:
                updates["oi_delta_1h"] = oi_delta_val
            if observations:
                updates["key_observations"] = json.dumps(observations)
            if confidence_score is not None:
                old_score = existing.get("confidence_score") or 0
                updates["confidence_score"] = max(old_score, confidence_score)
                updates["confidence"] = confidence
            if direction != "neutral":
                updates["direction_bias"] = direction
            update_opportunity(existing["id"], **updates)
            log_tool(run_id, "annotate_opportunity", "observe", True, output=existing["id"])
            return existing["id"], llm_error

    # IB-24-12: standalone neutral opportunities add no signal value and get
    # filtered immediately by the ranker's neutral_bias gate. Skip creation.
    if direction == "neutral":
        record_step(run_id, f"neutral_skipped:{symbol}", input_data=None,
                    output_data="direction=neutral standalone opp suppressed")
        return None, llm_error

    # P0-2: fill scanner_family from LLM text when not set by universe member,
    # so the sideways regime tax can fire on non-universe opportunities.
    effective_scanner_family = scanner_family or _infer_scanner_family(rationale, observations)

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
        metadata=json.dumps({"learning_manifest": learning_manifest}, sort_keys=True)
        if learning_manifest else "{}",
        universe_snapshot_id=universe_snapshot_id,
        universe_member_id=universe_member_id,
        scanner_family=effective_scanner_family,
        key_observations=json.dumps(observations) if observations else "",
        funding_rate=funding_rate_val,
        open_interest=open_interest_val,
        oi_delta_1h=oi_delta_val,
    )
    insert_opportunity(opp)
    try:
        from aletheia.agent_runtime.pi_daemon import notify_pi_daemon
        notify_pi_daemon(source="market_observer")
    except Exception:
        pass
    log_tool(run_id, "create_opportunity", "observe", True, output=opp.id)
    return opp.id, llm_error


@register_agent("market_observer")
async def run_market_observer(permission_mode: str = "Observe", run_id: str | None = None, **kwargs) -> dict:
    """Observe market state and create one Opportunity per symbol."""
    outputs: list[str] = []
    errors: list[str] = []

    if not is_tool_permitted("get_market_snapshot", permission_mode):
        errors.append("get_market_snapshot not permitted in current mode")
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
            instruments.append(InstrumentId.from_parts(
                cfg.market_data.ccxt_exchange, sym, mt
            ))
    else:
        from aletheia.data.symbols import to_canonical
        instruments = observed_instruments_from_config(cfg)
        universe_members = []

    from aletheia.agents.round_robin import DurableRoundRobin, rotate_from_next

    instrument_key = str
    round_robin = DurableRoundRobin(_ROUND_ROBIN_STATE_KEY)
    instruments = rotate_from_next(
        instruments, next_key=round_robin.next_key(), key=instrument_key,
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
        memory_block = build_advisory_context(
            role="market_observer",
            query=f"{' '.join(symbols)} market regime risk funding volatility",
            k=3,
        )
        log_tool(run_id, "retrieve_lessons", "observe", True,
                 output=f"lessons:{1 if memory_block else 0}")
    except Exception:
        pass

    # Trading Memo — historical performance insights (soft guidance for direction calibration)
    try:
        from aletheia.analytics.insight_synthesizer import get_latest_memo, render_memo_for_observer
        memo = get_latest_memo()
        if memo:
            memory_block += render_memo_for_observer(memo)
    except Exception:
        pass

    try:
        from aletheia.learning.control import learning_influence_enabled
        learning_memory_block = memory_block if learning_influence_enabled() else None
    except Exception:
        learning_memory_block = None
    if learning_memory_block is None:
        memory_block = ""

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
                memory_block += "\n".join(pred_lines)
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
    # WALL-CLOCK BUDGET (gateway-stability fix): each instrument is one LLM call that can
    # take up to the provider timeout (60s) under LLM-provider contention. With a large
    # universe this can blow the 240s job timeout → _run_async_with_timeout cancels the
    # coroutine mid-flight → SelectorEventLoop teardown stalls → the daemon thread is
    # abandoned and LEAKS (the "abandoned threads 3→5" cascade that starves the frontdoor).
    # Capping the loop wall-clock < the job timeout makes the coroutine ALWAYS return
    # normally before cancellation → clean teardown, no leak. Skipped instruments are
    # picked up next 15-min cycle (logged, not silent).
    import time as _time
    # MO-14: compute force_full ONCE per run (load_config has no cache; per-symbol
    # calls were a file read + YAML parse each iteration).
    _force_full = getattr(getattr(cfg, "observer", None), "force_full_scan", False)
    _loop_deadline = _time.monotonic() + _MARKET_OBSERVER_LOOP_BUDGET_SEC
    for _idx, instrument in enumerate(instruments):
        if _time.monotonic() > _loop_deadline:
            _skipped = len(instruments) - _idx
            errors.append(
                f"loop_budget_exceeded: processed {_idx}/{len(instruments)}, skipped "
                f"{_skipped} (wall-clock cap {_MARKET_OBSERVER_LOOP_BUDGET_SEC:.0f}s "
                f"< {_MARKET_OBSERVER_JOB_TIMEOUT_S:.0f}s job timeout — avoids thread leak)"
            )
            logger.warning(
                "market_observer loop budget hit after %d/%d instruments (%.0fs cap); "
                "skipping %d this cycle to finish within the job budget",
                _idx, len(instruments), _MARKET_OBSERVER_LOOP_BUDGET_SEC, _skipped,
            )
            break
        completed_attempt = False
        try:
            u_member = _universe_lookup.get(instrument.symbol)
            _sym_canonical = to_canonical(instrument.symbol)
            _linked_sid = _strategy_by_symbol.get(_sym_canonical) or _strategy_by_symbol.get("__generic__")
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
                venue=cfg.market_data.ccxt_exchange,
                force_full=_force_full,
                prediction_context_enabled=prediction_context_enabled,
                prediction_context_cutoff=prediction_context_cutoff,
                learning_memory_block=learning_memory_block,
            )
            if opp_id:
                outputs.append(f"opportunity:{opp_id}")
            if err:
                errors.append(f"{instrument}:{err}")
            completed_attempt = True
        except Exception as e:
            errors.append(f"{instrument}:unexpected:{e}")
            completed_attempt = True
        finally:
            # Cancellation must not skip an in-flight item. A budget interruption
            # breaks before entering this block, preserving the first skipped item.
            if completed_attempt:
                round_robin.advance_after(
                    instruments, completed_key=instrument_key(instrument), key=instrument_key,
                )

    return {"outputs": outputs, "errors": errors}
