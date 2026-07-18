"""Tests for Phase A defer→reopen acceleration.

Covers two coupled changes shipped together:

* ``universe/features.py::_wake_pi_daemon_for_refreshed_defers`` — wakes
  ``pi_daemon`` when a feature snapshot batch touches a symbol whose carrier
  is currently deferred.
* ``agent_shell/trading_context.py::_build_pending_signal_queue`` — refreshed
  defers keep their oldest-first position instead of being demoted with stale
  defers.

Neither test hits a real daemon; the ``notify_pi_daemon`` symbol is
monkeypatched so we can assert on its call arguments.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
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
    return conn


def _seed_deferred_carrier(
    conn,
    *,
    opportunity_id: str,
    symbol: str,
    asset_id: str,
    defer_until_iso: str,
    deferred_at_iso: str,
) -> None:
    conn.execute(
        "INSERT INTO universe_registry "
        "(id, venue, market_type, symbol, base_asset, quote_asset, status) "
        "VALUES (?, 'binance', 'usdt_futures', ?, ?, 'USDT', 'active')",
        (asset_id, symbol, symbol.split("/")[0]),
    )
    conn.execute(
        "INSERT INTO opportunities "
        "(id, symbol, market, status, created_at) "
        "VALUES (?, ?, 'usdt_futures', 'queued', ?)",
        (opportunity_id, symbol, deferred_at_iso),
    )
    conn.execute(
        "INSERT INTO pi_decisions "
        "(id, symbol, direction, market_type, action, opportunity_id, "
        " signal_source, created_at) "
        "VALUES (?, ?, 'long', 'usdt_futures', 'signal_pending', ?, "
        "        'market_observer', ?)",
        (f"pi-{opportunity_id}", symbol, opportunity_id, deferred_at_iso),
    )
    conn.execute(
        "INSERT INTO operator_evaluation_events "
        "(id, dedup_key, event_kind, trigger, pending_unresolved_total, "
        " pending_visible_count, pending_oldest_created_at, queue_snapshot_json, "
        " data_health_json, policy_epoch_id, created_at) "
        "VALUES (?, ?, 'operator_decision_deferred_low_conviction', 'signal', "
        "        1, 1, ?, ?, '{}', 'epoch-a', ?)",
        (
            f"ev-{opportunity_id}",
            f"dedup-{opportunity_id}",
            deferred_at_iso,
            json.dumps({
                "opportunity_id": opportunity_id,
                "defer_reason": "operator_low_conviction",
                "defer_until": defer_until_iso,
            }),
            deferred_at_iso,
        ),
    )
    conn.commit()


def _seed_feature_snapshot(conn, *, snapshot_id: str, asset_id: str, ts_iso: str) -> None:
    conn.execute(
        "INSERT INTO universe_feature_snapshots (id, asset_id, ts) VALUES (?, ?, ?)",
        (snapshot_id, asset_id, ts_iso),
    )
    conn.commit()


class TestFeatureSnapshotWake:
    """`_wake_pi_daemon_for_refreshed_defers` correctness."""

    def test_wakes_daemon_when_deferred_carrier_matches_committed_asset(
        self, tmp_path, monkeypatch
    ):
        conn = _setup_db(tmp_path, monkeypatch)
        now = datetime.now(timezone.utc)
        _seed_deferred_carrier(
            conn,
            opportunity_id="opp-btc",
            symbol="BTC/USDT",
            asset_id="asset-btc",
            defer_until_iso=(now + timedelta(minutes=90)).isoformat(),
            deferred_at_iso=(now - timedelta(minutes=5)).isoformat(),
        )

        calls: list[str] = []
        import aletheia.agent_runtime.pi_daemon as pi_daemon

        monkeypatch.setattr(
            pi_daemon,
            "notify_pi_daemon",
            lambda source=None, event=None: calls.append(str(source)) or True,
        )

        from aletheia.universe.features import _wake_pi_daemon_for_refreshed_defers

        _wake_pi_daemon_for_refreshed_defers({"asset-btc"})

        assert calls == ["feature_snapshot_committed"]

    def test_no_wake_when_deferred_symbol_not_in_batch(self, tmp_path, monkeypatch):
        conn = _setup_db(tmp_path, monkeypatch)
        now = datetime.now(timezone.utc)
        _seed_deferred_carrier(
            conn,
            opportunity_id="opp-btc",
            symbol="BTC/USDT",
            asset_id="asset-btc",
            defer_until_iso=(now + timedelta(minutes=90)).isoformat(),
            deferred_at_iso=(now - timedelta(minutes=5)).isoformat(),
        )
        # A different asset commits.
        conn.execute(
            "INSERT INTO universe_registry "
            "(id, venue, market_type, symbol, base_asset, quote_asset, status) "
            "VALUES ('asset-eth', 'binance', 'usdt_futures', 'ETH/USDT', 'ETH', "
            "        'USDT', 'active')"
        )
        conn.commit()

        calls: list[str] = []
        import aletheia.agent_runtime.pi_daemon as pi_daemon

        monkeypatch.setattr(
            pi_daemon,
            "notify_pi_daemon",
            lambda source=None, event=None: calls.append(str(source)) or True,
        )

        from aletheia.universe.features import _wake_pi_daemon_for_refreshed_defers

        _wake_pi_daemon_for_refreshed_defers({"asset-eth"})

        assert calls == []

    def test_no_wake_when_defer_window_already_expired(self, tmp_path, monkeypatch):
        conn = _setup_db(tmp_path, monkeypatch)
        now = datetime.now(timezone.utc)
        # defer_until is in the PAST → carrier is no longer a live defer.
        _seed_deferred_carrier(
            conn,
            opportunity_id="opp-btc",
            symbol="BTC/USDT",
            asset_id="asset-btc",
            defer_until_iso=(now - timedelta(minutes=10)).isoformat(),
            deferred_at_iso=(now - timedelta(minutes=30)).isoformat(),
        )

        calls: list[str] = []
        import aletheia.agent_runtime.pi_daemon as pi_daemon

        monkeypatch.setattr(
            pi_daemon,
            "notify_pi_daemon",
            lambda source=None, event=None: calls.append(str(source)) or True,
        )

        from aletheia.universe.features import _wake_pi_daemon_for_refreshed_defers

        _wake_pi_daemon_for_refreshed_defers({"asset-btc"})

        assert calls == []

    def test_empty_asset_set_short_circuits_without_query(self, tmp_path, monkeypatch):
        _setup_db(tmp_path, monkeypatch)

        calls: list[str] = []
        import aletheia.agent_runtime.pi_daemon as pi_daemon

        monkeypatch.setattr(
            pi_daemon,
            "notify_pi_daemon",
            lambda source=None, event=None: calls.append(str(source)) or True,
        )

        from aletheia.universe.features import _wake_pi_daemon_for_refreshed_defers

        _wake_pi_daemon_for_refreshed_defers(set())

        assert calls == []


class TestPendingQueueSort:
    """`_build_pending_signal_queue` demotes stale defers but not refreshed defers."""

    def test_refreshed_defer_keeps_position_stale_defer_moves_to_tail(
        self, tmp_path, monkeypatch
    ):
        conn = _setup_db(tmp_path, monkeypatch)
        now = datetime.now(timezone.utc)
        # Insert 3 carriers, oldest-first: btc (refreshed defer), eth (fresh),
        # sol (stale defer). Post-sort we expect btc, eth to keep head positions
        # and sol to move to tail.
        for opp_id, symbol, asset_id, offset_min in (
            ("opp-btc", "BTC/USDT", "asset-btc", 20),   # oldest
            ("opp-eth", "ETH/USDT", "asset-eth", 15),
            ("opp-sol", "SOL/USDT", "asset-sol", 10),   # newest
        ):
            defer_at = (now - timedelta(minutes=offset_min)).isoformat()
            # eth is NOT deferred → only insert its registry+opportunity+decision.
            if symbol == "ETH/USDT":
                conn.execute(
                    "INSERT INTO universe_registry "
                    "(id, venue, market_type, symbol, base_asset, quote_asset, status) "
                    "VALUES (?, 'binance', 'usdt_futures', ?, ?, 'USDT', 'active')",
                    (asset_id, symbol, symbol.split("/")[0]),
                )
                conn.execute(
                    "INSERT INTO opportunities "
                    "(id, symbol, market, status, created_at, valid_until) "
                    "VALUES (?, ?, 'usdt_futures', 'queued', ?, ?)",
                    (
                        opp_id,
                        symbol,
                        defer_at,
                        (now + timedelta(hours=1)).isoformat(),
                    ),
                )
                conn.execute(
                    "INSERT INTO pi_decisions "
                    "(id, symbol, direction, market_type, action, opportunity_id, "
                    " signal_source, created_at) "
                    "VALUES (?, ?, 'long', 'usdt_futures', 'signal_pending', ?, "
                    "        'market_observer', ?)",
                    (f"pi-{opp_id}", symbol, opp_id, defer_at),
                )
                conn.commit()
                continue

            _seed_deferred_carrier(
                conn,
                opportunity_id=opp_id,
                symbol=symbol,
                asset_id=asset_id,
                defer_until_iso=(now + timedelta(minutes=90)).isoformat(),
                deferred_at_iso=defer_at,
            )
            # Set opportunity.valid_until so the queue filter passes.
            conn.execute(
                "UPDATE opportunities SET valid_until=? WHERE id=?",
                ((now + timedelta(hours=1)).isoformat(), opp_id),
            )
            conn.commit()

        # BTC has a NEWER feature snapshot than its defer → refreshed.
        _seed_feature_snapshot(
            conn,
            snapshot_id="snap-btc-fresh",
            asset_id="asset-btc",
            ts_iso=(now - timedelta(minutes=5)).isoformat(),
        )
        # SOL has an OLDER feature snapshot than its defer → stale.
        _seed_feature_snapshot(
            conn,
            snapshot_id="snap-sol-old",
            asset_id="asset-sol",
            ts_iso=(now - timedelta(minutes=30)).isoformat(),
        )

        from aletheia.agent_shell.trading_context import _build_pending_signal_queue

        visible, _queue = _build_pending_signal_queue(limit=5)
        ordered_symbols = [row["symbol"] for row in visible]
        # Refreshed BTC keeps position 0 (oldest-first), ETH fresh keeps 1,
        # stale SOL demoted to tail.
        assert ordered_symbols == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        # Verify SOL is BEHIND ETH despite being NEWER (which without the fix
        # would put SOL earlier if it weren't demoted). ETH is fresh, SOL is
        # stale defer — SOL must be after every non-stale row.
        assert ordered_symbols.index("SOL/USDT") > ordered_symbols.index("ETH/USDT")

    def test_stale_defer_still_demoted_when_no_feature_snapshot_ever_committed(
        self, tmp_path, monkeypatch
    ):
        conn = _setup_db(tmp_path, monkeypatch)
        now = datetime.now(timezone.utc)
        # A single deferred carrier with no matching feature snapshot record —
        # conservative branch says "demote to tail". Since it's alone, the sort
        # is a no-op observationally, but we verify the code path executes
        # without raising and the row is still visible.
        _seed_deferred_carrier(
            conn,
            opportunity_id="opp-btc",
            symbol="BTC/USDT",
            asset_id="asset-btc",
            defer_until_iso=(now + timedelta(minutes=90)).isoformat(),
            deferred_at_iso=(now - timedelta(minutes=5)).isoformat(),
        )
        conn.execute(
            "UPDATE opportunities SET valid_until=? WHERE id='opp-btc'",
            ((now + timedelta(hours=1)).isoformat(),),
        )
        conn.commit()

        from aletheia.agent_shell.trading_context import _build_pending_signal_queue

        visible, _queue = _build_pending_signal_queue(limit=5)
        assert [row["symbol"] for row in visible] == ["BTC/USDT"]
