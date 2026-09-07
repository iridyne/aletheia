"""Unit tests for LessonStore and SkillStore CRUD lifecycle.

Covers the direct store-layer behavior that spec_learning_pipeline.py
(indirect pipeline claims) and learning_contract.md (data model) assume
but never exercise end-to-end:

- LessonStore.create → accept → mark_applied → archive state machine
- LessonStore guard timestamps (activated_at / dry_run_until / expires_at)
- SkillStore.create with FORBIDDEN_LAYERS enforcement
- SkillStore.create → activate → deprecate state machine
"""

import json
import sqlite3
from datetime import datetime

import pytest

import aletheia.config.loader as loader
import aletheia.storage.sqlite as sq
from aletheia.storage.migrations import run_migrations
from aletheia.learning.lessons import LessonStore
from aletheia.learning.skills import SkillStore, SkillPermissionError


def _setup_test_db(tmp_path, monkeypatch):
    """Wire a fresh SQLite test DB for store tests."""
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "knowledge" / "post_trade_reviews").mkdir(parents=True, exist_ok=True)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    sq._db_conn = conn
    run_migrations()


def _teardown():
    sq._db_conn = None


def _setup_legacy_skill_db(tmp_path, monkeypatch) -> sqlite3.Connection:
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq.reset_db()
    conn = sqlite3.connect(str(tmp_path / "legacy.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    sq._db_conn = conn
    conn.executescript(
        """
        CREATE TABLE ps_lessons (
            lesson_id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            category TEXT NOT NULL,
            severity TEXT NOT NULL,
            summary TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            recommendation_json TEXT,
            status TEXT NOT NULL,
            artifact_path TEXT,
            created_at TEXT NOT NULL,
            applied_policy_version TEXT,
            activated_at TEXT,
            dry_run_until TEXT,
            expires_at TEXT
        );
        CREATE TABLE ps_skills (
            skill_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            scope TEXT NOT NULL,
            description TEXT NOT NULL,
            inputs_json TEXT NOT NULL,
            outputs_json TEXT NOT NULL,
            risk_level TEXT NOT NULL,
            allowed_layers_json TEXT NOT NULL,
            forbidden_layers_json TEXT NOT NULL,
            created_from_lessons_json TEXT,
            version INTEGER NOT NULL,
            artifact_path TEXT,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL
        );
        """
    )
    return conn


def test_skill_lineage_migration_backfills_unambiguous_legacy_rows(tmp_path, monkeypatch):
    conn = _setup_legacy_skill_db(tmp_path, monkeypatch)
    conn.execute(
        "INSERT INTO ps_lessons (lesson_id, source_type, source_id, category, severity, "
        "summary, evidence_json, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("lesson-1", "trade_case_study", "case-1", "general", "info", "summary", "{}", "applied", "2026-08-27"),
    )
    conn.execute(
        "INSERT INTO ps_skills (skill_id, name, scope, description, inputs_json, outputs_json, "
        "risk_level, allowed_layers_json, forbidden_layers_json, created_from_lessons_json, "
        "version, artifact_path, created_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("skill-1", "lesson:lesson-1", "learning_advisory", "desc", "[]", "[]", "low", "[]", "[]", None, 1, None, "2026-08-27", "draft"),
    )
    conn.commit()

    run_migrations()

    row = conn.execute("SELECT source_lesson_id, created_from_lessons_json FROM ps_skills").fetchone()
    assert row["source_lesson_id"] == "lesson-1"
    assert json.loads(row["created_from_lessons_json"]) == ["lesson-1"]
    index = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_ps_skills_source_lesson_unique'"
    ).fetchone()
    assert index is not None
    _teardown()


def test_skill_lineage_migration_refuses_ambiguous_legacy_rows(tmp_path, monkeypatch):
    conn = _setup_legacy_skill_db(tmp_path, monkeypatch)
    conn.execute(
        "INSERT INTO ps_lessons (lesson_id, source_type, source_id, category, severity, "
        "summary, evidence_json, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("lesson-dup", "trade_case_study", "case-1", "general", "info", "summary", "{}", "applied", "2026-08-27"),
    )
    for skill_id in ("skill-a", "skill-b"):
        conn.execute(
            "INSERT INTO ps_skills (skill_id, name, scope, description, inputs_json, outputs_json, "
            "risk_level, allowed_layers_json, forbidden_layers_json, created_from_lessons_json, "
            "version, artifact_path, created_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (skill_id, "lesson:lesson-dup", "learning_advisory", "desc", "[]", "[]", "low", "[]", "[]", None, 1, None, "2026-08-27", "draft"),
        )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="ambiguous lesson-generated skill lineage"):
        run_migrations()
    _teardown()


# ---------------------------------------------------------------------------
# LessonStore — create
# ---------------------------------------------------------------------------


class TestLessonStoreCreate:
    def test_create_lesson_default_status_proposed(self, tmp_path, monkeypatch):
        _setup_test_db(tmp_path, monkeypatch)
        store = LessonStore()
        lesson = store.create(
            summary="Test lesson on BTC long",
            source_type="dreaming_agent",
            source_id="run-001",
            category="general",
            severity="info",
        )
        assert lesson is not None
        assert lesson.status == "proposed"
        assert lesson.source_type == "dreaming_agent"
        assert lesson.source_id == "run-001"
        assert lesson.activated_at is None
        assert lesson.dry_run_until is None
        assert lesson.expires_at is None
        _teardown()

    def test_create_lesson_with_evidence(self, tmp_path, monkeypatch):
        _setup_test_db(tmp_path, monkeypatch)
        store = LessonStore()
        evidence = {"outcome_counts": {"win": 3, "loss": 1}, "avg_pnl_pct": 1.5}
        lesson = store.create(
            summary="Lesson with evidence",
            source_type="dreaming_agent",
            source_id="run-002",
            evidence=evidence,
        )
        parsed = json.loads(lesson.evidence_json)
        assert parsed["outcome_counts"]["win"] == 3
        assert parsed["avg_pnl_pct"] == 1.5
        _teardown()

    def test_create_lesson_with_recommendation(self, tmp_path, monkeypatch):
        _setup_test_db(tmp_path, monkeypatch)
        store = LessonStore()
        rec = {"action": "tighten_stop_loss", "params": {"pct": 2.0}}
        lesson = store.create(
            summary="Lesson with rec",
            source_type="post_trade_reviewer",
            source_id="run-003",
            recommendation=rec,
        )
        parsed = json.loads(lesson.recommendation_json)
        assert parsed["action"] == "tighten_stop_loss"
        _teardown()

    def test_get_lesson_by_id(self, tmp_path, monkeypatch):
        _setup_test_db(tmp_path, monkeypatch)
        store = LessonStore()
        created = store.create(
            summary="Retrievable lesson",
            source_type="dreaming_agent",
            source_id="run-004",
        )
        fetched = store.get(created.lesson_id)
        assert fetched is not None
        assert fetched.lesson_id == created.lesson_id
        assert fetched.summary == "Retrievable lesson"
        _teardown()

    def test_list_all_filters_by_status(self, tmp_path, monkeypatch):
        _setup_test_db(tmp_path, monkeypatch)
        store = LessonStore()
        store.create("Lesson A", "dreaming_agent", "r1")
        store.create("Lesson B", "dreaming_agent", "r2")
        all_lessons = store.list_all()
        assert len(all_lessons) == 2
        proposed = store.list_all(status="proposed")
        assert len(proposed) == 2
        accepted = store.list_all(status="accepted")
        assert len(accepted) == 0
        _teardown()


# ---------------------------------------------------------------------------
# LessonStore — state machine
# ---------------------------------------------------------------------------


class TestLessonStoreStateMachine:
    def test_accept_stamps_guard_timestamps(self, tmp_path, monkeypatch):
        _setup_test_db(tmp_path, monkeypatch)
        store = LessonStore()
        lesson = store.create("Acceptable lesson", "dreaming_agent", "r1")
        store.accept(lesson.lesson_id)

        fetched = store.get(lesson.lesson_id)
        assert fetched.status == "accepted"
        assert fetched.activated_at is not None
        assert fetched.dry_run_until is not None
        assert fetched.expires_at is not None

        # dry_run_until ~= activated_at + 24h
        activated = datetime.fromisoformat(fetched.activated_at)
        dry_run = datetime.fromisoformat(fetched.dry_run_until)
        delta_hours = (dry_run - activated).total_seconds() / 3600
        assert abs(delta_hours - 24.0) < 0.1

        # expires_at ~= activated_at + 30 days
        expires = datetime.fromisoformat(fetched.expires_at)
        delta_days = (expires - activated).days
        assert delta_days == 30
        _teardown()

    def test_reject_stamps_status(self, tmp_path, monkeypatch):
        _setup_test_db(tmp_path, monkeypatch)
        store = LessonStore()
        lesson = store.create("Rejectable lesson", "dreaming_agent", "r1")
        store.reject(lesson.lesson_id)
        fetched = store.get(lesson.lesson_id)
        assert fetched.status == "rejected"
        _teardown()

    def test_mark_applied_closes_policy_chain(self, tmp_path, monkeypatch):
        _setup_test_db(tmp_path, monkeypatch)
        store = LessonStore()
        lesson = store.create("Applicable lesson", "dreaming_agent", "r1")
        store.accept(lesson.lesson_id)
        dry_run_until = store.get(lesson.lesson_id).dry_run_until
        policy_version_id = "pv-test-001"
        store.mark_applied(lesson.lesson_id, policy_version_id)

        fetched = store.get(lesson.lesson_id)
        assert fetched.status == "applied"
        assert fetched.applied_policy_version == policy_version_id
        assert fetched.dry_run_until == dry_run_until
        _teardown()

    def test_archive_stamps_status(self, tmp_path, monkeypatch):
        _setup_test_db(tmp_path, monkeypatch)
        store = LessonStore()
        lesson = store.create("Archivable lesson", "dreaming_agent", "r1")
        store.archive(lesson.lesson_id)
        fetched = store.get(lesson.lesson_id)
        assert fetched.status == "archived"
        _teardown()

    def test_terminal_lesson_cannot_be_reaccepted_or_applied(
        self, tmp_path, monkeypatch,
    ):
        _setup_test_db(tmp_path, monkeypatch)
        from aletheia.storage.event_store import get_event_store

        store = LessonStore()
        rejected = store.create("Rejected lesson", "dreaming_agent", "r1")
        assert store.reject(rejected.lesson_id) is True
        assert store.reject(
            rejected.lesson_id, expected_status="rejected"
        ) is False

        assert store.accept(rejected.lesson_id) is False
        assert store.mark_applied(rejected.lesson_id, "pv-stale") is False
        assert store.get(rejected.lesson_id).status == "rejected"
        assert [
            event.event_type
            for event in get_event_store().list_by_aggregate(
                "lesson", rejected.lesson_id
            )
        ] == ["LESSON_CREATED", "LESSON_REJECTED"]
        _teardown()

    def test_lesson_transition_rolls_back_when_event_insert_fails(
        self, tmp_path, monkeypatch,
    ):
        _setup_test_db(tmp_path, monkeypatch)
        from aletheia.storage.event_store import get_event_store

        store = LessonStore()
        lesson = store.create("Atomic lesson", "dreaming_agent", "r1")
        event_store = get_event_store()
        original_write = event_store._write_db

        def fail_accept_event(event):
            if event.event_type == "LESSON_ACCEPTED":
                raise RuntimeError("injected event insert failure")
            return original_write(event)

        monkeypatch.setattr(event_store, "_write_db", fail_accept_event)
        with pytest.raises(RuntimeError, match="injected event insert failure"):
            store.accept(lesson.lesson_id)

        assert store.get(lesson.lesson_id).status == "proposed"
        assert [
            event.event_type
            for event in event_store.list_by_aggregate("lesson", lesson.lesson_id)
        ] == ["LESSON_CREATED"]
        _teardown()


# ---------------------------------------------------------------------------
# SkillStore — create + permission enforcement
# ---------------------------------------------------------------------------


class TestSkillStoreCreate:
    def test_create_skill_default_status_draft(self, tmp_path, monkeypatch):
        _setup_test_db(tmp_path, monkeypatch)
        store = SkillStore()
        skill = store.create(
            name="trend_scanner",
            scope="research",
            description="Scans for trend continuation patterns",
            allowed_layers=["research"],
        )
        assert skill is not None
        assert skill.status == "draft"
        assert skill.scope == "research"
        assert skill.version == 1
        _teardown()

    def test_create_skill_records_forbidden_layers(self, tmp_path, monkeypatch):
        _setup_test_db(tmp_path, monkeypatch)
        store = SkillStore()
        skill = store.create(
            name="safe_skill",
            scope="research",
            description="A safe skill",
        )
        forbidden = json.loads(skill.forbidden_layers_json)
        assert "risk_limits" in forbidden
        assert "execution_gateway" in forbidden
        assert "credentials" in forbidden
        assert "live_mode" in forbidden
        assert "order_submission" in forbidden
        _teardown()

    def test_create_skill_with_forbidden_layer_raises(self, tmp_path, monkeypatch):
        _setup_test_db(tmp_path, monkeypatch)
        store = SkillStore()
        with pytest.raises(SkillPermissionError) as exc_info:
            store.create(
                name="dangerous_skill",
                scope="execution_helper",
                description="Tries to touch risk limits",
                allowed_layers=["risk_limits"],
            )
        assert "risk_limits" in str(exc_info.value)
        _teardown()

    def test_create_skill_with_multiple_forbidden_raises(self, tmp_path, monkeypatch):
        _setup_test_db(tmp_path, monkeypatch)
        store = SkillStore()
        with pytest.raises(SkillPermissionError):
            store.create(
                name="multi_violation",
                scope="execution_helper",
                description="Multiple violations",
                allowed_layers=["risk_limits", "credentials", "research"],
            )
        _teardown()

    def test_list_all_filters(self, tmp_path, monkeypatch):
        _setup_test_db(tmp_path, monkeypatch)
        store = SkillStore()
        store.create("Skill A", "research", "desc A")
        store.create("Skill B", "validation", "desc B")
        all_skills = store.list_all()
        assert len(all_skills) == 2
        research_skills = store.list_all(scope="research")
        assert len(research_skills) == 1
        draft_skills = store.list_all(status="draft")
        assert len(draft_skills) == 2
        _teardown()

    def test_create_from_lesson_is_draft_and_idempotent(self, tmp_path, monkeypatch):
        _setup_test_db(tmp_path, monkeypatch)
        lesson_store = LessonStore()
        skill_store = SkillStore()
        lesson = lesson_store.create(
            summary="Use BTC/USDT momentum lessons as advisory context",
            source_type="trade_case_study",
            source_id="case-1",
            evidence={"symbol": "BTC/USDT", "scanner_family": "momentum"},
            recommendation={"action": "ranker_advisory"},
        )
        lesson_store.accept(lesson.lesson_id)
        lesson_store.mark_applied(lesson.lesson_id, "pv-1")
        applied = lesson_store.get(lesson.lesson_id)

        skill = skill_store.create_from_lesson(applied, policy_version_id="pv-1")
        again = skill_store.create_from_lesson(applied, policy_version_id="pv-1")

        assert skill is not None
        assert again.skill_id == skill.skill_id
        assert skill.name == f"lesson:{lesson.lesson_id}"
        assert skill.source_lesson_id == lesson.lesson_id
        assert json.loads(skill.created_from_lessons_json) == [lesson.lesson_id]
        assert skill.status == "draft"
        assert skill.scope == "learning_advisory"
        assert "risk_limits" not in json.loads(skill.allowed_layers_json)
        assert "execution_gateway" not in json.loads(skill.allowed_layers_json)
        assert "BTC/USDT" in applied.evidence_json
        assert len(skill_store.list_all()) == 1
        _teardown()

    def test_create_from_lesson_uses_typed_lineage_not_name(self, tmp_path, monkeypatch):
        _setup_test_db(tmp_path, monkeypatch)
        lesson_store = LessonStore()
        skill_store = SkillStore()
        lesson = lesson_store.create("Name collision", "trade_case_study", "case-2")
        lesson_store.accept(lesson.lesson_id)
        lesson_store.mark_applied(lesson.lesson_id, "pv-2")

        manual = skill_store.create(
            name=f"lesson:{lesson.lesson_id}",
            scope="research",
            description="Manual row with a colliding display name",
        )
        generated = skill_store.create_from_lesson(
            lesson_store.get(lesson.lesson_id),
            policy_version_id="pv-2",
        )
        again = skill_store.create_from_lesson(
            lesson_store.get(lesson.lesson_id),
            policy_version_id="pv-2",
        )

        assert manual.source_lesson_id is None
        assert generated is not None
        assert generated.skill_id == again.skill_id
        assert generated.skill_id != manual.skill_id
        assert generated.source_lesson_id == lesson.lesson_id
        assert len(skill_store.list_all()) == 2
        _teardown()


# ---------------------------------------------------------------------------
# SkillStore — state machine
# ---------------------------------------------------------------------------


class TestSkillStoreStateMachine:
    def test_activate(self, tmp_path, monkeypatch):
        _setup_test_db(tmp_path, monkeypatch)
        store = SkillStore()
        skill = store.create("Activatable", "research", "desc")
        store.activate(skill.skill_id)
        fetched = store.get(skill.skill_id)
        assert fetched.status == "active"
        _teardown()

    def test_deprecate(self, tmp_path, monkeypatch):
        _setup_test_db(tmp_path, monkeypatch)
        store = SkillStore()
        skill = store.create("Deprecatable", "research", "desc")
        store.activate(skill.skill_id)
        store.deprecate(skill.skill_id)
        fetched = store.get(skill.skill_id)
        assert fetched.status == "deprecated"
        _teardown()

    def test_get_nonexistent_returns_none(self, tmp_path, monkeypatch):
        _setup_test_db(tmp_path, monkeypatch)
        store = SkillStore()
        assert store.get("nonexistent-id") is None
        _teardown()
