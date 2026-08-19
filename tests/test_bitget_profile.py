"""Bitget profile contract tests — verifies classic_v2/uta_v3 isolation.

These are offline tests (no network, no credentials). They verify:
- Profile-aware client construction (unknown profile raises).
- classic_v2 cannot call uta_v3 endpoints and vice-versa.
- place_order on uta_v3 is blocked (qualification pending).
- Profile-specific error codes for "no position".
- Profile-specific pagination parameter name.
- Config validator rejects unknown profile values.
- Startup gate check recognises known profiles and warns on uta_v3.
"""

import pytest

from aletheia.exchanges.bitget.client import (
    BitgetClient,
    ProfileContractError,
    profile_close_absent_codes,
    profile_pagination_param,
)


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


# ─── Profile enforcement: uta_v3 cannot use classic_v2 endpoints ─────────────


def test_classic_v2_get_positions_blocked_on_uta_v3(monkeypatch):
    """uta_v3 client must not call v2 mix positions endpoint."""
    import asyncio

    c = BitgetClient(mode="demo", profile="uta_v3")
    with pytest.raises(ProfileContractError, match="classic_v2"):
        asyncio.run(c.get_positions())


def test_classic_v2_get_mix_account_blocked_on_uta_v3(monkeypatch):
    import asyncio

    c = BitgetClient(mode="demo", profile="uta_v3")
    with pytest.raises(ProfileContractError, match="classic_v2"):
        asyncio.run(c.get_mix_account())


# ─── Profile enforcement: classic_v2 cannot use uta_v3 endpoints ─────────────


def test_uta_v3_get_uta_account_blocked_on_classic_v2(monkeypatch):
    """classic_v2 client must not call the v3 UTA account endpoint."""
    import asyncio

    c = BitgetClient(mode="demo", profile="classic_v2")
    with pytest.raises(ProfileContractError, match="uta_v3"):
        asyncio.run(c.get_uta_account())


# ─── UTA v3 order placement is qualification-gated ───────────────────────────


def test_place_order_uta_v3_raises_not_implemented():
    """UTA v3 qualification guard must raise NotImplementedError."""
    c = BitgetClient(mode="demo", profile="uta_v3")
    with pytest.raises(NotImplementedError, match="uta_v3"):
        c._assert_uta_v3_qualified()


# ─── Error code tables ────────────────────────────────────────────────────────


def test_classic_v2_close_absent_codes():
    codes = profile_close_absent_codes("classic_v2")
    assert "22002" in codes
    assert "25227" in codes
    assert "45110" not in codes


def test_uta_v3_close_absent_codes_include_45110():
    codes = profile_close_absent_codes("uta_v3")
    assert "22002" in codes
    assert "25227" in codes
    assert "45110" in codes


# ─── Pagination parameter ─────────────────────────────────────────────────────


def test_classic_v2_uses_id_less_than_pagination():
    assert profile_pagination_param("classic_v2") == "idLessThan"


def test_uta_v3_uses_cursor_pagination():
    assert profile_pagination_param("uta_v3") == "cursor"


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


def _make_cfg(profile="classic_v2", mode="paper_internal"):
    from aletheia.config.schema import ExchangeConfig, AppConfig

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
