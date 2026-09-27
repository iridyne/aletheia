"""Tests for data retention / cleanup (Component 3).

Invariant: NEVER hard-delete trust-bearing records (audit, trade_proposals,
risk_decisions, orders, fills, positions, knowledge_items). Only high-volume
telemetry is pruned. JSONL rotates at a size cap without losing content.
"""

from tests.fixtures.repo_test_helpers import prune_universe_members

import sqlite3
from datetime import datetime, timezone, timedelta

import pytest

pytestmark = pytest.mark.usefixtures("observation_readers")
from pydantic import ValidationError

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    return conn


def _old(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _recent():
    return datetime.now(timezone.utc).isoformat()


def test_usage_retention_defaults_are_90_days():
    from aletheia.config.schema import RetentionConfig

    retention = RetentionConfig()
    assert retention.agent_runs_days == 90
    assert retention.tool_calls_days == 90
    assert retention.runtime_sessions_days == 90


def test_retention_config_rejects_nonpositive_cleanup_windows():
    from aletheia.config.schema import RetentionConfig

    cleanup_fields = RetentionConfig.model_fields.keys() - {"app_db_capacity_mb"}
    for field in cleanup_fields:
        for value in (0, -1):
            with pytest.raises(ValidationError, match=field):
                RetentionConfig.model_validate({field: value})


def test_retention_settings_usage_windows_are_limited_to_90_days(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from aletheia.config.schema import AletheiaConfig
    from aletheia.server.routes import settings

    cfg = AletheiaConfig()
    app = FastAPI()
    app.include_router(settings.router)
    monkeypatch.setattr(settings, "load_config", lambda: cfg)
    monkeypatch.setattr(settings, "save_config", lambda _cfg: None)
    monkeypatch.setattr("aletheia.files.jsonl.write_audit", lambda _event: None)
    client = TestClient(app)

    for field in ("agent_runs_days", "tool_calls_days", "runtime_sessions_days"):
        assert client.post("/api/settings/retention", json={field: 90}).status_code == 200
        assert client.post("/api/settings/retention", json={field: 91}).status_code == 400
        assert client.post("/api/settings/retention", json={field: 0}).status_code == 400


def _run_cleanup_with_test_config(monkeypatch):
    from aletheia.agent_runtime import scheduler
    from aletheia.config.schema import AletheiaConfig

    monkeypatch.setattr(scheduler, "load_config", lambda: AletheiaConfig())
    scheduler._run_cleanup()


def test_scheduler_cleanup_failure_keeps_shared_health_and_releases_db(monkeypatch):
    from unittest.mock import Mock

    from aletheia.agent_runtime import scheduler
    from aletheia.config.schema import AletheiaConfig
    from aletheia.infra import events
    from aletheia.storage.sqlite import get_db

    monkeypatch.setattr(scheduler, "load_config", lambda: AletheiaConfig())
    monkeypatch.setattr(scheduler, "_consecutive_failures", {})
    monkeypatch.setattr(scheduler, "_learning_job_failed", None)
    emitted = Mock()
    monkeypatch.setattr(events, "emit_infra_event", emitted)

    def fail_prune(_days):
        get_db().execute("BEGIN")
        raise RuntimeError("retention unavailable")

    monkeypatch.setattr(repo, "prune_run_steps", fail_prune)
    for count in range(1, 4):
        connection = get_db()
        scheduler._guarded("cleanup", scheduler._run_cleanup)()
        assert scheduler._consecutive_failures["cleanup"] == count
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")
    assert any(call.kwargs["code"] == "job_consecutive_failure:cleanup" for call in emitted.call_args_list)
    assert scheduler._guarded("following_job", lambda: "completed")() == "completed"


def test_cleanup_catchup_due_uses_last_successful_audit(tmp_path, monkeypatch):
    conn = setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import scheduler

    assert scheduler._cleanup_catchup_due() is True
    conn.execute(
        "INSERT INTO audit_events "
        "(id, actor, source, event_type, entity_type, entity_id, timestamp) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            "cleanup-recent", "system", "scheduler", "data_pruned", "telemetry", None,
            "2026-08-05T19:17:00+00:00",
        ),
    )
    conn.commit()
    before_next_fire = datetime.fromisoformat("2026-08-06T18:00:00+00:00")
    assert scheduler._cleanup_catchup_due(now=before_next_fire) is False
    conn.execute(
        "UPDATE audit_events SET timestamp=? WHERE id='cleanup-recent'",
        ("2026-08-04T19:17:00+00:00",),
    )
    conn.commit()
    assert scheduler._cleanup_catchup_due(now=before_next_fire) is True


def test_cleanup_catchup_due_detects_just_missed_local_cron(tmp_path, monkeypatch):
    conn = setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import scheduler

    conn.execute(
        "INSERT INTO audit_events "
        "(id, actor, source, event_type, entity_type, entity_id, timestamp) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            "cleanup-yesterday",
            "system",
            "scheduler",
            "data_pruned",
            "telemetry",
            None,
            "2026-08-05T19:17:00+00:00",
        ),
    )
    conn.commit()

    # 03:18 Shanghai: today's scheduled fire is one minute overdue even though
    # the previous success is only 24h01m old.
    assert scheduler._cleanup_catchup_due(
        now=datetime.fromisoformat("2026-08-06T19:18:00+00:00")
    ) is True
    # 03:16 Shanghai: yesterday's 03:17 is still the most recent scheduled fire.
    assert scheduler._cleanup_catchup_due(
        now=datetime.fromisoformat("2026-08-06T19:16:00+00:00")
    ) is False


def test_prune_agent_runs_keeps_recent_deletes_old(tmp_path, monkeypatch):
    conn = setup_env(tmp_path, monkeypatch)
    conn.execute("INSERT INTO agent_runs (id, agent_name, permission_mode, started_at, status) VALUES (?,?,?,?,?)",
                 ("old1", "market_observer", "Observe", _old(91), "completed"))
    conn.execute("INSERT INTO agent_runs (id, agent_name, permission_mode, started_at, status) VALUES (?,?,?,?,?)",
                 ("new1", "market_observer", "Observe", _old(89), "completed"))
    conn.execute(
        "INSERT INTO agent_run_artifacts (run_id, artifact_type, artifact_id) VALUES (?,?,?)",
        ("old1", "opportunity", "old-opportunity"),
    )
    conn.commit()

    deleted = repo.prune_agent_runs()
    assert deleted == 1
    ids = {r["id"] for r in repo.list_agent_runs(limit=100)}
    assert "new1" in ids and "old1" not in ids
    assert conn.execute("SELECT 1 FROM agent_run_artifacts WHERE run_id='old1'").fetchone() is None


def test_prune_market_snapshots(tmp_path, monkeypatch):
    conn = setup_env(tmp_path, monkeypatch)
    conn.execute("INSERT INTO market_snapshots (id, symbol, market_type, price, snapshot_at) VALUES (?,?,?,?,?)",
                 ("o", "BTCUSDT", "spot", 1.0, _old(60)))
    conn.execute("INSERT INTO market_snapshots (id, symbol, market_type, price, snapshot_at) VALUES (?,?,?,?,?)",
                 ("n", "BTCUSDT", "spot", 1.0, _recent()))
    conn.commit()
    assert repo.prune_market_snapshots(days=30) == 1


def test_prune_universe_forward_outcomes(tmp_path, monkeypatch):
    """Unreferenced labels prune at 30d; active-experiment fallbacks are guarded."""
    conn = setup_env(tmp_path, monkeypatch)
    cols = ("(id, member_id, snapshot_id, symbol, horizon, "
            "entry_price_usd, entry_ts, due_at, status)")
    conn.execute(f"INSERT INTO universe_forward_outcomes {cols} VALUES (?,?,?,?,?,?,?,?,?)",
                 ("o", "m1", "s1", "BTC/USDT", "1d", 1.0, _old(60), _old(59), "settled"))
    conn.execute(f"INSERT INTO universe_forward_outcomes {cols} VALUES (?,?,?,?,?,?,?,?,?)",
                 ("n", "m1", "s2", "BTC/USDT", "1d", 1.0, _recent(), _recent(), "pending"))
    conn.commit()
    assert repo.prune_universe_forward_outcomes(days=30) == 1
    remaining = {r["id"] for r in conn.execute("SELECT id FROM universe_forward_outcomes").fetchall()}
    assert remaining == {"n"}, "prune must drop only outcomes older than the window"


def test_prune_universe_forward_outcomes_keeps_open_experiment_fallback(tmp_path, monkeypatch):
    conn = setup_env(tmp_path, monkeypatch)
    conn.execute("UPDATE observation_authority_state SET authority_source='legacy'")
    from extensions.learning.models import LearningExperiment, LearningExposure, LearningHypothesis

    hypothesis = LearningHypothesis(
        hypothesis_id="retention-hypothesis",
        fingerprint="retention-hypothesis",
        cohort_id="test",
        path_id="universe_family_calibration",
        overlap_group="universe",
        source_type="test",
        scope_json="{}",
        action_json="{}",
        consumer="universe_allocator",
        target_horizon="4h",
    )
    experiment = LearningExperiment(
        experiment_id="retention-experiment",
        hypothesis_id=hypothesis.hypothesis_id,
        assignment_salt="test", status="active",
    )
    exposure = LearningExposure(
        dedup_key="retention-exposure",
        experiment_id=experiment.experiment_id,
        hypothesis_id=hypothesis.hypothesis_id,
        assignment="control",
        consumer="universe_allocator",
        consumer_stage="opportunity_score",
        path_id=hypothesis.path_id,
        overlap_group="universe",
        assignment_unit_key="run-1",
        candidate_key="candidate-1",
        outcome_candidate_key="candidate-1:4h",
        outcome_source="fixed_horizon_candidate",
        concurrent_policy_epoch="epoch-a",
        exposed_at=_old(70),
    )
    for table, record in (("learning_hypotheses", hypothesis), ("learning_experiments", experiment), ("learning_exposures", exposure)):
        values = record.model_dump()
        conn.execute(
            f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
            tuple(values.values()),
        )
    conn.execute(
        "INSERT INTO universe_registry (id, venue, symbol, market_type) VALUES (?,?,?,?)",
        ("asset-1", "test", "BTC/USDT", "perp"),
    )
    conn.execute(
        "INSERT INTO universe_snapshots (id, created_at, market_type, status) "
        "VALUES (?,?,?,?)",
        ("s1", _old(60), "crypto", "expired"),
    )
    conn.execute(
        "INSERT INTO universe_members "
        "(id, snapshot_id, asset_id, symbol, rank, total_score, expires_at) "
        "VALUES (?,?,?,?,?,?,?)",
        ("m1", "s1", "asset-1", "BTC/USDT", 1, 1.0, _old(59)),
    )
    columns = (
        "(id, member_id, snapshot_id, symbol, horizon, outcome_candidate_key, "
        "entry_price_usd, entry_ts, due_at, status)"
    )
    conn.execute(
        f"INSERT INTO universe_forward_outcomes {columns} VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "fallback", "m1", "s1", "BTC/USDT", "4h", "candidate-1:4h",
            1.0, _old(60), _old(59), "settled",
        ),
    )
    conn.commit()

    assert repo.prune_universe_forward_outcomes(days=30) == 0
    assert prune_universe_members(days=30) == 0
    assert repo.prune_universe_snapshots(days=30) == 0
    assert conn.execute(
        "SELECT 1 FROM universe_forward_outcomes WHERE id='fallback'"
    ).fetchone() is not None
    assert conn.execute(
        "SELECT 1 FROM universe_snapshots WHERE id='s1'"
    ).fetchone() is not None


def _seed_experiment_guarded_retention_rows(conn):
    from aletheia.storage.models import FilterCandidateOutcome

    repo.insert_filter_candidate_outcomes_batch([FilterCandidateOutcome(
        id="guarded-filter-outcome",
        outcome_candidate_key="guarded-candidate",
        venue="okx",
        symbol="BTC/USDT",
        direction="long",
        horizon="24h",
        entry_price_usd=100.0,
        entry_ts=_old(60),
        due_at=_old(59),
    )])
    conn.execute(
        "INSERT INTO universe_registry (id, venue, symbol, market_type) VALUES (?,?,?,?)",
        ("guarded-asset", "test", "BTC/USDT", "perp"),
    )
    conn.execute(
        "INSERT INTO universe_snapshots (id, created_at, market_type, status) "
        "VALUES (?,?,?,?)",
        ("guarded-snapshot", _old(60), "crypto", "expired"),
    )
    conn.execute(
        "INSERT INTO universe_members "
        "(id, snapshot_id, asset_id, symbol, rank, total_score, expires_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            "guarded-member", "guarded-snapshot", "guarded-asset", "BTC/USDT",
            1, 1.0, _old(59),
        ),
    )
    conn.execute(
        "INSERT INTO universe_forward_outcomes "
        "(id, member_id, snapshot_id, symbol, horizon, entry_price_usd, entry_ts, due_at, status) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "guarded-universe-outcome", "guarded-member", "guarded-snapshot",
            "BTC/USDT", "1d", 1.0, _old(60), _old(59), "settled",
        ),
    )
    conn.commit()


def test_explicit_no_experiment_floor_prunes_real_rows_without_reread(tmp_path, monkeypatch):
    conn = setup_env(tmp_path, monkeypatch)
    _seed_experiment_guarded_retention_rows(conn)
    monkeypatch.setattr(
        repo,
        "min_open_experiment_exposure_at",
        lambda: pytest.fail("explicit None must not re-read experiment state"),
    )
    monkeypatch.setattr(repo, "_observation_retention_floor", lambda _domain: None)

    assert repo.prune_filter_candidate_outcomes(30, min_experiment_started_at=None) == 1
    assert repo.prune_universe_forward_outcomes(30, min_experiment_started_at=None) == 1
    assert repo.prune_universe_snapshots(30, min_experiment_started_at=None) == 1
    assert conn.execute("SELECT 1 FROM universe_members").fetchone() is None
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_explicit_experiment_floor_protects_real_rows_without_reread(tmp_path, monkeypatch):
    conn = setup_env(tmp_path, monkeypatch)
    _seed_experiment_guarded_retention_rows(conn)
    monkeypatch.setattr(
        repo,
        "min_open_experiment_exposure_at",
        lambda: pytest.fail("explicit floor must not re-read experiment state"),
    )
    monkeypatch.setattr(repo, "_observation_retention_floor", lambda _domain: None)
    floor = _old(70)

    assert repo.prune_filter_candidate_outcomes(30, min_experiment_started_at=floor) == 0
    assert repo.prune_universe_forward_outcomes(30, min_experiment_started_at=floor) == 0
    assert prune_universe_members(30, min_experiment_started_at=floor) == 0
    assert repo.prune_universe_snapshots(30, min_experiment_started_at=floor) == 0
    assert conn.execute("SELECT 1 FROM filter_candidate_outcomes").fetchone() is not None
    assert conn.execute("SELECT 1 FROM universe_forward_outcomes").fetchone() is not None
    assert conn.execute("SELECT 1 FROM universe_members").fetchone() is not None
    assert conn.execute("SELECT 1 FROM universe_snapshots").fetchone() is not None
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_filter_outcomes_combine_learning_and_observation_floors_fail_closed(
    tmp_path, monkeypatch
):
    conn = setup_env(tmp_path, monkeypatch)
    _seed_experiment_guarded_retention_rows(conn)
    monkeypatch.setattr(repo, "_observation_retention_floor", lambda _domain: _old(90))

    assert repo.prune_filter_candidate_outcomes(
        30,
        min_experiment_started_at=_old(70),
    ) == 0

    def fail_observation_floor(_domain):
        raise repo.ExperimentStateReadError("unavailable")

    monkeypatch.setattr(repo, "_observation_retention_floor", fail_observation_floor)
    with pytest.raises(repo.ExperimentStateReadError, match="unavailable"):
        repo.prune_filter_candidate_outcomes(30, min_experiment_started_at=None)
    assert conn.execute("SELECT 1 FROM filter_candidate_outcomes").fetchone() is not None


def test_omitted_experiment_floor_still_reads_repository_state(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    reads = []
    monkeypatch.setattr(
        repo,
        "min_open_experiment_exposure_at",
        lambda: reads.append(True) or None,
    )

    assert repo.prune_filter_decisions(30) == 0
    assert reads == [True]


def test_prune_filter_evidence_keeps_recent_rows(tmp_path, monkeypatch):
    from aletheia.storage.models import (
        FilterCandidateOutcome,
        FilterDecision,
        FilterDecisionBatch,
    )

    conn = setup_env(tmp_path, monkeypatch)
    for suffix, timestamp in (("old", _old(120)), ("new", _recent())):
        repo.insert_filter_decision(FilterDecision(
            id=f"decision-{suffix}",
            dedup_key=f"decision-dedup-{suffix}",
            candidate_key=f"candidate-{suffix}",
            candidate_kind="opportunity",
            symbol="BTC/USDT",
            filter_name="ranker_confidence_gate",
            stage="paper_ranker",
            decision="passed",
            reason_code="confidence_sufficient",
            filter_version="v1-test",
            evaluated_at=timestamp,
        ))
        repo.insert_filter_decision_batch(FilterDecisionBatch(
            id=f"batch-{suffix}",
            dedup_key=f"batch-dedup-{suffix}",
            run_id=f"run-{suffix}",
            candidate_kind="universe_asset",
            filter_name="ticker_availability",
            stage="ticker_prefilter",
            candidate_count=1,
            passed_count=1,
            candidate_facts="[]",
            filter_version="v1-test",
            evaluated_at=timestamp,
        ))
        repo.insert_filter_candidate_outcomes_batch([FilterCandidateOutcome(
            id=f"outcome-{suffix}",
            outcome_candidate_key=f"candidate-{suffix}",
            venue="okx",
            symbol="BTC/USDT",
            direction="long",
            horizon="24h",
            entry_price_usd=100.0,
            entry_ts=timestamp,
            due_at=timestamp,
        )])

    assert repo.prune_filter_decision_batches(days=30) == 1
    assert repo.prune_filter_decisions(days=90) == 1
    assert repo.prune_filter_candidate_outcomes(days=90) == 1
    assert {row["id"] for row in conn.execute(
        "SELECT id FROM filter_decision_batches"
    ).fetchall()} == {"batch-new"}
    assert {row["id"] for row in conn.execute(
        "SELECT id FROM filter_decisions"
    ).fetchall()} == {"decision-new"}
    assert {row["id"] for row in conn.execute(
        "SELECT id FROM filter_candidate_outcomes"
    ).fetchall()} == {"outcome-new"}


def test_prune_operator_evaluation_events_keeps_recent_observations(tmp_path, monkeypatch):
    conn = setup_env(tmp_path, monkeypatch)
    for suffix, timestamp in (("old", _old(31)), ("new", _recent())):
        conn.execute(
            """INSERT INTO operator_evaluation_events
               (id, dedup_key, event_kind, trigger, pending_unresolved_total,
                pending_visible_count, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                f"operator-event-{suffix}", f"operator-dedup-{suffix}",
                "no_evaluable_candidate", "heartbeat", 0, 0, timestamp,
            ),
        )
    conn.commit()

    assert repo.prune_operator_evaluation_events(days=30) == 1
    assert {row["id"] for row in conn.execute(
        "SELECT id FROM operator_evaluation_events"
    ).fetchall()} == {"operator-event-new"}


def test_cleanup_never_touches_causal_chain(tmp_path, monkeypatch):
    conn = setup_env(tmp_path, monkeypatch)
    # Insert old trust-bearing rows; they must survive any cleanup.
    conn.execute("INSERT INTO trade_proposals (id, symbol, market_type, direction, created_at) VALUES (?,?,?,?,?)",
                 ("prop_old", "BTCUSDT", "spot", "long", _old(999)))
    conn.execute("INSERT INTO audit_events (id, timestamp, actor, source, event_type) VALUES (?,?,?,?,?)",
                 ("aud_old", _old(999), "system", "scheduler", "x"))
    conn.commit()

    _run_cleanup_with_test_config(monkeypatch)

    assert repo.get_trade_proposal("prop_old") is not None  # proposal survives
    audits = {r["id"] for r in repo.list_audit_events(limit=1000)}
    assert "aud_old" in audits  # audit survives
    # there is no prune function for the causal-chain tables
    assert not hasattr(repo, "prune_trade_proposals")
    assert not hasattr(repo, "prune_orders")
    assert not hasattr(repo, "prune_positions")


def test_cleanup_writes_audit(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _run_cleanup_with_test_config(monkeypatch)
    from aletheia.files.jsonl import read_jsonl
    assert any(e.get("event_type") == "data_pruned" for e in read_jsonl("audit.jsonl"))


def test_prune_pi_decisions_keeps_audit_and_only_deletes_settled_pending(
    tmp_path, monkeypatch
):
    """Only an unambiguous, durably settled pending carrier may be pruned.

    skip/watch are final Operator decisions, not telemetry. Unresolved carriers,
    submitting recovery state, legacy NULL source identity, and duplicate finals
    must all remain available for audit or fail-closed recovery.
    """
    conn = setup_env(tmp_path, monkeypatch)
    rows = [
        # id, action, opportunity_id, signal_source, proposal_status, created_at
        ("sp_settled", "signal_pending", "opp-settled", "market_observer", None, _old(30)),
        ("settled_final", "trade", "opp-settled", "market_observer", "executed", _old(30)),
        ("sp_unresolved", "signal_pending", "opp-unresolved", "market_observer", None, _old(30)),
        ("sp_submitting", "signal_pending", "opp-submitting", "market_observer", None, _old(30)),
        ("submitting_final", "trade", "opp-submitting", "market_observer", "submitting", _old(30)),
        ("sp_ambiguous", "signal_pending", "opp-ambiguous", "market_observer", None, _old(30)),
        ("ambiguous_final_a", "trade", "opp-ambiguous", "market_observer", "executed", _old(30)),
        ("ambiguous_final_b", "skip", "opp-ambiguous", "market_observer", "not_applicable", _old(30)),
        ("sp_legacy_source", "signal_pending", "opp-legacy", None, None, _old(30)),
        ("legacy_final", "trade", "opp-legacy", None, "executed", _old(30)),
        ("sp_new", "signal_pending", "opp-recent", "market_observer", None, _recent()),
        ("skip_old", "skip", "opp-skip", "market_observer", "not_applicable", _old(30)),
        ("watch_old", "watch", "opp-watch", "market_observer", "not_applicable", _old(30)),
        ("close_old", "close", "opp-close", "market_observer", "executed", _old(30)),
        ("promote_old", "recommend_promote", "opp-promote", "market_observer", "not_applicable", _old(30)),
    ]
    for rid, action, opportunity_id, signal_source, proposal_status, ts in rows:
        conn.execute(
            "INSERT INTO pi_decisions "
            "(id, action, opportunity_id, signal_source, proposal_status, "
            "created_at, conviction, debate_ran) VALUES (?,?,?,?,?,?,0,0)",
            (rid, action, opportunity_id, signal_source, proposal_status, ts),
        )
    conn.commit()

    deleted = repo.prune_pi_decisions(7)
    assert deleted == 1, f"expected only the settled pending row deleted, got {deleted}"

    remaining = {r["id"] for r in conn.execute("SELECT id FROM pi_decisions").fetchall()}
    assert {"skip_old", "watch_old", "close_old", "promote_old"} <= remaining, (
        "prune_pi_decisions deleted a final Operator decision"
    )
    assert "sp_new" in remaining, "recent signal_pending wrongly deleted"
    assert {
        "sp_unresolved", "sp_submitting", "sp_ambiguous", "sp_legacy_source"
    } <= remaining, "prune removed an unresolved or ambiguous recovery carrier"
    assert "sp_settled" not in remaining


def test_prune_pi_decisions_keeps_a_referenced_carrier(tmp_path, monkeypatch):
    conn = setup_env(tmp_path, monkeypatch)
    old = _old(30)
    conn.execute(
        "INSERT INTO agent_runs (id,agent_name,permission_mode,started_at) VALUES (?,?,?,?)",
        ("artifact-run", "paper_trader", "Observe", old),
    )
    for row in (
        ("referenced-pending", "signal_pending", None),
        ("referenced-final", "trade", "executed"),
    ):
        conn.execute(
            "INSERT INTO pi_decisions "
            "(id,action,opportunity_id,signal_source,proposal_status,created_at,conviction,debate_ran) "
            "VALUES (?,?,?,?,?,?,0,0)",
            (row[0], row[1], "referenced-opp", "market_observer", row[2], old),
        )
    conn.execute(
        "INSERT INTO agent_run_artifacts (run_id,artifact_type,artifact_id) VALUES (?,?,?)",
        ("artifact-run", "pi_decision", "referenced-pending"),
    )
    conn.commit()

    assert repo.prune_pi_decisions(7) == 0
    assert conn.execute(
        "SELECT 1 FROM pi_decisions WHERE id='referenced-pending'"
    ).fetchone() is not None


def test_delete_old_rows_requires_condition_for_pi_decisions(tmp_path, monkeypatch):
    """pi_decisions mixes transient queue rows with causal decisions.

    The shared delete helper may prune it only when the caller supplies an
    explicit action filter; a bare old-row delete would erase decision history.
    """
    setup_env(tmp_path, monkeypatch)

    try:
        repo._delete_old_rows("pi_decisions", days=7)
    except ValueError as exc:
        assert "not prunable" in str(exc)
    else:
        raise AssertionError("pi_decisions must not be bare-prunable")


def test_prune_agent_shell_heartbeats_keeps_recent_deletes_old(tmp_path, monkeypatch):
    """agent_shell_heartbeats is pure liveness telemetry (read latest-row-only)
    and was the dominant app.db bloat (~199k rows / ~85 MB by 2026-06-27). It
    had no prune path; this guards the DB-SIZE-1 retention. Old rows go, the
    latest row stays so get_latest_heartbeat still works."""
    conn = setup_env(tmp_path, monkeypatch)
    cols = "(id, agent_id, status, mode, permission_mode, last_seen_at, created_at)"
    conn.execute(f"INSERT INTO agent_shell_heartbeats {cols} VALUES (?,?,?,?,?,?,?)",
                 ("hb_old", "operator", "alive", "current", "Full", _old(10), _old(10)))
    conn.execute(f"INSERT INTO agent_shell_heartbeats {cols} VALUES (?,?,?,?,?,?,?)",
                 ("hb_new", "operator", "alive", "current", "Full", _recent(), _recent()))
    conn.commit()

    deleted = repo.prune_agent_shell_heartbeats(days=2)
    assert deleted == 1
    remaining = {r["id"] for r in conn.execute("SELECT id FROM agent_shell_heartbeats").fetchall()}
    assert remaining == {"hb_new"}, "prune must keep the latest heartbeat and drop only old ones"


def test_session_log_cleanup_preserves_window_after_db_failure(tmp_path, monkeypatch):
    import os
    import time
    from unittest.mock import Mock
    from aletheia.agent_runtime import scheduler
    from aletheia.config.schema import AletheiaConfig
    import aletheia.storage.sqlite as sq

    conn = setup_env(tmp_path, monkeypatch)
    sessions = tmp_path / "agent_shell" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    transcript = sessions / "twenty-days-old.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    old = time.time() - 20 * 86400
    os.utime(transcript, (old, old))
    cfg = AletheiaConfig()
    cfg.retention.agent_shell_session_logs_days = 30
    monkeypatch.setattr(sq, "get_db", Mock(side_effect=[sqlite3.OperationalError("database unavailable"), conn]))

    result = scheduler._prune_session_logs(cfg)

    assert transcript.exists()
    assert result == {"deleted": 0, "error": "database unavailable"}


def test_prune_session_logs_keeps_main_and_recent_deletes_old(tmp_path, monkeypatch):
    """The DB-join orphan sweep keeps recent and exempt transcripts."""
    import os
    import time
    from aletheia.agent_shell.file_artifacts import prune_session_logs_db_join

    setup_env(tmp_path, monkeypatch)
    sessions = tmp_path / "agent_shell" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)

    main = sessions / "operator-main.jsonl"          # persistent → KEEP via exempt
    recent = sessions / "task-abc.jsonl"             # fresh → KEEP via mtime
    old = sessions / "ephemeral-xyz.jsonl"           # stale → DELETE
    for p in (main, recent, old):
        p.write_text('{"e":"x"}\n', encoding="utf-8")

    stale_mtime = time.time() - 30 * 86400
    os.utime(main, (stale_mtime, stale_mtime))       # old mtime, but exempt by name
    os.utime(old, (stale_mtime, stale_mtime))        # genuinely stale → reaped

    deleted = prune_session_logs_db_join(14, extra_exempt_stems=("operator-main",))
    assert deleted == 1, f"expected only the stale ephemeral file deleted, got {deleted}"
    survivors = {p.name for p in sessions.glob("*.jsonl")}
    assert survivors == {"operator-main.jsonl", "task-abc.jsonl"}, (
        "prune must keep the exempt main session and recent transcripts"
    )


def test_jsonl_rotation(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    import aletheia.files.jsonl as jsonl
    monkeypatch.setattr(jsonl, "_max_bytes", lambda: 200)  # tiny cap for test
    for i in range(50):
        jsonl.append_jsonl("test.jsonl", {"i": i, "pad": "x" * 20})
    logs = tmp_path / "logs"
    assert (logs / "test.jsonl").exists()
    assert (logs / "test.jsonl.1").exists()  # rotated generation exists
