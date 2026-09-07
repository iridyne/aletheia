"""Regression coverage for the Opportunity Funnel lifecycle and honest read models."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3

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




def test_current_funnel_ignores_legacy_pending_but_preserves_history(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    from aletheia.server.routes.analytics import get_opportunity_funnel

    now = datetime.now(timezone.utc).isoformat()
    _insert_opportunity(conn, "historical", status="queued", created_at=now)
    _insert_pending(conn, "pending-old", "historical", now)
    conn.commit()
    data = get_opportunity_funnel(hours=1)
    assert data["emitted_count"] == 1
    assert data["current"]["summary"]["needs_planning"] == 0
    assert data["current"]["candidate_attention"] == []
    assert data["current"]["entry_plans"] == []
    assert conn.execute("SELECT action FROM pi_decisions WHERE id='pending-old'").fetchone()[0] == "signal_pending"


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


def test_reused_snapshot_renews_candidate_without_rewriting_old_carrier(
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

    refresh_active_snapshot_ttls("active", now)
    assert conn.execute(
        "SELECT expires_at FROM universe_members WHERE id='member'"
    ).fetchone()[0] == (now + timedelta(hours=1)).isoformat()
    assert conn.execute(
        "SELECT valid_until FROM opportunities WHERE id='queued-revalidated'"
    ).fetchone()[0] == old_expiry






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







def test_expired_opportunity_cannot_transition_to_reviewed(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    _insert_opportunity(conn, "expired", status="expired")
    conn.commit()

    from aletheia.storage.repositories import transition_opportunity_to_reviewed

    assert transition_opportunity_to_reviewed("expired") is False
    assert conn.execute(
        "SELECT status FROM opportunities WHERE id='expired'"
    ).fetchone()[0] == "expired"




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
        "portfolio_guard_reject": 0,
        "user_reject": 1,
        "auto_reject": 1,
        "reject_unknown": 1,
    }
    assert len(data["items"]) == 2
    assert data["total_count"] == 5
    assert "trade-intent" not in {item.get("decision_id") for item in data["items"]}
    assert data["counts"]["user_reject"] == 1  # old proposal, recent rejection audit


def test_operator_decisions_excludes_non_terminal_deferrals_but_trace_preserves_identity(
    tmp_path, monkeypatch
):
    conn = _setup_db(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(minutes=10)).isoformat()
    old = (now - timedelta(days=2)).isoformat()
    _insert_opportunity(conn, "defer-opp", status="open", created_at=recent)
    for index in range(3):
        conn.execute(
            "INSERT INTO operator_evaluation_events "
            "(id,dedup_key,event_kind,trigger,pending_unresolved_total,"
            "pending_visible_count,queue_snapshot_json,data_health_json,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                f"defer-evt-{index + 1}", f"dk-{index + 1}",
                "operator_decision_deferred_low_conviction",
                "scheduled", 1, 1,
                json.dumps({
                    "opportunity_id": "defer-opp",
                    "defer_reason": "operator_low_conviction",
                    "defer_until": "2099-01-01T00:00:00+00:00",
                }),
                "{}",
                recent,
            ),
        )
    # Old event outside window — should NOT appear
    _insert_opportunity(conn, "old-defer-opp", status="open", created_at=old)
    conn.execute(
        "INSERT INTO operator_evaluation_events "
        "(id,dedup_key,event_kind,trigger,pending_unresolved_total,"
        "pending_visible_count,queue_snapshot_json,data_health_json,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "defer-evt-old", "dk-old",
            "operator_decision_deferred_low_conviction",
            "scheduled", 1, 1,
            json.dumps({"opportunity_id": "old-defer-opp", "defer_reason": "operator_low_conviction"}),
            "{}",
            old,
        ),
    )
    conn.commit()

    from aletheia.server.routes.analytics import get_operator_decisions
    from aletheia.analytics.opportunity_flow import trace_opportunity_flow

    data = get_operator_decisions(hours=24, limit=50)
    assert data["counts"] == {
        "trade": 0,
        "operator_skip": 0,
        "operator_watch": 0,
        "portfolio_guard_reject": 0,
        "user_reject": 0,
        "auto_reject": 0,
        "reject_unknown": 0,
    }
    assert data["total_count"] == 0
    assert data["matched_count"] == 0
    assert data["items"] == []
    filtered = get_operator_decisions(hours=24, limit=50, outcome="conviction_deferred")
    assert filtered["matched_count"] == 0
    assert filtered["items"] == []

    trace = trace_opportunity_flow("defer-opp")
    assert trace["opportunity_id"] == "defer-opp"
    assert trace["symbol"] == "defer-opp/USDT"
    assert trace["direction"] == "long"
    assert trace["market"] == "spot"
    deferred = [event for event in trace["timeline"] if event["stage"] == "operator_deferred"]
    assert len(deferred) == 3
    assert {event["details"]["event_id"] for event in deferred} == {
        "defer-evt-1", "defer-evt-2", "defer-evt-3",
    }
    assert all("decision_id" not in event["details"] for event in deferred)

    conn.execute(
        "INSERT INTO pi_decisions "
        "(id,created_at,symbol,direction,market_type,action,opportunity_id,signal_source) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            "terminal-skip", recent, "defer-opp/USDT", "long", "spot", "skip",
            "defer-opp", "market_observer",
        ),
    )
    conn.commit()

    data = get_operator_decisions(hours=24, limit=50)
    assert data["total_count"] == 1
    assert data["counts"]["operator_skip"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["decision_id"] == "terminal-skip"
    assert all(item["outcome"] != "conviction_deferred" for item in data["items"])

    trace = trace_opportunity_flow("defer-opp")
    assert len([event for event in trace["timeline"] if event["stage"] == "operator_deferred"]) == 3
    assert any(
        event["stage"] == "operator_skip" and event["details"]["decision_id"] == "terminal-skip"
        for event in trace["timeline"]
    )


def test_operator_decisions_terminal_taxonomy_and_filters_stay_in_sync(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc).isoformat()

    for decision_id, action, symbol in (
        ("terminal-trade", "trade", "BTC/USDT"),
        ("terminal-skip", "skip", "ETH/USDT"),
        ("terminal-watch", "watch", "SOL/USDT"),
        ("terminal-guard", "portfolio_guard_skip", "XRP/USDT"),
    ):
        conn.execute(
            "INSERT INTO pi_decisions "
            "(id,created_at,symbol,direction,market_type,action,opportunity_id,signal_source) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                decision_id, now, symbol, "long", "spot", action,
                decision_id, "market_observer",
            ),
        )

    for outcome, proposal_id, actor, source in (
        ("user_reject", "terminal-user-reject", "human", "dashboard"),
        ("auto_reject", "terminal-auto-reject", "system", "execution_gateway"),
        ("reject_unknown", "terminal-unknown-reject", "worker", "unknown"),
    ):
        _insert_proposal(conn, proposal_id, proposal_id, created_at=now, status="rejected")
        _insert_audit(
            conn, f"audit-{proposal_id}", proposal_id,
            actor=actor, source=source, timestamp=now,
        )
    conn.commit()

    from aletheia.server.routes.analytics import get_operator_decisions

    terminal_outcomes = (
        "trade", "operator_skip", "operator_watch", "portfolio_guard_reject",
        "user_reject", "auto_reject", "reject_unknown",
    )
    data = get_operator_decisions(hours=24, limit=50)
    assert set(data["counts"]) == set(terminal_outcomes)
    assert data["total_count"] == len(terminal_outcomes)
    assert all(data["counts"][outcome] == 1 for outcome in terminal_outcomes)

    for outcome in terminal_outcomes:
        filtered = get_operator_decisions(hours=24, limit=50, outcome=outcome)
        assert filtered["total_count"] == len(terminal_outcomes)
        assert filtered["matched_count"] == 1
        assert len(filtered["items"]) == 1
        assert filtered["items"][0]["outcome"] == outcome

    unsupported = get_operator_decisions(
        hours=24, limit=50, outcome="conviction_deferred"
    )
    assert unsupported["total_count"] == len(terminal_outcomes)
    assert unsupported["matched_count"] == 0
    assert unsupported["items"] == []


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
