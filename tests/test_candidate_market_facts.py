"""Focused regressions for exact pending-candidate market-fact supply."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3


def _setup_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    loader._config_cache = None
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq.reset_db()
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    conn.execute(
        "INSERT INTO universe_snapshots (id, status) VALUES ('snapshot-a', 'active')"
    )
    conn.commit()
    return conn


def _seed_candidate(
    conn,
    *,
    suffix: str,
    venue: str,
    symbol: str,
    physical_market: str,
) -> None:
    now = datetime.now(timezone.utc)
    carrier_market = "usdt_futures" if physical_market == "perp" else "spot"
    asset_id = f"asset-{suffix}"
    member_id = f"member-{suffix}"
    opportunity_id = f"opp-{suffix}"
    conn.execute(
        "INSERT INTO universe_registry "
        "(id, venue, market_type, symbol, status) VALUES (?, ?, ?, ?, 'active')",
        (asset_id, venue, physical_market, symbol),
    )
    conn.execute(
        "INSERT INTO universe_members "
        "(id, snapshot_id, asset_id, symbol, market_type, rank, total_score, expires_at) "
        "VALUES (?, 'snapshot-a', ?, ?, ?, 1, 80, ?)",
        (member_id, asset_id, symbol, physical_market, (now + timedelta(hours=1)).isoformat()),
    )
    conn.execute(
        "INSERT INTO opportunities "
        "(id, symbol, market, status, universe_member_id, valid_until, created_at) "
        "VALUES (?, ?, ?, 'queued', ?, ?, ?)",
        (
            opportunity_id,
            symbol,
            carrier_market,
            member_id,
            (now + timedelta(minutes=30)).isoformat(),
            now.isoformat(),
        ),
    )
    conn.execute(
        "INSERT INTO pi_decisions "
        "(id, symbol, direction, market_type, action, opportunity_id, "
        " signal_source, created_at) "
        "VALUES (?, ?, 'long', ?, 'signal_pending', ?, 'market_observer', ?)",
        (f"pi-{suffix}", symbol, carrier_market, opportunity_id, now.isoformat()),
    )
    conn.commit()


def test_pending_spot_and_perp_resolve_to_exact_instruments(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    _seed_candidate(
        conn, suffix="spot", venue="okx", symbol="BTC/USDT", physical_market="spot",
    )
    _seed_candidate(
        conn, suffix="perp", venue="binance", symbol="BTC/USDT", physical_market="perp",
    )

    from aletheia.data.candidate_market_facts import (
        list_pending_candidate_facts,
        pending_candidate_instruments,
    )

    facts = list_pending_candidate_facts()
    assert {fact["identity_status"] for fact in facts} == {"exact"}
    assert {str(item) for item in pending_candidate_instruments()} == {
        "okx:BTC/USDT:spot",
        "binance:BTC/USDT:perp",
    }


def test_legacy_carrier_does_not_guess_between_venues(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    for venue in ("okx", "binance"):
        conn.execute(
            "INSERT INTO universe_registry "
            "(id, venue, market_type, symbol, status) VALUES (?, ?, 'perp', "
            "'ETH/USDT', 'active')",
            (f"asset-{venue}", venue),
        )
    conn.execute(
        "INSERT INTO opportunities "
        "(id, symbol, market, status, valid_until, created_at) "
        "VALUES ('opp-legacy', 'ETH/USDT', 'usdt_futures', 'queued', ?, ?)",
        ((now + timedelta(minutes=30)).isoformat(), now.isoformat()),
    )
    conn.execute(
        "INSERT INTO pi_decisions "
        "(id, symbol, direction, market_type, action, opportunity_id, "
        " signal_source, created_at) VALUES "
        "('pi-legacy', 'ETH/USDT', 'long', 'usdt_futures', 'signal_pending', "
        " 'opp-legacy', 'market_observer', ?)",
        (now.isoformat(),),
    )
    conn.commit()

    from aletheia.data.candidate_market_facts import list_pending_candidate_facts

    fact = list_pending_candidate_facts()[0]
    assert fact["identity_status"] == "unavailable"
    assert fact["identity_reason"] == "legacy_asset_identity_not_unique"


def test_submitting_recovery_carrier_stays_observed_after_source_expiry(
    tmp_path, monkeypatch,
):
    conn = _setup_db(tmp_path, monkeypatch)
    _seed_candidate(
        conn, suffix="recovery", venue="binance", symbol="BTC/USDT",
        physical_market="spot",
    )
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    conn.execute(
        "UPDATE opportunities SET valid_until=? WHERE id='opp-recovery'",
        ((old - timedelta(minutes=1)).isoformat(),),
    )
    conn.execute(
        "UPDATE pi_decisions SET created_at=? WHERE id='pi-recovery'",
        (old.isoformat(),),
    )
    conn.execute(
        "INSERT INTO pi_decisions "
        "(id, symbol, direction, market_type, action, opportunity_id, "
        "signal_source, proposal_status, created_at) VALUES "
        "('final-recovery', 'BTC/USDT', 'long', 'spot', 'trade', "
        "'opp-recovery', 'market_observer', 'submitting', ?)",
        ((old + timedelta(seconds=1)).isoformat(),),
    )
    conn.commit()

    from aletheia.data.candidate_market_facts import list_pending_candidate_facts

    assert list_pending_candidate_facts()[0]["identity_status"] == "exact"


def test_candidate_report_requires_fresh_feature_ohlcv_and_exact_mark(
    tmp_path, monkeypatch,
):
    conn = _setup_db(tmp_path, monkeypatch)
    _seed_candidate(
        conn, suffix="complete", venue="binance", symbol="SOL/USDT",
        physical_market="perp",
    )
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO universe_feature_snapshots "
        "(id, asset_id, ts, rsi_14, data_freshness_sec) "
        "VALUES ('feature-complete', 'asset-complete', ?, 55, 30)",
        (now.isoformat(),),
    )
    for timeframe in ("1h", "4h"):
        conn.execute(
            "INSERT INTO market_ohlcv "
            "(exchange, symbol, market_type, timeframe, ts, open, high, low, close, volume) "
            "VALUES ('binance', 'SOL/USDT', 'perp', ?, ?, 100, 101, 99, 100, 10)",
            (timeframe, int((now - timedelta(minutes=5)).timestamp() * 1000)),
        )
    conn.commit()

    from aletheia.data import price_feed

    with price_feed._cache_lock:
        price_feed._instrument_price_cache[("binance", "SOL/USDT", "perp")] = {
            "price": 100.0,
            "ts": now,
        }
    from aletheia.data.candidate_market_facts import (
        build_pending_candidate_market_report,
    )

    try:
        report = build_pending_candidate_market_report(now=now)
        assert report["status"] == "complete"
        assert report["complete_count"] == 1
        assert report["items"][0]["reasons"] == []
    finally:
        with price_feed._cache_lock:
            price_feed._instrument_price_cache.pop(
                ("binance", "SOL/USDT", "perp"), None,
            )


def test_candidate_report_names_missing_ohlcv_and_mark(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    _seed_candidate(
        conn, suffix="missing", venue="binance", symbol="XRP/USDT",
        physical_market="perp",
    )
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO universe_feature_snapshots "
        "(id, asset_id, ts, return_1h, data_freshness_sec) "
        "VALUES ('feature-missing', 'asset-missing', ?, 0.1, 30)",
        (now.isoformat(),),
    )
    conn.commit()

    from aletheia.data.candidate_market_facts import (
        build_pending_candidate_market_report,
    )

    item = build_pending_candidate_market_report(now=now)["items"][0]
    assert item["availability"] == "partial"
    assert "candidate_ohlcv_1h_unavailable" in item["reasons"]
    assert "candidate_ohlcv_4h_unavailable" in item["reasons"]
    assert "candidate_entry_mark_unavailable" in item["reasons"]


def test_candidate_report_all_unavailable_is_not_partial(monkeypatch):
    from aletheia.data import candidate_market_facts

    monkeypatch.setattr(
        candidate_market_facts,
        "list_pending_candidate_facts",
        lambda: [
            {
                "opportunity_id": "opp-a",
                "identity_status": "unavailable",
                "identity_reason": "linked_asset_unavailable",
            },
            {
                "opportunity_id": "opp-b",
                "identity_status": "unavailable",
                "identity_reason": "legacy_asset_identity_not_unique",
            },
        ],
    )

    report = candidate_market_facts.build_pending_candidate_market_report()

    assert report["status"] == "unavailable"
    assert report["partial_count"] == 0
    assert report["unavailable_count"] == 2


def test_candidate_technical_reason_keeps_missing_incomplete_and_stale_typed():
    from aletheia.agent_shell.decision_facts import (
        candidate_context_unavailable_reason,
    )

    now = datetime.now(timezone.utc)
    candidate = {
        "opportunity_id": "opp-typed",
        "signal_source": "market_observer",
        "asset_id": "asset-typed",
        "symbol": "ADA/USDT",
        "market_type": "perp",
    }

    def reason(feature):
        return candidate_context_unavailable_reason({
            "layer1": {"features": [feature] if feature else []},
            "layer2": {"pending_signals": [candidate]},
        }, opportunity_id="opp-typed")

    assert reason({}) == "candidate_technical_snapshot_unavailable"
    assert reason({
        "asset_id": "asset-typed",
        "feature_snapshot_id": "feature-empty",
        "feature_ts": now.isoformat(),
        "return_1h": 0.1,
    }) == "candidate_technical_snapshot_incomplete"
    assert reason({
        "asset_id": "asset-typed",
        "feature_snapshot_id": "feature-stale",
        "feature_ts": (now - timedelta(hours=3)).isoformat(),
        "data_freshness_sec": 10,
        "return_1h": 0.1,
    }) == "candidate_technical_snapshot_stale"
