"""Context builder — merges all 9 data domains into a MarketContextSnapshot.

The snapshot is the single object passed to market_observer so the LLM
receives microstructure + derivatives + macro in one structured block.
Also renders an enriched markdown string for the LLM prompt.

Two usage paths:
1. Direct (unit-testable): pass model objects to build_market_context_snapshot()
2. From DB (runtime): call build_market_context_from_db(symbols) — reads latest
   collector data from SQLite and computes scores automatically.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from aletheia.data.models import (
    LeverageState,
    MacroSnapshot,
    MarketContextSnapshot,
    TradabilityScore,
)

logger = logging.getLogger("aletheia.data.context_builder")

_STALENESS_THRESHOLD = timedelta(minutes=15)


def build_market_context_snapshot(
    symbols: list[str],
    tradability: dict[str, TradabilityScore],
    leverage: dict[str, LeverageState],
    macro: MacroSnapshot | None,
) -> MarketContextSnapshot:
    """Build a MarketContextSnapshot from component parts."""
    now = datetime.now(timezone.utc).isoformat()

    expected_fields = len(symbols) * 2 + 1  # tradability + leverage per sym + macro
    filled = sum(1 for s in symbols if s in tradability) + \
             sum(1 for s in symbols if s in leverage) + \
             (1 if macro else 0)
    completeness = round(filled / max(expected_fields, 1), 2)

    stale = []
    if not tradability:
        stale.append("microstructure")
    if not leverage:
        stale.append("derivatives")
    if macro is None:
        stale.append("macro")

    return MarketContextSnapshot(
        id=uuid4().hex[:12],
        symbols=symbols,
        tradability={s: tradability[s].model_dump() for s in symbols if s in tradability},
        leverage={s: leverage[s].model_dump() for s in symbols if s in leverage},
        macro=macro.model_dump() if macro else {},
        data_completeness=completeness,
        stale_domains=stale,
        observed_at=now,
    )


def render_enriched_context_md(
    symbols: list[str],
    tradability: dict[str, TradabilityScore],
    leverage: dict[str, LeverageState],
    macro: MacroSnapshot | None,
    onchain: list[dict] | None = None,
    xvenue_funding: dict[str, dict] | None = None,
) -> str:
    """Render a markdown block with microstructure + derivatives + macro sections.

    onchain: latest onchain_snapshots rows (DefiLlama stablecoin/TVL/DEX). Rendered
    as its own market-regime section — fills the on-chain hole left by the
    never-built Glassnode. Omitted when empty.

    xvenue_funding: {symbol: {hl_ann, hl_predicted_ann, primary_ann}} — Hyperliquid
    cross-venue funding (ANNUALIZED, since HL uses 1h vs the primary venue's 8h
    interval — raw per-interval rates are not comparable). Routes the otherwise
    orphan Hyperliquid feed into the decision context as a basis/sentiment-
    divergence signal. Omitted when empty.

    Returns "" if all domains are empty so callers can skip appending.
    """
    sections: list[str] = []

    # --- Microstructure ---
    if tradability:
        rows = ["| Symbol | Spread bps | Depth $10bps | Score | Tradable |",
                "|--------|-----------|-------------|-------|---------|"]
        for sym in symbols:
            t = tradability.get(sym)
            if t is None:
                continue
            rows.append(
                f"| {sym} | {t.spread_bps:.1f} | ${t.depth_10bps_usd:,.0f} "
                f"| {t.score:.0f} | {'✓' if t.is_tradable else '✗'} |"
            )
        sections.append("### Microstructure (execution cost)\n" + "\n".join(rows))

    # --- Derivatives ---
    if leverage:
        rows = ["| Symbol | Funding/8h | Funding Ann. | OI USD | OI Δ1h% | LSR | Crowding |",
                "|--------|-----------|-------------|--------|---------|-----|---------|"]
        for sym in symbols:
            lv = leverage.get(sym)
            if lv is None:
                continue
            fr = f"{lv.funding_rate * 100:.4f}%" if lv.funding_rate is not None else "n/a"
            fr_ann = f"{lv.funding_annualized * 100:.1f}%" if lv.funding_annualized is not None else "n/a"
            oi = f"${lv.open_interest_usd:,.0f}" if lv.open_interest_usd else "n/a"
            oi_d = f"{lv.oi_delta_1h_pct:+.1f}%" if lv.oi_delta_1h_pct is not None else "n/a"
            lsr = f"{lv.long_short_ratio:.2f}" if lv.long_short_ratio is not None else "n/a"
            rows.append(
                f"| {sym} | {fr} | {fr_ann} | {oi} | {oi_d} | {lsr} | {lv.crowding_score:.0f}/100 |"
            )
        sections.append("### Derivatives state\n" + "\n".join(rows))

        # Flag funding opportunities
        opps = [sym for sym, lv in leverage.items() if lv.funding_opportunity]
        if opps:
            sections.append(f"**Funding opportunity signals**: {', '.join(opps)}")

    # --- Cross-venue funding (Hyperliquid) — basis / sentiment divergence ---
    # Compares ANNUALIZED funding: HL uses 1h intervals, the primary venue 8h,
    # so only the annualized rates are apples-to-apples. A large HL-vs-primary
    # gap flags venue-specific crowding (one side paying up to hold the trade).
    if xvenue_funding:
        xrows: list[str] = []
        for sym in symbols:
            xv = xvenue_funding.get(sym)
            if not xv or xv.get("hl_ann") is None:
                continue
            hl = xv["hl_ann"]
            line = f"{sym}: Hyperliquid {hl * 100:+.1f}%/yr"
            prim = xv.get("primary_ann")
            if prim is not None:
                line += f" vs primary {prim * 100:+.1f}%/yr → Δ{(hl - prim) * 100:+.1f}pp"
            if xv.get("hl_predicted_ann") is not None:
                line += f"; HL predicted {xv['hl_predicted_ann'] * 100:+.1f}%/yr"
            xrows.append(line)
        if xrows:
            sections.append("### Cross-venue funding (Hyperliquid, annualized)\n" + "  \n".join(xrows))

    # --- Macro ---
    if macro:
        lines = [f"**Risk regime**: {macro.risk_regime}"]
        if macro.btc_dominance is not None:
            lines.append(f"BTC.D: {macro.btc_dominance:.1f}%")
        if macro.total_market_cap_usd is not None:
            cap = macro.total_market_cap_usd / 1e9
            chg = f" ({macro.total_market_cap_change_24h_pct:+.1f}% 24h)" \
                  if macro.total_market_cap_change_24h_pct is not None else ""
            lines.append(f"Total cap: ${cap:,.0f}B{chg}")
        if macro.vix_close is not None:
            lines.append(f"VIX: {macro.vix_close:.1f}")
        if macro.nasdaq_close is not None:
            lines.append(f"Nasdaq: {macro.nasdaq_close:,.0f}")
        if macro.dxy_close is not None:
            lines.append(f"DXY: {macro.dxy_close:.2f}")
        # Round 2 — Step 2 (F&G) and Step 3 (CoinGecko top movers).
        if macro.fng_value is not None:
            cls = f" ({macro.fng_classification})" if macro.fng_classification else ""
            lines.append(f"Fear & Greed: {macro.fng_value}{cls}")
        if macro.top_movers_summary:
            lines.append(f"24h movers: {macro.top_movers_summary}")
        sections.append("### Macro context\n" + "  \n".join(lines))

    # --- On-chain (DefiLlama; zero-key, fills the Glassnode hole) ---
    if onchain:
        by_metric: dict[str, dict] = {}
        for r in onchain:  # rows arrive newest-first; keep the newest per type
            mt = r.get("metric_type")
            if mt:
                by_metric.setdefault(mt, r)
        ol: list[str] = []
        sc = by_metric.get("stablecoin_mcap")
        if sc and sc.get("total_usd") is not None:
            chg = f" ({sc['change_24h_pct']:+.2f}% 24h)" if sc.get("change_24h_pct") is not None else ""
            ol.append(f"Stablecoin cap: ${sc['total_usd'] / 1e9:,.1f}B{chg}")
        tvl = by_metric.get("chain_tvl")
        if tvl and tvl.get("total_usd") is not None:
            ol.append(f"DeFi TVL: ${tvl['total_usd'] / 1e9:,.1f}B")
        dex = by_metric.get("dex_volume")
        if dex and dex.get("total_usd") is not None:
            chg = f" ({dex['change_24h_pct']:+.1f}% 1d)" if dex.get("change_24h_pct") is not None else ""
            ol.append(f"DEX vol 24h: ${dex['total_usd'] / 1e9:,.2f}B{chg}")
        if ol:
            sections.append("### On-chain (DefiLlama)\n" + "  \n".join(ol))

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# DB-based convenience (runtime path)
# ---------------------------------------------------------------------------

def build_market_context_from_db(symbols: list[str]) -> MarketContextSnapshot:
    """Read latest collector data from SQLite and produce a full context snapshot.

    This is the runtime entry point called by the scheduler job after collectors
    have persisted their data.
    """
    from aletheia.data.normalizer import compute_leverage_state, compute_tradability
    from aletheia.storage import repositories as repo

    now = datetime.now(timezone.utc)
    tradability: dict[str, TradabilityScore] = {}
    leverage_states: dict[str, LeverageState] = {}

    for sym in symbols:
        # Orderbook → TradabilityScore
        ob = repo.latest_orderbook(sym)
        if ob and _is_fresh(ob.get("observed_at"), now):
            vol = _get_volume(sym)
            tradability[sym] = compute_tradability(
                symbol=sym,
                exchange=ob.get("exchange", ""),
                spread_bps=ob.get("spread_bps", 0),
                depth_10bps_usd=ob.get("total_depth_10bps_usd", 0),
                volume_24h_usd=vol,
            )

        # Derivatives → LeverageState
        fr = repo.latest_funding_rate(sym)
        oi = repo.latest_open_interest(sym)
        liqs = repo.recent_liquidations(sym, hours=1)
        liq_total = sum(l.get("quantity_usd", 0) for l in liqs)

        leverage_states[sym] = compute_leverage_state(
            symbol=sym,
            funding_rate=fr.get("funding_rate") if fr and _is_fresh(fr.get("observed_at"), now) else None,
            funding_annualized=fr.get("funding_rate_annualized") if fr else None,
            open_interest_usd=oi.get("open_interest_usd") if oi and _is_fresh(oi.get("observed_at"), now) else None,
            oi_delta_1h_pct=oi.get("oi_change_1h_pct") if oi else None,
            liquidation_1h_usd=liq_total if liq_total > 0 else None,
        )

    # Macro
    macro_row = repo.latest_macro_snapshot()
    macro: MacroSnapshot | None = None
    if macro_row and _is_fresh(macro_row.get("observed_at"), now, threshold=timedelta(minutes=30)):
        macro = MacroSnapshot(
            btc_dominance=macro_row.get("btc_dominance"),
            total_market_cap_usd=macro_row.get("total_market_cap_usd"),
            total_market_cap_change_24h_pct=macro_row.get("total_market_cap_change_24h_pct"),
            spy_close=macro_row.get("spy_close"),
            vix_close=macro_row.get("vix_close"),
            dxy_close=macro_row.get("dxy_close"),
            nasdaq_close=macro_row.get("nasdaq_close"),
            risk_regime=macro_row.get("risk_regime", "neutral"),
            observed_at=macro_row.get("observed_at", ""),
        )

    return build_market_context_snapshot(symbols, tradability, leverage_states, macro)


def _is_fresh(
    observed_at: str | None,
    now: datetime,
    threshold: timedelta = _STALENESS_THRESHOLD,
) -> bool:
    if not observed_at:
        return False
    try:
        ts = datetime.fromisoformat(observed_at)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (now - ts) < threshold
    except (ValueError, TypeError):
        return False


def _get_volume(symbol: str) -> float:
    from aletheia.storage import repositories as repo
    snaps = repo.list_market_snapshots(symbol=symbol, limit=1)
    if snaps:
        return float(snaps[0].get("volume_24h") or 0)
    return 0.0
