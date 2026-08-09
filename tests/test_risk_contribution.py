from __future__ import annotations

import json


def _seed_candidate(db, index: int, *, approved: bool, net_return_pct: float) -> None:
    proposal_id = f"risk-proposal-{index}"
    decision_id = f"risk-decision-{index}"
    outcome_key = f"proposal:{proposal_id}"
    created_at = f"2026-08-08T00:{index:02d}:00+00:00"
    due_at = f"2026-08-09T00:{index:02d}:00+00:00"
    settled_at = f"2026-08-09T00:{index + 1:02d}:00+00:00"
    proposal_commit = f"proposal-commit-{index}"
    directional_return_pct = net_return_pct + 0.2
    exit_price = 100 * (1 + directional_return_pct / 100)
    db.execute(
        "INSERT INTO trade_proposals "
        "(id, symbol, market_type, direction, quantity, price, execution_mode, "
        "status, source, created_at, commit_hash, runtime_session_id, "
        "producer_commit, behavior_fingerprint) "
        "VALUES (?, 'BTC/USDT', 'spot', 'long', 1, 100, 'paper_internal', "
        "'pending', 'native', ?, ?, 'session-a', 'build-a', 'behavior-a')",
        (proposal_id, created_at, proposal_commit),
    )
    db.execute(
        "INSERT INTO risk_decisions "
        "(id, proposal_id, approved, decision_role, evaluated_quantity, "
        "proposal_commit_hash, proposal_execution_mode, allowed_execution_mode, "
        "risk_mode, policy_id, policy_revision, policy_hash, created_at, "
        "runtime_session_id, producer_commit, behavior_fingerprint) "
        "VALUES (?, ?, ?, 'authoritative', 1, ?, 'paper_internal', "
        "'paper_internal', 'demo', 'demo-policy-v1', 1, 'policy-hash-a', ?, "
        "'session-a', 'build-a', 'behavior-a')",
        (decision_id, proposal_id, int(approved), proposal_commit, created_at),
    )
    decision = "passed" if approved else "rejected"
    reason = "risk_approved" if approved else "risk_rejected"
    db.execute(
        "INSERT INTO filter_decisions "
        "(id, dedup_key, candidate_key, candidate_kind, proposal_id, venue, "
        "symbol, market_type, direction, filter_name, stage, decision, reason_code, "
        "input_snapshot, parameter_snapshot, filter_version, code_commit, "
        "outcome_candidate_key, evaluated_at) "
        "VALUES (?, ?, ?, 'proposal', ?, 'bitget', 'BTC/USDT', 'spot', 'long', "
        "'risk_engine', 'risk', ?, ?, ?, ?, 'risk-v1', 'build-a', ?, ?)",
        (
            f"risk-filter-{index}",
            f"risk-filter-dedup-{index}",
            proposal_id,
            proposal_id,
            decision,
            reason,
            json.dumps({"risk_decision_id": decision_id, "execution_mode": "paper_internal"}),
            json.dumps({"authority": "RiskDecision", "auto_tunable": False}),
            outcome_key,
            created_at,
        ),
    )
    db.execute(
        "INSERT INTO filter_candidate_outcomes "
        "(id, outcome_candidate_key, venue, symbol, market_type, direction, horizon, "
        "entry_price_usd, entry_ts, due_at, settled_at, exit_price_usd, "
        "directional_return_pct, estimated_fee_pct, estimated_spread_slippage_pct, "
        "estimated_funding_pct, net_return_pct, settlement_price_source, "
        "cost_model_version, status) "
        "VALUES (?, ?, 'bitget', 'BTC/USDT', 'spot', 'long', '24h', 100, ?, "
        "?, ?, ?, ?, 0.1, 0.1, 0, ?, 'test_ohlcv', 'candidate-net-v1', 'settled')",
        (
            f"risk-outcome-{index}", outcome_key, created_at,
            due_at, settled_at, exit_price, directional_return_pct, net_return_pct,
        ),
    )


def test_risk_policy_value_is_paired_against_pass_all_control(monkeypatch):
    import aletheia.analytics.alpha_attribution as alpha_attribution
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for index in range(10):
        _seed_candidate(
            db,
            index,
            approved=index < 5,
            net_return_pct=1.0 if index < 5 else -2.0,
        )
    for table, key in (
        ("trade_proposals", "id"),
        ("risk_decisions", "id"),
    ):
        prefix = "risk-proposal" if table == "trade_proposals" else "risk-decision"
        db.execute(
            f"UPDATE {table} SET producer_commit='build-b' "
            f"WHERE {key} IN ({','.join('?' for _ in range(5))})",
            tuple(f"{prefix}-{index}" for index in range(5, 10)),
        )
    db.execute(
        "UPDATE filter_decisions SET code_commit='build-b' "
        "WHERE id IN ('risk-filter-5','risk-filter-6','risk-filter-7',"
        "'risk-filter-8','risk-filter-9')"
    )

    monkeypatch.setattr(
        alpha_attribution,
        "_utcnow",
        lambda: alpha_attribution.datetime.fromisoformat("2026-08-09T12:00:00+00:00"),
    )
    evidence = alpha_attribution.build_alpha_attribution_audit(days=7)[
        "risk_policy_evidence"
    ]

    assert evidence["measurement_status"] == "measured_contribution"
    assert evidence["contribution_verdict"] == "positive"
    assert evidence["causal_grade"] == "strong_prospective_paired_policy"
    assert evidence["statistical_unit"] == "proposal_id"
    assert evidence["economic_unit"] == "net_return_pct"
    assert evidence["eligible_count"] == 10
    assert evidence["approved_count"] == 5
    assert evidence["rejected_count"] == 5
    assert evidence["actual_intervention_count"] == 5
    assert evidence["baseline"] == -0.5
    assert evidence["observed"] == 0.5
    assert evidence["delta"] == 1.0
    assert evidence["behavior_authority"] is False
    assert evidence["promotion_eligible"] is False
    assert evidence["policy_identity"]["source_code_commits"] == ["build-a", "build-b"]


def test_risk_policy_value_fails_closed_without_enough_interventions():
    from aletheia.analytics.risk_contribution import build_risk_policy_contribution
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for index in range(5):
        _seed_candidate(db, index, approved=True, net_return_pct=1.0)

    evidence = build_risk_policy_contribution(
        db,
        cutoff="2026-08-01T00:00:00+00:00",
    )

    assert evidence["measurement_status"] == "not_measured"
    assert evidence["contribution_verdict"] == "insufficient_data"
    assert evidence["actual_intervention_count"] == 0
    assert evidence["reason"] == "insufficient_actual_risk_interventions"


def test_risk_policy_value_selects_current_policy_identity():
    from aletheia.analytics.risk_contribution import build_risk_policy_contribution
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for index in range(10):
        _seed_candidate(db, index, approved=index < 5, net_return_pct=-1.0)
    db.execute(
        "UPDATE risk_decisions SET policy_hash='policy-hash-b' "
        "WHERE id='risk-decision-9'",
    )

    evidence = build_risk_policy_contribution(
        db,
        cutoff="2026-08-01T00:00:00+00:00",
    )

    assert evidence["measurement_status"] == "not_measured"
    assert evidence["contribution_verdict"] == "insufficient_data"
    assert evidence["eligible_count"] == 1
    assert evidence["exclusion_counts"] == {"non_current_risk_policy_identity": 9}
    assert evidence["policy_identity"]["policy_hash"] == "policy-hash-b"
    assert evidence["reason"] == "insufficient_paired_risk_samples"


def test_risk_policy_value_rejects_mixed_proposal_risk_provenance():
    from aletheia.analytics.risk_contribution import build_risk_policy_contribution
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for index in range(10):
        _seed_candidate(db, index, approved=index < 5, net_return_pct=-1.0)
    db.execute(
        "UPDATE trade_proposals SET producer_commit='other-build' "
        "WHERE id='risk-proposal-9'",
    )

    evidence = build_risk_policy_contribution(
        db,
        cutoff="2026-08-01T00:00:00+00:00",
    )

    assert evidence["measurement_status"] == "not_measured"
    assert evidence["exclusion_counts"] == {
        "proposal_risk_provenance_mismatch": 1,
    }
    assert evidence["reason"] == "insufficient_actual_risk_interventions"


def test_risk_policy_value_rejects_malformed_approval_value():
    from datetime import datetime, timezone

    from aletheia.analytics.risk_contribution import _exclusion_reason

    assert _exclusion_reason(
        {"approved": "0"},
        datetime(2026, 8, 1, tzinfo=timezone.utc),
    ) == "invalid_risk_decision_value"


def test_risk_policy_value_accepts_equivalent_perp_market_aliases():
    from aletheia.analytics.risk_contribution import build_risk_policy_contribution
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _seed_candidate(db, 0, approved=False, net_return_pct=-1.0)
    db.execute(
        "UPDATE trade_proposals SET market_type='usdt_futures' "
        "WHERE id='risk-proposal-0'",
    )
    db.execute(
        "UPDATE filter_decisions SET market_type='perp' "
        "WHERE id='risk-filter-0'",
    )
    db.execute(
        "UPDATE filter_candidate_outcomes SET market_type='perp' "
        "WHERE id='risk-outcome-0'",
    )

    evidence = build_risk_policy_contribution(
        db,
        cutoff="2026-08-01T00:00:00+00:00",
    )

    assert evidence["eligible_count"] == 1
    assert evidence["exclusion_counts"] == {}
