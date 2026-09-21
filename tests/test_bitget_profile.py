"""Native reader profile construction, configuration and startup gates.

Private account request-family isolation and close rejection semantics belong to
the current UTA profiles (tests/uta/test_ccxt_adapter.py). Native exact-fill and
plan-history pagination is exercised by test_tpsl and test_bitget_uta_v3_switch,
not by tables belonging to the retired native trading implementation.
"""

import pytest

from aletheia.exchanges.bitget.client import BitgetClient


# ─── Construction ────────────────────────────────────────────────────────────


def test_classic_v2_constructs():
    c = BitgetClient(mode="demo", profile="classic_v2")
    assert c.profile == "classic_v2"


def test_uta_v3_constructs():
    c = BitgetClient(mode="demo", profile="uta_v3")
    assert c.profile == "uta_v3"


def test_unknown_profile_raises():
    with pytest.raises(ValueError, match="unknown Bitget profile"):
        BitgetClient(mode="demo", profile="v4_future")


def test_default_profile_is_classic_v2():
    c = BitgetClient(mode="demo")
    assert c.profile == "classic_v2"


# ─── Config validator ────────────────────────────────────────────────────────


def test_exchange_config_rejects_unknown_profile():
    from pydantic import ValidationError
    from aletheia.config.schema import ExchangeConfig

    with pytest.raises(ValidationError, match="bitget_profile"):
        ExchangeConfig(bitget_profile="v99_invalid")


def test_exchange_config_accepts_classic_v2():
    from aletheia.config.schema import ExchangeConfig

    cfg = ExchangeConfig(bitget_profile="classic_v2")
    assert cfg.bitget_profile == "classic_v2"


def test_exchange_config_accepts_uta_v3():
    from aletheia.config.schema import ExchangeConfig

    cfg = ExchangeConfig(bitget_profile="uta_v3")
    assert cfg.bitget_profile == "uta_v3"


def test_exchange_config_default_profile_is_classic_v2():
    from aletheia.config.schema import ExchangeConfig

    assert ExchangeConfig().bitget_profile == "classic_v2"


# ─── Startup gate check ──────────────────────────────────────────────────────


def _make_cfg(profile, mode):
    from aletheia.config.schema import ExchangeConfig

    class _FakeCfg:
        exchange = ExchangeConfig(bitget_profile=profile, paper_execution_mode=mode)

    return _FakeCfg()


def test_startup_gate_classic_v2_ok():
    from aletheia.agent_runtime.startup_behavior_gate import _check_bitget_profile

    ok, detail = _check_bitget_profile(_make_cfg("classic_v2", "bitget_demo"))
    assert ok
    assert "classic_v2" in detail


def test_startup_gate_uta_v3_ok_but_warns():
    from aletheia.agent_runtime.startup_behavior_gate import _check_bitget_profile

    ok, detail = _check_bitget_profile(_make_cfg("uta_v3", "bitget_demo"))
    assert ok  # non-blocking: still passes
    assert "Experimental" in detail


def test_startup_gate_unknown_profile_fails():
    from aletheia.agent_runtime.startup_behavior_gate import _check_bitget_profile

    class _FakeCfg:
        class exchange:
            bitget_profile = "bad_profile"
            paper_execution_mode = "bitget_demo"

    ok, detail = _check_bitget_profile(_FakeCfg())
    assert not ok
    assert "bad_profile" in detail
