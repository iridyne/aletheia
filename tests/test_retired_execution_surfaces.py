"""Retired local account controls must not remain an executable API."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from aletheia.server.routes import settings


def test_new_config_has_no_implicit_execution_account():
    from aletheia.config.schema import AletheiaConfig

    cfg = AletheiaConfig()
    assert cfg.exchange.paper_execution_mode == ""

    from aletheia.risk.engine import _config_active_execution_mode

    cfg.exchange.mode = "live"
    assert _config_active_execution_mode(cfg) == ""


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

    assert result["exchange_id"] == exchange
    # Market data follows the account: the override is cleared, the effective venue aligned.
    from aletheia.config.exchange_alignment import market_data_exchange
    assert cfg.market_data.ccxt_exchange == cfg.market_data.ccxt_fallback == ""
    assert market_data_exchange(cfg) == exchange
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

    from aletheia.portfolio.exit_plan import ExitPlan, save_exit_plan
    from aletheia.portfolio.outcome_hooks import enqueue_outcome_hook_jobs, _load_eligible_position

    with pytest.raises(ValueError, match="unsupported position execution mode"):
        save_exit_plan("archived-open", ExitPlan(stop_price=95))
    assert enqueue_outcome_hook_jobs({"id": "archived-open", "execution_mode": "paper_internal"}) == []
    assert _load_eligible_position("archived-open") == (None, "archived_execution_mode")
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


@pytest.mark.parametrize("mode", ["paper_internal", "", "bitget_unknown"])
def test_unsupported_modes_cannot_create_trading_state_or_supply_a_protective_venue(mode):
    from aletheia.data.price_feed import protective_price_venue
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position, PortfolioSnapshot

    assert protective_price_venue(mode) is None
    before = repo.get_db().total_changes
    with pytest.raises(ValueError, match="unsupported Order execution mode"):
        repo.validate_native_order_authority({"execution_mode": mode})
    with pytest.raises(ValueError, match="unsupported Position execution mode"):
        repo.insert_position(Position(
            symbol="BTC/USDT", market_type="spot", direction="long",
            quantity=1, entry_price=100, execution_mode=mode,
        ))
    with pytest.raises(ValueError, match="unsupported PortfolioSnapshot execution mode"):
        repo.insert_portfolio_snapshot(PortfolioSnapshot(total_value_usdt=100, execution_mode=mode))
    assert repo.get_db().total_changes == before


def test_new_proposal_and_snapshot_require_an_explicit_execution_mode():
    from pydantic import ValidationError
    from aletheia.storage.models import TradeProposal, PortfolioSnapshot

    with pytest.raises(ValidationError, match="execution_mode"):
        TradeProposal(symbol="BTC/USDT", market_type="spot", direction="long")
    with pytest.raises(ValidationError, match="execution_mode"):
        PortfolioSnapshot(total_value_usdt=100)


@pytest.mark.asyncio
async def test_simulated_approval_route_is_removed():
    from aletheia.server.routes import data

    app = FastAPI()
    app.include_router(data.router)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.post("/api/proposals/archived/approve-paper")
    assert response.status_code == 404


@pytest.mark.parametrize("mode", ["", "paper_internal", "unknown_exchange"])
def test_current_analytics_cannot_select_archived_or_missing_account(monkeypatch, mode):
    from aletheia.analytics import execution_mode, go_live_scorecard
    from aletheia.config.schema import AletheiaConfig

    cfg = AletheiaConfig()
    cfg.exchange.paper_execution_mode = mode
    monkeypatch.setattr("aletheia.config.loader.load_config", lambda: cfg)
    monkeypatch.setattr(go_live_scorecard, "read_complete_outcome_cohort", lambda **kwargs: pytest.fail("unresolved selection must not query all-mode outcomes"))

    assert execution_mode.resolve_selected_execution_mode() is None
    assert go_live_scorecard._genuine_closed_pnls(go_live_scorecard._scorecard_cohort()) == []
    assert go_live_scorecard._check_exchange_connectivity()["score"] == 0
    assert go_live_scorecard._check_risk_violations()["score"] == 0


def test_scorecard_uses_external_cohort_without_archived_activity(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from aletheia.analytics import go_live_scorecard
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage.sqlite import get_db

    now = datetime(2026, 1, 10, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(go_live_scorecard, "datetime", SimpleNamespace(now=lambda _tz: now))
    cfg = AletheiaConfig()
    cfg.exchange.paper_execution_mode = "bitget_demo"
    monkeypatch.setattr("aletheia.config.loader.load_config", lambda: cfg)
    modes = []

    from tests.helpers.outcome_cohort import available_cohort

    def outcomes(*, execution_mode):
        modes.append(execution_mode)
        return available_cohort([{"realized_pnl": 7.0, "trade_school": "short_term"}])

    monkeypatch.setattr(go_live_scorecard, "read_complete_outcome_cohort", outcomes)
    assert go_live_scorecard._genuine_closed_pnls(go_live_scorecard._scorecard_cohort()) == [7.0]
    assert go_live_scorecard.compute_pnl_by_trade_school()["short_term"]["count"] == 1
    assert modes == ["bitget_demo", "bitget_demo"]

    db = get_db()
    for mode, age, violations in [("paper_internal", 0, '["archived"]'), ("bitget_demo", 2, '[]')]:
        db.execute(
            "INSERT INTO positions(id,symbol,market_type,direction,execution_mode,opened_at,quantity,entry_price) VALUES (?,?,?,?,?,?,1,100)",
            (mode, "BTC/USDT", "spot", "long", mode, (now - timedelta(days=age)).isoformat()),
        )
        from aletheia.storage import repositories as repo
        from aletheia.storage.models import RiskDecision, TradeProposal

        repo._insert("trade_proposals", TradeProposal(
            id=mode, symbol="BTC/USDT", market_type="spot", direction="long", execution_mode=mode,
        ).model_dump())
        repo._insert("risk_decisions", RiskDecision(
            id=mode, proposal_id=mode, approved=True, proposal_execution_mode=mode, violations=violations,
            created_at=now.isoformat(),
        ).model_dump())
    assert go_live_scorecard.count_consecutive_zero_fill_days() == 2
    assert go_live_scorecard._check_risk_violations()["score"] == 100
    db.execute("UPDATE risk_decisions SET violations='[\"current\"]' WHERE id='bitget_demo'")
    assert go_live_scorecard._check_risk_violations()["score"] == 0
