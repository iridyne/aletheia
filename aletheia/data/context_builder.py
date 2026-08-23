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
from datetime import datetime, timezone
from uuid import uuid4

from aletheia.data.models import (
    LeverageState,
    MacroSnapshot,
    MarketContextSnapshot,
    TradabilityScore,
)
from aletheia.data.position_x import build_position_x_agent_context
from aletheia.data.freshness import fresh_row

logger = logging.getLogger("aletheia.data.context_builder")


def _physical_market_type(market_type: str | None) -> str:
    """Map proposal/exchange vocabulary onto collector storage identity."""
    normalized = str(market_type or "").strip().lower()
    if normalized in {"usdt_futures", "swap"}:
        return "perp"
    if normalized in {"spot", "perp", "futures"}:
        return normalized
    return ""

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
    perp_structure: dict[str, dict] | None = None,
    options_structure: dict[str, dict] | None = None,
) -> str:
    """Render a markdown block with microstructure + derivatives + macro sections.

    onchain: latest onchain_snapshots rows (DefiLlama stablecoin/TVL/DEX). Rendered
    as its own market-regime section — fills the on-chain hole left by the
    never-built Glassnode. Omitted when empty.

    xvenue_funding: Hyperliquid cross-venue funding plus OI evidence. Funding is
    ANNUALIZED, since HL uses 1h vs the primary venue's 8h
    interval — raw per-interval rates are not comparable). Routes the otherwise
    orphan Hyperliquid feed into the decision context as a basis/sentiment-
    divergence signal. Omitted when empty.

    perp_structure: {symbol: latest row} — observation-only perp positioning
    structure (value area, POC, liquidation walls, concentration). Omitted when
    empty so the prompt never invents this signal.

    options_structure: {currency: latest row} — derived GEX/gamma-structure factors
    from Deribit public options chain (BTC/ETH only). Supplementary context only;
    do not promote to hard filters without Shadow attribution. Omitted when empty.

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
            # MO-8: crowding_score is 0.0 when NO crowding input exists (funding/OI/
            # LSR all None). Rendering "0/100" reads to the LLM as "not crowded";
            # show n/a instead so absence-of-data isn't mistaken for a real signal.
            has_crowd_input = (lv.funding_rate is not None
                               or lv.oi_delta_1h_pct is not None
                               or lv.long_short_ratio is not None)
            crowd = f"{lv.crowding_score:.0f}/100" if has_crowd_input else "n/a"
            rows.append(
                f"| {sym} | {fr} | {fr_ann} | {oi} | {oi_d} | {lsr} | {crowd} |"
            )
        # MO-9: glossary so the LLM reads LSR/Crowding consistently.
        sections.append(
            "### Derivatives state\n"
            "_LSR = long/short account ratio (>1 crowd-long, <1 crowd-short, "
            "n/a=no data); Crowding 0-100, higher = more one-sided._\n"
            + "\n".join(rows)
        )

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
            if not xv or (
                xv.get("hl_ann") is None and xv.get("hl_open_interest_usd") is None
            ):
                continue
            line = f"{sym}: Hyperliquid"
            hl = xv.get("hl_ann")
            if hl is not None:
                line += f" funding {hl * 100:+.1f}%/yr"
                prim = xv.get("primary_ann")
                if prim is not None:
                    line += f" vs primary {prim * 100:+.1f}%/yr → Δ{(hl - prim) * 100:+.1f}pp"
                if xv.get("hl_predicted_ann") is not None:
                    line += f"; predicted {xv['hl_predicted_ann'] * 100:+.1f}%/yr"
            hl_oi = xv.get("hl_open_interest_usd")
            if hl_oi is not None:
                line += f"; OI {_fmt_usd_compact_precise(hl_oi)}"
                oi_delta = xv.get("hl_oi_change_1h_pct")
                if oi_delta is not None:
                    line += f"; OI Delta1h {float(oi_delta):+.1f}%"
            if xv.get("hl_observed_at"):
                line += f"; observed {xv['hl_observed_at']}"
            xrows.append(line)
        if xrows:
            sections.append("### Cross-venue funding and OI (Hyperliquid)\n" + "  \n".join(xrows))

    # --- Perp structure (observation-only positioning map) ---
    if perp_structure:
        ps_rows: list[str] = []
        for sym in symbols:
            row = perp_structure.get(sym)
            if not row:
                continue
            parts: list[str] = []
            if row.get("data_quality"):
                parts.append(str(row["data_quality"]))
            if row.get("as_of_time"):
                parts.append(f"as-of {row['as_of_time']}")
            if row.get("poc") is not None:
                parts.append(f"POC {_fmt_usd(row['poc'])}")
            if row.get("value_area_low") is not None and row.get("value_area_high") is not None:
                parts.append(
                    f"Value Area {_fmt_usd(row['value_area_low'])}-{_fmt_usd(row['value_area_high'])}"
                )
            if row.get("short_liq_above_usd") is not None or row.get("long_liq_below_usd") is not None:
                short_liq = _fmt_usd_compact(row.get("short_liq_above_usd"))
                long_liq = _fmt_usd_compact(row.get("long_liq_below_usd"))
                parts.append(f"short-liq above {short_liq} vs long-liq below {long_liq}")
            conc: list[str] = []
            if row.get("top10_concentration") is not None:
                conc.append(f"top10 {row['top10_concentration'] * 100:.0f}%")
            if row.get("top50_concentration") is not None:
                conc.append(f"top50 {row['top50_concentration'] * 100:.0f}%")
            if conc:
                parts.append("concentration " + ", ".join(conc))
            px = build_position_x_agent_context(row)
            if px["status"] == "ok":
                parts.append(px["rendered"])
            if parts:
                src = row.get("source") or "unknown"
                venue = row.get("venue") or "unknown"
                ps_rows.append(f"{sym} ({src}/{venue}): " + "; ".join(parts))
        if ps_rows:
            sections.append("### Perp structure (positioning map)\n" + "  \n".join(ps_rows))

    # --- Options market structure (Deribit public chain; BTC/ETH only) ---
    if options_structure:
        opt_rows: list[str] = []
        for currency in ("BTC", "ETH"):
            row = options_structure.get(currency)
            if not row:
                continue
            parts: list[str] = []
            regime = row.get("gamma_regime")
            net_gex = row.get("net_gex_estimate")
            if regime:
                parts.append(f"gamma regime: {regime}")
            if net_gex is not None:
                parts.append(f"net GEX {_fmt_usd_compact(net_gex)}/1%move")
            flip = row.get("gamma_flip_estimate")
            if flip is not None:
                parts.append(f"gamma flip ~{_fmt_usd(flip)}")
            call_wall = row.get("call_gamma_wall")
            put_wall = row.get("put_gamma_wall")
            if call_wall:
                parts.append(f"call wall {_fmt_usd(call_wall)}")
            if put_wall:
                parts.append(f"put wall {_fmt_usd(put_wall)}")
            dist = row.get("nearest_gamma_wall_distance")
            if dist is not None:
                parts.append(f"nearest wall {dist:.1f}% from spot")
            obs = (row.get("observed_at") or "")[:16]
            if obs:
                parts.append(f"as-of {obs}")
            if parts:
                opt_rows.append(f"{currency}: " + "; ".join(parts))
        if opt_rows:
            sections.append(
                "### Options market structure (Deribit public; GEX convention v1)\n"
                + "  \n".join(opt_rows)
            )

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


def _fmt_usd(value: object) -> str:
    try:
        return f"${float(value):,.0f}"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_usd_compact(value: object) -> str:
    if value is None:
        return "n/a"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "n/a"
    abs_num = abs(num)
    if abs_num >= 1_000_000_000:
        return f"${num / 1_000_000_000:.1f}B".replace(".0B", "B")
    if abs_num >= 1_000_000:
        return f"${num / 1_000_000:.0f}M"
    return f"${num:,.0f}"


def _fmt_usd_compact_precise(value: object) -> str:
    """Compact USD formatter retaining useful precision for aggregate OI."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if abs(num) >= 1_000_000_000:
        return f"${num / 1_000_000_000:.2f}B"
    if abs(num) >= 1_000_000:
        return f"${num / 1_000_000:.1f}M"
    return f"${num:,.0f}"


# ---------------------------------------------------------------------------
# DB-based convenience (runtime path)
# ---------------------------------------------------------------------------

def build_market_context_from_db(
    symbols: list[str], *, exchange: str = "", market_type: str = "",
    now: datetime | None = None,
) -> MarketContextSnapshot:
    """Read latest collector data from SQLite and produce a full context snapshot.

    This is the runtime entry point called by the scheduler job after collectors
    have persisted their data.
    """
    from aletheia.data.normalizer import compute_leverage_state, compute_tradability
    from aletheia.storage import repositories as repo

    now = now or datetime.now(timezone.utc)
    exact_exchange = str(exchange or "").strip().lower()
    physical_market_type = _physical_market_type(market_type)
    tradability: dict[str, TradabilityScore] = {}
    leverage_states: dict[str, LeverageState] = {}

    for sym in symbols:
        # Orderbook → TradabilityScore
        ob = repo.latest_orderbook(
            sym,
            exchange=exact_exchange,
            market_type=physical_market_type,
        )
        ob = fresh_row(ob, "orderbook", now=now)
        if ob:
            vol = _get_volume(
                sym,
                exchange=exact_exchange,
                market_type=physical_market_type,
            )
            tradability[sym] = compute_tradability(
                symbol=sym,
                exchange=ob.get("exchange", ""),
                spread_bps=ob.get("spread_bps", 0),
                depth_10bps_usd=ob.get("total_depth_10bps_usd", 0),
                volume_24h_usd=vol,
            )

        # Derivatives → LeverageState. Only an explicit derivatives identity may
        # consume perp funding, OI, liquidation, or positioning evidence.
        normalized_market_type = str(market_type or "").lower()
        if normalized_market_type in {"perp", "swap", "futures", "usdt_futures"}:
            deriv_exchange = exact_exchange or None
            fr = fresh_row(
                repo.latest_funding_rate(sym, exchange=deriv_exchange),
                "funding",
                now=now,
            )
            oi = fresh_row(
                repo.latest_open_interest(sym, exchange=deriv_exchange),
                "open_interest",
                now=now,
            )
            liqs = repo.recent_liquidations(sym, hours=1)
            liq_total = sum(l.get("quantity_usd", 0) for l in liqs)

            if fr or oi or liq_total > 0:
                leverage_states[sym] = compute_leverage_state(
                    symbol=sym,
                    funding_rate=fr.get("funding_rate") if fr else None,
                    funding_annualized=fr.get("funding_rate_annualized") if fr else None,
                    open_interest_usd=oi.get("open_interest_usd") if oi else None,
                    oi_delta_1h_pct=oi.get("oi_change_1h_pct") if oi else None,
                    long_short_ratio=oi.get("long_short_ratio") if oi else None,
                    liquidation_1h_usd=liq_total if liq_total > 0 else None,
                )

    # Macro
    macro_row = repo.latest_macro_snapshot()
    macro: MacroSnapshot | None = None
    macro_row = fresh_row(macro_row, "macro", now=now)
    if macro_row:
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
            # MO-3: F&G + top movers are collected (macro/coingecko collectors) and
            # rendered (build_market_context_snapshot §Macro), but were never read
            # here — so the runtime path always saw None. macro_snapshots stores all
            # three columns; surface them.
            fng_value=macro_row.get("fng_value"),
            fng_classification=macro_row.get("fng_classification"),
            top_movers_summary=macro_row.get("top_movers_summary"),
        )

    return build_market_context_snapshot(symbols, tradability, leverage_states, macro)


def _get_volume(
    symbol: str,
    *,
    exchange: str = "",
    market_type: str = "",
) -> float:
    from aletheia.storage import repositories as repo
    snaps = repo.list_market_snapshots(
        symbol=symbol,
        exchange=exchange,
        market_type=market_type or None,
        limit=1,
    )
    if snaps:
        return float(snaps[0].get("volume_24h") or 0)
    return 0.0
