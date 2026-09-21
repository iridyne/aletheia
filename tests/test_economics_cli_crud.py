"""End-to-end CLI coverage for the read + delete surface completing CRUD on
every mutable-facing tokenomics data source.

Existing coverage:
    - public-catalog pricing sync (model and provider-native tool rates)
    - budget-set / -list / -disable / -enable (via test_economics_budgets)
    - costs (compute, via test_economic_cli)

This file locks the missing reads + delete:
    - pricing-list                     (R for immutable catalog snapshots)
    - budget-remove                    (D for mutable policy table)
    - viability-list, viability-show   (R for persisted reports)

Plus the underlying helper functions.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from typer.testing import CliRunner

from aletheia.economics.budgets import add_budget, remove_budget
from aletheia.economics.schema import ensure_economics_schema
from aletheia.economics.tracing import (
    add_catalog_pricing_snapshot,
    list_pricing_snapshots,
)
from aletheia.economics.historical_viability import (
    get_viability_report,
    list_viability_reports,
)
from aletheia.main import app
from aletheia.storage.sqlite import get_db


runner = CliRunner()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_viability_report(*, report_id: str, scope: str = "system") -> None:
    ensure_economics_schema()
    now = _now()
    get_db().execute(
        """INSERT INTO viability_reports (
            id,dedup_key,scope,window_start,window_end,execution_mode,
            metric_version,net_trading_result_usd,deployment_cost_usd,
            viability_amount_usd,verdict,cost_completeness,sample_count,
            exclusion_counts_json,assumptions_json,reason,generated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            report_id, f"dedup-{report_id}", scope, now, now, "paper_internal",
            "test", 1.0, 0.5, 0.5, "positive", "complete", 1, "{}", "[]",
            "unit test", now,
        ),
    )


# ── list_pricing_snapshots helper ────────────────────────────────────────────


def test_list_pricing_snapshots_empty():
    ensure_economics_schema()
    assert list_pricing_snapshots() == []


def test_list_pricing_snapshots_returns_seeded_rows():
    add_catalog_pricing_snapshot(
        provider="p1", model="m1",
        input_usd_per_million=1.0, output_usd_per_million=2.0,
        web_search_usd_per_request=0.01,
        effective_from="2020-01-01T00:00:00+00:00",
        source="litellm_public_median:test", version="catalog-model-family-test",
    )
    add_catalog_pricing_snapshot(
        provider="p2", model="m2",
        input_usd_per_million=3.0, output_usd_per_million=4.0,
        effective_from="2020-01-01T00:00:00+00:00",
        source="litellm_public_median:test", version="catalog-model-family-test",
    )

    all_rows = list_pricing_snapshots()
    assert len(all_rows) == 2

    only_p1 = list_pricing_snapshots(provider="p1")
    assert len(only_p1) == 1 and only_p1[0]["provider"] == "p1"
    assert only_p1[0]["web_search_usd_per_request"] == 0.01

    only_m2 = list_pricing_snapshots(model="m2")
    assert len(only_m2) == 1 and only_m2[0]["model"] == "m2"


# ── remove_budget helper ─────────────────────────────────────────────────────


def test_remove_budget_deletes_existing_row():
    ensure_economics_schema()
    row = add_budget(scope="system", subject=None, usd_cap=1.0, window="daily")
    deleted = remove_budget(budget_id=row["id"])
    assert deleted is not None
    assert deleted["id"] == row["id"]

    survivors = get_db().execute(
        "SELECT COUNT(*) FROM economics_budgets WHERE id=?", (row["id"],)
    ).fetchone()[0]
    assert survivors == 0


def test_remove_budget_unknown_returns_none():
    ensure_economics_schema()
    assert remove_budget(budget_id="budget_missing") is None


def test_remove_budget_preserves_historic_observations():
    """Observations already logged in metadata_json.$.would_block keep their reference."""
    ensure_economics_schema()
    row = add_budget(scope="system", subject=None, usd_cap=1.0, window="daily")

    now = _now()
    observations = [{"policy_id": row["id"], "scope": "system", "over_by_usd": 0.5}]
    get_db().execute(
        """INSERT INTO runtime_cost_events (
            id,event_type,call_id,provider,model_or_tool,attempt_index,status,
            started_at,finished_at,usage_json,estimated_cost_usd,currency,cost_status,
            metadata_json,created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "ev1", "llm", "call1", "test", "model", 0, "success",
            now, now, "{}", 0.1, "USD", "known",
            json.dumps({"would_block": observations}), now,
        ),
    )

    remove_budget(budget_id=row["id"])

    surviving = get_db().execute(
        "SELECT metadata_json FROM runtime_cost_events WHERE id='ev1'"
    ).fetchone()
    metadata = json.loads(surviving[0])
    assert metadata["would_block"][0]["policy_id"] == row["id"]


# ── list_viability_reports + get_viability_report helpers ────────────────────


def test_list_viability_reports_empty():
    ensure_economics_schema()
    assert list_viability_reports() == []


def test_list_viability_reports_scope_and_pagination():
    for i in range(3):
        _seed_viability_report(report_id=f"sys-{i}", scope="system")
    _seed_viability_report(report_id="op-0", scope="operator")

    all_rows = list_viability_reports(limit=10)
    assert len(all_rows) == 4

    system_only = list_viability_reports(scope="system")
    assert len(system_only) == 3

    with pytest.raises(ValueError):
        list_viability_reports(scope="not-a-scope")

    page = list_viability_reports(limit=2, offset=1)
    assert len(page) == 2


def test_get_viability_report_returns_row_or_none():
    ensure_economics_schema()
    _seed_viability_report(report_id="rep-1")
    db = get_db()
    original = dict(db.execute("SELECT * FROM viability_reports WHERE id='rep-1'").fetchone())
    before = db.total_changes
    assert get_viability_report("rep-1") == original
    assert get_viability_report("nope") is None
    assert db.total_changes == before


@pytest.mark.parametrize("command", ["viability-list", "viability-show"])
def test_archived_viability_cli_does_not_initialize_current_economics(command):
    """An archive read must not perform a pending current-ledger backfill."""
    from aletheia.economics.schema import _TOOL_BACKFILL_KEY

    _seed_viability_report(report_id="archived")
    db = get_db()
    db.execute("DELETE FROM economics_settings WHERE key=?", (_TOOL_BACKFILL_KEY,))
    before = db.total_changes
    args = ["audit", command]
    if command == "viability-show":
        args.append("archived")
    result = runner.invoke(app, [*args, "--json"])
    assert result.exit_code == 0, result.output
    assert "archived" in result.output
    assert db.total_changes == before
    assert db.execute(
        "SELECT 1 FROM economics_settings WHERE key=?", (_TOOL_BACKFILL_KEY,),
    ).fetchone() is None


@pytest.mark.parametrize("empty_database", [False, True])
def test_absent_viability_history_does_not_create_database_or_schema(tmp_path, empty_database):
    import os
    from pathlib import Path
    import subprocess
    import sys

    script = f"empty_database = {empty_database!r}\n" + '''
import sqlite3
from aletheia.storage.sqlite import db_path
from aletheia.economics.historical_viability import list_viability_reports, get_viability_report
from aletheia.server.routes.economics import viability_reports
path = db_path()
if empty_database:
    path.parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(path).close()
before = path.read_bytes() if path.exists() else None
assert list_viability_reports() == []
assert get_viability_report("missing") is None
assert viability_reports(scope=None, limit=20, offset=0) == {
    "reports": [], "limit": 20, "offset": 0, "total": 0}
assert (path.read_bytes() if path.exists() else None) == before
'''
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "ALETHEIA_DATA_DIR": str(tmp_path)},
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr


# ── CLI: catalog pricing is read-only ────────────────────────────────────────


def test_cli_has_no_manual_model_pricing_writer():
    result = runner.invoke(
        app,
        [
            "audit", "pricing-set", "--provider", "p", "--model", "m",
            "--input", "1", "--output", "2",
        ],
    )
    assert result.exit_code != 0
    assert "No such command" in result.output


def test_cli_pricing_list_empty():
    result = runner.invoke(app, ["audit", "pricing-list"])
    assert result.exit_code == 0
    assert "No pricing snapshots recorded" in result.output


def test_cli_pricing_list_json_and_filter():
    add_catalog_pricing_snapshot(
        provider="anthropic_native", model="claude-x",
        input_usd_per_million=3.0, output_usd_per_million=15.0,
        effective_from="2020-01-01T00:00:00+00:00",
        source="litellm_public_median:test", version="catalog-model-family-test",
    )
    add_catalog_pricing_snapshot(
        provider="openai_native", model="gpt-5",
        input_usd_per_million=1.0, output_usd_per_million=4.0,
        effective_from="2020-01-01T00:00:00+00:00",
        source="litellm_public_median:test", version="catalog-model-family-test",
    )

    text_all = runner.invoke(app, ["audit", "pricing-list"])
    assert text_all.exit_code == 0
    assert "anthropic_native" in text_all.output
    assert "openai_native" in text_all.output

    json_filtered = runner.invoke(
        app, ["audit", "pricing-list", "--json", "--provider", "openai_native"]
    )
    assert json_filtered.exit_code == 0
    rows = json.loads(json_filtered.output)
    assert len(rows) == 1 and rows[0]["provider"] == "openai_native"


def test_cli_has_no_manual_tool_pricing_writer_or_profile_list():
    assert runner.invoke(app, ["audit", "tool-pricing-set"]).exit_code != 0
    assert runner.invoke(app, ["audit", "tool-pricing-list"]).exit_code != 0


# ── CLI: budget-remove ───────────────────────────────────────────────────────


def test_cli_budget_remove_requires_confirmation():
    row = add_budget(scope="system", subject=None, usd_cap=1.0, window="daily")
    # Prompt appears — declining aborts with exit 1
    result = runner.invoke(app, ["audit", "budget-remove", row["id"]], input="n\n")
    assert result.exit_code == 1
    assert "Aborted" in result.output


def test_cli_budget_remove_force_skips_prompt_and_deletes():
    row = add_budget(scope="system", subject=None, usd_cap=1.0, window="daily")
    result = runner.invoke(app, ["audit", "budget-remove", row["id"], "--force"])
    assert result.exit_code == 0
    deleted = json.loads(result.output)
    assert deleted["id"] == row["id"]

    survivors = get_db().execute(
        "SELECT COUNT(*) FROM economics_budgets WHERE id=?", (row["id"],)
    ).fetchone()[0]
    assert survivors == 0


def test_cli_budget_remove_unknown_id_exits_1():
    result = runner.invoke(app, ["audit", "budget-remove", "budget_missing", "--force"])
    assert result.exit_code == 1
    assert "Unknown budget policy" in result.output


# ── CLI: viability-list ──────────────────────────────────────────────────────


def test_cli_viability_list_empty():
    result = runner.invoke(app, ["audit", "viability-list"])
    assert result.exit_code == 0
    assert "No viability reports persisted" in result.output


def test_cli_viability_list_scope_filter_and_json():
    _seed_viability_report(report_id="sys-1", scope="system")
    _seed_viability_report(report_id="op-1", scope="operator")

    all_rows = runner.invoke(app, ["audit", "viability-list", "--json"])
    assert all_rows.exit_code == 0
    assert len(json.loads(all_rows.output)) == 2

    op_only = runner.invoke(app, ["audit", "viability-list", "--json", "--scope", "operator"])
    assert op_only.exit_code == 0
    parsed = json.loads(op_only.output)
    assert len(parsed) == 1 and parsed[0]["scope"] == "operator"


# ── CLI: viability-show ──────────────────────────────────────────────────────


def test_cli_viability_show_unknown_exits_1():
    result = runner.invoke(app, ["audit", "viability-show", "rep_missing"])
    assert result.exit_code == 1
    assert "Unknown viability report" in result.output


def test_cli_viability_show_json_returns_row():
    _seed_viability_report(report_id="rep-2")
    result = runner.invoke(app, ["audit", "viability-show", "rep-2", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["id"] == "rep-2"
    assert payload["scope"] == "system"


def test_cli_viability_show_text_renders_report():
    _seed_viability_report(report_id="rep-3")
    result = runner.invoke(app, ["audit", "viability-show", "rep-3"])
    assert result.exit_code == 0
    # render_viability produces a multi-line human-readable block
    assert len(result.output.strip().splitlines()) > 1
