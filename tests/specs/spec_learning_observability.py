"""Executable claims for Learning Contribution Observability.

The target contract is
docs/design/proposals/learning_observability.md. Every claim currently indexed
in this module is implemented as a behavioral test; this file carries no
strict-xfail placeholder for future target slices.
"""

from __future__ import annotations

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


_CONTRACT = "learning_observability"


@spec_claim(SpecClaim(
    "LCO-S01", _CONTRACT, "§6 target read APIs", "L2a",
    "A typed GET-only lco-v1 projection is indexed and introduces no Learning mutation endpoint.",
    status="implemented",
))
def test_lco_projection_is_indexed_and_get_only(tmp_path, monkeypatch):

    from aletheia.learning.read_models import build_learning_contribution_projection
    from aletheia.server.app import app

    routes = [
        route
        for route in app.routes
        if getattr(route, "path", None) == "/api/analytics/learning-contribution"
    ]
    assert len(routes) == 1
    assert getattr(routes[0], "methods", set()) == {"GET"}
    assert build_learning_contribution_projection()["schema_version"] == "lco-v1"
    assert all(
        set(getattr(route, "methods", set())) <= {"GET", "HEAD"}
        for route in app.routes
        if "learning-contribution" in getattr(route, "path", "")
    )


@spec_claim(SpecClaim(
    "LCO-C03", _CONTRACT, "§7 bounded read path", "L2c",
    "Observability reads perform no write, evaluation, lifecycle transition or authority mutation and use "
    "bounded cached work outside the event loop.",
    status="implemented",
))
@pytest.mark.anyio
async def test_lco_reads_are_side_effect_free_and_off_the_event_loop(
    tmp_path, monkeypatch,
):

    from httpx import ASGITransport, AsyncClient
    from aletheia.server.app import app
    from aletheia.server.routes import analytics
    from aletheia.storage.sqlite import get_db
    import aletheia.config.loader as config_loader

    config_loader.CONFIG_PATH.write_text(
        "agent:\n  permission_mode: Research\n"
        "operator:\n  permission_mode: Observe\n",
        encoding="utf-8",
    )
    config_loader._config_cache = None
    config_before = config_loader.CONFIG_PATH.read_bytes()

    analytics._learning_contribution_cache.clear()
    tables = ("learning_hypotheses", "learning_job_health", "learning_policy_epochs", "audit_events")

    def snapshot():
        db = get_db()
        return {
            table: [tuple(row) for row in db.execute(f"SELECT * FROM {table}")]
            for table in tables
        }

    before = snapshot()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        assert (await client.get("/api/analytics/learning-contribution")).status_code == 200
        assert (await client.get("/api/settings/learning/experiments?limit=20")).status_code == 503
        detail = await client.get(
            "/api/settings/learning/experiments/historical-only"
        )
        assert detail.status_code == 503
    assert snapshot() == before
    assert config_loader.CONFIG_PATH.read_bytes() == config_before


@spec_claim(SpecClaim(
    "LCO-C06", _CONTRACT, "§7 query and performance", "L2c",
    "The current summary uses monotonic TTL caching and emits explainable cold/cache-hit "
    "Server-Timing.",
    status="implemented",
))
@pytest.mark.anyio
async def test_lco_limits_monotonic_cache_and_server_timing(tmp_path, monkeypatch):

    from httpx import ASGITransport, AsyncClient
    from aletheia.server.app import app
    from aletheia.server.routes import analytics


    ticks = iter((100.0, 100.0, 120.0, 131.0, 131.0))
    monkeypatch.setattr(
        analytics, "_learning_contribution_monotonic", lambda: next(ticks),
    )
    analytics._learning_contribution_cache.clear()
    computes = 0
    real_compute = analytics._compute_learning_contribution

    def tracked_compute():
        nonlocal computes
        computes += 1
        return real_compute()

    monkeypatch.setattr(analytics, "_compute_learning_contribution", tracked_compute)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        cold = await client.get("/api/analytics/learning-contribution")
        warm = await client.get("/api/analytics/learning-contribution")
        expired = await client.get("/api/analytics/learning-contribution")

    assert computes == 2
    assert "ledger;dur=" in cold.headers["server-timing"]
    assert "cache_hit;dur=0.0" in warm.headers["server-timing"]
    assert "ledger;dur=" in expired.headers["server-timing"]
    assert cold.json()["generated_at"] == warm.json()["generated_at"]


def test_lco_cache_values_are_not_shared_with_callers(tmp_path, monkeypatch):

    from fastapi import Response
    from aletheia.server.routes import analytics

    analytics._learning_contribution_cache.clear()
    first = analytics.get_learning_contribution(Response())
    first["schema_version"] = "caller-mutated"
    first["items"].clear()

    second = analytics.get_learning_contribution(Response())
    assert second["schema_version"] == "lco-v1"


def test_lco_older_concurrent_miss_cannot_overwrite_newer_projection(
    tmp_path, monkeypatch,
):

    from fastapi import Response
    from threading import Event, Thread, current_thread
    from aletheia.server.routes import analytics

    older_started = Event()
    newer_published = Event()
    results: dict[str, dict] = {}

    def monotonic():
        name = current_thread().name
        if name == "older-lco":
            return 10.0
        if name == "newer-lco":
            return 20.0 if not newer_published.is_set() else 30.0
        return 31.0

    def compute():
        if current_thread().name == "older-lco":
            older_started.set()
            assert newer_published.wait(2)
            return {"generated_at": "older"}
        assert older_started.wait(2)
        return {"generated_at": "newer"}

    def request(name: str):
        results[name] = analytics.get_learning_contribution(Response())
        if name == "newer":
            newer_published.set()

    monkeypatch.setattr(analytics, "_learning_contribution_monotonic", monotonic)
    monkeypatch.setattr(analytics, "_compute_learning_contribution", compute)
    analytics._learning_contribution_cache.clear()
    older = Thread(target=request, args=("older",), name="older-lco")
    newer = Thread(target=request, args=("newer",), name="newer-lco")
    older.start()
    newer.start()
    older.join(2)
    newer.join(2)

    assert results == {
        "newer": {"generated_at": "newer"},
        "older": {"generated_at": "older"},
    }
    assert analytics.get_learning_contribution(Response())["generated_at"] == "newer"
