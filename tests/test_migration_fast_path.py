from __future__ import annotations

import inspect
import ast
from pathlib import Path
import sqlite3

import pytest


def _connection(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _production_migration_env(monkeypatch, migrations, connection) -> list[str]:
    applied: list[str] = []
    monkeypatch.setattr(migrations, "get_db", lambda: connection)
    monkeypatch.setattr(migrations, "_is_pytest", lambda: False)
    monkeypatch.setattr(migrations, "acquire_write_lock", lambda **_kwargs: 0.0)
    monkeypatch.setattr(migrations, "release_write_lock", lambda: None)

    def apply_step(db):
        applied.append("baseline")
        db.execute("CREATE TABLE domain_state (id INTEGER PRIMARY KEY)")

    monkeypatch.setattr(
        migrations,
        "_MIGRATIONS",
        (
            migrations.MigrationStep(
                migration_id="20260806_0001_legacy_schema_v2",
                checksum="a" * 64,
                schema_version="aletheia-sqlite-v2",
                apply=apply_step,
            ),
        ),
    )
    monkeypatch.setattr(
        migrations,
        "_legacy_baseline_implementation_checksum",
        lambda: "a" * 64,
    )
    return applied


def test_production_migrations_persist_stable_ledger_and_apply_once(tmp_path, monkeypatch):
    import aletheia.storage.migrations as migrations

    database = tmp_path / "app.db"
    connection = _connection(database)
    applied = _production_migration_env(monkeypatch, migrations, connection)

    assert migrations.apply_migrations() is True
    assert migrations.apply_migrations() is False
    assert applied == ["baseline"]

    row = connection.execute(
        "SELECT migration_id, checksum, schema_version, duration_ms "
        "FROM aletheia_schema_migrations"
    ).fetchone()
    assert dict(row) == {
        "migration_id": "20260806_0001_legacy_schema_v2",
        "checksum": "a" * 64,
        "schema_version": "aletheia-sqlite-v2",
        "duration_ms": pytest.approx(row["duration_ms"], abs=0.01),
    }
    assert row["duration_ms"] >= 0

    connection.close()
    reopened = _connection(database)
    monkeypatch.setattr(migrations, "get_db", lambda: reopened)
    assert migrations.apply_migrations() is False
    assert applied == ["baseline"]
    reopened.close()


def test_production_migrations_emit_storage_owned_progress(tmp_path, monkeypatch):
    import aletheia.storage.migrations as migrations

    connection = _connection(tmp_path / "app.db")
    _production_migration_env(monkeypatch, migrations, connection)
    events: list[migrations.MigrationProgressEvent] = []

    assert migrations.apply_migrations(progress=events.append) is True

    assert [event.phase for event in events] == ["started", "completed"]
    assert events[0].step_index == 1
    assert events[0].step_count == 1
    assert events[0].estimated_remaining_sec is not None
    assert "CASI" in str(events[0].estimate_basis)
    assert events[1].duration_sec is not None
    connection.close()


def test_migration_checksum_drift_fails_closed_without_replaying(tmp_path, monkeypatch):
    import aletheia.storage.migrations as migrations

    connection = _connection(tmp_path / "app.db")
    applied = _production_migration_env(monkeypatch, migrations, connection)
    assert migrations.apply_migrations() is True

    def must_not_run(_db):
        raise AssertionError("checksum drift must never replay an applied migration")

    monkeypatch.setattr(
        migrations,
        "_MIGRATIONS",
        (
            migrations.MigrationStep(
                migration_id="20260806_0001_legacy_schema_v2",
                checksum="b" * 64,
                schema_version="aletheia-sqlite-v2",
                apply=must_not_run,
            ),
        ),
    )
    monkeypatch.setattr(
        migrations,
        "_legacy_baseline_implementation_checksum",
        lambda: "b" * 64,
    )

    with pytest.raises(migrations.MigrationChecksumMismatch):
        migrations.apply_migrations()
    assert applied == ["baseline"]
    connection.close()


def test_failed_migration_is_not_recorded_and_can_retry(tmp_path, monkeypatch):
    import aletheia.storage.migrations as migrations

    connection = _connection(tmp_path / "app.db")
    _production_migration_env(monkeypatch, migrations, connection)

    def fail(_db):
        raise RuntimeError("migration interrupted")

    failing = migrations.MigrationStep(
        migration_id="20260806_0001_legacy_schema_v2",
        checksum="a" * 64,
        schema_version="aletheia-sqlite-v2",
        apply=fail,
    )
    monkeypatch.setattr(migrations, "_MIGRATIONS", (failing,))
    with pytest.raises(RuntimeError, match="interrupted"):
        migrations.apply_migrations()
    assert connection.execute(
        "SELECT COUNT(*) FROM aletheia_schema_migrations"
    ).fetchone()[0] == 0

    applied: list[str] = []
    succeeding = migrations.MigrationStep(
        migration_id=failing.migration_id,
        checksum=failing.checksum,
        schema_version=failing.schema_version,
        apply=lambda db: applied.append("retried"),
    )
    monkeypatch.setattr(migrations, "_MIGRATIONS", (succeeding,))
    assert migrations.apply_migrations() is True
    assert applied == ["retried"]
    assert connection.execute(
        "SELECT COUNT(*) FROM aletheia_schema_migrations"
    ).fetchone()[0] == 1
    connection.close()


def test_read_only_verification_reports_pending_without_creating_ledger(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.migrations as migrations
    from aletheia.storage import sqlite as sqlite_store

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    sqlite_store.reset_db()
    with sqlite3.connect(tmp_path / "app.db") as connection:
        connection.execute("CREATE TABLE existing_state (id INTEGER PRIMARY KEY)")

    with pytest.raises(migrations.PendingMigrationsError) as exc_info:
        migrations.assert_migrations_current()
    assert "20260806_0001_legacy_schema_v2" in str(exc_info.value)

    with sqlite3.connect(tmp_path / "app.db") as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert tables == {"existing_state"}
    sqlite_store.reset_db()


def test_legacy_migration_checksum_matches_immutable_implementation():
    import aletheia.storage.migrations as migrations

    legacy = migrations._MIGRATIONS[0]
    assert legacy.checksum == migrations._legacy_baseline_implementation_checksum()


def test_gateway_startup_has_one_explicit_migration_owner_and_read_only_children():
    from aletheia import _gateway_runner
    from aletheia.gateway import lifecycle
    from aletheia.runtime_continuity import processes
    from aletheia.server import app as server_app

    lifecycle_source = inspect.getsource(lifecycle.do_start)
    assert "_apply_gateway_migrations" in lifecycle_source

    assert "run_migrations" not in inspect.getsource(processes.current_runtime_record)
    assert "apply_migrations" not in inspect.getsource(processes.current_runtime_record)
    assert "run_migrations" not in inspect.getsource(_gateway_runner)
    assert "apply_migrations" not in inspect.getsource(_gateway_runner)

    lifespan_source = inspect.getsource(server_app.lifespan)
    assert "assert_migrations_current" in lifespan_source
    assert "run_migrations" not in lifespan_source
    assert "apply_migrations" not in lifespan_source
    assert "reindex_all" not in lifespan_source


def test_startup_dataset_boundary_is_a_versioned_one_time_migration(monkeypatch):
    import aletheia.knowledge.retrieval as retrieval
    import aletheia.storage.migrations as migrations

    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE agent_runs (id TEXT PRIMARY KEY, status TEXT, started_at TEXT)"
    )
    connection.execute(
        "CREATE TABLE universe_scanner_runs "
        "(id TEXT PRIMARY KEY, status TEXT, started_at TEXT)"
    )
    connection.execute(
        "CREATE TABLE runtime_cost_events (id TEXT PRIMARY KEY, started_at TEXT)"
    )
    connection.execute(
        "CREATE TABLE alpha_control_observations "
        "(id TEXT PRIMARY KEY, layer TEXT, created_at TEXT)"
    )
    reindexed: list[tuple[sqlite3.Connection, bool]] = []
    monkeypatch.setattr(
        retrieval,
        "reindex_all",
        lambda *, db, strict: reindexed.append((db, strict)) or 0,
    )

    migrations._apply_startup_dataset_boundary_v3(connection)

    indexes = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    }
    assert "idx_agent_runs_status_started" in indexes
    assert "idx_universe_scanner_runs_status_started" in indexes
    assert "idx_runtime_cost_events_normalized_started" in indexes
    assert "idx_alpha_control_layer_created" in indexes
    assert reindexed == [(connection, True)]
    assert [step.schema_version for step in migrations._MIGRATIONS] == [
        f"aletheia-sqlite-v{version}" for version in range(2, 32)
    ]
    assert migrations.SCHEMA_VERSION == "aletheia-sqlite-v31"
    connection.close()


def test_position_protection_v9_migration_is_additive_and_idempotent():
    import aletheia.storage.migrations as migrations

    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE positions (id TEXT PRIMARY KEY)")

    migrations._apply_position_protection_state_v9(connection)
    migrations._apply_position_protection_state_v9(connection)

    position_columns = [
        row[1] for row in connection.execute("PRAGMA table_info(positions)")
    ]
    assert position_columns.count("exit_plan_effective_at") == 1
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {
        "position_exit_plan_versions",
        "position_protection_state",
        "position_protection_triggers",
    } <= tables
    indexes = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    }
    assert "idx_position_exit_plan_versions_effective" in indexes
    triggers = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        )
    }
    assert {
        "trg_position_exit_plan_versions_no_update",
        "trg_position_exit_plan_versions_no_delete",
        "trg_position_protection_state_trigger_write_once",
        "trg_position_protection_state_no_delete",
        "trg_position_protection_triggers_no_update",
        "trg_position_protection_triggers_no_delete",
    } <= triggers
    connection.close()


def test_position_realtime_revision_v30_tracks_every_change():
    import aletheia.storage.migrations as migrations

    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE positions (id TEXT PRIMARY KEY, current_price REAL)"
    )

    migrations._apply_position_realtime_revision_v30(connection)
    migrations._apply_position_realtime_revision_v30(connection)
    connection.execute("INSERT INTO positions (id,current_price) VALUES ('p1',100)")
    connection.execute("UPDATE positions SET current_price=101 WHERE id='p1'")
    connection.execute("DELETE FROM positions WHERE id='p1'")

    assert connection.execute(
        "SELECT revision FROM position_realtime_revision WHERE singleton=1"
    ).fetchone()[0] == 3
    triggers = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='trigger' AND name LIKE 'trg_positions_realtime_revision_%'"
        )
    }
    assert triggers == {
        "trg_positions_realtime_revision_insert",
        "trg_positions_realtime_revision_update",
        "trg_positions_realtime_revision_delete",
    }
    connection.close()


def test_persona_rollout_v31_is_additive_and_events_are_append_only():
    import aletheia.storage.migrations as migrations

    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE positions (id TEXT PRIMARY KEY)")

    migrations._apply_persona_rollout_v31(connection)
    migrations._apply_persona_rollout_v31(connection)

    columns = [row[1] for row in connection.execute("PRAGMA table_info(positions)")]
    assert columns.count("sizing_manifest") == 1
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"persona_rollouts", "persona_rollout_events"} <= tables
    connection.execute(
        "INSERT INTO persona_rollouts "
        "(rollout_id, persona_id, persona_fingerprint, policy_epoch, "
        "step_started_at, status, authority, reason, created_at, updated_at) "
        "VALUES ('r1','p','fp','epoch','2026-08-17T00:00:00+00:00',"
        "'observing','safe','test','2026-08-17T00:00:00+00:00',"
        "'2026-08-17T00:00:00+00:00')"
    )
    connection.execute(
        "INSERT INTO persona_rollout_events "
        "(event_id, rollout_id, step, status, event_type, authority, reason, "
        "actor, source, created_at) VALUES "
        "('e1','r1',0,'observing','started','safe','test','system','test',"
        "'2026-08-17T00:00:00+00:00')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "UPDATE persona_rollout_events SET reason='changed' WHERE event_id='e1'"
        )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("DELETE FROM persona_rollout_events WHERE event_id='e1'")
    connection.close()


def test_agent_usage_storage_v20_is_additive_and_keeps_legacy_null():
    import aletheia.storage.migrations as migrations

    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE agent_runs (id TEXT PRIMARY KEY);
        CREATE TABLE runtime_sessions (session_id TEXT PRIMARY KEY);
        INSERT INTO runtime_sessions VALUES ('legacy-session');
        """
    )

    migrations._apply_agent_usage_storage_v20(connection)
    migrations._apply_agent_usage_storage_v20(connection)

    assert connection.execute(
        "SELECT economics_tracing_enabled FROM runtime_sessions "
        "WHERE session_id='legacy-session'"
    ).fetchone()[0] is None
    assert connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='agent_run_artifacts'"
    ).fetchone()
    assert connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' "
        "AND name='idx_agent_run_artifacts_artifact'"
    ).fetchone()
    artifact_columns = {
        row[1]: row for row in connection.execute("PRAGMA table_info(agent_run_artifacts)")
    }
    assert artifact_columns["created_at"][3] == 1
    assert "datetime('now')" in str(artifact_columns["created_at"][4])
    connection.close()


def test_learning_fixed_look_v10_migration_is_additive_and_idempotent():
    import aletheia.storage.migrations as migrations

    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE learning_experiments (experiment_id TEXT PRIMARY KEY);
        CREATE TABLE learning_contribution_verdicts (
            verdict_id TEXT PRIMARY KEY,
            experiment_id TEXT NOT NULL REFERENCES learning_experiments(experiment_id)
        );
        """
    )

    migrations._apply_learning_fixed_look_v10(connection)
    migrations._apply_learning_fixed_look_v10(connection)

    verdict_columns = {
        row[1] for row in connection.execute(
            "PRAGMA table_info(learning_contribution_verdicts)"
        )
    }
    assert {
        "inference_contract_version",
        "inference_role",
        "authority_eligible",
        "inference_plan_hash",
        "inference_cutoff_fingerprint",
    } <= verdict_columns
    assert connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='learning_inference_decisions'"
    ).fetchone() is not None
    connection.close()


def test_learning_randomization_inference_v12_preserves_v1_and_accepts_v2():
    import aletheia.storage.migrations as migrations

    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE learning_experiments (experiment_id TEXT PRIMARY KEY);
        CREATE TABLE learning_contribution_verdicts (
            verdict_id TEXT PRIMARY KEY,
            experiment_id TEXT NOT NULL REFERENCES learning_experiments(experiment_id)
        );
        INSERT INTO learning_experiments VALUES ('legacy'), ('current');
        """
    )
    migrations._apply_learning_fixed_look_v10(connection)
    insert_sql = """
        INSERT INTO learning_inference_decisions (
            experiment_id, inference_family_id, inference_contract_version,
            plan_json, plan_hash, status, authority_alpha, created_at, updated_at
        ) VALUES (?, ?, ?, '{}', ?, 'collecting', 0.025, ?, ?)
    """
    timestamp = "2026-08-08T00:00:00+00:00"
    connection.execute(
        insert_sql,
        ("legacy", "family-v1", "ufc-fixed-look/v1", "a" * 64, timestamp, timestamp),
    )

    migrations._apply_learning_randomization_inference_v12(connection)
    migrations._apply_learning_randomization_inference_v12(connection)

    assert connection.execute(
        "SELECT inference_contract_version FROM learning_inference_decisions "
        "WHERE experiment_id='legacy'"
    ).fetchone()[0] == "ufc-fixed-look/v1"
    objects = {
        (str(row[0]), str(row[1]))
        for row in connection.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE tbl_name='learning_inference_decisions' AND sql IS NOT NULL"
        ).fetchall()
    }
    assert ("index", "idx_learning_inference_family") in objects
    assert ("trigger", "trg_learning_inference_plan_immutable") in objects
    assert ("trigger", "trg_learning_inference_decisions_no_delete") in objects
    connection.execute(
        insert_sql,
        ("current", "family-v2", "ufc-fixed-look/v2", "b" * 64, timestamp, timestamp),
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "UPDATE learning_inference_decisions SET plan_hash=? "
            "WHERE experiment_id='current'",
            ("c" * 64,),
        )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "DELETE FROM learning_inference_decisions WHERE experiment_id='current'"
        )
    connection.close()


def test_learning_randomization_inference_v12_rolls_back_failed_rebuild(monkeypatch):
    import aletheia.storage.migrations as migrations

    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE learning_experiments (experiment_id TEXT PRIMARY KEY);
        CREATE TABLE learning_contribution_verdicts (
            verdict_id TEXT PRIMARY KEY,
            experiment_id TEXT NOT NULL REFERENCES learning_experiments(experiment_id)
        );
        INSERT INTO learning_experiments VALUES ('legacy');
        """
    )
    migrations._apply_learning_fixed_look_v10(connection)
    monkeypatch.setattr(
        migrations,
        "_LEARNING_RANDOMIZATION_INFERENCE_TABLE_SQL",
        "CREATE TABLE learning_inference_decisions (",
    )

    with pytest.raises(sqlite3.OperationalError):
        migrations._apply_learning_randomization_inference_v12(connection)

    table_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='learning_inference_decisions'"
    ).fetchone()[0]
    assert "ufc-fixed-look/v1" in table_sql
    assert connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
        "AND name='learning_inference_decisions_v1'"
    ).fetchone()[0] == 0
    assert connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' "
        "AND name='trg_learning_inference_plan_immutable'"
    ).fetchone()[0] == 1
    connection.close()


def test_exit_breakeven_shadow_v11_migration_is_additive_and_idempotent():
    import aletheia.storage.migrations as migrations

    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE positions (id TEXT PRIMARY KEY)")

    migrations._apply_exit_breakeven_shadow_v11(connection)
    migrations._apply_exit_breakeven_shadow_v11(connection)

    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {
        "exit_breakeven_shadow_assignments",
        "exit_breakeven_shadow_outcomes",
    } <= tables
    triggers = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        )
    }
    assert {
        "trg_exit_breakeven_assignment_no_update",
        "trg_exit_breakeven_assignment_no_delete",
        "trg_exit_breakeven_outcome_no_update",
        "trg_exit_breakeven_outcome_no_delete",
    } <= triggers
    connection.close()


def test_production_ledger_advances_through_current_schema(tmp_path, monkeypatch):
    import aletheia.storage.migrations as migrations

    connection = _connection(tmp_path / "v9.db")
    connection.executescript(
        """
        CREATE TABLE learning_hypotheses (
            hypothesis_id TEXT PRIMARY KEY,
            fingerprint TEXT,
            path_id TEXT,
            scope_json TEXT,
            action_json TEXT,
            target_horizon TEXT,
            source_type TEXT
        );
        CREATE TABLE learning_experiments (
            experiment_id TEXT PRIMARY KEY,
            hypothesis_id TEXT,
            stage TEXT,
            ended_at TEXT
        );
        CREATE TABLE learning_contribution_verdicts (
            verdict_id TEXT PRIMARY KEY,
            experiment_id TEXT NOT NULL REFERENCES learning_experiments(experiment_id)
        );
        CREATE TABLE universe_feature_snapshots (id TEXT PRIMARY KEY);
        CREATE TABLE open_interest_snapshots (id TEXT PRIMARY KEY);
        CREATE TABLE serenity_research_runs (
            id TEXT PRIMARY KEY,
            parent_run_id TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            idempotency_key TEXT
        );
        CREATE TABLE orders (id TEXT PRIMARY KEY);
        CREATE TABLE shadow_trades (id TEXT PRIMARY KEY);
        CREATE TABLE agent_runs (id TEXT PRIMARY KEY);
        CREATE TABLE agent_tool_calls (id TEXT PRIMARY KEY);
        CREATE TABLE agent_shell_commands (id TEXT PRIMARY KEY);
        CREATE TABLE runtime_sessions (session_id TEXT PRIMARY KEY);
        CREATE TABLE ps_event_log (event_id TEXT PRIMARY KEY, created_at TEXT NOT NULL);
        CREATE TABLE positions (id TEXT PRIMARY KEY);
        INSERT INTO learning_experiments (experiment_id) VALUES ('legacy-experiment');
        INSERT INTO learning_contribution_verdicts
        VALUES ('legacy-positive', 'legacy-experiment');
        """
    )
    migrations._apply_learning_fixed_look_v10(connection)
    migrations._apply_universe_twin_shadow_evidence_v8(connection)
    migrations._apply_position_protection_state_v9(connection)
    connection.executescript(migrations._MIGRATION_LEDGER_DDL)
    connection.executemany(
        "INSERT INTO aletheia_schema_migrations "
        "(migration_id, checksum, schema_version, applied_at, duration_ms) "
        "VALUES (?, ?, ?, '2026-08-08T00:00:00+00:00', 0)",
        [
            (step.migration_id, step.checksum, step.schema_version)
            for step in migrations._MIGRATIONS[:-7]
        ],
    )
    connection.commit()
    monkeypatch.setattr(migrations, "get_db", lambda: connection)
    monkeypatch.setattr(migrations, "_is_pytest", lambda: False)
    monkeypatch.setattr(migrations, "acquire_write_lock", lambda **_kwargs: 0.0)
    monkeypatch.setattr(migrations, "release_write_lock", lambda: None)

    assert migrations.apply_migrations() is True
    assert migrations.apply_migrations() is False

    rows = connection.execute(
        "SELECT migration_id, schema_version FROM aletheia_schema_migrations "
        "ORDER BY migration_id"
    ).fetchall()
    assert len(rows) == len(migrations._MIGRATIONS)
    assert tuple(rows[-1]) == (
        "20260817_0030_persona_rollout_v31",
        "aletheia-sqlite-v31",
    )
    health_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(learning_job_health)")
    }
    assert "input_cursor" in health_columns
    legacy = connection.execute(
        "SELECT inference_role, authority_eligible "
        "FROM learning_contribution_verdicts WHERE verdict_id='legacy-positive'"
    ).fetchone()
    assert tuple(legacy) == ("monitoring", 0)
    connection.close()


def test_event_jsonl_outbox_migration_does_not_replay_historical_rows():
    import aletheia.storage.migrations as migrations

    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE ps_event_log (event_id TEXT PRIMARY KEY, created_at TEXT NOT NULL)"
    )
    connection.execute("INSERT INTO ps_event_log VALUES ('old', '2026-08-01T00:00:00+00:00')")

    migrations._apply_event_jsonl_outbox_v22(connection)
    migrations._apply_event_jsonl_outbox_v22(connection)

    row = connection.execute(
        "SELECT jsonl_mirror_status, jsonl_attempt_count, jsonl_mirrored_at "
        "FROM ps_event_log WHERE event_id='old'"
    ).fetchone()
    assert tuple(row) == ("mirrored", 0, "2026-08-01T00:00:00+00:00")
    connection.close()


def test_exit_trailing_recovery_v23_is_additive_idempotent_and_write_once():
    import aletheia.storage.migrations as migrations

    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE position_protection_state (position_id TEXT PRIMARY KEY)"
    )
    connection.execute("INSERT INTO position_protection_state VALUES ('position-1')")

    migrations._apply_exit_trailing_recovery_v23(connection)
    migrations._apply_exit_trailing_recovery_v23(connection)

    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(position_protection_state)")
    }
    assert {
        "recovery_observation_json",
        "recovery_observation_hash",
    } <= columns
    connection.execute(
        "UPDATE position_protection_state SET recovery_observation_json='{}', "
        "recovery_observation_hash=? WHERE position_id='position-1'",
        ("a" * 64,),
    )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute(
            "UPDATE position_protection_state SET recovery_observation_json='[]' "
            "WHERE position_id='position-1'"
        )
    connection.close()


def test_event_jsonl_outbox_migration_rolls_back_partial_schema(monkeypatch):
    import aletheia.storage.migrations as migrations

    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE ps_event_log (event_id TEXT PRIMARY KEY, created_at TEXT NOT NULL)"
    )
    monkeypatch.setattr(migrations, "_EVENT_JSONL_OUTBOX_INDEX_SQL", "invalid SQL")

    with pytest.raises(sqlite3.OperationalError):
        migrations._apply_event_jsonl_outbox_v22(connection)

    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(ps_event_log)")
    }
    assert "jsonl_mirror_status" not in columns
    connection.close()


def test_learning_hypothesis_identity_v24_is_additive_and_idempotent():
    import aletheia.storage.migrations as migrations

    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE learning_hypotheses (hypothesis_id TEXT PRIMARY KEY)"
    )
    connection.execute("INSERT INTO learning_hypotheses VALUES ('legacy')")

    migrations._apply_learning_hypothesis_identity_v24(connection)
    migrations._apply_learning_hypothesis_identity_v24(connection)

    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(learning_hypotheses)")
    }
    assert {
        "behavior_contract_version",
        "fingerprint_schema_version",
    } <= columns
    assert tuple(
        connection.execute(
            "SELECT behavior_contract_version, fingerprint_schema_version "
            "FROM learning_hypotheses WHERE hypothesis_id='legacy'"
        ).fetchone()
    ) == (None, None)
    connection.close()


def _legacy_active_ufc_db(*, fingerprint: str) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE learning_hypotheses ("
        "hypothesis_id TEXT PRIMARY KEY, fingerprint TEXT, path_id TEXT, "
        "scope_json TEXT, action_json TEXT, target_horizon TEXT, source_type TEXT, "
        "behavior_contract_version TEXT, fingerprint_schema_version INTEGER)"
    )
    connection.execute(
        "CREATE TABLE learning_experiments ("
        "experiment_id TEXT PRIMARY KEY, hypothesis_id TEXT, stage TEXT, ended_at TEXT)"
    )
    connection.execute(
        "INSERT INTO learning_hypotheses VALUES "
        "('hypothesis', ?, 'universe_family_calibration', "
        "'{\"direction\":\"long\",\"family\":\"momentum\",\"market_type\":\"spot\"}', "
        "'{\"multiplier\":1.1,\"type\":\"opportunity_multiplier\"}', "
        "'4h', 'universe_forward_outcomes:clean_net:v2', NULL, NULL)",
        (fingerprint,),
    )
    connection.execute(
        "INSERT INTO learning_experiments VALUES "
        "('experiment', 'hypothesis', 'paper_10', NULL)"
    )
    return connection


def test_ufc_canonical_identity_v25_materializes_verified_active_paper(monkeypatch):
    import aletheia.storage.migrations as migrations
    import aletheia.learning.hypotheses as live_hypotheses

    connection = _legacy_active_ufc_db(
        fingerprint="64b53e9eade95232da73ba2760080dabbbeb8f8297e05de4fbd22b17b96dfec8"
    )
    assert migrations._UFC_CANONICAL_IDENTITY_CHECKSUM == (
        "76eac7e0a1e81de4b043d5004b461d650b4deb61f51be2ec9c5f307ebe2b180a"
    )
    monkeypatch.setattr(
        live_hypotheses,
        "hypothesis_fingerprint",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("live helper used")),
    )

    migrations._apply_ufc_canonical_identity_v25(connection)
    migrations._apply_ufc_canonical_identity_v25(connection)

    assert tuple(connection.execute(
        "SELECT behavior_contract_version, fingerprint_schema_version "
        "FROM learning_hypotheses WHERE hypothesis_id='hypothesis'"
    ).fetchone()) == ("universe-family-calibration/v11", 1)
    connection.close()


def test_ufc_canonical_identity_v25_rejects_unproven_identity():
    import aletheia.storage.migrations as migrations

    connection = _legacy_active_ufc_db(fingerprint="not-the-canonical-fingerprint")

    with pytest.raises(migrations.MigrationStateError):
        migrations._apply_ufc_canonical_identity_v25(connection)

    assert tuple(connection.execute(
        "SELECT behavior_contract_version, fingerprint_schema_version "
        "FROM learning_hypotheses WHERE hypothesis_id='hypothesis'"
    ).fetchone()) == (None, None)
    connection.close()


def test_universe_positioning_v18_is_additive_and_idempotent():
    import aletheia.storage.migrations as migrations

    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE universe_feature_snapshots (id TEXT PRIMARY KEY)")
    connection.execute("CREATE TABLE open_interest_snapshots (id TEXT PRIMARY KEY)")

    migrations._apply_universe_positioning_v18(connection)
    migrations._apply_universe_positioning_v18(connection)

    feature_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(universe_feature_snapshots)")
    }
    oi_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(open_interest_snapshots)")
    }
    assert {"long_short_ratio", "top_trader_position_ratio"} <= feature_columns
    assert "top_trader_position_ratio" in oi_columns
    assert connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
        "AND name='universe_relative_rank_shadow_batches'"
    ).fetchone()[0] == 1
    connection.close()


def test_universe_twin_shadow_migration_is_additive_and_idempotent():
    import aletheia.storage.migrations as migrations

    connection = sqlite3.connect(":memory:")
    migrations._apply_universe_twin_shadow_evidence_v8(connection)
    migrations._apply_universe_twin_shadow_evidence_v8(connection)

    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "universe_twin_shadow_batches" in tables
    assert "universe_twin_shadow_records" in tables
    columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(universe_twin_shadow_records)"
        )
    }
    assert {
        "candidate_index",
        "compact_output_json",
        "input_json",
        "record_hash",
    } <= columns
    assert {
        "assignment_arm",
        "propensity",
        "authority_outcome_candidate_key",
    }.isdisjoint(columns)
    connection.close()


def test_universe_twin_prospective_migration_is_additive_and_idempotent():
    import aletheia.storage.migrations as migrations

    connection = sqlite3.connect(":memory:")
    migrations._apply_universe_twin_shadow_evidence_v8(connection)
    migrations._apply_universe_twin_prospective_v13(connection)
    migrations._apply_universe_twin_prospective_v13(connection)

    batch_columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(universe_twin_shadow_batches)"
        )
    }
    record_columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(universe_twin_shadow_records)"
        )
    }
    assert {
        "experiment_id",
        "behavior_contract_hash",
        "assignment_arm",
        "assignment_propensity",
        "primary_horizon",
        "outcome_contract_version",
    } <= batch_columns
    assert {
        "policy_epoch_id",
        "assignment_unit_key",
        "treatment_coefficient",
        "metric_version",
        "outcome_candidate_key",
        "assigned_policy_selected",
    } <= record_columns
    connection.close()


def test_technical_snapshot_metadata_v14_is_additive_and_idempotent():
    import aletheia.storage.migrations as migrations

    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE universe_feature_snapshots (id TEXT PRIMARY KEY)"
    )

    migrations._apply_technical_snapshot_metadata_v14(connection)
    migrations._apply_technical_snapshot_metadata_v14(connection)

    columns = {
        row[1]: row
        for row in connection.execute(
            "PRAGMA table_info(universe_feature_snapshots)"
        )
    }
    assert "technical_algorithm_version" in columns
    assert columns["technical_snapshot_manifest"][3] == 1
    assert columns["technical_snapshot_manifest"][4] == "'{}'"
    connection.close()


def test_execution_reference_v16_is_additive_idempotent_and_immutable():
    import aletheia.storage.migrations as migrations

    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE orders (id TEXT PRIMARY KEY)")

    migrations._apply_execution_reference_v16(connection)
    migrations._apply_execution_reference_v16(connection)

    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(orders)")
    }
    assert {"reference_price", "reference_price_source"} <= columns
    connection.execute(
        "INSERT INTO orders (id, reference_price, reference_price_source) "
        "VALUES ('order-1', 100, 'risk_verified_proposal_price')"
    )
    with pytest.raises(sqlite3.IntegrityError, match="execution reference is immutable"):
        connection.execute(
            "UPDATE orders SET reference_price=101 WHERE id='order-1'"
        )
    connection.close()


def test_serenity_retry_idempotency_v15_protects_new_attempts_without_rewriting_legacy():
    import aletheia.storage.migrations as migrations

    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE serenity_research_runs ("
        "id TEXT PRIMARY KEY, parent_run_id TEXT, retry_count INTEGER NOT NULL, "
        "idempotency_key TEXT)"
    )
    connection.executemany(
        "INSERT INTO serenity_research_runs VALUES (?, 'parent', 1, NULL)",
        [("legacy-a",), ("legacy-b",)],
    )

    migrations._apply_serenity_retry_idempotency_v15(connection)
    migrations._apply_serenity_retry_idempotency_v15(connection)
    connection.execute(
        "INSERT INTO serenity_research_runs VALUES "
        "('new-a', 'parent', 2, 'serenity-retry:parent:2')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO serenity_research_runs VALUES "
            "('new-b', 'parent', 2, 'serenity-retry:parent:2')"
        )
    connection.close()


def test_universe_twin_prospective_v13_rolls_back_partial_failure(monkeypatch):
    import aletheia.storage.migrations as migrations

    connection = sqlite3.connect(":memory:")
    migrations._apply_universe_twin_shadow_evidence_v8(connection)
    before = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(universe_twin_shadow_batches)"
        )
    }
    monkeypatch.setattr(
        migrations,
        "_UNIVERSE_TWIN_PROSPECTIVE_COLUMNS",
        migrations._UNIVERSE_TWIN_PROSPECTIVE_COLUMNS
        + (("forced_failure", "missing_twin_table", "TEXT"),),
    )

    with pytest.raises(sqlite3.OperationalError):
        migrations._apply_universe_twin_prospective_v13(connection)

    after = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(universe_twin_shadow_batches)"
        )
    }
    assert after == before
    connection.close()


def test_capacity_guardrail_migration_drops_only_redundant_indexes():
    import aletheia.storage.migrations as migrations

    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE filter_candidate_outcomes (
            id TEXT PRIMARY KEY,
            outcome_candidate_key TEXT NOT NULL,
            horizon TEXT NOT NULL,
            UNIQUE(outcome_candidate_key, horizon)
        );
        CREATE INDEX idx_filter_outcomes_candidate
        ON filter_candidate_outcomes(outcome_candidate_key, horizon);
        CREATE INDEX idx_filter_candidate_outcomes_key
        ON filter_candidate_outcomes(outcome_candidate_key);
        CREATE TABLE market_ohlcv (
            exchange TEXT NOT NULL,
            symbol TEXT NOT NULL,
            market_type TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            ts TEXT NOT NULL,
            PRIMARY KEY(exchange, symbol, market_type, timeframe, ts)
        );
        CREATE INDEX idx_market_ohlcv_instrument_tf_ts
        ON market_ohlcv(exchange, symbol, market_type, timeframe, ts);
        CREATE TABLE audit_events (id TEXT PRIMARY KEY, event_type TEXT, timestamp TEXT);
        """
    )

    migrations._apply_capacity_guardrails_v4(connection)

    indexes = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    }
    assert "idx_filter_outcomes_candidate" not in indexes
    assert "idx_filter_candidate_outcomes_key" not in indexes
    assert "idx_market_ohlcv_instrument_tf_ts" not in indexes
    assert "idx_audit_events_type_timestamp" in indexes
    outcome_plan = connection.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM filter_candidate_outcomes "
        "WHERE outcome_candidate_key=? AND horizon=?",
        ("candidate", "4h"),
    ).fetchone()[3]
    ohlcv_plan = connection.execute(
        "EXPLAIN QUERY PLAN SELECT ts FROM market_ohlcv "
        "WHERE exchange=? AND symbol=? AND market_type=? AND timeframe=? "
        "ORDER BY ts DESC LIMIT 1",
        ("okx", "BTC/USDT", "spot", "1m"),
    ).fetchone()[3]
    assert "sqlite_autoindex_filter_candidate_outcomes_2" in outcome_plan
    assert "sqlite_autoindex_market_ohlcv_1" in ohlcv_plan
    connection.close()


def test_pending_outcome_partial_index_covers_due_queue_only():
    import aletheia.storage.migrations as migrations

    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE filter_candidate_outcomes "
        "(id TEXT PRIMARY KEY, status TEXT NOT NULL, due_at TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE INDEX idx_filter_outcomes_status_due "
        "ON filter_candidate_outcomes(status,due_at)"
    )

    migrations._apply_pending_outcome_partial_index_v5(connection)

    indexes = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    }
    assert "idx_filter_outcomes_status_due" not in indexes
    assert "idx_filter_outcomes_pending_due" in indexes
    pending_plan = connection.execute(
        "EXPLAIN QUERY PLAN SELECT id FROM filter_candidate_outcomes "
        "WHERE status='pending' AND due_at <= ? ORDER BY due_at,id LIMIT 10",
        ("2026-08-06T00:00:00+00:00",),
    ).fetchone()[3]
    settled_plan = connection.execute(
        "EXPLAIN QUERY PLAN SELECT id FROM filter_candidate_outcomes "
        "WHERE status='settled' AND due_at <= ?",
        ("2026-08-06T00:00:00+00:00",),
    ).fetchone()[3]
    assert "idx_filter_outcomes_pending_due" in pending_plan
    assert "idx_filter_outcomes_pending_due" not in settled_plan
    connection.close()


def test_llm_inference_linkage_migration_is_additive_and_idempotent():
    import aletheia.storage.migrations as migrations

    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE agent_shell_commands (id TEXT PRIMARY KEY);
        CREATE TABLE agent_tool_calls (
            id TEXT PRIMARY KEY,
            inference_call_id TEXT
        );
        """
    )

    migrations._apply_llm_inference_linkage_v6(connection)
    migrations._apply_llm_inference_linkage_v6(connection)

    for table in ("agent_shell_commands", "agent_tool_calls"):
        columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
        assert columns.count("inference_call_id") == 1
    connection.close()


def test_market_signal_identity_migration_is_additive_and_idempotent():
    import aletheia.storage.migrations as migrations

    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE positions ("
        "id TEXT PRIMARY KEY, last_market_signal_action TEXT)"
    )

    migrations._apply_market_signal_identity_v7(connection)
    migrations._apply_market_signal_identity_v7(connection)

    columns = [
        row[1] for row in connection.execute("PRAGMA table_info(positions)")
    ]
    assert columns.count("last_market_signal_evidence") == 1
    assert columns.count("last_market_signal_action") == 1
    connection.close()


def test_migration_progress_display_explains_eta_as_a_range():
    from io import StringIO

    from rich.console import Console

    from aletheia.cli.migration_progress import MigrationProgressDisplay
    from aletheia.storage.migrations import MigrationProgressEvent

    output = StringIO()
    console = Console(file=output, force_terminal=False, width=160)
    with MigrationProgressDisplay(console=console) as progress:
        progress(MigrationProgressEvent(
            phase="started",
            migration_id="migration-a",
            description="large compatibility migration",
            step_index=1,
            step_count=1,
            estimated_remaining_sec=(20.0, 50.0),
            estimate_basis="broad local calibration",
        ))
        progress(MigrationProgressEvent(
            phase="completed",
            migration_id="migration-a",
            description="large compatibility migration",
            step_index=1,
            step_count=1,
            duration_sec=31.0,
            estimated_remaining_sec=(0.0, 0.0),
        ))

    rendered = output.getvalue()
    assert "Completed 1/1" in rendered
    assert "31s" in rendered
    assert "complete" in rendered


def test_production_code_has_no_legacy_implicit_run_migrations_callers():
    package_root = Path(__file__).resolve().parents[1] / "aletheia"
    offenders: list[str] = []
    for path in package_root.rglob("*.py"):
        if path.name == "migrations.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "aletheia.storage.migrations":
                if any(alias.name == "run_migrations" for alias in node.names):
                    offenders.append(f"{path.relative_to(package_root)}:{node.lineno}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "run_migrations":
                    offenders.append(f"{path.relative_to(package_root)}:{node.lineno}")
    assert offenders == []


def test_database_ahead_of_code_fails_closed(tmp_path, monkeypatch):
    import aletheia.storage.migrations as migrations

    connection = _connection(tmp_path / "app.db")
    _production_migration_env(monkeypatch, migrations, connection)
    connection.executescript(migrations._MIGRATION_LEDGER_DDL)
    connection.execute(
        "INSERT INTO aletheia_schema_migrations "
        "(migration_id, checksum, schema_version, applied_at, duration_ms) "
        "VALUES ('20990101_9999_future', ?, 'future', datetime('now'), 1)",
        ("f" * 64,),
    )
    connection.commit()

    with pytest.raises(migrations.DatabaseSchemaAheadError):
        migrations.apply_migrations()
    connection.close()


def test_migration_ledger_rejects_schema_version_drift(tmp_path, monkeypatch):
    import aletheia.storage.migrations as migrations

    connection = _connection(tmp_path / "app.db")
    _production_migration_env(monkeypatch, migrations, connection)
    assert migrations.apply_migrations() is True
    connection.execute(
        "UPDATE aletheia_schema_migrations SET schema_version='corrupt'"
    )
    connection.commit()

    with pytest.raises(migrations.MigrationStateError, match="schema version mismatch"):
        migrations.apply_migrations()
    connection.close()


def test_migration_ledger_rejects_an_applied_step_after_a_gap(tmp_path, monkeypatch):
    import aletheia.storage.migrations as migrations

    connection = _connection(tmp_path / "app.db")
    _production_migration_env(monkeypatch, migrations, connection)
    first, second = (
        migrations.MigrationStep(
            migration_id=migration_id,
            checksum=checksum * 64,
            schema_version="aletheia-sqlite-v2",
            apply=lambda _db: None,
        )
        for migration_id, checksum in (
            ("20260806_0001_legacy_schema_v2", "a"),
            ("20260807_0002_example", "b"),
        )
    )
    monkeypatch.setattr(migrations, "_MIGRATIONS", (first, second))
    connection.executescript(migrations._MIGRATION_LEDGER_DDL)
    connection.execute(
        "INSERT INTO aletheia_schema_migrations "
        "(migration_id, checksum, schema_version, applied_at, duration_ms) "
        "VALUES (?, ?, ?, datetime('now'), 1)",
        (second.migration_id, second.checksum, second.schema_version),
    )
    connection.commit()

    with pytest.raises(migrations.MigrationStateError, match="contiguous prefix"):
        migrations.apply_migrations()
    connection.close()


def test_position_path_observations_v26_distinguishes_available_evidence_contracts():
    import aletheia.storage.migrations as migrations

    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE positions (id TEXT PRIMARY KEY)")
    connection.execute("INSERT INTO positions VALUES ('position-1')")
    migrations._apply_position_path_observations_v26(connection)

    table_info = {
        row[1]: row[5]
        for row in connection.execute("PRAGMA table_info(position_path_observations)")
    }
    assert table_info["position_id"] == 1
    assert table_info["bucket_open_ms"] == 2
    triggers = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        )
    }
    assert {
        "trg_position_path_observations_no_update",
        "trg_position_path_observations_no_delete",
    } <= triggers

    columns = (
        "position_id, bucket_open_ms, observed_at, symbol, market_type, direction, "
        "entry_price, quantity, funding_source, exit_plan_version, evidence_status, "
        "evidence_reason, capture_mode"
    )
    connection.execute(
        f"INSERT INTO position_path_observations ({columns}) VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "position-1",
            0,
            "2026-08-13T00:00:00+00:00",
            "BTC/USDT",
            None,
            None,
            None,
            None,
            "unavailable",
            0,
            "unavailable",
            "position fields unavailable",
            "post_close_final_snapshot",
        ),
    )

    available_columns = (
        f"{columns}, venue, bar_open, bar_high, bar_low, bar_close, bar_volume, "
        "bar_provider"
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            f"INSERT INTO position_path_observations ({available_columns}) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "position-1",
                300000,
                "2026-08-13T00:05:00+00:00",
                "BTC/USDT",
                None,
                None,
                None,
                None,
                "exchange",
                0,
                "available",
                "complete_exact_5m_bar",
                "open_snapshot",
                "binance",
                100.0,
                101.0,
                99.0,
                100.5,
                1.0,
                "binance",
            ),
        )
    connection.execute(
        f"INSERT INTO position_path_observations ({available_columns}) VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "position-1",
            300000,
            "2026-08-13T00:05:00+00:00",
            "BTC/USDT",
            "spot",
            "long",
            100.0,
            1.0,
            "exchange",
            0,
            "available",
            "complete_exact_5m_bar",
            "open_snapshot",
            "binance",
            100.0,
            101.0,
            99.0,
            100.5,
            1.0,
            "binance",
        ),
    )
    assert [
        row[0]
        for row in connection.execute(
            "SELECT capture_mode FROM position_path_observations "
            "ORDER BY bucket_open_ms"
        )
    ] == ["post_close_final_snapshot", "open_snapshot"]
    connection.close()
