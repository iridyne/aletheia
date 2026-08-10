"""Current-run artifact references remain structured beside legacy output text."""

import json

import pytest


def test_end_run_persists_typed_output_artifacts_without_rewriting_outputs_created():
    from aletheia.agent_runtime.run_logger import end_run, start_run
    from aletheia.storage.models import AgentArtifactRef
    from aletheia.storage.sqlite import get_db

    run = start_run("market_observer", "scheduled", "Observe")
    outputs = ["opportunity:legacy-output"]
    end_run(
        run,
        outputs=outputs,
        artifact_refs=[
            AgentArtifactRef(artifact_type="opportunity", artifact_id="opp-1"),
            AgentArtifactRef(artifact_type="filter_decision", artifact_id="decision-1"),
            AgentArtifactRef(artifact_type="pi_decision", artifact_id="pi-1"),
        ],
    )

    assert json.loads(
        get_db().execute(
            "SELECT outputs_created FROM agent_runs WHERE id=?", (run.id,)
        ).fetchone()[0]
    ) == outputs
    assert [tuple(row) for row in get_db().execute(
        "SELECT artifact_type, artifact_id FROM agent_run_artifacts "
        "WHERE run_id=? ORDER BY artifact_type, artifact_id",
        (run.id,),
    ).fetchall()] == [
        ("filter_decision", "decision-1"),
        ("opportunity", "opp-1"),
        ("pi_decision", "pi-1"),
    ]


@pytest.mark.anyio
async def test_orchestrator_persists_registered_agent_output_artifacts(monkeypatch):
    import aletheia.agents as agents
    from aletheia.agent_runtime import orchestrator
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage.sqlite import get_db

    async def artifact_agent(**_kwargs):
        return {
            "outputs": ["opportunity:raw-output"],
            "errors": [],
            "output_artifacts": [
                {"artifact_type": "opportunity", "artifact_id": "opp-current"},
                {"artifact_type": "pi_decision", "artifact_id": "decision-current"},
            ],
        }

    monkeypatch.setitem(agents._AGENTS, "artifact_ref_test", artifact_agent)
    monkeypatch.setattr(orchestrator, "load_config", lambda: AletheiaConfig())

    result = await orchestrator.run_agent("artifact_ref_test")

    assert result["output_artifacts"] == [
        {"artifact_type": "opportunity", "artifact_id": "opp-current"},
        {"artifact_type": "pi_decision", "artifact_id": "decision-current"},
    ]
    assert [tuple(row) for row in get_db().execute(
        "SELECT artifact_type, artifact_id FROM agent_run_artifacts "
        "WHERE run_id=? ORDER BY artifact_type, artifact_id",
        (result["run_id"],),
    ).fetchall()] == [
        ("opportunity", "opp-current"),
        ("pi_decision", "decision-current"),
    ]


@pytest.mark.anyio
async def test_portfolio_agent_returns_current_snapshot_as_output_artifact(monkeypatch):
    from aletheia.agents.portfolio_agent import run_portfolio_agent
    from aletheia.agent_runtime.tool_registry import init_tool_registry
    from aletheia.portfolio import pnl
    from aletheia.storage.models import PortfolioSnapshot

    monkeypatch.setattr(
        pnl,
        "compute_and_store_snapshot",
        lambda: PortfolioSnapshot(id="snapshot-current", total_value_usdt=100.0),
    )
    init_tool_registry()

    result = await run_portfolio_agent()

    assert result["outputs"] == ["portfolio_snapshot:snapshot-current"]
    assert result["output_artifacts"] == [
        {"artifact_type": "portfolio_snapshot", "artifact_id": "snapshot-current"}
    ]
