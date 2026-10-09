"""`aletheia dev test-order`: debug-only demo entry through the real Gateway path."""

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

_ARGS = ["test-order", "BTC/USDT", "--direction", "short", "--quantity", "0.01", "--stop", "125000"]


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
    assert (audit["actor"], audit["source"], audit["entity_id"]) == ("human", "cli", "p1")
