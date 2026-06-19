"""Universe Forward Outcome Recorder.

Tracks how universe members perform AFTER being selected, answering:
"did the scanner families actually pick winners?"

Two entry points called from UniverseService.refresh():
  1. register_pending(members) — create outcome rows for each member × horizon
  2. settle_due() — find overdue pending rows, fetch exit price, compute return

Self-evolution Tier 1:
  After settling, outcomes with 4h forward_return below the loss threshold
  automatically generate guard_candidate knowledge items for human review.
  Closes the feedback loop: scanner signal → actual result → candidate rule.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from aletheia.storage.models import UniverseForwardOutcome
from aletheia.storage import repositories as repo

logger = logging.getLogger("aletheia.universe.outcome_recorder")

# Tier 1: loss threshold for auto guard_candidate generation
_GUARD_HORIZON = "4h"
_LOSS_THRESHOLD_LONG = -3.0   # long outcome: return < -3% triggers candidate
_LOSS_THRESHOLD_SHORT = 3.0   # short outcome: return > +3% triggers candidate
_DEDUP_WINDOW_DAYS = 7

_HORIZONS: dict[str, timedelta] = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "24h": timedelta(hours=24),
}


def register_pending(members: list[dict], entry_ts: str) -> int:
    """Create pending outcome rows for each member × horizon.

    Args:
        members: list of member dicts (must have id, snapshot_id, symbol,
                 scanner_family, entry_price_usd).
        entry_ts: ISO timestamp of the universe snapshot creation.

    Returns:
        Number of outcome rows created.
    """
    ts = datetime.fromisoformat(entry_ts.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    outcomes: list[UniverseForwardOutcome] = []
    skipped_no_price = 0
    for m in members:
        entry_price = m.get("entry_price_usd")
        if not entry_price or entry_price <= 0:
            skipped_no_price += 1
            continue

        for horizon_key, delta in _HORIZONS.items():
            due_at = (ts + delta).isoformat()
            outcomes.append(UniverseForwardOutcome(
                member_id=m["id"],
                snapshot_id=m["snapshot_id"],
                symbol=m["symbol"],
                scanner_family=m.get("scanner_family"),
                direction=m.get("direction"),
                horizon=horizon_key,
                entry_price_usd=entry_price,
                entry_ts=entry_ts,
                due_at=due_at,
            ))

    if outcomes:
        repo.insert_forward_outcomes_batch(outcomes)
        logger.debug("Registered %d forward outcome rows", len(outcomes))

    if skipped_no_price:
        logger.info(
            "Forward-outcome registration skipped %d/%d members with no entry price "
            "(missing 1h OHLCV at feature collection)", skipped_no_price, len(members),
        )

    return len(outcomes)


async def settle_due() -> int:
    """Settle all overdue pending outcomes by fetching current prices.

    Returns:
        Number of outcomes settled.
    """
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    pending = repo.list_pending_forward_outcomes(before_ts=now_iso, limit=500)
    if not pending:
        return 0

    symbols_needed = list({r["symbol"] for r in pending})
    prices = await _batch_fetch_prices(symbols_needed)

    settled = 0
    settled_rows: list[dict] = []
    for row in pending:
        symbol = row["symbol"]
        price = prices.get(symbol)
        if price is None or price <= 0:
            repo.fail_forward_outcome(row["id"], settled_at=now_iso)
            logger.warning("forward_outcome FAILED: symbol=%s horizon=%s — price unavailable", symbol, row.get("horizon"))
            continue

        entry = row["entry_price_usd"]

        # Sanity: reject price deviating >10x from entry in either direction.
        # Catches cross-symbol contamination (e.g. BTC price ~50000 applied to
        # PEPE at entry 0.000003 → ratio 1.6B), which ccxt can return without
        # raising when a symbol isn't found on the fallback exchange.
        ratio = price / entry
        if ratio > 10.0 or ratio < 0.1:
            repo.fail_forward_outcome(row["id"], settled_at=now_iso)
            logger.warning("forward_outcome FAILED: symbol=%s horizon=%s — price unavailable", symbol, row.get("horizon"))
            logger.warning(
                "Rejected anomalous exit price for %s: entry=%.8g exit=%.8g ratio=%.2f",
                symbol, entry, price, ratio,
            )
            continue

        forward_return = ((price - entry) / entry) * 100.0

        repo.settle_forward_outcome(
            outcome_id=row["id"],
            exit_price=price,
            forward_return=forward_return,
            settled_at=now_iso,
        )
        settled += 1
        settled_rows.append({**row, "forward_return_pct": forward_return})

    if settled:
        logger.info("Settled %d / %d forward outcomes", settled, len(pending))

<<<<<<< HEAD
    # Emit rolling win_rate metrics for observability (§4.2)
    if settled:
        try:
            stats = repo.calibration_stats()
            for row in stats:
                if row["n"] >= 10:
                    wr = row["win_count"] / row["n"] * 100
                    logger.info(
                        "METRIC family=%s horizon=%s n=%d win_rate=%.1f%%",
                        row["scanner_family"], row["horizon"], row["n"], wr,
                    )
        except Exception:
            pass
=======
    # Tier 1: auto-generate guard candidates for significant losses
    if settled_rows:
        _maybe_generate_guard_candidates(settled_rows)
>>>>>>> worktree-self-evolution-tier1

    return settled


async def _batch_fetch_prices(symbols: list[str]) -> dict[str, float | None]:
    """Fetch latest prices for all universe symbols.

    Forward outcomes cover the full rotating universe, but stored market
    snapshots only exist for the few statically observed instruments
    (BTC/ETH/SOL). Reading snapshots alone left every rotating member priceless,
    so its outcome was marked `failed` and calibration never accumulated data.

    Fetch live tickers via the shared CCXT market-data provider for all symbols,
    falling back to the latest stored snapshot when a live fetch fails.
    """
    from aletheia.trading.market_data import latest_snapshot

    prices: dict[str, float | None] = {}

    # 1. Live tickers for every symbol (one provider, closed once).
    try:
        from aletheia.data.ccxt_provider import get_market_data_provider

        provider = get_market_data_provider()
        try:
            for sym in symbols:
                try:
                    ticker = await provider.get_ticker(sym)
                    price = float(ticker.last_price) if ticker.last_price else None
                    prices[sym] = price if price and price > 0 else None
                except Exception:
                    prices[sym] = None
        finally:
            await provider.close()
    except Exception as e:  # provider construction failed — fall through to snapshots
        logger.warning("Live price fetch unavailable, using snapshots only: %s", e)

    # 2. Snapshot fallback for any symbol the live fetch couldn't price.
    for sym in symbols:
        if prices.get(sym):
            continue
        try:
            snap = latest_snapshot(sym)
            prices[sym] = float(snap["price"]) if snap and snap.get("price") else None
        except Exception:
            prices[sym] = None

    return prices


def _maybe_generate_guard_candidates(settled_rows: list[dict]) -> None:
    """Self-evolution Tier 1: generate guard_candidate items for notable losses.

    Only processes 4h horizon rows. Direction-aware: long outcomes trigger on
    return < -3%, short outcomes on return > +3%. Deduplicates within 7 days
    per symbol+family to avoid flooding the knowledge base.

    Never raises — failures are logged and ignored so settle_due() is unaffected.
    """
    from aletheia.knowledge.manager import create_guard_candidate

    candidates = [
        r for r in settled_rows
        if r.get("horizon") == _GUARD_HORIZON
        and r.get("forward_return_pct") is not None
        and r.get("scanner_family")
    ]
    if not candidates:
        return

    generated = 0
    for row in candidates:
        fwd = row["forward_return_pct"]
        direction = row.get("direction") or "long"
        symbol = row["symbol"]
        family = row["scanner_family"]

        # Direction-aware loss check
        is_loss = (
            (direction == "short" and fwd > _LOSS_THRESHOLD_SHORT)
            or (direction != "short" and fwd < _LOSS_THRESHOLD_LONG)
        )
        if not is_loss:
            continue

        # Dedup: skip if a candidate for this symbol+family exists within 7 days
        try:
            if repo.guard_candidate_exists_recent(symbol, family, _DEDUP_WINDOW_DAYS):
                logger.debug(
                    "evolution:tier1 skipped dedup symbol=%s family=%s", symbol, family
                )
                continue
        except Exception as e:
            logger.warning("evolution:tier1 dedup check failed: %s", e)
            continue

        title = f"Auto guard candidate: {symbol} {family} 4h loss"
        content = _build_guard_candidate_content(row, fwd, direction)

        try:
            create_guard_candidate(
                title=title,
                content=content,
                metadata={
                    "outcome_id": row["id"],
                    "symbol": symbol,
                    "scanner_family": family,
                    "horizon": _GUARD_HORIZON,
                    "forward_return_pct": fwd,
                    "direction": direction,
                    "entry_ts": row.get("entry_ts", ""),
                    "auto_generated": True,
                    "tier": 1,
                },
                tags=["auto_generated", "tier1_evolution"],
            )
            generated += 1
            logger.info(
                "evolution:tier1 guard_candidate created symbol=%s family=%s return=%.2f%%",
                symbol, family, fwd,
            )
        except Exception as e:
            logger.warning(
                "evolution:tier1 failed to create guard_candidate symbol=%s: %s", symbol, e
            )

    if generated:
        logger.info("evolution:tier1 generated %d guard candidate(s)", generated)


def _build_guard_candidate_content(row: dict, fwd: float, direction: str) -> str:
    symbol = row["symbol"]
    family = row["scanner_family"]
    entry_price = row.get("entry_price_usd", "?")
    entry_ts = row.get("entry_ts", "?")
    outcome_id = row.get("id", "?")
    snapshot_id = row.get("snapshot_id", "?")

    threshold = _LOSS_THRESHOLD_SHORT if direction == "short" else _LOSS_THRESHOLD_LONG
    direction_label = "short（上涨为亏损）" if direction == "short" else "long（下跌为亏损）"

    return f"""## 观察

scanner_family=`{family}` 选出 `{symbol}`，方向 {direction_label}，
4h 实际收益 **{fwd:+.2f}%**（触发阈值：{threshold:+.1f}%）。

## 信号上下文

- symbol: {symbol}
- scanner_family: {family}
- direction: {direction}
- entry_price_usd: {entry_price}
- entry_ts: {entry_ts}
- horizon: 4h
- forward_return_pct: {fwd:+.2f}%
- outcome_id: {outcome_id}
- snapshot_id: {snapshot_id}

## 候选规则（待人工确认）

当 `{family}` scanner 选出 {direction} 信号，且 4h 实际收益为 {fwd:+.2f}%，
说明本次入场的时机或信号强度判断存在问题。

建议：
- 回溯该时点的 RSI、Vol z-score、ATR 等指标值，确认是否存在共性
- 若多次类似形态均出现亏损，可考虑在 scanners.py 中提高该族的门槛
- 确认无误后升级为 trading_guard，将阻止后续同类信号进入执行队列

**此条目为自动生成，medium_confidence，不会参与 LLM 检索。须人工审核后升级。**
"""
