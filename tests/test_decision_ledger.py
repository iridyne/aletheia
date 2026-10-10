"""Decision ledger reports the outcome authority's net return, never a local formula."""

import json
from datetime import datetime, timezone

import pytest


def test_ledger_reports_authority_outcome_unknown_funding_and_planless_state(tmp_path, monkeypatch):
    from aletheia.analytics.decision_ledger import build_decision_ledger
    from aletheia.storage import repositories as repo
    from tests.test_sqlite_writer import _setup_db
    from tests.test_universe_lifecycle_regression import _build_universe_lifecycle_chain

    _setup_db(tmp_path, monkeypatch)
    for name in ("account_equity_for_execution", "_account_equity", "account_available_for_execution"):
        monkeypatch.setattr(f"aletheia.risk.engine.{name}", lambda *a, **k: 5000.0)
    ids = _build_universe_lifecycle_chain(position_market_type="usdt_futures")
    hypothesis = {"decision": "ENTRY_PLAN", "reason_code": "OK", "mechanism": "reversal",
                  "direction": "long", "capture_reason": "range low reclaim", "risk_tier": "LOW"}
    repo.execute_write("UPDATE candidate_planning_generations SET validated_hypothesis_json=? WHERE id=?",
                       (json.dumps(hypothesis), ids["generation_id"]))
    # Long 1 @ 100 -> 108: gross 8, fees 0.1 + 0.1, broker funding 0.4 -> net 7.4 on notional 100.
    repo.execute_write("UPDATE positions SET funding_cost=0.4, funding_source='broker_ledger', "
                       "realized_pnl=7.4 WHERE id=?", (ids["position_id"],))
    now = datetime.now(timezone.utc).isoformat()
    repo.execute_write(
        "INSERT INTO candidate_planning_generations(id, planning_key, planning_identity, candidate_id, "
        "last_candidate_id, venue, symbol, market_type, candidate_family, direction, is_current, status, "
        "outcome_class, outcome_reason, created_at, updated_at, validated_hypothesis_json) "
        "VALUES('gen-rejected','key-rejected','identity-rejected','candidate-x','candidate-x','bitget',"
        "'ETH/USDT','perp','momentum','short',0,'superseded','planning_failed',"
        "'ENTRY_PLAN_COMPILE_INVALID:stop_distance_below_vol_target',?,?,?)",
        (now, now, json.dumps({**hypothesis, "direction": "short"})),
    )

    ledger = build_decision_ledger(days=7)
    rows = {row["generation_id"]: row for row in ledger["rows"]}
    traded = rows[ids["generation_id"]]
    assert traded["state"] == "closed"
    assert traded["outcome_eligible"] is True
    assert traded["net_return_pct"] == pytest.approx(7.4)
    assert traded["capture_reason"] == "range low reclaim"
    assert ledger["summary"]["outcomes"]["eligible"] == 1
    rejected = rows["gen-rejected"]
    assert (rejected["state"], rejected["state_reason"]) == (
        "planning_failed", "ENTRY_PLAN_COMPILE_INVALID:stop_distance_below_vol_target")

    repo.execute_write("UPDATE positions SET funding_cost=NULL, funding_source='unavailable' WHERE id=?",
                       (ids["position_id"],))
    traded = {row["generation_id"]: row for row in build_decision_ledger(days=7)["rows"]}[ids["generation_id"]]
    assert traded["outcome_eligible"] is False
    assert traded["outcome_reason"] == "incomplete_funding_economics"
    assert traded["net_return_pct"] is None
    assert traded["funding_cost"] is None
