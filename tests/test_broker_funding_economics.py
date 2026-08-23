from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aletheia.execution.broker_funding import (
    compute_broker_funding,
    make_complete_ledger,
    normalize_binance_funding_rows,
    normalize_bitget_funding_rows,
    normalize_okx_funding_rows,
)
from aletheia.storage import repositories as repo
from aletheia.storage.models import Position
from tests.test_demo_trading import (
    _demo_config,
    _fresh_snapshot,
    _patch_place_order,
    setup_env,
)


@pytest.mark.parametrize(
    ("normalizer", "row", "event_id"),
    [
        (
            normalize_bitget_funding_rows,
            {
                "billId": "bg-1", "symbol": "BTCUSDT", "amount": "-2.5",
                "businessType": "contract_settle_fee", "coin": "USDT",
                "cTime": "1784044800000",
            },
            "bg-1",
        ),
        (
            normalize_okx_funding_rows,
            {
                "billId": "okx-1", "instId": "BTC-USDT-SWAP", "pnl": "-2.5",
                "type": "8", "subType": "173", "ccy": "USDT",
                "ts": "1784044800000", "posSide": "long",
            },
            "okx-1",
        ),
        (
            normalize_binance_funding_rows,
            {
                "tranId": "bn-1", "symbol": "BTCUSDT", "income": "-2.5",
                "incomeType": "FUNDING_FEE", "asset": "USDT",
                "time": "1784044800000",
            },
            "bn-1",
        ),
    ],
)
def test_private_funding_normalizers_preserve_identity_and_cost_sign(
    normalizer, row, event_id,
):
    events = normalizer([row, dict(row)])
    assert len(events) == 1
    assert events[0]["event_id"] == event_id
    assert events[0]["symbol"] == "BTC/USDT"
    assert events[0]["cash_change"] == -2.5
    assert events[0]["funding_cost"] == 2.5


@pytest.mark.parametrize(
    ("mode", "environment"),
    [
        ("bitget_demo", "demo"),
        ("bitget_live_limited", "live"),
        ("okx_demo", "demo"),
        ("okx_live_limited", "live"),
        ("binance_testnet", "testnet"),
        ("binance_live_limited", "live"),
    ],
)
def test_complete_empty_private_ledger_proves_zero_funding(mode, environment):
    position = {
        "id": f"p-{mode}",
        "symbol": "BTC/USDT",
        "market_type": "usdt_futures",
        "direction": "long",
        "execution_mode": mode,
        "opened_at": "2026-07-14T00:00:00+00:00",
    }
    ledger = make_complete_ledger(
        broker_id=mode,
        account_environment=environment,
        source_endpoint="/private/funding",
        history_start_ms=1783987200000,
        history_end_ms=1784073600000,
        events=[],
    )
    result = compute_broker_funding(
        position=position,
        closed_at="2026-07-14T12:00:00+00:00",
        ledger=ledger,
    )
    assert result.complete is True
    assert result.funding_cost == 0.0
    assert result.funding_source == "broker_ledger"
    assert result.evidence["events"] == []


def test_private_history_retention_cannot_fabricate_complete_zero_funding():
    from aletheia.execution.broker_funding import (
        BrokerFundingIntegrityError,
        require_retained_history,
    )

    now_ms = 1_800_000_000_000
    ninety_one_days_ms = 91 * 24 * 60 * 60 * 1000
    with pytest.raises(BrokerFundingIntegrityError, match="cannot prove"):
        require_retained_history(
            now_ms - ninety_one_days_ms,
            retention_days=90,
            source="private ledger",
            now_ms=now_ms,
        )

    require_retained_history(
        now_ms - 89 * 24 * 60 * 60 * 1000,
        retention_days=90,
        source="private ledger",
        now_ms=now_ms,
    )


def test_private_history_retention_exposes_typed_terminal_evidence():
    from aletheia.execution.broker_funding import (
        BrokerFundingHistoryExpired,
        BrokerFundingIntegrityError,
        require_retained_history,
    )

    now_ms = 1_800_000_000_000
    start_ms = now_ms - 91 * 24 * 60 * 60 * 1000
    with pytest.raises(BrokerFundingHistoryExpired) as caught:
        require_retained_history(
            start_ms,
            retention_days=90,
            source="fixture private ledger",
            now_ms=now_ms,
        )

    assert caught.value.source == "fixture private ledger"
    assert caught.value.retention_days == 90
    assert caught.value.history_start_ms == start_ms
    assert caught.value.oldest_supported_ms == (
        now_ms - 90 * 24 * 60 * 60 * 1000
    )
    require_retained_history(
        caught.value.oldest_supported_ms,
        retention_days=90,
        source="fixture private ledger",
        now_ms=now_ms,
    )
    with pytest.raises(BrokerFundingIntegrityError) as malformed:
        require_retained_history(
            "not-a-timestamp",
            retention_days=90,
            source="fixture private ledger",
            now_ms=now_ms,
        )
    assert type(malformed.value) is BrokerFundingIntegrityError


def _seed_unsettled_exchange_close(
    *,
    position_id: str,
    mode: str,
    symbol: str,
    opened_at: datetime,
    closed_at: datetime,
) -> None:
    position = Position(
        id=position_id,
        symbol=symbol,
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=110.0,
        execution_mode=mode,
        opened_at=opened_at.isoformat(),
    )
    repo.insert_position(position)
    from aletheia.portfolio.pnl import close_position

    close_position(
        position.id,
        exit_price=110.0,
        actual_exit_fee=0.1,
        closed_at=closed_at.isoformat(),
        verified_exit_evidence={
            "source": "private_fills",
            "fill_ids": [f"fill:{position.id}"],
            "fill_quantity": 1.0,
        },
    )


@pytest.mark.parametrize(
    ("expired_count", "with_transient"),
    [(50, False), (49, True)],
)
@pytest.mark.asyncio
async def test_expired_history_prefix_does_not_starve_recoverable_close(
    tmp_path,
    monkeypatch,
    expired_count,
    with_transient,
):
    setup_env(tmp_path, monkeypatch)
    _demo_config(monkeypatch)
    _fresh_snapshot(price=110.0)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    expired_opened = now - timedelta(days=100, hours=2)
    expired_closed = now - timedelta(days=100, hours=1)
    for index in range(expired_count):
        _seed_unsettled_exchange_close(
            position_id=f"expired-{index:02d}",
            mode="bitget_demo",
            symbol="BTC/USDT",
            opened_at=expired_opened,
            closed_at=expired_closed,
        )

    if with_transient:
        _seed_unsettled_exchange_close(
            position_id="transient",
            mode="bitget_demo",
            symbol="ETH/USDT",
            opened_at=now - timedelta(hours=4),
            closed_at=now - timedelta(hours=3),
        )
    _seed_unsettled_exchange_close(
        position_id="fresh",
        mode="okx_demo",
        symbol="SOL/USDT",
        opened_at=now - timedelta(hours=2),
        closed_at=now - timedelta(hours=1),
    )
    if not with_transient:
        _seed_unsettled_exchange_close(
            position_id="fresh-binance",
            mode="binance_testnet",
            symbol="XRP/USDT",
            opened_at=now - timedelta(minutes=90),
            closed_at=now - timedelta(minutes=30),
        )

    network_calls: list[tuple[str, str]] = []

    class FixtureFundingClient:
        def __init__(self, mode: str):
            self.mode = mode

        async def get_funding_ledger(
            self,
            *,
            broker_id,
            symbol,
            start_time_ms,
            end_time_ms,
        ):
            from aletheia.execution.broker_funding import require_retained_history

            require_retained_history(
                start_time_ms,
                retention_days=90,
                source=f"{self.mode} fixture private ledger",
            )
            network_calls.append((broker_id, symbol))
            if symbol == "ETH/USDT":
                raise TimeoutError("fixture transient")
            environment = {
                "bitget_demo": "demo",
                "okx_demo": "demo",
                "binance_testnet": "testnet",
            }[broker_id]
            return make_complete_ledger(
                broker_id=broker_id,
                account_environment=environment,
                source_endpoint="/fixture/private/funding",
                history_start_ms=start_time_ms,
                history_end_ms=end_time_ms,
                events=[],
            )

        async def close(self):
            return None

    from aletheia.portfolio import broker_funding_settlement as settlement

    monkeypatch.setattr(
        settlement,
        "_client_for_mode",
        lambda mode: FixtureFundingClient(mode),
    )
    first = await settlement.settle_unsettled_exchange_closes()

    fresh_count = 1 if with_transient else 2
    assert first["checked"] == expired_count + int(with_transient) + fresh_count
    assert first["broker_io_attempted"] == int(with_transient) + fresh_count
    assert first["history_expired"] == expired_count
    assert first["settled"] == fresh_count
    assert first["still_pending"] == int(with_transient)
    assert len(first["errors"]) == int(with_transient)
    assert network_calls == (
        [("bitget_demo", "ETH/USDT"), ("okx_demo", "SOL/USDT")]
        if with_transient
        else [
            ("okx_demo", "SOL/USDT"),
            ("binance_testnet", "XRP/USDT"),
        ]
    )

    expired_rows = [
        dict(row)
        for row in repo.get_db().execute(
            "SELECT * FROM positions WHERE id LIKE 'expired-%' ORDER BY id"
        ).fetchall()
    ]
    assert len(expired_rows) == expired_count
    for row in expired_rows:
        assert row["realized_pnl"] is None
        assert row["funding_cost"] is None
        assert row["funding_source"] == "unavailable"
        assert row["outcome_eligible"] == 0
        assert row["outcome_exclusion_reason"] == "broker_funding_history_expired"
        terminal = json.loads(row["reconciliation_evidence"])["funding_terminal"]
        assert terminal["reason"] == "broker_funding_history_expired"
        assert terminal["retention_days"] == 90

    fresh = repo._select_one("positions", "fresh")
    assert fresh["funding_source"] == "broker_ledger"
    assert fresh["realized_pnl"] is not None
    if not with_transient:
        assert repo._select_one("positions", "fresh-binance")[
            "funding_source"
        ] == "broker_ledger"
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM position_close_events "
        "WHERE position_id LIKE 'expired-%' AND realized_pnl IS NOT NULL"
    ).fetchone()[0] == 0
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM outcome_hook_jobs "
        "WHERE position_id LIKE 'expired-%'"
    ).fetchone()[0] == 0
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM paper_account_events "
        "WHERE position_id LIKE 'expired-%'"
    ).fetchone()[0] == 0
    audit_count = repo.get_db().execute(
        "SELECT COUNT(*) FROM audit_events WHERE "
        "event_type='exchange_position_funding_history_expired'"
    ).fetchone()[0]
    assert audit_count == expired_count

    await settlement.settle_unsettled_exchange_closes()
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM audit_events WHERE "
        "event_type='exchange_position_funding_history_expired'"
    ).fetchone()[0] == audit_count


@pytest.mark.asyncio
async def test_expired_history_is_classified_before_credentialed_client_init(
    tmp_path,
    monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    _demo_config(monkeypatch)
    _fresh_snapshot(price=110.0)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    for index in range(50):
        _seed_unsettled_exchange_close(
            position_id=f"expired-unconfigured-{index:02d}",
            mode="okx_demo",
            symbol="BTC/USDT",
            opened_at=now - timedelta(days=100, hours=1),
            closed_at=now - timedelta(days=100),
        )
    _seed_unsettled_exchange_close(
        position_id="fresh-configured",
        mode="bitget_demo",
        symbol="SOL/USDT",
        opened_at=now - timedelta(hours=2),
        closed_at=now - timedelta(hours=1),
    )

    constructor_calls: list[str] = []

    class FreshClient:
        async def get_funding_ledger(
            self, *, broker_id, start_time_ms, end_time_ms, **_kwargs
        ):
            return make_complete_ledger(
                broker_id=broker_id,
                account_environment="demo",
                source_endpoint="/fixture/private/funding",
                history_start_ms=start_time_ms,
                history_end_ms=end_time_ms,
                events=[],
            )

        async def close(self):
            return None

    def _client(mode):
        constructor_calls.append(mode)
        if mode == "okx_demo":
            raise RuntimeError("fixture credentials not configured")
        return FreshClient()

    from aletheia.portfolio import broker_funding_settlement as settlement

    monkeypatch.setattr(settlement, "_client_for_mode", _client)
    result = await settlement.settle_unsettled_exchange_closes()

    assert result["checked"] == 51
    assert result["broker_io_attempted"] == 1
    assert result["history_expired"] == 50
    assert result["settled"] == 1
    assert result["errors"] == []
    assert constructor_calls == ["bitget_demo"]
    assert repo._select_one("positions", "fresh-configured")[
        "funding_source"
    ] == "broker_ledger"


@pytest.mark.parametrize(
    "conflict",
    [
        "malformed_evidence",
        "malformed_identity",
        "mismatched_identity",
        "mismatched_close",
        "stale_owner",
    ],
)
@pytest.mark.asyncio
async def test_expired_history_terminalization_fails_closed_on_conflict(
    tmp_path,
    monkeypatch,
    conflict,
):
    setup_env(tmp_path, monkeypatch)
    _demo_config(monkeypatch)
    _fresh_snapshot(price=110.0)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _seed_unsettled_exchange_close(
        position_id="expired-conflict",
        mode="bitget_demo",
        symbol="BTC/USDT",
        opened_at=now - timedelta(days=100, hours=1),
        closed_at=now - timedelta(days=100),
    )
    if conflict == "malformed_evidence":
        repo.update_position(
            "expired-conflict",
            reconciliation_evidence="not-json",
        )
    elif conflict == "malformed_identity":
        repo.update_position(
            "expired-conflict",
            reconciliation_identity="not-json",
        )
    elif conflict == "mismatched_identity":
        identity = json.loads(repo._select_one(
            "positions", "expired-conflict"
        )["reconciliation_identity"])
        identity["broker_id"] = "binance_testnet"
        identity["execution_mode"] = "binance_testnet"
        repo.update_position(
            "expired-conflict",
            reconciliation_identity=json.dumps(identity, sort_keys=True),
        )
    elif conflict == "mismatched_close":
        repo.get_db().execute(
            "UPDATE position_close_events SET symbol=?, execution_mode=?, "
            "closed_qty=?, exit_price=? WHERE position_id=?",
            ("ETH/USDT", "okx_demo", 2.0, 999.0, "expired-conflict"),
        )

    class ConflictingClient:
        async def get_funding_ledger(self, *, start_time_ms, **_kwargs):
            from aletheia.execution.broker_funding import require_retained_history

            require_retained_history(
                start_time_ms,
                retention_days=90,
                source="fixture private ledger",
            )

        async def close(self):
            return None

    from aletheia.portfolio import broker_funding_settlement as settlement

    if conflict == "stale_owner":
        real_guard = settlement.require_broker_funding_history

        def _concurrent_owner(broker_id, start_time_ms):
            repo.update_position(
                "expired-conflict",
                outcome_exclusion_reason="manual_owner_state",
            )
            return real_guard(broker_id, start_time_ms)

        monkeypatch.setattr(
            settlement,
            "require_broker_funding_history",
            _concurrent_owner,
        )
    monkeypatch.setattr(
        settlement,
        "_client_for_mode",
        lambda _mode: ConflictingClient(),
    )
    result = await settlement.settle_unsettled_exchange_closes()
    row = repo._select_one("positions", "expired-conflict")

    assert result["checked"] == 1
    assert result["broker_io_attempted"] == 0
    assert result["history_expired"] == 0
    assert row["realized_pnl"] is None
    assert row["funding_cost"] is None
    assert row["funding_source"] == "unavailable"
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM audit_events WHERE "
        "event_type='exchange_position_funding_history_expired'"
    ).fetchone()[0] == 0
    if conflict != "stale_owner":
        assert result["still_pending"] == 1
        assert result["stale_noop"] == 0
        assert result["errors"] == ["expired-conflict: ValueError"]
        assert row["outcome_exclusion_reason"] == "incomplete_funding_economics"
        if conflict == "malformed_evidence":
            assert row["reconciliation_evidence"] == "not-json"
    else:
        assert result["still_pending"] == 0
        assert result["stale_noop"] == 1
        assert result["errors"] == []
        assert row["outcome_exclusion_reason"] == "manual_owner_state"


@pytest.mark.asyncio
async def test_expired_history_audit_failure_recovers_after_connection_reopen(
    tmp_path,
    monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    _demo_config(monkeypatch)
    _fresh_snapshot(price=110.0)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _seed_unsettled_exchange_close(
        position_id="expired-restart",
        mode="bitget_demo",
        symbol="BTC/USDT",
        opened_at=now - timedelta(days=100, hours=1),
        closed_at=now - timedelta(days=100),
    )
    _seed_unsettled_exchange_close(
        position_id="fresh-restart",
        mode="okx_demo",
        symbol="SOL/USDT",
        opened_at=now - timedelta(hours=2),
        closed_at=now - timedelta(hours=1),
    )

    class RestartClient:
        def __init__(self, mode):
            self.mode = mode

        async def get_funding_ledger(
            self, *, broker_id, start_time_ms, end_time_ms, **_kwargs
        ):
            from aletheia.execution.broker_funding import require_retained_history

            require_retained_history(
                start_time_ms,
                retention_days=90,
                source=f"{broker_id} fixture private ledger",
            )
            return make_complete_ledger(
                broker_id=broker_id,
                account_environment="demo",
                source_endpoint="/fixture/private/funding",
                history_start_ms=start_time_ms,
                history_end_ms=end_time_ms,
                events=[],
            )

        async def close(self):
            return None

    from aletheia.files import jsonl as jsonl_mod
    from aletheia.portfolio import broker_funding_settlement as settlement
    from aletheia.portfolio.funding_audit import (
        funding_audit_backlog,
        reconcile_funding_audits,
    )

    monkeypatch.setattr(
        settlement,
        "_client_for_mode",
        lambda mode: RestartClient(mode),
    )
    real_writer = jsonl_mod.write_audits_deduplicated
    monkeypatch.setattr(
        jsonl_mod,
        "write_audits_deduplicated",
        lambda _events: set(),
    )
    first = await settlement.settle_unsettled_exchange_closes()

    assert first["settled"] == 1
    assert first["history_expired"] == 1
    assert first["still_pending"] == 0
    assert first["audit_pending"] == 2
    assert funding_audit_backlog()["count"] == 2

    from aletheia.storage.sqlite import close_db

    close_db()
    monkeypatch.setattr(jsonl_mod, "write_audits_deduplicated", real_writer)
    replay = await settlement.settle_unsettled_exchange_closes()
    assert replay["checked"] == 0
    assert replay["settled"] == 0
    assert replay["history_expired"] == 0
    reconciled = reconcile_funding_audits()
    assert reconciled["attempted"] == 2
    assert reconciled["mirrored"] == 2
    assert reconciled["pending"] == 0
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM audit_events WHERE entity_id IN (?, ?) AND "
        "event_type IN (?, ?)",
        (
            "expired-restart",
            "fresh-restart",
            "exchange_position_funding_history_expired",
            "exchange_position_funding_settled",
        ),
    ).fetchone()[0] == 2


@pytest.mark.asyncio
async def test_expired_history_scan_budget_progresses_across_restarts(
    tmp_path,
    monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    _demo_config(monkeypatch)
    _fresh_snapshot(price=110.0)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    for index in range(7):
        _seed_unsettled_exchange_close(
            position_id=f"bounded-expired-{index}",
            mode="bitget_demo",
            symbol="BTC/USDT",
            opened_at=now - timedelta(days=100, hours=1),
            closed_at=now - timedelta(days=100),
        )
    _seed_unsettled_exchange_close(
        position_id="bounded-fresh",
        mode="okx_demo",
        symbol="SOL/USDT",
        opened_at=now - timedelta(hours=2),
        closed_at=now - timedelta(hours=1),
    )

    class BoundedClient:
        async def get_funding_ledger(
            self, *, broker_id, start_time_ms, end_time_ms, **_kwargs
        ):
            from aletheia.execution.broker_funding import require_retained_history

            require_retained_history(
                start_time_ms,
                retention_days=90,
                source=f"{broker_id} fixture private ledger",
            )
            return make_complete_ledger(
                broker_id=broker_id,
                account_environment="demo",
                source_endpoint="/fixture/private/funding",
                history_start_ms=start_time_ms,
                history_end_ms=end_time_ms,
                events=[],
            )

        async def close(self):
            return None

    from aletheia.portfolio import broker_funding_settlement as settlement
    from aletheia.storage.sqlite import close_db

    monkeypatch.setattr(
        settlement,
        "_client_for_mode",
        lambda _mode: BoundedClient(),
    )
    first = await settlement.settle_unsettled_exchange_closes(scan_limit=3)
    assert (first["checked"], first["history_expired"], first["settled"]) == (
        3, 3, 0,
    )
    close_db()
    second = await settlement.settle_unsettled_exchange_closes(scan_limit=3)
    assert (second["checked"], second["history_expired"], second["settled"]) == (
        3, 3, 0,
    )
    close_db()
    third = await settlement.settle_unsettled_exchange_closes(scan_limit=3)
    assert (third["checked"], third["history_expired"], third["settled"]) == (
        2, 1, 1,
    )
    assert repo._select_one("positions", "bounded-fresh")[
        "funding_source"
    ] == "broker_ledger"


@pytest.mark.parametrize(
    "prefix_kind",
    ["malformed_evidence", "malformed_window", "transient_failure"],
)
@pytest.mark.asyncio
async def test_funding_queue_cursor_rotates_past_retryable_or_conflicting_prefix(
    tmp_path,
    monkeypatch,
    prefix_kind,
):
    setup_env(tmp_path, monkeypatch)
    _demo_config(monkeypatch)
    _fresh_snapshot(price=110.0)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    for index in range(3):
        position_id = f"cursor-conflict-{index}"
        retryable = prefix_kind == "transient_failure"
        _seed_unsettled_exchange_close(
            position_id=position_id,
            mode="bitget_demo",
            symbol="BTC/USDT",
            opened_at=(
                now - timedelta(hours=4)
                if retryable
                else now - timedelta(days=100, hours=1)
            ),
            closed_at=(
                now - timedelta(hours=3)
                if retryable
                else now - timedelta(days=100)
            ),
        )
        if not retryable:
            repo.update_position(position_id, **(
                {"reconciliation_evidence": "not-json"}
                if prefix_kind == "malformed_evidence"
                else {"opened_at": "not-a-timestamp"}
            ))
    _seed_unsettled_exchange_close(
        position_id="cursor-fresh",
        mode="okx_demo",
        symbol="SOL/USDT",
        opened_at=now - timedelta(hours=2),
        closed_at=now - timedelta(hours=1),
    )

    class CursorClient:
        async def get_funding_ledger(
            self, *, broker_id, start_time_ms, end_time_ms, **_kwargs
        ):
            return make_complete_ledger(
                broker_id=broker_id,
                account_environment="demo",
                source_endpoint="/fixture/private/funding",
                history_start_ms=start_time_ms,
                history_end_ms=end_time_ms,
                events=[],
            )

        async def close(self):
            return None

    from aletheia.portfolio import broker_funding_settlement as settlement
    from aletheia.storage.sqlite import close_db

    def _client(mode):
        if prefix_kind == "transient_failure" and mode == "bitget_demo":
            raise RuntimeError("fixture transient constructor failure")
        return CursorClient()

    monkeypatch.setattr(settlement, "_client_for_mode", _client)
    run_kwargs = (
        {"limit": 2, "scan_limit": 3}
        if prefix_kind == "transient_failure"
        else {"scan_limit": 2}
    )
    first = await settlement.settle_unsettled_exchange_closes(**run_kwargs)
    assert first["checked"] == 2
    assert first["settled"] == 0
    assert first["cursor_advanced"] is True
    close_db()
    second = await settlement.settle_unsettled_exchange_closes(**run_kwargs)
    assert second["checked"] == 2
    assert second["settled"] == 1
    assert second["cursor_advanced"] is True
    assert repo._select_one("positions", "cursor-fresh")[
        "funding_source"
    ] == "broker_ledger"


def test_overlapping_same_symbol_position_rejects_directionless_funding_event():
    event = normalize_bitget_funding_rows([{
        "billId": "bg-shared",
        "symbol": "BTCUSDT",
        "amount": "-1",
        "businessType": "contract_settle_fee",
        "coin": "USDT",
        "cTime": "1784030400000",
    }])[0]
    ledger = make_complete_ledger(
        broker_id="bitget_demo",
        account_environment="demo",
        source_endpoint="/api/v2/mix/account/bill",
        history_start_ms=1783987200000,
        history_end_ms=1784073600000,
        events=[event],
    )
    position = {
        "id": "p-long", "symbol": "BTC/USDT", "market_type": "usdt_futures",
        "direction": "long", "execution_mode": "bitget_demo",
        "opened_at": "2026-07-14T00:00:00+00:00",
    }
    overlapping = {
        "id": "p-short", "symbol": "BTC/USDT", "market_type": "usdt_futures",
        "direction": "short", "execution_mode": "bitget_demo",
        "opened_at": "2026-07-14T00:00:00+00:00",
        "closed_at": "2026-07-14T12:00:00+00:00",
    }
    result = compute_broker_funding(
        position=position,
        closed_at="2026-07-14T12:00:00+00:00",
        ledger=ledger,
        candidate_positions=[overlapping],
    )
    assert result.complete is False
    assert result.evidence["reason"] == "broker_funding_attribution_ambiguous"


@pytest.mark.parametrize(
    ("mode", "environment"),
    [
        ("bitget_demo", "demo"),
        ("okx_demo", "demo"),
        ("binance_testnet", "testnet"),
    ],
)
def test_closed_exchange_position_can_be_recovered_from_broker_funding(
    tmp_path, monkeypatch, mode, environment,
):
    setup_env(tmp_path, monkeypatch)
    _demo_config(monkeypatch)
    _fresh_snapshot(price=110.0)
    position = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=110.0,
        execution_mode=mode,
        opened_at="2026-07-14T00:00:00+00:00",
    )
    repo.insert_position(position)
    from aletheia.portfolio.pnl import close_position

    closed_at = "2026-07-14T12:00:00+00:00"
    close_position(
        position.id,
        exit_price=110.0,
        actual_exit_fee=0.1,
        closed_at=closed_at,
        verified_exit_evidence={
            "source": "private_fills",
            "fill_ids": ["fill-1"],
            "fill_quantity": 1.0,
        },
    )
    incomplete = repo._select_one("positions", position.id)
    assert incomplete["realized_pnl"] is None

    ledger = make_complete_ledger(
        broker_id=mode,
        account_environment=environment,
        source_endpoint="/private/funding",
        history_start_ms=1783987200000,
        history_end_ms=1784073600000,
        events=[],
    )
    funding = compute_broker_funding(
        position=incomplete,
        closed_at=closed_at,
        ledger=ledger,
    )
    from aletheia.portfolio.broker_funding_settlement import (
        materialize_broker_funding_economics,
    )

    result = materialize_broker_funding_economics(
        incomplete,
        funding_cost=funding.funding_cost,
        funding_evidence=funding.evidence,
    )
    stored = repo._select_one("positions", position.id)
    close_event = repo.get_db().execute(
        "SELECT * FROM position_close_events WHERE position_id=?",
        (position.id,),
    ).fetchone()
    assert result["status"] == "settled"
    assert stored["funding_source"] == "broker_ledger"
    assert stored["realized_pnl"] is not None
    assert close_event["realized_pnl"] == stored["realized_pnl"]


def test_broker_funding_audit_failure_replays_without_reapplying_settlement(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    _demo_config(monkeypatch)
    _fresh_snapshot(price=110.0)
    position = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=110.0,
        execution_mode="bitget_demo",
        opened_at="2026-07-14T00:00:00+00:00",
    )
    repo.insert_position(position)
    from aletheia.portfolio.pnl import close_position

    closed_at = "2026-07-14T12:00:00+00:00"
    close_position(
        position.id,
        exit_price=110.0,
        actual_exit_fee=0.1,
        closed_at=closed_at,
        verified_exit_evidence={
            "source": "private_fills",
            "fill_ids": ["fill-audit"],
            "fill_quantity": 1.0,
        },
    )
    incomplete = repo._select_one("positions", position.id)
    ledger = make_complete_ledger(
        broker_id="bitget_demo",
        account_environment="demo",
        source_endpoint="/private/funding",
        history_start_ms=1783987200000,
        history_end_ms=1784073600000,
        events=[],
    )
    funding = compute_broker_funding(
        position=incomplete,
        closed_at=closed_at,
        ledger=ledger,
    )
    from aletheia.files import jsonl as jsonl_mod
    from aletheia.portfolio.broker_funding_settlement import (
        materialize_broker_funding_economics,
    )

    real_writer = jsonl_mod.write_audits_deduplicated
    monkeypatch.setattr(jsonl_mod, "write_audits_deduplicated", lambda _events: set())
    first = materialize_broker_funding_economics(
        incomplete,
        funding_cost=funding.funding_cost,
        funding_evidence=funding.evidence,
    )
    stored = dict(repo._select_one("positions", position.id))
    audit = dict(repo.get_db().execute(
        "SELECT * FROM audit_events WHERE entity_id=? AND "
        "event_type='exchange_position_funding_settled'",
        (position.id,),
    ).fetchone())
    assert first["status"] == "settled"
    assert first["audit_mirror_status"] == "pending"
    assert audit["jsonl_mirror_status"] == "failed"
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM paper_account_events WHERE position_id=?",
        (position.id,),
    ).fetchone()[0] == 0

    monkeypatch.setattr(jsonl_mod, "write_audits_deduplicated", real_writer)
    repeated = materialize_broker_funding_economics(
        incomplete,
        funding_cost=funding.funding_cost,
        funding_evidence=funding.evidence,
    )
    records = [
        json.loads(line)
        for line in (tmp_path / "logs" / "audit.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and json.loads(line).get("id") == audit["id"]
    ]
    assert repeated["status"] == "already_current"
    assert repeated["audit_mirror_status"] == "mirrored"
    assert len(records) == 1
    assert records[0]["timestamp"] == audit["timestamp"]
    assert records[0]["after"]["funding_economics_fingerprint"] == (
        json.loads(stored["reconciliation_evidence"])[
            "funding_economics_fingerprint"
        ]
    )
    assert dict(repo._select_one("positions", position.id)) == stored
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM audit_events WHERE entity_id=? AND "
        "event_type='exchange_position_funding_settled'",
        (position.id,),
    ).fetchone()[0] == 1
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM paper_account_events WHERE position_id=?",
        (position.id,),
    ).fetchone()[0] == 0


def test_bitget_reconciliation_materializes_zero_funding_pnl(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    _demo_config(monkeypatch)
    _fresh_snapshot(price=61000.0)
    captured = []
    _patch_place_order(
        monkeypatch, captured, fill_price=61000.0, filled_quantity=0.001,
    )
    from tests.specs.spec_pipeline_stop_propagation import (
        _patch_private_fill_history,
    )
    _patch_private_fill_history(
        monkeypatch,
        "aletheia.execution.bitget_broker",
        "aletheia.exchanges.bitget.client",
        fill_price=61000.0,
    )
    from aletheia.exchanges.bitget.client import BitgetClient

    async def _funding(self, *, broker_id, start_time_ms, end_time_ms, **kwargs):
        return make_complete_ledger(
            broker_id=broker_id,
            account_environment="demo",
            source_endpoint="/api/v2/mix/account/bill",
            history_start_ms=start_time_ms,
            history_end_ms=end_time_ms,
            events=[],
        )

    monkeypatch.setattr(BitgetClient, "get_funding_ledger", _funding)
    position = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.001,
        entry_price=60000.0,
        current_price=61000.0,
        execution_mode="bitget_demo",
        opened_at="2026-07-14T00:00:00+00:00",
    )
    repo.insert_position(position)
    from aletheia.execution.bitget_broker import close_demo_position
    from aletheia.execution.reconciliation import reconcile

    close_demo_position(position.id, reason="take_profit")
    assert reconcile()["filled"] == 1
    stored = repo._select_one("positions", position.id)
    assert stored["realized_pnl"] is not None
    assert stored["funding_cost"] == 0.0
    assert stored["funding_source"] == "broker_ledger"
    assert stored["outcome_exclusion_reason"] != "incomplete_funding_economics"


@pytest.mark.asyncio
async def test_okx_funding_reader_uses_private_account_bills():
    from aletheia.exchanges.okx.client import OKXClient

    now = datetime.now(timezone.utc)
    start_ms = int((now - timedelta(hours=2)).timestamp() * 1000)
    end_ms = int((now - timedelta(hours=1)).timestamp() * 1000)
    exchange = MagicMock()
    exchange.load_markets = AsyncMock()
    exchange.market.return_value = {"id": "BTC-USDT-SWAP"}
    exchange.private_get_account_bills = AsyncMock(return_value={
        "code": "0",
        "data": [{
            "billId": "okx-funding-1",
            "instId": "BTC-USDT-SWAP",
            "pnl": "-1.25",
            "type": "8",
            "subType": "173",
            "ccy": "USDT",
            "ts": str(start_ms + 30 * 60 * 1000),
            "posSide": "long",
        }],
    })
    with patch(
        "aletheia.exchanges.okx.client.get_secret", return_value="configured",
    ):
        client = OKXClient(mode="demo")
    client._exchange = exchange
    ledger = await client.get_funding_ledger(
        broker_id="okx_demo",
        symbol="BTC/USDT",
        start_time_ms=start_ms,
        end_time_ms=end_ms,
    )
    assert ledger["events"][0]["event_id"] == "okx-funding-1"
    assert ledger["events"][0]["funding_cost"] == 1.25
    exchange.private_get_account_bills.assert_awaited_once()


@pytest.mark.asyncio
async def test_binance_funding_reader_uses_signed_income_history():
    from aletheia.exchanges.binance.client import BinanceClient

    now = datetime.now(timezone.utc)
    start_ms = int((now - timedelta(hours=2)).timestamp() * 1000)
    end_ms = int((now - timedelta(hours=1)).timestamp() * 1000)
    exchange = MagicMock()
    exchange.fapiPrivateGetIncome = AsyncMock(return_value=[{
        "tranId": "binance-funding-1",
        "symbol": "BTCUSDT",
        "income": "0.75",
        "incomeType": "FUNDING_FEE",
        "asset": "USDT",
        "time": start_ms + 30 * 60 * 1000,
    }])
    with patch(
        "aletheia.exchanges.binance.client.get_secret", return_value="configured",
    ):
        client = BinanceClient(mode="testnet")
    client._exchange = exchange
    ledger = await client.get_funding_ledger(
        broker_id="binance_testnet",
        symbol="BTC/USDT",
        start_time_ms=start_ms,
        end_time_ms=end_ms,
    )
    assert ledger["events"][0]["event_id"] == "binance-funding-1"
    assert ledger["events"][0]["funding_cost"] == -0.75
    exchange.fapiPrivateGetIncome.assert_awaited_once()
