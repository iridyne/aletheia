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


def test_archived_accounting_is_readable_without_reconstruction():
    from aletheia.maintenance.historical_accounting import (
        historical_accounting_for_position,
        historical_accounting_summary,
    )
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute(
        "INSERT INTO positions(id,symbol,market_type,direction,quantity,entry_price,execution_mode,status) "
        "VALUES ('archived','BTC/USDT','spot','long',1,100,'paper_internal','closed')"
    )
    db.execute(
        "INSERT INTO historical_accounting_reconciliations "
        "(position_id,reconciliation_version,status,reconstructed_realized_pnl,method,evidence_json) "
        "VALUES ('archived','original-version','reconstructed',-3.5,'original-method',?)",
        ('{"source":"archive"}',),
    )
    before = db.total_changes

    row = historical_accounting_for_position("archived")
    totals = historical_accounting_summary(execution_mode="paper_internal")

    assert row["reconciliation_version"] == "original-version"
    assert row["evidence"] == {"source": "archive"}
    assert row["reconstructed_realized_pnl"] == totals["historical_reconciled_pnl"] == -3.5
    assert db.total_changes == before


def test_archived_position_has_no_current_execution_authority():
    from aletheia.portfolio.pnl import close_position
    from aletheia.portfolio.position_manager import close_position_routed
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute(
        "INSERT INTO positions(id,symbol,market_type,direction,quantity,entry_price,execution_mode,status) "
        "VALUES ('archived-open','BTC/USDT','spot','long',1,100,'paper_internal','open')"
    )
    before = db.total_changes

    with pytest.raises(ValueError, match="unsupported position execution mode"):
        close_position("archived-open", exit_price=101)
    with pytest.raises(ValueError, match="unsupported position execution_mode"):
        close_position_routed("archived-open", reason="manual")

    assert db.total_changes == before

    from aletheia.config.loader import load_config, save_config
    from aletheia.risk.engine import evaluate_proposal
    from aletheia.storage.models import TradeProposal

    cfg = load_config()
    cfg.risk.max_open_positions_demo = 1
    save_config(cfg)
    result = evaluate_proposal(TradeProposal(
        symbol="ETH/USDT", market_type="spot", direction="long",
        quantity=0.01, price=100, execution_mode="bitget_demo",
    ))
    assert not any(item.startswith("max_demo_positions_reached") for item in result.violations)
