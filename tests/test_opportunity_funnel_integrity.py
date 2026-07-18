"""Regression coverage for the Opportunity Funnel lifecycle and honest read models."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
import uuid

import pytest


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
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn
    run_migrations()
    return conn


def _insert_snapshot(conn, snapshot_id: str, status: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO universe_snapshots (id,market_type,status,created_at) VALUES (?,?,?,?)",
        (snapshot_id, "crypto", status, now),
    )


def _insert_opportunity(
    conn,
    opportunity_id: str,
    *,
    status: str,
    snapshot_id: str | None = None,
    created_at: str | None = None,
    valid_until: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO opportunities "
        "(id,symbol,market,direction_bias,confidence,status,asset_class,scanner_family,"
        "universe_snapshot_id,created_at,valid_until) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            opportunity_id, f"{opportunity_id}/USDT", "spot", "long", "high",
            status, "crypto", "momentum", snapshot_id,
            created_at or datetime.now(timezone.utc).isoformat(),
            valid_until,
        ),
    )


def _insert_pending(conn, decision_id: str, opportunity_id: str, created_at: str) -> None:
    conn.execute(
        "INSERT INTO pi_decisions "
        "(id,created_at,symbol,direction,market_type,action,opportunity_id,signal_source) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (decision_id, created_at, "BTC/USDT", "long", "spot", "signal_pending", opportunity_id, "market_observer"),
    )


def test_superseded_cascade_expires_only_unclaimed_generations_with_atomic_evidence(
    tmp_path, monkeypatch
):
    conn = _setup_db(tmp_path, monkeypatch)
    _insert_snapshot(conn, "old", "superseded")
    _insert_snapshot(conn, "active", "active")
    for oid, status, snapshot in (
        ("open-old", "open", "old"),
        ("queued-old", "queued", "old"),
        ("reviewed-old", "reviewed", "old"),
        ("open-active", "open", "active"),
    ):
        _insert_opportunity(conn, oid, status=status, snapshot_id=snapshot)
    conn.commit()

    from aletheia.storage import repositories as repo

    assert repo.expire_opportunities_from_superseded_snapshots("crypto") == 1
    assert repo.expire_opportunities_from_superseded_snapshots("crypto") == 0
    statuses = dict(conn.execute("SELECT id,status FROM opportunities"))
    assert statuses == {
        "open-old": "expired",
        "queued-old": "queued",
        "reviewed-old": "reviewed",
        "open-active": "open",
    }
    evidence = conn.execute(
        "SELECT opportunity_id,decision,reason_code FROM filter_decisions "
        "WHERE filter_name='universe_snapshot_status' ORDER BY opportunity_id"
    ).fetchall()
    assert [tuple(row) for row in evidence] == [
        ("open-old", "rejected", "universe_snapshot_superseded"),
    ]


def test_superseded_queued_carrier_remains_visible_until_its_own_valid_until(
    tmp_path, monkeypatch
):
    conn = _setup_db(tmp_path, monkeypatch)
    _insert_snapshot(conn, "old", "superseded")
    valid_until = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    _insert_opportunity(
        conn,
        "queued-old",
        status="queued",
        snapshot_id="old",
        valid_until=valid_until,
    )
    _insert_pending(
        conn,
        "pending-old",
        "queued-old",
        datetime.now(timezone.utc).isoformat(),
    )
    conn.commit()

    from aletheia.agent_shell.trading_context import _build_pending_signal_queue
    from aletheia.storage import repositories as repo

    before, _ = _build_pending_signal_queue()
    assert [row["opportunity_id"] for row in before] == ["queued-old"]

    assert repo.expire_opportunities_from_superseded_snapshots("crypto") == 0
    visible, queue = _build_pending_signal_queue()
    assert [row["opportunity_id"] for row in visible] == ["queued-old"]
    assert visible[0]["valid_until"] == valid_until
    assert queue["unresolved_total"] == 1


def test_superseded_cascade_rolls_back_status_when_evidence_fails(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    _insert_snapshot(conn, "old", "superseded")
    _insert_opportunity(conn, "open-old", status="open", snapshot_id="old")
    _insert_opportunity(conn, "queued-old", status="queued", snapshot_id="old")
    conn.execute(
        "CREATE TRIGGER reject_snapshot_expiry_evidence BEFORE INSERT ON filter_decisions "
        "WHEN NEW.filter_name='universe_snapshot_status' "
        "BEGIN SELECT RAISE(ABORT, 'forced snapshot evidence failure'); END"
    )
    conn.commit()

    from aletheia.storage import repositories as repo

    with pytest.raises(sqlite3.IntegrityError, match="forced snapshot evidence failure"):
        repo.expire_opportunities_from_superseded_snapshots("crypto")
    assert dict(conn.execute("SELECT id,status FROM opportunities")) == {
        "open-old": "open", "queued-old": "queued",
    }


def test_reused_active_snapshot_revalidates_linked_queued_carrier(
    tmp_path, monkeypatch
):
    conn = _setup_db(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    old_expiry = (now + timedelta(minutes=5)).isoformat()
    _insert_snapshot(conn, "active", "active")
    conn.execute(
        "INSERT INTO universe_registry (id,venue,market_type,symbol) "
        "VALUES ('asset','binance','spot','BTC/USDT')"
    )
    conn.execute(
        "INSERT INTO universe_members "
        "(id,snapshot_id,asset_id,symbol,rank,total_score,ttl_sec,expires_at) "
        "VALUES ('member','active','asset','BTC/USDT',1,50,3600,?)",
        (old_expiry,),
    )
    _insert_opportunity(
        conn,
        "queued-revalidated",
        status="queued",
        snapshot_id="active",
        valid_until=old_expiry,
    )
    conn.execute(
        "UPDATE opportunities SET universe_member_id='member' "
        "WHERE id='queued-revalidated'"
    )
    conn.commit()

    from aletheia.storage.repositories import refresh_active_snapshot_ttls

    members_updated, opportunities_updated = refresh_active_snapshot_ttls(
        "active", now
    )
    expected = (now + timedelta(hours=1)).isoformat()
    assert (members_updated, opportunities_updated) == (1, 1)
    assert conn.execute(
        "SELECT valid_until FROM opportunities WHERE id='queued-revalidated'"
    ).fetchone()[0] == expected


def test_queued_market_carrier_uses_opportunity_valid_until_not_fixed_30_minutes(
    tmp_path, monkeypatch
):
    conn = _setup_db(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    old = (now - timedelta(minutes=45)).isoformat()
    valid_until = (now + timedelta(hours=2)).isoformat()
    _insert_opportunity(
        conn,
        "long-lived",
        status="queued",
        created_at=old,
        valid_until=valid_until,
    )
    _insert_pending(conn, "pending-long-lived", "long-lived", old)
    conn.commit()

    from aletheia.agent_shell.trading_context import _build_pending_signal_queue
    from aletheia.storage import repositories as repo

    assert repo.count_unresolved_signal_pending(max_age_minutes=30) == 1
    assert repo.expire_stale_queued_opportunities(max_age_minutes=30) == 0
    visible, queue = _build_pending_signal_queue(max_age_minutes=30)
    assert [row["opportunity_id"] for row in visible] == ["long-lived"]
    assert visible[0]["valid_until"] == valid_until
    assert queue["unresolved_total"] == 1
    assert queue["expired_unconsumed_rows"] == 0


def test_queued_market_carrier_expires_at_valid_until_with_truthful_evidence(
    tmp_path, monkeypatch
):
    conn = _setup_db(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    pending_at = (now - timedelta(minutes=10)).isoformat()
    valid_until = (now - timedelta(seconds=1)).isoformat()
    _insert_opportunity(
        conn,
        "ttl-ended",
        status="queued",
        created_at=pending_at,
        valid_until=valid_until,
    )
    _insert_pending(conn, "pending-ttl-ended", "ttl-ended", pending_at)
    conn.commit()

    from aletheia.storage import repositories as repo

    assert repo.expire_stale_queued_opportunities(max_age_minutes=30) == 1
    assert conn.execute(
        "SELECT status FROM opportunities WHERE id='ttl-ended'"
    ).fetchone()[0] == "expired"
    fact = conn.execute(
        "SELECT reason_code,parameter_snapshot FROM filter_decisions "
        "WHERE opportunity_id='ttl-ended' AND filter_name='opportunity_ttl'"
    ).fetchone()
    assert fact["reason_code"] == "ttl_expired"
    parameters = json.loads(fact["parameter_snapshot"])
    assert parameters["expiry_basis"] == "valid_until"
    assert parameters["valid_until"] == valid_until


def test_open_generation_past_valid_until_remains_for_ranker_soft_ttl_window(
    tmp_path, monkeypatch
):
    conn = _setup_db(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    _insert_opportunity(
        conn,
        "soft-ttl",
        status="open",
        created_at=(now - timedelta(minutes=20)).isoformat(),
        valid_until=(now - timedelta(minutes=1)).isoformat(),
    )
    conn.commit()

    from aletheia.storage.repositories import expire_stale_opportunities

    assert expire_stale_opportunities(ttl_hours=48) == 0
    assert conn.execute(
        "SELECT status FROM opportunities WHERE id='soft-ttl'"
    ).fetchone()[0] == "open"


def test_queued_market_carrier_without_valid_until_keeps_30_minute_legacy_fallback(
    tmp_path, monkeypatch
):
    conn = _setup_db(tmp_path, monkeypatch)
    old = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    _insert_opportunity(conn, "legacy", status="queued", created_at=old)
    _insert_pending(conn, "pending-legacy", "legacy", old)
    conn.commit()

    from aletheia.storage import repositories as repo

    assert repo.expire_stale_queued_opportunities(max_age_minutes=30) == 1
    fact = conn.execute(
        "SELECT parameter_snapshot FROM filter_decisions "
        "WHERE opportunity_id='legacy' AND filter_name='opportunity_ttl'"
    ).fetchone()
    assert json.loads(fact["parameter_snapshot"])["expiry_basis"] == (
        "pending_created_at_legacy_fallback"
    )


def test_active_queued_setup_blocks_duplicate_signal_after_cooldown_window(
    tmp_path, monkeypatch
):
    conn = _setup_db(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    old = (now - timedelta(minutes=45)).isoformat()
    _insert_opportunity(
        conn,
        "active-carrier",
        status="queued",
        created_at=old,
        valid_until=(now + timedelta(hours=2)).isoformat(),
    )
    conn.execute(
        "UPDATE opportunities SET symbol='BTC/USDT',direction_bias='short' WHERE id=?",
        ("active-carrier",),
    )
    conn.execute(
        "INSERT INTO pi_decisions "
        "(id,created_at,symbol,direction,market_type,action,opportunity_id,signal_source) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            "pending-active-carrier",
            old,
            "BTC/USDT",
            "short",
            "spot",
            "signal_pending",
            "active-carrier",
            "market_observer",
        ),
    )
    conn.commit()

    from aletheia.agents.paper_trader import (
        _active_signal_setups,
        _plan_signal_batch,
    )

    active = _active_signal_setups()
    assert ("BTC/USDT", "short", "momentum") in active
    plan = _plan_signal_batch(
        [
            {
                "opportunity_id": "new-generation",
                "symbol": "BTC/USDT",
                "direction": "short",
                "scanner_family": "momentum",
            }
        ],
        open_symbols=set(),
        recent_signal_pairs=set(),
        active_signal_setups=active,
    )
    assert plan.selected == []
    assert plan.decisions["new-generation"] == (
        "active_operator_carrier",
        "rejected",
    )


def test_old_carrier_with_future_valid_until_passes_preflight_and_atomic_claim(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    conn = _setup_db(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    old = (now - timedelta(minutes=45)).isoformat()
    _insert_opportunity(
        conn,
        "claimable",
        status="queued",
        created_at=old,
        valid_until=(now + timedelta(hours=2)).isoformat(),
    )
    conn.execute(
        "UPDATE opportunities SET symbol='BTC/USDT',market='spot',"
        "direction_bias='long' WHERE id='claimable'"
    )
    _insert_pending(conn, "pending-claimable", "claimable", old)
    conn.commit()

    from aletheia.agent_shell.commands import _pending_carrier_authority_failure
    from aletheia.agent_shell.reflection import _pending_claim_authority_failure

    decision = SimpleNamespace(
        opportunity_id="claimable",
        signal_source="market_observer",
        symbol="BTC/USDT",
        direction="long",
        market_type="spot",
        filter_trace=[],
    )
    assert _pending_carrier_authority_failure(decision) is None
    assert _pending_claim_authority_failure(decision) is None


def test_operator_defer_deadline_is_capped_by_carrier_valid_until(
    tmp_path, monkeypatch
):
    conn = _setup_db(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    valid_until = (now + timedelta(minutes=20)).isoformat()

    from aletheia.agent_shell import commands

    monkeypatch.setattr(
        commands,
        "_deferred_context_dedup_key",
        lambda *_args, **_kwargs: ("fixed-dedup", now.isoformat()),
    )

    event = commands._record_deferred_context_observation(
        {
            "layer2": {
                "pending_queue": {
                    "unresolved_total": 1,
                    "visible_count": 1,
                    "overflow_count": 0,
                    "oldest_at": datetime.now(timezone.utc).isoformat(),
                },
                "pending_signals": [
                    {"opportunity_id": "defer-capped", "valid_until": valid_until}
                ],
            }
        },
        context_fingerprint="fingerprint",
        opportunity_id="defer-capped",
        policy_epoch_id="test-epoch",
        trigger="signal",
        reason="candidate_technical_snapshot_unavailable",
        window_minutes=120,
    )

    snapshot = json.loads(event["queue_snapshot_json"])
    assert snapshot["defer_until"] == valid_until
    assert snapshot["defer_deadline_basis"] == "opportunity_valid_until"
    assert conn.execute(
        "SELECT COUNT(*) FROM operator_evaluation_events WHERE id=?", (event["id"],)
    ).fetchone()[0] == 1


def test_expired_opportunity_cannot_transition_to_reviewed(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    _insert_opportunity(conn, "expired", status="expired")
    conn.commit()

    from aletheia.storage.repositories import transition_opportunity_to_reviewed

    assert transition_opportunity_to_reviewed("expired") is False
    assert conn.execute(
        "SELECT status FROM opportunities WHERE id='expired'"
    ).fetchone()[0] == "expired"


def test_stale_pending_prune_only_removes_terminal_nonrecovering_carriers(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    _insert_opportunity(conn, "queued", status="queued")
    _insert_opportunity(conn, "expired", status="expired")
    _insert_opportunity(conn, "recovering", status="reviewed")
    _insert_pending(conn, "pending-queued", "queued", old)
    _insert_pending(conn, "pending-expired", "expired", old)
    _insert_pending(conn, "pending-recovering", "recovering", old)
    conn.execute(
        "INSERT INTO pi_decisions "
        "(id,created_at,symbol,direction,market_type,action,opportunity_id,signal_source,proposal_status) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("final-recovering", old, "BTC/USDT", "long", "spot", "trade", "recovering", "market_observer", "submitting"),
    )
    conn.commit()

    from aletheia.storage.repositories import prune_signal_pending_stale

    assert prune_signal_pending_stale(minutes=30) == 1
    remaining = {
        row[0] for row in conn.execute(
            "SELECT id FROM pi_decisions WHERE action='signal_pending'"
        )
    }
    assert remaining == {"pending-queued", "pending-recovering"}


def _insert_proposal(
    conn,
    proposal_id: str,
    opportunity_id: str | None,
    *,
    created_at: str,
    status: str = "pending",
) -> None:
    conn.execute(
        "INSERT INTO trade_proposals "
        "(id,opportunity_id,symbol,market_type,direction,execution_mode,status,created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (proposal_id, opportunity_id, "BTC/USDT", "spot", "long", "paper_internal", status, created_at),
    )


def _insert_audit(conn, event_id: str, proposal_id: str, *, actor: str, source: str, timestamp: str) -> None:
    conn.execute(
        "INSERT INTO audit_events "
        "(id,timestamp,actor,source,event_type,entity_type,entity_id,message) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (event_id, timestamp, actor, source, "proposal_rejected", "trade_proposal", proposal_id, f"{actor} rejected"),
    )


def test_opportunity_funnel_uses_iso_safe_causal_cohort_and_marks_stale_snapshot(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(hours=1)).isoformat()
    old = (now - timedelta(hours=25)).isoformat()
    future = (now + timedelta(hours=1)).isoformat()
    for oid, status, created in (
        ("open", "open", recent),
        ("queued", "queued", recent),
        ("reviewed", "reviewed", recent),
        ("expired", "expired", recent),
        ("old", "reviewed", old),
        ("future", "open", future),
    ):
        _insert_opportunity(conn, oid, status=status, created_at=created)
    _insert_proposal(conn, "causal", "reviewed", created_at=recent)
    _insert_proposal(conn, "unrelated", None, created_at=recent)
    _insert_proposal(conn, "old-cohort", "old", created_at=recent)
    stale_at = (now - timedelta(minutes=20)).isoformat()
    conn.execute(
        "INSERT INTO agent_shell_events (id,event_type,payload,created_at) VALUES (?,?,?,?)",
        ("snapshot", "candidate_supply_snapshot", json.dumps({
            "checked_at": stale_at, "executable_count": 2,
            "excluded_candidates_summary": [],
        }), stale_at),
    )
    conn.commit()

    from aletheia.server.routes.analytics import get_opportunity_funnel

    data = get_opportunity_funnel(hours=24)
    assert data["emitted_count"] == 4
    assert data["still_open_count"] == 1
    assert data["queued_count"] == 1
    assert data["reviewed_count"] == 1
    assert data["expired_count"] == 1
    assert data["setup_count"] == 4
    assert data["active_setup_count"] == 2
    assert data["terminal_breakdown"] == {
        "snapshot_superseded": 0,
        "ttl_expired": 0,
        "other_expired": 1,
        "operator_trade": 0,
        "operator_skip": 0,
        "operator_watch": 0,
        "deterministic_review": 0,
        "other_reviewed": 1,
    }
    assert data["proposed_count"] == 1
    assert data["snapshot"]["freshness"] == "stale"
    assert data["snapshot"]["age_sec"] >= 19 * 60


def test_opportunity_funnel_separates_generations_setups_and_terminal_exits(
    tmp_path, monkeypatch
):
    conn = _setup_db(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    created_at = (now - timedelta(minutes=10)).isoformat()
    for oid, status in (
        ("btc-superseded", "expired"),
        ("btc-ttl", "expired"),
        ("btc-active", "open"),
        ("eth-skip", "reviewed"),
    ):
        _insert_opportunity(conn, oid, status=status, created_at=created_at)
    for oid in ("btc-superseded", "btc-ttl", "btc-active"):
        conn.execute(
            "UPDATE opportunities SET symbol='BTC/USDT',market='usdt_futures',"
            "direction_bias='short' WHERE id=?",
            (oid,),
        )
    conn.execute(
        "UPDATE opportunities SET symbol='ETH/USDT',market='usdt_futures',"
        "direction_bias='long' WHERE id='eth-skip'"
    )
    for oid, filter_name, reason_code in (
        (
            "btc-superseded",
            "universe_snapshot_status",
            "universe_snapshot_superseded",
        ),
        ("btc-ttl", "opportunity_ttl", "ttl_expired"),
    ):
        conn.execute(
            "INSERT INTO filter_decisions "
            "(id,dedup_key,candidate_key,candidate_kind,opportunity_id,symbol,"
            "market_type,direction,filter_name,stage,sequence_no,decision,reason_code,"
            "input_snapshot,parameter_snapshot,filter_version,evaluated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"fact-{oid}",
                f"fact-key-{oid}",
                f"opportunity:{oid}",
                "opportunity",
                oid,
                "BTC/USDT",
                "usdt_futures",
                "short",
                filter_name,
                "opportunity_lifecycle",
                0,
                "rejected",
                reason_code,
                "{}",
                "{}",
                "test-v1",
                created_at,
            ),
        )
    conn.execute(
        "INSERT INTO pi_decisions "
        "(id,created_at,symbol,direction,market_type,action,opportunity_id,signal_source) "
        "VALUES ('eth-final-skip',?,'ETH/USDT','long','usdt_futures','skip',"
        "'eth-skip','market_observer')",
        (created_at,),
    )
    conn.commit()

    from aletheia.server.routes.analytics import get_opportunity_funnel

    data = get_opportunity_funnel(hours=1)
    assert data["emitted_count"] == 4
    assert data["setup_count"] == 2
    assert data["active_setup_count"] == 1
    assert data["expired_count"] == 2
    assert data["terminal_breakdown"] == {
        "snapshot_superseded": 1,
        "ttl_expired": 1,
        "other_expired": 0,
        "operator_trade": 0,
        "operator_skip": 1,
        "operator_watch": 0,
        "deterministic_review": 0,
        "other_reviewed": 0,
    }
    assert data["current"]["counts"]["discovered_generations"] == 4
    assert data["current"]["counts"]["unique_setups"] == 2
    assert data["current"]["counts"]["superseded_generations"] == 1
    assert data["current"]["counts"]["ttl_expired"] == 1
    assert data["current"]["policy_epoch_id"] == (
        "opportunity_carrier_continuity/v1"
    )
    assert data["current"]["ranker"]["carrier_epoch"] == (
        "opportunity_carrier_continuity/v1"
    )


def test_opportunity_funnel_explains_current_operator_defer(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    queued_at = (now - timedelta(minutes=2)).isoformat()
    valid_until = (now + timedelta(minutes=28)).isoformat()
    # This represents a pre-fix observation whose nominal 120-minute defer
    # outlives the source carrier. The read model must report the authoritative
    # Opportunity deadline rather than promising a retry after expiry.
    defer_until = (now + timedelta(minutes=40)).isoformat()

    for oid, symbol, direction in (
        ("near", "NEAR/USDT", "short"),
        ("syn", "SYN/USDT", "long"),
    ):
        _insert_opportunity(conn, oid, status="queued", created_at=queued_at)
        conn.execute(
            "UPDATE opportunities SET symbol=?, direction_bias=?, confidence_score=48, "
            "valid_until=? WHERE id=?",
            (symbol, direction, valid_until, oid),
        )
        conn.execute(
            "INSERT INTO pi_decisions "
            "(id,created_at,symbol,direction,market_type,action,opportunity_id,signal_source) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (f"pending-{oid}", queued_at, symbol, direction, "usdt_futures",
             "signal_pending", oid, "market_observer"),
        )

    event_at = (now - timedelta(minutes=1)).isoformat()
    conn.execute(
        "INSERT INTO operator_evaluation_events "
        "(id,dedup_key,event_kind,trigger,pending_unresolved_total,pending_visible_count,"
        "pending_oldest_created_at,queue_snapshot_json,data_health_json,policy_epoch_id,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "defer-near", "defer-near-key",
            "operator_decision_deferred_unchanged_context", "signal", 2, 2,
            queued_at,
            json.dumps({
                "opportunity_id": "near",
                "defer_reason": "candidate_technical_snapshot_unavailable",
                "defer_until": defer_until,
            }),
            "{}", "epoch-current", event_at,
        ),
    )
    conn.commit()

    from aletheia.server.routes.analytics import get_opportunity_funnel

    data = get_opportunity_funnel(hours=1)
    current = data["current"]

    assert current["state"] == "operator_deferred"
    assert current["primary_stage"] == "system_operator"
    assert current["reason_code"] == "candidate_technical_snapshot_unavailable"
    assert current["next_trigger"] == "feature_refresh"
    assert current["policy_epoch_id"] == "epoch-current"
    assert current["counts"] == {
        "open_positions": 0,
        "open_opportunities": 0,
        "queued_candidates": 2,
        "deferred_candidates": 1,
        "pending_proposals": 0,
        "discovered_generations": 2,
        "unique_setups": 2,
        "active_setups": 2,
        "superseded_generations": 0,
        "ttl_expired": 0,
        # Layer-1 observability fields (see aletheia.analytics.defer_classifier
        # and aletheia.diagnostics.pipeline_progress._active_defer_stats). The
        # fixture inserts a single live candidate_specific defer with a 60s
        # oldest age and no other operator events, so defer_rate=1.0 and
        # queue_capacity mirrors paper_trader._PENDING_SIGNAL_QUEUE_CAPACITY=5.
        "market_quiet_defers": 0,
        "candidate_specific_defers": 1,
        "defer_rate": 1.0,
        "oldest_defer_age_seconds": 60,
        "queue_capacity": 5,
    }
    candidates = {row["symbol"]: row for row in current["candidates"]}
    assert set(candidates) == {"NEAR/USDT", "SYN/USDT"}
    assert candidates["NEAR/USDT"]["status"] == "deferred"
    assert candidates["NEAR/USDT"]["reason_code"] == (
        "candidate_technical_snapshot_unavailable"
    )
    assert candidates["NEAR/USDT"]["expires_in_sec"] > 0
    assert candidates["NEAR/USDT"]["defer_until"] == valid_until
    assert candidates["NEAR/USDT"]["retry_at"] == valid_until
    assert 0 < candidates["NEAR/USDT"]["retry_in_sec"] <= 28 * 60
    assert candidates["SYN/USDT"]["status"] == "queued"
    assert [row["state"] for row in current["pipeline"]] == [
        "active", "passed", "waiting", "not_reached", "not_reached", "not_reached",
    ]


def test_opportunity_funnel_marks_completed_entry_stages_for_existing_position(
    tmp_path, monkeypatch
):
    conn = _setup_db(tmp_path, monkeypatch)
    conn.execute(
        "INSERT INTO positions "
        "(id,symbol,market_type,direction,quantity,entry_price,execution_mode,status) "
        "VALUES ('position-1','BTC/USDT','usdt_futures','long',1,100,"
        "'paper_internal','open')"
    )
    conn.commit()

    from aletheia.server.routes.analytics import get_opportunity_funnel

    current = get_opportunity_funnel(hours=1)["current"]

    assert current["state"] == "position_open"
    assert current["counts"]["open_positions"] == 1
    stages = {row["stage"]: row["state"] for row in current["pipeline"]}
    assert stages["proposal"] == "reached"
    assert stages["risk"] == "reached"
    assert stages["execution"] == "position_open"


def test_opportunity_funnel_explains_ranker_cooldown_from_durable_filter_facts(
    tmp_path, monkeypatch
):
    conn = _setup_db(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    created_at = (now - timedelta(minutes=2)).isoformat()
    ranked_at = (now - timedelta(minutes=1)).isoformat()
    prior_signal_at = (now - timedelta(minutes=20)).isoformat()
    valid_until = (now + timedelta(minutes=28)).isoformat()
    _insert_opportunity(conn, "cooldown", status="open", created_at=created_at)
    conn.execute(
        "UPDATE opportunities SET symbol='NEAR/USDT',direction_bias='short',"
        "confidence_score=48,valid_until=?,last_ranked_at=? WHERE id='cooldown'",
        (valid_until, ranked_at),
    )
    conn.execute(
        "INSERT INTO pi_decisions "
        "(id,created_at,symbol,direction,market_type,action,opportunity_id,signal_source) "
        "VALUES ('prior-signal',?,'NEAR/USDT','short','usdt_futures','signal_pending',"
        "'older-opportunity','market_observer')",
        (prior_signal_at,),
    )
    conn.execute(
        "INSERT INTO pi_decisions "
        "(id,created_at,symbol,direction,market_type,action,opportunity_id,signal_source) "
        "VALUES ('prior-final',?,'NEAR/USDT','short','usdt_futures','skip',"
        "'older-opportunity','market_observer')",
        ((now - timedelta(minutes=19)).isoformat(),),
    )
    filter_names = [
        "execution_eligibility",
        "direction_present",
        "ranker_confidence_gate",
        "universe_execution_eligibility",
        "family_ttl",
        "factor_hard_block",
        "guard_candidate_block",
    ]
    for sequence, filter_name in enumerate(filter_names, start=1):
        conn.execute(
            "INSERT INTO filter_decisions "
            "(id,dedup_key,candidate_key,candidate_kind,run_id,opportunity_id,symbol,"
            "market_type,direction,filter_name,stage,sequence_no,decision,reason_code,"
            "input_snapshot,parameter_snapshot,filter_version,evaluated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"pass-{sequence}", f"pass-key-{sequence}", "opportunity:cooldown",
                "opportunity", "ranker-pass-run", "cooldown", "NEAR/USDT",
                "usdt_futures", "short", filter_name, "paper_ranker", sequence,
                "passed", f"{filter_name}_passed", "{}", "{}", "ranker-v1", ranked_at,
            ),
        )
    conn.execute(
        "INSERT INTO filter_decisions "
        "(id,dedup_key,candidate_key,candidate_kind,run_id,opportunity_id,symbol,"
        "market_type,direction,filter_name,stage,sequence_no,decision,reason_code,"
        "input_snapshot,parameter_snapshot,filter_version,evaluated_at) "
        "VALUES ('cooldown-filter','cooldown-filter-key','opportunity:cooldown',"
        "'opportunity','ranker-run','cooldown','NEAR/USDT','usdt_futures','short',"
        "'signal_cooldown','paper_trader_selection',1,'rejected','signal_cooldown',"
        "'{}','{\"cooldown_minutes\":30}','ranker-v1',?)",
        (ranked_at,),
    )
    conn.commit()

    from aletheia.server.routes.analytics import get_opportunity_funnel

    current = get_opportunity_funnel(hours=1)["current"]

    assert current["state"] == "ranker_cooldown"
    assert current["primary_stage"] == "ranker_selection"
    assert current["reason_code"] == "signal_cooldown"
    assert current["next_trigger"] == "cooldown_expiry"
    assert current["ranker"]["counts"] == {
        "open": 1,
        "not_evaluated": 0,
        "cooldown": 1,
        "backpressure": 0,
        "batch_limit": 0,
        "carrier_active": 0,
        "other_wait": 0,
    }
    candidate = current["candidates"][0]
    assert candidate["status"] == "ranker_cooldown"
    assert candidate["stage"] == "ranker_selection"
    assert candidate["blocking_filter"] == "signal_cooldown"
    assert candidate["passed_filter_count"] == 7
    assert candidate["latest_ranker_run_id"] == "ranker-run"
    assert candidate["retry_at"] is not None
    assert 8 * 60 < candidate["retry_in_sec"] < 12 * 60


def test_opportunity_funnel_distinguishes_not_yet_ranked_open_opportunity(
    tmp_path, monkeypatch
):
    conn = _setup_db(tmp_path, monkeypatch)
    created_at = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    _insert_opportunity(conn, "fresh", status="open", created_at=created_at)
    conn.commit()

    from aletheia.server.routes.analytics import get_opportunity_funnel

    current = get_opportunity_funnel(hours=1)["current"]

    assert current["state"] == "ranker_pending"
    assert current["reason_code"] == "ranker_not_yet_evaluated"
    assert current["next_trigger"] == "ranker_wake"
    assert current["candidates"][0]["status"] == "ranker_pending"
    assert current["candidates"][0]["blocking_filter"] is None


@pytest.mark.parametrize(
    ("blocking_filter", "expected_state", "expected_trigger"),
    [
        ("signal_queue_capacity", "ranker_backpressure", "operator_queue_slot"),
        ("signal_batch_limit", "ranker_batch_limit", "next_ranker_run"),
        ("guard_candidate_block", "ranker_filter_wait", "ranker_fact_change"),
    ],
)
def test_opportunity_funnel_preserves_adjacent_ranker_blockers(
    tmp_path,
    monkeypatch,
    blocking_filter,
    expected_state,
    expected_trigger,
):
    conn = _setup_db(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    created_at = (now - timedelta(minutes=2)).isoformat()
    ranked_at = (now - timedelta(minutes=1)).isoformat()
    _insert_opportunity(conn, "adjacent", status="open", created_at=created_at)
    conn.execute(
        "UPDATE opportunities SET last_ranked_at=? WHERE id='adjacent'",
        (ranked_at,),
    )
    conn.execute(
        "INSERT INTO filter_decisions "
        "(id,dedup_key,candidate_key,candidate_kind,run_id,opportunity_id,symbol,"
        "market_type,direction,filter_name,stage,sequence_no,decision,reason_code,"
        "input_snapshot,parameter_snapshot,filter_version,evaluated_at) "
        "VALUES ('adjacent-filter','adjacent-key','opportunity:adjacent','opportunity',"
        "'ranker-adjacent','adjacent','BTC/USDT','spot','long',?,?,1,'rejected',?,"
        "'{}','{}','ranker-v1',?)",
        (
            blocking_filter,
            "paper_trader_selection"
            if blocking_filter.startswith("signal_")
            else "paper_ranker",
            blocking_filter,
            ranked_at,
        ),
    )
    conn.commit()

    from aletheia.server.routes.analytics import get_opportunity_funnel

    current = get_opportunity_funnel(hours=1)["current"]

    assert current["state"] == expected_state
    assert current["reason_code"] == blocking_filter
    assert current["next_trigger"] == expected_trigger
    assert current["candidates"][0]["blocking_filter"] == blocking_filter


def test_operator_decisions_classifies_unknown_uses_rejection_time_and_counts_full_window(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(minutes=5)).isoformat()
    old = (now - timedelta(days=2)).isoformat()
    for oid in ("user-opp", "auto-opp", "unknown-opp", "skip-opp"):
        _insert_opportunity(conn, oid, status="reviewed", created_at=old)
    _insert_proposal(conn, "user-prop", "user-opp", created_at=old, status="rejected")
    _insert_proposal(conn, "retry-prop", "user-opp", created_at=recent)
    _insert_proposal(conn, "auto-prop", "auto-opp", created_at=recent, status="rejected")
    _insert_proposal(conn, "unknown-prop", "unknown-opp", created_at=recent, status="rejected")
    _insert_audit(conn, "audit-user", "user-prop", actor="human", source="dashboard", timestamp=recent)
    _insert_audit(conn, "audit-auto", "auto-prop", actor="system", source="execution_gateway", timestamp=recent)
    conn.execute(
        "INSERT INTO pi_decisions "
        "(id,created_at,symbol,direction,market_type,action,opportunity_id,proposal_id,signal_source) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("trade-intent", recent, "BTC/USDT", "long", "spot", "trade", "user-opp", "user-prop", "market_observer"),
    )
    conn.execute(
        "INSERT INTO pi_decisions "
        "(id,created_at,symbol,direction,market_type,action,opportunity_id,proposal_id,signal_source) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("retry-trade", recent, "BTC/USDT", "long", "spot", "trade", "user-opp", "retry-prop", "market_observer"),
    )
    conn.execute(
        "INSERT INTO pi_decisions "
        "(id,created_at,symbol,direction,market_type,action,opportunity_id,signal_source) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("skip", recent, "ETH/USDT", "short", "spot", "skip", "skip-opp", "market_observer"),
    )
    conn.execute(
        "INSERT INTO pi_decisions "
        "(id,created_at,symbol,direction,market_type,action,opportunity_id,signal_source) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("old-skip", old, "OLD/USDT", "short", "spot", "skip", "old", "market_observer"),
    )
    conn.commit()

    from aletheia.server.routes.analytics import get_operator_decisions

    data = get_operator_decisions(hours=24, limit=2)
    assert data["counts"] == {
        "trade": 1,
        "operator_skip": 1,
        "operator_watch": 0,
        "user_reject": 1,
        "auto_reject": 1,
        "reject_unknown": 1,
    }
    assert len(data["items"]) == 2
    assert data["total_count"] == 5
    assert "trade-intent" not in {item.get("decision_id") for item in data["items"]}
    assert data["counts"]["user_reject"] == 1  # old proposal, recent rejection audit


def test_operator_decisions_preserve_history_with_policy_and_runtime_attribution(
    tmp_path, monkeypatch
):
    conn = _setup_db(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    old_start = (now - timedelta(minutes=55)).isoformat()
    old_end = (now - timedelta(minutes=25)).isoformat()
    current_start = (now - timedelta(minutes=20)).isoformat()
    old_decided = (now - timedelta(minutes=40)).isoformat()
    current_decided = (now - timedelta(minutes=5)).isoformat()

    for epoch_id, started_at, ended_at in (
        ("policy-old", old_start, old_end),
        ("policy-current", current_start, None),
    ):
        conn.execute(
            "INSERT INTO learning_policy_epochs "
            "(epoch_id,epoch_kind,effective_values_hash,effective_values_json,"
            "affected_path_ids_json,started_at,ended_at,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                epoch_id,
                "system_operator_skip_remediation",
                f"hash-{epoch_id}",
                "{}",
                "[]",
                started_at,
                ended_at,
                started_at,
            ),
        )
    for session_id, runtime_id, commit, started_at, ended_at in (
        ("session-current", "runtime-local-current", "commit-current", current_start, None),
        ("session-old", "runtime-old", "commit-old", old_start, old_end),
    ):
        conn.execute(
            "INSERT INTO runtime_sessions "
            "(session_id,runtime_id,boot_commit,started_at,ended_at,shutdown_clean) "
            "VALUES (?,?,?,?,?,?)",
            (session_id, runtime_id, commit, started_at, ended_at, int(bool(ended_at))),
        )
    for opportunity_id, created_at in (
        ("current-opp", current_decided),
        ("old-opp", old_decided),
    ):
        _insert_opportunity(
            conn,
            opportunity_id,
            status="reviewed",
            created_at=created_at,
        )
    conn.execute(
        "INSERT INTO pi_decisions "
        "(id,created_at,symbol,direction,market_type,action,opportunity_id,"
        "signal_source,evidence_manifest_json,runtime_session_id,producer_commit) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "decision-current",
            current_decided,
            "BTC/USDT",
            "long",
            "usdt_futures",
            "trade",
            "current-opp",
            "market_observer",
            json.dumps({"skip_policy_epoch_id": "policy-current"}),
            "session-current",
            "commit-current",
        ),
    )
    # No manifest on this legacy row: attribution must use the durable epoch window.
    conn.execute(
        "INSERT INTO pi_decisions "
        "(id,created_at,symbol,direction,market_type,action,opportunity_id,"
        "signal_source,runtime_session_id,producer_commit) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "decision-old",
            old_decided,
            "ETH/USDT",
            "short",
            "usdt_futures",
            "skip",
            "old-opp",
            "market_observer",
            "session-old",
            "commit-old",
        ),
    )
    conn.commit()
    monkeypatch.setattr(
        "aletheia.agent_runtime.runtime_session.current_session",
        lambda: {
            "session_id": "session-current",
            "runtime_id": "runtime-local-current",
            "boot_commit": "commit-current",
        },
    )

    from aletheia.server.routes.analytics import get_operator_decisions

    data = get_operator_decisions(hours=1, limit=20)
    items = {item["decision_id"]: item for item in data["items"]}
    assert items["decision-current"]["attribution"] == {
        "policy_epoch_id": "policy-current",
        "policy_basis": "decision_manifest",
        "policy_relation": "current",
        "runtime_session_id": "session-current",
        "runtime_id": "runtime-local-current",
        "producer_commit": "commit-current",
        "runtime_relation": "current_session",
    }
    assert items["decision-old"]["attribution"]["policy_epoch_id"] == "policy-old"
    assert items["decision-old"]["attribution"]["policy_basis"] == "epoch_time_window"
    assert items["decision-old"]["attribution"]["policy_relation"] == "historical"
    assert items["decision-old"]["attribution"]["runtime_relation"] == "historical_runtime"
    assert data["current_context"]["policy_epoch_id"] == "policy-current"
    assert data["current_policy_counts"]["trade"] == 1
    assert data["historical_policy_counts"]["operator_skip"] == 1
