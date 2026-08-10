"""AgentRun read models must not present Ranker skips as Operator decisions."""

from __future__ import annotations

import json


def test_paper_trader_outputs_receive_non_operator_semantics():
    from aletheia.server.routes.data import _agent_run_output_events

    events = _agent_run_output_events(
        {
            "agent_name": "paper_trader",
            "outputs_created": json.dumps(
                [
                    "skip:BTC/USDT:confidence_below_medium:speculative",
                    "skip_recent_signal:ETH/USDT:short",
                    "reuse_active_carrier:SOL/USDT:long:momentum",
                    "backpressure:AAVE/USDT:5/5",
                    "signals_pending:2",
                ]
            ),
        }
    )

    assert [event["event_class"] for event in events] == [
        "ranker_filter",
        "signal_cooldown",
        "carrier_reuse",
        "queue_wait",
        "signal_enqueue",
    ]
    assert all(event["is_operator_decision"] is False for event in events)
    assert events[1]["retryable"] is True
    assert events[3]["reason_code"] == "signal_queue_capacity"


def test_agent_run_routes_preserve_raw_outputs_and_add_semantics(monkeypatch):
    from aletheia.server.routes import data

    run = {
        "id": "run-1",
        "agent_name": "paper_trader",
        "outputs_created": '["skip_existing_position:BTC/USDT"]',
    }
    monkeypatch.setattr(data.repo, "list_agent_runs", lambda: [dict(run)])
    monkeypatch.setattr(data.repo, "get_agent_run", lambda _run_id: dict(run))

    listed = data.get_agent_runs()[0]
    detail = data.get_agent_run("run-1")
    assert listed["outputs_created"] == run["outputs_created"]
    assert detail["outputs_created"] == run["outputs_created"]
    assert listed["output_events"][0]["event_class"] == "existing_position"
    assert detail["output_events"][0]["is_operator_decision"] is False


def test_agent_run_route_forwards_usage_window(monkeypatch):
    from aletheia.server.routes import data

    observed = {}
    run = {"id": "run-1", "agent_name": "paper_trader", "outputs_created": "[]"}

    monkeypatch.setattr(data, "_ensure_db", lambda: None)

    def list_agent_runs(*, limit, days):
        observed.update(limit=limit, days=days)
        return [dict(run)]

    monkeypatch.setattr(data.repo, "list_agent_runs", list_agent_runs)

    assert data.get_agent_runs(days=7, limit=5_000)[0]["id"] == "run-1"
    assert observed == {"days": 7, "limit": 5_000}


def test_non_paper_agent_outputs_are_left_unclassified():
    from aletheia.server.routes.data import _agent_run_output_events

    assert _agent_run_output_events(
        {"agent_name": "market_observer", "outputs_created": '["opportunity:abc"]'}
    ) == []
