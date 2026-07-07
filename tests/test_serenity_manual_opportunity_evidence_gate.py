"""Focused regression for #100 — manual POST /serenity/opportunities honours the
evidence-derived assessed_tier floor.

serenity_contract §5 requires scoring/promotion tier to come from independently
assessed supporting evidence, not the opportunity author's own submission.
Before this fix the manual route copied `payload.evidence_tier` straight into
`assessed_tier` and labelled the assessment `operator`, so an operator (or a
buggy UI) could create a `disclosed`-tier opportunity with NO evidence and it
would score as `disclosed` instead of being floored to `speculative`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient


def _setup_env(tmp_path: Path, monkeypatch) -> None:
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    loader._config_cache = None

    sq.reset_db()
    sq._db_conn = None
    test_db = tmp_path / "test.db"
    conn = sqlite3.connect(str(test_db), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    sq._db_conn = conn
    monkeypatch.setattr(sq, "db_path", lambda: test_db)
    run_migrations()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_manual_opportunity_with_no_evidence_is_floored_to_speculative(
    tmp_path, monkeypatch,
):
    """#100: create a theme with NO supporting evidence, then submit a manual
    opportunity claiming evidence_tier='disclosed'. The persisted
    assessed_tier MUST be 'speculative' (the pipeline's floor for
    evidence-less claims), not the submitted 'disclosed'."""
    _setup_env(tmp_path, monkeypatch)

    from aletheia.storage.models import SerenityTheme
    from aletheia.storage.repositories import insert_serenity_theme
    from aletheia.server.app import app

    # Seed a theme with NO evidence rows.
    theme = SerenityTheme(
        name="Test Theme",
        thesis="Test thesis with no supporting evidence",
        status="active",
    )
    insert_serenity_theme(theme)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/serenity/opportunities",
            json={
                "theme_id": theme.id,
                "symbol": "BTC/USDT",
                "direction": "long",
                "thesis": "Manual submission claiming disclosed tier",
                "evidence_tier": "disclosed",  # advisory-only from now on
                "stage": "candidate",
                "average_mode": "none",
                "action_intent": "observe",
                "status": "active",
            },
        )
        assert r.status_code == 200
        opp_id = r.json()["id"]

        # Read it back and check the assessed_tier the pipeline actually stored.
        r = await client.get(f"/api/serenity/opportunities/{opp_id}?include_scores=false")
        assert r.status_code == 200
        stored = r.json()

    # The operator's submitted `evidence_tier` stays as advisory.
    assert stored["evidence_tier"] == "disclosed"
    # But `assessed_tier` — the tier the risk engine uses — MUST be
    # floored because there's no supporting evidence.
    assert stored["assessed_tier"] == "speculative", (
        "#100 fix: manual opportunity with no supporting evidence must be "
        f"floored to 'speculative'; got assessed_tier={stored['assessed_tier']!r} "
        f"(pipeline was bypassed and the operator's disclosed tier leaked in)"
    )
    # assessed_by names both the pipeline and the operator-author path so
    # audit can distinguish this from a fully automated Serenity opportunity.
    assert "pipeline" in stored["assessed_by"]
    assert "operator" in stored["assessed_by"]
