"""Populate the isolated preview instance with observation fixture data.

Never touches the real gateway state. Idempotent: reseeding replaces the
observation-facing tables (`market_snapshots`, `universe_snapshots`,
`universe_members`, `market_opportunities`) and only appends OHLCV rows that
are not already present via the standard `INSERT OR IGNORE` semantics of
`upsert_ohlcv_candles`.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import typer

from aletheia.runtime_paths import preview_instance_dir

dev_seed_app = typer.Typer(no_args_is_help=False)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


# The preview universe. Keep grouped so the seed also drives rail groups and
# the Instrument Search popup exercising every asset class.
@dataclass(frozen=True)
class SeedSymbol:
    symbol: str
    market_type: str  # "spot" | "perp"
    scanner_family: str
    base_price: float
    display_name: str


_MEDIUM_UNIVERSE: tuple[SeedSymbol, ...] = (
    SeedSymbol("BTC/USDT", "spot", "momentum", 63500.0, "Bitcoin"),
    SeedSymbol("ETH/USDT", "spot", "momentum", 3120.0, "Ethereum"),
    SeedSymbol("SOL/USDT", "spot", "momentum", 152.0, "Solana"),
    SeedSymbol("BNB/USDT", "spot", "mean_reversion", 594.0, "BNB"),
    SeedSymbol("XRP/USDT", "spot", "mean_reversion", 0.55, "XRP"),
    SeedSymbol("DOGE/USDT", "spot", "event", 0.13, "Dogecoin"),
    SeedSymbol("ADA/USDT", "spot", "mean_reversion", 0.42, "Cardano"),
    SeedSymbol("AVAX/USDT", "spot", "momentum", 30.5, "Avalanche"),
    SeedSymbol("BTC/USDT:USDT", "perp", "derivatives", 63500.0, "Bitcoin Perp"),
    SeedSymbol("ETH/USDT:USDT", "perp", "derivatives", 3120.0, "Ethereum Perp"),
    SeedSymbol("SOL/USDT:USDT", "perp", "derivatives", 152.0, "Solana Perp"),
    SeedSymbol("LINK/USDT:USDT", "perp", "momentum", 14.8, "Chainlink Perp"),
    SeedSymbol("MATIC/USDT:USDT", "perp", "mean_reversion", 0.62, "Polygon Perp"),
    SeedSymbol("APT/USDT:USDT", "perp", "momentum", 8.9, "Aptos Perp"),
    SeedSymbol("ARB/USDT:USDT", "perp", "event", 0.83, "Arbitrum Perp"),
    SeedSymbol("OP/USDT:USDT", "perp", "event", 1.75, "Optimism Perp"),
    SeedSymbol("SUI/USDT:USDT", "perp", "momentum", 2.14, "Sui Perp"),
    SeedSymbol("SHIB/USDT:USDT", "perp", "event", 0.0000185, "Shiba Inu Perp"),
    SeedSymbol("DOT/USDT", "spot", "mean_reversion", 6.7, "Polkadot"),
    SeedSymbol("TON/USDT", "spot", "momentum", 5.9, "Toncoin"),
)

_TIMEFRAMES = ("5m", "15m", "1h", "4h", "1d")
_TIMEFRAME_MINUTES: dict[str, int] = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}


def _generate_bars(seed: SeedSymbol, timeframe: str, count: int, exchange: str):
    """Deterministic OHLCV path with realistic-looking drift + volatility."""
    from aletheia.data.providers import Candle

    tf_min = _TIMEFRAME_MINUTES[timeframe]
    seed_bytes = hashlib.blake2b(
        f"{seed.symbol}|{seed.market_type}|{timeframe}".encode(),
        digest_size=8,
    ).digest()
    rng_state = int.from_bytes(seed_bytes, "big")

    def next_uniform() -> float:
        nonlocal rng_state
        rng_state = (rng_state * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)
        return (rng_state >> 11) / float(1 << 53)

    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start_ts = now - timedelta(minutes=tf_min * count)
    close_price = seed.base_price
    volatility = 0.006 if seed.market_type == "spot" else 0.012
    drift = 0.00015 if seed.scanner_family == "momentum" else -0.0001 if seed.scanner_family == "mean_reversion" else 0.0
    bars: list[Candle] = []
    for index in range(count):
        ts_dt = start_ts + timedelta(minutes=tf_min * index)
        change = drift + (next_uniform() - 0.5) * 2 * volatility
        open_price = close_price
        close_price = max(open_price * (1 + change), 1e-9)
        high_price = max(open_price, close_price) * (1 + next_uniform() * volatility * 0.5)
        low_price = min(open_price, close_price) * (1 - next_uniform() * volatility * 0.5)
        volume = max(seed.base_price * 100 * (0.6 + next_uniform()) / max(seed.base_price, 1), 0.01)
        bars.append(
            Candle(
                timestamp=int(ts_dt.timestamp() * 1000),
                open=round(open_price, 8),
                high=round(high_price, 8),
                low=round(low_price, 8),
                close=round(close_price, 8),
                volume=round(volume, 6),
                is_closed=True,
                provider="preview_seed",
                exchange=exchange,
            )
        )
    return bars


def _clear(table: str) -> int:
    from aletheia.storage.sqlite import execute_write, get_db

    row_count = get_db().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    execute_write(f"DELETE FROM {table}")
    return int(row_count)


def _preview_data_dir(port: int) -> Path:
    override = os.environ.get("ALETHEIA_PREVIEW_DATA_DIR")
    return Path(override) if override else preview_instance_dir(_project_root(), port)


def _seed_ohlcv(symbols: Iterable[SeedSymbol], exchange: str, bars_per_pair: int) -> int:
    from aletheia.storage import repositories as repo

    inserted = 0
    for symbol in symbols:
        for timeframe in _TIMEFRAMES:
            candles = _generate_bars(symbol, timeframe, bars_per_pair, exchange)
            repo.upsert_ohlcv_candles(
                symbol.symbol,
                timeframe,
                candles,
                provider="preview_seed",
                exchange=exchange,
                market_type=symbol.market_type,
            )
            inserted += len(candles)
    return inserted


def _seed_snapshots(symbols: Iterable[SeedSymbol], exchange: str) -> int:
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import MarketSnapshot

    snaps: list[MarketSnapshot] = []
    for symbol in symbols:
        latest_candles = repo.get_ohlcv_candles(
            symbol.symbol,
            "1h",
            limit=25,
            exchange=exchange,
            market_type=symbol.market_type,
        )
        if len(latest_candles) < 2:
            continue
        latest = latest_candles[-1]
        prior = latest_candles[0]
        price = float(latest["close"])
        prior_close = float(prior["close"])
        change_pct = (price - prior_close) / prior_close * 100.0 if prior_close else 0.0
        highs = [float(c["high"]) for c in latest_candles]
        lows = [float(c["low"]) for c in latest_candles]
        volume_24h = sum(float(c["volume"]) for c in latest_candles)
        snaps.append(
            MarketSnapshot(
                symbol=symbol.symbol,
                market_type=symbol.market_type,
                exchange=exchange,
                price=price,
                volume_24h=volume_24h,
                change_pct_24h=change_pct,
                high_24h=max(highs),
                low_24h=min(lows),
                provider="preview_seed",
            )
        )
    return repo.insert_market_snapshot_batch(snaps)

def _seed_universe(symbols: Iterable[SeedSymbol]) -> tuple[str, int]:
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import UniverseAsset, UniverseMember, UniverseSnapshot
    from aletheia.storage.sqlite import get_db

    _clear("universe_members")
    _clear("universe_snapshots")

    snapshot = UniverseSnapshot(market_type="crypto", policy_id=None, status="active", summary=json.dumps({"seed": "medium"}))
    repo.insert_universe_snapshot(snapshot)

    members: list[UniverseMember] = []
    rank = 0
    for symbol in symbols:
        if symbol.market_type != "spot":
            continue
        rank += 1
        if rank > 12:
            break
        asset = UniverseAsset(
            venue="preview_seed",
            market_type=symbol.market_type,
            symbol=symbol.symbol,
            base_asset=symbol.symbol.split("/")[0],
            quote_asset=symbol.symbol.split("/")[1].split(":")[0],
        )
        repo.upsert_universe_asset(asset)
        row = get_db().execute(
            "SELECT id FROM universe_registry WHERE venue = ? AND symbol = ? AND market_type = ?",
            (asset.venue, asset.symbol, asset.market_type),
        ).fetchone()
        if not row:
            raise RuntimeError(f"universe_registry row missing after upsert for {asset.symbol}")
        asset_id = row["id"]
        score = round(94.0 - rank * 3.2, 2)
        members.append(
            UniverseMember(
                snapshot_id=snapshot.id,
                asset_id=asset_id,
                symbol=symbol.symbol,
                market_type=symbol.market_type,
                rank=rank,
                total_score=score,
                opportunity_score=score,
                tradability_score=round(score - 4, 2),
                liquidity_score=round(score - 6, 2),
                estimated_cost=8.0,
                crowding_risk=round(rank * 4.5, 2),
                late_entry_penalty=round(rank * 1.4, 2),
                scanner_family=symbol.scanner_family,
                scanner_direction="long" if symbol.scanner_family != "event" else "short",
                regime_tag="risk_on",
                quote_volume_24h=symbol.base_price * 1_500_000 / max(rank, 1),
                spread_bps=6.0 + rank * 0.5,
                inclusion_reason=json.dumps([f"seed_{symbol.scanner_family}"]),
                risk_flags="[]",
                execution_eligible=True,
                entry_price_usd=symbol.base_price,
            )
        )
    inserted = repo.insert_universe_members_batch(members)
    return snapshot.id, inserted


def _seed_opportunities(symbols: Iterable[SeedSymbol], snapshot_id: str) -> int:
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity

    _clear("opportunities")
    opportunities: list[Opportunity] = []
    watchlist = [s for s in symbols if s.scanner_family in {"momentum", "event", "derivatives"}][:6]
    now = datetime.now(timezone.utc)
    for index, symbol in enumerate(watchlist):
        direction_bias = "long" if index % 2 == 0 else "short"
        opportunities.append(
            Opportunity(
                symbol=symbol.symbol,
                market=symbol.market_type,
                direction_bias=direction_bias,
                thesis=f"Seed opportunity for {symbol.display_name} ({symbol.scanner_family}).",
                confidence="observation",
                confidence_score=68 + index,
                source_agent="preview_seed",
                created_at=(now - timedelta(minutes=15 + index * 20)).isoformat().replace("+00:00", "Z"),
                status="open",
                asset_class="crypto",
                scanner_family=symbol.scanner_family,
                universe_snapshot_id=snapshot_id if symbol.market_type == "spot" else None,
                key_observations=f"Seed observation for {symbol.symbol} {symbol.scanner_family}.",
            )
        )
    for opportunity in opportunities:
        repo.insert_opportunity(opportunity)
    return len(opportunities)


@dev_seed_app.callback(invoke_without_command=True)
def seed_markets(
    port: int = typer.Option(8791, "--port", help="Preview API port; picks the matching isolated data dir."),
    symbols: str = typer.Option("medium", "--symbols", help="Which symbol preset to write (only 'medium' is supported)."),
    exchange: str = typer.Option("binance", "--exchange", help="Exchange name recorded on every row; must match the OHLCV REST default."),
    bars: int = typer.Option(300, "--bars", min=50, max=1000, help="Bars per (symbol, timeframe) tuple."),
):
    """Populate the isolated preview instance with observation fixture data."""
    if symbols != "medium":
        raise typer.BadParameter("only the medium preset is defined", param_hint="--symbols")

    data_dir = _preview_data_dir(port)
    os.environ.setdefault("ALETHEIA_PREVIEW_MODE", "1")
    os.environ["ALETHEIA_PREVIEW_DATA_DIR"] = str(data_dir)

    typer.echo(f"Seeding preview instance under {data_dir}")

    from aletheia.storage.sqlite import get_db

    get_db()  # touches the DB so migrations are asserted before writing.

    universe = list(_MEDIUM_UNIVERSE)
    _clear("market_snapshots")

    ohlcv_count = _seed_ohlcv(universe, exchange, bars)
    snap_count = _seed_snapshots(universe, exchange)
    snapshot_id, member_count = _seed_universe(universe)
    opp_count = _seed_opportunities(universe, snapshot_id)

    typer.echo(
        "Preview seed complete: "
        f"ohlcv={ohlcv_count} bars, snapshots={snap_count}, universe_members={member_count}, opportunities={opp_count}."
    )
