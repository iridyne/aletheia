import pytest

from aletheia.portfolio.uta.okx_adapter import OkxDemoAdapter


class _BalanceClient:
    def __init__(self, balance):
        self.balance = balance

    async def fetch_balance(self):
        return self.balance


def _adapter_with_balance(data, fallback_usdt=100):
    adapter = OkxDemoAdapter.__new__(OkxDemoAdapter)
    adapter._client = _BalanceClient(
        {
            "info": {"data": data},
            "total": {"USDT": fallback_usdt},
            "free": {"USDT": 0},
            "used": {"USDT": 0},
        }
    )
    return adapter


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ([{"totalEq": "0"}], 0.0),
        ([{"totalEq": "-5"}], -5.0),
        ([{"totalEq": "12.5"}], 12.5),
        ([{}], 100.0),
        ([{"totalEq": ""}], 100.0),
    ],
)
async def test_total_equity_fallback_depends_on_presence_not_sign(data, expected):
    account = await _adapter_with_balance(data).get_account()

    assert account.equity_usdt == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("total_equity", ["invalid", "NaN", "Infinity", "-Infinity"])
async def test_invalid_total_equity_fails_closed(total_equity):
    adapter = _adapter_with_balance([{"totalEq": total_equity}])

    with pytest.raises(ValueError):
        await adapter.get_account()


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [None, "", "NaN", "Infinity", "-Infinity"])
async def test_invalid_fallback_equity_fails_closed(fallback):
    adapter = _adapter_with_balance([{}], fallback_usdt=fallback)

    with pytest.raises(ValueError):
        await adapter.get_account()


@pytest.mark.asyncio
async def test_zero_equity_reaches_named_uta_row_and_fails_closed_in_risk(monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.portfolio import exchange_sync
    from aletheia.risk import engine
    from aletheia.uta.manager import _legacy_projection
    from aletheia.uta.models import UTAAccountIdentity

    adapter = _adapter_with_balance([{"totalEq": "0"}])
    account = await adapter.get_account()
    identity = UTAAccountIdentity(
        account_id="okx_demo",
        execution_mode="okx_demo",
        venue="okx",
        environment="demo",
        profile="okx_demo",
        credential_generation=0,
        adapter_implementation="OkxDemoAdapter",
        adapter_version="legacy",
    )
    account_row, _ = _legacy_projection(identity, "legacy", adapter, account, [])
    monkeypatch.setattr(
        exchange_sync,
        "get_unified_cached_state",
        lambda: {"accounts": [account_row], "age_seconds": 0, "sync_status": "ok"},
    )
    cfg = AletheiaConfig()

    assert account_row["broker_id"] == "okx_demo"
    assert account_row["equity_usdt"] == 0.0
    assert engine._resolve_capital_base("okx_demo", cfg.risk.demo, cfg) == (
        None,
        "account_equity_non_positive:okx_demo",
    )
