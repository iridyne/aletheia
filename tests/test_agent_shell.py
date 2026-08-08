"""Tests for the Agent Shell (Pi Agent, Operator Layer Track B Stage 1)."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from aletheia.agent_shell.registry import COMMANDS, get_command, FORBIDDEN_INTENTS
from aletheia.agent_shell.permissions import check_permission, allowed_commands
from aletheia.agent_shell.intent import _classify_keyword, CommandIntent
from aletheia.agent_shell.context import build_app_context, context_summary_text, sections_for_profile
from aletheia.agent_shell.synthesis import _format_fallback


def test_reflection_module_imports_cleanly():
    """Regression guard for the negative-feedback reflection loop (Path 6).

    `commands.py` and `trading_context.py` import reflection lazily inside
    `try/except Exception`, so a SyntaxError/ImportError in reflection.py is
    swallowed silently — record_decision()/get_last_reflection() just stop
    working and no health check fires (commit 5be7826 shipped exactly this:
    a duplicate `opportunity_id=` kwarg -> SyntaxError -> Path 6 dead).

    Importing the module fresh and asserting its public surface trips here in
    milliseconds instead of silently disabling the loop. No DB required.
    """
    import importlib

    mod = importlib.import_module("aletheia.agent_shell.reflection")
    importlib.reload(mod)  # force re-compile, not a cached module object
    assert callable(getattr(mod, "record_decision", None))
    assert callable(getattr(mod, "get_last_reflection", None))


def _init_agent_shell_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import reset_db

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    return conn


def test_operator_context_features_keep_registry_identity(tmp_path, monkeypatch):
    """Feature snapshots must not collapse into one blank-symbol PI feature."""
    conn = _init_agent_shell_db(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        "INSERT INTO universe_registry (id, venue, market_type, symbol) VALUES (?, ?, ?, ?)",
        [
            ("asset-btc-perp", "okx", "perp", "BTC/USDT"),
            ("asset-eth-perp", "okx", "perp", "ETH/USDT"),
        ],
    )
    conn.executemany(
        "INSERT INTO universe_feature_snapshots "
        "(id, asset_id, ts, rsi_14, return_24h) VALUES (?, ?, ?, ?, ?)",
        [
            ("feature-btc", "asset-btc-perp", now, 61.0, 2.5),
            ("feature-eth", "asset-eth-perp", now, 39.0, -1.5),
        ],
    )
    conn.commit()

    import aletheia.data.price_feed as price_feed
    from aletheia.agent_shell.trading_context import _build_layer1_snapshot

    monkeypatch.setattr(
        price_feed,
        "get_cached_price_with_meta",
        lambda symbol: {"price": {"BTC/USDT": 100.0, "ETH/USDT": 200.0}[symbol], "provider": "test"},
    )

    features = _build_layer1_snapshot()["features"]

    assert {
        row["symbol"]: (row["price"], row["rsi_14"])
        for row in features
    } == {
        "BTC/USDT": (100.0, 61.0),
        "ETH/USDT": (200.0, 39.0),
    }


@pytest.mark.asyncio
async def test_portfolio_review_can_close_old_target_beyond_default_position_page(
    tmp_path, monkeypatch
):
    """Execution authority must search every open position, not the UI-sized page."""
    conn = _init_agent_shell_db(tmp_path, monkeypatch)
    from aletheia.agent_shell.commands import review_portfolio_state
    from aletheia.config.loader import load_config
    from aletheia.storage.models import MarketSnapshot, Position
    from aletheia.storage.repositories import (
        insert_market_snapshot,
        insert_position,
        list_positions,
        _select_one,
    )

    target = Position(
        id="oldest-target-beyond-fifty",
        symbol="OLD/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        execution_mode="paper_internal",
    )
    insert_position(target)
    for index in range(50):
        insert_position(Position(
            id=f"newer-open-{index:02d}",
            symbol=f"NEW{index}/USDT",
            market_type="spot",
            direction="long",
            quantity=1.0,
            entry_price=100.0,
            execution_mode="paper_internal",
        ))

    assert target.id not in {
        row["id"] for row in list_positions(status="open")
    }, "fixture no longer crosses the default 50-row boundary"

    monkeypatch.setattr(
        "aletheia.agent_shell.context.build_app_context",
        lambda sections=None: {
            "trading": {"open_positions": [_select_one("positions", target.id)]},
            "risk": {"max_open_positions_demo": 3, "kill_switch": False},
        },
    )

    async def choose_old_target(_system, _user, *, schema, **_kwargs):
        return ({
            "action": "close_one",
            "target_symbol": target.symbol,
            "target_market_type": target.market_type,
            "target_direction": target.direction,
            "target_position_id": target.id,
            "reasoning": "close the explicitly selected old position",
        }, None)

    monkeypatch.setattr(
        "aletheia.agents.helpers.llm_structured", choose_old_target
    )
    cfg = load_config()
    insert_market_snapshot(
        MarketSnapshot(
            symbol=target.symbol,
            market_type="spot",
            exchange=cfg.market_data.ccxt_exchange or "binance",
            price=110.0,
        )
    )

    result = await review_portfolio_state({})

    stored = _select_one("positions", target.id)
    close_rows = conn.execute(
        "SELECT o.position_id, f.fill_price FROM orders o JOIN fills f "
        "ON f.order_id=o.id WHERE o.position_id=? AND o.close_reason=?",
        (target.id, "pi_agent_portfolio_review"),
    ).fetchall()
    assert result["positions_closed"] == 1
    assert stored["status"] == "closed"
    assert [dict(row) for row in close_rows] == [
        {"position_id": target.id, "fill_price": pytest.approx(109.945)}
    ]


@pytest.mark.asyncio
async def test_wake_driven_portfolio_review_scopes_and_persists_hold(
    tmp_path, monkeypatch
):
    conn = _init_agent_shell_db(tmp_path, monkeypatch)
    from aletheia.agent_shell.commands import review_portfolio_state
    from aletheia.storage.models import Position
    from aletheia.storage.repositories import _select_one, insert_position, update_position

    target = Position(
        id="wake-review-target",
        symbol="SOL/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        unrealized_pnl=-0.25,
        execution_mode="paper_internal",
    )
    unrelated = Position(
        id="wake-review-unrelated",
        symbol="ETH/USDT",
        market_type="spot",
        direction="short",
        quantity=1.0,
        entry_price=2000.0,
        unrealized_pnl=1.0,
        execution_mode="paper_internal",
    )
    insert_position(target)
    insert_position(unrelated)
    monkeypatch.setattr(
        "aletheia.agent_shell.context.build_app_context",
        lambda sections=None: {
            "trading": {
                "open_positions": [
                    _select_one("positions", target.id),
                    _select_one("positions", unrelated.id),
                ]
            },
            "risk": {"max_open_positions_demo": 3, "kill_switch": False},
        },
    )

    async def choose_hold(_system, user, *, schema, **_kwargs):
        assert target.id in user
        assert "adverse_signal" in user
        assert unrelated.id not in user
        assert "Positions in this review scope (1 of 2 total open positions)" in user
        assert "Global unrealized PnL: 0.75 USDT" in user
        return ({"action": "hold", "reasoning": "signal is not yet decisive"}, None)

    monkeypatch.setattr("aletheia.agents.helpers.llm_structured", choose_hold)
    wake_batches = [{
        "position_id": target.id,
        "max_priority": 2,
        "events": [{
            "kind": "adverse_signal",
            "priority": 2,
            "payload": {"suggestion": "tighten_stop", "score": 65},
        }],
    }]

    result = await review_portfolio_state({
        "trigger": "adverse_signal",
        "wake_batches": wake_batches,
    })

    row = conn.execute(
        "SELECT * FROM pi_decisions WHERE id=?",
        (result["position_review_decision_id"],),
    ).fetchone()
    evidence = json.loads(row["evidence_manifest_json"])
    assert result["action"] == "hold"
    assert result["wake_fingerprint"].startswith("sha256:")
    assert row["action"] == "hold"
    assert row["symbol"] == target.symbol
    assert evidence["position_review"]["target_position_id"] == target.id
    assert evidence["position_review"]["wake_batches"] == wake_batches
    assert evidence["position_review"]["portfolio_context"] == {
        "review_scope_positions": 1,
        "total_open_positions": 2,
        "total_unrealized_pnl_usdt": 0.75,
        "unrealized_pnl_available": True,
    }


@pytest.mark.asyncio
async def test_portfolio_review_does_not_coerce_missing_global_pnl_to_zero(
    tmp_path, monkeypatch
):
    conn = _init_agent_shell_db(tmp_path, monkeypatch)
    from aletheia.agent_shell.commands import review_portfolio_state
    from aletheia.storage.models import Position
    from aletheia.storage.repositories import insert_position

    target = Position(
        id="wake-review-missing-pnl",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.1,
        entry_price=100.0,
        execution_mode="paper_internal",
    )
    insert_position(target)
    conn.execute(
        "UPDATE positions SET unrealized_pnl=NULL WHERE id=?",
        (target.id,),
    )
    conn.commit()
    monkeypatch.setattr(
        "aletheia.agent_shell.context.build_app_context",
        lambda sections=None: {
            "risk": {"max_open_positions_demo": 3, "kill_switch": False},
        },
    )

    async def choose_hold(_system, user, *, schema, **_kwargs):
        assert "Global unrealized PnL: not available" in user
        return ({"action": "hold", "reasoning": "missing aggregate evidence"}, None)

    monkeypatch.setattr("aletheia.agents.helpers.llm_structured", choose_hold)
    result = await review_portfolio_state({
        "trigger": "horizon_due",
        "wake_batches": [{"position_id": target.id, "events": []}],
    })

    row = conn.execute(
        "SELECT evidence_manifest_json FROM pi_decisions WHERE id=?",
        (result["position_review_decision_id"],),
    ).fetchone()
    context = json.loads(row["evidence_manifest_json"])["position_review"][
        "portfolio_context"
    ]
    assert context["unrealized_pnl_available"] is False
    assert context["total_unrealized_pnl_usdt"] is None


@pytest.mark.asyncio
async def test_wake_driven_portfolio_review_adjusts_exact_position(
    tmp_path, monkeypatch
):
    conn = _init_agent_shell_db(tmp_path, monkeypatch)
    from aletheia.agent_shell.commands import review_portfolio_state
    from aletheia.storage.models import Position
    from aletheia.storage.repositories import _select_one, insert_position

    target = Position(
        id="wake-adjust-target",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.1,
        entry_price=100.0,
        stop_price=90.0,
        execution_mode="paper_internal",
    )
    insert_position(target)
    monkeypatch.setattr(
        "aletheia.agent_shell.context.build_app_context",
        lambda sections=None: {
            "trading": {"open_positions": [_select_one("positions", target.id)]},
            "risk": {"max_open_positions_demo": 3, "kill_switch": False},
        },
    )
    monkeypatch.setattr(
        "aletheia.portfolio.position_manager.get_fresh_price_for_protective_close",
        lambda symbol, market_type, *, venue: 100.0,
    )

    async def choose_adjust(_system, _user, *, schema, **_kwargs):
        return ({
            "action": "adjust_position",
            "target_position_id": target.id,
            "new_stop_price": 95.0,
            "reasoning": "reduce downside after adverse evidence",
        }, None)

    monkeypatch.setattr("aletheia.agents.helpers.llm_structured", choose_adjust)
    result = await review_portfolio_state({
        "trigger": "adverse_signal",
        "wake_batches": [{
            "position_id": target.id,
            "max_priority": 2,
            "events": [{
                "kind": "adverse_signal",
                "priority": 2,
                "payload": {"suggestion": "tighten_stop", "score": 70},
            }],
        }],
    })

    row = conn.execute(
        "SELECT action, evidence_manifest_json FROM pi_decisions WHERE id=?",
        (result["position_review_decision_id"],),
    ).fetchone()
    assert result["adjust"]["updated"] is True
    assert _select_one("positions", target.id)["stop_price"] == pytest.approx(95.0)
    assert row["action"] == "adjust_position"
    assert json.loads(row["evidence_manifest_json"])["position_review"]["effect"][
        "adjust"
    ]["updated"] is True


@pytest.mark.asyncio
async def test_portfolio_review_persists_intent_before_close_submission(
    tmp_path, monkeypatch
):
    conn = _init_agent_shell_db(tmp_path, monkeypatch)
    from aletheia.agent_shell.commands import review_portfolio_state
    from aletheia.storage.models import Position
    from aletheia.storage.repositories import _select_one, insert_position, update_position

    target = Position(
        id="review-close-intent-target",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.1,
        entry_price=100.0,
        execution_mode="bitget_demo",
    )
    insert_position(target)
    monkeypatch.setattr(
        "aletheia.agent_shell.context.build_app_context",
        lambda sections=None: {
            "trading": {"open_positions": [_select_one("positions", target.id)]},
            "risk": {"max_open_positions_demo": 3, "kill_switch": False},
        },
    )

    async def choose_close(_system, _user, *, schema, **_kwargs):
        return ({
            "action": "close_one",
            "target_position_id": target.id,
            "reasoning": "exit after the thesis failed",
        }, None)

    def submit_close(position_id, **_kwargs):
        assert position_id == target.id
        row = conn.execute(
            "SELECT action, evidence_manifest_json FROM pi_decisions "
            "WHERE proposal_reason='position_review' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row["action"] == "close"
        assert json.loads(row["evidence_manifest_json"])["position_review"][
            "effect"
        ] == {"status": "pending"}
        update_position(
            position_id,
            pending_close_order_id="pending-close-order",
            pending_close_status="submitted",
        )
        return {
            "closed": False,
            "status": "submitted",
            "order_id": "pending-close-order",
        }

    monkeypatch.setattr("aletheia.agents.helpers.llm_structured", choose_close)
    monkeypatch.setattr(
        "aletheia.portfolio.position_manager.close_position_routed", submit_close
    )

    result = await review_portfolio_state({"trigger": "horizon_wake"})

    assert result["positions_closed"] == 0
    assert result["close"]["status"] == "submitted"
    assert result["review_completed"] is True
    persisted = conn.execute(
        "SELECT evidence_manifest_json FROM pi_decisions WHERE id=?",
        (result["position_review_decision_id"],),
    ).fetchone()
    effect = json.loads(persisted["evidence_manifest_json"])["position_review"][
        "effect"
    ]
    assert effect["close"]["closed"] is False
    assert effect["positions_closed"] == 0


@pytest.mark.asyncio
async def test_portfolio_review_requires_exact_id_for_mutating_action(
    tmp_path, monkeypatch
):
    conn = _init_agent_shell_db(tmp_path, monkeypatch)
    from aletheia.agent_shell.commands import review_portfolio_state
    from aletheia.storage.models import Position
    from aletheia.storage.repositories import insert_position

    target = Position(
        id="review-exact-id-target",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        execution_mode="paper_internal",
    )
    insert_position(target)
    monkeypatch.setattr(
        "aletheia.agent_shell.context.build_app_context",
        lambda sections=None: {
            "trading": {"open_positions": []},
            "risk": {"max_open_positions_demo": 3, "kill_switch": False},
        },
    )

    async def choose_ambiguous_close(_system, _user, *, schema, **_kwargs):
        return ({
            "action": "close_one",
            "target_symbol": target.symbol,
            "reasoning": "close without an exact durable identity",
        }, None)

    close_calls: list[str] = []
    monkeypatch.setattr(
        "aletheia.agents.helpers.llm_structured", choose_ambiguous_close
    )
    monkeypatch.setattr(
        "aletheia.portfolio.position_manager.close_position_routed",
        lambda position_id, **_kwargs: close_calls.append(position_id),
    )

    result = await review_portfolio_state({})

    assert result["close_one_error"] == "target_position_id_required"
    assert close_calls == []
    row = conn.execute(
        "SELECT evidence_manifest_json FROM pi_decisions WHERE id=?",
        (result["position_review_decision_id"],),
    ).fetchone()
    assert json.loads(row["evidence_manifest_json"])["position_review"]["effect"] == {
        "close_one_error": "target_position_id_required"
    }


def test_proactive_exit_never_defaults_an_unconfirmed_close_to_success(
    tmp_path, monkeypatch
):
    _init_agent_shell_db(tmp_path, monkeypatch)
    from aletheia.portfolio.position_manager import proactive_exit
    from aletheia.storage.models import Position
    from aletheia.storage.repositories import insert_position

    target = Position(
        id="proactive-unconfirmed-target",
        symbol="ETH/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        execution_mode="paper_internal",
    )
    insert_position(target)
    monkeypatch.setattr(
        "aletheia.portfolio.position_manager._latest_price", lambda _symbol: 101.0
    )
    monkeypatch.setattr(
        "aletheia.portfolio.position_manager.close_position_routed",
        lambda *_args, **_kwargs: {"error": "close_not_confirmed"},
    )

    result = proactive_exit(target.id, reason="test unconfirmed close")

    assert result["closed"] is False
    assert result["error"] == "close_not_confirmed"


@pytest.mark.asyncio
async def test_wake_driven_portfolio_review_persists_unavailable_result(
    tmp_path, monkeypatch
):
    conn = _init_agent_shell_db(tmp_path, monkeypatch)
    from aletheia.agent_shell.commands import review_portfolio_state
    from aletheia.storage.models import Position
    from aletheia.storage.repositories import insert_position

    target = Position(
        id="wake-unavailable-target",
        symbol="SOL/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        execution_mode="paper_internal",
    )
    insert_position(target)
    monkeypatch.setattr(
        "aletheia.agent_shell.context.build_app_context",
        lambda sections=None: {
            "trading": {"open_positions": []},
            "risk": {"max_open_positions_demo": 3, "kill_switch": False},
        },
    )

    structured_kwargs = {}

    async def unavailable(_system, _user, *, schema, **kwargs):
        structured_kwargs.update(kwargs)
        return None, "provider_timeout"

    monkeypatch.setattr("aletheia.agents.helpers.llm_structured", unavailable)
    result = await review_portfolio_state({
        "trigger": "adverse_signal",
        "wake_batches": [{
            "position_id": target.id,
            "max_priority": 2,
            "events": [{"kind": "adverse_signal", "priority": 2, "payload": {}}],
        }],
    })

    row = conn.execute(
        "SELECT * FROM pi_decisions WHERE id=?",
        (result["position_review_decision_id"],),
    ).fetchone()
    assert result["action"] == "review_unavailable"
    assert result["reason"] == "operator_review_unavailable"
    assert result["review_error_code"] == "provider_timeout"
    assert structured_kwargs["reasoning_effort"] == "none"
    assert row["action"] == "review_unavailable"
    assert row["proposal_status"] == "failed"
    assert row["proposal_reason"] == "operator_review_unavailable"
    evidence = json.loads(row["evidence_manifest_json"])["position_review"]
    assert evidence["effect"] == {"status": "unavailable"}
    assert evidence["error_code"] == "provider_timeout"
    assert result["review_completed"] is False


@pytest.mark.asyncio
async def test_successful_horizon_review_acknowledges_the_durable_wake(
    tmp_path, monkeypatch
):
    conn = _init_agent_shell_db(tmp_path, monkeypatch)
    from aletheia.agent_shell.commands import review_portfolio_state
    from aletheia.storage.models import Position
    from aletheia.storage.repositories import (
        _select_one,
        insert_position,
        update_position,
    )

    target = Position(
        id="horizon-review-ack-target",
        symbol="SOL/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=102.0,
        unrealized_pnl=2.0,
        execution_mode="paper_internal",
        stop_price=95.0,
        take_profit_price=110.0,
    )
    sibling = Position(
        id="horizon-review-ack-sibling",
        symbol="ETH/USDT",
        market_type="spot",
        direction="short",
        quantity=1.0,
        entry_price=200.0,
        execution_mode="paper_internal",
    )
    insert_position(target)
    insert_position(sibling)
    # Exit-plan fields were added to the durable table after the base Position
    # model; the production open path writes them as a separate update too.
    update_position(target.id, expected_horizon_hours=10.0)
    update_position(sibling.id, expected_horizon_hours=10.0)
    monkeypatch.setattr(
        "aletheia.agent_shell.context.build_app_context",
        lambda sections=None: {
            "risk": {"max_open_positions_demo": 3, "kill_switch": False},
        },
    )

    async def choose_hold(_system, user, *, schema, **_kwargs):
        assert "entry=100.0" in user
        assert "current=102.0" in user
        assert "upnl=2.0" in user
        assert "stop=95.0" in user
        assert "target=110.0" in user
        assert "expected_horizon_hours=10.0" in user
        assert '"hold" acknowledges the one-time horizon review for every' in user
        assert "another horizon wake" in user
        assert "new_horizon_hours to an absolute age from opened_at" in user
        assert 'horizon review will occur after "hold"' in user
        return ({"action": "hold", "reasoning": "thesis remains valid"}, None)

    monkeypatch.setattr("aletheia.agents.helpers.llm_structured", choose_hold)
    result = await review_portfolio_state({
        "trigger": "horizon_due",
        "wake_batches": [
            {
                "position_id": target.id,
                "max_priority": 1,
                "events": [{"kind": "horizon_due", "priority": 1, "payload": {}}],
            },
            {
                "position_id": sibling.id,
                "max_priority": 1,
                "events": [{"kind": "horizon_due", "priority": 1, "payload": {}}],
            },
        ],
    })

    assert result["action"] == "hold"
    assert result["review_completed"] is True
    assert result["horizon_reviews_acknowledged"] == [target.id, sibling.id]
    assert _select_one("positions", target.id)["horizon_wake_sent_at"] is not None
    assert _select_one("positions", sibling.id)["horizon_wake_sent_at"] is not None
    row = conn.execute(
        "SELECT evidence_manifest_json FROM pi_decisions WHERE id=?",
        (result["position_review_decision_id"],),
    ).fetchone()
    assert json.loads(row["evidence_manifest_json"])["position_review"]["effect"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["close_one", "adjust_position"])
async def test_horizon_mutation_acknowledges_only_the_effective_target(
    tmp_path, monkeypatch, action
):
    _init_agent_shell_db(tmp_path, monkeypatch)
    from aletheia.agent_shell.commands import review_portfolio_state
    from aletheia.storage.models import Position
    from aletheia.storage.repositories import _select_one, insert_position

    target = Position(
        id=f"horizon-{action}-target",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        execution_mode="paper_internal",
    )
    sibling = Position(
        id=f"horizon-{action}-sibling",
        symbol="ETH/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=200.0,
        execution_mode="paper_internal",
    )
    insert_position(target)
    insert_position(sibling)
    monkeypatch.setattr(
        "aletheia.agent_shell.context.build_app_context",
        lambda sections=None: {
            "risk": {"max_open_positions_demo": 3, "kill_switch": False},
        },
    )

    async def choose_target(_system, _user, *, schema, **_kwargs):
        return ({
            "action": action,
            "target_position_id": target.id,
            "reasoning": "act only on the selected horizon-due position",
        }, None)

    monkeypatch.setattr("aletheia.agents.helpers.llm_structured", choose_target)
    monkeypatch.setattr(
        "aletheia.portfolio.position_manager.close_position_routed",
        lambda position_id, **_kwargs: {"closed": position_id == target.id},
    )
    monkeypatch.setattr(
        "aletheia.portfolio.position_manager.apply_operator_adjustment",
        lambda position_id, **_kwargs: {"updated": position_id == target.id},
    )
    result = await review_portfolio_state({
        "trigger": "horizon_due",
        "wake_batches": [
            {
                "position_id": position.id,
                "events": [{"kind": "horizon_due", "priority": 1, "payload": {}}],
            }
            for position in (target, sibling)
        ],
    })

    assert result["review_completed"] is True
    assert result["horizon_reviews_acknowledged"] == [target.id]
    assert _select_one("positions", target.id)["horizon_wake_sent_at"] is not None
    assert _select_one("positions", sibling.id)["horizon_wake_sent_at"] is None


@pytest.mark.asyncio
async def test_wake_for_already_closed_position_is_terminal_not_retried(
    tmp_path, monkeypatch
):
    _init_agent_shell_db(tmp_path, monkeypatch)
    from aletheia.agent_shell.commands import review_portfolio_state

    result = await review_portfolio_state({
        "trigger": "horizon_due",
        "wake_batches": [{
            "position_id": "already-closed-position",
            "max_priority": 1,
            "events": [{"kind": "horizon_due", "priority": 1, "payload": {}}],
        }],
    })

    assert result == {
        "action": "none",
        "reason": "wake_positions_not_open",
        "review_completed": True,
    }


@pytest.mark.asyncio
async def test_portfolio_review_stale_mark_leaves_position_and_close_facts_untouched(
    tmp_path, monkeypatch
):
    conn = _init_agent_shell_db(tmp_path, monkeypatch)
    from aletheia.agent_shell.commands import review_portfolio_state
    from aletheia.config.loader import load_config
    from aletheia.storage.models import MarketSnapshot, Position
    from aletheia.storage.repositories import (
        _select_one,
        insert_market_snapshot,
        insert_position,
    )

    target = Position(
        id="portfolio-stale-mark-target",
        symbol="STALE/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=999.0,
        execution_mode="paper_internal",
    )
    insert_position(target)
    cfg = load_config()
    insert_market_snapshot(MarketSnapshot(
        symbol=target.symbol,
        market_type="spot",
        exchange=cfg.market_data.ccxt_exchange or "binance",
        price=120.0,
        snapshot_at=(datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
    ))
    monkeypatch.setattr(
        "aletheia.agent_shell.context.build_app_context",
        lambda sections=None: {
            "trading": {"open_positions": [_select_one("positions", target.id)]},
            "risk": {"max_open_positions_demo": 3, "kill_switch": False},
        },
    )

    async def choose_close(_system, _user, *, schema, **_kwargs):
        return ({
            "action": "close_one",
            "target_position_id": target.id,
            "target_symbol": target.symbol,
            "reasoning": "close only with a fresh observed mark",
        }, None)

    monkeypatch.setattr("aletheia.agents.helpers.llm_structured", choose_close)
    result = await review_portfolio_state({})

    assert result["positions_closed"] == 0
    assert "protective_mark_unavailable" in result["close_error"]
    assert result["review_completed"] is False
    assert _select_one("positions", target.id)["status"] == "open"
    assert conn.execute(
        "SELECT COUNT(*) FROM orders WHERE position_id=? AND close_reason IS NOT NULL",
        (target.id,),
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM fills f JOIN orders o ON o.id=f.order_id "
        "WHERE o.position_id=? AND o.close_reason IS NOT NULL",
        (target.id,),
    ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_portfolio_review_legacy_reduce_exposure_cannot_full_close(
    tmp_path, monkeypatch
):
    _init_agent_shell_db(tmp_path, monkeypatch)
    from aletheia.agent_shell.commands import review_portfolio_state

    position = {
        "id": "legacy-reduce-target",
        "symbol": "BTC/USDT",
        "market_type": "spot",
        "direction": "long",
        "quantity": 1.0,
    }
    monkeypatch.setattr(
        "aletheia.agent_shell.context.build_app_context",
        lambda sections=None: {
            "trading": {"open_positions": [position]},
            "risk": {"max_open_positions_demo": 3, "kill_switch": False},
        },
    )
    monkeypatch.setattr(
        "aletheia.agent_shell.commands.list_all_open_positions",
        lambda: [position],
    )

    seen_schema: dict = {}

    async def legacy_reduce(_system, _user, *, schema, **_kwargs):
        seen_schema.update(schema.model_json_schema())
        return ({
            "action": "reduce_exposure",
            "target_position_id": position["id"],
            "target_symbol": position["symbol"],
            "reasoning": "legacy output must not mean full close",
        }, None)

    close_calls: list[str] = []
    monkeypatch.setattr("aletheia.agents.helpers.llm_structured", legacy_reduce)
    monkeypatch.setattr(
        "aletheia.portfolio.position_manager.close_position_routed",
        lambda position_id, **_kwargs: close_calls.append(position_id),
    )

    result = await review_portfolio_state({})

    assert seen_schema["properties"]["action"]["enum"] == [
        "hold",
        "adjust_position",
        "close_one",
    ]
    assert result["action"] == "hold"
    assert "unsupported_action" in result["reasoning"]
    assert close_calls == []


def test_operator_assessment_counts_all_open_positions_above_one_hundred(
    tmp_path, monkeypatch
):
    """The deterministic Operator assessment must not report its query cap as truth."""
    _init_agent_shell_db(tmp_path, monkeypatch)
    from aletheia.agent_shell.operator_loop import build_system_assessment
    from aletheia.config.loader import load_config, save_config
    from aletheia.storage.models import Position
    from aletheia.storage.repositories import insert_position

    cfg = load_config()
    cfg.risk.max_open_positions_demo = 200
    save_config(cfg)
    for index in range(101):
        insert_position(Position(
            id=f"operator-count-{index:03d}",
            symbol=f"COUNT{index}/USDT",
            market_type="spot",
            direction="long",
            quantity=1.0,
            entry_price=100.0,
            execution_mode="paper_internal",
        ))

    assessment = build_system_assessment()

    assert assessment.open_positions == 101


# --- Registry tests ---

def test_registry_has_32_commands():
    # Position C (2026-07-02): removed propose_live_limited_enablement + 5 strategy-FSM commands.
    assert len(COMMANDS) == 36


def test_registry_all_read_commands_require_observe():
    read_cmds = [c for c in COMMANDS.values() if c.risk_level == "read"]
    assert len(read_cmds) == 12
    for cmd in read_cmds:
        assert cmd.required_permission == "Observe"


def test_registry_operational_commands_require_paper():
    ops = [c for c in COMMANDS.values() if c.risk_level == "operational"]
    assert len(ops) == 13
    observe_ops = {c.name for c in ops if c.required_permission == "Observe"}
    paper_ops = {c.name for c in ops if c.required_permission == "Paper"}
    assert observe_ops == {
        "run_market_scan",
        "rerun_failed_agent_task",
        "propose_command_center_behavior_change",
    }
    assert paper_ops == {
        "pause_agents",
        "resume_agents",
        "set_symbol_observe_only",
        "reconcile_positions",
        "propose_prompt_change",
        "propose_schedule_change",
        "propose_risk_boundary_change",
        "propose_extension_enablement",
        "evaluate_trading_opportunity",
        "review_portfolio_state",
    }


def test_get_command_returns_none_for_unknown():
    assert get_command("nonexistent") is None


# --- Permission tests ---

def test_observe_allows_read_commands():
    allowed, reason = check_permission("get_system_status", "Observe")
    assert allowed is True
    assert reason is None


def test_observe_denies_pause():
    allowed, reason = check_permission("pause_agents", "Observe")
    assert allowed is False
    assert "Paper" in reason


def test_observe_denies_create_note():
    allowed, reason = check_permission("create_research_note", "Observe")
    assert allowed is False


def test_paper_allows_all_15():
    for cmd_name in COMMANDS:
        allowed, reason = check_permission(cmd_name, "Paper")
        assert allowed is True, f"{cmd_name} should be allowed in Paper mode"


def test_full_allows_all_15():
    for cmd_name in COMMANDS:
        allowed, reason = check_permission(cmd_name, "Full")
        assert allowed is True


def test_forbidden_always_denied():
    for intent in FORBIDDEN_INTENTS:
        allowed, reason = check_permission(intent, "Full")
        assert allowed is False
        assert "permanently forbidden" in reason


def test_allowed_commands_observe():
    cmds = allowed_commands("Observe")
    assert "get_system_status" in cmds
    assert "pause_agents" not in cmds
    assert "create_review_report" in cmds
    assert "create_diagnostic_report" in cmds
    assert len(cmds) == 18


def test_allowed_commands_paper():
    cmds = allowed_commands("Paper")
    assert len(cmds) == 36


# --- Intent keyword fallback tests ---

def test_keyword_status():
    intent = _classify_keyword("what is the system status?")
    assert intent.command == "get_system_status"


def test_keyword_pause():
    intent = _classify_keyword("pause everything")
    assert intent.command == "pause_agents"


def test_keyword_market():
    intent = _classify_keyword("show me BTC price")
    assert intent.command == "get_market_snapshot"


def test_keyword_forbidden():
    intent = _classify_keyword("enable live trading now")
    assert intent.command == "forbidden"


def test_keyword_unknown():
    intent = _classify_keyword("hello world how are you today")
    assert intent.command == "query"


def test_keyword_positions():
    intent = _classify_keyword("show my positions")
    assert intent.command == "get_positions"


def test_keyword_operator_status():
    intent = _classify_keyword("show the operator heartbeat")
    assert intent.command == "get_operator_status"


def test_keyword_failed_tasks():
    intent = _classify_keyword("review failed tasks")
    assert intent.command == "get_failed_tasks"


def test_keyword_recommendations():
    intent = _classify_keyword("what recommendations do you have")
    assert intent.command == "get_recommendations"


def test_keyword_suggestions_map_to_recommendations():
    intent = _classify_keyword("what suggestions do you have")
    assert intent.command == "get_recommendations"


def test_keyword_improvement_proposal():
    intent = _classify_keyword("create an improvement proposal")
    assert intent.command == "create_improvement_proposal"


def test_keyword_dreaming_log():
    intent = _classify_keyword("write a dreaming log")
    assert intent.command == "create_dreaming_log"


def test_keyword_mark_task_done():
    intent = _classify_keyword("mark task done")
    assert intent.command == "mark_task_done"


def test_keyword_run_market_scan():
    intent = _classify_keyword("run market scan")
    assert intent.command == "run_market_scan"


def test_keyword_rerun_failed_task():
    intent = _classify_keyword("rerun failed task")
    assert intent.command == "rerun_failed_agent_task"


def test_keyword_symbol_observe_only():
    intent = _classify_keyword("set BTC/USDT observe only")
    assert intent.command == "set_symbol_observe_only"


def test_keyword_create_review_report():
    intent = _classify_keyword("create a risk review report")
    assert intent.command == "create_review_report"


def test_keyword_create_diagnostic_report():
    intent = _classify_keyword("create a system health diagnostic report")
    assert intent.command == "create_diagnostic_report"


def test_keyword_propose_prompt_change():
    intent = _classify_keyword("propose prompt change")
    assert intent.command == "propose_prompt_change"


def test_keyword_propose_schedule_change():
    intent = _classify_keyword("propose schedule change")
    assert intent.command == "propose_schedule_change"


def test_keyword_propose_risk_boundary_change():
    intent = _classify_keyword("propose risk boundary change")
    assert intent.command == "propose_risk_boundary_change"


def test_keyword_propose_command_center_behavior_change():
    intent = _classify_keyword("propose command center behavior change")
    assert intent.command == "propose_command_center_behavior_change"


def test_keyword_archive_low_value_notification():
    intent = _classify_keyword("archive this recommendation")
    assert intent.command == "archive_low_value_notification"


def test_keyword_resume():
    intent = _classify_keyword("resume agents please")
    assert intent.command == "resume_agents"


@pytest.mark.asyncio
async def test_resume_agents_blocks_when_llm_unavailable(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    import aletheia.config.loader as loader
    from aletheia.agent_runtime import resume_gate
    from aletheia.agent_shell.commands import resume_agents

    cfg = loader.load_config()
    cfg.runtime.paused = True
    loader.save_config(cfg)
    monkeypatch.setattr(
        resume_gate,
        "resume_if_llm_available",
        AsyncMock(
            return_value={
                "allowed": False,
                "reason": "llm_unavailable",
                "checked": 1,
                "results": [{"provider": "anthropic_compatible", "usable": False, "error": "429"}],
            }
        ),
    )

    result = await resume_agents({})

    assert result["error"].startswith("Resume blocked")
    assert result["resume_gate"]["reason"] == "llm_unavailable"
    assert loader.load_config().runtime.paused is True


def _insert_recent_proposal(conn, i: int, direction: str) -> None:
    created_at = (datetime.now(timezone.utc) - timedelta(minutes=i)).isoformat()
    conn.execute(
        "INSERT INTO trade_proposals (id, symbol, market_type, direction, created_at, source) "
        "VALUES (?,?,?,?,?,?)",
        (f"bias-p{i}", "BTC/USDT", "spot", direction, created_at, "native"),
    )
    conn.commit()


def _operator_trade_payload(**updates):
    payload = {
        "action": "trade",
        "symbol": "BTC/USDT",
        "direction": "long",
        "market_type": "spot",
        "conviction": 70,
        "reasoning": "momentum continuation",
        "bear_case": "momentum can reverse before follow-through",
        "stop_price": 98.0,
        "take_profit": 104.0,
        "parameter_rationale": {
            key: f"reason for {key}"
            for key in (
                "entry_price", "stop_loss", "take_profit", "notional_usdt", "leverage"
            )
        },
    }
    payload.update(updates)
    return payload


def _operator_entry_context():
    return {
        "layer1": {"opportunities": []},
        "layer2": {
            "entry_mark": {
                "status": "available",
                "symbol": "BTC/USDT",
                "market_type": "spot",
                "price": 100.0,
                "authority": "fresh_exact_market_mark",
            },
        },
    }


@pytest.mark.asyncio
async def test_trading_decision_brakes_one_sided_recent_proposals(tmp_path, monkeypatch):
    conn = _init_agent_shell_db(tmp_path, monkeypatch)
    for i in range(22):
        _insert_recent_proposal(conn, i, "long")

    from aletheia.agent_shell import trading_decision as td

    monkeypatch.setattr(
        td,
        "_assess_opportunity",
        AsyncMock(return_value=_operator_trade_payload()),
    )
    debate = AsyncMock()
    monkeypatch.setattr(td, "_run_debate", debate)

    decision = await td.evaluate_trading_opportunity(_operator_entry_context())

    # Brake rejection uses a distinct action so the decisions API can tell it apart
    # from a genuine LLM-initiated operator_skip (#330).
    assert decision.action == "portfolio_guard_skip"
    assert "directional proposal brake" in decision.reasoning
    brake_trace = next(
        item for item in decision.filter_trace
        if item["filter_name"] == "direction_bias_brake"
    )
    assert brake_trace["decision"] == "rejected"
    assert brake_trace["reason_code"] == "proposal_direction_concentration"
    debate.assert_not_awaited()


@pytest.mark.asyncio
async def test_trading_decision_bias_brake_allows_high_conviction(tmp_path, monkeypatch):
    conn = _init_agent_shell_db(tmp_path, monkeypatch)
    for i in range(22):
        _insert_recent_proposal(conn, i, "long")

    from aletheia.agent_shell import trading_decision as td

    monkeypatch.setattr(
        td,
        "_assess_opportunity",
        AsyncMock(return_value=_operator_trade_payload(
            conviction=90,
            reasoning="exceptionally clean setup",
        )),
    )

    decision = await td.evaluate_trading_opportunity(_operator_entry_context())

    assert decision.action == "trade"
    assert decision.position_size_usdt is not None


@pytest.mark.asyncio
async def test_trading_decision_records_debate_trace(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell import trading_decision as td

    monkeypatch.setattr(
        td,
        "_assess_opportunity",
        AsyncMock(return_value=_operator_trade_payload(
            conviction=60,
            reasoning="uncertain breakout",
        )),
    )
    monkeypatch.setattr(
        td,
        "_run_debate",
        AsyncMock(return_value=td.DebateResult(
            bull_confidence=40,
            bull_thesis="some upside",
            bull_evidence=["support"],
            bear_confidence=80,
            bear_thesis="bad entry",
            bear_evidence=["resistance"],
        )),
    )

    from aletheia.agent_shell.trading_decision import DecisionUnavailable

    with pytest.raises(DecisionUnavailable, match="debate_not_confirmed") as exc_info:
        await td.evaluate_trading_opportunity(_operator_entry_context())

    exc = exc_info.value
    assert exc.reason == "debate_not_confirmed"
    assert exc.stage == "operator_debate_gate"
    debate_entry = next(
        e for e in exc.filter_trace if e.get("filter_name") == "debate_downgrade"
    )
    assert debate_entry["decision"] == "rejected"
    assert debate_entry["inputs"]["initial_conviction"] == 60
    assert debate_entry["inputs"]["final_conviction"] == 48
    assert debate_entry["inputs"]["bull_confidence"] == 40
    assert debate_entry["inputs"]["bear_confidence"] == 80
    assert debate_entry["reason_code"] == "debate_not_confirmed"


@pytest.mark.asyncio
async def test_direct_model_skip_records_operator_action_trace(tmp_path, monkeypatch):
    """A direct model skip remains an auditable operator gate, not a trace-less exit."""
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell import trading_decision as td

    monkeypatch.setattr(td, "_assess_opportunity", AsyncMock(return_value={
        "action": "skip",
        "symbol": "BTC/USDT",
        "direction": "short",
        "market_type": "usdt_futures",
        "conviction": 31,
        "reasoning": "sideways market offers no directional edge",
    }))

    decision = await td.evaluate_trading_opportunity({"layer1": {"opportunities": []}})

    assert decision.action == "skip"
    assert decision.filter_trace[0] == {
        "filter_name": "operator_action_assessment",
        "stage": "system_operator",
        "decision": "rejected",
        "reason_code": "operator_model_skip",
        "inputs": {
            "model_action": "skip",
            "initial_conviction": 31,
            "confirmation_attempts": 0,
            "initial_model_action": "skip",
        },
        "parameters": {"schema_actions": ["trade", "skip"]},
    }


# --- Synthesis fallback tests ---

def test_fallback_error():
    result = _format_fallback({"error": "something broke"})
    assert "something broke" in result


def test_fallback_message():
    result = _format_fallback({"message": "Agents paused."})
    assert result == "Agents paused."


def test_fallback_list():
    result = _format_fallback({"positions": [{"symbol": "BTC/USDT", "pnl": 5.0}]})
    assert "BTC/USDT" in result


# --- Context tests ---

@patch("aletheia.agent_shell.context.load_config")
@patch("aletheia.agent_runtime.health.compute_health",
       return_value={"healthy": True, "issues": []})
def test_build_context_system_section(mock_health, mock_cfg):
    from unittest.mock import MagicMock
    cfg = MagicMock()
    cfg.agent.permission_mode = "Paper"
    cfg.live.state = "live_disabled"
    cfg.runtime.paused = False
    cfg.runtime.kill_switch_active = False
    cfg.exchange.mode = "demo"
    mock_cfg.return_value = cfg

    ctx = build_app_context(["system"])

    assert "system" in ctx
    assert ctx["system"]["healthy"] is True
    assert ctx["system"]["permission_mode"] == "Paper"


def test_context_summary_text():
    ctx = {
        "system": {"healthy": True, "issues": [], "permission_mode": "Paper", "paused": False},
        "market": [{"symbol": "BTC/USDT", "price": 65000}],
        "agents": {"total_recent": 5, "failed_recent": 1},
        "trading": {"open_positions": [{"symbol": "SOL/USDT"}]},
    }
    text = context_summary_text(ctx)
    assert "healthy" in text
    assert "BTC/USDT" in text
    assert "5 recent runs" in text


@patch("aletheia.agent_shell.context.load_config")
@patch("aletheia.agent_shell.context.LessonStore")
def test_learning_context_exposes_only_applied_unexpired_lessons(mock_lessons, mock_cfg):
    """The live Operator receives reviewed Learning facts, never raw proposals."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    cfg = MagicMock()
    cfg.agent.permission_mode = "Paper"
    cfg.live.state = "live_disabled"
    cfg.runtime.paused = False
    cfg.runtime.kill_switch_active = False
    cfg.exchange.mode = "demo"
    mock_cfg.return_value = cfg
    mock_lessons.return_value.list_all.return_value = [
        SimpleNamespace(
            lesson_id="applied-current",
            category="quality",
            severity="warning",
            summary="Recent opportunities need stricter evidence.",
            created_at="2026-07-14T00:00:00+00:00",
            expires_at="2027-08-01T00:00:00+00:00",
        ),
        SimpleNamespace(
            lesson_id="applied-expired",
            category="quality",
            severity="warning",
            summary="Expired lesson must not be shown.",
            created_at="2026-06-01T00:00:00+00:00",
            expires_at="2026-06-02T00:00:00+00:00",
        ),
    ]

    ctx = build_app_context(["learning"])

    assert ctx["learning"]["advisory_only"] is True
    assert ctx["learning"]["lessons"] == [
        {
            "lesson_id": "applied-current",
            "category": "quality",
            "severity": "warning",
            "summary": "Recent opportunities need stricter evidence.",
            "created_at": "2026-07-14T00:00:00+00:00",
        }
    ]
    assert ctx["learning"]["manifest"]["lesson_ids"] == ["applied-current"]


def test_repair_context_includes_doctor_section():
    assert "doctor" in sections_for_profile("repair_context")


@patch("aletheia.agent_shell.context.load_config")
@patch("aletheia.agent_runtime.health.compute_health",
       return_value={"healthy": False, "issues": ["market_data_stale:45m"]})
@patch("aletheia.diagnostics.doctor.build_report")
def test_build_context_doctor_section_surfaces_fix_hints(mock_doctor, mock_health, mock_cfg):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    cfg = MagicMock()
    cfg.agent.permission_mode = "Paper"
    cfg.live.state = "live_disabled"
    cfg.runtime.paused = False
    cfg.runtime.kill_switch_active = False
    cfg.exchange.mode = "demo"
    mock_cfg.return_value = cfg

    mock_doctor.return_value = SimpleNamespace(to_dict=lambda: {
        "generated_at": "2026-07-03T00:00:00Z",
        "healthy": False,
        "summary": {
            "total": 2,
            "blockers": ["process.gateway"],
            "warns": ["health.rollup"],
        },
        "checks": [
            {
                "name": "process.gateway",
                "severity": "blocker",
                "detail": "stopped",
                "fix_hint": "`aletheia gateway start`",
            },
            {
                "name": "health.rollup",
                "severity": "warn",
                "detail": "degraded: market_data_stale:45m",
                "fix_hint": "inspect market data scheduler",
            },
        ],
    })

    ctx = build_app_context(["system", "doctor"])

    assert ctx["doctor"]["healthy"] is False
    assert ctx["doctor"]["summary"]["blockers"] == ["process.gateway"]
    assert ctx["doctor"]["attention_checks"][0]["fix_hint"] == "`aletheia gateway start`"

    text = context_summary_text(ctx)
    assert "Doctor: 1 blocker(s), 1 warn(s)" in text
    assert "process.gateway" in text
    assert "`aletheia gateway start`" in text


# --- Runtime integration test (mocked LLM) ---

@pytest.mark.asyncio
async def test_runtime_handle_status_query():
    """Full cycle: 'what is system status' → keyword → get_system_status → response."""
    from unittest.mock import MagicMock

    db = MagicMock()
    cfg = MagicMock()
    cfg.agent.permission_mode = "Paper"
    cfg.live.state = "live_disabled"
    cfg.runtime.paused = False
    cfg.runtime.kill_switch_active = False
    cfg.runtime.scheduler_enabled = True
    cfg.exchange.mode = "demo"
    cfg.risk.max_daily_loss_pct = 1.0
    cfg.risk.max_demo_position_notional_usdt = 200.0
    cfg.risk.max_open_positions_demo = 6

    with patch("aletheia.agent_shell.sessions.get_db", return_value=db), \
         patch("aletheia.agent_shell.audit.get_db", return_value=db), \
         patch("aletheia.config.loader.load_config", return_value=cfg), \
         patch("aletheia.agent_runtime.health.compute_health",
               return_value={"healthy": True, "issues": []}), \
         patch("aletheia.storage.repositories._select_all", return_value=[]), \
         patch("aletheia.agents.helpers.llm_structured",
               new_callable=AsyncMock, return_value=(None, "unavailable")), \
         patch("aletheia.agents.helpers.llm_complete",
               new_callable=AsyncMock, return_value=(None, "unavailable")):

        from aletheia.agent_shell.runtime import AgentShellRuntime
        runtime = AgentShellRuntime()
        resp = await runtime.handle("what is the system status?")

    assert resp.intent == "get_system_status"
    assert "health" in resp.response.lower()


@pytest.mark.asyncio
async def test_runtime_reads_operator_status_from_heartbeat(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    import aletheia.config.loader as loader
    from aletheia.agent_shell.operator_store import create_task, record_heartbeat

    cfg = loader.load_config()
    cfg.agent.permission_mode = "Observe"
    cfg.operator.permission_mode = "ReadOnly"
    cfg.runtime.paused = False
    loader.save_config(cfg)

    task_id = create_task(
        task_type="diagnose_system_health",
        status="running",
        priority="high",
        created_by="operator_loop",
        assigned_to="operator",
        input_data={"issue": "market_data_stale:15m"},
    )
    record_heartbeat(
        agent_id="operator",
        status="online",
        mode="Working",
        permission_mode="ReadOnly",
        is_busy=True,
        current_task_id=task_id,
        current_intent="investigating market_data_stale:15m",
        queue_size=2,
        last_error=None,
        model_provider="pi-shell",
        session_id="operator-main",
    )

    with patch(
        "aletheia.agent_runtime.health.compute_health",
        return_value={"healthy": True, "issues": []},
    ), patch(
        "aletheia.agents.helpers.llm_structured",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ), patch(
        "aletheia.agents.helpers.llm_complete",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ):
        from aletheia.agent_shell.runtime import AgentShellRuntime

        runtime = AgentShellRuntime()
        resp = await runtime.handle("show the operator heartbeat")

    assert resp.intent == "get_operator_status"
    assert "Working" in resp.response
    assert "queue" in resp.response.lower()


@pytest.mark.asyncio
async def test_runtime_handles_what_is_the_system_doing_with_operator_evidence(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.operator_store import create_task, record_heartbeat
    from aletheia.storage.models import AgentRun, Position
    from aletheia.storage.repositories import insert_agent_run, insert_position
    import aletheia.config.loader as loader

    cfg = loader.load_config()
    cfg.agent.permission_mode = "Observe"
    cfg.operator.permission_mode = "ReadOnly"
    cfg.live.state = "live_disabled"
    cfg.runtime.paused = False
    cfg.runtime.kill_switch_active = False
    cfg.runtime.scheduler_enabled = True
    cfg.exchange.mode = "demo"
    loader.save_config(cfg)

    insert_agent_run(AgentRun(agent_name="market_observer", status="completed"))
    insert_agent_run(AgentRun(agent_name="paper_trader", status="failed", errors='["network timeout"]'))
    insert_position(
        Position(
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            quantity=0.01,
            entry_price=65000,
            execution_mode="paper_internal",
        )
    )
    task_id = create_task(
        task_type="diagnose_system_health",
        status="running",
        priority="high",
        created_by="operator_loop",
        assigned_to="operator",
        input_data={"issue": "market_data_stale:15m"},
    )
    record_heartbeat(
        agent_id="operator",
        status="online",
        mode="Working",
        permission_mode="ReadOnly",
        is_busy=True,
        current_task_id=task_id,
        current_intent="investigating market_data_stale:15m",
        queue_size=1,
        last_error=None,
        model_provider="pi-shell",
        session_id="operator-main",
    )

    with patch(
        "aletheia.agent_runtime.health.compute_health",
        return_value={"healthy": False, "issues": ["market_data_stale:15m"]},
    ), patch(
        "aletheia.agents.helpers.llm_structured",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ), patch(
        "aletheia.agents.helpers.llm_complete",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ):
        from aletheia.agent_shell.runtime import AgentShellRuntime

        runtime = AgentShellRuntime()
        resp = await runtime.handle("what is the system doing?")

    assert resp.intent == "get_system_status"
    assert "market_data_stale:15m" in resp.response
    assert "Working" in resp.response
    assert "1 failed recent agent run" in resp.response
    assert "1 open position" in resp.response


@pytest.mark.asyncio
async def test_runtime_explains_why_proposal_failed_from_risk_decision(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    import aletheia.config.loader as loader
    from aletheia.storage.models import RiskDecision, TradeProposal
    from aletheia.storage.repositories import insert_risk_decision, insert_trade_proposal

    cfg = loader.load_config()
    cfg.agent.permission_mode = "Observe"
    cfg.operator.permission_mode = "ReadOnly"
    loader.save_config(cfg)

    proposal = TradeProposal(
        symbol="ETH/USDT",
        market_type="spot",
        direction="short",
        status="rejected",
        agent_rationale="Funding reversal thesis.",
    )
    insert_trade_proposal(proposal)
    insert_risk_decision(
        RiskDecision(
            proposal_id=proposal.id,
            approved=False,
            risk_score=0.91,
            violations='["max_daily_loss", "permission_mode_insufficient:Observe"]',
            message="Daily loss limit breached.",
        )
    )

    with patch(
        "aletheia.agent_runtime.health.compute_health",
        return_value={"healthy": True, "issues": []},
    ), patch(
        "aletheia.agents.helpers.llm_structured",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ), patch(
        "aletheia.agents.helpers.llm_complete",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ):
        from aletheia.agent_shell.runtime import AgentShellRuntime

        runtime = AgentShellRuntime()
        resp = await runtime.handle("why did this proposal fail?")

    assert resp.intent == "explain_proposal"
    assert "ETH/USDT" in resp.response
    assert "Daily loss limit breached." in resp.response
    assert "max_daily_loss" in resp.response
    assert "risk score 0.91" in resp.response


@pytest.mark.asyncio
async def test_runtime_reads_backend_recommendations(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    import aletheia.config.loader as loader
    from aletheia.agent_shell.operator_store import create_task

    cfg = loader.load_config()
    cfg.runtime.paused = True
    loader.save_config(cfg)

    create_task(
        task_type="diagnose_system_health",
        status="failed",
        priority="high",
        created_by="operator_loop",
        assigned_to="operator",
        input_data={"issue": "market_data_stale:45m"},
        error="market_data_stale:45m",
    )

    with patch(
        "aletheia.agent_runtime.health.compute_health",
        return_value={"healthy": True, "issues": []},
    ), patch(
        "aletheia.agents.helpers.llm_structured",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ), patch(
        "aletheia.agents.helpers.llm_complete",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ):
        from aletheia.agent_shell.runtime import AgentShellRuntime

        runtime = AgentShellRuntime()
        resp = await runtime.handle("what recommendations do you have?")

    assert resp.intent == "get_recommendations"
    assert "recommendation" in resp.response.lower()


@pytest.mark.asyncio
async def test_runtime_uses_session_kind_context_profile_for_selected_branch(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.operator_store import create_session_record
    from aletheia.agent_shell.runtime import AgentShellRuntime

    create_session_record(
        session_id="strategy-session-context",
        user_input="Investigate strategy drift.",
        kind="strategy_session",
        parent_session_id="operator-main",
        title="Strategy Drift Review",
    )

    captured_sections: list[list[str]] = []

    def fake_build_app_context(sections=None):
        captured_sections.append(list(sections or []))
        return {"system": {"healthy": True}}

    with patch(
        "aletheia.agent_shell.context.build_app_context",
        side_effect=fake_build_app_context,
    ), patch(
        "aletheia.agent_shell.context.context_summary_text",
        return_value="mock strategy summary",
    ), patch(
        "aletheia.agent_shell.intent.classify_intent",
        new_callable=AsyncMock,
        return_value=CommandIntent(command="forbidden", parameters={}, confidence=1.0),
    ):
        runtime = AgentShellRuntime()
        resp = await runtime.handle(
            "continue the current strategy branch",
            session_id="strategy-session-context",
        )

    assert resp.intent == "forbidden"
    assert captured_sections == [sections_for_profile("trading_context")]


@pytest.mark.asyncio
async def test_runtime_includes_recent_session_context_and_trace_in_branch_summary(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.operator_store import append_message, create_session_record
    from aletheia.agent_shell.runtime import AgentShellRuntime

    create_session_record(
        session_id="diagnostic-session-history",
        user_input="Investigate stale market data.",
        kind="diagnostic_session",
        parent_session_id="operator-main",
        title="Stale Market Diagnostic",
    )
    append_message(
        session_id="diagnostic-session-history",
        role="assistant",
        message_type="context",
        content={"text": "Diagnostic branch context: market_data_stale:45m affects promotion safety."},
    )
    append_message(
        session_id="diagnostic-session-history",
        role="assistant",
        message_type="response",
        content={"text": "Previous diagnostic result: keep the gateway restart todo visible."},
    )

    captured_summary: dict[str, str] = {}

    async def fake_classify_intent(text, summary):
        captured_summary["value"] = summary
        return CommandIntent(command="query", parameters={}, confidence=0.9)

    with patch(
        "aletheia.agent_runtime.health.compute_health",
        return_value={"healthy": True, "issues": []},
    ), patch(
        "aletheia.agents.helpers.llm_complete",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ), patch(
        "aletheia.agent_shell.intent.classify_intent",
        side_effect=fake_classify_intent,
    ):
        runtime = AgentShellRuntime()
        resp = await runtime.handle(
            "what should this branch do next?",
            permission_mode="ReadOnly",
            permission_scope="operator",
            session_id="diagnostic-session-history",
        )

    assert resp.intent == "query"
    assert "Diagnostic branch context: market_data_stale:45m affects promotion safety." in captured_summary["value"]
    assert "Previous diagnostic result: keep the gateway restart todo visible." in captured_summary["value"]


@pytest.mark.asyncio
async def test_runtime_includes_parent_branch_trace_for_nested_session_summary(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.operator_store import append_message, create_session_record
    from aletheia.agent_shell.runtime import AgentShellRuntime

    create_session_record(
        session_id="strategy-parent-session",
        user_input="Investigate Momentum root.",
        kind="strategy_session",
        parent_session_id="operator-main",
        title="Momentum Root",
    )
    create_session_record(
        session_id="review-child-session",
        user_input="Review nested drawdown branch.",
        kind="review_session",
        parent_session_id="strategy-parent-session",
        title="Nested Drawdown Review",
    )
    append_message(
        session_id="strategy-parent-session",
        role="assistant",
        message_type="context",
        content={"text": "Parent branch context: Momentum root still owns the promotion backlog."},
    )
    append_message(
        session_id="strategy-parent-session",
        role="assistant",
        message_type="response",
        content={"text": "Parent branch result: resolve promotion blockers before widening experiments."},
    )

    captured_summary: dict[str, str] = {}

    async def fake_classify_intent(text, summary):
        captured_summary["value"] = summary
        return CommandIntent(command="query", parameters={}, confidence=0.9)

    with patch(
        "aletheia.agent_runtime.health.compute_health",
        return_value={"healthy": True, "issues": []},
    ), patch(
        "aletheia.agents.helpers.llm_complete",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ), patch(
        "aletheia.agent_shell.intent.classify_intent",
        side_effect=fake_classify_intent,
    ):
        runtime = AgentShellRuntime()
        resp = await runtime.handle(
            "what does this nested branch depend on?",
            permission_mode="ReadOnly",
            permission_scope="operator",
            session_id="review-child-session",
        )

    assert resp.intent == "query"
    assert "Parent branch context: Momentum root still owns the promotion backlog." in captured_summary["value"]
    assert "Parent branch result: resolve promotion blockers before widening experiments." in captured_summary["value"]


@pytest.mark.asyncio
async def test_runtime_dispatches_enabled_extension_to_target_command(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    import aletheia.config.loader as loader
    from aletheia.agent_shell.operator_store import create_extension_record
    from aletheia.storage.models import MarketSnapshot
    from aletheia.storage.repositories import insert_market_snapshot

    cfg = loader.load_config()
    cfg.agent.permission_mode = "Observe"
    cfg.operator.permission_mode = "ReadOnly"
    loader.save_config(cfg)

    insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=65000))
    create_extension_record(
        session_id="operator-main",
        extension_name="Summarize Market",
        slash_command="/summarize-market",
        description="Summarize the latest market snapshot via slash command.",
        target_command="get_market_snapshot",
        manifest={
            "slash_command": "/summarize-market",
            "target_command": "get_market_snapshot",
            "description": "Summarize the latest market snapshot via slash command.",
            "default_parameters": {"symbol": "BTC/USDT"},
            "capabilities": ["operator_command_proxy"],
            "sandbox_only": True,
            "requires_human_approval": True,
        },
        status="enabled",
        created_by="operator",
        enabled_by="human-review",
    )

    with patch(
        "aletheia.agent_runtime.health.compute_health",
        return_value={"healthy": True, "issues": []},
    ), patch(
        "aletheia.agents.helpers.llm_structured",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ), patch(
        "aletheia.agents.helpers.llm_complete",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ):
        from aletheia.agent_shell.runtime import AgentShellRuntime

        runtime = AgentShellRuntime()
        resp = await runtime.handle("/summarize-market")

    assert resp.intent == "get_market_snapshot"
    assert "BTC/USDT" in resp.response


@pytest.mark.asyncio
async def test_runtime_reuses_explicit_session_id_for_operator_turn(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    import aletheia.config.loader as loader
    from aletheia.agent_shell.operator_store import create_session_record, list_commands, list_sessions

    cfg = loader.load_config()
    cfg.agent.permission_mode = "Observe"
    cfg.operator.permission_mode = "ReadOnly"
    loader.save_config(cfg)

    create_session_record(
        session_id="strategy-session-1",
        user_input="review strategy",
        kind="strategy_session",
        parent_session_id="operator-main",
        title="Strategy Review",
    )

    with patch(
        "aletheia.agent_runtime.health.compute_health",
        return_value={"healthy": True, "issues": []},
    ), patch(
        "aletheia.agents.helpers.llm_structured",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ), patch(
        "aletheia.agents.helpers.llm_complete",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ):
        from aletheia.agent_shell.runtime import AgentShellRuntime

        runtime = AgentShellRuntime()
        resp = await runtime.handle(
            "review failed tasks",
            permission_mode="ReadOnly",
            permission_scope="operator",
            session_id="strategy-session-1",
        )

    assert resp.intent == "get_failed_tasks"
    commands = list_commands("strategy-session-1")
    assert len(commands) == 1
    sessions = list_sessions(limit=10)
    assert [session["id"] for session in sessions].count("strategy-session-1") == 1
    assert len(sessions) == 2  # operator-main placeholder + the explicit child session


@pytest.mark.asyncio
async def test_runtime_maps_extension_text_tail_into_target_parameters(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    import aletheia.config.loader as loader
    from aletheia.agent_shell.operator_store import create_extension_record
    from aletheia.storage.repositories import list_knowledge_items

    cfg = loader.load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.operator.permission_mode = "Research"
    loader.save_config(cfg)

    create_extension_record(
        session_id="operator-main",
        extension_name="Quick Research Note",
        slash_command="/create-research-note",
        description="Create a research note from a slash command tail.",
        target_command="create_research_note",
        manifest={
            "slash_command": "/create-research-note",
            "target_command": "create_research_note",
            "description": "Create a research note from a slash command tail.",
            "default_parameters": {"title": "Quick Note"},
            "text_parameter": "content",
            "capabilities": ["operator_command_proxy"],
            "sandbox_only": True,
            "requires_human_approval": True,
        },
        status="enabled",
        created_by="operator",
        enabled_by="human-review",
    )

    with patch(
        "aletheia.agent_runtime.health.compute_health",
        return_value={"healthy": True, "issues": []},
    ), patch(
        "aletheia.agents.helpers.llm_structured",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ), patch(
        "aletheia.agents.helpers.llm_complete",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ):
        from aletheia.agent_shell.runtime import AgentShellRuntime

        runtime = AgentShellRuntime()
        resp = await runtime.handle("/create-research-note Review BTC basis dislocation before Asia open. Funding rates turning negative suggests crowded longs being squeezed.")

    assert resp.intent == "create_research_note"
    notes = list_knowledge_items(item_type="research_note", limit=10)
    assert len(notes) == 1
    assert notes[0]["title"] == "Quick Note"


@pytest.mark.asyncio
async def test_runtime_rejects_extension_that_is_not_enabled(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    import aletheia.config.loader as loader
    from aletheia.agent_shell.operator_store import create_extension_record

    cfg = loader.load_config()
    cfg.agent.permission_mode = "Observe"
    cfg.operator.permission_mode = "ReadOnly"
    loader.save_config(cfg)

    create_extension_record(
        session_id="operator-main",
        extension_name="Explain Proposal",
        slash_command="/explain-proposal",
        description="Explain the latest proposal via slash command.",
        target_command="explain_proposal",
        manifest={
            "slash_command": "/explain-proposal",
            "target_command": "explain_proposal",
            "description": "Explain the latest proposal via slash command.",
            "capabilities": ["operator_command_proxy"],
            "sandbox_only": True,
            "requires_human_approval": True,
        },
        status="proposed",
        created_by="operator",
    )

    with patch(
        "aletheia.agent_runtime.health.compute_health",
        return_value={"healthy": True, "issues": []},
    ), patch(
        "aletheia.agents.helpers.llm_structured",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ), patch(
        "aletheia.agents.helpers.llm_complete",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ):
        from aletheia.agent_shell.runtime import AgentShellRuntime

        runtime = AgentShellRuntime()
        resp = await runtime.handle("/explain-proposal")

    assert resp.intent == "extension_not_enabled"
    assert "not enabled" in resp.response.lower()


@pytest.mark.asyncio
async def test_runtime_explains_what_failed_with_specific_failures(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    import aletheia.config.loader as loader
    from aletheia.agent_shell.operator_store import create_task
    from aletheia.storage.models import AgentRun
    from aletheia.storage.repositories import insert_agent_run

    cfg = loader.load_config()
    cfg.agent.permission_mode = "Observe"
    cfg.operator.permission_mode = "ReadOnly"
    loader.save_config(cfg)

    create_task(
        task_type="diagnose_system_health",
        status="failed",
        priority="high",
        created_by="operator_loop",
        assigned_to="operator",
        input_data={"issue": "market_data_stale:45m"},
        error="market_data_stale:45m",
    )
    insert_agent_run(
        AgentRun(
            agent_name="paper_trader",
            status="failed",
            errors='["exchange timeout"]',
        )
    )

    with patch(
        "aletheia.agent_runtime.health.compute_health",
        return_value={"healthy": True, "issues": []},
    ), patch(
        "aletheia.agents.helpers.llm_structured",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ), patch(
        "aletheia.agents.helpers.llm_complete",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ):
        from aletheia.agent_shell.runtime import AgentShellRuntime

        runtime = AgentShellRuntime()
        resp = await runtime.handle("what failed?")

    assert resp.intent == "get_failed_tasks"
    assert "diagnose_system_health" in resp.response
    assert "paper_trader" in resp.response


@pytest.mark.asyncio
async def test_create_daily_review_handler_preserves_item_type(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import create_daily_review
    from aletheia.storage.repositories import list_knowledge_items

    result = await create_daily_review({"title": "Daily Review", "content": "Today was stable. Markets traded sideways with low volatility. No significant news events. System health nominal, all agents running as expected."})

    assert result["created"] is True
    items = list_knowledge_items(item_type="daily_review")
    assert len(items) == 1
    assert items[0]["item_type"] == "daily_review"
    assert items[0]["title"] == "Daily Review"


@pytest.mark.asyncio
async def test_create_dreaming_log_handler_preserves_item_type(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import create_dreaming_log
    from aletheia.storage.repositories import list_knowledge_items

    result = await create_dreaming_log({"title": "Dreaming Log", "content": "Pattern synthesis: momentum divergence on BTC 4h chart with RSI overbought and flat price action suggesting distribution phase."})

    assert result["created"] is True
    items = list_knowledge_items(item_type="dreaming_log")
    assert len(items) == 1
    assert items[0]["item_type"] == "dreaming_log"
    assert items[0]["title"] == "Dreaming Log"


@pytest.mark.asyncio
async def test_mark_task_done_completes_operator_task(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import mark_task_done
    from aletheia.agent_shell.operator_store import create_task, get_task

    task_id = create_task(
        task_type="diagnose_system_health",
        status="running",
        priority="high",
        created_by="operator_loop",
        assigned_to="operator",
        input_data={"issue": "market_data_stale:15m"},
    )

    result = await mark_task_done({"task_id": task_id, "summary": "issue reviewed"})

    assert result["completed"] is True
    task = get_task(task_id)
    assert task is not None
    assert task["status"] == "completed"
    assert task["output_data"]["summary"] == "issue reviewed"


@pytest.mark.asyncio
async def test_run_market_scan_triggers_market_observer(monkeypatch):
    from aletheia.agent_shell.commands import run_market_scan

    async def fake_run_agent(agent_name: str, trigger_type: str = "scheduled"):
        assert agent_name == "market_observer"
        assert trigger_type == "manual"
        return {"run_id": "run-scan-1", "status": "completed", "outputs": ["scan complete"], "errors": []}

    monkeypatch.setattr("aletheia.agent_shell.commands.run_agent", fake_run_agent, raising=False)

    result = await run_market_scan({})

    assert result["run"]["agent_name"] == "market_observer"
    assert result["run"]["run_id"] == "run-scan-1"


@pytest.mark.asyncio
async def test_rerun_failed_agent_task_uses_latest_failed_run(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import rerun_failed_agent_task
    from aletheia.storage.models import AgentRun
    from aletheia.storage.repositories import insert_agent_run

    insert_agent_run(AgentRun(agent_name="paper_trader", status="failed", errors='["timeout"]'))
    insert_agent_run(AgentRun(agent_name="market_observer", status="completed"))

    async def fake_run_agent(agent_name: str, trigger_type: str = "scheduled"):
        assert agent_name == "paper_trader"
        assert trigger_type == "manual"
        return {"run_id": "run-rerun-1", "status": "completed_with_errors", "outputs": [], "errors": ["still flaky"]}

    monkeypatch.setattr("aletheia.agent_shell.commands.run_agent", fake_run_agent, raising=False)

    result = await rerun_failed_agent_task({})

    assert result["rerun"]["agent_name"] == "paper_trader"
    assert result["rerun"]["new_run_id"] == "run-rerun-1"


@pytest.mark.asyncio
async def test_set_symbol_observe_only_removes_trading_but_keeps_observation(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    import aletheia.config.loader as loader
    from aletheia.agent_shell.commands import set_symbol_observe_only

    cfg = loader.load_config()
    cfg.risk.symbol_filter_mode = "whitelist"
    cfg.risk.symbol_list = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    cfg.exchange.symbol_list = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    cfg.market_data.observed_symbols = ["BTC/USDT", "ETH/USDT"]
    cfg.market_data.observed_instruments = []
    loader.save_config(cfg)

    result = await set_symbol_observe_only({"symbol": "BTC/USDT"})

    assert result["updated"] is True
    updated = loader.load_config()
    assert "BTCUSDT" not in updated.exchange.symbol_list
    assert "BTCUSDT" not in updated.risk.symbol_list
    assert "BTC/USDT" in updated.market_data.observed_symbols


@pytest.mark.asyncio
async def test_archive_low_value_notification_creates_artifact_and_filters_recommendations(
    tmp_path, monkeypatch
):
    _init_agent_shell_db(tmp_path, monkeypatch)

    import aletheia.config.loader as loader
    from aletheia.agent_shell.commands import archive_low_value_notification
    from aletheia.agent_shell.operator_recommendations import build_recommendations
    from aletheia.agent_shell.operator_store import list_artifacts

    cfg = loader.load_config()
    cfg.runtime.paused = True
    loader.save_config(cfg)

    before = build_recommendations(limit=10)
    assert any(item["id"] == "gateway-paused" for item in before)

    result = await archive_low_value_notification({"recommendation_id": "gateway-paused"})

    assert result["archived"] is True
    artifacts = list_artifacts(session_id="operator-main")
    assert artifacts[0]["artifact_type"] == "notification_archive"
    assert artifacts[0]["content"]["recommendation_id"] == "gateway-paused"

    after = build_recommendations(limit=10)
    assert all(item["id"] != "gateway-paused" for item in after)


@pytest.mark.asyncio
async def test_create_review_report_writes_markdown_and_artifact(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import create_review_report
    from aletheia.agent_shell.operator_store import list_artifacts

    result = await create_review_report(
        {
            "report_type": "risk_review",
            "title": "Risk Review Report",
            "content": "# Risk Review\n\n- rejected decisions: 2\n- proposal candidates: 1\n",
            "summary": "Summarized recent risk rejections for operator follow-up.",
        }
    )

    assert result["created"] is True
    assert result["report_type"] == "risk_review"
    report_path = Path(result["path"])
    assert report_path.exists()
    assert report_path.read_text(encoding="utf-8").startswith("# Risk Review Report")

    artifacts = list_artifacts(session_id="operator-main")
    assert artifacts[0]["artifact_type"] == "review_report"
    assert artifacts[0]["content"]["report_type"] == "risk_review"
    assert artifacts[0]["content"]["file_path"] == str(report_path)
    assert "rejected decisions" in artifacts[0]["content"]["body"]


@pytest.mark.asyncio
async def test_create_diagnostic_report_writes_markdown_and_artifact(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import create_diagnostic_report
    from aletheia.agent_shell.operator_store import list_artifacts

    result = await create_diagnostic_report(
        {
            "report_type": "system_health",
            "title": "System Health Diagnostic Report",
            "content": "# System Health\n\n- status: degraded\n- issue: market_data_stale:45m\n",
            "summary": "Captured degraded gateway health for operator diagnosis.",
        }
    )

    assert result["created"] is True
    assert result["report_type"] == "system_health"
    report_path = Path(result["path"])
    assert report_path.exists()
    assert "market_data_stale:45m" in report_path.read_text(encoding="utf-8")

    artifacts = list_artifacts(session_id="operator-main")
    assert artifacts[0]["artifact_type"] == "diagnostic_report"
    assert artifacts[0]["content"]["report_type"] == "system_health"
    assert artifacts[0]["content"]["file_path"] == str(report_path)
    assert "degraded gateway health" in artifacts[0]["content"]["summary"].lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("report_type", "title", "summary"),
    [
        ("worker_health", "Worker Health Diagnostic Report", "Captured degraded worker health for operator diagnosis."),
        ("model_runtime_health", "Model Runtime Health Diagnostic Report", "Captured degraded model/runtime health for operator diagnosis."),
        ("data_source_health", "Data Source Health Diagnostic Report", "Captured degraded data-source health for operator diagnosis."),
    ],
)
async def test_create_diagnostic_report_supports_specialized_health_report_types(
    tmp_path,
    monkeypatch,
    report_type,
    title,
    summary,
):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import create_diagnostic_report
    from aletheia.agent_shell.operator_store import list_artifacts

    result = await create_diagnostic_report(
        {
            "report_type": report_type,
            "title": title,
            "content": f"# {title}\n\n- issue: test\n",
            "summary": summary,
        }
    )

    assert result["created"] is True
    assert result["report_type"] == report_type
    report_path = Path(result["path"])
    assert report_path.exists()
    assert title in report_path.read_text(encoding="utf-8")

    artifacts = list_artifacts(session_id="operator-main")
    assert artifacts[0]["artifact_type"] == "diagnostic_report"
    assert artifacts[0]["content"]["report_type"] == report_type
    assert artifacts[0]["content"]["file_path"] == str(report_path)
    assert summary == artifacts[0]["content"]["summary"]


@pytest.mark.asyncio
async def test_propose_prompt_change_creates_artifact(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import propose_prompt_change
    from aletheia.agent_shell.operator_store import list_artifacts

    result = await propose_prompt_change(
        {
            "target": "paper_trader.system_prompt",
            "content": "Tighten rejection reasoning.",
            "rationale": "Reduce vague trade explanations.",
        }
    )

    assert result["proposed"] is True
    artifacts = list_artifacts(session_id="operator-main")
    assert artifacts[0]["artifact_type"] == "prompt_change_proposal"
    assert artifacts[0]["content"]["target"] == "paper_trader.system_prompt"


@pytest.mark.asyncio
async def test_propose_schedule_change_creates_artifact(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import propose_schedule_change
    from aletheia.agent_shell.operator_store import list_artifacts

    result = await propose_schedule_change(
        {
            "agent_name": "market_observer",
            "requested_schedule": "every 10 minutes",
            "rationale": "faster scan cadence during active sessions",
        }
    )

    assert result["proposed"] is True
    artifacts = list_artifacts(session_id="operator-main")
    assert artifacts[0]["artifact_type"] == "schedule_change_proposal"
    assert artifacts[0]["content"]["agent_name"] == "market_observer"


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_propose_risk_boundary_change_creates_artifact(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import propose_risk_boundary_change
    from aletheia.agent_shell.operator_store import list_artifacts
    from aletheia.config.loader import load_config
    from aletheia.risk.policy import risk_policy_hash

    demo_policy = load_config().risk.demo

    result = await propose_risk_boundary_change(
        {
            "risk_mode": "demo",
            "policy_id": demo_policy.policy_id,
            "policy_revision": demo_policy.revision,
            "policy_hash": risk_policy_hash(demo_policy),
            "changes": {"max_demo_position_notional_usdt": 300.0},
            "rationale": "Paper sizing is too restrictive for current symbol set.",
        }
    )

    assert result["proposed"] is True
    artifacts = list_artifacts(session_id="operator-main")
    assert artifacts[0]["artifact_type"] == "risk_boundary_change_proposal"
    assert artifacts[0]["content"]["risk_mode"] == "demo"
    assert artifacts[0]["content"]["policy_id"] == demo_policy.policy_id
    assert artifacts[0]["content"]["policy_revision"] == demo_policy.revision
    assert artifacts[0]["content"]["policy_hash"] == risk_policy_hash(demo_policy)
    assert artifacts[0]["content"]["changes"]["max_demo_position_notional_usdt"] == 300.0


@pytest.mark.asyncio
async def test_propose_risk_boundary_change_rejects_stale_policy_identity(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import propose_risk_boundary_change
    from aletheia.agent_shell.operator_store import list_artifacts
    from aletheia.config.loader import load_config
    from aletheia.risk.policy import risk_policy_hash

    demo_policy = load_config().risk.demo
    result = await propose_risk_boundary_change(
        {
            "risk_mode": "demo",
            "policy_id": demo_policy.policy_id,
            "policy_revision": demo_policy.revision + 1,
            "policy_hash": risk_policy_hash(demo_policy),
            "changes": {"max_daily_loss_pct": 2.0},
            "rationale": "stale candidate",
        }
    )

    assert "stale policy identity" in result["error"]
    assert list_artifacts(session_id="operator-main") == []


@pytest.mark.asyncio
async def test_propose_command_center_behavior_change_creates_artifact(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import propose_command_center_behavior_change
    from aletheia.agent_shell.operator_store import list_artifacts

    result = await propose_command_center_behavior_change(
        {
            "target_command": "pause_agents",
            "requested_behavior": "Show an inline permission gate before retrying pause_agents from a denied mode.",
            "rationale": "Users keep retrying the same denied control without a better affordance.",
            "evidence": {
                "rejection_count": 2,
                "sample_inputs": ["pause everything", "pause agents now"],
            },
        }
    )

    assert result["proposed"] is True
    artifacts = list_artifacts(session_id="operator-main")
    assert artifacts[0]["artifact_type"] == "command_center_behavior_proposal"
    assert artifacts[0]["content"]["target_command"] == "pause_agents"


@pytest.mark.asyncio
async def test_draft_extension_manifest_persists_registry_and_manifest_file(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import draft_extension_manifest
    from aletheia.agent_shell.operator_store import list_extensions

    result = await draft_extension_manifest(
        {
            "extension_name": "Summarize Market",
            "slash_command": "/summarize-market",
            "description": "Summarize the latest market snapshot via a slash command.",
            "target_command": "get_market_snapshot",
            "default_parameters": {"symbol": "BTC/USDT"},
            "capabilities": ["operator_command_proxy"],
        }
    )

    assert result["created"] is True
    extensions = list_extensions()
    assert len(extensions) == 1
    assert extensions[0]["status"] == "draft"
    assert extensions[0]["slash_command"] == "/summarize-market"

    manifest_files = list((tmp_path / "agent_shell" / "extensions").glob("*.json"))
    assert len(manifest_files) == 1
    manifest = json.loads(manifest_files[0].read_text(encoding="utf-8"))
    assert manifest["slash_command"] == "/summarize-market"
    assert manifest["target_command"] == "get_market_snapshot"


@pytest.mark.asyncio
async def test_test_extension_sandbox_records_failed_run_and_scratch_report(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import draft_extension_manifest, test_extension_sandbox
    from aletheia.agent_shell.operator_store import get_extension, list_extension_runs

    draft = await draft_extension_manifest(
        {
            "extension_name": "Leaky Extension",
            "slash_command": "/leaky-extension",
            "description": "An unsafe extension that incorrectly requests credential access.",
            "target_command": "get_market_snapshot",
            "capabilities": ["credentials_access"],
        }
    )

    result = await test_extension_sandbox({"extension_id": draft["extension_id"]})

    assert result["passed"] is False
    assert result["status"] == "failed"
    assert "credentials access" in result["summary"].lower()
    scratch_path = tmp_path / "agent_shell" / "scratch"
    scratch_files = list(scratch_path.glob("*.md"))
    assert len(scratch_files) == 1
    scratch_body = scratch_files[0].read_text(encoding="utf-8").lower()
    assert "sandbox test" in scratch_body
    assert "credentials access" in scratch_body

    extension = get_extension(draft["extension_id"])
    assert extension is not None
    assert extension["status"] == "sandbox_failed"
    runs = list_extension_runs(extension_id=draft["extension_id"])
    assert len(runs) == 1
    assert runs[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_propose_extension_enablement_requires_passed_sandbox_and_creates_artifact(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import (
        draft_extension_manifest,
        propose_extension_enablement,
        test_extension_sandbox,
    )
    from aletheia.agent_shell.operator_store import get_extension, list_artifacts

    draft = await draft_extension_manifest(
        {
            "extension_name": "Explain Proposal",
            "slash_command": "/explain-proposal",
            "description": "Open a structured explanation flow for the latest proposal.",
            "target_command": "explain_proposal",
            "capabilities": ["operator_command_proxy"],
        }
    )

    sandbox = await test_extension_sandbox({"extension_id": draft["extension_id"]})
    assert sandbox["passed"] is True

    result = await propose_extension_enablement(
        {
            "extension_id": draft["extension_id"],
            "rationale": "This shortcut is stable enough for explicit human review.",
        }
    )

    assert result["proposed"] is True
    artifacts = list_artifacts(session_id="operator-main")
    assert artifacts[0]["artifact_type"] == "extension_enablement_proposal"
    assert artifacts[0]["content"]["extension_id"] == draft["extension_id"]
    assert artifacts[0]["content"]["latest_sandbox_status"] == "passed"

    extension = get_extension(draft["extension_id"])
    assert extension is not None
    assert extension["status"] == "proposed"
