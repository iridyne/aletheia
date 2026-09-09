"""Retired local account controls must not remain an executable API."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from aletheia.server.routes import settings


def test_new_config_has_no_implicit_execution_account():
    from aletheia.config.schema import AletheiaConfig

    cfg = AletheiaConfig()
    assert cfg.exchange.name == cfg.exchange.paper_execution_mode == cfg.risk.allowed_exchange == ""


@pytest.mark.parametrize("mode", ["paper_internal", ""])
def test_retired_or_unconfigured_mode_has_no_entry_or_risk_authority(mode):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.risk.policy import UnknownRiskExecutionMode, resolve_risk_policy
    from aletheia.trading.entry_authority import resolve_entry_runtime_authority

    cfg = AletheiaConfig()
    cfg.exchange.paper_execution_mode = mode
    cfg.agent.permission_mode = "Full"
    with pytest.raises(UnknownRiskExecutionMode):
        resolve_risk_policy(cfg, mode)
    authority = resolve_entry_runtime_authority(cfg)
    assert not authority.allowed
    assert authority.reason == "entry_execution_route_unsupported"


@pytest.mark.parametrize("method,path", [
    ("GET", "/paper-account"),
    ("POST", "/paper-account/credit"),
    ("POST", "/paper-account/debit"),
    ("POST", "/paper-account/set-balance"),
    ("POST", "/paper-account/close-all"),
    ("POST", "/paper-account/close-spot"),
])
@pytest.mark.asyncio
async def test_local_account_routes_are_removed(method, path):
    app = FastAPI()
    app.include_router(settings.router)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.request(method, f"/api/settings{path}", json={})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_retired_exchange_is_rejected_before_config_is_read(monkeypatch):
    def unexpected_config_read():
        pytest.fail("retired selection must not read or mutate runtime configuration")

    monkeypatch.setattr(settings, "load_config", unexpected_config_read)
    app = FastAPI()
    app.include_router(settings.router)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.post("/api/settings/primary-exchange", json={"exchange_id": "paper_internal"})
    assert response.status_code == 400


@pytest.mark.parametrize("exchange,mode", [
    ("bitget", "bitget_demo"),
    ("okx", "okx_demo"),
    ("binance", "binance_testnet"),
])
def test_external_exchange_selection_keeps_all_axes_aligned(monkeypatch, exchange, mode):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.files import jsonl

    cfg = AletheiaConfig()
    audits = []
    monkeypatch.setattr(settings, "load_config", lambda: cfg)
    monkeypatch.setattr(settings, "_mutate_config_with_candidate_publication_permit", lambda mutate: mutate(cfg))
    monkeypatch.setattr(jsonl, "write_audit", audits.append)

    result = settings.set_primary_exchange(settings.PrimaryExchangeRequest(exchange_id=exchange))

    assert result["exchange_id"] == cfg.exchange.name == cfg.risk.allowed_exchange == exchange
    assert cfg.market_data.ccxt_exchange == cfg.market_data.ccxt_fallback == exchange
    assert cfg.exchange.paper_execution_mode == mode
    assert audits[0]["event_type"] == "primary_exchange_changed"
