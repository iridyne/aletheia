from __future__ import annotations

import sys

import pytest

from scripts import stability_drive


def test_agent_degraded_status_is_a_drive_failure():
    statuses = {"completed": 0, "completed_with_errors": 0, "other": 0}
    degraded: list[str] = []

    stability_drive._record_agent_result(
        "paper_trader",
        2,
        {"run_id": "run-1", "status": "completed_with_errors", "errors": ["ValueError: bad"]},
        statuses,
        degraded,
    )

    assert statuses == {"completed": 0, "completed_with_errors": 1, "other": 0}
    assert degraded == ["cycle2:paper_trader:run-1:completed_with_errors:errors=ValueError"]


def test_agent_unknown_status_fails_closed():
    statuses = {"completed": 0, "completed_with_errors": 0, "other": 0}
    degraded: list[str] = []

    stability_drive._record_agent_result(
        "dreaming_agent",
        0,
        {"run_id": "run-2", "status": "half_done"},
        statuses,
        degraded,
    )

    assert statuses == {"completed": 0, "completed_with_errors": 0, "other": 1}
    assert degraded == ["cycle0:dreaming_agent:run-2:half_done"]


@pytest.mark.asyncio
async def test_initial_strategy_designer_degradation_is_recorded(monkeypatch):
    from aletheia.agent_runtime import orchestrator

    async def degraded_run_agent(agent_name: str, trigger_type: str = "scheduled"):
        assert agent_name == "strategy_designer"
        assert trigger_type == "manual"
        return {
            "run_id": "run-initial",
            "status": "completed_with_errors",
            "errors": ["provider unavailable"],
        }

    monkeypatch.setattr(orchestrator, "run_agent", degraded_run_agent)

    result = await stability_drive._drive(0)

    assert result["crashes"] == []
    assert result["statuses"] == {"completed": 0, "completed_with_errors": 1, "other": 0}
    assert result["degraded"] == [
        "initial:strategy_designer:run-initial:completed_with_errors:errors=provider unavailable"
    ]


def test_exercise_checks_require_a_real_chain():
    assert stability_drive._exercise_checks([], [], [], [], []) == [
        "not_exercised:strategy_designer",
        "not_exercised:proposal",
        "not_exercised:order",
        "not_exercised:fill_or_position",
        "not_exercised:proposal_order_link",
    ]

    clean = stability_drive._exercise_checks(
        [{"id": "strategy"}],
        [{"id": "proposal"}],
        [{"id": "order", "proposal_id": "proposal"}],
        [{"id": "fill", "order_id": "order"}],
        [],
    )
    assert clean == []


def _patch_main_storage(monkeypatch):
    from aletheia.storage import repositories as repo
    from aletheia.trading import strategy_lifecycle

    monkeypatch.setattr(stability_drive, "_setup", lambda _tmp: None)
    monkeypatch.setattr(repo, "list_strategies", lambda limit=100: [{"id": "strategy-1", "state": "paper_running"}])
    monkeypatch.setattr(repo, "list_trade_proposals", lambda limit=10000: [{"id": "proposal-1"}])
    monkeypatch.setattr(repo, "list_orders", lambda limit=10000: [{"id": "order-1", "proposal_id": "proposal-1"}])
    monkeypatch.setattr(repo, "list_fills", lambda limit=10000: [{"id": "fill-1", "order_id": "order-1"}])
    monkeypatch.setattr(repo, "list_positions", lambda limit=10000: [])
    monkeypatch.setattr(stability_drive, "_consistency_checks", lambda: [])
    monkeypatch.setattr(
        strategy_lifecycle,
        "check_promotion_gates",
        lambda _strategy_id: {"metrics": {"paper_trades": 1, "paper_runtime_days": 1, "closed_positions": 1}},
    )


def test_main_fails_when_drive_has_degraded_agent_runs(monkeypatch, capsys):
    _patch_main_storage(monkeypatch)

    async def degraded_drive(_cycles: int):
        return {
            "statuses": {"completed": 0, "completed_with_errors": 1, "other": 0},
            "crashes": [],
            "degraded": ["cycle0:paper_trader:run-1:completed_with_errors:errors=ValueError"],
        }

    monkeypatch.setattr(stability_drive, "_drive", degraded_drive)
    monkeypatch.setattr(sys, "argv", ["stability_drive.py", "1"])

    assert stability_drive.main() == 1
    output = capsys.readouterr().out
    assert "DEGRADED RUNS" in output
    assert "CLEAN:" not in output


def test_main_reports_clean_only_when_required_drive_and_chain_pass(monkeypatch, capsys):
    _patch_main_storage(monkeypatch)

    async def clean_drive(_cycles: int):
        return {
            "statuses": {"completed": 4, "completed_with_errors": 0, "other": 0},
            "crashes": [],
            "degraded": [],
        }

    monkeypatch.setattr(stability_drive, "_drive", clean_drive)
    monkeypatch.setattr(sys, "argv", ["stability_drive.py", "1"])

    assert stability_drive.main() == 0
    output = capsys.readouterr().out
    assert "CLEAN: required agent runs completed" in output
