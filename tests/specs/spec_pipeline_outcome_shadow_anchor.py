"""Deterministic shadow feedback anchors across Outcome outbox retries."""

from __future__ import annotations

from tests.specs.spec_meta import SpecClaim, spec_claim


@spec_claim(
    SpecClaim(
        "PL-OF-18",
        "pipeline_outcome_feedback",
        "durable shadow retry anchor",
        "L2c",
        "A partial shadow-feedback failure durably retains every first-attempt exit "
        "price and the Position close time, so replay cannot mix a later market "
        "observation into the same batch.",
        status="implemented",
    )
)
def test_partial_shadow_retry_reuses_first_price_and_close_time(
    tmp_path, monkeypatch,
):
    from tests.specs.spec_pipeline_outcome_feedback import _setup_db

    _setup_db(tmp_path, monkeypatch)
    from aletheia.analytics import shadow_trades
    from aletheia.portfolio import outcome_closure, outcome_hooks
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import ShadowTrade
    from tests.test_outcome_closure import _position, _verified_result

    position = _position(execution_mode="bitget_demo")
    repo.insert_position(position)
    batch_id = "durable-shadow-anchor"
    selected = ShadowTrade(
        id="shadow-anchor-selected",
        batch_id=batch_id,
        opportunity_id=position.opportunity_id,
        symbol=position.symbol,
        market_type=position.market_type,
        direction=position.direction,
        entry_price=position.entry_price,
        was_selected=True,
    )
    counterfactual = ShadowTrade(
        id="shadow-anchor-counterfactual",
        batch_id=batch_id,
        opportunity_id="opportunity-anchor-counterfactual",
        symbol="ETH/USDT",
        market_type="usdt_futures",
        direction="long",
        entry_price=200.0,
        was_selected=False,
    )
    repo.insert_shadow_trade(selected)
    repo.insert_shadow_trade(counterfactual)

    # Commit the close + outbox intent without delivering it immediately.
    monkeypatch.setattr(outcome_closure, "_run_outcome_hooks", lambda *_args: None)
    outcome_closure.close_position_verified(
        position.id,
        _verified_result(position),
        reason="take_profit",
    )
    closed_position = repo._select_one("positions", position.id)
    assert closed_position is not None
    close_time = closed_position["closed_at"]

    first_prices = {counterfactual.symbol: 210.0}
    first_price_requests = []

    def _first_price(symbol, *_args, **kwargs):
        first_price_requests.append((symbol, kwargs))
        return first_prices[symbol]

    monkeypatch.setattr(
        shadow_trades,
        "_current_price",
        _first_price,
    )
    original_resolve = repo.resolve_shadow_trade_if_pending
    failed_once = False

    def _fail_counterfactual_once(shadow_id, **kwargs):
        nonlocal failed_once
        if shadow_id == counterfactual.id and not failed_once:
            failed_once = True
            raise RuntimeError("forced row persistence failure")
        return original_resolve(shadow_id, **kwargs)

    monkeypatch.setattr(
        repo,
        "resolve_shadow_trade_if_pending",
        _fail_counterfactual_once,
    )

    first = outcome_hooks.process_outcome_hook_jobs(position_id=position.id)

    [failed_job] = repo.list_outcome_hook_jobs(position_id=position.id)
    first_rows = {
        row["id"]: row for row in repo.list_shadow_trades(batch_id=batch_id)
    }
    assert first["failed"] == 1
    assert failed_job["status"] == "failed"
    assert first_rows[counterfactual.id]["resolved_at"] is None
    assert first_rows[counterfactual.id]["exit_price"] == 210.0
    assert {symbol for symbol, _kwargs in first_price_requests} == {
        counterfactual.symbol,
    }
    assert all(
        kwargs["at_or_before"] == close_time
        for _symbol, kwargs in first_price_requests
    )

    # A later market observation must never replace the first event anchor.
    later_price_calls = []

    def _later_price(symbol, *_args, **_kwargs):
        later_price_calls.append(symbol)
        return 999.0

    monkeypatch.setattr(shadow_trades, "_current_price", _later_price)
    monkeypatch.setattr(
        repo,
        "resolve_shadow_trade_if_pending",
        original_resolve,
    )
    replayed = outcome_hooks.process_outcome_hook_jobs(
        position_id=position.id,
        now="2100-01-01T00:00:00+00:00",
    )

    [completed_job] = repo.list_outcome_hook_jobs(position_id=position.id)
    final_rows = {
        row["id"]: row for row in repo.list_shadow_trades(batch_id=batch_id)
    }
    assert replayed["completed"] == 1
    assert completed_job["status"] == "completed"
    assert completed_job["attempts"] == 2
    assert later_price_calls == []
    # The selected instrument uses the Position's verified fill average (111),
    # not a market snapshot. Only cross-instrument counterfactuals need a
    # close-time market observation.
    assert final_rows[selected.id]["exit_price"] == 111.0
    assert final_rows[counterfactual.id]["exit_price"] == 210.0
    assert final_rows[selected.id]["resolved_at"] == close_time
    assert final_rows[counterfactual.id]["resolved_at"] == close_time

    frozen = {
        row_id: (row["exit_price"], row["pnl_pct"], row["resolved_at"])
        for row_id, row in final_rows.items()
    }
    no_op = outcome_hooks.process_outcome_hook_jobs(
        position_id=position.id,
        now="2100-01-02T00:00:00+00:00",
    )
    after_no_op = {
        row["id"]: (row["exit_price"], row["pnl_pct"], row["resolved_at"])
        for row in repo.list_shadow_trades(batch_id=batch_id)
    }
    assert no_op["claimed"] == 0
    assert after_no_op == frozen


def test_event_price_lookup_ignores_snapshots_after_position_close(
    tmp_path, monkeypatch,
):
    from tests.specs.spec_pipeline_outcome_feedback import _setup_db

    _setup_db(tmp_path, monkeypatch)
    from aletheia.analytics.shadow_trades import _current_price
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import MarketSnapshot

    repo.insert_market_snapshot(MarketSnapshot(
        id="snapshot-before-close",
        symbol="ETH/USDT",
        market_type="perp",
        price=210.0,
        snapshot_at="2026-07-13T10:00:00+00:00",
    ))
    repo.insert_market_snapshot(MarketSnapshot(
        id="snapshot-after-close",
        symbol="ETH/USDT",
        market_type="perp",
        price=999.0,
        snapshot_at="2026-07-13T10:02:00+00:00",
    ))

    assert _current_price(
        "ETH/USDT",
        market_type="usdt_futures",
        at_or_before="2026-07-13T10:01:00+00:00",
    ) == 210.0


def test_event_price_lookup_rejects_snapshot_older_than_freshness_window(
    tmp_path, monkeypatch,
):
    from tests.specs.spec_pipeline_outcome_feedback import _setup_db

    _setup_db(tmp_path, monkeypatch)
    from aletheia.analytics.shadow_trades import _current_price
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import MarketSnapshot

    repo.insert_market_snapshot(MarketSnapshot(
        id="snapshot-too-old-for-close",
        symbol="ETH/USDT",
        market_type="perp",
        price=210.0,
        snapshot_at="2026-07-13T08:00:00+00:00",
    ))
    repo.insert_market_snapshot(MarketSnapshot(
        id="snapshot-fresh-but-wrong-market",
        symbol="ETH/USDT",
        market_type="spot",
        price=211.0,
        snapshot_at="2026-07-13T10:00:00+00:00",
    ))

    assert _current_price(
        "ETH/USDT",
        market_type="usdt_futures",
        at_or_before="2026-07-13T10:01:00+00:00",
    ) is None


def test_fixed_horizon_resolver_does_not_overwrite_outbox_price_anchor(
    tmp_path, monkeypatch,
):
    from datetime import datetime, timezone

    from tests.specs.spec_pipeline_outcome_feedback import _setup_db

    _setup_db(tmp_path, monkeypatch)

    from aletheia.analytics.shadow_trades import resolve_stale_shadows
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import ShadowTrade
    from aletheia.storage.sqlite import get_db

    created_at = "2026-06-01T00:00:00+00:00"
    shadow = ShadowTrade(
        id="shadow-outbox-anchor-reserved",
        batch_id="shadow-outbox-anchor-batch",
        opportunity_id="opportunity-outbox-anchor",
        symbol="ETH/USDT",
        market_type="usdt_futures",
        direction="long",
        entry_price=200.0,
        exit_price=210.0,
        created_at=created_at,
    )
    repo.insert_shadow_trade(shadow)
    target_ms = int(
        (
            datetime.fromisoformat(created_at).astimezone(timezone.utc).timestamp()
            + 24 * 3600
        )
        * 1000
    )
    get_db().execute(
        "INSERT INTO market_ohlcv "
        "(symbol, timeframe, ts, open, high, low, close, volume, provider) "
        "VALUES (?, '1h', ?, 999, 999, 999, 999, 1, 'spec')",
        (shadow.symbol, target_ms),
    )
    get_db().commit()

    counts = resolve_stale_shadows(min_age_hours=1, horizon_hours=24)

    stored = repo._select_one("shadow_trades", shadow.id)
    assert counts["scanned"] == 0
    assert stored["exit_price"] == 210.0
    assert stored["resolved_at"] is None

    # Existing read surfaces continue to classify this as pending: an anchor is
    # not a completed Outcome until pnl_pct and resolved_at are committed.
    from aletheia.analytics.opportunity_flow import _counterfactual

    view = _counterfactual(
        get_db(), {"id": shadow.opportunity_id}, shadow.direction
    )["shadow"]
    assert view["status"] == "pending"
    assert view["pnl_pct"] is None
    assert repo.list_shadow_trades(resolved=True) == []
