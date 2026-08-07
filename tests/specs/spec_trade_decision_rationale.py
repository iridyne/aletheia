"""Spec: trade decision rationale (entry parameters + honest close causes)."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


def _setup_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    loader._config_cache = None
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    return conn


@spec_claim(
    SpecClaim(
        "TDR-S01", "trade_decision_rationale", "§3.1 entry carrier", "L2a",
        "TradingDecision carries five per-parameter AI reasons and Position persists an "
        "honest exit_reasoning record after closure.",
        status="implemented",
    ),
)
def test_trade_decision_rationale_carriers_exist():
    from aletheia.agent_shell.trading_decision import TradingDecision
    from aletheia.storage.models import Position

    decision_fields = {field.name for field in dataclasses.fields(TradingDecision)}
    assert "parameter_rationale" in decision_fields
    assert "exit_reasoning" in Position.model_fields


@spec_claim(
    SpecClaim(
        "TDR-B01", "trade_decision_rationale", "§3.1 final entry snapshot", "L2b",
        "An operator trade persists the final entry/stop/target/notional/leverage values, "
        "their sources, and five AI reasons inside the proposal's immutable rationale.",
        status="implemented",
    ),
)
def test_proposal_rationale_carries_final_parameter_snapshot(tmp_path, monkeypatch):
    import asyncio

    _setup_db(tmp_path, monkeypatch)
    import aletheia.agent_shell.trading_decision as td
    from aletheia.agent_shell.trading_decision import TradingDecision
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage.models import MarketSnapshot
    from aletheia.storage.repositories import insert_market_snapshot

    captured: dict[str, object] = {}
    monkeypatch.setattr(td, "_proposal_direction_overheated", lambda *_a, **_kw: None)
    snapshot_at = datetime.now(timezone.utc).isoformat()
    insert_market_snapshot(MarketSnapshot(
        id="snapshot-entry-exact",
        symbol="BTC/USDT",
        market_type="perp",
        exchange="binance",
        price=100.0,
        snapshot_at=snapshot_at,
    ))
    monkeypatch.setattr(
        "aletheia.config.loader.load_config", lambda: AletheiaConfig(),
    )
    monkeypatch.setattr("aletheia.trust.guard_chain.run_guards", lambda _ctx: [])
    monkeypatch.setattr(
        "aletheia.trust.guard_chain.summarize",
        lambda _items: {"guards_run": 0, "blocks": 0, "warnings": 0, "details": []},
    )
    monkeypatch.setattr(
        "aletheia.trading.instruments.get_max_leverage", lambda *_a, **_kw: 3,
    )

    def capture_proposal(**kwargs):
        captured.update(kwargs)
        return {"status": "executed", "proposal_id": "tdr-proposal"}

    monkeypatch.setattr(
        "aletheia.execution.paper_tools.create_paper_proposal", capture_proposal,
    )

    decision = TradingDecision(
        action="trade",
        symbol="BTC/USDT",
        direction="long",
        conviction=75,
        reasoning="Breakout has held after a pullback.",
        bear_case="A failed retest would invalidate the breakout.",
        market_type="usdt_futures",
        leverage=9,
        position_size_usdt=25.0,
        parameter_rationale={
            "entry_price": "Enter at the confirmed pullback reclaim rather than chase higher.",
            "stop_loss": "Below the reclaim level, the breakout thesis is invalid.",
            "take_profit": "Target the next visible resistance where momentum may exhaust.",
            "notional_usdt": "Keep exposure small while the setup is still a single-symbol thesis.",
            "leverage": "Use modest leverage to express the perp view without oversizing margin risk.",
        },
    )

    asyncio.run(td.submit_decision_as_proposal(decision, "paper_internal"))
    _text, _sep, raw_meta = str(captured["agent_rationale"]).partition("\n---\n")
    snapshot = json.loads(raw_meta)["entry_parameters"]

    assert set(snapshot) == {
        "entry_price", "stop_loss", "take_profit", "notional_usdt", "leverage",
    }
    assert snapshot["entry_price"]["final_value"] == 100.0
    assert snapshot["entry_price"]["market_snapshot_id"] == "snapshot-entry-exact"
    assert snapshot["entry_price"]["market_type"] == "perp"
    assert snapshot["entry_price"]["snapshot_at"] == snapshot_at
    assert snapshot["entry_price"]["value_source"] == "fresh_exact_market_mark"
    assert snapshot["stop_loss"]["reason"] == decision.parameter_rationale["stop_loss"]
    assert snapshot["take_profit"]["value_source"] == "default_config"
    assert snapshot["notional_usdt"]["actual_value"] == 25.0
    assert snapshot["notional_usdt"]["estimated_loss_at_stop_usdt"] > 0
    assert snapshot["leverage"]["requested_value"] == 9
    assert snapshot["leverage"]["final_value"] == 3
    assert snapshot["leverage"]["value_source"] == "risk_cap_clamped"


@spec_claim(
    SpecClaim(
        "TDR-B02", "trade_decision_rationale", "§3.2 close record", "L2b",
        "A protective close saves an exit explanation identifying the automatic source, "
        "the actual close reason, and the recorded trigger instead of fabricating an AI judgment.",
        status="implemented",
    ),
)
def test_protective_close_persists_honest_exit_reasoning(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    from aletheia.portfolio.pnl import close_position
    from aletheia.storage.models import Position
    from aletheia.storage import repositories as repo

    position = Position(
        symbol="ETH/USDT", market_type="spot", direction="long", quantity=1.0,
        entry_price=100.0, current_price=100.0, execution_mode="paper_internal",
        stop_price=95.0,
    )
    repo.insert_position(position)
    close_position(position.id, exit_price=95.0, reason="stop_loss")

    row = conn.execute(
        "SELECT close_reason, exit_reasoning FROM positions WHERE id = ?", (position.id,)
    ).fetchone()
    assert row["close_reason"] == "stop_loss"
    explanation = json.loads(row["exit_reasoning"])
    assert explanation["actor"] == "system"
    assert explanation["source"] == "position_lifecycle"
    assert explanation["reason_code"] == "stop_loss"
    assert explanation["is_ai_judgment"] is False
    assert explanation["trigger"]["configured_stop_price"] == 95.0


@spec_claim(
    SpecClaim(
        "TDR-B03", "trade_decision_rationale", "§3.2 AI close record", "L2b",
        "A System Operator portfolio-review close replaces the generic lifecycle explanation "
        "only after it closes, preserving the AI's original reason and actor.",
        status="implemented",
    ),
)
def test_operator_close_persists_original_ai_reason_after_success(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    from aletheia.portfolio.position_manager import close_position_routed
    from aletheia.storage.models import MarketSnapshot, Position
    from aletheia.storage import repositories as repo

    position = Position(
        symbol="SOL/USDT", market_type="spot", direction="long", quantity=1.0,
        entry_price=100.0, current_price=100.0, execution_mode="paper_internal",
    )
    repo.insert_position(position)
    repo.insert_market_snapshot(MarketSnapshot(
        symbol=position.symbol,
        market_type="spot",
        exchange="binance",
        price=101.0,
        snapshot_at=datetime.now(timezone.utc).isoformat(),
    ))
    closed = close_position_routed(
        position.id,
        reason="pi_agent_portfolio_review",
        decision_reasoning="Momentum reversed while the market breadth weakened.",
        decision_actor="system_operator",
        decision_source="portfolio_review",
    )

    row = conn.execute("SELECT exit_reasoning FROM positions WHERE id = ?", (position.id,)).fetchone()
    assert closed["fill_price"] == 100.9495
    explanation = json.loads(row["exit_reasoning"])
    assert explanation["actor"] == "system_operator"
    assert explanation["source"] == "portfolio_review"
    assert explanation["reason"] == "Momentum reversed while the market breadth weakened."
    assert explanation["is_ai_judgment"] is True


@spec_claim(
    SpecClaim(
        "TDR-B04", "trade_decision_rationale", "§3.2 reconciliation close record", "L2b",
        "An administrative reconciliation close persists its real evidence source and marks "
        "the record as non-AI rather than producing a fictitious exit thesis.",
        status="implemented",
    ),
)
def test_administrative_close_persists_non_ai_exit_reasoning(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    from aletheia.portfolio.outcome_closure import close_position_administrative
    from aletheia.storage.models import Position
    from aletheia.storage import repositories as repo

    position = Position(
        symbol="ADA/USDT", market_type="spot", direction="long", quantity=2.0,
        entry_price=1.0, current_price=1.0, execution_mode="okx_demo",
    )
    repo.insert_position(position)
    identity = {
        "broker_id": "okx", "account_environment": "demo", "account_scope": "spec-account",
        "execution_mode": "okx_demo", "position_id": position.id,
        "symbol": "ADA/USDT", "market_type": "spot", "direction": "long",
    }
    close_position_administrative(
        position.id,
        reason="reconciled_gone",
        reconciliation_identity=identity,
        reconciliation_evidence={"confirmation": "position absent with complete history"},
        actor="position_reconciler",
        source="verified_exit_protocol",
    )

    row = conn.execute("SELECT exit_reasoning FROM positions WHERE id = ?", (position.id,)).fetchone()
    explanation = json.loads(row["exit_reasoning"])
    assert explanation["actor"] == "position_reconciler"
    assert explanation["source"] == "verified_exit_protocol"
    assert explanation["is_ai_judgment"] is False
    assert explanation["trigger"]["closure_type"] == "administrative"


@spec_claim(
    SpecClaim(
        "TDR-C01", "trade_decision_rationale", "§3.1 required AI explanation", "L2c",
        "An LLM-originated trade intent without all five parameter reasons is retryably "
        "unavailable and cannot consume its queue identity or create an unexplained Proposal.",
        status="implemented",
    ),
)
def test_llm_trade_without_five_parameter_reasons_does_not_open():
    import aletheia.agent_shell.trading_decision as td

    with pytest.raises(
        td.DecisionUnavailable,
        match="incomplete_operator_entry_plan:.*parameter_rationale",
    ):
        td._parse_decision({
            "action": "trade",
            "symbol": "BTC/USDT",
            "direction": "long",
            "conviction": 80,
            "reasoning": "A trade with incomplete explanations.",
            "bear_case": "The breakout can fail.",
            "stop_price": 98.0,
            "take_profit": 104.0,
        })
