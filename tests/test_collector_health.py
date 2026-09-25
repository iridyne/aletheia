from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def collector_db(tmp_path, monkeypatch):
    from aletheia.config import loader
    from aletheia.storage import migrations
    from aletheia.storage import sqlite as sq

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    sq.close_db()
    migrations.run_migrations()
    yield
    sq.close_db()


def test_collector_health_keeps_one_durable_row_per_domain(collector_db):
    from aletheia.data.collector_health import record_collector_status
    from aletheia.storage.repositories import list_collector_health_states

    record_collector_status("macro", "success", tick=1, duration_ms=25)
    record_collector_status("macro", "empty", tick=6, duration_ms=12)

    rows = list_collector_health_states()
    assert len(rows) == 1
    assert rows[0]["domain"] == "macro"
    assert rows[0]["last_status"] == "empty"
    assert rows[0]["attempts"] == 2
    assert rows[0]["successes"] == 1
    assert rows[0]["empty_results"] == 1


def test_scheduler_health_write_releases_writer_permit(collector_db):
    from aletheia.agent_runtime import scheduler
    from aletheia.storage.repositories import list_collector_health_states
    from aletheia.storage.sqlite import get_db

    token = scheduler._DC_HEALTH_TICK.set(1)
    try:
        scheduler._record_collector_health("macro", "success")
    finally:
        scheduler._DC_HEALTH_TICK.reset(token)

    assert list_collector_health_states()[0]["domain"] == "macro"

    assert get_db().in_transaction is False


def test_three_consecutive_collector_problems_emit_one_operator_warning(
    collector_db, monkeypatch
):
    from aletheia.data import collector_health
    from aletheia.infra.events import clear_infra_events, list_infra_events

    monkeypatch.setattr(
        "aletheia.agent_runtime.connectivity.get_status",
        lambda: {"external_connectivity": {"state": "reachable"}},
    )
    clear_infra_events()
    for tick in (1, 6, 11):
        collector_health.record_collector_status(
            "defillama", "failed", tick=tick
        )

    rows = collector_health.get_collector_health_report()["domains"]
    row = next(item for item in rows if item["domain"] == "defillama")
    assert row["consecutive_problems"] == 3
    warnings = [
        event
        for event in list_infra_events()
        if event["code"] == "collector_consecutive_problem"
    ]
    assert len(warnings) == 1
    assert warnings[0]["entity_id"] == "defillama"
    assert "3 consecutive" in warnings[0]["message"]


def test_connectivity_dependent_collector_problem_is_informational(
    collector_db, monkeypatch
):
    from aletheia.data import collector_health
    from aletheia.infra.events import clear_infra_events, list_infra_events

    monkeypatch.setattr(
        "aletheia.agent_runtime.connectivity.get_status",
        lambda: {"external_connectivity": {"state": "unavailable"}},
    )
    clear_infra_events()
    for tick in (1, 6, 11):
        collector_health.record_collector_status("defillama", "failed", tick=tick)

    events = [
        event
        for event in list_infra_events()
        if event["code"] == "collector_problem_dependent_connectivity"
    ]
    assert len(events) == 1
    assert events[0]["severity"] == "info"
    assert events[0]["metadata"]["dependent_on"] == "external_connectivity"


def test_skipped_budget_does_not_count_toward_consecutive_problems(collector_db):
    from aletheia.data.collector_health import (
        get_collector_health_report,
        record_collector_status,
    )

    for tick in (1, 6, 11):
        record_collector_status("defillama", "skipped_budget", tick=tick)

    rows = get_collector_health_report()["domains"]
    row = next(item for item in rows if item["domain"] == "defillama")
    # This test's purpose is the counter: skips must never be counted as source
    # problems. #3089 changes only the derived projection - a domain that has
    # never attempted while being skipped on every turn is scheduler starvation,
    # so `degraded` is now True for that reason. The issue quotes this assertion
    # as the behaviour being corrected.
    assert row["consecutive_problems"] == 0
    assert row["starved"] is True
    assert row["degraded"] is True
    assert row["budget_skips"] == 3


def test_success_resets_consecutive_problem_counter(collector_db):
    from aletheia.data.collector_health import (
        get_collector_health_report,
        record_collector_status,
    )

    record_collector_status("derivatives", "timed_out", tick=1)
    record_collector_status("derivatives", "failed", tick=2)
    record_collector_status("derivatives", "partial", tick=3)

    row = next(
        item
        for item in get_collector_health_report()["domains"]
        if item["domain"] == "derivatives"
    )
    assert row["consecutive_problems"] == 0
    assert row["last_status"] == "partial"


def test_collector_report_marks_overdue_domain_degraded(collector_db):
    from aletheia.data.collector_health import (
        get_collector_health_report,
        record_collector_status,
    )

    now = datetime.now(timezone.utc)
    record_collector_status(
        "microstructure",
        "success",
        tick=1,
        occurred_at=(now - timedelta(minutes=20)).isoformat(),
    )

    report = get_collector_health_report(now=now)
    row = next(item for item in report["domains"] if item["domain"] == "microstructure")
    assert row["overdue"] is True
    assert report["status"] == "degraded"


def test_collector_rollup_requires_success_without_partial_empty_or_never_run(
    collector_db,
):
    from aletheia.data.collector_health import (
        COLLECTOR_CADENCE_SEC,
        get_collector_health_report,
        record_collector_status,
    )

    record_collector_status("microstructure", "success", tick=1)
    report = get_collector_health_report(connectivity_status={"state": "reachable"})
    assert report["status"] == "initializing"

    record_collector_status("derivatives", "partial", tick=1)
    report = get_collector_health_report(connectivity_status={"state": "reachable"})
    partial = next(row for row in report["domains"] if row["domain"] == "derivatives")
    assert report["status"] == "attention"
    assert partial["display_severity"] == "warning"
    assert partial["consecutive_problems"] == 0

    for domain in COLLECTOR_CADENCE_SEC:
        record_collector_status(domain, "success", tick=2)
    assert get_collector_health_report(
        connectivity_status={"state": "reachable"}
    )["status"] == "healthy"


@pytest.mark.parametrize(
    "occurred_at,reason",
    [
        ("", "collector_health_timestamp_missing"),
        ("not-a-timestamp", "collector_health_timestamp_invalid"),
        (
            # Use a fixed far-future offset so this parametrization stays "future"
            # even in a long test suite run; datetime.now() at module import time
            # drifts over 4 minutes and stops being "future" mid-suite.
            (datetime(2099, 1, 1, 0, 0, tzinfo=timezone.utc)).isoformat(),
            "collector_health_timestamp_future",
        ),
    ],
)
def test_invalid_collector_timestamp_is_rejected_before_state_access(
    monkeypatch, occurred_at, reason
):
    from aletheia.data import collector_health

    state = {"attempts": 7, "successes": 5}
    calls = []
    monkeypatch.setattr(
        collector_health.repo,
        "get_collector_health_state",
        lambda _domain: calls.append("read") or state,
    )
    monkeypatch.setattr(
        collector_health.repo,
        "upsert_collector_health_state",
        lambda row: calls.append("write") or state.update(row),
    )

    with pytest.raises(ValueError, match=reason):
        collector_health.record_collector_status(
            "macro", "success", occurred_at=occurred_at
        )

    assert state == {"attempts": 7, "successes": 5}
    assert calls == []


@pytest.mark.parametrize(
    "timestamp,reason",
    [
        (None, "collector_health_timestamp_missing"),
        ("not-a-timestamp", "collector_health_timestamp_invalid"),
        (
            datetime(2026, 1, 1, 0, 6, tzinfo=timezone.utc).isoformat(),
            "collector_health_timestamp_future",
        ),
    ],
)
def test_historical_invalid_timestamp_projects_degraded(
    monkeypatch, timestamp, reason
):
    from aletheia.data import collector_health

    monkeypatch.setattr(collector_health.repo, "list_collector_health_states", lambda: [{
        "domain": "macro",
        "cadence_sec": 600,
        "last_status": "success",
        "last_scheduled_at": timestamp,
        "attempts": 1,
        "successes": 1,
    }])

    report = collector_health.get_collector_health_report(
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
        connectivity_status={"state": "reachable"},
    )
    row = next(item for item in report["domains"] if item["domain"] == "macro")

    assert row["last_status"] == "success"
    assert row["display_status"] == "degraded"
    assert row["degraded"] is True
    assert row["age_sec"] is None
    assert row["last_error_summary"] == reason
    assert report["status"] == "degraded"


def test_collector_timestamp_within_future_skew_is_fresh(collector_db):
    from aletheia.data.collector_health import (
        get_collector_health_report,
        record_collector_status,
    )

    now = datetime.now(timezone.utc)
    record_collector_status(
        "macro",
        "success",
        occurred_at=(now + timedelta(minutes=4, seconds=59)).isoformat(),
    )

    report = get_collector_health_report(
        now=now,
        connectivity_status={"state": "reachable"},
    )
    row = next(item for item in report["domains"] if item["domain"] == "macro")

    assert row["age_sec"] == 0
    assert row["degraded"] is False
    assert row["last_status"] == "success"
    assert report["status"] == "initializing"


def test_result_classifier_distinguishes_empty_partial_and_not_configured():
    from aletheia.data.collector_health import classify_collector_result

    assert classify_collector_result({"snapshots": [], "errors": []}) == "empty"
    assert classify_collector_result({"snapshots": [object()], "errors": []}) == "success"
    assert classify_collector_result({"snapshots": [object()], "errors": ["one"]}) == "partial"
    assert classify_collector_result({"snapshots": [], "errors": ["one"]}) == "failed"
    assert classify_collector_result({"errors": ["no_key"]}) == "not_configured"
    assert classify_collector_result({
        "snapshots": [],
        "errors": ["chain_tvl: empty_or_invalid"],
        "metric_outcomes": {"chain_tvl": "empty_or_invalid"},
    }) == "failed"
    assert classify_collector_result({
        "funding": 2,
        "open_interest": 0,
        "errors": ["oi_history_invalid BTC/USDT"],
    }) == "partial"


def test_derivatives_history_is_a_recordable_health_domain(collector_db):
    from aletheia.data.collector_health import (
        get_collector_health_report,
        record_collector_status,
    )

    record_collector_status("derivatives_history", "failed", tick=1)

    row = next(
        item for item in get_collector_health_report()["domains"]
        if item["domain"] == "derivatives_history"
    )
    assert row["last_status"] == "failed"


def test_report_attributes_cross_source_failures_to_external_connectivity(collector_db):
    from aletheia.data.collector_health import (
        get_collector_health_report,
        record_collector_status,
    )

    now = datetime.now(timezone.utc)
    occurred_at = (now - timedelta(seconds=20)).isoformat()
    record_collector_status("microstructure", "failed", tick=1, occurred_at=occurred_at)
    record_collector_status("coingecko_top", "timed_out", tick=1, occurred_at=occurred_at)

    report = get_collector_health_report(
        now=now,
        connectivity_status={
            "state": "unavailable",
            "severity": "warning",
            "priority": 1,
            "observed_at": (now - timedelta(seconds=5)).timestamp(),
            "outage_started_at": (now - timedelta(seconds=30)).timestamp(),
            "last_recovery_at": None,
            "stale_after_sec": 180,
        },
    )

    incident = report["infrastructure_incident"]
    assert incident["code"] == "external_connectivity_unavailable"
    assert incident["status"] == "active"
    assert incident["priority"] == 1
    assert incident["affected_domains"] == ["microstructure", "coingecko_top"]
    affected = {
        row["domain"]: row
        for row in report["domains"]
        if row["domain"] in incident["affected_domains"]
    }
    assert {row["display_status"] for row in affected.values()} == {
        "blocked_by_connectivity"
    }
    assert {row["attributed_cause"] for row in affected.values()} == {
        "external_connectivity_unavailable"
    }
    assert {row["display_severity"] for row in affected.values()} == {"info"}


def test_report_keeps_collector_warning_when_external_network_is_reachable(collector_db):
    from aletheia.data.collector_health import (
        get_collector_health_report,
        record_collector_status,
    )

    now = datetime.now(timezone.utc)
    occurred_at = (now - timedelta(seconds=20)).isoformat()
    record_collector_status("microstructure", "failed", tick=1, occurred_at=occurred_at)
    record_collector_status("derivatives", "failed", tick=1, occurred_at=occurred_at)

    report = get_collector_health_report(
        now=now,
        connectivity_status={
            "state": "reachable",
            "severity": "ok",
            "priority": 4,
            "observed_at": (now - timedelta(seconds=5)).timestamp(),
            "outage_started_at": None,
            "last_recovery_at": None,
            "stale_after_sec": 180,
        },
    )

    assert report["infrastructure_incident"] is None
    rows = {
        row["domain"]: row
        for row in report["domains"]
        if row["domain"] in {"microstructure", "derivatives"}
    }
    assert {row["display_status"] for row in rows.values()} == {"failed"}
    assert {row["attributed_cause"] for row in rows.values()} == {None}
    assert {row["display_severity"] for row in rows.values()} == {"warning"}


def test_report_does_not_attribute_failures_that_predate_current_outage(collector_db):
    from aletheia.data.collector_health import (
        get_collector_health_report,
        record_collector_status,
    )

    now = datetime.now(timezone.utc)
    old_failure = (now - timedelta(hours=1)).isoformat()
    record_collector_status("microstructure", "failed", tick=1, occurred_at=old_failure)
    record_collector_status("coingecko_top", "timed_out", tick=1, occurred_at=old_failure)

    report = get_collector_health_report(
        now=now,
        connectivity_status={
            "state": "unavailable",
            "severity": "warning",
            "priority": 1,
            "observed_at": (now - timedelta(seconds=5)).timestamp(),
            "outage_started_at": (now - timedelta(seconds=30)).timestamp(),
            "last_recovery_at": None,
            "stale_after_sec": 180,
        },
    )

    assert report["infrastructure_incident"]["code"] == "external_connectivity_unavailable"
    assert report["infrastructure_incident"]["affected_domains"] == []


def test_options_structure_success_appears_in_health_report(collector_db):
    from aletheia.data.collector_health import get_collector_health_report, record_collector_status

    record_collector_status("options_structure", "success", tick=1, duration_ms=1500)
    row = next(r for r in get_collector_health_report()["domains"] if r["domain"] == "options_structure")
    assert row["last_status"] == "success"
    assert row["successes"] == 1
    assert row["display_severity"] == "ok"


def test_options_structure_empty_appears_in_health_report(collector_db):
    from aletheia.data.collector_health import get_collector_health_report, record_collector_status

    record_collector_status("options_structure", "empty", tick=1)
    row = next(r for r in get_collector_health_report()["domains"] if r["domain"] == "options_structure")
    assert row["last_status"] == "empty"
    assert row["empty_results"] == 1


def test_options_structure_timeout_appears_in_health_report(collector_db):
    from aletheia.data.collector_health import get_collector_health_report, record_collector_status

    record_collector_status("options_structure", "timed_out", tick=1, error_code="asyncio_timeout")
    row = next(r for r in get_collector_health_report()["domains"] if r["domain"] == "options_structure")
    assert row["last_status"] == "timed_out"
    assert row["timeouts"] == 1
    assert row["display_severity"] == "warning"


def test_options_structure_disabled_appears_in_health_report(collector_db):
    from aletheia.data.collector_health import get_collector_health_report, record_collector_status

    record_collector_status("options_structure", "disabled", tick=1)
    row = next(r for r in get_collector_health_report()["domains"] if r["domain"] == "options_structure")
    assert row["last_status"] == "disabled"
    assert row["display_severity"] == "muted"


def test_options_structure_persist_failure_appears_in_health_report(collector_db):
    from aletheia.data.collector_health import get_collector_health_report, record_collector_status

    record_collector_status(
        "options_structure", "failed", tick=1,
        error_code="options_structure_persist_failed",
    )
    row = next(r for r in get_collector_health_report()["domains"] if r["domain"] == "options_structure")
    assert row["last_status"] == "failed"
    assert row["failures"] == 1
    assert row["last_error_code"] == "options_structure_persist_failed"
    assert row["display_severity"] == "warning"


def test_report_keeps_pre_recovery_failures_under_one_recovery_notice(collector_db):
    from aletheia.data.collector_health import (
        get_collector_health_report,
        record_collector_status,
    )

    now = datetime.now(timezone.utc)
    failed_at = now - timedelta(minutes=2)
    recovered_at = now - timedelta(seconds=30)
    record_collector_status(
        "coingecko_top", "timed_out", tick=1, occurred_at=failed_at.isoformat()
    )
    record_collector_status(
        "macro", "timed_out", tick=1, occurred_at=failed_at.isoformat()
    )

    report = get_collector_health_report(
        now=now,
        connectivity_status={
            "state": "recovering",
            "severity": "info",
            "priority": 2,
            "observed_at": recovered_at.timestamp(),
            "outage_started_at": (now - timedelta(minutes=10)).timestamp(),
            "last_recovery_at": recovered_at.timestamp(),
            "stale_after_sec": 180,
        },
    )

    incident = report["infrastructure_incident"]
    assert incident["code"] == "external_connectivity_recovering"
    assert incident["status"] == "recovering"
    assert incident["affected_domains"] == ["coingecko_top", "macro"]
    rows = {
        row["domain"]: row
        for row in report["domains"]
        if row["domain"] in incident["affected_domains"]
    }
    assert {row["display_status"] for row in rows.values()} == {"waiting_for_retry"}
    assert {row["display_severity"] for row in rows.values()} == {"info"}


# --- #3089: scheduler starvation must not be reported as healthy ---------------
# `last_scheduled_at` advances on every turn, so a domain whose every turn is
# `skipped_budget` keeps looking freshly scheduled. Liveness must come from real
# attempts. The raw status and counters stay untouched; only the derived
# projection changes.

_STARVED_DOMAIN = "defillama"  # cadence 600s -> 3-cadence window is 1800s


def _report_row(now, domain=_STARVED_DOMAIN):
    from aletheia.data.collector_health import get_collector_health_report

    report = get_collector_health_report(now=now)
    return report, next(item for item in report["domains"] if item["domain"] == domain)


def test_single_budget_skip_stays_informational(collector_db):
    from aletheia.data.collector_health import record_collector_status

    now = datetime.now(timezone.utc)
    record_collector_status(
        _STARVED_DOMAIN, "success", tick=1,
        occurred_at=(now - timedelta(minutes=1)).isoformat(),
    )
    record_collector_status(_STARVED_DOMAIN, "skipped_budget", tick=2, occurred_at=now.isoformat())

    report, row = _report_row(now)
    assert row["starved"] is False
    assert row["degraded"] is False
    assert row["consecutive_problems"] == 0
    assert row["budget_skips"] == 1
    assert report["status"] != "degraded"


def test_repeated_budget_skips_beyond_window_report_starvation(collector_db):
    from aletheia.data.collector_health import record_collector_status

    now = datetime.now(timezone.utc)
    record_collector_status(
        _STARVED_DOMAIN, "success", tick=1,
        occurred_at=(now - timedelta(minutes=40)).isoformat(),
    )
    for tick in (2, 3, 4):
        record_collector_status(
            _STARVED_DOMAIN, "skipped_budget", tick=tick, occurred_at=now.isoformat()
        )

    report, row = _report_row(now)
    assert row["starved"] is True
    assert row["degraded"] is True
    assert report["status"] == "degraded"
    # Starvation is the scheduler's fault, not the source's.
    assert row["consecutive_problems"] == 0
    assert row["last_status"] == "skipped_budget"
    assert row["budget_skips"] == 3
    assert "collector_budget_starvation" in str(row["last_error_summary"])


def test_advancing_scheduled_at_does_not_hide_stale_attempt(collector_db):
    from aletheia.data.collector_health import record_collector_status

    now = datetime.now(timezone.utc)
    record_collector_status(
        _STARVED_DOMAIN, "success", tick=1,
        occurred_at=(now - timedelta(minutes=40)).isoformat(),
    )
    record_collector_status(_STARVED_DOMAIN, "skipped_budget", tick=2, occurred_at=now.isoformat())

    _report, row = _report_row(now)
    # The scheduled tick is fresh, so the legacy overdue signal stays quiet...
    assert row["overdue"] is False
    assert row["age_sec"] == 0
    # ...but the real attempt age is what proves starvation.
    assert row["starved"] is True
    assert row["attempt_age_sec"] > row["cadence_sec"] * 3


def test_real_attempt_clears_starvation(collector_db):
    from aletheia.data.collector_health import record_collector_status

    now = datetime.now(timezone.utc)
    record_collector_status(
        _STARVED_DOMAIN, "success", tick=1,
        occurred_at=(now - timedelta(minutes=40)).isoformat(),
    )
    record_collector_status(_STARVED_DOMAIN, "skipped_budget", tick=2, occurred_at=now.isoformat())
    assert _report_row(now)[1]["starved"] is True

    later = now + timedelta(minutes=1)
    record_collector_status(_STARVED_DOMAIN, "success", tick=3, occurred_at=later.isoformat())

    report, row = _report_row(later)
    assert row["starved"] is False
    assert row["degraded"] is False
    assert row["attempt_age_sec"] == 0
    assert report["status"] != "degraded"


def test_partial_attempt_clears_starvation_but_keeps_attention(collector_db):
    from aletheia.data.collector_health import record_collector_status

    now = datetime.now(timezone.utc)
    record_collector_status(
        _STARVED_DOMAIN, "success", tick=1,
        occurred_at=(now - timedelta(minutes=40)).isoformat(),
    )
    record_collector_status(_STARVED_DOMAIN, "skipped_budget", tick=2, occurred_at=now.isoformat())

    later = now + timedelta(minutes=1)
    record_collector_status(_STARVED_DOMAIN, "partial", tick=3, occurred_at=later.isoformat())

    report, row = _report_row(later)
    assert row["starved"] is False
    assert row["degraded"] is False
    # A real attempt happened, so this is the pre-existing partial/attention
    # path, not starvation.
    assert row["display_severity"] == "warning"
    assert report["status"] == "attention"


@pytest.mark.parametrize("status", ["disabled", "not_configured"])
def test_inactive_domains_never_report_starvation(collector_db, status):
    from aletheia.data.collector_health import record_collector_status

    now = datetime.now(timezone.utc)
    record_collector_status(
        _STARVED_DOMAIN, "success", tick=1,
        occurred_at=(now - timedelta(minutes=40)).isoformat(),
    )
    record_collector_status(_STARVED_DOMAIN, status, tick=2, occurred_at=now.isoformat())

    _report, row = _report_row(now)
    assert row["starved"] is False
    assert row["degraded"] is False


def test_source_failure_semantics_are_unchanged_by_starvation(collector_db):
    from aletheia.data.collector_health import record_collector_status

    now = datetime.now(timezone.utc)
    # Attempts happen on cadence and fail: a source problem, not starvation.
    for tick in (1, 2, 3):
        record_collector_status(
            _STARVED_DOMAIN, "failed", tick=tick,
            occurred_at=(now - timedelta(seconds=20)).isoformat(),
        )

    report, row = _report_row(now)
    # A real attempt happened every time, so this stays a source problem.
    assert row["starved"] is False
    assert row["consecutive_problems"] == 3
    assert row["degraded"] is True
    assert report["status"] == "degraded"


def test_starvation_emits_one_deduplicated_operator_warning(collector_db):
    from aletheia.data.collector_health import record_collector_status
    from aletheia.infra.events import clear_infra_events, list_infra_events

    clear_infra_events()
    now = datetime.now(timezone.utc)
    record_collector_status(
        _STARVED_DOMAIN, "success", tick=1,
        occurred_at=(now - timedelta(minutes=40)).isoformat(),
    )
    for tick in (2, 3, 4, 5):
        record_collector_status(
            _STARVED_DOMAIN, "skipped_budget", tick=tick, occurred_at=now.isoformat()
        )

    events = [
        e for e in list_infra_events(limit=50) if e["code"] == "collector_budget_starvation"
    ]
    assert len(events) == 1, events
    assert events[0]["entity_id"] == _STARVED_DOMAIN
    assert "no real attempt" in events[0]["message"]


def test_domain_skipped_before_its_turn_every_tick_reports_starvation(collector_db):
    """`_bounded` records `skipped_budget` before the coroutine runs, so a domain
    can be skipped on every tick without ever attempting. It has no attempt
    timestamp to age, and its scheduled tick keeps advancing, so without a
    count-based bound it would report healthy forever."""
    from aletheia.data.collector_health import record_collector_status
    from aletheia.infra.events import clear_infra_events, list_infra_events

    clear_infra_events()
    now = datetime.now(timezone.utc)
    for tick in (1, 2, 3):
        record_collector_status(_STARVED_DOMAIN, "skipped_budget", tick=tick, occurred_at=now.isoformat())

    report, row = _report_row(now)
    assert row["attempts"] == 0
    assert row["attempt_age_sec"] is None
    assert row["starved"] is True
    assert row["degraded"] is True
    assert report["status"] == "degraded"
    assert "no real attempt recorded" in str(row["last_error_summary"])

    events = [
        e for e in list_infra_events(limit=50) if e["code"] == "collector_budget_starvation"
    ]
    assert len(events) == 1, events


def test_two_budget_skips_without_attempt_stay_informational(collector_db):
    """The count-based bound must not fire before the 3-cadence convention."""
    from aletheia.data.collector_health import record_collector_status

    now = datetime.now(timezone.utc)
    for tick in (1, 2):
        record_collector_status(_STARVED_DOMAIN, "skipped_budget", tick=tick, occurred_at=now.isoformat())

    _report, row = _report_row(now)
    assert row["starved"] is False
    assert row["degraded"] is False
