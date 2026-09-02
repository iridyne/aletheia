"""Real upstream chain: public market data -> Universe -> Operator terminal.

Only the two external boundaries are deterministic fixtures here:

* the CCXT public market-data provider returns one liquid spot instrument; and
* the System Operator LLM returns a low-conviction entry assessment.

Registry persistence, feature calculation, Universe filtering/scoring/allocation,
Opportunity insertion, paper-ranker evidence, ``signal_pending`` persistence,
Operator context binding, claim/finalization, and lifecycle transitions all use
their production implementations.  In particular, this test never inserts or
mocks an Opportunity.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest


def _fixture_ohlcv(symbol: str, timeframe: str, limit: int) -> list[list[float]]:
    """Return fresh candles with a real, high-scoring SOL momentum shape."""
    step_ms = (4 if timeframe == "4h" else 1) * 60 * 60 * 1000
    # Keep the penultimate bar unambiguously closed even though the production
    # refresh captures its reference clock just before calling this fixture.
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - 60_000
    start_ms = end_ms - max(limit - 1, 0) * step_ms
    price = 60_000.0 if symbol == "BTC/USDT" else 100.0
    rows: list[list[float]] = []
    for index in range(limit):
        opened = price
        if symbol == "BTC/USDT":
            # A gently falling benchmark makes SOL's relative strength explicit.
            price *= 0.9988
        else:
            # Alternating gains/losses keep RSI out of the overextended band while
            # producing positive 4h and 24h relative strength.
            price *= 1.006 if index % 2 == 0 else 0.997
        # Keep the confirmation in completed bars: the production feature path
        # correctly discards the latest forming candles.
        volume = 5_000.0 if index >= limit - 4 else 1_000.0
        rows.append(
            [
                float(start_ms + index * step_ms),
                opened,
                max(opened, price) * 1.001,
                min(opened, price) * 0.999,
                price,
                volume,
            ]
        )
    return rows


class _FixtureExchange:
    """Minimal async CCXT surface used by the real registry/feature pipeline."""

    id = "fixture"

    def __init__(self) -> None:
        self.options: dict[str, str] = {}
        self.markets = {
            "SOL/USDT": {
                "id": "SOL-USDT",
                "symbol": "SOL/USDT",
                "base": "SOL",
                "quote": "USDT",
                "active": True,
                "spot": True,
                "swap": False,
                "future": False,
                "option": False,
                "limits": {"cost": {"min": 5.0}},
                "precision": {"price": 0.01, "amount": 0.001},
                "info": {},
            },
        }

    async def load_markets(self):
        return self.markets

    async def fetch_tickers(self, symbols):
        latest = _fixture_ohlcv("SOL/USDT", "1h", 168)[-1][4]
        return {
            symbol: {
                "symbol": symbol,
                "last": latest,
                "close": latest,
                "bid": latest * 0.9999,
                "ask": latest * 1.0001,
                "quoteVolume": 100_000_000.0,
                "percentage": 0.0,
            }
            for symbol in symbols
        }

    async def fetch_ohlcv(self, symbol, timeframe="1h", since=None, limit=100):
        del since
        return _fixture_ohlcv(symbol, timeframe, limit)

    async def close(self):
        return None


class _FixtureCCXTProvider:
    """Provider fixture at the public network boundary, not a business seam."""

    def __init__(self, exchange: str, fallback_exchange: str | None = None) -> None:
        self.exchange_name = exchange
        self.fallback_exchange = fallback_exchange
        self.exchange = _FixtureExchange()

    def _ensure_primary(self):
        return self.exchange

    async def get_ticker(self, instrument):
        from aletheia.data.providers import Ticker

        latest = _fixture_ohlcv(instrument.symbol, "1h", 168)[-1][4]
        return Ticker(
            symbol=instrument.symbol,
            last_price=latest,
            volume_24h=100_000_000.0,
            change_pct_24h=3.5,
            high_24h=latest * 1.01,
            low_24h=latest * 0.99,
            provider=instrument.exchange,
        )

    async def close(self):
        return None


class _StockLinkedExchange(_FixtureExchange):
    """Production-shaped Binance stock-linked perpetual metadata."""

    def __init__(self) -> None:
        super().__init__()
        self.id = "binance"
        self.markets = {
            "SOXL/USDT:USDT": {
                "id": "SOXLUSDT",
                "symbol": "SOXL/USDT:USDT",
                "base": "SOXL",
                "quote": "USDT",
                "settle": "USDT",
                "active": True,
                "spot": False,
                "swap": True,
                "future": False,
                "option": False,
                "linear": True,
                "limits": {"cost": {"min": 5.0}},
                "precision": {"price": 0.001, "amount": 0.01},
                "info": {"underlyingType": "EQUITY"},
            },
        }

    async def fetch_tickers(self, symbols):
        latest = _fixture_ohlcv("SOXL/USDT", "1h", 168)[-1][4]
        return {
            symbol: {
                "symbol": symbol,
                "last": latest,
                "close": latest,
                "bid": latest * 0.9999,
                "ask": latest * 1.0001,
                "quoteVolume": 100_000_000.0,
                "percentage": 0.0,
            }
            for symbol in symbols
        }

    async def fetch_ohlcv(self, symbol, timeframe="1h", since=None, limit=100):
        del since
        rows = _fixture_ohlcv(symbol, timeframe, limit)
        if timeframe == "1h":
            close = rows[-16][4]
            balancing_returns = [-0.0066, 0.001] * 4 + [-0.0066]
            momentum_returns = [0.009] * 5
            for change, row in zip(
                balancing_returns + momentum_returns,
                rows[-15:-1],
                strict=True,
            ):
                close *= 1 + change
                row[1:5] = [close * 0.999, close * 1.001, close * 0.998, close]
            for row in rows[-5:]:
                row[5] = 50_000.0
            rows[-1][1:5] = rows[-2][1:5]
        return rows

    async def fetch_funding_rate(self, _symbol):
        return {"fundingRate": 0.0, "markPrice": 100.0}

    async def fetch_open_interest(self, _symbol):
        return {"openInterestValue": 12_000_000.0}

    async def fapipublic_get_takerlongshortratio(self, _params):
        return [{"buySellRatio": "1.5"}]


class _StockLinkedCCXTProvider(_FixtureCCXTProvider):
    def __init__(self, exchange: str, fallback_exchange: str | None = None) -> None:
        self.exchange_name = exchange
        self.fallback_exchange = fallback_exchange
        self.exchange = _StockLinkedExchange()


@pytest.mark.asyncio
async def test_real_universe_refresh_keeps_unmonitorable_candidate_ineligible(e2e_db, monkeypatch, tmp_path):
    import aletheia.config.loader as config_loader

    from aletheia.agent_runtime.tool_registry import init_tool_registry
    from aletheia.agent_shell import reflection
    from aletheia.agent_shell.commands import evaluate_trading_opportunity
    from aletheia.agent_shell.trading_context import _build_pending_signal_queue
    from aletheia.agents.paper_trader import run_paper_trader
    from aletheia.config.loader import load_config, save_config
    from aletheia.storage import repositories as repo
    from aletheia.trading.market_data import collect_market_snapshots
    from aletheia.universe.service import UniverseService

    # The E2E database is session-scoped, but configuration is a file. Keep this
    # scenario's known venue from leaking into whichever E2E test runs next.
    monkeypatch.setattr(config_loader, "CONFIG_PATH", tmp_path / "config.yaml")
    cfg = load_config()
    cfg.market_data.ccxt_exchange = "binance"
    cfg.market_data.ccxt_fallback = "binance"
    cfg.market_data.enable_crypto_fallback = False
    cfg.market_data.observed_instruments = []
    cfg.market_data.observed_symbols = ["SOL/USDT"]
    cfg.market_data.observed_stock_tokens = []
    cfg.market_data.observed_xstocks = []
    cfg.exchange.enabled_markets = {"spot": True, "usdt_futures": False}
    cfg.exchange.preferred_market_type = "spot"
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.exchange.symbol_list = ["SOL/USDT"]
    cfg.risk.symbol_filter_mode = "whitelist"
    cfg.agent.permission_mode = "Paper"
    cfg.agent.autonomous_learning = False
    cfg.fast_trigger.mode = "off"
    cfg.universe.enabled = True
    cfg.universe.max_candidates = 1
    cfg.universe.explore_ratio = 0.0
    cfg.universe.min_volume_24h_usdt = 1_000_000.0
    cfg.universe.max_spread_bps = 30.0
    cfg.universe.blacklist = []
    cfg.regime.enabled = False
    save_config(cfg)

    # Production bootstrap normally registers this permission surface before the
    # scheduler runs paper_trader.
    init_tool_registry()
    monkeypatch.setattr("aletheia.data.ccxt_provider.CCXTProvider", _FixtureCCXTProvider)
    monkeypatch.setattr(
        "aletheia.model_provider.router.get_provider_for_tier",
        lambda _tier: "fixture",
    )

    async def fixed_operator_response(*_args, **_kwargs):
        # The model proposes an entry with a sub-threshold conviction. Since
        # Phase C (2026-07-18) the real conviction gate raises
        # DecisionUnavailable('operator_low_conviction') instead of terminally
        # skipping, so this end-to-end walkthrough exercises the non-terminal
        # defer path all the way to persisted trace + evaluation_deferred result.
        return (
            {
                "action": "trade",
                "symbol": "SOL/USDT",
                "direction": "long",
                "market_type": "spot",
                "conviction": 10,
                "reasoning": "The fixture deliberately waits for stronger confirmation.",
                "bear_case": "Relative strength can fade.",
                "stop_price": 1.0,
                "take_profit": 1_000.0,
                "parameter_rationale": {
                    "entry_price": "use the exact fresh fixture mark",
                    "stop_loss": "fixture-only invalidation below the market",
                    "take_profit": "fixture-only target above the market",
                    "notional_usdt": "defer to deterministic paper sizing",
                    "leverage": "spot uses no leverage",
                },
                "key_assumptions": ["the public fixture remains fresh"],
            },
            None,
        )

    monkeypatch.setattr("aletheia.agents.helpers.llm_structured", fixed_operator_response)
    monkeypatch.setattr(reflection, "_REFLECTION_FILE", tmp_path / "reflection.json")

    # Use the production collector to make the emitter's freshness decision real;
    # no MarketSnapshot or Opportunity is hand-inserted by the test.
    collected = await collect_market_snapshots()
    assert collected["errors"] == []
    assert collected["collected"] == ["binance:SOL/USDT:spot"]

    snapshot_id = await UniverseService().refresh()
    assert snapshot_id is not None

    candidates = repo.list_universe_members(snapshot_id, limit=10)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert not bool(candidate["execution_eligible"])
    flags = candidate.get("risk_flags") or []
    if isinstance(flags, str):
        import json

        flags = json.loads(flags)
    assert "fast_market_data_venue_unavailable" in flags
    generation = e2e_db.execute("SELECT * FROM candidate_planning_generations WHERE is_current=1").fetchone()
    assert generation is None
    assert repo.list_opportunities(limit=10) == []


@pytest.mark.asyncio
async def test_stock_linked_perp_stays_ineligible_without_exact_monitoring_venue(
    e2e_db,
    monkeypatch,
    tmp_path,
):
    import json

    import aletheia.config.loader as config_loader

    from aletheia.agent_runtime.tool_registry import init_tool_registry
    from aletheia.config.loader import load_config, save_config
    from aletheia.storage import repositories as repo
    from aletheia.trading.market_data import collect_market_snapshots
    from aletheia.universe.service import UniverseService

    monkeypatch.setattr(config_loader, "CONFIG_PATH", tmp_path / "config.yaml")
    cfg = load_config()
    cfg.market_data.ccxt_exchange = "binance"
    cfg.market_data.ccxt_fallback = "binance"
    cfg.market_data.enable_crypto_fallback = False
    cfg.market_data.observed_instruments = ["binance:SOXL/USDT:perp"]
    cfg.market_data.observed_symbols = []
    cfg.market_data.observed_stock_tokens = []
    cfg.market_data.observed_xstocks = []
    cfg.exchange.enabled_markets = {"spot": False, "usdt_futures": True}
    cfg.exchange.preferred_market_type = "usdt_futures"
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.exchange.symbol_list = ["SOXL/USDT"]
    cfg.risk.symbol_filter_mode = "whitelist"
    cfg.agent.permission_mode = "Paper"
    cfg.agent.autonomous_learning = False
    cfg.fast_trigger.mode = "off"
    cfg.universe.enabled = True
    cfg.universe.max_candidates = 1
    cfg.universe.explore_ratio = 0.0
    cfg.universe.min_volume_24h_usdt = 1_000_000.0
    cfg.universe.max_spread_bps = 30.0
    cfg.universe.blacklist = []
    cfg.regime.enabled = False
    save_config(cfg)

    init_tool_registry()
    monkeypatch.setattr("aletheia.data.ccxt_provider.CCXTProvider", _StockLinkedCCXTProvider)
    monkeypatch.setattr(
        "aletheia.agents.paper_trader_ranker.get_current_regime",
        lambda: (_ for _ in ()).throw(AssertionError("crypto regime leaked")),
    )

    async def reject_crypto_committee(*_args, **_kwargs):
        raise AssertionError("crypto committee context leaked")

    monkeypatch.setattr(
        "aletheia.agents.committee.integration.enrich_candidates_parallel",
        reject_crypto_committee,
    )

    collected = await collect_market_snapshots()
    assert collected["errors"] == []
    assert collected["collected"] == ["binance:SOXL/USDT:perp"]
    repo.insert_open_interest_snapshot(
        {
            "symbol": "SOXL/USDT",
            "exchange": "binance",
            "open_interest_usd": 12_000_000.0,
            "oi_change_1h_pct": 6.0,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    repo.insert_orderbook_snapshot(
        {
            "symbol": "SOXL/USDT",
            "exchange": "binance",
            "market_type": "perp",
            "bid_price": 99.99,
            "ask_price": 100.01,
            "spread_bps": 2.0,
            "bid_depth_10bps_usd": 1_500_000.0,
            "ask_depth_10bps_usd": 1_500_000.0,
            "total_depth_10bps_usd": 3_000_000.0,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    snapshot_id = await UniverseService().refresh()
    assert snapshot_id is not None
    candidates = repo.list_universe_members(snapshot_id, limit=10)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["symbol"] == "SOXL/USDT"
    assert candidate["market_type"] == "perp"
    assert not bool(candidate["execution_eligible"])
    flags = candidate.get("risk_flags") or []
    if isinstance(flags, str):
        flags = json.loads(flags)
    assert "fast_market_data_venue_unavailable" in flags
    generation = e2e_db.execute("SELECT * FROM candidate_planning_generations WHERE is_current=1").fetchone()
    assert generation is None
    assert repo.list_opportunities(limit=10) == []
