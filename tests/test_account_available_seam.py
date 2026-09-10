"""The available-balance seam that proposal sizing reads.

Equity answers "how big is this account"; available balance answers "how much is
not already pledged as margin".  Sizing needs the second one so each open
position shrinks the next entry.  An unreadable account must surface as None so
callers fail closed rather than substituting configured capital.
"""

import pytest


def test_uta_generation_mismatch_revokes_risk_equity_and_available(monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.portfolio import exchange_sync
    from aletheia.risk import engine

    cfg = AletheiaConfig()
    stale_generation = {
        "age_seconds": 1.0,
        "sync_status": "generation_mismatch",
        "accounts": [
            {
                "broker_id": "bitget_demo",
                "equity_usdt": 999.0,
                "available_usdt": 999.0,
            }
        ],
    }
    monkeypatch.setattr(engine, "load_config", lambda: cfg)
    monkeypatch.setattr(
        exchange_sync, "get_unified_cached_state", lambda: stale_generation
    )

    assert engine.account_equity_for_execution("bitget_demo", cfg) is None
    assert engine.account_available_for_execution("bitget_demo", cfg) is None


def test_uta_complete_zero_remains_authoritative_for_risk(monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.portfolio import exchange_sync
    from aletheia.risk import engine

    cfg = AletheiaConfig()
    complete_zero = {
        "age_seconds": 1.0,
        "sync_status": "ok",
        "accounts": [
            {
                "broker_id": "bitget_demo",
                "equity_usdt": 0.0,
                "available_usdt": 0.0,
            }
        ],
    }
    monkeypatch.setattr(engine, "load_config", lambda: cfg)
    monkeypatch.setattr(exchange_sync, "get_unified_cached_state", lambda: complete_zero)

    assert engine.account_equity_for_execution("bitget_demo", cfg) == 0.0
    assert engine.account_available_for_execution("bitget_demo", cfg) == 0.0

@pytest.mark.parametrize("mode", ["binance_testnet", "okx_demo", "bitget_demo",
                                 "binance_live_limited", "okx_live_limited", "bitget_live_limited"])
def test_external_mode_uses_only_matching_uta_balance(monkeypatch, mode):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.portfolio import exchange_sync
    from aletheia.risk import engine

    account = {"broker_id": mode, "equity_usdt": 100.0, "available_usdt": 30.0}
    cache = {"age_seconds": 1.0, "sync_status": "ok", "accounts": [account,
             {"broker_id": "unrelated", "equity_usdt": 9999.0, "available_usdt": 9999.0}]}
    monkeypatch.setattr(exchange_sync, "get_unified_cached_state", lambda **kwargs: cache)
    cfg = AletheiaConfig()
    assert engine.account_equity_for_execution(mode, cfg) == 100.0
    assert engine.account_available_for_execution(mode, cfg) == 30.0
    cache["accounts"].remove(account)
    assert engine.account_available_for_execution(mode, cfg) is None
    assert engine.account_equity_for_execution(mode, cfg) is None
    account["available_usdt"] = -12.5
    cache["accounts"].append(account)
    assert engine.account_available_for_execution(mode, cfg) == 0.0
