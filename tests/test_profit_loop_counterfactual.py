from __future__ import annotations

from copy import deepcopy

import aletheia.analytics.profit_loop_counterfactual as counterfactual
import pytest
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
        "as_of_at": "2026-09-12T05:34:28+00:00",
        "producer": {
            "status": "available",
            "producer_commits": ["test-verified-deployment"],
            "behavior_fingerprints": ["test-behavior"],
        },
        "causal_link": {"status": "available"},
        "economics": {"availability": "available"},
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
            "holding": {
                "availability": "available",
                "ohlcv": {"availability": "available", "rows": rows},
            },
            "exit": {
                "position": {
                    "opened_at": "2026-08-13T05:34:28+00:00",
                    "direction": "long",
                    "closed_at": "2026-08-13T06:39:28+00:00",
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


@pytest.fixture(autouse=True)
def _registered_test_cohort(tmp_path, monkeypatch):
    monkeypatch.setenv("ALETHEIA_DATA_DIR", str(tmp_path))
    from aletheia.storage.migrations import apply_migrations
    from aletheia.storage.sqlite import get_db

    apply_migrations()
    values = {
        "path_id": "profit_loop_observability",
        "behavior_contract": counterfactual.COUNTERFACTUAL_CONTRACT_VERSION,
        "deploy_commit": "test-verified-deployment",
        "behavior_fingerprint": "test-behavior",
        "registry_hash": counterfactual.profit_loop_registry_hash(),
        "duration_days": 30,
        "assignment_unit": "position_id",
        "eligibility": counterfactual._ELIGIBILITY,
        "stopping_rule": counterfactual._STOPPING_RULE,
    }
    import json

    get_db().execute(
        "INSERT INTO learning_policy_epochs "
        "(epoch_id, epoch_kind, effective_values_hash, effective_values_json, "
        "affected_path_ids_json, safety_required, started_at, ended_at, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "profit-epoch",
            "evidence_anchor:profit_loop_observability",
            "test-hash",
            json.dumps(values, sort_keys=True),
            '["profit_loop_observability"]',
            0,
            "2026-08-13T05:34:28+00:00",
            None,
            "2026-08-13T05:34:28+00:00",
        ),
    )
    get_db().commit()
    monkeypatch.setattr(
        counterfactual,
        "_now_utc",
        lambda: counterfactual._parse_utc("2026-09-12T05:34:28+00:00"),
    )
    yield
    import aletheia.storage.sqlite as sqlite_mod

    try:
        sqlite_mod._local.conn.close()
    except AttributeError:
        pass


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


def test_delayed_entry_is_no_trade_when_frozen_stop_was_crossed_in_delay_prefix():
    fact = _fact()
    fact["stages"]["holding"]["ohlcv"]["rows"][0][3] = 97.0

    result = counterfactual.evaluate_counterfactual_registry(fact, cost_model=_cost())

    delay = next(item for item in result["results"] if item["hypothesis_id"] == "delay_1_bar/v1")
    assert delay["treatment"] == {
        "status": "no_trade",
        "reason": "frozen_stop_crossed_before_delayed_entry",
    }


def test_stop_gap_uses_adverse_bar_open_and_same_bar_breakeven_is_pessimistic():
    fact = _fact()
    rows = fact["stages"]["holding"]["ohlcv"]["rows"]
    rows[1][1:5] = [97.0, 101.2, 96.0, 100.0]
    result = counterfactual.evaluate_counterfactual_registry(fact, cost_model=_cost())
    baseline = result["results"][0]["baseline"]
    assert baseline["reason"] == "stop"
    assert baseline["exit_price"] < 97.0

    fact = _fact()
    rows = fact["stages"]["holding"]["ohlcv"]["rows"]
    rows[0][2] = 103.0
    rows[0][3] = 99.0
    result = counterfactual.evaluate_counterfactual_registry(fact, cost_model=_cost())
    breakeven = next(item for item in result["results"] if item["hypothesis_id"] == "breakeven_after_1r/v1")
    assert breakeven["treatment"]["reason"] == "breakeven_same_bar_ambiguous"


def test_registered_cohort_rejects_positions_opened_before_registration():
    fact = _fact()
    fact["stages"]["exit"]["position"]["opened_at"] = "2026-08-13T04:00:00+00:00"
    result = counterfactual.evaluate_counterfactual_registry(fact, cost_model=_cost())
    assert result["reason"] == "registered_cohort_eligibility_failed"


def test_registry_fails_closed_before_registered_cutoff():
    fact = _fact()
    fact["as_of_at"] = "2026-09-12T05:34:27+00:00"

    result = counterfactual.evaluate_counterfactual_registry(fact, cost_model=_cost())

    assert result["status"] == "not_evaluable"
    assert result["reason"] == "registered_evidence_cutoff_mismatch"


def test_registry_requires_explicit_fact_or_current_as_of():
    fact = _fact()
    fact.pop("as_of_at")

    result = counterfactual.evaluate_counterfactual_registry(fact, cost_model=_cost())

    assert result["status"] == "not_evaluable"
    assert result["reason"] == "evaluation_as_of_unavailable"


def test_registry_requires_post_deployment_registration():
    from aletheia.storage.sqlite import get_db

    get_db().execute("DELETE FROM learning_policy_epochs")
    get_db().commit()

    result = counterfactual.evaluate_counterfactual_registry(_fact(), cost_model=_cost())

    assert result["status"] == "not_evaluable"
    assert result["reason"] == "verified_deployment_registration_required"


def test_registry_rejects_multiple_producer_identities():
    fact = _fact()
    fact["producer"]["producer_commits"] = [
        "test-verified-deployment",
        "test-registration-deployment",
    ]

    result = counterfactual.evaluate_counterfactual_registry(fact, cost_model=_cost())

    assert result["reason"] == "complete_producer_identity_required"


@pytest.mark.parametrize("field", ["producer_commits", "behavior_fingerprints"])
def test_registry_rejects_duplicate_producer_identity_values(field):
    fact = _fact()
    fact["producer"][field] = fact["producer"][field] * 2

    result = counterfactual.evaluate_counterfactual_registry(fact, cost_model=_cost())

    assert result["reason"] == "complete_producer_identity_required"


def test_registry_rejects_unregistered_producer_commit():
    fact = _fact()
    fact["producer"]["producer_commits"] = ["later-unregistered-deployment"]

    result = counterfactual.evaluate_counterfactual_registry(fact, cost_model=_cost())

    assert result["reason"] == "verified_deployment_registration_required"


def test_registry_rejects_mismatched_behavior_fingerprint():
    fact = _fact()
    fact["producer"]["behavior_fingerprints"] = ["changed-config"]

    result = counterfactual.evaluate_counterfactual_registry(fact, cost_model=_cost())

    assert result["reason"] == "verified_deployment_registration_required"


def test_registry_rejects_wrong_registry_hash_duplicate_and_shortened_epoch():
    import json
    from aletheia.storage.sqlite import get_db

    db = get_db()
    row = db.execute("SELECT effective_values_json FROM learning_policy_epochs").fetchone()
    values = json.loads(row["effective_values_json"])
    values["registry_hash"] = "0" * 64
    db.execute(
        "UPDATE learning_policy_epochs SET effective_values_json=?",
        (json.dumps(values, sort_keys=True),),
    )
    db.commit()
    assert counterfactual.evaluate_counterfactual_registry(_fact(), cost_model=_cost())["reason"] == (
        "registered_registry_hash_mismatch"
    )

    values["registry_hash"] = counterfactual.profit_loop_registry_hash()
    db.execute(
        "UPDATE learning_policy_epochs SET effective_values_json=?",
        (json.dumps(values, sort_keys=True),),
    )
    duplicate_values = {**values, "registry_hash": "f" * 64}
    db.execute(
        "INSERT INTO learning_policy_epochs "
        "(epoch_id, epoch_kind, effective_values_hash, effective_values_json, "
        "affected_path_ids_json, safety_required, started_at, created_at) "
        "VALUES ('duplicate-epoch','evidence_anchor:profit_loop_observability',?,?,?,0,?,?)",
        (
            "duplicate-hash",
            json.dumps(duplicate_values, sort_keys=True),
            '["profit_loop_observability"]',
            "2026-08-13T05:34:28.000001+00:00",
            "2026-08-13T05:34:28.000001+00:00",
        ),
    )
    db.commit()
    assert counterfactual.evaluate_counterfactual_registry(_fact(), cost_model=_cost())["reason"] == (
        "registered_epoch_conflict"
    )

    db.execute("DELETE FROM learning_policy_epochs WHERE epoch_id='duplicate-epoch'")
    db.execute("UPDATE learning_policy_epochs SET ended_at='2026-08-14T05:34:28+00:00'")
    db.commit()
    assert counterfactual.evaluate_counterfactual_registry(_fact(), cost_model=_cost())["reason"] == (
        "registered_epoch_ended_before_cutoff"
    )


@pytest.mark.parametrize("payload", ["{invalid", "[]"])
def test_malformed_epoch_payload_fails_closed_before_identity_filtering(payload):
    from aletheia.storage.sqlite import get_db

    get_db().execute(
        "UPDATE learning_policy_epochs SET effective_values_json=?",
        (payload,),
    )
    get_db().commit()

    result = counterfactual.evaluate_counterfactual_registry(_fact(), cost_model=_cost())

    assert result["reason"] == "registered_epoch_malformed"


def test_epoch_ending_exactly_at_cutoff_is_complete():
    from aletheia.storage.sqlite import get_db

    get_db().execute("UPDATE learning_policy_epochs SET ended_at='2026-09-12T05:34:28+00:00'")
    get_db().commit()

    result = counterfactual.evaluate_counterfactual_registry(_fact(), cost_model=_cost())

    assert result["status"] == "evaluated"


@pytest.mark.parametrize("ended_at", ["", "not-a-timestamp"])
def test_malformed_epoch_end_fails_closed(ended_at):
    from aletheia.storage.sqlite import get_db

    get_db().execute("UPDATE learning_policy_epochs SET ended_at=?", (ended_at,))
    get_db().commit()

    result = counterfactual.evaluate_counterfactual_registry(_fact(), cost_model=_cost())

    assert result["reason"] == "registered_epoch_end_malformed"


def test_overflowing_epoch_start_fails_closed_and_is_not_a_cli_cutoff():
    from aletheia.storage.sqlite import get_db

    get_db().execute("UPDATE learning_policy_epochs SET started_at='9999-12-15T00:00:00+00:00'")
    get_db().commit()

    result = counterfactual.evaluate_counterfactual_registry(_fact(), cost_model=_cost())

    assert result["reason"] == "registered_epoch_malformed"
    assert counterfactual.profit_loop_registration_cutoffs() == []


def test_position_closed_after_registered_cutoff_is_ineligible():
    fact = _fact()
    fact["stages"]["exit"]["position"]["closed_at"] = "2026-09-12T05:34:29+00:00"

    result = counterfactual.evaluate_counterfactual_registry(fact, cost_model=_cost())

    assert result["reason"] == "registered_cohort_eligibility_failed"


def test_registry_rejects_incomplete_producer_identity():
    fact = _fact()
    fact["producer"]["status"] = "unavailable"

    result = counterfactual.evaluate_counterfactual_registry(fact, cost_model=_cost())

    assert result["reason"] == "complete_producer_identity_required"


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("causal_link", "status"), "unavailable"),
        (("economics", "availability"), "unavailable"),
        (("stages", "holding", "availability"), "unavailable"),
        (("stages", "holding", "ohlcv", "availability"), "unavailable"),
    ],
)
def test_registry_rejects_incomplete_position_fact(path, value):
    fact = _fact()
    target = fact
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    result = counterfactual.evaluate_counterfactual_registry(fact, cost_model=_cost())

    assert result["reason"] == "complete_position_fact_required"


def test_future_as_of_cannot_bypass_registered_cutoff(monkeypatch):
    monkeypatch.setattr(
        counterfactual,
        "_now_utc",
        lambda: counterfactual._parse_utc("2026-09-12T05:34:27+00:00"),
    )
    fact = _fact()
    fact["as_of_at"] = "2026-09-13T05:34:28+00:00"

    result = counterfactual.evaluate_counterfactual_registry(fact, cost_model=_cost())

    assert result["reason"] == "registered_cutoff_not_reached"


def test_registry_rejects_replay_evidence_after_registered_cutoff():
    fact = _fact()
    fact["as_of_at"] = "2026-09-12T05:34:29+00:00"

    result = counterfactual.evaluate_counterfactual_registry(fact, cost_model=_cost())

    assert result["reason"] == "registered_evidence_cutoff_mismatch"


def test_as_of_override_cannot_mask_post_cutoff_fact():
    fact = _fact()
    fact["as_of_at"] = "2026-09-12T05:34:29+00:00"

    result = counterfactual.evaluate_counterfactual_registry(
        fact,
        cost_model=_cost(),
        as_of_at="2026-09-12T05:34:28+00:00",
    )

    assert result["reason"] == "evaluation_as_of_conflicts_with_fact"
