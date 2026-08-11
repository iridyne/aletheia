"""Contracts for on-demand Ranker wakes and non-black-box inspection."""

from __future__ import annotations

import json
import asyncio
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner


ROOT = Path(__file__).resolve().parents[1]


class _FakeScheduler:
    running = True

    def __init__(self):
        self.modified: list[dict] = []

    def get_job(self, job_id: str):
        return SimpleNamespace(id=job_id, next_run_time=None)

    def modify_job(self, job_id: str, **kwargs):
        self.modified.append({"job_id": job_id, **kwargs})


def test_ranker_wake_coalesces_sources_and_exposes_status(monkeypatch):
    import aletheia.agent_runtime.scheduler as scheduler

    fake = _FakeScheduler()
    monkeypatch.setattr(scheduler, "_scheduler", fake)
    scheduler._reset_paper_trader_wake_state_for_tests()
    monkeypatch.setattr(scheduler.time, "monotonic", lambda: 100.0)

    assert scheduler.notify_paper_trader("universe_refresh_new_snapshot") is True
    assert scheduler.notify_paper_trader("guard_config_changed") is False

    status = scheduler.paper_trader_wake_status()
    assert status["pending"] is True
    assert status["pending_sources"] == [
        "guard_config_changed",
        "universe_refresh_new_snapshot",
    ]
    assert status["burst_count"] == 2
    assert len(fake.modified) == 1


def test_ranker_run_records_coalesced_wake_sources_and_result(monkeypatch):
    import aletheia.agent_runtime.scheduler as scheduler

    fake = _FakeScheduler()
    monkeypatch.setattr(scheduler, "_scheduler", fake)
    monkeypatch.setattr(
        scheduler,
        "load_config",
        lambda: SimpleNamespace(
            runtime=SimpleNamespace(paused=False, kill_switch_active=False),
            agent=SimpleNamespace(permission_mode="Paper"),
        ),
    )
    scheduler._reset_paper_trader_wake_state_for_tests()
    monkeypatch.setattr(scheduler.time, "monotonic", lambda: 100.0)
    scheduler.notify_paper_trader("universe_refresh_new_snapshot")
    scheduler.notify_paper_trader("ranker_config_changed")

    captured: dict[str, str] = {}

    async def fake_async_run(agent_name: str, *, trigger_type: str = "scheduled"):
        captured.update(agent_name=agent_name, trigger_type=trigger_type)
        return {"run_id": "run-1", "status": "completed", "outputs": [], "errors": []}

    monkeypatch.setattr(scheduler, "_async_run", fake_async_run)
    monkeypatch.setattr(
        scheduler,
        "_run_async_with_timeout",
        lambda _job_id, coroutine: asyncio.run(coroutine),
    )

    scheduler._run_paper_trader()

    assert captured == {
        "agent_name": "paper_trader",
        "trigger_type": "on_demand:ranker_config_changed,universe_refresh_new_snapshot",
    }
    status = scheduler.paper_trader_wake_status()
    assert status["pending"] is False
    assert status["last_result"]["run_id"] == "run-1"
    assert status["last_result"]["status"] == "completed"


def test_entry_status_cli_uses_authoritative_progress_projection(monkeypatch):
    from aletheia.cli.inspect_cmd import inspect_app
    import aletheia.server.routes.analytics as analytics

    payload = {
        "window_hours": 1,
        "current": {
            "state": "ranker_cooldown",
            "headline": "1 candidate is waiting for signal cooldown.",
            "primary_stage": "ranker_selection",
            "reason_code": "signal_cooldown",
            "reason": "The same instrument and direction was recently queued.",
            "next_trigger": "cooldown_expiry",
            "as_of": "2026-07-18T08:00:00+00:00",
            "ranker": {"counts": {"open": 1, "cooldown": 1}, "last_run": None},
            "candidates": [{
                "opportunity_id": "opp-1",
                "symbol": "NEAR/USDT",
                "direction": "short",
                "status": "ranker_cooldown",
                "stage": "ranker_selection",
                "blocking_filter": "signal_cooldown",
                "reason": "Waiting for the 30 minute signal cooldown.",
                "passed_filter_count": 7,
                "retry_in_sec": 600,
            }],
            "pipeline": [],
        },
    }
    monkeypatch.setattr(analytics, "get_opportunity_funnel", lambda hours=1: payload)

    result = CliRunner().invoke(inspect_app, ["entry-status", "--hours", "1", "--json"])

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["state"] == "ranker_cooldown"
    assert parsed["candidates"][0]["blocking_filter"] == "signal_cooldown"
    assert parsed["candidates"][0]["passed_filter_count"] == 7


def test_all_ranker_fact_changes_have_on_demand_wake_sources():
    expected = {
        "aletheia/universe/service.py": "universe_refresh_new_snapshot",
        "aletheia/agents/equity_observer.py": "equity_observer_opportunity",
        "aletheia/agents/prediction_observer.py": "prediction_observer_opportunity",
        "aletheia/agents/divergence_scanner.py": "divergence_scanner_opportunity",
        "aletheia/server/routes/settings.py": "ranker_config_changed",
        "aletheia/analytics/guard_promotion.py": "guard_set_changed",
        "aletheia/agent_shell/commands.py": "operator_queue_slot_released",
    }
    for relative, source in expected.items():
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert source in text, f"missing Ranker wake source {source} in {relative}"
