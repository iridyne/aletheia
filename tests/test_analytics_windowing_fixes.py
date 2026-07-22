"""Focused regressions for analytics windowing and grouping defects.

- #124: /api/analytics/equity-curve applied `ORDER BY closed_at ASC LIMIT ?`,
        so under full_cumulative on an account with more closes than `limit`
        the query returned the OLDEST N rows — recent points were truncated
        and the equity curve froze on ancient history. Now the SQL selects
        the newest N via a DESC-limit subquery and re-sorts ASC.
- #158: trade_insights._by_exit_reason had an operator-precedence bug —
        `r["close_reason"] if "close_reason" in r.keys() else None or "unknown"`
        parses as `X if C else (None or "unknown")`, so a present-but-null
        close_reason returned None instead of "unknown". The unknown bucket's
        avg_pnl_pct then summed zero rows and divided by a nonzero count,
        returning ~0. Extracted to a real `_reason_of` helper.
- Overview funnel routes compared ISO ``T`` timestamps directly with SQLite's
        space-separated ``datetime('now', ...)`` result. Rows before the real
        cutoff on the same calendar date were therefore counted as recent.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


def _setup_env(tmp_path: Path, monkeypatch) -> None:
    """The equity-curve endpoint is a SYNC FastAPI route so it runs in a
    threadpool worker. `sq._db_conn` set here won't be reachable from that
    worker unless we also make workers open their OWN connection to the same
    file (via monkeypatched `sq.db_path`). Mirrors test_pnl_pipeline._setup."""
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
    conn.execute("PRAGMA busy_timeout=5000")
    sq._db_conn = conn
    monkeypatch.setattr(sq, "db_path", lambda: test_db)
    run_migrations()


# ---------------------------------------------------------------------------
# #124 — equity-curve returns recent points, not the oldest N
# ---------------------------------------------------------------------------


def _seed_positions(days_ago_list: list[int], pnl: float = 1.0) -> None:
    """Insert ordered post-clean-window closes for equity-curve truncation."""
    from datetime import datetime, timedelta, timezone
    from aletheia.storage.models import Position
    from aletheia.storage.repositories import insert_position
    from tests.test_outcome_closure import _insert_entry_chain

    now = datetime.fromisoformat("2026-07-12T08:00:00+00:00")
    for i, days_ago in enumerate(days_ago_list):
        closed_at = (now + timedelta(minutes=i)).isoformat()
        opened_at = (now + timedelta(minutes=i - 1)).isoformat()
        position_id = f"window-position-{i}"
        symbol = f"SEED{i}/USDT"
        opportunity_id, proposal_id, order_id = _insert_entry_chain(
            position_id=position_id,
            symbol=symbol,
            market_type="usdt_futures",
            direction="long",
            quantity=1.0,
            price=100.0,
            execution_mode="paper_internal",
        )
        pos = Position(
            id=position_id, symbol=symbol,
            market_type="usdt_futures", direction="long",
            quantity=1.0, entry_price=100.0, current_price=100.0 + pnl,
            realized_pnl=pnl, execution_mode="paper_internal",
            status="closed", opened_at=opened_at, closed_at=closed_at,
            entry_fee=0.0, exit_fee=0.0,
            funding_cost=0.0, funding_source="local_simulation",
            entry_price_source="local_fill", entry_fill_verified=True,
            close_category="strategy", exit_price_source="local_fill",
            exit_fill_verified=True, outcome_eligible=True,
            outcome_exclusion_reason="eligible", outcome_semantics_version="oi-v1",
            opportunity_id=opportunity_id, proposal_id=proposal_id,
            order_id=order_id,
        )
        insert_position(pos)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_equity_curve_returns_recent_points_when_over_limit(tmp_path, monkeypatch):
    """#124: with more closes than the LIMIT, the ASC-limit SQL used to keep
    the oldest rows — the equity curve froze. Now we keep the newest."""
    _setup_env(tmp_path, monkeypatch)
    # Seed 10 ordered closes after the versioned clean-window anchor.
    _seed_positions(days_ago_list=list(range(30, 0, -3)))  # 30, 27, 24, ..., 3

    from httpx import ASGITransport, AsyncClient
    from aletheia.server.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # limit=3 forces truncation — we should get the 3 MOST RECENT closes,
        # not the 3 oldest. All returned times are within the last 10 days.
        r = await client.get("/api/analytics/equity-curve?limit=3")

    assert r.status_code == 200
    points = r.json()["points"]
    assert len(points) == 3, f"expected 3 points; got {len(points)}"

    from datetime import datetime, timedelta, timezone
    ten_days_ago = (datetime.now(timezone.utc) - timedelta(days=11)).isoformat()
    for p in points:
        assert p["time"] > ten_days_ago, (
            f"equity-curve returned an OLD point {p['time']!r} under limit=3 — "
            f"the ASC-limit truncation regressed"
        )


# ---------------------------------------------------------------------------
# #158 — unknown-bucket avg_pnl_pct is real, not ~0
# ---------------------------------------------------------------------------


def test_by_exit_reason_unknown_bucket_avg_pnl_is_nonzero_when_pnl_nonzero():
    """#158: rows with close_reason=None used to route into "unknown" for the
    bucket but return None from the inline ternary in the avg_pnl_pct sum, so
    the loop iterated zero matching rows and avg_pnl_pct came back ~0.

    Seed two null-reason rows with a real _pnl_pct value. The unknown bucket's
    avg_pnl_pct MUST NOT be 0 — that would prove the sum still misses them.
    """
    from aletheia.analytics.trade_insights import _by_exit_reason

    # Rows shaped like list_positions output; _pnl_pct needs realized_pnl,
    # entry_price, quantity. close_reason present but None to reproduce #158.
    rows = [
        {
            "close_reason": None, "realized_pnl": 5.0, "entry_price": 100.0,
            "quantity": 1.0, "market_type": "usdt_futures",
        },
        {
            "close_reason": None, "realized_pnl": 3.0, "entry_price": 100.0,
            "quantity": 1.0, "market_type": "usdt_futures",
        },
    ]

    result = _by_exit_reason(rows)
    unknown = next((b for b in result if b["reason"] == "unknown"), None)
    assert unknown is not None, "unknown bucket should exist for null close_reason rows"
    assert unknown["trade_count"] == 2
    assert unknown["avg_pnl_pct"] != 0, (
        f"unknown bucket avg_pnl_pct must reflect the actual PnL of its rows, "
        f"not the operator-precedence trap; got {unknown['avg_pnl_pct']}"
    )


def test_by_exit_reason_named_buckets_still_work():
    """#158 corollary: the fix must not regress the (already-working) named-
    reason path. stop_loss + take_profit buckets stay honest."""
    from aletheia.analytics.trade_insights import _by_exit_reason

    rows = [
        {
            "close_reason": "stop_loss", "realized_pnl": -5.0,
            "entry_price": 100.0, "quantity": 1.0, "market_type": "usdt_futures",
        },
        {
            "close_reason": "take_profit", "realized_pnl": 10.0,
            "entry_price": 100.0, "quantity": 1.0, "market_type": "usdt_futures",
        },
    ]

    result = _by_exit_reason(rows)
    stops = next(b for b in result if b["reason"] == "stop_loss")
    tps = next(b for b in result if b["reason"] == "take_profit")

    assert stops["trade_count"] == 1
    assert stops["avg_pnl_pct"] < 0  # loss row
    assert tps["trade_count"] == 1
    assert tps["avg_pnl_pct"] > 0  # win row


# ---------------------------------------------------------------------------
# Overview analytics — ISO timestamps respect the real rolling cutoff
# ---------------------------------------------------------------------------


def _overview_window_fixture_times() -> tuple[int, str, str]:
    """Choose a cutoff near noon and an older ISO row on the same date."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    hours = now.hour - 12 if now.hour >= 12 else now.hour + 12
    if hours == 0:
        hours = 24
    cutoff = now - timedelta(hours=hours)
    old = cutoff.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    recent = (now - timedelta(minutes=5)).isoformat()
    assert old < cutoff.isoformat() < recent
    return hours, old, recent


def _insert_overview_decision(
    db, *, row_id: str, action: str, created_at: str,
    signal_source: str = "market_observer",
) -> None:
    db.execute(
        "INSERT INTO pi_decisions "
        "(id, symbol, direction, market_type, action, conviction, reasoning, "
        "opportunity_id, signal_source, created_at) "
        "VALUES (?, 'BTC/USDT', 'long', 'spot', ?, 0, 'window test', ?, ?, ?)",
        (row_id, action, f"opp-{row_id}", signal_source, created_at),
    )


def test_overview_operator_decisions_excludes_old_iso_row_on_cutoff_date(
    tmp_path, monkeypatch,
):
    from datetime import datetime, timezone

    _setup_env(tmp_path, monkeypatch)
    from aletheia.server.routes.analytics import get_operator_decisions
    from aletheia.storage.sqlite import get_db

    hours, old, recent = _overview_window_fixture_times()
    db = get_db()
    _insert_overview_decision(db, row_id="old-skip", action="skip", created_at=old)
    _insert_overview_decision(db, row_id="recent-skip", action="skip", created_at=recent)
    newest_space = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    _insert_overview_decision(
        db, row_id="newest-space-skip", action="skip", created_at=newest_space,
    )
    _insert_overview_decision(
        db, row_id="cascade-skip", action="skip", created_at=newest_space,
        signal_source="system_cascade",
    )

    result = get_operator_decisions(hours=hours, limit=50)

    assert result["counts"]["operator_skip"] == 2
    assert [item["decision_id"] for item in result["items"]] == [
        "newest-space-skip", "recent-skip",
    ]


def test_overview_funnels_share_the_same_parsed_iso_window(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    from aletheia.server.routes.analytics import (
        get_opportunity_funnel,
        get_pipeline_funnel,
    )
    from aletheia.storage.sqlite import get_db

    hours, old, recent = _overview_window_fixture_times()
    db = get_db()
    for suffix, created_at in (("old", old), ("recent", recent)):
        db.execute(
            "INSERT INTO opportunities "
            "(id, symbol, market, direction_bias, confidence, created_at, status) "
            "VALUES (?, 'BTC/USDT', 'spot', 'long', 'medium_confidence', ?, 'reviewed')",
            (f"opp-{suffix}", created_at),
        )
        _insert_overview_decision(
            db, row_id=f"pending-{suffix}", action="signal_pending", created_at=created_at,
        )
        _insert_overview_decision(
            db, row_id=f"skip-{suffix}", action="skip", created_at=created_at,
        )
        db.execute(
            "INSERT INTO trade_proposals "
            "(id, opportunity_id, symbol, market_type, direction, order_type, "
            "execution_mode, status, created_at) "
            "VALUES (?, ?, 'BTC/USDT', 'spot', 'long', 'market', "
            "'paper_internal', 'pending', ?)",
            (f"proposal-{suffix}", f"opp-{suffix}", created_at),
        )
    _insert_overview_decision(
        db, row_id="cascade-recent", action="skip", created_at=recent,
        signal_source="system_cascade",
    )

    pipeline = get_pipeline_funnel(hours=hours)
    opportunity = get_opportunity_funnel(hours=hours)

    assert pipeline["opportunities"] == 1
    assert pipeline["signal_pending"] == 1
    assert pipeline["pi_decisions"]["skip"] == 1
    assert opportunity["emitted_count"] == 1
    assert opportunity["reviewed_count"] == 1
    assert opportunity["proposed_count"] == 1


# ---------------------------------------------------------------------------
# #322 — learning-contribution verdict column fix
# ---------------------------------------------------------------------------


def _insert_verdict(conn, verdict_id: str, verdict: str, experiment_id: str = "exp-1") -> None:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO learning_contribution_verdicts "
        "(verdict_id, experiment_id, hypothesis_id, window_start, window_end, "
        "unique_treatment_n, unique_control_n, verdict, reason, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (verdict_id, experiment_id, "hyp-1", now, now, 5, 5, verdict, "test", now),
    )
    conn.commit()


def test_learning_contribution_verdict_counts_use_verdict_column(tmp_path, monkeypatch):
    """#322: route queried 'classification' (non-existent) — fixed to 'verdict'."""
    _setup_env(tmp_path, monkeypatch)
    import aletheia.storage.sqlite as sq

    conn = sq._db_conn
    _insert_verdict(conn, "v-1", "invalid")
    _insert_verdict(conn, "v-2", "invalid")
    _insert_verdict(conn, "v-3", "insufficient_data")

    from aletheia.server.routes.analytics import get_learning_contribution

    result = get_learning_contribution()

    assert result["verdicts"]["total"] == 3
    assert "error" not in result["verdicts"]
    by_verdict = {row["verdict"]: row["n"] for row in result["verdicts"]["by_verdict"]}
    assert by_verdict["invalid"] == 2
    assert by_verdict["insufficient_data"] == 1


def test_learning_contribution_verdict_db_error_surfaces_error_not_zero(tmp_path, monkeypatch):
    """#322: a schema/read failure must not silently return total=0."""
    _setup_env(tmp_path, monkeypatch)
    import aletheia.storage.sqlite as sq

    # Drop the table to simulate a schema error.
    sq._db_conn.execute("DROP TABLE learning_contribution_verdicts")
    sq._db_conn.commit()

    from aletheia.server.routes.analytics import get_learning_contribution

    result = get_learning_contribution()

    # total stays 0 because no rows were read — but an explicit error key is present
    assert result["verdicts"]["total"] == 0
    assert "error" in result["verdicts"]
    assert result["verdicts"]["error"]  # non-empty error string


# ---------------------------------------------------------------------------
# #327 — conviction_defer decisions visible in operator-decisions API
# ---------------------------------------------------------------------------


def test_conviction_defer_appears_in_operator_decisions(tmp_path, monkeypatch):
    """#327: conviction gate deferrals must appear as outcome='conviction_gate_defer'
    in get_operator_decisions. Sourced from paired filter_decisions rows:
    operator_action_assessment=passed + operator_conviction_gate=rejected."""
    import json
    from datetime import datetime, timezone

    _setup_env(tmp_path, monkeypatch)
    import aletheia.storage.sqlite as sq

    conn = sq._db_conn
    now = datetime.now(timezone.utc).isoformat()
    opp_id = "opp-conviction-327"
    candidate_key = f"opportunity:{opp_id}"
    # Paired filter_decisions rows for the same evaluation batch.
    for row_id, fname, decision, reason in (
        ("fd-action-327", "operator_action_assessment", "passed", "action_approved"),
        ("fd-gate-327", "operator_conviction_gate", "rejected", "conviction_below_minimum"),
    ):
        conn.execute(
            "INSERT INTO filter_decisions "
            "(id, dedup_key, candidate_key, candidate_kind, opportunity_id, "
            "symbol, market_type, direction, filter_name, stage, sequence_no, "
            "decision, reason_code, input_snapshot, parameter_snapshot, "
            "filter_version, evaluated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row_id, row_id, candidate_key, "opportunity", opp_id,
             "ETH/USDT", "usdt_futures", "long", fname, "paper_trader_selection", 0,
             decision, reason, json.dumps({"conviction": 42}), "{}",
             "1.0", now),
        )
    conn.commit()

    from aletheia.server.routes.analytics import get_operator_decisions

    result = get_operator_decisions(hours=24, limit=50)

    assert result["counts"]["conviction_gate_defer"] == 1
    defer_items = [it for it in result["items"] if it["outcome"] == "conviction_gate_defer"]
    assert len(defer_items) == 1
    item = defer_items[0]
    assert item["opportunity_id"] == opp_id
    assert item["symbol"] == "ETH/USDT"
    assert item["direction"] == "long"
    assert item["market_type"] == "usdt_futures"


# ---------------------------------------------------------------------------
# #330 — direction_bias_brake rejection → portfolio_guard_reject, not operator_skip
# ---------------------------------------------------------------------------


def test_direction_bias_brake_skip_classified_as_portfolio_guard_reject(tmp_path, monkeypatch):
    """#330: when direction_bias_brake rejects a trade after operator_action_assessment
    passed, the pi_decisions.action='skip' row must surface as portfolio_guard_reject,
    not operator_skip — the LLM approved the trade; a portfolio guard blocked it."""
    from datetime import datetime, timezone

    _setup_env(tmp_path, monkeypatch)
    import aletheia.storage.sqlite as sq

    conn = sq._db_conn
    now = datetime.now(timezone.utc).isoformat()

    # pi_decisions row: LLM said trade, but direction_bias_brake overrode to skip.
    conn.execute(
        "INSERT INTO pi_decisions "
        "(id, symbol, direction, market_type, action, conviction, reasoning, "
        "opportunity_id, signal_source, created_at) "
        "VALUES (?, 'ACE/USDT', 'long', 'spot', 'skip', 72, "
        "'operator approved; guard blocked', 'opp-guard-330', 'market_observer', ?)",
        ("pid-guard-330", now),
    )
    # filter_decisions row: direction_bias_brake rejected this pi_decision.
    conn.execute(
        "INSERT INTO filter_decisions "
        "(id, dedup_key, candidate_key, candidate_kind, symbol, market_type, "
        "filter_name, stage, sequence_no, decision, reason_code, "
        "input_snapshot, parameter_snapshot, filter_version, evaluated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "fd-guard-330", "fd-dedup-guard-330",
            "pi_decision:pid-guard-330", "pi_decision",
            "ACE/USDT", "spot",
            "direction_bias_brake", "system_operator", 2, "rejected",
            "proposal_direction_concentration",
            "{}", "{}", "v1", now,
        ),
    )
    # A regular skip with no guard trace — must stay operator_skip.
    conn.execute(
        "INSERT INTO pi_decisions "
        "(id, symbol, direction, market_type, action, conviction, reasoning, "
        "opportunity_id, signal_source, created_at) "
        "VALUES (?, 'BTC/USDT', 'short', 'spot', 'skip', 55, "
        "'llm decided to skip', 'opp-skip-330', 'market_observer', ?)",
        ("pid-skip-330", now),
    )
    conn.commit()

    from aletheia.server.routes.analytics import get_operator_decisions

    result = get_operator_decisions(hours=24, limit=50)

    assert result["counts"]["portfolio_guard_reject"] == 1, (
        "direction_bias_brake skip should count as portfolio_guard_reject"
    )
    assert result["counts"]["operator_skip"] == 1, (
        "regular skip must still count as operator_skip"
    )

    guard_items = [it for it in result["items"] if it["outcome"] == "portfolio_guard_reject"]
    assert len(guard_items) == 1
    g = guard_items[0]
    assert g["decision_id"] == "pid-guard-330"
    assert g["symbol"] == "ACE/USDT"
    assert g["guard_filter"] == "direction_bias_brake"
    assert g["guard_block_reason"] == "proposal_direction_concentration"

    skip_items = [it for it in result["items"] if it["outcome"] == "operator_skip"]
    assert len(skip_items) == 1
    assert skip_items[0]["decision_id"] == "pid-skip-330"
