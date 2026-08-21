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


def _make_candidate_fact(
    *,
    candidate_kind: str,
    terminal_state: str,
    net_return_pct: float,
    fact_id: str = "f",
    outcome_candidate_key: str = "key-1",
    direction: str = "long",
    cost_model_version: str = "v1",
    entry_ts: str = "2026-08-01T00:00:00+00:00",
    fixed_horizon: str = "24h",
) -> dict:
    return {
        "fact_id": fact_id,
        "first_exposure": {
            "candidate_kind": candidate_kind,
            "direction": direction,
            "entry_ts": entry_ts,
        },
        "terminal_state": terminal_state,
        "outcome_candidate_key": outcome_candidate_key,
        "fixed_horizon": fixed_horizon,
        "fixed_horizon_outcome": {
            "status": "settled",
            "net_return_pct": net_return_pct,
            "cost_model_version": cost_model_version,
        },
    }


def test_candidate_diagnosis_uses_only_fixed_horizon_candidate_units():
    report = {
        "as_of_at": "2026-08-13T00:00:00+00:00",
        "fixed_horizon": "24h",
        "settled_outcome_count": 2,
        "outcome_coverage": 1.0,
        "facts": [
            _make_candidate_fact(
                candidate_kind="opportunity",
                terminal_state="filtered",
                net_return_pct=-1.0,
                fact_id="filtered",
                outcome_candidate_key="key-filtered",
            ),
            _make_candidate_fact(
                candidate_kind="opportunity",
                terminal_state="traded",
                net_return_pct=2.0,
                fact_id="traded",
                outcome_candidate_key="key-traded",
            ),
        ],
    }
    frozen = deepcopy(report)

    result = diagnosis.diagnose_candidate_report(report)

    assert report == frozen
    assert result["layers"]["Selection"]["delta_pct"] == 3.0
    assert result["layers"]["Selection"]["statistical_unit"] == "outcome_candidate_key"
    assert result["layers"]["Selection"]["economic_unit"] == "fixed_horizon_net_return_pct"


def test_candidate_layers_compare_only_their_matching_populations():
    facts = []
    idx = 0
    for candidate_kind, rejected, accepted in (
        ("opportunity", "filtered", "traded"),
        ("pi_decision", "pi_skip", "traded"),
        ("proposal", "risk_reject", "traded"),
    ):
        facts.extend(
            [
                _make_candidate_fact(
                    candidate_kind=candidate_kind,
                    terminal_state=rejected,
                    net_return_pct=-1.0,
                    fact_id=f"{candidate_kind}-{rejected}",
                    outcome_candidate_key=f"key-{idx}",
                ),
                _make_candidate_fact(
                    candidate_kind=candidate_kind,
                    terminal_state=accepted,
                    net_return_pct=2.0,
                    fact_id=f"{candidate_kind}-{accepted}",
                    outcome_candidate_key=f"key-{idx+1}",
                ),
            ]
        )
        idx += 2
    report = {
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


def test_candidate_missing_direction_and_cost_model_excluded_from_eligible():
    """#1269: Rows missing direction or cost_model_version are excluded from eligible count."""
    report = {
        "as_of_at": "2026-08-13T00:00:00+00:00",
        "fixed_horizon": "24h",
        "settled_outcome_count": 3,
        "outcome_coverage": 1.0,
        "facts": [
            _make_candidate_fact(
                candidate_kind="opportunity",
                terminal_state="traded",
                net_return_pct=2.0,
                fact_id="valid",
                outcome_candidate_key="key-valid",
            ),
            # Missing direction
            {
                "fact_id": "no_direction",
                "first_exposure": {
                    "candidate_kind": "opportunity",
                    "entry_ts": "2026-08-01T00:00:00+00:00",
                },
                "terminal_state": "traded",
                "outcome_candidate_key": "key-no-direction",
                "fixed_horizon": "24h",
                "fixed_horizon_outcome": {
                    "status": "settled",
                    "net_return_pct": 3.0,
                    "cost_model_version": "v1",
                },
            },
            # Missing cost_model_version
            {
                "fact_id": "no_cost_model",
                "first_exposure": {
                    "candidate_kind": "opportunity",
                    "direction": "long",
                    "entry_ts": "2026-08-01T00:00:00+00:00",
                },
                "terminal_state": "traded",
                "outcome_candidate_key": "key-no-cost",
                "fixed_horizon": "24h",
                "fixed_horizon_outcome": {
                    "status": "settled",
                    "net_return_pct": 4.0,
                },
            },
        ],
    }

    result = diagnosis.diagnose_candidate_report(report)

    assert result["raw_count"] == 3
    assert result["eligible_count"] == 1  # Only the first valid fact
    assert result["excluded_count"] == 2
    # Check specific layer
    assert result["layers"]["Selection"]["eligible_count"] == 1
    assert result["layers"]["Selection"]["excluded_count"] == 2
    assert "missing_direction" in result["layers"]["Selection"]["exclusion_reasons"]
    assert "missing_cost_model_version" in result["layers"]["Selection"]["exclusion_reasons"]


def test_candidate_all_ineligible_yields_insufficient_data_not_directional():
    """#1269: All-ineligible groups → insufficient_data not directional."""
    report = {
        "as_of_at": "2026-08-13T00:00:00+00:00",
        "fixed_horizon": "24h",
        "settled_outcome_count": 2,
        "outcome_coverage": 1.0,
        "facts": [
            # Both facts missing cost_model_version
            {
                "fact_id": "filtered",
                "first_exposure": {
                    "candidate_kind": "opportunity",
                    "direction": "long",
                    "entry_ts": "2026-08-01T00:00:00+00:00",
                },
                "terminal_state": "filtered",
                "outcome_candidate_key": "key-1",
                "fixed_horizon": "24h",
                "fixed_horizon_outcome": {
                    "status": "settled",
                    "net_return_pct": -1.0,
                },
            },
            {
                "fact_id": "traded",
                "first_exposure": {
                    "candidate_kind": "opportunity",
                    "direction": "long",
                    "entry_ts": "2026-08-01T00:00:00+00:00",
                },
                "terminal_state": "traded",
                "outcome_candidate_key": "key-2",
                "fixed_horizon": "24h",
                "fixed_horizon_outcome": {
                    "status": "settled",
                    "net_return_pct": 2.0,
                },
            },
        ],
    }

    result = diagnosis.diagnose_candidate_report(report)

    assert result["layers"]["Selection"]["status"] == "insufficient_data"
    assert result["layers"]["Selection"]["eligible_count"] == 0
    assert result["layers"]["Selection"]["excluded_count"] == 2
    assert result["layers"]["Selection"]["baseline"] is None
    assert result["layers"]["Selection"]["observed"] is None
    assert result["layers"]["Selection"]["delta_pct"] is None

