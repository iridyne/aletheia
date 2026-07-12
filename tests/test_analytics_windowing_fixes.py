"""Focused regressions for B9-analytics batch (#124, #158).

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
    """Insert closed positions at N-days-ago timestamps for equity-curve to walk."""
    from datetime import datetime, timedelta, timezone
    from aletheia.storage.models import Position
    from aletheia.storage.repositories import insert_position

    now = datetime.now(timezone.utc)
    for i, days_ago in enumerate(days_ago_list):
        closed_at = (now - timedelta(days=days_ago)).isoformat()
        opened_at = (now - timedelta(days=days_ago + 1)).isoformat()
        pos = Position(
            symbol=f"SEED{i}/USDT", market_type="usdt_futures", direction="long",
            quantity=1.0, entry_price=100.0, current_price=100.0 + pnl,
            realized_pnl=pnl, execution_mode="paper_internal",
            status="closed", opened_at=opened_at, closed_at=closed_at,
            close_category="strategy", exit_price_source="local_fill",
            exit_fill_verified=True, outcome_eligible=True,
            outcome_exclusion_reason="eligible", outcome_semantics_version="oi-v1",
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
    # Seed 10 closes spanning day-30 back to day-1 (day-30 = oldest).
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
