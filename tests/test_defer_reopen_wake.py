"""Tests for defer→reopen acceleration and defer-loop prevention.

Covers:

* ``universe/features.py::_wake_pi_daemon_for_refreshed_defers`` — wakes
  ``pi_daemon`` only when a candidate-specific (data-availability) defer exists
  for a symbol whose feature batch was just committed. Market-quiet defers
  (direct_skip_not_confirmed, operator_low_conviction, debate_not_confirmed)
  do not trigger a wake, preventing the tight re-evaluation loop.
* ``agent_shell/trading_context.py::_build_pending_signal_queue`` — all active
  defers (defer_until > now) are demoted to the queue tail so non-deferred
  candidates are processed first.

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
    defer_reason: str = "candidate_technical_snapshot_unavailable",
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
        "VALUES (?, ?, 'operator_decision_deferred_unchanged_context', 'signal', "
        "        1, 1, ?, ?, '{}', 'epoch-a', ?)",
        (
            f"ev-{opportunity_id}",
            f"dedup-{opportunity_id}",
            deferred_at_iso,
            json.dumps({
                "opportunity_id": opportunity_id,
                "defer_reason": defer_reason,
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

    def test_same_symbol_wrong_market_does_not_wake(self, tmp_path, monkeypatch):
        conn = _setup_db(tmp_path, monkeypatch)
        now = datetime.now(timezone.utc)
        _seed_deferred_carrier(
            conn,
            opportunity_id="opp-btc-perp",
            symbol="BTC/USDT",
            asset_id="asset-btc-perp",
            defer_until_iso=(now + timedelta(minutes=90)).isoformat(),
            deferred_at_iso=(now - timedelta(minutes=5)).isoformat(),
        )
        conn.execute(
            "INSERT INTO universe_registry "
            "(id, venue, market_type, symbol, status) "
            "VALUES ('asset-btc-spot', 'binance', 'spot', 'BTC/USDT', 'active')"
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

        _wake_pi_daemon_for_refreshed_defers({"asset-btc-spot"})
        assert calls == []

    def test_exact_mark_refresh_wakes_only_mark_unavailability(
        self, tmp_path, monkeypatch,
    ):
        conn = _setup_db(tmp_path, monkeypatch)
        now = datetime.now(timezone.utc)
        _seed_deferred_carrier(
            conn,
            opportunity_id="opp-mark",
            symbol="BTC/USDT",
            asset_id="asset-mark",
            defer_until_iso=(now + timedelta(minutes=90)).isoformat(),
            deferred_at_iso=(now - timedelta(minutes=5)).isoformat(),
            defer_reason="candidate_entry_mark_unavailable",
        )
        calls: list[str] = []
        import aletheia.agent_runtime.pi_daemon as pi_daemon

        monkeypatch.setattr(
            pi_daemon,
            "notify_pi_daemon",
            lambda source=None, event=None: calls.append(str(source)) or True,
        )
        from aletheia.data.candidate_market_facts import (
            wake_pending_candidate_defers,
        )
        from aletheia.data.symbols import InstrumentId

        wake_pending_candidate_defers(
            instruments=[InstrumentId.from_parts("binance", "BTC/USDT", "perp")],
            defer_reasons={"candidate_entry_mark_unavailable"},
            source="candidate_entry_mark_refreshed",
        )
        assert calls == ["candidate_entry_mark_refreshed"]

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

    def test_no_wake_for_market_quiet_defer_direct_skip(self, tmp_path, monkeypatch):
        """direct_skip_not_confirmed is a market-judgment defer — no wake on feature refresh."""
        conn = _setup_db(tmp_path, monkeypatch)
        now = datetime.now(timezone.utc)
        _seed_deferred_carrier(
            conn,
            opportunity_id="opp-btc",
            symbol="BTC/USDT",
            asset_id="asset-btc",
            defer_until_iso=(now + timedelta(minutes=90)).isoformat(),
            deferred_at_iso=(now - timedelta(minutes=5)).isoformat(),
            defer_reason="direct_skip_not_confirmed:neutral_or_missing_skip_kind",
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

        assert calls == [], "market-quiet defer must not trigger feature-refresh wake"

    def test_no_wake_for_market_quiet_defer_low_conviction(self, tmp_path, monkeypatch):
        """operator_low_conviction is a market-judgment defer — no wake on feature refresh."""
        conn = _setup_db(tmp_path, monkeypatch)
        now = datetime.now(timezone.utc)
        _seed_deferred_carrier(
            conn,
            opportunity_id="opp-btc",
            symbol="BTC/USDT",
            asset_id="asset-btc",
            defer_until_iso=(now + timedelta(minutes=90)).isoformat(),
            deferred_at_iso=(now - timedelta(minutes=5)).isoformat(),
            defer_reason="operator_low_conviction",
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

        assert calls == [], "market-quiet defer must not trigger feature-refresh wake"

    def test_wakes_for_candidate_specific_defer_incomplete(self, tmp_path, monkeypatch):
        """candidate_technical_snapshot_incomplete is data-availability — should wake."""
        conn = _setup_db(tmp_path, monkeypatch)
        now = datetime.now(timezone.utc)
        _seed_deferred_carrier(
            conn,
            opportunity_id="opp-btc",
            symbol="BTC/USDT",
            asset_id="asset-btc",
            defer_until_iso=(now + timedelta(minutes=90)).isoformat(),
            deferred_at_iso=(now - timedelta(minutes=5)).isoformat(),
            defer_reason="candidate_technical_snapshot_incomplete",
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


class TestDeferredDeadlineRecovery:
    """A promised Operator re-evaluation remains discoverable after restart."""

    def test_due_defer_is_returned_until_a_new_operator_observation(
        self, tmp_path, monkeypatch
    ):
        conn = _setup_db(tmp_path, monkeypatch)
        now = datetime.now(timezone.utc)
        defer_until = (now - timedelta(seconds=1)).isoformat()
        _seed_deferred_carrier(
            conn,
            opportunity_id="opp-due",
            symbol="BTC/USDT",
            asset_id="asset-btc-due",
            defer_until_iso=defer_until,
            deferred_at_iso=(now - timedelta(minutes=10)).isoformat(),
            defer_reason="operator_low_conviction",
        )
        conn.execute(
            "UPDATE opportunities SET valid_until=? WHERE id='opp-due'",
            ((now + timedelta(minutes=5)).isoformat(),),
        )
        conn.commit()

        from aletheia.storage.repositories import next_pending_signal_defer_deadline

        assert next_pending_signal_defer_deadline(now_iso=now.isoformat()) == defer_until

        observed_at = (now + timedelta(seconds=1)).isoformat()
        conn.execute(
            "INSERT INTO operator_evaluation_events "
            "(id, dedup_key, event_kind, trigger, pending_unresolved_total, "
            " pending_visible_count, pending_oldest_created_at, queue_snapshot_json, "
            " data_health_json, policy_epoch_id, created_at) "
            "VALUES ('ev-due-reviewed', 'dedup-due-reviewed', "
            "        'operator_skip_validated', 'defer_deadline', 1, 1, ?, ?, '{}', "
            "        'epoch-a', ?)",
            (
                observed_at,
                json.dumps({"opportunity_id": "opp-due"}),
                observed_at,
            ),
        )
        conn.commit()

        assert next_pending_signal_defer_deadline(now_iso=now.isoformat()) is None

    def test_terminal_or_expired_candidate_is_not_rearmed(self, tmp_path, monkeypatch):
        conn = _setup_db(tmp_path, monkeypatch)
        now = datetime.now(timezone.utc)
        defer_until = (now + timedelta(seconds=30)).isoformat()
        _seed_deferred_carrier(
            conn,
            opportunity_id="opp-terminal",
            symbol="ETH/USDT",
            asset_id="asset-eth-terminal",
            defer_until_iso=defer_until,
            deferred_at_iso=(now - timedelta(minutes=5)).isoformat(),
        )
        conn.execute(
            "UPDATE opportunities SET valid_until=? WHERE id='opp-terminal'",
            ((now + timedelta(minutes=5)).isoformat(),),
        )
        conn.execute(
            "INSERT INTO pi_decisions "
            "(id, symbol, direction, market_type, action, opportunity_id, "
            " signal_source, created_at) "
            "VALUES ('pi-terminal-final', 'ETH/USDT', 'long', 'usdt_futures', "
            "        'skip', 'opp-terminal', 'market_observer', ?)",
            ((now + timedelta(seconds=1)).isoformat(),),
        )
        conn.commit()

        from aletheia.storage.repositories import next_pending_signal_defer_deadline

        assert next_pending_signal_defer_deadline(now_iso=now.isoformat()) is None

        conn.execute("DELETE FROM pi_decisions WHERE id='pi-terminal-final'")
        conn.execute(
            "UPDATE opportunities SET valid_until=? WHERE id='opp-terminal'",
            ((now - timedelta(seconds=1)).isoformat(),),
        )
        conn.commit()
        assert next_pending_signal_defer_deadline(now_iso=now.isoformat()) is None


class TestPendingQueueSort:
    """`_build_pending_signal_queue` demotes all active defers to the queue tail."""

    def test_all_active_defers_demoted_non_deferred_at_head(
        self, tmp_path, monkeypatch
    ):
        conn = _setup_db(tmp_path, monkeypatch)
        now = datetime.now(timezone.utc)
        # Insert 3 carriers oldest-first: btc (deferred, feature refreshed since defer),
        # eth (not deferred), sol (deferred, no feature snapshot).
        # After sort: eth (non-deferred) first; btc and sol both demoted to tail.
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

        # BTC has a NEWER feature snapshot than its defer (was "refreshed defer" under
        # the old logic). Under the new logic this still gets demoted to tail.
        _seed_feature_snapshot(
            conn,
            snapshot_id="snap-btc-fresh",
            asset_id="asset-btc",
            ts_iso=(now - timedelta(minutes=5)).isoformat(),
        )
        # SOL has an OLDER feature snapshot than its defer (was "stale defer"). Also
        # demoted to tail.
        _seed_feature_snapshot(
            conn,
            snapshot_id="snap-sol-old",
            asset_id="asset-sol",
            ts_iso=(now - timedelta(minutes=30)).isoformat(),
        )

        from aletheia.agent_shell.trading_context import _build_pending_signal_queue

        visible, _queue = _build_pending_signal_queue(limit=5)
        ordered_symbols = [row["symbol"] for row in visible]
        # Non-deferred ETH is at head; both deferred BTC and SOL are at tail.
        assert ordered_symbols[0] == "ETH/USDT"
        assert ordered_symbols.index("BTC/USDT") > ordered_symbols.index("ETH/USDT")
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


class TestRegistryFallbackHydration:
    """Feature snapshot resolves via universe_registry when opportunity has no member link."""

    def test_feature_found_via_registry_when_no_universe_member_id(self, tmp_path, monkeypatch):
        """No universe_member_id → fallback to registry by symbol+market_type."""
        conn = _setup_db(tmp_path, monkeypatch)
        now = datetime.now(timezone.utc)

        # Opportunity produced by observer before universe was enabled — no member link.
        conn.execute(
            "INSERT INTO opportunities "
            "(id, symbol, market, status, created_at, valid_until) "
            "VALUES (?, 'BTC/USDT', 'spot', 'queued', ?, ?)",
            ("opp-btc-nomember", now.isoformat(), (now + timedelta(hours=4)).isoformat()),
        )
        conn.execute(
            "INSERT INTO pi_decisions "
            "(id, symbol, direction, market_type, action, opportunity_id, "
            " signal_source, created_at) "
            "VALUES ('pi-btc-nomember', 'BTC/USDT', 'long', 'spot', 'signal_pending', "
            "        'opp-btc-nomember', 'market_observer', ?)",
            (now.isoformat(),),
        )
        # universe_registry has this symbol — added by the universe scanner independently.
        conn.execute(
            "INSERT INTO universe_registry "
            "(id, venue, market_type, symbol, base_asset, quote_asset, status) "
            "VALUES ('asset-btc-spot', 'binance', 'spot', 'BTC/USDT', 'BTC', 'USDT', 'active')",
        )
        conn.execute(
            "INSERT INTO universe_feature_snapshots "
            "(id, asset_id, ts, rsi_14, return_1h, return_4h, return_24h, data_freshness_sec) "
            "VALUES ('snap-btc', 'asset-btc-spot', ?, 58.0, 0.5, 1.2, 2.8, 60)",
            (now.isoformat(),),
        )
        conn.commit()

        from aletheia.agent_shell.decision_facts import candidate_context_unavailable_reason
        from aletheia.agent_shell.trading_context import _hydrate_pending_candidate_context

        candidate = {
            "symbol": "BTC/USDT",
            "direction": "long",
            "market_type": "spot",
            "opportunity_id": "opp-btc-nomember",
            "signal_source": "market_observer",
            "universe_member_id": None,
            "asset_id": None,
        }
        layer1: dict = {"feature_index": {}, "features": [], "opportunities": []}
        layer2: dict = {"pending_signals": [candidate], "executable_candidates": []}

        _hydrate_pending_candidate_context(layer1, layer2)

        assert candidate.get("asset_id") == "asset-btc-spot", (
            f"fallback should populate asset_id from registry, got: {candidate.get('asset_id')}"
        )
        assert layer1["feature_index"], "feature_index must be populated via fallback"

        ctx = {"layer1": layer1, "layer2": layer2}
        reason = candidate_context_unavailable_reason(ctx, opportunity_id="opp-btc-nomember")
        assert reason is None, (
            f"unavailable reason should be None after fallback resolves feature, got: {reason}"
        )

    def test_no_registry_entry_still_unavailable(self, tmp_path, monkeypatch):
        """When symbol is also absent from registry, unavailable reason is preserved."""
        conn = _setup_db(tmp_path, monkeypatch)
        now = datetime.now(timezone.utc)

        conn.execute(
            "INSERT INTO opportunities "
            "(id, symbol, market, status, created_at, valid_until) "
            "VALUES (?, 'XYZ/USDT', 'spot', 'queued', ?, ?)",
            ("opp-xyz", now.isoformat(), (now + timedelta(hours=4)).isoformat()),
        )
        conn.execute(
            "INSERT INTO pi_decisions "
            "(id, symbol, direction, market_type, action, opportunity_id, "
            " signal_source, created_at) "
            "VALUES ('pi-xyz', 'XYZ/USDT', 'long', 'spot', 'signal_pending', "
            "        'opp-xyz', 'market_observer', ?)",
            (now.isoformat(),),
        )
        conn.commit()

        from aletheia.agent_shell.decision_facts import candidate_context_unavailable_reason
        from aletheia.agent_shell.trading_context import _hydrate_pending_candidate_context

        candidate = {
            "symbol": "XYZ/USDT",
            "direction": "long",
            "market_type": "spot",
            "opportunity_id": "opp-xyz",
            "signal_source": "market_observer",
            "universe_member_id": None,
            "asset_id": None,
        }
        layer1: dict = {"feature_index": {}, "features": [], "opportunities": []}
        layer2: dict = {"pending_signals": [candidate], "executable_candidates": []}

        _hydrate_pending_candidate_context(layer1, layer2)

        assert candidate.get("asset_id") is None, "no registry row → asset_id stays None"
        ctx = {"layer1": layer1, "layer2": layer2}
        reason = candidate_context_unavailable_reason(ctx, opportunity_id="opp-xyz")
        assert reason == "candidate_technical_snapshot_unavailable"
