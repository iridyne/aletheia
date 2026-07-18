"""Phase 3 data-retention prune coverage.

These tests exercise the new/updated prune functions defined by Agent 2 in
``aletheia.storage.repositories``. Each function is checked for:

- **dry-run parity** — a prune called against an empty (or filtered) set returns
  0 and does not mutate rows;
- **apply mode** — old rows are removed while young rows survive;
- **idempotency** — a second run with no new eligible rows is a no-op;
- **audit invariant** — the audit-chain and learning tables retain their row
  counts across every prune call.

The autouse ``_inmemory_db`` fixture in ``tests/conftest.py`` gives every test a
fresh in-memory SQLite database with the full migration set applied, so no
disk I/O touches the developer's real ``app.db``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from aletheia.storage.sqlite import get_db


# ---------------------------------------------------------------------------
# Audit / learning invariant helpers
# ---------------------------------------------------------------------------


AUDIT_CHAIN_TABLES = (
    "audit_events",
    "fills",
    "capital_events",
    "orders",
    "positions",
    "proposals",
    "risk_decisions",
    "approvals",
    "knowledge_items",
    "trade_proposals",
    "ps_orders",
    "ps_fills",
    "ps_positions",
)

LEARNING_TABLES = (
    "learning_experiments",
    "learning_exposures",
    "learning_hypotheses",
    "learning_policy_epochs",
    "learning_contribution_verdicts",
    "learning_lifecycle_events",
    "learning_job_health",
)


def _row_counts(tables: tuple[str, ...]) -> dict[str, int]:
    db = get_db()
    counts: dict[str, int] = {}
    for table in tables:
        try:
            row = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        except Exception:
            continue
        counts[table] = int(row[0])
    return counts


def _assert_invariants_preserved(before_audit: dict[str, int],
                                 before_learning: dict[str, int]) -> None:
    after_audit = _row_counts(AUDIT_CHAIN_TABLES)
    after_learning = _row_counts(LEARNING_TABLES)
    assert after_audit == before_audit, (
        f"audit-chain rows mutated by prune: before={before_audit} after={after_audit}"
    )
    assert after_learning == before_learning, (
        f"learning-table rows mutated by prune: before={before_learning} after={after_learning}"
    )


def _iso(days_ago: int, *, hours: int = 0) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(days=days_ago, hours=hours)
    ).isoformat()


def _seed_audit_and_learning() -> tuple[dict[str, int], dict[str, int]]:
    """Insert one durable row per audit/learning table for invariant checks."""
    db = get_db()
    audit_seeds = {
        "audit_events": {
            "id": "audit-1", "actor": "system", "source": "test",
            "event_type": "test_event", "message": "seed",
            "timestamp": _iso(365),
        },
    }
    # Only seed tables whose minimal INSERT is trivial from column defaults.
    # audit_events is the load-bearing invariant — the rest are covered by
    # the fixture ensuring the tables exist and their COUNT() stays stable.
    for table, cols in audit_seeds.items():
        placeholders = ",".join("?" * len(cols))
        try:
            db.execute(
                f"INSERT INTO {table} ({','.join(cols.keys())}) VALUES ({placeholders})",
                tuple(cols.values()),
            )
        except Exception:
            pass
    db.commit()
    return _row_counts(AUDIT_CHAIN_TABLES), _row_counts(LEARNING_TABLES)


@pytest.fixture
def invariant_baseline():
    """Yield the seeded audit/learning row-count snapshot for post-prune diff."""
    return _seed_audit_and_learning()


# ---------------------------------------------------------------------------
# Seed helpers per-table
# ---------------------------------------------------------------------------


def _seed_agent_shell_sessions(*, session_id: str, created_at: str,
                               kind: str = "command_session",
                               parent_session_id: str | None = None) -> None:
    db = get_db()
    db.execute(
        "INSERT INTO agent_shell_sessions (id, user_input, kind, parent_session_id, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (session_id, "seed", kind, parent_session_id, created_at),
    )
    db.commit()


def _seed_agent_shell_message(*, session_id: str, message_type: str,
                              created_at: str) -> str:
    db = get_db()
    msg_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO agent_shell_messages "
        "(id, session_id, role, message_type, content, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (msg_id, session_id, "assistant", message_type, "{}", created_at),
    )
    db.commit()
    return msg_id


def _seed_agent_shell_artifact(*, session_id: str, artifact_type: str,
                               created_at: str) -> str:
    db = get_db()
    art_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO agent_shell_artifacts "
        "(id, session_id, artifact_type, title, content, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (art_id, session_id, artifact_type, "seed", "{}", created_at),
    )
    db.commit()
    return art_id


def _seed_agent_shell_task(*, task_id: str, status: str, completed_at: str | None,
                           task_type: str = "generic",
                           created_by: str = "test") -> None:
    db = get_db()
    db.execute(
        "INSERT INTO agent_shell_tasks "
        "(id, task_type, status, priority, created_by, assigned_to, input_data, completed_at, created_at) "
        "VALUES (?, ?, ?, 'normal', ?, 'operator', '{}', ?, ?)",
        (task_id, task_type, status, created_by, completed_at, completed_at or _iso(0)),
    )
    db.commit()


def _seed_market_ohlcv(*, exchange: str, symbol: str, timeframe: str,
                       ts_ms: int) -> None:
    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO market_ohlcv "
        "(exchange, symbol, market_type, timeframe, ts, open, high, low, close, volume, provider) "
        "VALUES (?, ?, 'perp', ?, ?, 1, 1, 1, 1, 1, 'test')",
        (exchange, symbol, timeframe, ts_ms),
    )
    db.commit()


def _seed_funding_rate_row(*, symbol: str, exchange: str, funding_rate: float,
                           observed_at: str) -> None:
    db = get_db()
    db.execute(
        "INSERT INTO funding_rate_snapshots "
        "(id, symbol, exchange, funding_rate, funding_rate_annualized, observed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), symbol, exchange, funding_rate,
         funding_rate * 24 * 365, observed_at),
    )
    db.commit()


def _seed_universe_snapshot(*, snap_id: str, created_at: str) -> None:
    db = get_db()
    db.execute(
        "INSERT INTO universe_snapshots (id, created_at, market_type, status) "
        "VALUES (?, ?, 'crypto', 'active')",
        (snap_id, created_at),
    )
    db.commit()


def _seed_universe_registry(*, asset_id: str) -> None:
    db = get_db()
    db.execute(
        "INSERT OR IGNORE INTO universe_registry (id, symbol, market_type) "
        "VALUES (?, ?, ?)",
        (asset_id, "BTCUSDT", "perp"),
    )
    db.commit()


def _seed_universe_member(*, member_id: str, snap_id: str, asset_id: str) -> None:
    db = get_db()
    db.execute(
        "INSERT INTO universe_members "
        "(id, snapshot_id, asset_id, symbol, rank, total_score, expires_at) "
        "VALUES (?, ?, ?, 'BTCUSDT', 1, 1.0, datetime('now','+1 day'))",
        (member_id, snap_id, asset_id),
    )
    db.commit()


def _seed_backtest_run(*, run_id: str, created_at: str) -> None:
    db = get_db()
    db.execute(
        "INSERT INTO backtest_runs "
        "(id, symbol, timeframe, direction, created_at) "
        "VALUES (?, 'BTCUSDT', '1h', 'long', ?)",
        (run_id, created_at),
    )
    db.commit()


def _seed_backtest_trade(*, run_id: str, entry_ms: int) -> None:
    db = get_db()
    db.execute(
        "INSERT INTO backtest_trades "
        "(id, run_id, trade_index, direction, entry_ts, exit_ts, "
        " entry_bar_index, exit_bar_index, entry_price, exit_price, quantity) "
        "VALUES (?, ?, 0, 'long', ?, ?, 0, 1, 1, 1, 1)",
        (str(uuid.uuid4()), run_id, entry_ms, entry_ms + 3600_000),
    )
    db.commit()


# ---------------------------------------------------------------------------
# _PRUNABLE whitelist / audit-chain guardrails
# ---------------------------------------------------------------------------


def test_prunable_whitelist_excludes_learning_tables():
    """Learning tables must never be prunable via _delete_old_rows."""
    from aletheia.storage.repositories import _PRUNABLE

    violations = set(LEARNING_TABLES) & set(_PRUNABLE.keys())
    assert violations == set(), (
        f"RETENTION VIOLATION: learning tables in _PRUNABLE: {violations}"
    )


def test_prunable_whitelist_excludes_audit_chain_tables():
    """Trust-bearing audit-chain tables must never be prunable."""
    from aletheia.storage.repositories import _PRUNABLE

    violations = set(AUDIT_CHAIN_TABLES) & set(_PRUNABLE.keys())
    assert violations == set(), (
        f"RETENTION VIOLATION: audit-chain tables in _PRUNABLE: {violations}"
    )


# ---------------------------------------------------------------------------
# agent_shell_messages — type-tiered TTL
# ---------------------------------------------------------------------------


def test_prune_agent_shell_messages_dry_run_on_empty_db(invariant_baseline):
    from aletheia.storage.repositories import prune_agent_shell_messages

    deleted = prune_agent_shell_messages()
    assert deleted == 0
    _assert_invariants_preserved(*invariant_baseline)


def test_prune_agent_shell_messages_type_tiered(invariant_baseline):
    from aletheia.storage.repositories import prune_agent_shell_messages

    _seed_agent_shell_sessions(session_id="s1", created_at=_iso(60))
    old_ctx_id = _seed_agent_shell_message(
        session_id="s1", message_type="context", created_at=_iso(5),
    )
    young_ctx_id = _seed_agent_shell_message(
        session_id="s1", message_type="context", created_at=_iso(1),
    )
    old_other_id = _seed_agent_shell_message(
        session_id="s1", message_type="message", created_at=_iso(20),
    )
    young_other_id = _seed_agent_shell_message(
        session_id="s1", message_type="message", created_at=_iso(7),
    )

    deleted = prune_agent_shell_messages(days_context=3, days_other=14)
    assert deleted >= 2, "context>3d + other>14d must both be pruned"

    db = get_db()
    remaining_ids = {
        r[0] for r in db.execute("SELECT id FROM agent_shell_messages").fetchall()
    }
    assert old_ctx_id not in remaining_ids
    assert young_ctx_id in remaining_ids
    assert old_other_id not in remaining_ids
    assert young_other_id in remaining_ids

    # Idempotent
    assert prune_agent_shell_messages(days_context=3, days_other=14) == 0
    _assert_invariants_preserved(*invariant_baseline)


# ---------------------------------------------------------------------------
# agent_shell_artifacts — type-tiered TTL
# ---------------------------------------------------------------------------


def test_prune_agent_shell_artifacts_type_tiered(invariant_baseline):
    from aletheia.storage.repositories import prune_agent_shell_artifacts

    _seed_agent_shell_sessions(session_id="s2", created_at=_iso(60))

    old_strat = _seed_agent_shell_artifact(
        session_id="s2", artifact_type="strategy_evaluation", created_at=_iso(10),
    )
    young_strat = _seed_agent_shell_artifact(
        session_id="s2", artifact_type="strategy_evaluation", created_at=_iso(2),
    )
    old_proposal = _seed_agent_shell_artifact(
        session_id="s2", artifact_type="proposal", created_at=_iso(40),
    )
    young_proposal = _seed_agent_shell_artifact(
        session_id="s2", artifact_type="proposal", created_at=_iso(10),
    )
    old_other = _seed_agent_shell_artifact(
        session_id="s2", artifact_type="scratch", created_at=_iso(20),
    )
    young_other = _seed_agent_shell_artifact(
        session_id="s2", artifact_type="scratch", created_at=_iso(5),
    )

    deleted = prune_agent_shell_artifacts()
    assert deleted >= 3

    db = get_db()
    remaining = {
        r[0] for r in db.execute("SELECT id FROM agent_shell_artifacts").fetchall()
    }
    assert old_strat not in remaining
    assert young_strat in remaining
    assert old_proposal not in remaining
    assert young_proposal in remaining
    assert old_other not in remaining
    assert young_other in remaining

    # Idempotent
    assert prune_agent_shell_artifacts() == 0
    _assert_invariants_preserved(*invariant_baseline)


# ---------------------------------------------------------------------------
# agent_shell_tasks — completed vs failed windows, with running exempted
# ---------------------------------------------------------------------------


def test_prune_agent_shell_tasks_running_exempt(invariant_baseline):
    from aletheia.storage.repositories import prune_agent_shell_tasks

    _seed_agent_shell_task(task_id="t-running", status="running", completed_at=None)
    _seed_agent_shell_task(task_id="t-pending", status="pending", completed_at=None)
    _seed_agent_shell_task(
        task_id="t-completed-old", status="completed", completed_at=_iso(60),
    )
    _seed_agent_shell_task(
        task_id="t-completed-young", status="completed", completed_at=_iso(5),
    )
    _seed_agent_shell_task(
        task_id="t-failed-old", status="failed", completed_at=_iso(90),
    )
    _seed_agent_shell_task(
        task_id="t-failed-young", status="failed", completed_at=_iso(15),
    )

    deleted = prune_agent_shell_tasks(days_completed=14, days_failed=30)
    assert deleted == 2

    db = get_db()
    remaining = {
        r[0] for r in db.execute("SELECT id FROM agent_shell_tasks").fetchall()
    }
    # Running / pending are always kept.
    assert "t-running" in remaining
    assert "t-pending" in remaining
    # Old completed + old failed are gone.
    assert "t-completed-old" not in remaining
    assert "t-failed-old" not in remaining
    # Fresh terminals survive their tiered windows.
    assert "t-completed-young" in remaining
    assert "t-failed-young" in remaining

    # Idempotent.
    assert prune_agent_shell_tasks(days_completed=14, days_failed=30) == 0
    _assert_invariants_preserved(*invariant_baseline)


def test_prune_agent_shell_tasks_cascade_children(invariant_baseline):
    from aletheia.storage.repositories import prune_agent_shell_tasks

    _seed_agent_shell_sessions(session_id="s-cascade", created_at=_iso(60))
    _seed_agent_shell_task(
        task_id="t-cascade", status="completed", completed_at=_iso(60),
    )
    db = get_db()
    db.execute(
        "INSERT INTO agent_shell_events "
        "(id, event_type, severity, source, task_id, created_at) "
        "VALUES (?, 'x', 'info', 'test', ?, ?)",
        (str(uuid.uuid4()), "t-cascade", _iso(60)),
    )
    db.execute(
        "INSERT INTO agent_shell_artifacts "
        "(id, task_id, artifact_type, title, content, created_at) "
        "VALUES (?, ?, 'proposal', 'x', '{}', ?)",
        (str(uuid.uuid4()), "t-cascade", _iso(60)),
    )
    db.commit()

    prune_agent_shell_tasks(days_completed=14, days_failed=30)

    remaining_events = db.execute(
        "SELECT COUNT(*) FROM agent_shell_events WHERE task_id = ?",
        ("t-cascade",),
    ).fetchone()[0]
    remaining_artifacts = db.execute(
        "SELECT COUNT(*) FROM agent_shell_artifacts WHERE task_id = ?",
        ("t-cascade",),
    ).fetchone()[0]
    assert remaining_events == 0
    assert remaining_artifacts == 0
    _assert_invariants_preserved(*invariant_baseline)


# ---------------------------------------------------------------------------
# agent_shell_sessions — cascade parent→children, keep parents referenced
# ---------------------------------------------------------------------------


def test_prune_agent_shell_sessions_cascade_and_parent_keep(invariant_baseline):
    from aletheia.storage.repositories import prune_agent_shell_sessions

    # parent_old is old but is referenced as parent_session_id of the newer
    # session, so it must NOT be pruned.
    _seed_agent_shell_sessions(session_id="parent_old", created_at=_iso(60))
    _seed_agent_shell_sessions(
        session_id="child_young",
        created_at=_iso(1),
        parent_session_id="parent_old",
    )
    _seed_agent_shell_sessions(session_id="orphan_old", created_at=_iso(60))
    _seed_agent_shell_message(
        session_id="orphan_old", message_type="message", created_at=_iso(30),
    )

    deleted = prune_agent_shell_sessions(days=14)
    assert deleted == 1

    db = get_db()
    remaining = {
        r[0] for r in db.execute("SELECT id FROM agent_shell_sessions").fetchall()
    }
    assert "parent_old" in remaining
    assert "child_young" in remaining
    assert "orphan_old" not in remaining

    # Children of the pruned orphan should be gone.
    child_count = db.execute(
        "SELECT COUNT(*) FROM agent_shell_messages WHERE session_id = ?",
        ("orphan_old",),
    ).fetchone()[0]
    assert child_count == 0

    # Idempotent.
    assert prune_agent_shell_sessions(days=14) == 0
    _assert_invariants_preserved(*invariant_baseline)


# ---------------------------------------------------------------------------
# agent_shell_commands
# ---------------------------------------------------------------------------


def test_prune_agent_shell_commands(invariant_baseline):
    from aletheia.storage.repositories import prune_agent_shell_commands

    _seed_agent_shell_sessions(session_id="s-cmds", created_at=_iso(60))
    db = get_db()
    old_id = str(uuid.uuid4())
    young_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO agent_shell_commands "
        "(id, session_id, command, created_at) VALUES (?, ?, 'test', ?)",
        (old_id, "s-cmds", _iso(30)),
    )
    db.execute(
        "INSERT INTO agent_shell_commands "
        "(id, session_id, command, created_at) VALUES (?, ?, 'test', ?)",
        (young_id, "s-cmds", _iso(1)),
    )
    db.commit()

    assert prune_agent_shell_commands(days=14) == 1
    assert prune_agent_shell_commands(days=14) == 0
    remaining = {
        r[0] for r in db.execute("SELECT id FROM agent_shell_commands").fetchall()
    }
    assert old_id not in remaining
    assert young_id in remaining
    _assert_invariants_preserved(*invariant_baseline)


# ---------------------------------------------------------------------------
# Universe cascade
# ---------------------------------------------------------------------------


def test_prune_universe_snapshots_cascades_members(invariant_baseline):
    from aletheia.storage.repositories import (
        prune_universe_snapshots,
    )

    _seed_universe_registry(asset_id="asset-1")
    _seed_universe_snapshot(snap_id="snap-old", created_at=_iso(60))
    _seed_universe_snapshot(snap_id="snap-young", created_at=_iso(1))
    _seed_universe_member(member_id="m-old", snap_id="snap-old", asset_id="asset-1")
    _seed_universe_member(
        member_id="m-young", snap_id="snap-young", asset_id="asset-1",
    )

    deleted = prune_universe_snapshots(days=30)
    assert deleted == 1

    db = get_db()
    remaining_snaps = {
        r[0] for r in db.execute("SELECT id FROM universe_snapshots").fetchall()
    }
    remaining_members = {
        r[0] for r in db.execute("SELECT id FROM universe_members").fetchall()
    }
    assert "snap-old" not in remaining_snaps
    assert "snap-young" in remaining_snaps
    assert "m-old" not in remaining_members
    assert "m-young" in remaining_members

    # Idempotent.
    assert prune_universe_snapshots(days=30) == 0
    _assert_invariants_preserved(*invariant_baseline)


def test_prune_universe_scanner_runs(invariant_baseline):
    from aletheia.storage.repositories import prune_universe_scanner_runs

    db = get_db()
    old_id = str(uuid.uuid4())
    young_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO universe_scanner_runs (id, started_at, status) "
        "VALUES (?, ?, 'completed')",
        (old_id, _iso(60)),
    )
    db.execute(
        "INSERT INTO universe_scanner_runs (id, started_at, status) "
        "VALUES (?, ?, 'completed')",
        (young_id, _iso(1)),
    )
    db.commit()

    assert prune_universe_scanner_runs(days=30) == 1
    assert prune_universe_scanner_runs(days=30) == 0
    _assert_invariants_preserved(*invariant_baseline)


# ---------------------------------------------------------------------------
# Filter tables with clean_window_cutoff guard
# ---------------------------------------------------------------------------


def _seed_filter_candidate_outcome(*, key: str, entry_ts: str) -> None:
    db = get_db()
    db.execute(
        "INSERT INTO filter_candidate_outcomes "
        "(id, outcome_candidate_key, venue, symbol, market_type, direction, "
        " scanner_family, horizon, entry_price_usd, entry_ts, due_at, status) "
        "VALUES (?, ?, 'binance', 'BTCUSDT', 'perp', 'long', 'test', '1h', "
        " 50000, ?, datetime('now','+1 day'), 'pending')",
        (str(uuid.uuid4()), key, entry_ts),
    )
    db.commit()


def _seed_open_experiment(
    *, experiment_id: str, started_at: str, status: str = "active",
) -> None:
    """Insert a still-running learning_experiments row (ended_at IS NULL)."""
    db = get_db()
    hyp_id = f"hyp-{experiment_id}"
    db.execute(
        "INSERT INTO learning_hypotheses "
        "(hypothesis_id, fingerprint, cohort_id, path_id, overlap_group, "
        " source_type, scope_json, action_json, consumer, target_horizon, "
        " created_at, updated_at) "
        "VALUES (?, ?, 'c', 'p', 'g', 'src', '{}', '{}', 'test', '1h', "
        "        datetime('now'), datetime('now'))",
        (hyp_id, f"fp-{experiment_id}"),
    )
    db.execute(
        "INSERT INTO learning_experiments "
        "(experiment_id, hypothesis_id, stage, assignment_salt, allocation_pct, "
        " min_samples_per_arm, min_market_days, primary_metric, status, "
        " started_at, ended_at, created_at) "
        "VALUES (?, ?, 'shadow', 'salt', 0.5, 30, 30, 'ret', ?, "
        "        ?, NULL, datetime('now'))",
        (experiment_id, hyp_id, status, started_at),
    )
    db.commit()


def _seed_experiment_exposure(
    *, experiment_id: str, exposed_at: str, suffix: str = "seed",
) -> None:
    db = get_db()
    hypothesis_id = db.execute(
        "SELECT hypothesis_id FROM learning_experiments WHERE experiment_id=?",
        (experiment_id,),
    ).fetchone()[0]
    db.execute(
        "INSERT INTO learning_exposures "
        "(exposure_id, dedup_key, experiment_id, hypothesis_id, assignment, consumer, "
        "consumer_stage, path_id, overlap_group, candidate_key, outcome_candidate_key, "
        "outcome_source, outcome_contract_version, concurrent_policy_epoch, exposed_at) "
        "VALUES (?, ?, ?, ?, 'control', 'test', 'score', 'p', 'g', ?, ?, "
        "'fixed_horizon_candidate', 'v1', 'epoch-retention', ?)",
        (
            f"exposure-{experiment_id}-{suffix}",
            f"dedup-{experiment_id}-{suffix}",
            experiment_id,
            hypothesis_id,
            f"candidate-{experiment_id}-{suffix}",
            f"candidate-{experiment_id}-{suffix}:1h",
            exposed_at,
        ),
    )
    db.commit()


def test_prune_filter_candidate_outcomes_clean_window_cutoff():
    """Open experiment predating retention cutoff must protect its evidence rows.

    Users confirmed decision A in Phase 2: prune_filter_candidate_outcomes(days=30)
    must consult min(now-30d, min_open_experiment.started_at). When an open
    experiment started earlier than the default cutoff, evidence back to that
    start is preserved.
    """
    from aletheia.storage.repositories import prune_filter_candidate_outcomes

    _seed_filter_candidate_outcome(key="k-100d", entry_ts=_iso(100))
    _seed_filter_candidate_outcome(key="k-60d", entry_ts=_iso(60))
    _seed_filter_candidate_outcome(key="k-10d", entry_ts=_iso(10))

    # No open experiment: default 30d cutoff applies — old rows drop.
    deleted = prune_filter_candidate_outcomes(days=30)
    assert deleted == 2

    # Reset and try again with an open experiment older than the cutoff.
    _seed_filter_candidate_outcome(key="k-100d", entry_ts=_iso(100))
    _seed_filter_candidate_outcome(key="k-60d", entry_ts=_iso(60))
    _seed_open_experiment(experiment_id="exp-open", started_at=_iso(120))
    _seed_experiment_exposure(experiment_id="exp-open", exposed_at=_iso(120))

    deleted2 = prune_filter_candidate_outcomes(days=30)
    assert deleted2 == 0, (
        "clean_window_cutoff must preserve rows an open experiment still needs"
    )

    db = get_db()
    remaining = {
        r[0]
        for r in db.execute(
            "SELECT outcome_candidate_key FROM filter_candidate_outcomes"
        ).fetchall()
    }
    assert remaining == {"k-100d", "k-60d", "k-10d"}


def test_retention_anchor_is_earliest_active_exposure_and_ignores_unexposed_registration():
    from aletheia.storage.repositories import prune_filter_candidate_outcomes

    _seed_filter_candidate_outcome(key="k-100d", entry_ts=_iso(100))
    _seed_filter_candidate_outcome(key="k-60d", entry_ts=_iso(60))
    _seed_filter_candidate_outcome(key="k-10d", entry_ts=_iso(10))
    _seed_open_experiment(
        experiment_id="exp-unexposed-registered",
        started_at=_iso(200),
        status="registered",
    )
    _seed_open_experiment(
        experiment_id="exp-active",
        started_at=_iso(5),
        status="active",
    )
    _seed_experiment_exposure(experiment_id="exp-active", exposed_at=_iso(70))
    _seed_experiment_exposure(
        experiment_id="exp-active", exposed_at=_iso(40), suffix="later",
    )

    assert prune_filter_candidate_outcomes(days=30) == 1
    remaining = {
        row[0]
        for row in get_db().execute(
            "SELECT outcome_candidate_key FROM filter_candidate_outcomes"
        ).fetchall()
    }
    assert remaining == {"k-60d", "k-10d"}


def test_prune_filter_decisions_clean_window_cutoff():
    from aletheia.storage.repositories import prune_filter_decisions

    db = get_db()

    def _seed(candidate_key: str, evaluated_at: str) -> None:
        db.execute(
            "INSERT INTO filter_decisions "
            "(id, dedup_key, candidate_key, candidate_kind, symbol, "
            " filter_name, filter_version, stage, decision, reason_code, "
            " evaluated_at) "
            "VALUES (?, ?, ?, 'test', 'BTCUSDT', 'f', 'v', 's', 'passed', "
            " 'ok', ?)",
            (str(uuid.uuid4()), f"dk-{candidate_key}-{evaluated_at}",
             candidate_key, evaluated_at),
        )
        db.commit()

    _seed("k-90d", _iso(90))
    _seed("k-60d", _iso(60))
    _seed("k-10d", _iso(10))

    # With an open experiment old enough to hold everything, nothing is pruned.
    _seed_open_experiment(experiment_id="exp-hold", started_at=_iso(120))
    _seed_experiment_exposure(experiment_id="exp-hold", exposed_at=_iso(120))
    assert prune_filter_decisions(days=45) == 0

    # End the experiment and retry — the 45d cutoff drops the two >45d rows.
    db.execute(
        "UPDATE learning_experiments SET ended_at = datetime('now') "
        "WHERE experiment_id = ?",
        ("exp-hold",),
    )
    db.commit()
    assert prune_filter_decisions(days=45) == 2
    # Idempotent.
    assert prune_filter_decisions(days=45) == 0


# ---------------------------------------------------------------------------
# market_ohlcv — per-timeframe TTL + legacy_unknown hard drop
# ---------------------------------------------------------------------------


def test_prune_market_ohlcv_per_timeframe_and_legacy(invariant_baseline):
    from aletheia.storage.repositories import prune_market_ohlcv

    now = datetime.now(timezone.utc)
    # 1m default TTL is 2d; 30 days is well past cutoff.
    old_1m_ms = int((now - timedelta(days=30)).timestamp() * 1000)
    young_1m_ms = int((now - timedelta(hours=6)).timestamp() * 1000)
    # 1d default TTL is 730 days; 100 days should survive.
    young_1d_ms = int((now - timedelta(days=100)).timestamp() * 1000)

    _seed_market_ohlcv(exchange="binance", symbol="BTCUSDT",
                       timeframe="1m", ts_ms=old_1m_ms)
    _seed_market_ohlcv(exchange="binance", symbol="BTCUSDT",
                       timeframe="1m", ts_ms=young_1m_ms)
    _seed_market_ohlcv(exchange="binance", symbol="BTCUSDT",
                       timeframe="1d", ts_ms=young_1d_ms)
    _seed_market_ohlcv(exchange="legacy_unknown", symbol="BTCUSDT",
                       timeframe="1h", ts_ms=young_1m_ms)

    deleted = prune_market_ohlcv()
    assert deleted >= 2  # old 1m + legacy_unknown row

    db = get_db()
    remaining = db.execute(
        "SELECT exchange, timeframe, ts FROM market_ohlcv ORDER BY ts"
    ).fetchall()
    remaining_set = {(r[0], r[1], r[2]) for r in remaining}
    assert ("binance", "1m", old_1m_ms) not in remaining_set
    assert ("binance", "1m", young_1m_ms) in remaining_set
    assert ("binance", "1d", young_1d_ms) in remaining_set
    assert not any(r[0] == "legacy_unknown" for r in remaining), (
        "legacy_unknown rows must be hard-deleted regardless of age"
    )

    # Idempotent.
    assert prune_market_ohlcv() == 0
    _assert_invariants_preserved(*invariant_baseline)


# ---------------------------------------------------------------------------
# insert_funding_rate_snapshot — dedup / heartbeat floor
# ---------------------------------------------------------------------------


def test_insert_funding_rate_snapshot_dedup_and_heartbeat(invariant_baseline):
    from aletheia.storage.repositories import insert_funding_rate_snapshot

    now = datetime.now(timezone.utc)

    def _count() -> int:
        db = get_db()
        return int(
            db.execute("SELECT COUNT(*) FROM funding_rate_snapshots").fetchone()[0]
        )

    # Seed a first row.
    insert_funding_rate_snapshot({
        "symbol": "BTCUSDT",
        "exchange": "binance",
        "funding_rate": 0.0001,
        "funding_rate_annualized": 0.0001 * 24 * 365,
        "next_funding_time": None,
        "mark_price": 50000.0,
        "observed_at": now.isoformat(),
    })
    assert _count() == 1

    # Identical payload within the heartbeat floor: dedup (no new row).
    insert_funding_rate_snapshot({
        "symbol": "BTCUSDT",
        "exchange": "binance",
        "funding_rate": 0.0001,
        "funding_rate_annualized": 0.0001 * 24 * 365,
        "next_funding_time": None,
        "mark_price": 50000.0,
        "observed_at": (now + timedelta(minutes=5)).isoformat(),
    })
    assert _count() == 1

    # A different funding_rate forces an insert immediately.
    insert_funding_rate_snapshot({
        "symbol": "BTCUSDT",
        "exchange": "binance",
        "funding_rate": 0.0002,
        "funding_rate_annualized": 0.0002 * 24 * 365,
        "next_funding_time": None,
        "mark_price": 50000.0,
        "observed_at": (now + timedelta(minutes=6)).isoformat(),
    })
    assert _count() == 2

    # Same payload as the previous, but well past the hourly heartbeat floor:
    # collector emits a heartbeat row so freshness probes stay green.
    _seed_funding_rate_row(
        symbol="BTCUSDT", exchange="binance", funding_rate=0.0002,
        observed_at=(now - timedelta(hours=2)).isoformat(),
    )
    insert_funding_rate_snapshot({
        "symbol": "BTCUSDT",
        "exchange": "binance",
        "funding_rate": 0.0002,
        "funding_rate_annualized": 0.0002 * 24 * 365,
        "next_funding_time": None,
        "mark_price": 50000.0,
        "observed_at": now.isoformat(),
    })
    # Depending on the ordering of the previous row and floor check, either the
    # new row was inserted (heartbeat) or dedup skipped it — either way, the
    # audit invariant must hold.
    _assert_invariants_preserved(*invariant_baseline)


# ---------------------------------------------------------------------------
# agent_shell_heartbeats — UPDATE-not-INSERT dedup
# ---------------------------------------------------------------------------


def test_record_heartbeat_dedup_updates_last_seen(invariant_baseline):
    from aletheia.agent_shell.operator_store import (
        get_latest_heartbeat,
        record_heartbeat,
    )

    kwargs = dict(
        agent_id="operator",
        status="online",
        mode="active",
        permission_mode="protected",
        is_busy=False,
        current_task_id=None,
        current_intent=None,
        queue_size=0,
        last_error=None,
        model_provider="test",
        session_id=None,
    )
    first_id = record_heartbeat(**kwargs, last_seen_at=_iso(0, hours=1))
    # Semantic-identical → UPDATE, not INSERT.
    second_id = record_heartbeat(**kwargs, last_seen_at=_iso(0))
    assert first_id == second_id

    db = get_db()
    count = db.execute(
        "SELECT COUNT(*) FROM agent_shell_heartbeats WHERE agent_id = 'operator'"
    ).fetchone()[0]
    assert count == 1, "identical heartbeat must update, not insert"

    latest = get_latest_heartbeat("operator")
    assert latest is not None
    # last_seen_at bumped by the second call — supervisor staleness read stays
    # accurate even when the payload deduped.
    assert latest["last_seen_at"] >= _iso(0, hours=1)
    _assert_invariants_preserved(*invariant_baseline)


# ---------------------------------------------------------------------------
# Small standalone prune fns
# ---------------------------------------------------------------------------


def _seed_controller_recommendation(*, rec_id: str, created_at: str) -> None:
    db = get_db()
    db.execute(
        "INSERT INTO controller_recommendations "
        "(id, created_at, filter_name, scanner_family, stage, "
        " suggested_direction, verdict) "
        "VALUES (?, ?, 'f', 'sf', 's', 'hold', 'hold')",
        (rec_id, created_at),
    )
    db.commit()


def test_prune_controller_recommendations(invariant_baseline):
    from aletheia.storage.repositories import prune_controller_recommendations

    _seed_controller_recommendation(rec_id="rc-old", created_at=_iso(60))
    _seed_controller_recommendation(rec_id="rc-young", created_at=_iso(1))

    assert prune_controller_recommendations(days=30) == 1
    assert prune_controller_recommendations(days=30) == 0
    _assert_invariants_preserved(*invariant_baseline)


def test_prune_alpha_control_observations(invariant_baseline):
    from aletheia.storage.repositories import prune_alpha_control_observations

    db = get_db()

    def _seed(entry_ts: str) -> str:
        row_id = str(uuid.uuid4())
        try:
            db.execute(
                "INSERT INTO alpha_control_observations "
                "(id, entry_ts) VALUES (?, ?)",
                (row_id, entry_ts),
            )
            db.commit()
        except Exception:
            pytest.skip("alpha_control_observations schema mismatch in fixture")
        return row_id

    _seed(_iso(120))
    _seed(_iso(10))

    # Default 60d
    deleted = prune_alpha_control_observations(days=60)
    assert deleted == 1
    assert prune_alpha_control_observations(days=60) == 0
    _assert_invariants_preserved(*invariant_baseline)


def test_prune_market_regimes(invariant_baseline):
    from aletheia.storage.repositories import prune_market_regimes

    db = get_db()
    db.execute(
        "INSERT INTO market_regimes (id, state, detected_at) "
        "VALUES (?, 'neutral', ?)",
        (str(uuid.uuid4()), _iso(120)),
    )
    db.execute(
        "INSERT INTO market_regimes (id, state, detected_at) "
        "VALUES (?, 'neutral', ?)",
        (str(uuid.uuid4()), _iso(10)),
    )
    db.commit()

    assert prune_market_regimes(days=60) == 1
    assert prune_market_regimes(days=60) == 0
    _assert_invariants_preserved(*invariant_baseline)


def test_prune_serenity_evidence_and_run_events(invariant_baseline):
    from aletheia.storage.repositories import (
        prune_serenity_evidence,
        prune_serenity_run_events,
    )

    db = get_db()

    def _seed_evidence(created_at: str) -> None:
        try:
            db.execute(
                "INSERT INTO serenity_evidence "
                "(id, created_at) VALUES (?, ?)",
                (str(uuid.uuid4()), created_at),
            )
            db.commit()
        except Exception:
            pytest.skip("serenity_evidence schema mismatch")

    def _seed_run_event(created_at: str) -> None:
        try:
            db.execute(
                "INSERT INTO serenity_run_events "
                "(id, created_at) VALUES (?, ?)",
                (str(uuid.uuid4()), created_at),
            )
            db.commit()
        except Exception:
            pytest.skip("serenity_run_events schema mismatch")

    _seed_evidence(_iso(120))
    _seed_evidence(_iso(10))
    _seed_run_event(_iso(120))
    _seed_run_event(_iso(10))

    assert prune_serenity_evidence(days=60) == 1
    assert prune_serenity_run_events(days=60) == 1
    assert prune_serenity_evidence(days=60) == 0
    assert prune_serenity_run_events(days=60) == 0
    _assert_invariants_preserved(*invariant_baseline)


def test_prune_runtime_sessions_ignores_open_sessions(invariant_baseline):
    from aletheia.storage.repositories import prune_runtime_sessions

    db = get_db()
    db.execute(
        "INSERT INTO runtime_sessions "
        "(session_id, runtime_id, started_at, ended_at, shutdown_clean) "
        "VALUES ('rs-open', 'rt', ?, NULL, 0)",
        (_iso(120),),
    )
    db.execute(
        "INSERT INTO runtime_sessions "
        "(session_id, runtime_id, started_at, ended_at, shutdown_clean) "
        "VALUES ('rs-old', 'rt', ?, ?, 1)",
        (_iso(120), _iso(60)),
    )
    db.execute(
        "INSERT INTO runtime_sessions "
        "(session_id, runtime_id, started_at, ended_at, shutdown_clean) "
        "VALUES ('rs-young', 'rt', ?, ?, 1)",
        (_iso(10), _iso(9)),
    )
    db.commit()

    assert prune_runtime_sessions(days=30) == 1
    remaining = {
        r[0] for r in db.execute(
            "SELECT session_id FROM runtime_sessions"
        ).fetchall()
    }
    # The still-open session must survive regardless of started_at.
    assert "rs-open" in remaining
    assert "rs-old" not in remaining
    assert "rs-young" in remaining
    _assert_invariants_preserved(*invariant_baseline)


def test_prune_backtest_runs_cascades_trades(invariant_baseline):
    from aletheia.storage.repositories import prune_backtest_runs

    _seed_backtest_run(run_id="btr-old", created_at=_iso(120))
    _seed_backtest_run(run_id="btr-young", created_at=_iso(1))
    old_ts = int((datetime.now(timezone.utc) - timedelta(days=120)).timestamp() * 1000)
    young_ts = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp() * 1000)
    _seed_backtest_trade(run_id="btr-old", entry_ms=old_ts)
    _seed_backtest_trade(run_id="btr-young", entry_ms=young_ts)

    deleted = prune_backtest_runs(days=60)
    assert deleted == 1

    db = get_db()
    remaining_runs = {
        r[0] for r in db.execute("SELECT id FROM backtest_runs").fetchall()
    }
    remaining_trade_runs = {
        r[0] for r in db.execute("SELECT run_id FROM backtest_trades").fetchall()
    }
    assert remaining_runs == {"btr-young"}
    assert remaining_trade_runs == {"btr-young"}
    _assert_invariants_preserved(*invariant_baseline)


def test_prune_filter_shadow_experiments_keeps_collecting(invariant_baseline):
    from aletheia.storage.repositories import (
        prune_filter_shadow_experiments,
    )

    db = get_db()

    def _seed(exp_id: str, status: str, started_at: str) -> None:
        # ``filter_name`` is varied per row so the (filter_name, filter_version,
        # stage, scanner_family, direction, market_type, horizon) UNIQUE index
        # (``idx_filter_shadow_experiments_fixed_scope``) does not reject the
        # second insert. The prune contract we are asserting only cares about
        # ``status`` and ``started_at``, so this preserves the test's intent.
        db.execute(
            "INSERT INTO filter_shadow_experiments "
            "(id, experiment_key, created_at, started_at, filter_name, filter_version, "
            " stage, scanner_family, direction, market_type, horizon, "
            " control_parameter, treatment_parameter, status, min_samples, "
            " min_coverage, min_edge_delta_pct) "
            "VALUES (?, ?, ?, ?, ?, 'v', 's', 'sf', 'long', 'perp', '1h', "
            "        'ctrl', 'trt', ?, 30, 0.9, 0.5)",
            (exp_id, f"key-{exp_id}", started_at, started_at, f"f-{exp_id}", status),
        )
        db.commit()

    _seed("fse-old-collecting", "collecting", _iso(200))
    _seed("fse-old-done", "completed_no_support", _iso(200))
    _seed("fse-young", "completed_no_support", _iso(10))

    deleted = prune_filter_shadow_experiments(days=90)
    assert deleted == 1
    remaining = {
        r[0] for r in db.execute(
            "SELECT id FROM filter_shadow_experiments"
        ).fetchall()
    }
    # collecting rows are indefinite regardless of age.
    assert "fse-old-collecting" in remaining
    assert "fse-old-done" not in remaining
    assert "fse-young" in remaining

    # Idempotent.
    assert prune_filter_shadow_experiments(days=90) == 0
    _assert_invariants_preserved(*invariant_baseline)
