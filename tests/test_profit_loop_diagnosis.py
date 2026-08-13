from __future__ import annotations

from copy import deepcopy
import json

import aletheia.analytics.profit_loop_diagnosis as diagnosis


def _position_fact(*, path_status: str = "available") -> dict:
    return {
        "contract_version": "profit_loop_fact/v1",
        "fact_unit": "position_fact",
        "fact_id": "position-1",
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
                        [1, 100.0, 101.0, 99.0, 100.5, 10.0, "ccxt"],
                        [2, 100.5, 103.0, 97.0, 97.5, 11.0, "ccxt"],
                    ],
                },
                "trade_path": {
                    "path_evidence_status": "ambiguous",
                    "mfe_lower_bound_pct": 2.0,
                    "mae_upper_bound_pct": -3.0,
                    "realized_return_pct": -0.2,
                    "pre_stop_mfe_lower_bound_pct": 1.0,
                    "first_stop_barrier_status": "available",
                    "first_stop_barrier_open_ms": 2,
                },
            },
            "exit": {
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
    assert result["contract_version"] == "profit_loop_diagnosis/v1"
    assert result["metrics"]["cost_initial_r"] == 0.6
    assert result["metrics"]["same_bar_ordering"] == "ambiguous"
    assert result["metrics"]["mfe_pct"] == 2.0
    assert result["metrics"]["hypothesis_prefix_states"] == [
        "confirming",
        "invalidated",
    ]
    assert result["diagnoses"]["cost_drag"]["status"] == "measured"
    assert result["diagnoses"]["profit_management_error"]["status"] == "insufficient_data"


def test_low_path_coverage_fails_closed_before_root_cause():
    result = diagnosis.diagnose_position_fact(_position_fact(path_status="unavailable"))

    assert result["status"] == "unavailable"
    assert result["primary_diagnosis"] == "data_or_accounting_gap"
    assert "ordered_path_unavailable" in result["limitations"]


def test_candidate_diagnosis_uses_only_fixed_horizon_candidate_units():
    report = {
        "as_of_at": "2026-08-13T00:00:00+00:00",
        "fixed_horizon": "24h",
        "settled_outcome_count": 2,
        "outcome_coverage": 1.0,
        "facts": [
            {
                "fact_id": "filtered",
                "terminal_state": "filtered",
                "fixed_horizon_outcome": {
                    "status": "settled",
                    "net_return_pct": -1.0,
                },
                "variable_horizon_position_outcome": {"net_return_pct": 99.0},
            },
            {
                "fact_id": "traded",
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
