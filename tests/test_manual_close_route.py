"""Tests for POST /api/positions/{id}/close — human manual close endpoint.

Covers:
- Market close dispatches through proactive_exit with actor='human'
- Close reason is 'human_manual_close', not 'operator_close'
- Live positions return 409 live_position_requires_exchange_close without calling proactive_exit
- Limit order sets exit_limit_price + exit_limit_source='human' on the position
- Non-existent/already-closed position returns 404
- Invalid order_type returns 400
- Auth guard: POST /api/positions/ is in _PROTECTED_METHOD_PREFIXES
"""

import sqlite3

import pytest
from httpx import ASGITransport, AsyncClient

from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import Position
from aletheia.storage.sqlite import reset_db
from aletheia.storage import repositories as repo


def _setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.files.jsonl as jsonl
    import aletheia.storage.sqlite as sq

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    jsonl._last_audit_hash = None
    run_migrations()


def _insert_open(execution_mode: str = "paper_internal") -> str:
    pos = Position(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=105.0,
        execution_mode=execution_mode,
    )
    repo.insert_position(pos)
    return pos.id


# ---------------------------------------------------------------------------
# _decide_exit: human_limit_exit vs operator_limit_exit based on exit_limit_source
# ---------------------------------------------------------------------------

def test_decide_exit_human_limit_exit():
    import aletheia.portfolio.position_manager as pm
    from types import SimpleNamespace

    cfg = SimpleNamespace(
        trading=SimpleNamespace(
            default_stop_loss_pct=2.0, default_take_profit_pct=4.0, max_position_hold_hours=72.0,
        ),
        serenity=SimpleNamespace(max_hold_days=90),
        risk=SimpleNamespace(horizon_hard_backstop_mult=3.0),
    )
    pos = {"id": "p1", "symbol": "BTC/USDT", "direction": "long", "entry_price": 100.0,
           "exit_limit_price": 110.0, "exit_limit_source": "human", "take_profit_price": 200.0}
    # Not yet crossed: no decision
    assert pm._decide_exit(pos, 105.0, cfg) is None
    # Crossed: human_limit_exit
    assert pm._decide_exit(pos, 111.0, cfg) == ("human_limit_exit", 110.0)


def test_decide_exit_operator_limit_exit_for_system_operator():
    import aletheia.portfolio.position_manager as pm
    from types import SimpleNamespace

    cfg = SimpleNamespace(
        trading=SimpleNamespace(
            default_stop_loss_pct=2.0, default_take_profit_pct=4.0, max_position_hold_hours=72.0,
        ),
        serenity=SimpleNamespace(max_hold_days=90),
        risk=SimpleNamespace(horizon_hard_backstop_mult=3.0),
    )
    pos = {"id": "p1", "symbol": "BTC/USDT", "direction": "long", "entry_price": 100.0,
           "exit_limit_price": 110.0, "exit_limit_source": "system_operator", "take_profit_price": 200.0}
    assert pm._decide_exit(pos, 111.0, cfg) == ("operator_limit_exit", 110.0)


def test_decide_exit_legacy_null_source_defaults_to_operator():
    """Backward compat: pre-existing positions with no exit_limit_source → operator_limit_exit."""
    import aletheia.portfolio.position_manager as pm
    from types import SimpleNamespace

    cfg = SimpleNamespace(
        trading=SimpleNamespace(
            default_stop_loss_pct=2.0, default_take_profit_pct=4.0, max_position_hold_hours=72.0,
        ),
        serenity=SimpleNamespace(max_hold_days=90),
        risk=SimpleNamespace(horizon_hard_backstop_mult=3.0),
    )
    pos = {"id": "p1", "symbol": "BTC/USDT", "direction": "long", "entry_price": 100.0,
           "exit_limit_price": 110.0, "take_profit_price": 200.0}
    assert pm._decide_exit(pos, 111.0, cfg) == ("operator_limit_exit", 110.0)


# ---------------------------------------------------------------------------
# proactive_exit: actor='human' market close
# ---------------------------------------------------------------------------

def test_proactive_exit_human_market_sets_close_reason(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)

    import aletheia.portfolio.position_manager as pm

    pid = _insert_open()
    calls = []
    monkeypatch.setattr(pm, "close_position_routed",
                        lambda *a, **kw: calls.append(kw) or {"closed": True})
    monkeypatch.setattr(pm, "_latest_price", lambda s: 105.0)

    result = pm.proactive_exit(pid, actor="human")
    assert result["close_reason"] == "human_manual_close"
    assert calls[0]["reason"] == "human_manual_close"
    assert calls[0]["decision_actor"] == "human"
    assert calls[0]["decision_source"] == "dashboard_manual_close"


def test_proactive_exit_system_operator_keeps_original_reason(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)

    import aletheia.portfolio.position_manager as pm

    pid = _insert_open()
    calls = []
    monkeypatch.setattr(pm, "close_position_routed",
                        lambda *a, **kw: calls.append(kw) or {"closed": True})
    monkeypatch.setattr(pm, "_latest_price", lambda s: 105.0)

    result = pm.proactive_exit(pid, actor="system_operator")
    assert result["close_reason"] == "operator_close"
    assert calls[0]["reason"] == "operator_close"
    assert calls[0]["decision_actor"] == "system_operator"


def test_proactive_exit_human_limit_sets_exit_limit_source(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)

    import aletheia.portfolio.position_manager as pm

    pid = _insert_open()
    result = pm.proactive_exit(pid, order_type="limit", limit_price=110.0, actor="human")
    assert result["pending_limit_exit"] is True
    assert result["exit_limit_source"] == "human"
    row = repo._select_one("positions", pid)
    assert row["exit_limit_price"] == pytest.approx(110.0)
    assert row["exit_limit_source"] == "human"


def test_proactive_exit_unsupported_actor_rejected(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)

    import aletheia.portfolio.position_manager as pm

    pid = _insert_open()
    result = pm.proactive_exit(pid, actor="rogue_agent")
    assert result.get("error") == "unsupported_exit_actor"


def test_proactive_exit_missing_position_returns_error(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)

    import aletheia.portfolio.position_manager as pm

    result = pm.proactive_exit("nonexistent-id", actor="human")
    assert result.get("error") == "position_not_found_or_not_open"


# ---------------------------------------------------------------------------
# POST /api/positions/{id}/close — HTTP integration
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_manual_close_route_market_dispatches_human_close(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)

    from aletheia.server.app import app
    import aletheia.portfolio.position_manager as pm

    pid = _insert_open()
    calls = []
    monkeypatch.setattr(pm, "close_position_routed",
                        lambda *a, **kw: calls.append(kw) or {"closed": True})
    monkeypatch.setattr(pm, "_latest_price", lambda s: 105.0)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/api/positions/{pid}/close", json={"order_type": "market"})

    assert resp.status_code == 200
    data = resp.json()
    assert data.get("closed") is True
    assert calls[0]["decision_actor"] == "human"
    assert calls[0]["reason"] == "human_manual_close"


@pytest.mark.anyio
async def test_manual_close_route_live_returns_409(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)

    from aletheia.server.app import app
    import aletheia.portfolio.position_manager as pm

    pid = _insert_open(execution_mode="bitget_live_limited")
    calls = []
    monkeypatch.setattr(pm, "close_position_routed",
                        lambda *a, **kw: calls.append(kw) or {})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/api/positions/{pid}/close", json={"order_type": "market"})

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "live_position_requires_exchange_close"
    # proactive_exit must NOT be called for live positions
    assert calls == []


@pytest.mark.anyio
async def test_manual_close_route_not_found_returns_404(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)

    from aletheia.server.app import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/positions/nonexistent/close", json={"order_type": "market"})

    assert resp.status_code == 404


@pytest.mark.anyio
async def test_manual_close_route_invalid_order_type_returns_400(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)

    from aletheia.server.app import app

    pid = _insert_open()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/api/positions/{pid}/close",
                                 json={"order_type": "vwap_magic"})

    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_order_type"


@pytest.mark.anyio
async def test_manual_close_route_limit_sets_exit_limit_source_human(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)

    from aletheia.server.app import app

    pid = _insert_open()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/api/positions/{pid}/close",
                                 json={"order_type": "limit", "limit_price": 110.0})

    assert resp.status_code == 200
    data = resp.json()
    assert data.get("pending_limit_exit") is True
    row = repo._select_one("positions", pid)
    assert row["exit_limit_price"] == pytest.approx(110.0)
    assert row["exit_limit_source"] == "human"


@pytest.mark.anyio
async def test_manual_close_route_limit_requires_price(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)

    from aletheia.server.app import app

    pid = _insert_open()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/api/positions/{pid}/close",
                                 json={"order_type": "limit", "limit_price": None})

    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Auth guard: _PROTECTED_METHOD_PREFIXES includes POST /api/positions/
# ---------------------------------------------------------------------------

def test_protected_method_prefixes_includes_positions_post():
    """POST /api/positions/ must be in _PROTECTED_METHOD_PREFIXES.

    This mirrors API-S11 for the close-all/close-spot routes: an unauthenticated
    tunnel caller must not be able to force-close positions.
    """
    import ast
    import pathlib

    src = pathlib.Path("aletheia/server/app.py").read_text()
    tree = ast.parse(src)
    prefixes: list[str] = []
    for node in ast.walk(tree):
        # _PROTECTED_METHOD_PREFIXES uses an annotated assignment, not plain Assign.
        tgt_name = None
        val_node = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            tgt_name, val_node = node.target.id, node.value
        elif isinstance(node, ast.Assign):
            try:
                tgt_name, val_node = node.targets[0].id, node.value  # type: ignore[union-attr]
            except (AttributeError, IndexError):
                pass
        if tgt_name != "_PROTECTED_METHOD_PREFIXES" or val_node is None:
            continue
        for elt in ast.walk(val_node):
            if isinstance(elt, ast.Tuple) and len(elt.elts) == 2:
                _methods_node, path_node = elt.elts
                if isinstance(path_node, ast.Constant) and isinstance(path_node.value, str):
                    prefixes.append(path_node.value)
    assert "/api/positions/" in prefixes, (
        "/api/positions/ must be in _PROTECTED_METHOD_PREFIXES "
        "so POST /api/positions/{id}/close requires tunnel auth when tunnel is enabled."
    )


# ---------------------------------------------------------------------------
# exit_driver attribution: human_* not miscounted as AI
# ---------------------------------------------------------------------------

def test_exit_driver_human_reasons_classified_correctly():
    from aletheia.analytics.exit_driver_comparison import exit_driver, HUMAN_DRIVEN

    assert "human_manual_close" in HUMAN_DRIVEN
    assert "human_limit_exit" in HUMAN_DRIVEN
    assert exit_driver("human_manual_close") == "human"
    assert exit_driver("human_limit_exit") == "human"
    # Ensure they are NOT in the operator bucket
    assert exit_driver("human_manual_close") != "operator"
    assert exit_driver("human_limit_exit") != "operator"


def test_exit_driver_human_does_not_inflate_operator_stats():
    """A profitable human close must not be counted as AI exit discretion."""
    from aletheia.analytics.exit_driver_comparison import compare_exit_outcomes

    rows = [
        {"close_reason": "human_manual_close", "realized_pnl": 100.0},
        {"close_reason": "operator_close",     "realized_pnl": -5.0},
        {"close_reason": "stop_loss",           "realized_pnl": -3.0},
    ]
    stats = compare_exit_outcomes(rows)
    # human is its own bucket
    assert "human" in stats
    assert stats["human"].count == 1
    assert stats["human"].total_pnl == pytest.approx(100.0)
    # operator bucket does NOT include the human close
    assert "operator" in stats
    assert stats["operator"].count == 1
    assert stats["operator"].total_pnl == pytest.approx(-5.0)
