"""Focused regressions for the EX-04 experimental Live boundary."""

from aletheia.config.schema import AletheiaConfig
from aletheia.diagnostics.config_consistency import _check_live_state


def test_unlocked_live_readiness_reports_paper_proposal_routing():
    cfg = AletheiaConfig()
    cfg.exchange.mode = "live"
    cfg.exchange.paper_execution_mode = "okx_demo"
    cfg.live.state = "live_limited"

    finding = _check_live_state(cfg)

    assert finding is not None
    assert finding.severity == "warning"
    assert "new proposals still use" in finding.message
    assert "okx_demo" in finding.message
    assert "GitHub #418" in finding.fix_hint


def test_locked_live_readiness_does_not_claim_unlock_enables_trading():
    cfg = AletheiaConfig()
    cfg.exchange.mode = "live"
    cfg.live.state = "live_disabled"

    finding = _check_live_state(cfg)

    assert finding is not None
    assert "paper/demo execution mode" in finding.message
    assert "does not enable live proposal routing" in finding.fix_hint
