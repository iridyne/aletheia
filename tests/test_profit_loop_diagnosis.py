from __future__ import annotations

from copy import deepcopy
import json

import aletheia.analytics.profit_loop_diagnosis as diagnosis


def _position_fact(*, path_status: str = "available") -> dict:
    return {
        "contract_version": "profit_loop_fact/v1",
        "fact_unit": "position_fact",
        "fact_id": "position-1",
        "availability": "available",
        "producer": {"status": "available"},
        "causal_link": {"status": "available", "errors": []},
        "economics": {
            "availability": "available",
            "gross_pnl": 1.0,
            "net_pnl": -0.2,
            "entry_fee": 0.4,
            "exit_fee": 0.4,
            "funding_cost": 0.4,
        },
        "stages": {
            "decision": {
                "proposal": {
                    "attribution_snapshot": {
                        "position_hypothesis": {
                            "contract_version": "position_hypothesis/v1",
                            "invalidation_conditions": [{"kind": "price_at_or_below", "threshold": 98.0}],
                            "supporting_signals": [{"id": "atr_pct", "value": 1.0}],
                            "initial_risk": {
                                "quote_amount": 2.0,
                                "stop_price": 98.0,
                            },
                        }
                    }
                }
            },
            "holding": {
                "ohlcv": {
                    "availability": path_status,
                    "rows": [
                        [0, 100.0, 101.0, 99.0, 100.5, 10.0, "ccxt"],
                        [60_000, 100.5, 103.0, 97.0, 97.5, 11.0, "ccxt"],
                    ],
                },
                "trade_path": {
                    "path_evidence_status": "available",
                    "mfe_lower_bound_pct": 2.0,
                    "mae_upper_bound_pct": -3.0,
                    "realized_return_pct": -0.2,
                    "pre_stop_mfe_lower_bound_pct": 1.0,
                    "first_stop_barrier_status": "ambiguous",
                    "first_stop_barrier_open_ms": 2,
                },
            },
            "exit": {
                "availability": "available",
                "position": {
                    "entry_price": 100.0,
                    "quantity": 1.0,
                }
            },
        },
    }


def test_diagnosis_keeps_fact_byte_identical_and_costs_are_deducted_once():
    fact = _position_fact()
    before = json.dumps(fact, sort_keys=True, separators=(",", ":"))

    result = diagnosis.diagnose_position_fact(fact)

    after = json.dumps(fact, sort_keys=True, separators=(",", ":"))
    assert after == before
    assert result["contract_version"] == "profit_loop_diagnosis/v2"
    assert result["metrics"]["cost_initial_r"] == 0.6
    assert result["metrics"]["same_bar_ordering"] == "ambiguous"
    assert result["metrics"]["mfe_pct"] == 2.0
    assert result["metrics"]["gross_realized_return_pct"] is None
    assert result["metrics"]["giveback_pct"] is None
    assert result["metrics"]["hypothesis_prefix_states"] == [
        "confirming",
        "invalidated",
    ]
    assert result["metrics"]["accounting_cost_total"] == 1.2
    assert result["metrics"]["cost_attribution_status"] == "unavailable"
    assert result["diagnoses"]["cost_drag"]["status"] == "insufficient_data"
    assert result["diagnoses"]["cost_drag"]["reason"] == "avoidable_cost_baseline_required"
    assert result["diagnoses"]["profit_management_error"]["status"] == "insufficient_data"


def test_malformed_ohlcv_is_not_filtered_into_a_directional_diagnosis():
    fact = _position_fact()
    fact["stages"]["holding"]["ohlcv"]["rows"].append({"ts": "bad"})

    result = diagnosis.diagnose_position_fact(fact)

    assert result["status"] == "unavailable"
    assert "ohlcv_rows_invalid" in result["limitations"]
    assert result["diagnoses"]["profit_management_error"]["status"] == "unavailable"


def test_non_contiguous_ohlcv_cannot_produce_a_directional_diagnosis():
    fact = _position_fact()
    fact["stages"]["holding"]["ohlcv"]["rows"][1][0] = 120_000

    result = diagnosis.diagnose_position_fact(fact)

    assert result["status"] == "unavailable"
    assert "ohlcv_rows_invalid" in result["limitations"]


def test_malformed_hypothesis_is_not_an_exception_or_root_cause():
    fact = _position_fact()
    fact["stages"]["decision"]["proposal"]["attribution_snapshot"]["position_hypothesis"][
        "invalidation_conditions"
    ] = [{"kind": "unsupported"}]

    result = diagnosis.diagnose_position_fact(fact)

    assert result["metrics"]["hypothesis_prefix_states"] == ["not_evaluable", "not_evaluable"]
    assert result["diagnoses"]["direction_hypothesis_failure"]["status"] == "unavailable"


def test_verified_avoidable_cost_baseline_is_the_only_cost_candidate():
    fact = _position_fact()
    fact["cost_evidence"] = {
        "evidence_integrity": "verified",
        "evidence_refs": ["cost-evidence-1"],
        "avoidable": True,
        "baseline_kind": "realizable_limit",
        "baseline_id": "cost-baseline-1",
        "cost_model_id": "cost-v1",
        "avoidable_extra_cost_quote": 0.25,
    }

    result = diagnosis.diagnose_position_fact(fact)

    assert result["metrics"]["cost_attribution_status"] == "available"
    assert result["metrics"]["avoidable_extra_cost_quote"] == 0.25
    assert result["diagnoses"]["cost_drag"]["status"] == "measured"
    assert result["diagnoses"]["cost_drag"]["reason"] == "verified_avoidable_cost_baseline"


def test_overall_fact_unavailability_blocks_all_position_root_causes():
    fact = _position_fact()
    fact["availability"] = "unavailable"

    result = diagnosis.diagnose_position_fact(fact)

    assert result["status"] == "unavailable"
    assert result["primary_diagnosis"] == "data_or_accounting_gap"
    assert result["diagnoses"]["direction_hypothesis_failure"]["status"] == "unavailable"
    assert result["diagnoses"]["cost_drag"]["status"] == "unavailable"


def test_low_path_coverage_fails_closed_before_root_cause():
    result = diagnosis.diagnose_position_fact(_position_fact(path_status="unavailable"))

    assert result["status"] == "unavailable"
    assert result["primary_diagnosis"] == "data_or_accounting_gap"
    assert "ordered_path_unavailable" in result["limitations"]
    assert result["diagnoses"]["direction_hypothesis_failure"]["status"] == "unavailable"
    assert result["diagnoses"]["profit_management_error"]["status"] == "unavailable"
    assert result["diagnoses"]["cost_drag"]["status"] == "unavailable"


def test_candidate_diagnosis_uses_only_fixed_horizon_candidate_units():
    report = {
        "contract_version": "profit_loop_fact/v1",
        "fact_unit": "candidate_fact",
        "availability": "available",
        "as_of_at": "2026-08-13T00:00:00+00:00",
        "fixed_horizon": "24h",
        "settled_outcome_count": 2,
        "outcome_coverage": 1.0,
        "facts": [
            {
                "contract_version": "profit_loop_fact/v1",
                "fact_unit": "candidate_fact",
                "fact_id": "filtered",
                "first_exposure": {"candidate_kind": "opportunity"},
                "terminal_state": "filtered",
                "fixed_horizon_outcome": {
                    "status": "settled",
                    "net_return_pct": -1.0,
                },
                "variable_horizon_position_outcome": {"net_return_pct": 99.0},
            },
            {
                "contract_version": "profit_loop_fact/v1",
                "fact_unit": "candidate_fact",
                "fact_id": "traded",
                "first_exposure": {"candidate_kind": "opportunity"},
                "terminal_state": "traded",
                "fixed_horizon_outcome": {
                    "status": "settled",
                    "net_return_pct": 2.0,
                },
                "variable_horizon_position_outcome": {"net_return_pct": -99.0},
            },
        ],
    }
    frozen = deepcopy(report)

    result = diagnosis.diagnose_candidate_report(report)

    assert report == frozen
    assert result["layers"]["Selection"]["delta_pct"] == 3.0
    assert result["layers"]["Selection"]["statistical_unit"] == "outcome_candidate_key"
    assert result["layers"]["Selection"]["economic_unit"] == "fixed_horizon_net_return_pct"


def test_unavailable_candidate_report_cannot_produce_layer_direction():
    result = diagnosis.diagnose_candidate_report(
        {"availability": "unavailable", "facts": [], "unavailable_count": 1}
    )

    assert result["status"] == "unavailable"
    assert result["eligible_count"] == 0
    assert all(layer["status"] == "unavailable" for layer in result["layers"].values())


def test_malformed_candidate_report_fails_closed():
    result = diagnosis.diagnose_candidate_report(None)

    assert result["status"] == "unavailable"
    assert "candidate_report_unavailable" in result["limitations"]


def test_candidate_layers_compare_only_their_matching_populations():
    facts = []
    for candidate_kind, rejected, accepted in (
        ("opportunity", "filtered", "traded"),
        ("pi_decision", "pi_skip", "traded"),
        ("proposal", "risk_reject", "traded"),
    ):
        facts.extend(
            [
                {
                    "contract_version": "profit_loop_fact/v1",
                    "fact_unit": "candidate_fact",
                    "first_exposure": {"candidate_kind": candidate_kind},
                    "terminal_state": rejected,
                    "fixed_horizon_outcome": {
                        "status": "settled",
                        "net_return_pct": -1.0,
                    },
                },
                {
                    "contract_version": "profit_loop_fact/v1",
                    "fact_unit": "candidate_fact",
                    "first_exposure": {"candidate_kind": candidate_kind},
                    "terminal_state": accepted,
                    "fixed_horizon_outcome": {
                        "status": "settled",
                        "net_return_pct": 2.0,
                    },
                },
            ]
        )
    report = {
        "contract_version": "profit_loop_fact/v1",
        "fact_unit": "candidate_fact",
        "availability": "available",
        "as_of_at": "2026-08-13T00:00:00+00:00",
        "fixed_horizon": "24h",
        "settled_outcome_count": len(facts),
        "outcome_coverage": 1.0,
        "facts": facts,
    }

    result = diagnosis.diagnose_candidate_report(report)

    assert {layer: result["layers"][layer]["delta_pct"] for layer in ("Selection", "Decision", "Risk")} == {
        "Selection": 3.0,
        "Decision": 3.0,
        "Risk": 3.0,
    }
