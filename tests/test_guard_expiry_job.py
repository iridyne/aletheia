"""The retained scheduled guard task has expiry authority, not historical promotion."""

from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("legacy_enabled", [False, True])
def test_guard_job_only_runs_existing_expiry(monkeypatch, legacy_enabled, optional_evidence_jobs):
    from aletheia.agent_runtime import scheduler
    from extensions.analytics import guard_promotion as gp
    from aletheia.analytics import trade_attribution
    from aletheia.config.loader import load_config, save_config
    from extensions.knowledge.guards import TradingGuard
    from extensions.learning import control

    cfg = load_config()
    cfg.runtime.paused = False
    cfg.guard.auto_promote_enabled = legacy_enabled
    save_config(cfg)
    monkeypatch.setattr(gp, "load_trading_guards", lambda status: [TradingGuard(
        guard_id="auto_historical", title="historical", tags=["auto-generated"],
    )])
    reviewer = Mock(return_value=None)
    monkeypatch.setattr(control, "promotion_refreeze_reason", reviewer)
    historical = Mock(side_effect=AssertionError("legacy promotion read historical outcomes"))
    monkeypatch.setattr(trade_attribution, "build_trade_attribution_rows", historical)
    expiry = Mock(return_value=2)
    monkeypatch.setattr(gp, "_deprecate_expired_learned_guards", expiry)
    scheduler._run_guard_promotion()
    historical.assert_not_called()
    reviewer.assert_not_called()
    expiry.assert_called_once_with()


def test_paused_guard_job_does_not_run_expiry(monkeypatch, optional_evidence_jobs):
    from aletheia.agent_runtime import scheduler
    from extensions.analytics import guard_promotion as gp
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.runtime.paused = True
    save_config(cfg)
    expiry = Mock()
    monkeypatch.setattr(gp, "_deprecate_expired_learned_guards", expiry)
    scheduler._run_guard_promotion()
    expiry.assert_not_called()
