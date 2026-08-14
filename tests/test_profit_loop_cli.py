from __future__ import annotations

import json

from typer.testing import CliRunner

import aletheia.analytics.candidate_facts as candidate_facts_module
import aletheia.analytics.position_replay as position_replay_module
import aletheia.analytics.profit_loop_counterfactual as counterfactual_module
from aletheia.cli.inspect_cmd import inspect_app


runner = CliRunner()


def test_position_and_candidate_diagnosis_commands_emit_versioned_json(monkeypatch):
    monkeypatch.setattr(
        position_replay_module,
        "build_position_replay",
        lambda position_id, **kwargs: {
            "fact_id": position_id,
            "causal_link": {"status": "unavailable", "errors": ["missing"]},
            "economics": {"availability": "unavailable"},
            "stages": {"holding": {"ohlcv": {"availability": "unavailable"}}},
        },
    )
    position = runner.invoke(inspect_app, ["position-diagnosis", "--id", "position-1", "--json"])
    assert position.exit_code == 0
    assert json.loads(position.stdout)["contract_version"] == "profit_loop_diagnosis/v1"

    monkeypatch.setattr(
        candidate_facts_module,
        "build_candidate_facts",
        lambda **kwargs: {
            "as_of_at": "2026-08-13T00:00:00+00:00",
            "fixed_horizon": "24h",
            "settled_outcome_count": 0,
            "outcome_coverage": 0.0,
            "facts": [],
        },
    )
    candidate = runner.invoke(inspect_app, ["candidate-diagnosis", "--json"])
    assert candidate.exit_code == 0
    candidate_payload = json.loads(candidate.stdout)
    assert candidate_payload["fact_unit"] == "candidate_fact"
    assert candidate_payload["read_only"] is True
    assert candidate_payload["behavior_changed"] is False


def test_counterfactual_command_requires_explicit_verified_cost_file(tmp_path, monkeypatch):
    cost_file = tmp_path / "cost.json"
    cost_file.write_text(
        json.dumps(
            {
                "model_id": "paper-cost-v1",
                "entry_fee_bps": 10.0,
                "exit_fee_bps": 10.0,
                "slippage_bps_per_side": 5.0,
                "funding_bps_per_hour": 2.0,
                "source_evidence_hash": "a" * 64,
                "verified_at": "2026-08-13T03:59:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        position_replay_module,
        "build_position_replay",
        lambda position_id, **kwargs: {"fact_id": position_id},
    )
    monkeypatch.setattr(
        counterfactual_module,
        "evaluate_counterfactual_registry",
        lambda fact, *, cost_model, as_of_at=None: {
            "contract_version": "profit_loop_counterfactual/v1",
            "counterfactual_scope": "first_60_minutes",
            "fact_id": fact["fact_id"],
            "cost_model_valid": cost_model.valid(),
        },
    )
    monkeypatch.setattr(
        counterfactual_module,
        "resolve_profit_loop_registration",
        lambda fact: {"status": "not_evaluable"},
    )
    monkeypatch.setattr(counterfactual_module, "profit_loop_registration_cutoffs", lambda: [])

    result = runner.invoke(
        inspect_app,
        [
            "profit-counterfactual",
            "--id",
            "position-1",
            "--cost-model",
            str(cost_file),
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["fact_id"] == "position-1"
    assert payload["counterfactual_scope"] == "first_60_minutes"
    assert payload["cost_model_valid"] is True


def test_counterfactual_default_replay_stops_at_first_matching_cutoff(tmp_path, monkeypatch):
    cost_file = tmp_path / "cost.json"
    cost_file.write_text(
        json.dumps(
            {
                "model_id": "paper-cost-v1",
                "entry_fee_bps": 1.0,
                "exit_fee_bps": 1.0,
                "slippage_bps_per_side": 1.0,
                "funding_bps_per_hour": 1.0,
                "source_evidence_hash": "a" * 64,
                "verified_at": "2026-08-13T03:59:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    newest = "2026-10-12T05:34:28+00:00"
    matching = "2026-09-12T05:34:28+00:00"
    older = "2026-08-12T05:34:28+00:00"
    calls = []
    monkeypatch.setattr(
        counterfactual_module,
        "profit_loop_registration_cutoffs",
        lambda: [newest, matching, older],
    )
    monkeypatch.setattr(
        position_replay_module,
        "build_position_replay",
        lambda position_id, **kwargs: (
            calls.append(kwargs["as_of_at"]) or {"fact_id": position_id, "as_of_at": kwargs["as_of_at"]}
        ),
    )
    monkeypatch.setattr(
        counterfactual_module,
        "resolve_profit_loop_registration",
        lambda fact: {
            "status": "available",
            "cutoff_at": matching,
        },
    )
    monkeypatch.setattr(
        counterfactual_module,
        "evaluate_counterfactual_registry",
        lambda fact, **kwargs: {"fact_id": fact["fact_id"], "as_of_at": fact["as_of_at"]},
    )

    result = runner.invoke(
        inspect_app,
        ["profit-counterfactual", "--id", "position-1", "--cost-model", str(cost_file), "--json"],
    )

    assert result.exit_code == 0
    assert calls == [newest, matching]
    assert json.loads(result.stdout)["as_of_at"] == matching


def test_profit_gradient_command_fails_closed_on_empty_evidence(tmp_path):
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "layer_samples": {},
                "evidence_context": {
                    "behavior_fingerprints": ["behavior-v1"],
                    "policy_epochs": ["policy-v1"],
                    "market_data_epochs": ["market-v1"],
                    "cost_model_epochs": ["cost-v1"],
                    "candidate_outcome_coverage": 0.0,
                    "position_causal_link_completeness": 0.0,
                    "verified_settled_economics_coverage": 0.0,
                    "ordered_path_coverage": 0.0,
                    "candidate_count": 0,
                    "capital_days": 0,
                    "risk_reject_count": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        inspect_app,
        ["profit-gradient", "--evidence", str(evidence), "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["verdict"] == "no_unique_direction"
    assert payload["winner_layer"] is None
    assert payload["automatic_behavior_change_authorized"] is False
