"""Shared entry-time enrichment for execution brokers.

The four brokers (paper / okx / binance / bitget) are structural clones. On OPEN
each must stamp the new Position row with the same entry-time context so the
downstream learning/analytics layer treats every venue identically:

- ``btc_entry_price`` — BTC benchmark at entry (Edge Lab alpha/beta separation;
  family_scorecard's alpha column is gated on this being non-null).
- ``regime_tag``      — macro regime at entry (per-regime bucketing key in
  reasoning_feedback / insight_synthesizer / post_trade_reviewer).
- ``entry_market_summary`` — market-context JSON snapshot for post-trade review.
- ``decision_reasoning``    — the originating proposal's agent rationale
  (post-trade reviewer context + UI explainability).
- ``entry_fee`` / ``slippage_cost`` — cost visibility at open time.

Keeping this in ONE place means a live-broker clone can no longer silently drop
these fields the way it dropped stop_price before ca782c7. paper_broker was the
reference implementation; this is that logic factored out verbatim so all four
brokers stamp identical metadata.
"""

from __future__ import annotations


def btc_price_now() -> float | None:
    """Fetch current BTC/USDT price for benchmark tracking. Best-effort."""
    try:
        from aletheia.trading.market_data import latest_snapshot
        snap = latest_snapshot("BTC/USDT")
        return float(snap["price"]) if snap else None
    except Exception:
        return None


def current_regime() -> str:
    """Read current macro regime tag. Best-effort; 'unknown' on any failure."""
    try:
        from aletheia.storage.repositories import latest_macro_snapshot
        macro = latest_macro_snapshot()
        if macro and macro.get("risk_regime"):
            return macro["risk_regime"]
    except Exception:
        pass
    return "unknown"


def _entry_market_summary(symbol: str, fill_price: float, regime_tag: str) -> str | None:
    """Capture an entry-time market-context snapshot as JSON (truncated). Best-effort."""
    try:
        import json as _json
        from aletheia.data.context_builder import build_market_context_from_db
        ctx = build_market_context_from_db([symbol])
        return _json.dumps({
            "regime": regime_tag,
            "price": fill_price,
            "data_completeness": ctx.data_completeness,
            "tradability": ctx.tradability.get(symbol, {}),
            "leverage": ctx.leverage.get(symbol, {}),
        }, default=str)[:2000]
    except Exception:
        return None


def _decision_reasoning(proposal_id: str | None) -> str | None:
    """Pull the originating proposal's agent rationale for explainability. Best-effort."""
    if not proposal_id:
        return None
    try:
        from aletheia.storage.repositories import _select_one
        proposal = _select_one("trade_proposals", proposal_id)
        if proposal:
            return proposal.get("agent_rationale")
    except Exception:
        pass
    return None


def build_entry_enrichment(
    symbol: str,
    fill_price: float,
    quantity: float,
    requested_price: float,
    fee_rate: float,
    proposal_id: str | None,
) -> dict:
    """Return the entry-time metadata fields every broker must stamp on a new Position.

    Mirrors paper_broker's open path exactly. All sub-lookups are best-effort and
    never raise — a metadata lookup failure must never break order execution (this
    runs AFTER the exchange fill is already confirmed). ``requested_price`` is the
    pre-slippage requested entry; ``fill_price`` is the actual fill, so slippage_cost
    is their per-unit difference times quantity.
    """
    regime_tag = current_regime()
    return {
        "btc_entry_price": btc_price_now(),
        "regime_tag": regime_tag,
        "entry_market_summary": _entry_market_summary(symbol, fill_price, regime_tag),
        "decision_reasoning": _decision_reasoning(proposal_id),
        "entry_fee": round(fill_price * quantity * fee_rate, 8),
        "slippage_cost": round(abs(fill_price - requested_price) * quantity, 8),
    }
