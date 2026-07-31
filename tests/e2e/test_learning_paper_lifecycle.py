"""Isolated causal proof for the bounded Paper Learning lifecycle."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import math

from httpx import ASGITransport, AsyncClient
import pytest


_PATH_ID = "universe_family_calibration"
_FAMILY = "momentum"
_DIRECTION = "long"
_MARKET_TYPE = "perp"
_CALIBRATION_KEY = f"{_FAMILY}:{_DIRECTION}:{_MARKET_TYPE}"
_METRIC_VERSION = "universe_family_calibration_cluster_allocation_utility_v3"


@pytest.mark.anyio
async def test_universe_learning_paper_trial_refreezes_after_determinate_harm(
    monkeypatch,
    tmp_path,
):
    """Prove expose -> settle -> admit -> treat -> harm -> refreeze -> fallback."""
    from aletheia.config import loader, secrets
    from aletheia.config.schema import AletheiaConfig
    import aletheia.files.jsonl as jsonl_file
    import aletheia.learning.contribution_runner as contribution_runner
    import aletheia.learning.experiments as experiments
    import aletheia.learning.lifecycle as lifecycle
    import aletheia.learning.models as learning_models
    import aletheia.learning.policy_epochs as policy_epochs
    import aletheia.universe.filter_outcome_recorder as filter_settler
    from aletheia.learning.authority import resolve_learning_authority
    from aletheia.learning.cohorts import build_experiment_cohorts
    from aletheia.learning.contribution_runner import evaluate_experiment
    from aletheia.learning.experiments import stable_assignment
    from aletheia.learning.ledger import get_record
    from aletheia.learning.models import LearningExperiment
    from aletheia.server.app import app
    from aletheia.storage.sqlite import get_db
    from aletheia.universe.allocator import compute_total_score
    from aletheia.universe.filter_outcome_recorder import settle_due_filter_outcomes
    from aletheia.universe.service import _register_experiment_candidate_outcomes

    # Use the real config and secret persistence APIs against a test-private runtime
    # home. This exercises every module's normal load_config import alias and the
    # tunnel middleware's real token lookup without touching operator credentials.
    runtime_home = tmp_path / "phase4-runtime"
    runtime_home.mkdir()
    monkeypatch.setattr(loader, "DATA_DIR", runtime_home)
    monkeypatch.setattr(loader, "CONFIG_PATH", runtime_home / "config.yaml")
    monkeypatch.setattr(secrets, "get_data_dir", lambda: runtime_home)
    monkeypatch.setattr(jsonl_file, "get_data_dir", lambda: runtime_home)
    monkeypatch.setattr(jsonl_file, "_last_audit_hash", None)
    monkeypatch.setattr(experiments, "_LAST_SUCCESS_POLICY_VALUES", {})

    cfg = AletheiaConfig()
    cfg.agent.autonomous_learning = True
    cfg.runtime.paused = False
    cfg.learning.contribution_evaluation_enabled = True
    cfg.learning.lifecycle_automation_enabled = True
    cfg.learning.paper_influence_enabled = True
    cfg.learning.paper_influence_paths = [_PATH_ID]
    cfg.learning.auto_refreeze = True
    cfg.tunnel.enabled = True
    loader.save_config(cfg)
    secrets.set_secret("tunnel_api_token", "phase4-owner-token")

    # The production path has no wall-clock sleep requirement. A single test clock
    # advances every timestamp-producing module while preserving production model,
    # config, persistence, evaluator, authority and lifecycle interfaces.
    clock = [datetime(2035, 1, 1, tzinfo=timezone.utc)]

    class ClockDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = clock[0]
            if tz is None:
                return value.replace(tzinfo=None)
            return value.astimezone(tz)

    for module in (
        learning_models,
        policy_epochs,
        contribution_runner,
        lifecycle,
        filter_settler,
        jsonl_file,
    ):
        monkeypatch.setattr(module, "datetime", ClockDateTime)

    outcome_profiles: dict[str, str] = {}

    async def observations(rows, *, now):
        resolved = {}
        for row in rows:
            harmful = outcome_profiles[str(row["outcome_candidate_key"])] == "harmful"
            due_at = datetime.fromisoformat(str(row["due_at"]).replace("Z", "+00:00"))
            resolved[row["id"]] = {
                "exit_price": 90.0 if harmful else 102.0,
                "range_high": 103.0,
                "range_low": 85.0 if harmful else 99.0,
                "price_source": "phase4_e2e_horizon",
                "price_quality_ok": True,
                "settlement_lag_sec": max(0.0, (now - due_at).total_seconds()),
            }
        return resolved

    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder._batch_fetch_market_observations",
        observations,
    )

    def identities(phase: str, index: int) -> tuple[str, str, str]:
        # Two candidates share one production-like scanner run and therefore one
        # v3 assignment cluster; one is selected and one is dropped below.
        scoring_key = f"{phase}-run-{index // 2}"
        feature_id = f"{phase}-feature-{index}"
        candidate_key = (
            f"universe:{scoring_key}:{feature_id}:"
            f"{_FAMILY}:{_DIRECTION}:{_MARKET_TYPE}"
        )
        cluster_key = (
            f"universe:cluster:{scoring_key}:"
            f"{_FAMILY}:{_DIRECTION}:{_MARKET_TYPE}"
        )
        return scoring_key, candidate_key, cluster_key

    def score_candidate(phase: str, index: int) -> tuple[float, float, dict, dict]:
        scoring_key, candidate_key, _cluster_key = identities(phase, index)
        feature = {
            "feature_snapshot_id": f"{phase}-feature-{index}",
            "market_type": _MARKET_TYPE,
            "tradability_score": 70.0,
            "quote_volume_24h": cfg.universe.min_volume_24h_usdt * 20,
            "spread_bps": 1.0,
            "rsi_14": 50.0,
            "return_1h": 0.0,
            "return_4h": 0.0,
        }
        scanner = {
            "opportunity_score": 50.0,
            "scanner_family": _FAMILY,
            "scanner_direction": _DIRECTION,
            "direction": _DIRECTION,
        }
        baseline = compute_total_score(
            dict(feature),
            dict(scanner),
            cfg.universe,
            calibration=None,
            scoring_cohort_key=scoring_key,
        )
        scored_feature = dict(feature)
        score = compute_total_score(
            scored_feature,
            dict(scanner),
            cfg.universe,
            calibration={_CALIBRATION_KEY: 1.1},
            scoring_cohort_key=scoring_key,
        )
        assert scored_feature["learning_candidate_key"] == candidate_key
        exposure = get_record(
            "learning_exposures",
            scored_feature["learning_exposure_id"],
        )
        scored = {
            **scored_feature,
            **scanner,
            "symbol": f"P4{index}/USDT",
            "venue": "okx",
            "last_price": 100.0,
            "funding_rate": 0.0,
        }
        return score, baseline, exposure, scored

    def produce_phase(
        phase: str,
        *,
        experiment: LearningExperiment | None = None,
        harmful_treatment: bool,
    ) -> tuple[
        LearningExperiment,
        list[dict],
        dict[str, tuple[float, float]],
        Counter[str],
    ]:
        cluster_counts: Counter[str] = Counter()
        exposures: list[dict] = []
        scores: dict[str, tuple[float, float]] = {}
        index = 0
        for _batch in range(2_000):
            batch: list[dict] = []
            entry_at = clock[0] + timedelta(minutes=1)
            batch_assignment = None
            for _ in range(2):
                score, baseline, exposure, scored = score_candidate(phase, index)
                if experiment is None:
                    experiment = LearningExperiment(**get_record(
                        "learning_experiments",
                        exposure["experiment_id"],
                    ))
                assert exposure["experiment_id"] == experiment.experiment_id
                assignment = str(exposure["assignment"])
                if batch_assignment is None:
                    batch_assignment = assignment
                assert assignment == batch_assignment
                outcome_profiles[exposure["candidate_key"]] = (
                    "harmful"
                    if harmful_treatment and assignment == "treatment"
                    else "clear"
                )
                exposures.append(exposure)
                scores.setdefault(assignment, (score, baseline))
                batch.append(scored)
                index += 1
            assert batch_assignment is not None
            cluster_counts[batch_assignment] += 1

            # Register the complete scored arm while selecting only half the batch.
            # Both selected and dropped candidates retain all canonical horizons.
            assert _register_experiment_candidate_outcomes(
                batch,
                cfg,
                entry_ts=entry_at.isoformat(),
                allocated=batch[:1],
            ) == len(batch) * 4
            clock[0] += timedelta(hours=4)
            if cluster_counts["treatment"] >= 50 and cluster_counts["control"] >= 50:
                break

        assert experiment is not None
        assert cluster_counts["treatment"] >= 50
        assert cluster_counts["control"] >= 50
        return experiment, exposures, scores, cluster_counts

    async def settle_and_assert(experiment_id: str, exposure_count: int) -> list[dict]:
        clock[0] += timedelta(days=8)
        assert await settle_due_filter_outcomes(
            max_rows=10_000,
            time_budget_sec=30.0,
        ) == exposure_count * 4
        rows = [dict(row) for row in get_db().execute(
            "SELECT f.* FROM filter_candidate_outcomes f "
            "JOIN learning_exposures e "
            "ON e.candidate_key=f.outcome_candidate_key "
            "WHERE e.experiment_id=? ORDER BY f.entry_ts,f.horizon",
            (experiment_id,),
        ).fetchall()]
        assert len(rows) == exposure_count * 4
        assert Counter(row["horizon"] for row in rows) == {
            "15m": exposure_count,
            "1h": exposure_count,
            "4h": exposure_count,
            "24h": exposure_count,
        }
        assert Counter(row["allocation_selected"] for row in rows) == {
            0: exposure_count * 2,
            1: exposure_count * 2,
        }
        assert all(row["status"] == "settled" for row in rows)
        assert all(row["settlement_price_source"] == "phase4_e2e_horizon" for row in rows)
        assert all(row["cost_model_version"] for row in rows)
        economic_fields = (
            "exit_price_usd",
            "gross_return_pct",
            "directional_return_pct",
            "estimated_fee_pct",
            "estimated_spread_slippage_pct",
            "estimated_funding_pct",
            "net_return_pct",
            "min_tradable_edge_pct",
            "mfe_pct",
            "mae_pct",
            "settlement_lag_sec",
        )
        assert all(
            row[field] is not None and math.isfinite(float(row[field]))
            for row in rows
            for field in economic_fields
        )
        assert all(float(row["settlement_lag_sec"]) >= 0 for row in rows)
        return rows

    def evaluate_one(experiment: LearningExperiment, expected_count: int):
        cohorts = build_experiment_cohorts(experiment.experiment_id)
        assert len(cohorts) == 1
        assert not cohorts[0].invalid_reasons
        assert not cohorts[0].exclusion_reasons
        verdicts = evaluate_experiment(experiment.experiment_id)
        assert len(verdicts) == 1
        verdict = verdicts[0]
        harm = json.loads(verdict.harm_metrics_json)
        assert verdict.stage == experiment.stage
        assert verdict.target_horizon == "4h"
        assert verdict.metric_version == _METRIC_VERSION
        assert verdict.policy_epoch == epoch
        assert verdict.behavior_contract_hash == behavior_contract_hash
        assert verdict.behavior_contract_hash
        assert (
            datetime.fromisoformat(verdict.window_end)
            - datetime.fromisoformat(verdict.window_start)
        ) >= timedelta(days=7)
        assert verdict.unique_treatment_n + verdict.unique_control_n == expected_count
        assert harm["harm_indeterminate_reason"] is None
        return verdict, harm

    # Shadow traffic is not pre-filtered by assignment and spans a real seven-day
    # event-time window before owner admission.
    clock[0] += timedelta(minutes=1)
    shadow, shadow_exposures, _shadow_scores, shadow_cluster_counts = produce_phase(
        "shadow",
        harmful_treatment=False,
    )
    shadow_manifest = [json.loads(row["manifest_json"]) for row in shadow_exposures]
    assert all(manifest["authority_applied"] is False for manifest in shadow_manifest)
    await settle_and_assert(shadow.experiment_id, len(shadow_exposures))
    epoch = str(shadow_exposures[0]["concurrent_policy_epoch"])
    behavior_contract_hash = str(shadow_exposures[0]["behavior_contract_hash"])
    shadow_verdict, shadow_harm = evaluate_one(
        shadow,
        sum(shadow_cluster_counts.values()),
    )
    assert shadow_verdict.verdict == "insufficient_data"
    assert shadow_verdict.reason == "no_actual_intervention"
    assert shadow_harm["harm_state"] == "clear"
    assert shadow_harm["authority_applied_treatment_n"] == 0
    assert (
        shadow_verdict.unique_treatment_n,
        shadow_verdict.unique_control_n,
    ) == (
        shadow_cluster_counts["treatment"],
        shadow_cluster_counts["control"],
    )

    # Admission is an authenticated owner action: invalid confirmation still
    # fails validation, while missing/wrong credentials fail before the route.
    clock[0] += timedelta(hours=1)
    endpoint = f"/api/settings/learning/experiments/{shadow.experiment_id}/admit-trial"
    payload = {
        "verdict_id": shadow_verdict.verdict_id,
        "expected_policy_epoch": epoch,
        "reason": "phase4 bounded paper causal proof",
    }
    headers = {"X-Aletheia-Token": "phase4-owner-token"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        missing_token = await client.post(
            endpoint,
            json={**payload, "confirm_admission": True},
        )
        wrong_token = await client.post(
            endpoint,
            json={**payload, "confirm_admission": True},
            headers={"X-Aletheia-Token": "wrong"},
        )
        denied_confirmation = await client.post(
            endpoint,
            json={**payload, "confirm_admission": False},
            headers=headers,
        )
        admitted = await client.post(
            endpoint,
            json={**payload, "confirm_admission": True},
            headers=headers,
        )
    assert missing_token.status_code == 401
    assert wrong_token.status_code == 401
    assert denied_confirmation.status_code == 422
    assert admitted.status_code == 200
    successor = LearningExperiment(**get_record(
        "learning_experiments",
        admitted.json()["successor_experiment_id"],
    ))
    assert successor.stage == "paper_10"
    assert successor.allocation_pct == pytest.approx(0.10)
    assert successor.activation_verdict_id == shadow_verdict.verdict_id
    assert get_record("learning_experiments", shadow.experiment_id)["status"] == "closed"

    db = get_db()
    admission_audit = db.execute(
        "SELECT actor,source,event_type,entity_id,message FROM audit_events "
        "WHERE entity_id=? ORDER BY rowid DESC LIMIT 1",
        (shadow.experiment_id,),
    ).fetchone()
    assert dict(admission_audit) == {
        "actor": "human",
        "source": "dashboard",
        "event_type": "learning_lifecycle_transition",
        "entity_id": shadow.experiment_id,
        "message": "owner_admitted:phase4 bounded paper causal proof",
    }

    # Keep every emitted paper candidate. At 10% allocation this naturally
    # produces a much larger control denominator than treatment.
    clock[0] += timedelta(minutes=1)
    paper, paper_exposures, paper_scores, paper_cluster_counts = produce_phase(
        "paper",
        experiment=successor,
        harmful_treatment=True,
    )
    paper_counts = Counter(row["assignment"] for row in paper_exposures)
    assert paper.experiment_id == successor.experiment_id
    assert paper_cluster_counts["treatment"] >= 50
    assert paper_cluster_counts["control"] >= 50
    assert paper_counts["control"] > paper_counts["treatment"] * 4
    assert paper_counts["treatment"] / len(paper_exposures) == pytest.approx(
        successor.allocation_pct,
        abs=0.05,
    )
    assert paper_scores["treatment"][0] > paper_scores["treatment"][1]
    assert paper_scores["control"][0] == pytest.approx(paper_scores["control"][1])
    assert all(
        json.loads(row["manifest_json"])["authority_applied"]
        is (row["assignment"] == "treatment")
        for row in paper_exposures
    )
    paper_rows = await settle_and_assert(successor.experiment_id, len(paper_exposures))

    paper_4h = {
        row["outcome_candidate_key"]: row
        for row in paper_rows
        if row["horizon"] == "4h"
    }
    for exposure in paper_exposures:
        outcome = paper_4h[exposure["candidate_key"]]
        started_at = datetime.fromisoformat(str(successor.started_at))
        exposed_at = datetime.fromisoformat(str(exposure["exposed_at"]))
        entry_at = datetime.fromisoformat(str(outcome["entry_ts"]))
        due_at = datetime.fromisoformat(str(outcome["due_at"]))
        settled_at = datetime.fromisoformat(str(outcome["settled_at"]))
        assert started_at <= exposed_at <= entry_at <= due_at <= settled_at
        if exposure["assignment"] == "treatment":
            assert float(outcome["net_return_pct"]) < 0
            assert float(outcome["mae_pct"]) < -0.25
        else:
            assert float(outcome["net_return_pct"]) > 0

    negative, harm = evaluate_one(successor, sum(paper_cluster_counts.values()))
    assert negative.verdict == "negative"
    assert harm["harm_state"] == "breached"
    assert harm["breached"] is True
    assert harm["loss_rate_delta"] > 0.15
    assert harm["authority_applied_treatment_n"] == paper_cluster_counts["treatment"]
    assert (
        negative.unique_treatment_n,
        negative.unique_control_n,
    ) == (
        paper_cluster_counts["treatment"],
        paper_cluster_counts["control"],
    )

    # A determinate current-stage negative removes authority before and after the
    # controller persists the refreeze.
    pre_refreeze_authority = resolve_learning_authority(_PATH_ID, successor, epoch)
    assert not pre_refreeze_authority.authorized
    assert pre_refreeze_authority.reason == "harm_gate_breached"
    clock[0] += timedelta(hours=1)
    transition = lifecycle.process_lifecycle(
        successor.experiment_id,
        current_policy_epoch=epoch,
    )
    assert transition.action == "refreeze"
    assert transition.verdict_id == negative.verdict_id
    persisted = get_record("learning_experiments", successor.experiment_id)
    assert persisted["status"] == "refrozen"
    assert persisted["ended_at"] is not None
    post_refreeze_authority = resolve_learning_authority(
        _PATH_ID,
        LearningExperiment(**persisted),
        epoch,
    )
    assert not post_refreeze_authority.authorized
    assert post_refreeze_authority.reason == "experiment_not_active"

    # Negative refreeze closes this paper generation but intentionally permits a
    # fresh shadow observation window. Pick a known treatment assignment in that
    # new shadow and prove its baseline comes specifically from non-paper authority.
    clock[0] += timedelta(minutes=1)
    first_score, first_baseline, first_exposure, _ = score_candidate("fallback", 0)
    assert first_score == pytest.approx(first_baseline)
    fallback_shadow = LearningExperiment(**get_record(
        "learning_experiments",
        first_exposure["experiment_id"],
    ))
    fallback_index = next(
        index
        for index in range(2, 10_000, 2)
        if stable_assignment(fallback_shadow, identities("fallback", index)[2]) == "treatment"
    )
    fallback_score, fallback_baseline, fallback_exposure, _ = score_candidate(
        "fallback",
        fallback_index,
    )
    fallback_manifest = json.loads(fallback_exposure["manifest_json"])
    assert fallback_shadow.hypothesis_id == successor.hypothesis_id
    assert fallback_shadow.stage == "shadow"
    assert fallback_shadow.status == "active"
    assert fallback_exposure["assignment"] == "treatment"
    assert fallback_score == pytest.approx(fallback_baseline)
    assert fallback_manifest["authority_applied"] is False
    assert fallback_manifest["authority_reason"] == "experiment_stage_not_paper"
    assert db.execute(
        "SELECT COUNT(*) FROM learning_experiments "
        "WHERE hypothesis_id=? AND stage LIKE 'paper_%' AND ended_at IS NULL",
        (successor.hypothesis_id,),
    ).fetchone()[0] == 0

    verdict_rows = db.execute(
        "SELECT experiment_id,COUNT(*) AS n FROM learning_contribution_verdicts "
        "WHERE hypothesis_id=? GROUP BY experiment_id ORDER BY experiment_id",
        (successor.hypothesis_id,),
    ).fetchall()
    assert {row["experiment_id"]: row["n"] for row in verdict_rows} == {
        shadow.experiment_id: 1,
        successor.experiment_id: 1,
    }
    exposure_rows = db.execute(
        "SELECT experiment_id,COUNT(*) AS n FROM learning_exposures "
        "WHERE hypothesis_id=? GROUP BY experiment_id",
        (successor.hypothesis_id,),
    ).fetchall()
    assert {row["experiment_id"]: row["n"] for row in exposure_rows} == {
        shadow.experiment_id: len(shadow_exposures),
        successor.experiment_id: len(paper_exposures),
        fallback_shadow.experiment_id: 2,
    }
    assert db.execute(
        "SELECT COUNT(*) FROM learning_lifecycle_events WHERE verdict_id IN (?, ?)",
        (shadow_verdict.verdict_id, negative.verdict_id),
    ).fetchone()[0] == 2

    lifecycle_audits = [
        json.loads(line)
        for line in (runtime_home / "logs" / "audit.jsonl").read_text().splitlines()
        if line.strip()
    ]
    transitions = [
        row for row in lifecycle_audits
        if row.get("event_type") == "learning_lifecycle_transition"
    ]
    assert [(row["actor"], row["source"], row["state_change"]) for row in transitions] == [
        ("human", "dashboard", "admit_trial"),
        ("learning_lifecycle_controller", "learning_lifecycle", "refreeze"),
    ]
    assert transitions[0]["metadata"]["successor_experiment_id"] == successor.experiment_id
    assert transitions[0]["metadata"]["verdict_id"] == shadow_verdict.verdict_id
    assert transitions[1]["metadata"]["verdict_id"] == negative.verdict_id

    for table in (
        "trade_proposals",
        "risk_decisions",
        "orders",
        "fills",
        "positions",
        "paper_account_events",
    ):
        assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
