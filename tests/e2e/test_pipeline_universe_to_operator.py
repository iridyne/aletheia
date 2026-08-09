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
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
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
        volume = 5_000.0 if index == limit - 1 else 1_000.0
        rows.append([
            float(start_ms + index * step_ms),
            opened,
            max(opened, price) * 1.001,
            min(opened, price) * 0.999,
            price,
            volume,
        ])
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
            provider="fixture",
        )

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_real_universe_refresh_reaches_one_operator_defer(
    e2e_db, monkeypatch, tmp_path
):
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
    # scenario's fixture venue from leaking into whichever E2E test runs next.
    monkeypatch.setattr(config_loader, "CONFIG_PATH", tmp_path / "config.yaml")
    cfg = load_config()
    cfg.market_data.ccxt_exchange = "fixture"
    cfg.market_data.ccxt_fallback = "fixture"
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
    monkeypatch.setattr(
        "aletheia.data.ccxt_provider.CCXTProvider", _FixtureCCXTProvider
    )
    monkeypatch.setattr(
        "aletheia.model_provider.router.get_provider_for_tier",
        lambda _tier: "fixture",
    )
    # This scenario owns one outer SAVEPOINT for rollback isolation. Twin evidence
    # persists on a second thread/connection, which cannot acquire SQLite's writer
    # slot until that SAVEPOINT ends. Exercise Twin persistence in its dedicated
    # contract tests; here, use the production cadence skip to keep this authority
    # pipeline focused on Universe -> Operator.
    monkeypatch.setattr(
        repo,
        "universe_twin_shadow_capture_due",
        lambda *_args, **_kwargs: False,
    )

    async def fixed_operator_response(*_args, **_kwargs):
        # The model proposes an entry with a sub-threshold conviction. Since
        # Phase C (2026-07-18) the real conviction gate raises
        # DecisionUnavailable('operator_low_conviction') instead of terminally
        # skipping, so this end-to-end walkthrough exercises the non-terminal
        # defer path all the way to persisted trace + evaluation_deferred result.
        return ({
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
        }, None)

    monkeypatch.setattr(
        "aletheia.agents.helpers.llm_structured", fixed_operator_response
    )
    monkeypatch.setattr(reflection, "_REFLECTION_FILE", tmp_path / "reflection.json")

    # Use the production collector to make the emitter's freshness decision real;
    # no MarketSnapshot or Opportunity is hand-inserted by the test.
    collected = await collect_market_snapshots()
    assert collected["errors"] == []
    assert collected["collected"] == ["fixture:SOL/USDT:spot"]

    snapshot_id = await UniverseService().refresh()
    assert snapshot_id is not None

    opportunities = repo.list_opportunities(limit=10)
    assert len(opportunities) == 1
    opportunity = opportunities[0]
    assert opportunity["source_agent"] == "universe_scanner"
    assert opportunity["symbol"] == "SOL/USDT"
    assert opportunity["market"] == "spot"
    assert opportunity["direction_bias"] == "long"
    assert opportunity["execution_eligible"] == 1
    assert opportunity["status"] == "open"
    assert opportunity["universe_snapshot_id"] == snapshot_id
    assert opportunity["universe_member_id"]

    ranker_result = await run_paper_trader(
        permission_mode="Paper", run_id="universe-to-operator-ranker"
    )
    assert ranker_result["errors"] == []
    assert ranker_result["outputs"][-1] == "signals_pending:1"

    pending_rows = e2e_db.execute(
        "SELECT action,symbol,direction,market_type,opportunity_id,signal_source "
        "FROM pi_decisions WHERE action='signal_pending'"
    ).fetchall()
    assert [dict(row) for row in pending_rows] == [{
        "action": "signal_pending",
        "symbol": opportunity["symbol"],
        "direction": opportunity["direction_bias"],
        "market_type": opportunity["market"],
        "opportunity_id": opportunity["id"],
        "signal_source": "market_observer",
    }]
    assert repo.get_opportunity(opportunity["id"])["status"] == "queued"

    result = await evaluate_trading_opportunity({})
    # Phase C (2026-07-18): sub-threshold conviction is a non-terminal defer,
    # not a skip. The pending carrier stays queued, no final PiDecision is
    # written, and material_context_change reopens evaluation on the next wake.
    assert result["action"] == "evaluation_deferred"
    assert result["defer_reason"] == "operator_low_conviction"
    assert result["defer_stage"] == "operator_conviction_gate"
    assert result["opportunity_id"] == opportunity["id"]
    assert result["operator_evaluation_event_id"]

    decision_rows = e2e_db.execute(
        "SELECT action,symbol,direction,market_type,opportunity_id,signal_source,"
        "proposal_id,proposal_status FROM pi_decisions WHERE opportunity_id=? "
        "ORDER BY CASE WHEN action='signal_pending' THEN 0 ELSE 1 END, rowid",
        (opportunity["id"],),
    ).fetchall()
    # Only the original signal_pending row — a defer does NOT synthesize a final
    # PiDecision (DL-INV-2 v1.5).
    assert len(decision_rows) == 1
    assert decision_rows[0]["action"] == "signal_pending"
    identity_fields = (
        "symbol", "direction", "market_type", "opportunity_id", "signal_source"
    )
    assert tuple(decision_rows[0][field] for field in identity_fields) == (
        opportunity["symbol"],
        opportunity["direction_bias"],
        opportunity["market"],
        opportunity["id"],
        "market_observer",
    )
    # Opportunity NOT consumed as reviewed — it stays queued for re-evaluation.
    assert repo.get_opportunity(opportunity["id"])["status"] == "queued"
    assert e2e_db.execute("SELECT COUNT(*) FROM trade_proposals").fetchone()[0] == 0

    # The defer event is persisted with the new event_kind.
    defer_event = e2e_db.execute(
        "SELECT event_kind FROM operator_evaluation_events WHERE id=?",
        (result["operator_evaluation_event_id"],),
    ).fetchone()
    assert defer_event is not None
    assert defer_event["event_kind"] == "operator_decision_deferred_low_conviction"

    # The pending queue still exposes the carrier (it remains unresolved).
    visible_pending, queue = _build_pending_signal_queue(limit=10)
    assert queue["unresolved_total"] == 1
    assert queue["expired_unconsumed_rows"] == 0

    evidence = e2e_db.execute(
        "SELECT candidate_kind,filter_name,stage,decision,reason_code,"
        "opportunity_id,universe_snapshot_id,universe_member_id,symbol,"
        "market_type,direction FROM filter_decisions WHERE opportunity_id=? "
        "ORDER BY evaluated_at,rowid",
        (opportunity["id"],),
    ).fetchall()
    evidence_keys = {
        (row["filter_name"], row["stage"], row["decision"], row["reason_code"])
        for row in evidence
    }
    assert (
        "opportunity_emission", "signal_emitter", "passed", "opportunity_emitted"
    ) in evidence_keys
    assert any(row["stage"] == "paper_ranker" for row in evidence)
    assert (
        "signal_batch_limit", "paper_trader_selection", "passed", "signal_batch_limit"
    ) in evidence_keys
    assert (
        "signal_enqueue", "paper_trader_enqueue", "passed", "signal_enqueued"
    ) in evidence_keys
    # Phase C reason_code: operator_low_conviction (was conviction_below_minimum).
    assert (
        "operator_conviction_gate",
        "system_operator",
        "rejected",
        "operator_low_conviction",
    ) in evidence_keys

    # Every per-candidate fact, from emitter to Operator, retains the same source
    # identity instead of switching symbol, market, member, or Opportunity.
    assert {row["opportunity_id"] for row in evidence} == {opportunity["id"]}
    assert {row["universe_snapshot_id"] for row in evidence} == {snapshot_id}
    assert {row["universe_member_id"] for row in evidence} == {
        opportunity["universe_member_id"]
    }
    assert {row["symbol"] for row in evidence} == {opportunity["symbol"]}
    assert {row["market_type"] for row in evidence} == {"spot"}
    assert {row["direction"] for row in evidence} == {"long"}
