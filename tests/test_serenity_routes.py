"""Functional API tests for Serenity Lens endpoints."""

import pytest
from httpx import AsyncClient, ASGITransport

from aletheia.server.app import app
from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations


@pytest.fixture
async def serenity_client(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    reset_db()


@pytest.mark.anyio
async def test_serenity_crud_flow(serenity_client):
    client = serenity_client

    create_theme = await client.post(
        "/api/serenity/themes",
        json={"name": "Photonics", "demand_wave": "AI", "tags": ["supply_chain", "hardware"]},
    )
    assert create_theme.status_code == 200
    theme_id = create_theme.json()["id"]

    themes = await client.get("/api/serenity/themes")
    assert themes.status_code == 200
    theme_rows = themes.json()
    assert len(theme_rows) == 1
    assert theme_rows[0]["id"] == theme_id
    assert theme_rows[0]["tags"] == ["supply_chain", "hardware"]

    evidence = await client.post(
        "/api/serenity/evidence",
        json={
            "theme_id": theme_id,
            "title": "Disclosed roadmap update",
            "tier": "disclosed",
            "summary": "official filing confirmed ramp",
            "source": "sec",
        },
    )
    assert evidence.status_code == 200

    opp = await client.post(
        "/api/serenity/opportunities",
        json={
            "theme_id": theme_id,
            "symbol": "NVDA",
            "direction": "long",
            "stage": "ramp",
            "average_mode": "historical_mismatch",
            "evidence_tier": "disclosed",
            "risk_flags": ["policy"],
            "confidence_score": 80,
        },
    )
    assert opp.status_code == 200
    opp_id = opp.json()["id"]

    gate = await client.post(
        f"/api/serenity/opportunities/{opp_id}/risk-gates",
        json={
            "gate_name": "liquidity",
            "status": "pass",
            "metric": "adv",
            "observed": 0.8,
            "threshold": 1.0,
            "message": "within tolerance",
        },
    )
    assert gate.status_code == 200

    list_opps = await client.get(f"/api/serenity/opportunities?theme_id={theme_id}&include_scores=true")
    assert list_opps.status_code == 200
    list_rows = list_opps.json()
    assert len(list_rows) == 1
    row = list_rows[0]
    assert row["id"] == opp_id
    assert row["serenity_score"]["opportunity_id"] == opp_id
    assert "score" in row["serenity_score"]

    theme_overview = await client.get(f"/api/serenity/themes/{theme_id}/overview")
    assert theme_overview.status_code == 200
    theme_metrics = theme_overview.json()["metrics"]
    assert theme_metrics["opportunity_count"] == 1
    assert theme_metrics["evidence_count"] == 1
    assert theme_metrics["stage_counts"]["ramp"] == 1
    assert theme_metrics["average_mode_counts"]["historical_mismatch"] == 1
    assert "pipeline_order" in theme_metrics and len(theme_metrics["pipeline_order"]) > 0

    get_one = await client.get(f"/api/serenity/opportunities/{opp_id}")
    assert get_one.status_code == 200
    fetched = get_one.json()
    assert fetched["serenity_score"]["opportunity_id"] == opp_id
    assert fetched["symbol"] == "NVDA"

    score = await client.get(f"/api/serenity/opportunities/{opp_id}/score")
    assert score.status_code == 200
    assert score.json()["opportunity_id"] == opp_id

    snapshot = await client.post(
        f"/api/serenity/opportunities/{opp_id}/replay",
        json={"as_of": "2026-06-11T00:00:00Z", "stage": "ramp", "payload": {"note": "manual capture"}},
    )
    assert snapshot.status_code == 200
    snapshot_id = snapshot.json()["id"]
    assert snapshot_id

    replay = await client.get(f"/api/serenity/opportunities/{opp_id}/replay")
    assert replay.status_code == 200
    replay_rows = replay.json()
    assert len(replay_rows) == 1
    assert replay_rows[0]["opportunity_id"] == opp_id

    research_cycle = await client.get(f"/api/serenity/opportunities/{opp_id}/research-cycle")
    assert research_cycle.status_code == 200
    cycle_json = research_cycle.json()
    assert cycle_json["opportunity"]["id"] == opp_id
    assert isinstance(cycle_json["timeline"], list)
    assert isinstance(cycle_json["risk_gates"], list)
    assert isinstance(cycle_json["post_mortems"], list)
    assert cycle_json["post_mortem_summary"]["count"] == 0
    assert "failure_bucket" in cycle_json["post_mortem_summary"]

    post_mortem = await client.post(
        f"/api/serenity/opportunities/{opp_id}/post-mortems",
        json={
            "outcome": "avoid",
            "summary": "risk changed before catalyst",
            "failure_bucket": "direction_wrong",
            "score_delta": -12,
        },
    )
    assert post_mortem.status_code == 200
    post_mortems = await client.get(f"/api/serenity/opportunities/{opp_id}/post-mortems")
    assert post_mortems.status_code == 200
    assert len(post_mortems.json()) == 1
    assert post_mortems.json()[0]["outcome"] == "avoid"

    research_cycle_after = await client.get(f"/api/serenity/opportunities/{opp_id}/research-cycle")
    assert research_cycle_after.status_code == 200
    cycle_after_json = research_cycle_after.json()
    assert cycle_after_json["post_mortem_summary"]["count"] == 1

    risk_gates = await client.get(f"/api/serenity/opportunities/{opp_id}/risk-gates")
    assert risk_gates.status_code == 200
    gate_rows = risk_gates.json()
    assert len(gate_rows) == 1
    assert gate_rows[0]["id"] == gate.json()["id"]


@pytest.mark.anyio
async def test_serenity_404_paths(serenity_client):
    client = serenity_client
    missing = "does-not-exist"

    score = await client.get(f"/api/serenity/opportunities/{missing}/score")
    assert score.status_code == 404

    gates = await client.get(f"/api/serenity/opportunities/{missing}/risk-gates")
    assert gates.status_code == 404

    replay = await client.get(f"/api/serenity/opportunities/{missing}/replay")
    assert replay.status_code == 404

    post_mortem = await client.get(f"/api/serenity/opportunities/{missing}/post-mortems")
    assert post_mortem.status_code == 404

    overview = await client.get(f"/api/serenity/themes/{missing}/overview")
    assert overview.status_code == 404

    research_cycle = await client.get(f"/api/serenity/opportunities/{missing}/research-cycle")
    assert research_cycle.status_code == 404
