"""Spec: Lesson Constraint Guards (risk_engine_contract §lesson_guards)

Executable claims: accepted lessons with block_* recommendations must pass through
cooldown (dry-run window) and TTL (expiry) guards before blocking live trades.
Without these guards, any manually-accepted lesson immediately blocks all trades
with no rollback path — a sleeping bomb (identified 2026-06-29).
"""

from __future__ import annotations

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


@spec_claim(
    SpecClaim("LG-S01", "risk_engine_contract", "§lesson_guards fields", "L2a",
              "PSLesson has activated_at, dry_run_until, expires_at fields"),
)
def test_pslesson_has_guard_fields():
    from aletheia.storage.models import PSLesson

    lesson = PSLesson(source_type="test", source_id="t1", category="risk",
                      severity="medium", summary="test")
    assert hasattr(lesson, "activated_at")
    assert hasattr(lesson, "dry_run_until")
    assert hasattr(lesson, "expires_at")


@spec_claim(
    SpecClaim("LG-S02", "risk_engine_contract", "§lesson_guards accept sets timestamps", "L2a",
              "accept() stamps activated_at; dry_run_until=+24h; expires_at=+30d"),
)
def test_accept_stamps_guard_timestamps(tmp_path, monkeypatch):
    from aletheia.storage.sqlite import get_db
    from aletheia.storage.migrations import run_migrations
    from aletheia.learning.lessons import LessonStore

    run_migrations()
    store = LessonStore()
    lesson = store.create("guard test", source_type="test", source_id="t1",
                          category="risk", severity="medium")

    before = store.get(lesson.lesson_id)
    assert before.activated_at is None, "should be None before accept()"

    store.accept(lesson.lesson_id)
    after = store.get(lesson.lesson_id)
    assert after.activated_at is not None, "accept() must stamp activated_at"
    assert after.dry_run_until is not None, "accept() must set dry_run_until"
    assert after.expires_at is not None, "accept() must set expires_at"

    # expires_at > dry_run_until > activated_at
    assert after.expires_at > after.dry_run_until
    assert after.dry_run_until > after.activated_at


@spec_claim(
    SpecClaim("LG-S03", "risk_engine_contract", "§lesson_guards dry-run is advisory-only", "L2b",
              "block_* constraints within dry_run_until window log but do NOT block trades"),
)
def test_lesson_block_in_dry_run_window_is_advisory(tmp_path, monkeypatch):
    """A just-accepted lesson (within 24h dry-run window) must NOT add violations."""
    from unittest.mock import MagicMock
    from aletheia.storage.migrations import run_migrations
    from aletheia.learning.lessons import LessonStore
    from aletheia.risk import engine as eng

    run_migrations()
    store = LessonStore()
    lesson = store.create("block BTC dry run", source_type="test", source_id="t1",
                          category="risk", severity="high",
                          recommendation={"action": "block_symbol", "symbol": "BTC/USDT"})
    store.accept(lesson.lesson_id)  # activated_at = now, dry_run_until = now+24h
    # #19 fix: _check_lesson_constraints filters on status='applied', not 'accepted'.
    # Without mark_applied, the lesson is invisible and the test passes vacuously.
    store.mark_applied(lesson.lesson_id, "pv-test-dry-run")

    proposal = MagicMock()
    proposal.symbol = "BTC/USDT"
    proposal.direction = "long"

    violations = eng._check_lesson_constraints(proposal)
    # In dry-run window → advisory only, must NOT produce a violation
    assert not any("lesson_block_symbol" in v for v in violations), (
        f"dry-run lesson must not block trades, got violations: {violations}"
    )


@spec_claim(
    SpecClaim("LG-S04", "risk_engine_contract", "§lesson_guards expired lesson is skipped", "L2b",
              "block_* constraints past expires_at are silently skipped"),
)
def test_expired_lesson_does_not_block(tmp_path, monkeypatch):
    """An expired lesson must not produce violations even if recommendation_json says block."""
    from unittest.mock import MagicMock
    from datetime import datetime, timezone, timedelta
    from aletheia.storage.sqlite import get_db
    from aletheia.storage.migrations import run_migrations
    from aletheia.learning.lessons import LessonStore
    from aletheia.risk import engine as eng

    run_migrations()
    store = LessonStore()
    lesson = store.create("expired block", source_type="test", source_id="t1",
                          category="risk", severity="medium",
                          recommendation={"action": "block_direction", "direction": "short"})
    store.accept(lesson.lesson_id)
    # #19 fix: _check_lesson_constraints reads status='applied', not 'accepted'.
    store.mark_applied(lesson.lesson_id, "pv-test-expired")

    # Manually expire the lesson by setting expires_at to the past
    db = get_db()
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    db.execute(
        "UPDATE ps_lessons SET expires_at = ? WHERE lesson_id = ?",
        (past, lesson.lesson_id)
    )
    db.commit()

    proposal = MagicMock()
    proposal.symbol = "ETH/USDT"
    proposal.direction = "short"

    violations = eng._check_lesson_constraints(proposal)
    assert not any("lesson_block_direction" in v for v in violations), (
        f"expired lesson must not block trades, got violations: {violations}"
    )


@spec_claim(
    SpecClaim(
        "LG-S05",
        "risk_engine_contract",
        "§lesson_guards complete applied set",
        "L2c",
        "Every active applied Risk lesson is authoritative; an older 51st lesson "
        "cannot disappear behind a display-sized query limit.",
        status="implemented",
    )
)
def test_older_applied_risk_lesson_is_not_truncated(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from unittest.mock import MagicMock

    from aletheia.risk import engine as eng
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import get_db

    run_migrations()
    now = datetime.now(timezone.utc)
    past = (now - timedelta(days=1)).isoformat()
    future = (now + timedelta(days=30)).isoformat()
    rows = [
        (
            "lesson-old-block",
            "manual",
            "old",
            "risk",
            "high",
            "old authoritative block",
            "{}",
            '{"action":"block_symbol","symbol":"BTC/USDT"}',
            "applied",
            "2026-01-01T00:00:00+00:00",
            "pv-old",
            past,
            past,
            future,
        )
    ]
    rows.extend(
        (
            f"lesson-new-{index:02d}",
            "manual",
            f"new-{index}",
            "risk",
            "info",
            "new advisory",
            "{}",
            '{"action":"reduce_size","factor":0.9}',
            "applied",
            f"2026-02-{index // 28 + 1:02d}T00:{index % 60:02d}:00+00:00",
            "pv-new",
            past,
            past,
            future,
        )
        for index in range(50)
    )
    get_db().executemany(
        "INSERT INTO ps_lessons "
        "(lesson_id,source_type,source_id,category,severity,summary,evidence_json,"
        "recommendation_json,status,created_at,applied_policy_version,activated_at,"
        "dry_run_until,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )

    proposal = MagicMock(symbol="BTC/USDT", direction="long")
    violations = eng._check_lesson_constraints(proposal)

    assert "lesson_block_symbol:lesson-old-block:BTC/USDT" in violations


@spec_claim(SpecClaim(
    "LG-S06", "risk_engine_contract", "§lesson_guards paper authority", "L2c",
    "An automatically produced applied Risk lesson remains advisory and creates no formal "
    "Learning exposure while risk_lesson_constraint lacks a measurement adapter; it only blocks "
    "when exact treatment authority is available. Owner/manual lessons remain static safety rules.",
    status="implemented",
))
def test_automatic_risk_lesson_requires_paper_treatment_authority(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from unittest.mock import MagicMock

    from aletheia.learning.lessons import LessonStore
    from aletheia.risk import engine as eng
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import get_db

    run_migrations()
    store = LessonStore()
    auto = store.create(
        "automatic BTC block", source_type="dreaming_agent", source_id="dream-1",
        category="risk", severity="high",
        recommendation={"action": "block_symbol", "symbol": "BTC/USDT"},
    )
    store.accept(auto.lesson_id)
    store.mark_applied(auto.lesson_id, "policy-auto")
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    get_db().execute(
        "UPDATE ps_lessons SET dry_run_until=? WHERE lesson_id=?", (past, auto.lesson_id),
    )
    get_db().commit()
    proposal = MagicMock(
        id="proposal-risk-authority", opportunity_id="opp-risk-authority",
        symbol="BTC/USDT", direction="long", scanner_family="momentum",
    )

    # The unsupported risk lesson path is absent from the exact default allowlist.
    assert not any("lesson_block_symbol" in row for row in eng._check_lesson_constraints(proposal))
    exposure = get_db().execute(
        "SELECT * FROM learning_exposures WHERE path_id='risk_lesson_constraint'"
    ).fetchone()
    assert exposure is None

    from aletheia.learning.experiments import InfluenceDecision
    captured = []

    def control(**kwargs):
        captured.append(kwargs)
        return InfluenceDecision(
            value=0.0, applied=False, assignment="control", reason="baseline:authorized",
            hypothesis_id="hyp-risk", experiment_id="exp-risk", exposure_id="x-control",
        )

    monkeypatch.setattr("aletheia.learning.experiments.apply_boolean_treatment", control)
    assert not any("lesson_block_symbol" in row for row in eng._check_lesson_constraints(proposal))
    assert captured[-1]["path_id"] == "risk_lesson_constraint"
    assert captured[-1]["outcome_candidate_key"] == f"proposal:{proposal.id}:4h"

    def treatment(**kwargs):
        return InfluenceDecision(
            value=1.0, applied=True, assignment="treatment", reason="authorized",
            hypothesis_id="hyp-risk", experiment_id="exp-risk", exposure_id="x-treatment",
        )

    monkeypatch.setattr("aletheia.learning.experiments.apply_boolean_treatment", treatment)
    assert f"lesson_block_symbol:{auto.lesson_id}:BTC/USDT" in eng._check_lesson_constraints(proposal)

    monkeypatch.setattr(
        "aletheia.learning.experiments.apply_boolean_treatment",
        lambda **_: (_ for _ in ()).throw(RuntimeError("authority unavailable")),
    )
    assert "lesson_check_error:internal_failure" in eng._check_lesson_constraints(proposal)
    monkeypatch.setattr("aletheia.learning.experiments.apply_boolean_treatment", control)

    manual = store.create(
        "owner BTC block", source_type="owner", source_id="owner-1",
        category="risk", severity="high",
        recommendation={"action": "block_symbol", "symbol": "BTC/USDT"},
    )
    store.accept(manual.lesson_id)
    store.mark_applied(manual.lesson_id, "policy-owner")
    get_db().execute(
        "UPDATE ps_lessons SET dry_run_until=? WHERE lesson_id=?", (past, manual.lesson_id),
    )
    get_db().commit()
    # Manual rules do not depend on the Learning authority adapter.
    assert f"lesson_block_symbol:{manual.lesson_id}:BTC/USDT" in eng._check_lesson_constraints(proposal)
