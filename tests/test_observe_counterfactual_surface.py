from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from unittest.mock import patch

from typer.testing import CliRunner


def _record_ranker_candidate(
    *,
    key: str,
    evaluated_at: datetime,
    decision: str,
    confidence: str,
    net_return_pct: float,
    minimum: str = "high",
    code_commit: str = "runtime-sha-a",
) -> str:
    from aletheia.analytics.filter_evidence import record_filter_decision
    from aletheia.storage.sqlite import get_db

    with patch(
        "aletheia.analytics.filter_evidence._boot_commit",
        return_value=code_commit,
    ):
        record_filter_decision(
            candidate_key=key,
            candidate_kind="opportunity",
            filter_name="ranker_confidence_gate",
            stage="paper_ranker",
            decision=decision,
            reason_code="ranker_confidence_test",
            symbol="BTC/USDT",
            venue="okx",
            market_type="perp",
            direction="long",
            scanner_family="mean_reversion",
            entry_price_usd=100.0,
            inputs={"confidence": confidence},
            parameters={"minimum": minimum},
            evaluated_at=evaluated_at.isoformat(),
        )
    db = get_db()
    db.execute(
        "UPDATE filter_candidate_outcomes SET status='settled', settled_at=?, "
        "net_return_pct=? WHERE outcome_candidate_key=? AND horizon='4h'",
        (evaluated_at.isoformat(), net_return_pct, key),
    )
    return str(
        db.execute(
            "SELECT filter_version FROM filter_decisions WHERE candidate_key=?",
            (key,),
        ).fetchone()[0]
    )


def _ready_ranker_replay(filter_version: str) -> dict:
    return {
        "filter_name": "ranker_confidence_gate",
        "filter_version": filter_version,
        "stage": "paper_ranker",
        "scanner_family": "mean_reversion",
        "direction": "long",
        "market_type": "perp",
        "horizon": "4h",
        "current_minimum_confidence": "high",
        "candidate_cap_reached": False,
        "min_samples": 20,
        "alternatives": [
            {
                "minimum_confidence": "medium",
                "readiness": "ready_for_controlled_experiment",
            }
        ],
    }


def test_ranker_observation_ignores_code_commit_provenance_changes():
    """Unrelated build identity must not fragment a fixed filter-version cohort."""
    from aletheia.counterfactual.filter_experiments import (
        evaluate_active_shadow_experiments,
        register_replay_ready_experiments,
    )
    from aletheia.storage.sqlite import get_db

    start = datetime(2026, 8, 18, 0, 0, tzinfo=timezone.utc)
    version = _record_ranker_candidate(
        key="opportunity:ranker-version-source",
        evaluated_at=start - timedelta(minutes=1),
        decision="passed",
        confidence="high",
        net_return_pct=0.0,
        code_commit="runtime-sha-a",
    )
    assert register_replay_ready_experiments(
        [_ready_ranker_replay(version)], now=start
    )["registered"] == 1

    for index in range(20):
        observed_at = start + timedelta(minutes=index + 1)
        runtime_sha = "runtime-sha-a" if index < 10 else "runtime-sha-b"
        assert _record_ranker_candidate(
            key=f"opportunity:ranker-control-{index}",
            evaluated_at=observed_at,
            decision="passed",
            confidence="high",
            net_return_pct=0.0,
            code_commit=runtime_sha,
        ) == version
        assert _record_ranker_candidate(
            key=f"opportunity:ranker-treatment-{index}",
            evaluated_at=observed_at,
            decision="rejected",
            confidence="medium",
            net_return_pct=0.4,
            code_commit=runtime_sha,
        ) == version

    commits = {
        row[0]
        for row in get_db().execute(
            "SELECT DISTINCT code_commit FROM filter_decisions "
            "WHERE datetime(evaluated_at) > datetime(?)",
            (start.isoformat(),),
        ).fetchall()
    }
    assert commits == {"runtime-sha-a", "runtime-sha-b"}

    summary = evaluate_active_shadow_experiments(now=start + timedelta(hours=5))
    experiment = get_db().execute(
        "SELECT * FROM filter_shadow_experiments"
    ).fetchone()
    evaluation = get_db().execute(
        "SELECT * FROM filter_shadow_evaluations ORDER BY rowid DESC LIMIT 1"
    ).fetchone()

    assert summary["invalidated"] == 0
    assert summary["ready_for_review"] == 1
    assert experiment["status"] == "ready_for_review"
    assert evaluation["control_settled_n"] == 20
    assert evaluation["treatment_settled_n"] == 20
    assert evaluation["disposition"] == "ready_for_review"


def test_observe_cli_projects_existing_filter_shadow_state():
    from aletheia.cli.observe_cmd import observe_app
    from aletheia.counterfactual.filter_experiments import (
        register_replay_ready_experiments,
    )
    from aletheia.storage.sqlite import get_db

    start = datetime(2026, 8, 18, 1, 0, tzinfo=timezone.utc)
    version = _record_ranker_candidate(
        key="opportunity:observe-cli-source",
        evaluated_at=start - timedelta(minutes=1),
        decision="passed",
        confidence="high",
        net_return_pct=0.0,
    )
    register_replay_ready_experiments([_ready_ranker_replay(version)], now=start)
    experiment_id = str(
        get_db().execute("SELECT id FROM filter_shadow_experiments").fetchone()[0]
    )

    runner = CliRunner()
    listed = runner.invoke(observe_app, ["list"])
    assert listed.exit_code == 0
    rows = json.loads(listed.stdout)
    assert any(row["id"] == experiment_id for row in rows)

    status = runner.invoke(observe_app, ["status", experiment_id])
    assert status.exit_code == 0
    row = json.loads(status.stdout)
    assert row["id"] == experiment_id
    assert row["filter_name"] == "ranker_confidence_gate"
    assert row["production_mutation_allowed"] == 0
