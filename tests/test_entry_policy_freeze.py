"""#1911: an open Position's Layer-0 guards stay frozen at its entry Risk Policy."""

from aletheia.config.schema import AletheiaConfig
from tests.specs import spec_pipeline_exchange_materialization as fixture


def _with_catastrophic(cfg, pct):
    live = cfg.risk.live
    cfg.risk.live = live.model_copy(
        update={"exits": live.exits.model_copy(update={"catastrophic_stop_pct": pct})}
    )
    return cfg


def test_open_position_keeps_entry_policy_catastrophic_stop(tmp_path, monkeypatch):
    import aletheia.portfolio.position_manager as pm
    from aletheia.risk.engine import _persist_risk_result
    from aletheia.risk.policy import resolve_risk_policy
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position, TradeProposal

    fixture._setup_db(tmp_path, monkeypatch)
    _, proposal, _, order = fixture._seed_native_chain(exchange_order_id="freeze-1", quantity=1.0)
    entry_cfg = _with_catastrophic(AletheiaConfig(), 10.0)
    decision = _persist_risk_result(
        TradeProposal.model_validate(repo.get_trade_proposal(proposal.id)), [],
        decision_role="preflight",
        resolved_policy=resolve_risk_policy(entry_cfg, "bitget_live_limited"),
    )
    repo.get_db().execute("UPDATE orders SET risk_decision_id=? WHERE id=?", (decision.decision_id, order.id))
    position = Position(
        symbol="BTC/USDT", market_type="spot", direction="long", quantity=1.0, entry_price=100.0,
        execution_mode="bitget_live_limited", order_id=order.id, proposal_id=proposal.id,
    )
    repo.insert_position(position)

    replaced_cfg = _with_catastrophic(AletheiaConfig(), 20.0)  # policy replaced after entry
    row = repo.get_position(position.id) or {}
    assert pm._layer0_safety_exit(row, 85.0, replaced_cfg) == ("catastrophic_stop", 85.0)


def test_unresolved_entry_policy_is_reported_not_silent(tmp_path, monkeypatch):
    import aletheia.portfolio.position_manager as pm
    from aletheia.storage import repositories as repo

    fixture._setup_db(tmp_path, monkeypatch)
    position = {"id": "p-no-entry", "execution_mode": "bitget_live_limited", "source": "native"}

    assert pm._report_entry_policy_unresolved(position, AletheiaConfig()) == "entry_order_missing"
    pm._report_entry_policy_unresolved(position, AletheiaConfig())
    events = [e["event_type"] for e in repo.list_audit_events(limit=50)]
    assert events.count("entry_risk_policy_unresolved") == 1
