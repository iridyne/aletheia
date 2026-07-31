"""Phase 2B-1: budget writer + would-block observation calculator.

Covers the pure-function surface of Phase 2B without engaging the hook
that Phase 2B-2 will add to `complete_with_fallback`. Tests import from
`aletheia.economics.budgets` directly.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner

from aletheia.economics.budgets import (
    add_budget,
    check_would_block,
    list_budgets,
    set_budget_enabled,
)
from aletheia.economics.schema import ensure_economics_schema
from aletheia.main import app
from aletheia.storage.sqlite import get_db


runner = CliRunner()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_cost(
    *,
    event_id: str,
    cost: float,
    run_id: str | None = None,
    provider: str = "test",
    logical_agent: str | None = None,
    started_at: str | None = None,
) -> None:
    ensure_economics_schema()
    now = started_at or _now_iso()
    metadata = {}
    if logical_agent:
        metadata["logical_agent"] = logical_agent
    get_db().execute(
        """INSERT INTO runtime_cost_events (
            id,event_type,run_id,call_id,provider,model_or_tool,attempt_index,status,
            started_at,finished_at,usage_json,estimated_cost_usd,currency,cost_status,
            metadata_json,created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            event_id, "llm", run_id, event_id, provider, "model", 0, "success",
            now, now, "{}", cost, "USD", "known",
            json.dumps(metadata), now,
        ),
    )


# ── add_budget ───────────────────────────────────────────────────────────────


def test_add_budget_creates_row_with_deterministic_id():
    a = add_budget(scope="system", subject=None, usd_cap=100.0, window="daily")
    b = add_budget(scope="system", subject=None, usd_cap=100.0, window="daily")
    assert a["id"] == b["id"]
    assert a["enabled"] == 1
    assert a["source"] == "cli:budget-set"


def test_add_budget_rejects_invalid_scope():
    with pytest.raises(ValueError):
        add_budget(scope="not_a_scope", subject="x", usd_cap=10.0, window="daily")


def test_add_budget_rejects_invalid_window():
    with pytest.raises(ValueError):
        add_budget(scope="system", subject=None, usd_cap=10.0, window="hourly")


def test_add_budget_requires_subject_for_non_system_scope():
    with pytest.raises(ValueError):
        add_budget(scope="logical_agent", subject=None, usd_cap=10.0, window="daily")


def test_add_budget_rejects_non_positive_cap():
    with pytest.raises(ValueError):
        add_budget(scope="system", subject=None, usd_cap=0, window="daily")
    with pytest.raises(ValueError):
        add_budget(scope="system", subject=None, usd_cap=-5.0, window="daily")


def test_add_budget_accepts_disabled_flag():
    row = add_budget(
        scope="system", subject=None, usd_cap=1.0, window="daily", enabled=False
    )
    assert row["enabled"] == 0


# ── list_budgets ─────────────────────────────────────────────────────────────


def test_list_budgets_returns_empty_when_no_policies_declared():
    assert list_budgets() == []


def test_list_budgets_filters_by_scope():
    add_budget(scope="system", subject=None, usd_cap=1.0, window="daily")
    add_budget(scope="logical_agent", subject="pi_trading_decision", usd_cap=2.0, window="daily")

    system_rows = list_budgets(scope="system")
    agent_rows = list_budgets(scope="logical_agent")
    assert len(system_rows) == 1 and system_rows[0]["scope"] == "system"
    assert len(agent_rows) == 1 and agent_rows[0]["subject"] == "pi_trading_decision"


def test_list_budgets_enabled_only_hides_disabled():
    add_budget(
        scope="system", subject=None, usd_cap=1.0, window="daily", enabled=False
    )
    assert list_budgets() != []
    assert list_budgets(enabled_only=True) == []


# ── set_budget_enabled ───────────────────────────────────────────────────────


def test_set_budget_enabled_toggles_flag():
    row = add_budget(scope="system", subject=None, usd_cap=1.0, window="daily")
    disabled = set_budget_enabled(budget_id=row["id"], enabled=False)
    assert disabled is not None and disabled["enabled"] == 0
    enabled = set_budget_enabled(budget_id=row["id"], enabled=True)
    assert enabled is not None and enabled["enabled"] == 1


def test_set_budget_enabled_returns_none_for_unknown_id():
    assert set_budget_enabled(budget_id="budget_missing", enabled=True) is None


# ── check_would_block: pure function ─────────────────────────────────────────


def test_check_would_block_returns_empty_when_no_budgets():
    obs = check_would_block(estimated_cost_usd=100.0, logical_agent="market_observer")
    assert obs == []


def test_check_would_block_returns_empty_when_within_cap():
    add_budget(scope="system", subject=None, usd_cap=10.0, window="daily")
    _seed_cost(event_id="prior", cost=2.0)

    obs = check_would_block(estimated_cost_usd=1.0)
    assert obs == []


def test_check_would_block_returns_observation_when_over_cap():
    add_budget(scope="system", subject=None, usd_cap=5.0, window="daily")
    _seed_cost(event_id="prior", cost=4.5)

    obs = check_would_block(estimated_cost_usd=1.0)
    assert len(obs) == 1
    entry = obs[0]
    assert entry["scope"] == "system"
    assert entry["usd_cap"] == pytest.approx(5.0)
    assert entry["spent_before_call_usd"] == pytest.approx(4.5)
    assert entry["projected_after_call_usd"] == pytest.approx(5.5)
    assert entry["over_by_usd"] == pytest.approx(0.5)


def test_check_would_block_scopes_by_logical_agent():
    add_budget(
        scope="logical_agent",
        subject="market_observer",
        usd_cap=1.0,
        window="daily",
    )
    _seed_cost(
        event_id="mo-1", cost=0.8, logical_agent="market_observer"
    )
    _seed_cost(
        event_id="other-1", cost=100.0, logical_agent="dreaming_agent"
    )

    obs = check_would_block(estimated_cost_usd=0.5, logical_agent="market_observer")
    assert len(obs) == 1
    assert obs[0]["subject"] == "market_observer"

    obs_other = check_would_block(estimated_cost_usd=0.5, logical_agent="dreaming_agent")
    assert obs_other == []


def test_check_would_block_scopes_by_agent_run():
    add_budget(scope="agent_run", subject="agent_run_ALPHA", usd_cap=0.5, window="daily")
    _seed_cost(event_id="alpha-1", cost=0.4, run_id="agent_run_ALPHA")
    _seed_cost(event_id="beta-1", cost=100.0, run_id="agent_run_BETA")

    obs = check_would_block(estimated_cost_usd=0.2, run_id="agent_run_ALPHA")
    assert len(obs) == 1
    assert obs[0]["subject"] == "agent_run_ALPHA"


def test_check_would_block_scopes_by_provider():
    add_budget(scope="provider", subject="anthropic_native", usd_cap=1.0, window="daily")
    _seed_cost(event_id="anth-1", cost=0.9, provider="anthropic_native")
    _seed_cost(event_id="oai-1", cost=100.0, provider="openai_native")

    obs = check_would_block(estimated_cost_usd=0.2, provider="anthropic_native")
    assert len(obs) == 1
    obs_other = check_would_block(estimated_cost_usd=0.2, provider="openai_native")
    assert obs_other == []


def test_check_would_block_ignores_disabled_policies():
    row = add_budget(
        scope="system",
        subject=None,
        usd_cap=1.0,
        window="daily",
        enabled=False,
    )
    _seed_cost(event_id="p1", cost=100.0)

    obs = check_would_block(estimated_cost_usd=100.0)
    assert obs == []

    # Enable and re-check
    set_budget_enabled(budget_id=row["id"], enabled=True)
    obs2 = check_would_block(estimated_cost_usd=100.0)
    assert len(obs2) == 1


def test_check_would_block_daily_window_ignores_older_spend():
    add_budget(scope="system", subject=None, usd_cap=1.0, window="daily")
    two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    _seed_cost(event_id="old", cost=100.0, started_at=two_days_ago)

    # Old spend outside the 24h window is not counted.
    obs = check_would_block(estimated_cost_usd=0.5)
    assert obs == []


def test_check_would_block_weekly_window_counts_prior_days():
    add_budget(scope="system", subject=None, usd_cap=1.0, window="weekly")
    three_days_ago = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    _seed_cost(event_id="within-week", cost=0.9, started_at=three_days_ago)

    obs = check_would_block(estimated_cost_usd=0.5)
    assert len(obs) == 1
    assert obs[0]["window"] == "weekly"


def test_check_would_block_ignores_unknown_cost_rows():
    add_budget(scope="system", subject=None, usd_cap=1.0, window="daily")
    # Insert an unknown-cost row (should NOT count toward the sum)
    ensure_economics_schema()
    now = _now_iso()
    get_db().execute(
        """INSERT INTO runtime_cost_events (
            id,event_type,call_id,provider,model_or_tool,attempt_index,status,
            started_at,finished_at,usage_json,estimated_cost_usd,currency,cost_status,
            metadata_json,created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("unk", "llm", "call-unk", "test", "model", 0, "success",
         now, now, "{}", None, "USD", "unknown", "{}", now),
    )

    obs = check_would_block(estimated_cost_usd=0.5)
    assert obs == []


def test_check_would_block_rejects_non_positive_estimate():
    add_budget(scope="system", subject=None, usd_cap=1.0, window="daily")
    _seed_cost(event_id="p1", cost=100.0)
    assert check_would_block(estimated_cost_usd=0) == []
    assert check_would_block(estimated_cost_usd=-1.0) == []


# ── CLI ──────────────────────────────────────────────────────────────────────


def test_cli_budget_set_and_list_roundtrip():
    result = runner.invoke(
        app,
        ["audit", "budget-set", "--scope", "system", "--usd-cap", "12.34",
         "--window", "daily"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["scope"] == "system"
    assert payload["usd_cap"] == 12.34
    assert payload["enabled"] == 1

    listed = runner.invoke(app, ["audit", "budget-list", "--json"])
    assert listed.exit_code == 0, listed.output
    rows = json.loads(listed.output)
    assert len(rows) == 1 and rows[0]["id"] == payload["id"]


def test_cli_budget_set_requires_subject_for_scoped_policy():
    result = runner.invoke(
        app,
        ["audit", "budget-set", "--scope", "logical_agent", "--usd-cap", "1.0",
         "--window", "daily"],
    )
    assert result.exit_code != 0


def test_cli_budget_disable_and_enable():
    created = runner.invoke(
        app,
        ["audit", "budget-set", "--scope", "system", "--usd-cap", "5.0",
         "--window", "daily"],
    )
    budget_id = json.loads(created.output)["id"]

    disabled = runner.invoke(app, ["audit", "budget-disable", budget_id])
    assert disabled.exit_code == 0
    assert json.loads(disabled.output)["enabled"] == 0

    enabled = runner.invoke(app, ["audit", "budget-enable", budget_id])
    assert enabled.exit_code == 0
    assert json.loads(enabled.output)["enabled"] == 1


def test_cli_budget_disable_unknown_id_returns_error():
    result = runner.invoke(app, ["audit", "budget-disable", "budget_nope"])
    assert result.exit_code == 1
    assert "Unknown budget policy" in result.output


def test_cli_budget_list_empty():
    result = runner.invoke(app, ["audit", "budget-list"])
    assert result.exit_code == 0
    assert "No budget policies declared" in result.output
