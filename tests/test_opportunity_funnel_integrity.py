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
) -> None:
    conn.execute(
        "INSERT INTO opportunities "
        "(id,symbol,market,direction_bias,confidence,status,asset_class,scanner_family,"
        "universe_snapshot_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            opportunity_id, f"{opportunity_id}/USDT", "spot", "long", "high",
            status, "crypto", "momentum", snapshot_id,
            created_at or datetime.now(timezone.utc).isoformat(),
        ),
    )


def _insert_pending(conn, decision_id: str, opportunity_id: str, created_at: str) -> None:
    conn.execute(
        "INSERT INTO pi_decisions "
        "(id,created_at,symbol,direction,market_type,action,opportunity_id,signal_source) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (decision_id, created_at, "BTC/USDT", "long", "spot", "signal_pending", opportunity_id, "market_observer"),
    )


def test_superseded_cascade_expires_open_and_queued_with_atomic_evidence(tmp_path, monkeypatch):
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

    assert repo.expire_opportunities_from_superseded_snapshots("crypto") == 2
    assert repo.expire_opportunities_from_superseded_snapshots("crypto") == 0
    statuses = dict(conn.execute("SELECT id,status FROM opportunities"))
    assert statuses == {
        "open-old": "expired",
        "queued-old": "expired",
        "reviewed-old": "reviewed",
        "open-active": "open",
    }
    evidence = conn.execute(
        "SELECT opportunity_id,decision,reason_code FROM filter_decisions "
        "WHERE filter_name='universe_snapshot_status' ORDER BY opportunity_id"
    ).fetchall()
    assert [tuple(row) for row in evidence] == [
        ("open-old", "rejected", "universe_snapshot_superseded"),
        ("queued-old", "rejected", "universe_snapshot_superseded"),
    ]


def test_superseded_queued_carrier_is_immediately_hidden_from_operator(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    _insert_snapshot(conn, "old", "superseded")
    _insert_opportunity(conn, "queued-old", status="queued", snapshot_id="old")
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

    assert repo.expire_opportunities_from_superseded_snapshots("crypto") == 1
    visible, queue = _build_pending_signal_queue()
    assert visible == []
    assert queue["unresolved_total"] == 0


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
    assert data["proposed_count"] == 1
    assert data["snapshot"]["freshness"] == "stale"
    assert data["snapshot"]["age_sec"] >= 19 * 60


def test_opportunity_funnel_explains_current_operator_defer(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    queued_at = (now - timedelta(minutes=2)).isoformat()
    valid_until = (now + timedelta(minutes=28)).isoformat()
    defer_until = (now + timedelta(minutes=8)).isoformat()

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
    }
    candidates = {row["symbol"]: row for row in current["candidates"]}
    assert set(candidates) == {"NEAR/USDT", "SYN/USDT"}
    assert candidates["NEAR/USDT"]["status"] == "deferred"
    assert candidates["NEAR/USDT"]["reason_code"] == (
        "candidate_technical_snapshot_unavailable"
    )
    assert candidates["NEAR/USDT"]["expires_in_sec"] > 0
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
