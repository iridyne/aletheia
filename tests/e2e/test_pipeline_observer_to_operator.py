"""Real adjacent-chain proof from Market Observer to System Operator."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest


@pytest.mark.asyncio
async def test_observer_ranker_pending_operator_skip_keeps_one_causal_identity(
    e2e_db, monkeypatch, tmp_path
):
    """A real observed market read is queued and consumed exactly once.

    Only the network market-data boundary and the two LLM answers are fixed.
    Opportunity creation, ranking, selection, pending persistence, Operator
    context/claim/finalization, and all SQLite writes use production code.
    """
    import aletheia.config.loader as loader
    from aletheia.agent_runtime.orchestrator import run_agent
    from aletheia.agents.paper_trader import run_paper_trader
    from aletheia.config.loader import load_config, save_config
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import MarketSnapshot

    # Keep configuration/audit files local to this test while the e2e fixture
    # provides the temporary, migrated SQLite connection.
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "knowledge" / "research_notes").mkdir(parents=True, exist_ok=True)

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.agent.autonomous_learning = False
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.exchange.enabled_markets = {"spot": True, "usdt_futures": False}
    cfg.exchange.preferred_market_type = "spot"
    cfg.exchange.symbol_list = ["BTC/USDT"]
    cfg.risk.symbol_filter_mode = "whitelist"
    cfg.market_data.observed_symbols = ["BTC/USDT"]
    cfg.market_data.observed_instruments = []
    cfg.market_data.observed_xstocks = []
    cfg.market_data.observed_stock_tokens = []
    cfg.market_data.prediction_enabled = False
    cfg.universe.enabled = False
    save_config(cfg)

    snapshot = MarketSnapshot(
        id="observer-operator-snapshot",
        symbol="BTC/USDT",
        market_type="spot",
        exchange="fixture",
        price=60_000.0,
        volume_24h=1_000_000_000.0,
        snapshot_at=datetime.now(timezone.utc).isoformat(),
    )
    repo.insert_market_snapshot(snapshot)

    async def deterministic_market_context(
        symbols, *, market_type="spot", exchange=None, **_kwargs
    ):
        assert symbols == ["BTC/USDT"]
        assert market_type == "spot"
        return (
            "### BTC/USDT\n"
            "| TF | Close | SMA20 | SMA50 | RSI14 | ATR14 | Mom10 |\n"
            "| 1h | 60000 | 59000 | 58000 | 68 | 900 | +3.2% |\n"
            "Breakout momentum is confirmed by price above both moving averages.",
            {
                "symbols": {
                    "BTC/USDT": {
                        "1h": {"close": 60_000.0, "rsi_14": 68.0}
                    }
                },
                "errors": [],
                "exchange": exchange,
            },
        )

    async def deterministic_observer_read(*_args, **_kwargs):
        return (
            {
                "direction_bias": "long",
                "confidence": "high",
                "confidence_score": 90,
                "rationale": (
                    "BTC breakout holds above SMA20 and SMA50 with positive momentum."
                ),
                "key_observations": [
                    "BTC 1h RSI is 68.",
                    "BTC trades at 60000 above SMA20 59000 and SMA50 58000.",
                ],
            },
            None,
        )

    monkeypatch.setattr(
        "aletheia.trading.market_context.build_market_context",
        deterministic_market_context,
    )
    monkeypatch.setattr(
        "aletheia.agents.market_observer.llm_structured",
        deterministic_observer_read,
    )

    observer_result = await run_agent("market_observer", trigger_type="manual")
    assert observer_result["status"] == "completed", observer_result
    assert observer_result["errors"] == []

    opportunities = e2e_db.execute(
        "SELECT * FROM opportunities ORDER BY rowid"
    ).fetchall()
    assert len(opportunities) == 1
    opportunity = dict(opportunities[0])
    assert observer_result["outputs"] == [f"opportunity:{opportunity['id']}"]
    assert (
        opportunity["symbol"],
        opportunity["market"],
        opportunity["direction_bias"],
        opportunity["status"],
    ) == ("BTC/USDT", "spot", "long", "open")

    observer_facts = e2e_db.execute(
        "SELECT * FROM filter_decisions WHERE run_id=? "
        "AND filter_name='observer_pre_filter' ORDER BY rowid",
        (observer_result["run_id"],),
    ).fetchall()
    assert len(observer_facts) == 1
    observer_fact = dict(observer_facts[0])
    assert (
        observer_fact["candidate_kind"],
        observer_fact["symbol"],
        observer_fact["market_type"],
        observer_fact["decision"],
    ) == ("observer_evaluation", "BTC/USDT", "spot", "passed")
    assert observer_result["run_id"] in observer_fact["candidate_key"]

    ranker_run_id = "observer-to-operator-ranker"
    ranker_result = await run_paper_trader(
        permission_mode="Paper", run_id=ranker_run_id
    )
    assert ranker_result["errors"] == [], ranker_result
    assert ranker_result["outputs"][-1] == "signals_pending:1"

    opportunity = repo.get_opportunity(opportunity["id"])
    assert opportunity is not None
    assert opportunity["status"] == "queued"
    pending_rows = e2e_db.execute(
        "SELECT * FROM pi_decisions WHERE action='signal_pending' ORDER BY rowid"
    ).fetchall()
    assert len(pending_rows) == 1
    pending = dict(pending_rows[0])
    expected_identity = {
        "opportunity_id": opportunity["id"],
        "signal_source": "market_observer",
        "symbol": "BTC/USDT",
        "direction": "long",
        "market_type": "spot",
    }
    assert {field: pending[field] for field in expected_identity} == expected_identity

    candidate_key = f"opportunity:{opportunity['id']}"
    candidate_facts = [
        dict(row)
        for row in e2e_db.execute(
            "SELECT * FROM filter_decisions WHERE opportunity_id=? ORDER BY rowid",
            (opportunity["id"],),
        ).fetchall()
    ]
    assert candidate_facts
    assert all(row["candidate_key"] == candidate_key for row in candidate_facts)
    assert all(row["symbol"] == "BTC/USDT" for row in candidate_facts)
    assert all(row["market_type"] == "spot" for row in candidate_facts)
    assert all(row["direction"] == "long" for row in candidate_facts)
    fact_pairs = {(row["stage"], row["filter_name"]) for row in candidate_facts}
    assert ("market_observer", "opportunity_emission") in fact_pairs
    assert ("paper_ranker", "execution_eligibility") in fact_pairs
    assert ("paper_ranker", "ranker_confidence_gate") in fact_pairs
    assert ("paper_trader_selection", "signal_batch_limit") in fact_pairs
    assert ("paper_trader_enqueue", "signal_enqueue") in fact_pairs
    producer_facts = [
        row for row in candidate_facts if row["stage"] == "market_observer"
    ]
    assert len(producer_facts) == 1
    assert (
        producer_facts[0]["run_id"],
        producer_facts[0]["decision"],
        producer_facts[0]["reason_code"],
    ) == (observer_result["run_id"], "passed", "opportunity_emitted")
    assert all(
        row["run_id"] == ranker_run_id
        for row in candidate_facts
        if row["stage"] != "market_observer"
    )

    workset_batch = e2e_db.execute(
        "SELECT * FROM filter_decision_batches WHERE run_id=? "
        "AND filter_name='paper_trader_workset'",
        (ranker_run_id,),
    ).fetchone()
    assert workset_batch is not None
    assert workset_batch["candidate_count"] == 1
    assert workset_batch["passed_count"] == 1

    async def deterministic_operator_skip(*_args, **_kwargs):
        return (
            {
                    # The model expresses trade intent, then the real conviction
                    # gate produces the terminal skip. Direct model skips are
                    # covered separately by candidate-fact validation specs.
                    "action": "trade",
                "symbol": "BTC/USDT",
                "direction": "long",
                "market_type": "spot",
                "leverage": 1,
                "conviction": 35,
                "reasoning": "The queued setup is valid but entry timing is not attractive.",
                "bear_case": "Waiting may miss the breakout continuation.",
                "risk_factors": ["late entry"],
                "supporting_signals": ["breakout momentum"],
                "opposing_signals": ["entry is extended"],
                "key_assumptions": ["the next pullback offers a safer entry"],
            },
            None,
        )

    monkeypatch.setattr(
        "aletheia.agents.helpers.llm_structured", deterministic_operator_skip
    )
    monkeypatch.setattr(
        "aletheia.agent_shell.reflection._REFLECTION_FILE",
        tmp_path / "pi_reflection.json",
    )

    from aletheia.agent_shell.commands import evaluate_trading_opportunity
    from aletheia.agent_shell.trading_context import _build_pending_signal_queue

    operator_result = await evaluate_trading_opportunity({})
    assert operator_result["action"] == "skip"
    assert operator_result["proposal_status"] == "not_applicable"
    assert operator_result["opportunity_id"] == opportunity["id"]
    assert operator_result["signal_source"] == "market_observer"
    assert operator_result["pending_queue_remaining"] == 0

    finals = e2e_db.execute(
        "SELECT * FROM pi_decisions WHERE action!='signal_pending' ORDER BY rowid"
    ).fetchall()
    assert len(finals) == 1
    final = dict(finals[0])
    assert final["action"] == "skip"
    assert {field: final[field] for field in expected_identity} == expected_identity
    assert final["proposal_status"] == "not_applicable"

    reviewed = repo.get_opportunity(opportunity["id"])
    assert reviewed is not None
    assert reviewed["status"] == "reviewed"
    visible_pending, queue = _build_pending_signal_queue(limit=10)
    assert visible_pending == []
    assert queue["unresolved_total"] == 0
    assert e2e_db.execute(
        "SELECT COUNT(*) FROM trade_proposals"
    ).fetchone()[0] == 0
