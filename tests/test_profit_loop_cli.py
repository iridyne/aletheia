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
            "counterfactual_horizon": "60_minutes_after_each_hypothetical_entry",
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
    assert payload["counterfactual_horizon"] == "60_minutes_after_each_hypothetical_entry"
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


def test_profit_gradient_command_rejects_hand_built_evidence(tmp_path):
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({"layer_samples": {}, "evidence_context": {}}), encoding="utf-8")

    result = runner.invoke(inspect_app, ["profit-gradient", "--evidence", str(evidence), "--json"])

    assert result.exit_code != 0
    assert "generated by profit-gradient-evidence" in result.output


def test_profit_gradient_command_fails_closed_on_empty_compiled_evidence(tmp_path):
    from aletheia.analytics.profit_gradient_evidence import build_profit_gradient_evidence

    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            build_profit_gradient_evidence(
                evidence_epoch_id="missing-epoch",
                cutoff_at="2026-08-14T00:00:00+00:00",
                cost_model_id="missing-cost-model",
            )
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
    assert payload["evidence_provenance"]["content_hash"]
    assert payload["evidence_provenance"]["inputs"]["evidence_epoch_id"] == "missing-epoch"


# ---------------------------------------------------------------------------
# `inspect lifecycle` — Candidate-first lifecycle inspector (#3281 Batch D)
# ---------------------------------------------------------------------------

def test_inspect_lifecycle_requires_exactly_one_anchor():
    """`inspect lifecycle` refuses zero or two anchors with exit code 2."""
    # Zero anchors
    zero = runner.invoke(inspect_app, ["lifecycle", "--json"])
    assert zero.exit_code == 2, zero.stdout
    # Two anchors — mutually exclusive
    two = runner.invoke(
        inspect_app, ["lifecycle", "--position-id", "a", "--entry-plan-id", "b", "--json"],
    )
    assert two.exit_code == 2, two.stdout


def test_inspect_lifecycle_all_anchors_return_universe_diagnosis(tmp_path, monkeypatch):
    """All six anchors emit lifecycle_diagnosis/v1 with source.kind=universe."""
    from tests.test_sqlite_writer import _setup_db
    from tests.test_universe_lifecycle_regression import _build_universe_lifecycle_chain

    _setup_db(tmp_path, monkeypatch)
    ids = _build_universe_lifecycle_chain(position_id="cli-uni-pos-1")

    anchor_flag_map = {
        "--candidate-id":   ids["candidate_id"],
        "--generation-id":  ids["generation_id"],
        "--entry-plan-id":  ids["entry_plan_id"],
        "--proposal-id":    ids["proposal_id"],
        "--order-id":       ids["entry_order_id"],
        "--position-id":    ids["position_id"],
    }

    for flag, value in anchor_flag_map.items():
        result = runner.invoke(inspect_app, ["lifecycle", flag, value, "--json"])
        assert result.exit_code == 0, f"{flag}: {result.stdout}"
        payload = json.loads(result.stdout)
        assert payload["contract_version"] == "lifecycle_diagnosis/v1", flag
        assert payload["source"]["kind"] == "universe", (
            f"{flag}: expected universe, got {payload['source']['kind']!r}"
        )
        assert payload["source"]["ids"]["candidate_id"] == ids["candidate_id"], flag
        assert payload["first_break"] is None, (
            f"{flag}: unexpected first_break={payload['first_break']}"
        )
        assert payload["coverage"]["gaps"] == [], (
            f"{flag}: unexpected gaps={payload['coverage']['gaps']}"
        )


def test_inspect_lifecycle_broken_plan_json_surfaces_identity_conflict(
    tmp_path, monkeypatch,
):
    """A corrupted plan_json.$.source_id must produce first_break.stage='identity'."""
    from tests.test_sqlite_writer import _setup_db
    from tests.test_universe_lifecycle_regression import _build_universe_lifecycle_chain
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db

    _setup_db(tmp_path, monkeypatch)
    ids = _build_universe_lifecycle_chain(position_id="cli-corrupt-pos-1")

    db = get_db()
    ep_row = repo.get_entry_plan(ids["entry_plan_id"])
    assert ep_row is not None
    plan = json.loads(ep_row["plan_json"])
    plan["source_id"] = "definitely-not-the-real-candidate-id"
    trg_sql_row = db.execute(
        "SELECT sql FROM sqlite_master WHERE name='trg_entry_plan_facts_immutable'"
    ).fetchone()
    trigger_sql = trg_sql_row["sql"]
    repo.execute_write("DROP TRIGGER trg_entry_plan_facts_immutable", ())
    repo.execute_write(
        "UPDATE entry_plans SET plan_json=? WHERE id=?",
        (json.dumps(plan, sort_keys=True), ids["entry_plan_id"]),
    )
    repo.execute_write(trigger_sql, ())

    result = runner.invoke(
        inspect_app, ["lifecycle", "--position-id", ids["position_id"], "--json"],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["source"]["kind"] == "universe"
    fb = payload["first_break"]
    assert fb is not None and fb["stage"] == "identity", payload
    assert payload["coverage"]["identity_conflicts"], payload
    assert payload["attention_state"] == "breakpoint"


def test_inspect_lifecycle_as_of_before_entry_plan_hides_the_chain(
    tmp_path, monkeypatch,
):
    """`--as-of` before the plan's created_at must produce an evidence_gap."""
    from tests.test_sqlite_writer import _setup_db
    from tests.test_universe_lifecycle_regression import _build_universe_lifecycle_chain

    _setup_db(tmp_path, monkeypatch)
    ids = _build_universe_lifecycle_chain(position_id="cli-cutoff-pos-1")

    result = runner.invoke(
        inspect_app,
        [
            "lifecycle",
            "--position-id", ids["position_id"],
            "--as-of", "1970-01-01T00:00:00+00:00",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["source"]["kind"] == "unknown", payload
    gaps = payload["coverage"]["gaps"]
    assert gaps and any("not_" in g for g in gaps), payload


def test_inspect_lifecycle_stages_expand_per_leg_and_execution(tmp_path, monkeypatch):
    """The upgraded inspector must emit per-leg execution stages, not just ancestry.

    Universe fixture has one entry leg → expect stages including
    proposal/risk_decision/order/fill/position/exit each tagged with leg=0,
    plus ancestry stages tagged leg=None. attention_state=='ended_ok' because
    the fixture's position is closed. economics.position_fact.availability
    should be 'available' because build_position_replay succeeds for the
    fixture.
    """
    from tests.test_sqlite_writer import _setup_db
    from tests.test_universe_lifecycle_regression import _build_universe_lifecycle_chain

    _setup_db(tmp_path, monkeypatch)
    ids = _build_universe_lifecycle_chain(position_id="cli-perleg-pos-1")

    result = runner.invoke(
        inspect_app, ["lifecycle", "--position-id", ids["position_id"], "--json"],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)

    stages = payload["stages"]
    stage_names = {s["stage"] for s in stages}
    assert {"proposal", "risk_decision", "order", "fill", "position", "exit"} <= stage_names, (
        f"missing execution stages, got {sorted(stage_names)}"
    )

    leg_stages = [s for s in stages if s.get("leg") is not None]
    assert leg_stages, "no per-leg stages emitted"
    leg_indices = {s["leg"] for s in leg_stages}
    assert leg_indices == {0}, f"expected only leg=0, got {leg_indices}"

    assert payload["attention_state"] == "ended_ok", payload
    assert payload["current_stage"] == "exit", payload

    econ = payload["economics"]["position_fact"]
    assert econ["availability"] == "available", econ
    assert "profit_loop_fact" in econ
