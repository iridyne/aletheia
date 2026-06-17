"""Market Observer agent — observes market conditions and creates opportunities.

Per-symbol loop: each symbol gets its own LLM MarketRead call and its own
Opportunity row. This fixes the original "only BTC" bug where all symbol data
was sent to the LLM together but only symbols[0] was used for the resulting
opportunity.
"""

from typing import Literal

from pydantic import BaseModel, Field

from aletheia.agents import register_agent
from aletheia.agents.helpers import llm_structured, log_tool, record_step
from aletheia.agent_runtime.permissions import is_tool_permitted
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

_VALID_DIRECTIONS = {"long", "short", "neutral"}
_VALID_CONFIDENCE = {"high", "medium", "speculative"}


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
) -> tuple[str | None, str | None]:
    """Run market context build + LLM read for a single symbol.

    Returns (opportunity_id, error_str). Both may be None if the symbol is
    simply skipped (no permission, neutral signal, etc.).
    """
    from aletheia.trading.market_context import build_market_context

    try:
        context, ctx_debug = await build_market_context(
            [symbol], market_type=market_type, provider=provider
        )
        log_tool(run_id, "build_market_context", "observe", True,
                 output=f"{symbol} fallback={ctx_debug.get('fallback')} "
                        f"errs={len(ctx_debug.get('errors', []))}")

        # Append enriched microstructure/derivatives/macro context if available.
        try:
            from aletheia.data.context_builder import render_enriched_context_md
            from aletheia.storage import repositories as repo
            from aletheia.data.normalizer import compute_tradability, compute_leverage_state
            from aletheia.data.models import TradabilityScore, LeverageState, MacroSnapshot

            ob = repo.latest_orderbook(symbol)
            fr = repo.latest_funding_rate(symbol)
            oi = repo.latest_open_interest(symbol)
            macro_row = repo.latest_macro_snapshot()

            tradability: dict = {}
            if ob:
                t = compute_tradability(
                    symbol=symbol, exchange=ob.get("exchange", ""),
                    spread_bps=ob["spread_bps"],
                    depth_10bps_usd=ob.get("total_depth_10bps_usd") or 0,
                    volume_24h_usd=0,  # supplemented from ticker if available
                )
                tradability[symbol] = t

            leverage: dict = {}
            if fr or oi:
                lv = compute_leverage_state(
                    symbol=symbol,
                    funding_rate=fr["funding_rate"] if fr else None,
                    funding_annualized=fr["funding_rate_annualized"] if fr else None,
                    open_interest_usd=oi["open_interest_usd"] if oi else None,
                    oi_delta_1h_pct=oi["oi_change_1h_pct"] if oi else None,
                )
                leverage[symbol] = lv

            macro = None
            if macro_row:
                macro = MacroSnapshot(**macro_row)

            enriched = render_enriched_context_md([symbol], tradability, leverage, macro)
            if enriched:
                context = context + "\n\n" + enriched
        except Exception:
            pass  # enriched context is optional; never break the main path

        # Append prediction market signals relevant to this symbol
        try:
            from aletheia.storage.repositories import _select_all
            base = symbol.split("/")[0].lower()
            keyword_variants = [base]
            _COIN_NAMES = {"btc": "bitcoin", "eth": "ethereum", "sol": "solana",
                           "xrp": "ripple", "doge": "dogecoin", "ada": "cardano",
                           "avax": "avalanche", "link": "chainlink"}
            if base in _COIN_NAMES:
                keyword_variants.append(_COIN_NAMES[base])
            where_clauses = " OR ".join(["LOWER(question) LIKE ?" for _ in keyword_variants])
            params = tuple(f"%{kw}%" for kw in keyword_variants)
            pred_rows = _select_all(
                "prediction_markets",
                where=f"({where_clauses}) AND status = 'active'",
                params=params,
                order="abs(probability_change_24h) DESC",
                limit=3,
            )
            if pred_rows:
                lines = []
                for r in pred_rows:
                    q = (r.get("question") or "")[:80]
                    yes = (r.get("yes_probability") or 0) * 100
                    delta = (r.get("probability_change_24h") or 0) * 100
                    vol = r.get("volume_24h") or 0
                    lines.append(f"- {q}: YES={yes:.0f}% (Δ24h {delta:+.1f}pp, vol=${vol:,.0f})")
                context += "\n\n## Prediction Market Signals\n" + "\n".join(lines)
        except Exception:
            pass

        # Append recent news headlines for this symbol
        try:
            from aletheia.storage.sqlite import get_db
            news_db = get_db()
            news_rows = news_db.execute(
                "SELECT title, source, created_at FROM news_items "
                "WHERE symbols_mentioned LIKE ? ORDER BY created_at DESC LIMIT 3",
                (f"%{symbol.split('/')[0]}%",),
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

    record_step(
        run_id, f"market_read_input:{symbol}",
        input_data=f"symbol={symbol} ctx_len={len(context)} memory={len(memory_block) > 0}",
        output_data=context[:1000],
    )

    read, err = await llm_structured(
        system=_SYSTEM_PROMPT,
        user=f"/no_think\nMarket data for {symbol}:\n{context}{memory_block}\n\nReturn the market read JSON.",
        schema=MarketRead,
        max_tokens=2048,
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

    # If a deterministic opportunity already exists for this symbol+snapshot,
    # annotate it with LLM insights instead of creating a duplicate.
    if universe_snapshot_id:
        existing = find_open_opportunity_by_symbol_snapshot(symbol, universe_snapshot_id)
        if existing:
            updates: dict = {"thesis": thesis}
            if confidence_score is not None:
                old_score = existing.get("confidence_score") or 0
                updates["confidence_score"] = max(old_score, confidence_score)
                updates["confidence"] = confidence
            if direction != "neutral":
                updates["direction_bias"] = direction
            update_opportunity(existing["id"], **updates)
            log_tool(run_id, "annotate_opportunity", "observe", True, output=existing["id"])
            return existing["id"], llm_error

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
        universe_snapshot_id=universe_snapshot_id,
        universe_member_id=universe_member_id,
        scanner_family=scanner_family,
    )
    insert_opportunity(opp)
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
        from aletheia.data.symbols import InstrumentId, MARKET_SPOT
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
        instruments = observed_instruments_from_config(cfg)
        universe_members = []

    symbols = [i.symbol for i in instruments]  # canonical list for memory query

    # Find an active strategy to link all opportunities from this run.
    linked_strategy_id = None
    for s in list_strategies(limit=50):
        if is_active_paper_strategy(s):
            linked_strategy_id = s["id"]
            break

    # Experience-loop advisory context (lessons + case studies + guard advisories)
    memory_block = ""
    try:
        from aletheia.knowledge.router import build_advisory_context
        memory_block = build_advisory_context(
            role="market_observer",
            query=f"{' '.join(symbols)} market regime risk funding volatility",
            k=3,
        )
        log_tool(run_id, "retrieve_lessons", "observe", True,
                 output=f"lessons:{1 if memory_block else 0}")
    except Exception:
        pass

    # Prediction market catalyst context (observation-only enrichment)
    try:
        from aletheia.storage.repositories import list_prediction_claims
        pred_claims = list_prediction_claims(claim_type="probability_momentum", limit=5)
        if pred_claims:
            pred_lines = ["\n\n## Prediction Market Context (observation-only)"]
            for pc in pred_claims[:5]:
                direction = pc.get("direction", "")
                pred_lines.append(f"- {pc['claim_text']} [{direction}]")
            memory_block += "\n".join(pred_lines)
    except Exception:
        pass

    # Build a lookup for universe member metadata (symbol → member dict)
    _universe_lookup = {m["symbol"]: m for m in universe_members} if universe_members else {}

    # Per-instrument loop: each instrument gets its own LLM call + Opportunity row.
    # instrument.market_type is the authoritative type; no more config-derived guessing.
    for instrument in instruments:
        try:
            u_member = _universe_lookup.get(instrument.symbol)
            opp_id, err = await _analyze_symbol(
                symbol=instrument.symbol,
                market_type=instrument.market_type,
                permission_mode=permission_mode,
                run_id=run_id,
                linked_strategy_id=linked_strategy_id,
                memory_block=memory_block,
                asset_class="crypto",
                universe_snapshot_id=universe_snapshot_id,
                universe_member_id=u_member.get("id") if u_member else None,
                scanner_family=u_member.get("scanner_family") if u_member else None,
            )
            if opp_id:
                outputs.append(f"opportunity:{opp_id}")
            if err:
                errors.append(f"{instrument}:{err}")
        except Exception as e:
            errors.append(f"{instrument}:unexpected:{e}")

    return {"outputs": outputs, "errors": errors}
