"""Regression tests for the local-only paper account ledger."""

import asyncio
import json
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

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


def test_finite_adjustment_that_overflows_balance_never_appends_event(
    tmp_path, monkeypatch,
):
    _setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio.paper_account import adjust_cash, set_cash_balance

    set_cash_balance(sys.float_info.max)
    before = repo.get_db().execute(
        "SELECT COUNT(*) FROM paper_account_events"
    ).fetchone()[0]

    with pytest.raises(ValueError, match="balance must remain finite"):
        adjust_cash(sys.float_info.max)

    after = repo.get_db().execute(
        "SELECT COUNT(*) FROM paper_account_events"
    ).fetchone()[0]
    assert after == before
    assert repo.get_paper_account_cash_balance() == sys.float_info.max


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


def test_portfolio_pairs_fresh_local_marks_with_local_account_equity(
    tmp_path, monkeypatch,
):
    _setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    from aletheia.portfolio.pnl import compute_portfolio

    cfg = load_config()
    cfg.exchange.paper_execution_mode = "paper_internal"
    save_config(cfg)
    local = Position(
        symbol="BTC/USDT", market_type="spot", direction="long",
        quantity=1.0, entry_price=100.0, current_price=100.0,
        unrealized_pnl=0.0, execution_mode="paper_internal",
    )
    repo.insert_position(local)

    marks = {"BTC/USDT": 110.0}
    monkeypatch.setattr(
        "aletheia.data.price_feed.get_fresh_price_for_protective_close",
        lambda symbol, *_args, **_kwargs: marks[symbol],
    )

    first = compute_portfolio()
    assert first["positions_count"] == 1
    assert first["exposure_by_symbol"] == {"BTC/USDT": pytest.approx(110.0)}
    assert first["unrealized_pnl"] == pytest.approx(10.0)
    assert first["account_equity_usdt"] == pytest.approx(210.0)
    assert first["total_value_usdt"] == pytest.approx(210.0)

    marks["BTC/USDT"] = 120.0
    second = compute_portfolio()
    assert second["unrealized_pnl"] == pytest.approx(20.0)
    assert second["account_equity_usdt"] == pytest.approx(220.0)
    assert repo._select_one("positions", local.id)["unrealized_pnl"] == 0.0


def test_local_portfolio_headline_excludes_other_execution_modes(
    tmp_path, monkeypatch,
):
    _setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    from aletheia.portfolio.pnl import compute_portfolio

    cfg = load_config()
    cfg.exchange.paper_execution_mode = "paper_internal"
    save_config(cfg)
    repo.insert_position(Position(
        symbol="BTC/USDT", market_type="spot", direction="long",
        quantity=1.0, entry_price=100.0, current_price=100.0,
        unrealized_pnl=0.0, execution_mode="paper_internal",
    ))
    repo.insert_position(Position(
        symbol="ETH/USDT", market_type="spot", direction="long",
        quantity=1.0, entry_price=1000.0, current_price=1000.0,
        unrealized_pnl=0.0, execution_mode="okx_demo",
    ))
    monkeypatch.setattr(
        "aletheia.data.price_feed.get_fresh_price_for_protective_close",
        lambda symbol, *_args, **_kwargs: {
            "BTC/USDT": 110.0,
            "ETH/USDT": 2000.0,
        }[symbol],
    )

    portfolio = compute_portfolio()
    assert portfolio["positions_count"] == 1
    assert [position["execution_mode"] for position in portfolio["positions"]] == [
        "paper_internal"
    ]
    assert portfolio["exposure_by_symbol"] == {"BTC/USDT": pytest.approx(110.0)}
    assert portfolio["unrealized_pnl"] == pytest.approx(10.0)


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


def test_valid_loss_closes_zero_cash_position_and_keeps_new_entries_unfunded(
    tmp_path, monkeypatch,
):
    _setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio.paper_account import get_paper_account, set_cash_balance
    from aletheia.portfolio.pnl import close_position

    position = Position(
        symbol="BTC/USDT", market_type="spot", direction="long",
        quantity=1.0, entry_price=100.0, current_price=100.0,
        execution_mode="paper_internal", entry_fee=0.0,
    )
    repo.insert_position(position)
    set_cash_balance(0.0, note="simulate an insolvent paper account")

    result = close_position(position.id, exit_price=90.0, reason="protective")

    account = get_paper_account()
    assert result["realized_pnl"] < 0
    assert account["cash_balance_usdt"] == pytest.approx(result["realized_pnl"])
    assert account["equity_usdt"] == pytest.approx(result["realized_pnl"])
    assert account["available_balance_usdt"] == pytest.approx(0.0)
    event = repo.get_db().execute(
        "SELECT action, amount_usdt FROM paper_account_events "
        "WHERE position_id=? AND action='position_closed'",
        (position.id,),
    ).fetchone()
    assert event["amount_usdt"] == pytest.approx(result["realized_pnl"])


def test_invalid_exit_price_is_terminal_not_unsettled_funding(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio.paper_account import (
        INVALID_EXIT_PRICE_EVIDENCE,
        get_paper_account,
    )

    invalid_position = Position(
        symbol="ON/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=47.916202,
        entry_price=0.17956974,
        current_price=90.524715,
        realized_pnl=None,
        execution_mode="paper_internal",
        status="closed",
        entry_fee=0.00516258,
        exit_fee=2.60256032,
        funding_cost=0.0,
        funding_source="local_simulation",
        outcome_eligible=False,
        outcome_exclusion_reason=INVALID_EXIT_PRICE_EVIDENCE,
    )
    repo.insert_position(invalid_position)

    account = get_paper_account()

    assert account["economics_complete"] is True
    assert account["unsettled_closed_positions_count"] == 0
    result = asyncio.run(
        __import__(
            "aletheia.portfolio.paper_account",
            fromlist=["settle_unsettled_paper_closes"],
        ).settle_unsettled_paper_closes()
    )
    assert result == {
        "settled": 0,
        "revived": 0,
        "still_pending": 0,
        "gave_up": 0,
        "audit_pending": 0,
        "errors": [],
    }
    assert repo._select_one(
        "positions", invalid_position.id,
    )["realized_pnl"] is None


def test_invalid_exit_without_cash_settlement_creates_no_reversal(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio.paper_account import (
        INVALID_EXIT_PRICE_EVIDENCE,
        get_paper_account,
        invalidate_paper_position_economics,
    )

    position = Position(
        symbol="ON/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=1.0,
        current_price=1.0,
        realized_pnl=None,
        execution_mode="paper_internal",
        status="closed",
        outcome_eligible=False,
        outcome_exclusion_reason=INVALID_EXIT_PRICE_EVIDENCE,
    )
    repo.insert_position(position)

    first = invalidate_paper_position_economics(position.id)
    replay = invalidate_paper_position_economics(position.id)

    assert first["status"] == "already_invalidated"
    assert replay["status"] == "already_invalidated"
    assert get_paper_account()["cash_balance_usdt"] == pytest.approx(200.0)
    count = repo.get_db().execute(
        "SELECT COUNT(*) FROM paper_account_events "
        "WHERE position_id=? AND action='position_economics_invalidated'",
        (position.id,),
    ).fetchone()[0]
    assert count == 0


def test_invalidation_retracts_completed_derived_outcomes(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    from datetime import datetime, timedelta, timezone
    from aletheia.portfolio.paper_account import (
        invalidate_paper_position_economics,
        settle_position_closed,
    )
    from aletheia.storage.models import (
        SerenityOpportunity,
        SerenityPostMortem,
        ShadowTrade,
    )

    opportunity = SerenityOpportunity(
        id="invalidated-opportunity",
        theme_id="theme",
        symbol="BTC/USDT",
        direction="long",
    )
    repo.insert_serenity_opportunity(opportunity)
    closed_at = datetime.now(timezone.utc)
    position = Position(
        id="invalidated-derived-position",
        opportunity_id=opportunity.id,
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=110.0,
        execution_mode="paper_internal",
        status="closed",
        realized_pnl=10.0,
        outcome_eligible=True,
        closed_at=closed_at.isoformat(),
    )
    repo.insert_position(position)
    settle_position_closed(position.id, 10.0)
    for opportunity_id, selected in ((opportunity.id, True), ("control", False)):
        repo.insert_shadow_trade(ShadowTrade(
            batch_id="invalidated-batch",
            opportunity_id=opportunity_id,
            symbol="BTC/USDT",
            direction="long",
            was_selected=selected,
            exit_price=110.0,
            pnl_pct=10.0,
            settlement_price_source="position_close",
            resolved_at=closed_at.isoformat(),
        ))
    repo.insert_shadow_trade(ShadowTrade(
        batch_id="independent-batch",
        opportunity_id=opportunity.id,
        symbol="BTC/USDT",
        direction="long",
        was_selected=True,
        exit_price=105.0,
        pnl_pct=5.0,
        settlement_price_source="stale_forward_ohlcv",
        resolved_at=(closed_at + timedelta(hours=24)).isoformat(),
    ))
    repo.insert_serenity_post_mortem(SerenityPostMortem(
        position_id=position.id,
        opportunity_id=opportunity.id,
        outcome="win",
        summary="derived from invalid exit evidence",
    ))
    repo.enqueue_outcome_hook_job(position.id, "shadow_trades")

    invalidate_paper_position_economics(position.id)

    raw_shadows = repo.get_db().execute(
        "SELECT invalidated_at,invalidation_reason FROM shadow_trades "
        "WHERE batch_id=?",
        ("invalidated-batch",),
    ).fetchall()
    raw_mortem = repo.get_db().execute(
        "SELECT invalidated_at,invalidation_reason FROM serenity_post_mortems "
        "WHERE position_id=?",
        (position.id,),
    ).fetchone()
    hook = repo.list_outcome_hook_jobs(position_id=position.id)[0]
    assert len(raw_shadows) == 2
    assert all(row["invalidated_at"] for row in raw_shadows)
    assert all(row["invalidation_reason"] == "invalid_exit_price_evidence" for row in raw_shadows)
    assert raw_mortem["invalidated_at"]
    assert raw_mortem["invalidation_reason"] == "invalid_exit_price_evidence"
    assert repo.list_shadow_trades(batch_id="invalidated-batch") == []
    assert len(repo.list_shadow_trades(batch_id="independent-batch")) == 1
    assert repo.list_serenity_post_mortems(opportunity.id) == []
    assert hook["status"] == "skipped"


def test_invalidation_fences_an_already_claimed_outcome_hook(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    from datetime import datetime, timezone
    from aletheia.portfolio import outcome_hooks
    from aletheia.portfolio.paper_account import (
        invalidate_paper_position_economics,
        settle_position_closed,
    )

    position = Position(
        id="claimed-hook-invalidation",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=110.0,
        execution_mode="paper_internal",
        status="closed",
        realized_pnl=10.0,
        outcome_eligible=True,
        closed_at=datetime.now(timezone.utc).isoformat(),
    )
    repo.insert_position(position)
    settle_position_closed(position.id, 10.0)
    repo.enqueue_outcome_hook_job(position.id, "shadow_trades")
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []
    real_load = outcome_hooks._load_eligible_position
    first_load = True

    def load_with_claim_gap(position_id):
        nonlocal first_load
        if first_load:
            first_load = False
            stale = dict(repo._select_one("positions", position_id))
            entered.set()
            assert release.wait(timeout=10)
            return stale, "eligible"
        return real_load(position_id)

    monkeypatch.setattr(outcome_hooks, "_load_eligible_position", load_with_claim_gap)
    monkeypatch.setattr(
        outcome_hooks,
        "_execute_hook",
        lambda _hook_type, row: calls.append(str(row["id"])),
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        worker = executor.submit(
            outcome_hooks.process_outcome_hook_jobs,
            position_id=position.id,
        )
        assert entered.wait(timeout=10)
        invalidate_paper_position_economics(position.id)
        release.set()
        summary = worker.result(timeout=10)

    [job] = repo.list_outcome_hook_jobs(position_id=position.id)
    assert summary["skipped"] == 1
    assert summary["completed"] == 0
    assert calls == []
    assert job["status"] == "skipped"
    assert job["last_error"] == "invalid_exit_price_evidence"


def test_position_ledger_continues_after_correction_makes_cash_negative(
    tmp_path, monkeypatch,
):
    _setup_env(tmp_path, monkeypatch)
    from datetime import datetime, timezone
    from aletheia.portfolio.paper_account import (
        adjust_cash,
        get_paper_account,
        invalidate_paper_position_economics,
        record_position_opened,
        settle_position_closed,
    )

    position = Position(
        id="consumed-false-profit",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=400.0,
        execution_mode="paper_internal",
        status="closed",
        realized_pnl=300.0,
        outcome_eligible=True,
        closed_at=datetime.now(timezone.utc).isoformat(),
    )
    repo.insert_position(position)
    settle_position_closed(position.id, 300.0)
    adjust_cash(-450.0, note="consume false profit before correction")
    invalidate_paper_position_economics(position.id)
    assert get_paper_account()["cash_balance_usdt"] == pytest.approx(-250.0)

    record_position_opened("already-open-position")
    settle_position_closed("later-loss", -10.0)
    settle_position_closed("later-small-profit", 5.0)
    adjust_cash(1.0, note="partial restoration")

    assert get_paper_account()["cash_balance_usdt"] == pytest.approx(-254.0)
    with pytest.raises(ValueError, match="cannot be negative"):
        adjust_cash(-1.0, note="do not deepen deficit manually")


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


def test_account_read_cannot_mix_open_position_with_settled_close(
    tmp_path, monkeypatch
):
    _setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio import paper_account
    from aletheia.portfolio.pnl import close_position
    from aletheia.storage.sqlite import close_db

    position = Position(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=110.0,
        unrealized_pnl=10.0,
        execution_mode="paper_internal",
        entry_fee=0.0,
    )
    repo.insert_position(position)
    paper_account.get_paper_account(event_limit=0)
    captured_open = threading.Event()
    release_reader = threading.Event()
    original_open = paper_account._open_local_positions

    def paused_open_positions():
        rows = original_open()
        captured_open.set()
        assert release_reader.wait(timeout=3)
        return rows

    monkeypatch.setattr(paper_account, "_open_local_positions", paused_open_positions)

    def read_account():
        close_db()
        try:
            return paper_account.get_paper_account(event_limit=0)
        finally:
            close_db()

    def close_account_position():
        close_db()
        try:
            return close_position(position.id, exit_price=110.0, reason="manual")
        finally:
            close_db()

    with ThreadPoolExecutor(max_workers=2) as pool:
        read_future = pool.submit(read_account)
        assert captured_open.wait(timeout=3)
        close_future = pool.submit(close_account_position)
        try:
            close_future.result(timeout=3)
        finally:
            release_reader.set()
        account = read_future.result(timeout=3)

    assert (
        account["cash_balance_usdt"],
        account["open_positions_count"],
        account["reserved_margin_usdt"],
        account["equity_usdt"],
    ) == pytest.approx((200.0, 1, 100.0, 210.0))


def test_account_read_cannot_omit_new_position_but_include_its_event(
    tmp_path, monkeypatch
):
    _setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio import paper_account
    from aletheia.storage.sqlite import close_db

    paper_account.get_paper_account()
    position = Position(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="paper_internal",
    )
    captured_open = threading.Event()
    release_reader = threading.Event()
    original_open = paper_account._open_local_positions

    def paused_open_positions():
        rows = original_open()
        captured_open.set()
        assert release_reader.wait(timeout=3)
        return rows

    monkeypatch.setattr(paper_account, "_open_local_positions", paused_open_positions)

    def read_account():
        close_db()
        try:
            return paper_account.get_paper_account()
        finally:
            close_db()

    def open_account_position():
        close_db()
        try:
            with repo.transaction(immediate=True):
                repo.insert_position(position)
                paper_account.record_position_opened(position.id)
        finally:
            close_db()

    with ThreadPoolExecutor(max_workers=2) as pool:
        read_future = pool.submit(read_account)
        assert captured_open.wait(timeout=3)
        open_future = pool.submit(open_account_position)
        try:
            open_future.result(timeout=3)
        finally:
            release_reader.set()
        account = read_future.result(timeout=3)

    assert account["open_positions_count"] == 0
    assert account["reserved_margin_usdt"] == 0
    assert not any(event["position_id"] == position.id for event in account["events"])


def test_portfolio_read_cannot_count_one_close_as_open_and_realized(
    tmp_path, monkeypatch
):
    _setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio import pnl
    from aletheia.portfolio.paper_account import get_paper_account
    from aletheia.storage.sqlite import close_db

    position = Position(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=110.0,
        unrealized_pnl=10.0,
        execution_mode="paper_internal",
        entry_fee=0.0,
    )
    repo.insert_position(position)
    get_paper_account(event_limit=0)
    monkeypatch.setattr(
        "aletheia.data.price_feed.get_fresh_price_for_protective_close",
        lambda *_args, **_kwargs: 110.0,
    )
    captured_open = threading.Event()
    release_reader = threading.Event()
    original_mark = pnl.mark_positions_to_market

    def paused_mark_positions(*, persist=False):
        rows = original_mark(persist=persist)
        captured_open.set()
        assert release_reader.wait(timeout=3)
        return rows

    monkeypatch.setattr(pnl, "mark_positions_to_market", paused_mark_positions)

    def read_portfolio():
        close_db()
        try:
            return pnl.compute_portfolio(execution_mode="paper_internal")
        finally:
            close_db()

    def close_account_position():
        close_db()
        try:
            return pnl.close_position(position.id, exit_price=110.0, reason="manual")
        finally:
            close_db()

    with ThreadPoolExecutor(max_workers=2) as pool:
        read_future = pool.submit(read_portfolio)
        assert captured_open.wait(timeout=3)
        close_future = pool.submit(close_account_position)
        try:
            close_future.result(timeout=3)
        finally:
            release_reader.set()
        portfolio = read_future.result(timeout=3)

    assert portfolio["positions_count"] == 1
    assert portfolio["today_realized_pnl"] == 0
    assert portfolio["today_pnl_by_symbol"] == {}
    assert portfolio["total_value_usdt"] == pytest.approx(210.0)
