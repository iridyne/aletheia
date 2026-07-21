"""Regression tests for the local-only paper account ledger."""

import sqlite3
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from httpx import ASGITransport, AsyncClient

from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import Position
from aletheia.storage.sqlite import reset_db
from aletheia.storage import repositories as repo


def _setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


def test_first_local_account_read_seeds_once_and_manual_zero_is_preserved(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio.paper_account import get_paper_account, set_cash_balance

    initial = get_paper_account()
    assert initial["cash_balance_usdt"] == pytest.approx(200.0)
    assert initial["available_balance_usdt"] == pytest.approx(200.0)
    assert len(initial["events"]) == 1
    assert initial["events"][0]["action"] == "initial_funding"

    set_cash_balance(0.0, note="operator reset")
    after_reset = get_paper_account()
    assert after_reset["cash_balance_usdt"] == pytest.approx(0.0)
    assert [event["action"] for event in after_reset["events"]] == [
        "manual_set_balance", "initial_funding"
    ]


@pytest.mark.parametrize(
    "amount",
    [float("nan"), float("inf"), float("-inf")],
    ids=["nan", "positive-infinity", "negative-infinity"],
)
def test_non_finite_values_never_append_paper_cash_events(
    tmp_path, monkeypatch, amount,
):
    _setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio.paper_account import (
        adjust_cash,
        get_paper_account,
        set_cash_balance,
        settle_position_closed,
    )

    get_paper_account()
    before = repo.get_db().execute(
        "SELECT COUNT(*) FROM paper_account_events"
    ).fetchone()[0]

    for write in (
        lambda: adjust_cash(amount),
        lambda: set_cash_balance(amount),
        lambda: settle_position_closed("non-finite-close", amount),
    ):
        with pytest.raises(ValueError, match="finite"):
            write()

    after = repo.get_db().execute(
        "SELECT COUNT(*) FROM paper_account_events"
    ).fetchone()[0]
    assert after == before == 1


def test_available_balance_reserves_open_position_collateral(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio.paper_account import get_paper_account

    repo.insert_position(Position(
        symbol="BTC/USDT", market_type="spot", direction="long",
        quantity=1.0, entry_price=50.0, current_price=50.0,
        execution_mode="paper_internal",
    ))
    account = get_paper_account()
    assert account["cash_balance_usdt"] == pytest.approx(200.0)
    assert account["reserved_margin_usdt"] == pytest.approx(50.0)
    assert account["available_balance_usdt"] == pytest.approx(150.0)


def test_local_close_settles_realized_pnl_once(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio.paper_account import get_paper_account
    from aletheia.portfolio.pnl import close_position

    position = Position(
        symbol="BTC/USDT", market_type="spot", direction="long",
        quantity=1.0, entry_price=100.0, current_price=100.0,
        execution_mode="paper_internal", entry_fee=0.0,
    )
    repo.insert_position(position)
    get_paper_account()  # initialize before the close path settles PnL

    result = close_position(position.id, exit_price=110.0, reason="manual")
    settled = get_paper_account()
    assert settled["reserved_margin_usdt"] == pytest.approx(0.0)
    assert settled["cash_balance_usdt"] == pytest.approx(200.0 + result["realized_pnl"])

    with pytest.raises(ValueError, match="not open"):
        close_position(position.id, exit_price=110.0, reason="manual")
    assert get_paper_account()["cash_balance_usdt"] == pytest.approx(settled["cash_balance_usdt"])


def test_local_paper_available_balance_tightens_risk_without_affecting_external(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    from aletheia.portfolio.paper_account import set_cash_balance
    from aletheia.risk.engine import _cached_available_balance_usdt

    cfg = load_config()
    cfg.exchange.paper_execution_mode = "paper_internal"
    save_config(cfg)
    set_cash_balance(25.0, note="small account")

    assert _cached_available_balance_usdt("paper_internal") == pytest.approx(25.0)
    # External modes still use their own account-cache logic, not local paper cash.
    assert _cached_available_balance_usdt("bitget_demo") is None


def test_agent_context_explains_human_local_cash_adjustments(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    from aletheia.portfolio.paper_account import adjust_cash
    from aletheia.agent_shell.trading_context import _build_layer1_snapshot

    cfg = load_config()
    cfg.exchange.paper_execution_mode = "paper_internal"
    save_config(cfg)
    adjust_cash(15.0, note="operator top-up")

    layer = _build_layer1_snapshot()
    assert layer["balance"]["source"] == "local_paper_ledger"
    assert layer["balance"]["available_usdt"] == pytest.approx(215.0)
    assert layer["recent_account_changes"] == [{
        "action": "manual_credit", "amount_usdt": 15.0,
        "note": "operator top-up", "at": layer["recent_account_changes"][0]["at"],
    }]


def test_account_write_does_not_invert_lock_order_with_risk_transaction(
    tmp_path, monkeypatch
):
    """A DB-owning Risk read and a manual adjustment must both finish promptly."""
    _setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio import paper_account
    from aletheia.storage.sqlite import close_db, get_db

    paper_account.get_paper_account()
    get_db().commit()
    close_db()

    # Reproduce the historical order exactly: the manual path owns the account
    # lock before it needs SQLite, while Risk already owns SQLite before reading
    # the local account. RLock allows the same manual thread to enter the public
    # function; the production fix must not wait on this process lock at all.
    account_lock = threading.RLock()
    monkeypatch.setattr(
        paper_account, "_account_lock", account_lock, raising=False
    )
    manual_db_ready = threading.Event()
    risk_db_ready = threading.Event()
    account_lock_held = threading.Event()
    risk_db_held = threading.Event()

    def manual_adjustment() -> float:
        close_db()
        db = get_db()
        db.execute("PRAGMA busy_timeout=250")
        manual_db_ready.set()
        assert risk_db_ready.wait(timeout=2)
        try:
            with account_lock:
                account_lock_held.set()
                assert risk_db_held.wait(timeout=2)
                return paper_account.adjust_cash(
                    1.0, note="concurrent lock-order regression"
                )["cash_balance_usdt"]
        finally:
            close_db()

    def risk_account_read() -> float:
        assert manual_db_ready.wait(timeout=2)
        close_db()
        db = get_db()
        db.execute("PRAGMA busy_timeout=250")
        risk_db_ready.set()
        try:
            assert account_lock_held.wait(timeout=2)
            with repo.transaction(immediate=True):
                risk_db_held.set()
                return paper_account.get_paper_account(event_limit=0)[
                    "cash_balance_usdt"
                ]
        finally:
            close_db()

    with ThreadPoolExecutor(max_workers=2) as pool:
        manual_future = pool.submit(manual_adjustment)
        risk_future = pool.submit(risk_account_read)
        assert risk_future.result(timeout=3) == pytest.approx(200.0)
        assert manual_future.result(timeout=3) == pytest.approx(201.0)

    assert paper_account.get_paper_account(event_limit=0)[
        "cash_balance_usdt"
    ] == pytest.approx(201.0)


@pytest.mark.anyio
async def test_local_account_routes_are_human_audited_and_reject_external_mode(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    from aletheia.server.app import app

    cfg = load_config()
    cfg.exchange.paper_execution_mode = "paper_internal"
    save_config(cfg)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        current = await client.get("/api/settings/paper-account")
        assert current.status_code == 200
        assert current.json()["available_balance_usdt"] == pytest.approx(200.0)

        credited = await client.post("/api/settings/paper-account/credit", json={
            "amount_usdt": 25.0, "note": "operator top-up",
        })
        assert credited.status_code == 200
        assert credited.json()["cash_balance_usdt"] == pytest.approx(225.0)

    entries = [json.loads(line) for line in (tmp_path / "logs" / "audit.jsonl").read_text().splitlines()]
    audit = next(entry for entry in entries if entry.get("event_type") == "paper_account_credit")
    assert audit["actor"] == "human"
    assert audit["after"]["cash_balance_usdt"] == pytest.approx(225.0)

    cfg.exchange.paper_execution_mode = "bitget_demo"
    save_config(cfg)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        blocked = await client.get("/api/settings/paper-account")
    assert blocked.status_code == 409


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "field", "token"),
    [
        ("/api/settings/paper-account/credit", "amount_usdt", "NaN"),
        ("/api/settings/paper-account/credit", "amount_usdt", "Infinity"),
        ("/api/settings/paper-account/credit", "amount_usdt", "-Infinity"),
        (
            "/api/settings/paper-account/set-balance",
            "cash_balance_usdt",
            "NaN",
        ),
        (
            "/api/settings/paper-account/set-balance",
            "cash_balance_usdt",
            "Infinity",
        ),
        (
            "/api/settings/paper-account/set-balance",
            "cash_balance_usdt",
            "-Infinity",
        ),
    ],
)
async def test_local_account_routes_reject_non_finite_json_numbers(
    tmp_path, monkeypatch, path, field, token,
):
    _setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    from aletheia.server.app import app

    cfg = load_config()
    cfg.exchange.paper_execution_mode = "paper_internal"
    save_config(cfg)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            path,
            content=f'{{"{field}": {token}}}',
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 400
    assert repo.get_paper_account_cash_balance() == pytest.approx(0.0)
    assert repo.has_paper_account_event() is False
