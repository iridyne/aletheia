"""Analytics endpoint index-usage benchmark (Phase 0 regression guard).

Builds a fixed fixture library (50k rows) over the funnel's target tables, then
runs EXPLAIN QUERY PLAN against the exact SQL patterns the /pipeline-funnel and
/opportunity-funnel endpoints issue. For a selective (narrow-window) query, the
optimizer should SEARCH the new dedicated ``created_at`` indexes rather than
SCAN the whole table.

The key insight: the win from Phase 1's sargable ``created_at >= ?`` predicates
+ dedicated indexes is that *selective* queries (narrow window → few rows) get a
SEARCH (O(log n + matches)) instead of a SCAN (O(n)). A wide-window query will
SCAN regardless — that's correct optimizer behavior, not a regression. So these
tests use a narrow window (selecting ~1% of rows) to prove the index is used.
"""
from __future__ import annotations

import sqlite3
import uuid

import pytest

# Fixture scale — large enough that the SQLite optimizer will prefer an index
# seek when one is usable (a tiny table always SCANs regardless).
N_ROWS = 50_000


@pytest.fixture
def migrated_db(tmp_path, monkeypatch):
    """Stand up a real file DB with schema + 50k-row fixture library."""
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    loader._config_cache = None
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    sq.reset_db()
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn
    run_migrations()
    _seed_fixture_library(conn, N_ROWS)
    yield conn
    sq.reset_db()


def _seed_fixture_library(conn: sqlite3.Connection, n: int) -> None:
    """Insert n rows spread across the funnel's target tables.

    created_at is spread across the last ~5.7 days (n * 5s) so a narrow 1h
    window selects ~1% of rows — selective enough for the optimizer to pick a
    SEARCH over a SCAN when a sargable predicate + index are present.
    """
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    rows_opps = []
    rows_pi = []
    rows_risk = []
    rows_fills = []
    for i in range(n):
        ts = now - timedelta(seconds=i * 5)  # spread over ~n*5s
        iso = ts.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        opp_id = uuid.uuid4().hex
        rows_opps.append(
            (
                opp_id,
                f"SYM{i}/USDT",
                "spot",
                "long" if i % 2 else "short",
                "high",
                "open" if i % 5 == 0 else "expired",
                iso,
            )
        )
        rows_pi.append(
            (
                uuid.uuid4().hex,
                iso,
                f"SYM{i}/USDT",
                "long",
                "spot",
                "signal_pending" if i % 3 == 0 else "skip",
                opp_id,
            )
        )
        # risk_decisions links to trade_proposals via proposal_id; we pass a
        # placeholder proposal_id (FK is not enforced without PRAGMA).
        rows_risk.append(
            (
                uuid.uuid4().hex,
                opp_id,  # proposal_id
                1 if i % 4 else 0,
                "within limits" if i % 4 else "size_cap",
                iso,
            )
        )
        # fills(id, order_id, fill_price, fill_quantity, fee, fee_currency,
        #       fee_source, filled_at)
        rows_fills.append(
            (
                uuid.uuid4().hex,
                uuid.uuid4().hex,  # order_id
                100.0 + i,          # fill_price
                0.1,                # fill_quantity
                0.01,               # fee
                "USDT",             # fee_currency
                "estimated",        # fee_source
                iso,                # filled_at
            )
        )

    # Wrap all inserts in one transaction — WAL checkpoint + fsync once at commit.
    conn.execute("BEGIN")
    conn.executemany(
        "INSERT INTO opportunities (id,symbol,market,direction_bias,confidence,"
        "status,created_at) VALUES (?,?,?,?,?,?,?)",
        rows_opps,
    )
    conn.executemany(
        "INSERT INTO pi_decisions (id,created_at,symbol,direction,market_type,"
        "action,opportunity_id) VALUES (?,?,?,?,?,?,?)",
        rows_pi,
    )
    conn.executemany(
        "INSERT INTO risk_decisions (id,proposal_id,approved,message,created_at) "
        "VALUES (?,?,?,?,?)",
        rows_risk,
    )
    conn.executemany(
        "INSERT INTO fills (id,order_id,fill_price,fill_quantity,fee,fee_currency,"
        "fee_source,filled_at) VALUES (?,?,?,?,?,?,?,?)",
        rows_fills,
    )
    conn.execute("COMMIT")


def _plan(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> str:
    """Return the EXPLAIN QUERY PLAN output as a single lowercased string."""
    rows = conn.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
    return " ".join(str(r[3]).lower() for r in rows)


def _search_index_on(plan: str, table: str) -> bool:
    """True if the plan has a SEARCH on the given table (selective index lookup).

    This is the key metric for selective queries: a SEARCH means the optimizer
    uses the index to seek to the start of the range and scan only matching
    rows — O(log n + matches) vs O(n) for a SCAN.

    Matches both full-name and single-alias forms:
      - 'search opportunities using index ...'
      - 'search o using index ...'  (when the query aliases the table)
      - 'search table opportunities using index ...'"""
    return (
        f"search {table} using" in plan
        or f"search table {table} using" in plan
        or _search_alias(plan, table)
    )


def _search_alias(plan: str, table: str) -> bool:
    """Detect 'search <alias> using index ...' where <alias> is a single-char
    or short alias that SQLite assigns when the query uses one. We can't know
    the alias up-front, so we look for any 'search <token> using index' whose
    referenced index belongs to the target table."""
    import re
    # Find all 'search <name> using index <idx>' or 'search <name> using covering index <idx>'
    for m in re.finditer(r"search\s+(\w+)\s+using\s+(?:covering\s+)?index\s+(\S+)", plan):
        idx_name = m.group(2)
        # Check if this index belongs to the target table via sqlite_master.
        # We can't query sqlite_master here (no conn), so we match by convention:
        # our new indexes are prefixed idx_<table>_*
        if idx_name.startswith(f"idx_{table}_"):
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# pipeline-funnel SQL patterns (from get_pipeline_funnel)
#
# These use a narrow window (last 1h ≈ 720 rows out of 50k ≈ 1.4%) to prove
# the optimizer picks a SEARCH on the new dedicated indexes.
# ─────────────────────────────────────────────────────────────────────────────

def _narrow_cutoff():
    """Return a UTC ISO cutoff ~1h ago — selects ~1.4% of the 50k fixture."""
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )


def test_pipeline_funnel_opportunities_uses_index(migrated_db):
    """The opportunities COUNT in /pipeline-funnel should SEARCH for a narrow window."""
    conn = migrated_db
    plan = _plan(
        conn,
        "SELECT COUNT(*) FROM opportunities WHERE created_at >= ?",
        (_narrow_cutoff(),),
    )
    assert _search_index_on(plan, "opportunities"), (
        f"opportunities query should SEARCH an index for a narrow window, got plan: {plan}"
    )


def test_pipeline_funnel_pi_decisions_uses_index(migrated_db):
    """The pi_decisions COUNTs in /pipeline-funnel should SEARCH for a narrow window."""
    conn = migrated_db
    plan = _plan(
        conn,
        "SELECT COUNT(*) FROM pi_decisions WHERE action='signal_pending' "
        "AND created_at >= ?",
        (_narrow_cutoff(),),
    )
    assert _search_index_on(plan, "pi_decisions"), (
        f"pi_decisions query should SEARCH an index for a narrow window, got plan: {plan}"
    )


def test_pipeline_funnel_risk_decisions_uses_index(migrated_db):
    """The risk_decisions aggregate in /pipeline-funnel should SEARCH for a narrow window."""
    conn = migrated_db
    plan = _plan(
        conn,
        "SELECT approved, COUNT(*) n FROM risk_decisions "
        "WHERE created_at >= ? GROUP BY approved",
        (_narrow_cutoff(),),
    )
    assert _search_index_on(plan, "risk_decisions"), (
        f"risk_decisions query should SEARCH an index for a narrow window, got plan: {plan}"
    )


def test_pipeline_funnel_fills_uses_index(migrated_db):
    """The fills COUNT in /pipeline-funnel should SEARCH for a narrow window."""
    conn = migrated_db
    plan = _plan(
        conn,
        "SELECT COUNT(*) FROM fills WHERE filled_at >= ?",
        (_narrow_cutoff(),),
    )
    assert _search_index_on(plan, "fills"), (
        f"fills query should SEARCH an index for a narrow window, got plan: {plan}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# opportunity-funnel cohort SQL patterns (from _funnel_cohort_inputs)
# ─────────────────────────────────────────────────────────────────────────────

def test_opportunity_funnel_cohort_uses_index(migrated_db):
    """The opportunities cohort aggregates should SEARCH for a narrow window."""
    conn = migrated_db
    now = _narrow_cutoff()
    # window_end far in the future so the upper bound doesn't filter.
    end = "2030-01-01T00:00:00+00:00"
    plan = _plan(
        conn,
        "SELECT COUNT(*), SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) "
        "FROM opportunities "
        "WHERE created_at >= ? AND created_at <= ?",
        (now, end),
    )
    assert _search_index_on(plan, "opportunities"), (
        f"opportunity-funnel cohort query should SEARCH, got plan: {plan}"
    )


def test_opportunity_funnel_trade_proposals_uses_index(migrated_db):
    """The trade_proposals JOIN cohort should SEARCH opportunities for a narrow window."""
    conn = migrated_db
    now = _narrow_cutoff()
    end = "2030-01-01T00:00:00+00:00"
    plan = _plan(
        conn,
        "SELECT COUNT(DISTINCT tp.opportunity_id) FROM trade_proposals tp "
        "JOIN opportunities o ON o.id=tp.opportunity_id "
        "WHERE o.created_at >= ? AND o.created_at <= ? "
        "AND tp.created_at <= ?",
        (now, end, end),
    )
    assert _search_index_on(plan, "opportunities"), (
        f"trade_proposals JOIN should SEARCH opportunities, got plan: {plan}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Negative control: julianday() wrapping defeats index SEEK even for a narrow
# window. This documents the regression the sargable predicate avoids.
# ─────────────────────────────────────────────────────────────────────────────

def test_julianday_wrapping_defeats_seek(migrated_db):
    """With julianday() wrapping, a narrow-window query cannot SEARCH → SCAN.

    This is the negative control that proves the fixture is large enough for
    the optimizer to care, and locks in the 'why' for future readers. The
    endpoint no longer issues this form after Phase 1."""
    conn = migrated_db
    plan = _plan(
        conn,
        "SELECT COUNT(*) FROM opportunities "
        "WHERE julianday(created_at) >= julianday(?)",
        (_narrow_cutoff(),),
    )
    assert not _search_index_on(plan, "opportunities"), (
        f"julianday-wrapped query should NOT SEARCH (negative control), got: {plan}"
    )
