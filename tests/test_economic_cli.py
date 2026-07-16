"""Owner-facing economic audit CLI contract."""

from __future__ import annotations

from datetime import datetime, timezone

from typer.testing import CliRunner

from aletheia.economics.schema import ensure_economics_schema
from aletheia.main import app
from aletheia.storage.sqlite import get_db


runner = CliRunner()


def test_costs_command_renders_current_summary_field_names():
    db = ensure_economics_schema()
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        """INSERT INTO runtime_cost_events (
            id,event_type,call_id,provider,model_or_tool,attempt_index,status,
            started_at,finished_at,usage_json,estimated_cost_usd,currency,
            cost_status,metadata_json,created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "cli-cost-event",
            "llm",
            "cli-cost-call",
            "test",
            "model",
            0,
            "success",
            now,
            now,
            "{}",
            0.25,
            "USD",
            "estimated",
            "{}",
            now,
        ),
    )

    result = runner.invoke(app, ["audit", "costs", "--days", "1"])

    assert result.exit_code == 0, result.output
    assert "Events: 1" in result.output
    assert "Known subtotal: $0.25000000" in result.output
    assert "llm: events=1 known=$0.25000000 unknown=0" in result.output
