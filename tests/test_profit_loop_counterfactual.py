from __future__ import annotations

from copy import deepcopy

import aletheia.analytics.profit_loop_counterfactual as counterfactual
from aletheia.storage.proposal_hypothesis import evaluate_hypothesis_prefix


def _fact(*, invalidation_threshold: float = 50.0) -> dict:
    rows = []
    for index in range(70):
        open_price = 100.0 + index * 0.05
        rows.append(
            [
                index * 60_000,
                open_price,
                open_price + 0.2,
                open_price - 0.2,
                open_price + 0.1,
                10.0,
                "ccxt",
            ]
        )
    return {
        "fact_id": "position-1",
        "stages": {
            "decision": {
                "proposal": {
                    "attribution_snapshot": {
                        "position_hypothesis": {
                            "contract_version": "position_hypothesis/v1",
                            "invalidation_conditions": [
                                {
                                    "kind": "price_at_or_below",
                                    "threshold": invalidation_threshold,
                                }
                            ],
                            "initial_risk": {"stop_price": 98.0},
                        }
                    }
                }
            },
            "holding": {"ohlcv": {"rows": rows}},
            "exit": {
                "position": {
                    "direction": "long",
                    "closed_at": "2026-08-13T04:00:00+00:00",
                }
            },
        },
    }


def _cost() -> counterfactual.CostModel:
    return counterfactual.CostModel(
        model_id="paper-cost-v1",
        source_evidence_hash="a" * 64,
        verified_at="2026-08-13T03:59:00+00:00",
        entry_fee_bps=10.0,
        exit_fee_bps=10.0,
        slippage_bps_per_side=5.0,
        funding_bps_per_hour=2.0,
    )


def test_registry_is_finite_exploratory_prefix_only_and_does_not_mutate_fact():
    fact = _fact()
    frozen = deepcopy(fact)
    observed_prefixes: list[tuple[int, int]] = []

    def prefix_guard(hypothesis, *, completed_bars):
        timestamps = [int(row["ts"]) for row in completed_bars]
        assert timestamps == sorted(timestamps)
        assert len(timestamps) == len(set(timestamps))
        observed_prefixes.append((len(timestamps), timestamps[-1] if timestamps else -1))
        return evaluate_hypothesis_prefix(hypothesis, completed_bars=completed_bars)

    result = counterfactual.evaluate_counterfactual_registry(
        fact, cost_model=_cost(), hypothesis_evaluator=prefix_guard
    )

    assert fact == frozen
    assert result["contract_version"] == "profit_loop_counterfactual/v1"
    assert result["status"] == "evaluated"
    assert result["evidence_class"] == "exploratory"
    assert result["promotion_eligible"] is False
    assert [item["hypothesis_id"] for item in result["registry"]] == [
        "delay_1_bar/v1",
        "delay_3_bars/v1",
        "delay_5_bars/v1",
        "breakeven_after_1r/v1",
        "thesis_invalidation_exit/v1",
    ]
    assert observed_prefixes
    for item in result["results"]:
        assert item["promotion_eligible"] is False
        if item["treatment"]["status"] == "evaluated":
            assert item["treatment"]["slippage_embedded_in_gross"] is True
            expected_explicit = (20.0 + 2.0 * item["treatment"]["held_bars"] / 60.0) / 100.0
            assert item["treatment"]["explicit_cost_pct"] == expected_explicit


def test_delay_becomes_no_trade_when_thesis_invalidates_in_completed_prefix():
    fact = _fact(invalidation_threshold=101.0)

    result = counterfactual.evaluate_counterfactual_registry(fact, cost_model=_cost())
    delay = next(item for item in result["results"] if item["hypothesis_id"] == "delay_1_bar/v1")

    assert delay["treatment"] == {
        "status": "no_trade",
        "reason": "hypothesis_invalidated_before_delayed_entry",
    }
    assert delay["paired_net_delta_pct"] is None


def test_missing_cost_or_fixed_horizon_coverage_is_not_evaluable():
    no_cost = counterfactual.evaluate_counterfactual_registry(_fact(), cost_model=None)
    assert no_cost["status"] == "not_evaluable"
    assert no_cost["reason"] == "verified_cost_model_required"

    short = _fact()
    short["stages"]["holding"]["ohlcv"]["rows"] = short["stages"]["holding"]["ohlcv"]["rows"][:10]
    no_coverage = counterfactual.evaluate_counterfactual_registry(short, cost_model=_cost())
    assert no_coverage["status"] == "not_evaluable"
    assert no_coverage["reason"] == "fixed_horizon_coverage_unavailable"


def test_future_bars_after_pessimistic_stop_cannot_change_result():
    fact = _fact()
    rows = fact["stages"]["holding"]["ohlcv"]["rows"]
    rows[1][3] = 97.0
    first = counterfactual.evaluate_counterfactual_registry(fact, cost_model=_cost())
    changed = deepcopy(fact)
    for row in changed["stages"]["holding"]["ohlcv"]["rows"][2:]:
        row[4] = 1_000_000.0
    second = counterfactual.evaluate_counterfactual_registry(changed, cost_model=_cost())

    assert first["results"] == second["results"]
