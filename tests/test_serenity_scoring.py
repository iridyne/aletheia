"""Unit tests for Serenity scoring and replay payload composition."""

from aletheia.trading.serenity_scoring import score_serenity_opportunity, make_replay_snapshot_payload


def test_serenity_score_candidate_bucket_and_reasons():
    opportunity = {
        "id": "opp-1",
        "symbol": "NVDA",
        "evidence_tier": "disclosed",
        "stage": "candidate_position",
        "average_mode": "historical_mismatch",
        "risk_flags": '["policy"]',
        "confidence_score": 85,
    }
    evidence = [
        {"id": "e1", "title": "Disclosed customer guide"},
        {"id": "e2", "title": "Supplier filing"},
    ]
    gates = [
        {"id": "g1", "status": "pass"},
        {"id": "g2", "status": "warn"},
    ]

    score = score_serenity_opportunity(opportunity, evidence=evidence, risk_gates=gates)
    assert score["score"] > 0
    assert 0 <= score["score"] <= 100
    assert score["bucket"] in {"watch", "research_priority", "candidate_position"}
    # candidate_position should still be possible with disclosed + advanced stage + avg score.
    assert score["opportunity_id"] == "opp-1"
    assert score["reasons"]["risk_gate_failures"] == 0
    assert score["risk_warn_gates"] == 1
    assert score["risk_flag_count"] == 1
    assert score["evidence_count"] == 2


def test_serenity_score_hard_fail_forces_watch_bucket():
    opportunity = {
        "id": "opp-2",
        "symbol": "NVDA",
        "evidence_tier": "disclosed",
        "stage": "candidate_position",
        "average_mode": "historical_mismatch",
        "risk_flags": [],
        "confidence_score": 85,
    }
    gates = [{"id": "g1", "status": "fail"}]
    score = score_serenity_opportunity(opportunity, evidence=[], risk_gates=gates)
    assert score["bucket"] == "watch"
    assert score["risk_fail_gates"] == 1
    assert score["evidence_count"] == 0


def test_replay_payload_includes_core_fields():
    payload = make_replay_snapshot_payload(
        {"id": "opp-3", "symbol": "NVDA", "stage": "order"},
        {"score": 74, "bucket": "candidate_position", "evidence_count": 5, "risk_fail_gates": 0, "risk_warn_gates": 1},
    )
    assert payload["opportunity_id"] == "opp-3"
    assert payload["symbol"] == "NVDA"
    assert payload["score_total"] == 74
    assert payload["bucket"] == "candidate_position"


def test_serenity_average_engine_reflects_risk_metrics():
    opportunity = {
        "id": "opp-4",
        "symbol": "RAMP",
        "stage": "ramp",
        "average_mode": "historical_mismatch",
        "risk_metrics": {
            "dca_fair_value_low": 10,
            "dca_fair_value_high": 20,
            "dca_max_adds": 3,
            "dca_current_adds": 1,
            "implied_vol": 2.0,
            "historical_iv": 1.0,
        },
        "evidence_tier": "disclosed",
        "confidence_score": 60,
        "risk_flags": "[]",
    }
    score = score_serenity_opportunity(opportunity, evidence=[], risk_gates=[])
    average = score["average_engine"]
    assert average["active"] is True
    assert average["dca_ladder"]["fair_value_low"] == 10
    assert average["dca_ladder"]["fair_value_high"] == 20
    assert average["signals"]["has_dca"] is True
    assert average["signals"]["has_regime_gap"] is True
    assert score["average_score"] > 0


def test_serenity_stage_index_mapping():
    for stage, expected in [
        ("research", 0),
        ("validation", 1),
        ("demand_wave", 2),
        ("sample", 3),
        ("supply_chain", 3),
        ("ramp", 4),
        ("order", 5),
        ("order_night", 5),
        ("candidate_position", 6),
    ]:
        opportunity = {
            "id": f"opp-stage-{stage}",
            "symbol": "ABC",
            "evidence_tier": "disclosed",
            "stage": stage,
            "average_mode": "none",
            "risk_flags": [],
            "confidence_score": 10,
        }
        score = score_serenity_opportunity(opportunity, evidence=[], risk_gates=[])
        assert score["stage_index"] == expected, stage
