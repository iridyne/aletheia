"""`aletheia dev test-order`: debug-only demo entry through the real Gateway path."""

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

_ARGS = ["test-order", "BTC/USDT", "--direction", "short", "--quantity", "0.01", "--price", "120000", "--stop", "125001", "--take-profit", "108000"]


def _mode(monkeypatch, mode):
    monkeypatch.setattr(
        "aletheia.config.loader.load_config",
        lambda: SimpleNamespace(exchange=SimpleNamespace(paper_execution_mode=mode)),
    )


def test_test_order_refuses_live_mode_before_any_proposal(monkeypatch):
    from aletheia.cli.dev import dev_app

    _mode(monkeypatch, "bitget_live_limited")
    monkeypatch.setattr("aletheia.execution.gateway.submit_proposal", lambda **_: pytest.fail("Proposal created"))

    result = CliRunner().invoke(dev_app, _ARGS)

    assert result.exit_code == 2
    assert "not a demo execution mode" in result.output


def test_test_order_submits_a_futures_proposal(monkeypatch):
    from aletheia.cli.dev import dev_app

    calls = []
    _mode(monkeypatch, "bitget_demo")
    monkeypatch.setattr(
        "aletheia.execution.gateway.submit_proposal",
        lambda **kwargs: calls.append(kwargs) or {"status": "rejected", "proposal_id": "p1", "risk_violations": ["x"]},
    )
    monkeypatch.setattr("aletheia.files.jsonl.write_audit", lambda event: calls.append(event))

    result = CliRunner().invoke(dev_app, _ARGS)

    assert result.exit_code == 1
    proposal, audit = calls
    assert (proposal["market_type"], proposal["execution_mode"], proposal["source"]) == ("usdt_futures", "bitget_demo", "native")
    # Risk compares reward:risk with calculate_entry_plan_risk_reward (rounded to 2 decimals), so an
    # unrounded 12000/5001 = 2.39952... would be rejected as entry_plan_risk_reward_mismatch.
    assert (proposal["price"], proposal["stop_price"], proposal["take_profit"], proposal["risk_reward"]) == (120000, 125001, 108000, 2.4)
    assert (audit["actor"], audit["source"], audit["entity_id"]) == ("human", "cli", "p1")
