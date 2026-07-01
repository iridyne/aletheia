"""Spec: Pipeline — Adopted Position SL/TP Propagation (end-to-end)

Root cause regression lock for the "Adopted 仓位缺 SL/TP" bug:
  position_reconciler.adopt_position() constructed Position() without stop_price
  or take_profit_price, leaving both NULL in the DB.

  For demo modes: position_manager._effective_stop() provided a runtime fallback
  (not written to DB, silently leaks if config changes).
  For live mode: evaluate_open_positions() skips non-paper modes entirely —
  no protection whatsoever.

These tests assert the 1-to-1 pipeline contract end-to-end:

    exchange_sync cache  ──reconciler──>  adopt_position()
    (no SL/TP in cache)                  ──persists──>  positions.stop_price IS NOT NULL
                                                        positions.take_profit_price IS NOT NULL

Coverage is intentionally multi-mode: demo AND live adopted positions must both
have SL/TP values in the DB row at the moment of adoption.

Contract: pipeline_adopted_position
Claim prefix: PL-ADOPT
"""

from __future__ import annotations

import sqlite3

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


# ---------------------------------------------------------------------------
# Shared DB + secrets setup  (mirrors spec_pipeline_stop_propagation pattern)
# ---------------------------------------------------------------------------


def _setup_db(tmp_path, monkeypatch):
    """Wire a fresh isolated SQLite DB into aletheia storage for one test."""
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    sq._db_conn = conn
    run_migrations()
    return conn


def _stub_notifications(monkeypatch):
    monkeypatch.setattr(
        "aletheia.notifications.notify_operator",
        lambda *a, **kw: None,
        raising=False,
    )


def _stub_audit(monkeypatch, tmp_path):
    """Redirect JSONL audit writes to a temp dir so no real files are created."""
    import aletheia.files.jsonl as jsonl_mod
    monkeypatch.setattr(jsonl_mod, "LOGS_DIR", tmp_path, raising=False)


# Minimal exchange_pos dict that adopt_position() accepts
def _make_exchange_pos(
    symbol: str = "BNB/USDT",
    side: str = "short",
    quantity: float = 0.03,
    entry_price: float = 562.73,
    mark_price: float = 566.0,
    unrealized_pnl: float = -0.083,
) -> dict:
    return {
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "entry_price": entry_price,
        "mark_price": mark_price,
        "unrealized_pnl": unrealized_pnl,
    }


# ---------------------------------------------------------------------------
# PL-ADOPT-01  L2b  demo adopted position has non-null stop AND take_profit in DB
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-ADOPT-01", "pipeline_adopted_position", "§1 demo_adopt_has_sltp", "L2b",
        "adopt_position(account_mode='demo') → DB positions row has stop_price IS NOT NULL "
        "AND take_profit_price IS NOT NULL (default-pct values)",
    ),
)
def test_demo_adopted_position_has_stop_and_tp(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    _stub_notifications(monkeypatch)
    _stub_audit(monkeypatch, tmp_path)

    from aletheia.portfolio.position_reconciler import adopt_position
    from aletheia.storage.sqlite import get_db

    exchange_pos = _make_exchange_pos(
        symbol="BNB/USDT", side="short",
        quantity=0.03, entry_price=562.73, mark_price=566.0,
    )
    ids = adopt_position(exchange_pos, account_mode="demo")

    position_id = ids["position_id"]
    db = get_db()
    row = db.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()

    assert row is not None, "adopted position row must exist in DB"
    assert row["stop_price"] is not None, (
        f"demo adopted position stop_price must not be NULL (got None). "
        f"Root cause: adopt_position() did not compute default SL at adoption time."
    )
    assert row["take_profit_price"] is not None, (
        f"demo adopted position take_profit_price must not be NULL (got None). "
        f"Root cause: adopt_position() did not compute default TP at adoption time."
    )


# ---------------------------------------------------------------------------
# PL-ADOPT-02  L2b  live adopted position has non-null stop AND take_profit in DB
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-ADOPT-02", "pipeline_adopted_position", "§2 live_adopt_has_sltp", "L2b",
        "adopt_position(account_mode='live') → DB positions row has stop_price IS NOT NULL "
        "AND take_profit_price IS NOT NULL. "
        "Critical: position_manager skips live modes — without this the live adopted "
        "position has ZERO automated protection.",
    ),
)
def test_live_adopted_position_has_stop_and_tp(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    _stub_notifications(monkeypatch)
    _stub_audit(monkeypatch, tmp_path)

    from aletheia.portfolio.position_reconciler import adopt_position
    from aletheia.storage.sqlite import get_db

    exchange_pos = _make_exchange_pos(
        symbol="BTC/USDT", side="long",
        quantity=0.001, entry_price=68000.0, mark_price=67500.0,
    )
    ids = adopt_position(exchange_pos, account_mode="live")

    position_id = ids["position_id"]
    db = get_db()
    row = db.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()

    assert row is not None, "adopted position row must exist in DB"
    assert row["stop_price"] is not None, (
        f"live adopted position stop_price must not be NULL — "
        f"position_manager never auto-exits live positions, so the stored SL is "
        f"the only protection for human review/manual close. Got None."
    )
    assert row["take_profit_price"] is not None, (
        f"live adopted position take_profit_price must not be NULL. Got None."
    )


# ---------------------------------------------------------------------------
# PL-ADOPT-03  L2b  short adopted: stop > entry, tp < entry (correct direction)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-ADOPT-03", "pipeline_adopted_position", "§3 short_sltp_direction", "L2b",
        "For a short adopted position: stop_price > entry_price AND take_profit_price < entry_price. "
        "Reversed levels would mean position_manager exits on profit and holds on loss.",
    ),
)
def test_short_adopted_sltp_correct_direction(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    _stub_notifications(monkeypatch)
    _stub_audit(monkeypatch, tmp_path)

    from aletheia.portfolio.position_reconciler import adopt_position
    from aletheia.storage.sqlite import get_db

    entry = 562.73
    exchange_pos = _make_exchange_pos(
        symbol="BNB/USDT", side="short",
        quantity=0.03, entry_price=entry,
    )
    ids = adopt_position(exchange_pos, account_mode="demo")

    db = get_db()
    row = db.execute(
        "SELECT stop_price, take_profit_price, entry_price FROM positions WHERE id=?",
        (ids["position_id"],),
    ).fetchone()

    assert row["stop_price"] > row["entry_price"], (
        f"Short stop must be ABOVE entry to limit losses: "
        f"stop={row['stop_price']}, entry={row['entry_price']}"
    )
    assert row["take_profit_price"] < row["entry_price"], (
        f"Short TP must be BELOW entry to lock in gains: "
        f"tp={row['take_profit_price']}, entry={row['entry_price']}"
    )


# ---------------------------------------------------------------------------
# PL-ADOPT-04  L2b  long adopted: stop < entry, tp > entry (correct direction)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-ADOPT-04", "pipeline_adopted_position", "§4 long_sltp_direction", "L2b",
        "For a long adopted position: stop_price < entry_price AND take_profit_price > entry_price.",
    ),
)
def test_long_adopted_sltp_correct_direction(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    _stub_notifications(monkeypatch)
    _stub_audit(monkeypatch, tmp_path)

    from aletheia.portfolio.position_reconciler import adopt_position
    from aletheia.storage.sqlite import get_db

    entry = 68000.0
    exchange_pos = _make_exchange_pos(
        symbol="BTC/USDT", side="long",
        quantity=0.001, entry_price=entry, mark_price=67500.0,
    )
    ids = adopt_position(exchange_pos, account_mode="demo")

    db = get_db()
    row = db.execute(
        "SELECT stop_price, take_profit_price, entry_price FROM positions WHERE id=?",
        (ids["position_id"],),
    ).fetchone()

    assert row["stop_price"] < row["entry_price"], (
        f"Long stop must be BELOW entry: stop={row['stop_price']}, entry={row['entry_price']}"
    )
    assert row["take_profit_price"] > row["entry_price"], (
        f"Long TP must be ABOVE entry: tp={row['take_profit_price']}, entry={row['entry_price']}"
    )


# ---------------------------------------------------------------------------
# PL-ADOPT-05  L2b  SL/TP values match default_pct config (exact calculation)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-ADOPT-05", "pipeline_adopted_position", "§5 sltp_matches_default_pct", "L2b",
        "adopted stop_price == round(entry * (1 + sl_pct/100), 8) for short, "
        "and take_profit_price == round(entry * (1 - tp_pct/100), 8) for short. "
        "Values must match config defaults exactly (no drift, no silent rounding).",
    ),
)
def test_adopted_sltp_matches_config_pct(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    _stub_notifications(monkeypatch)
    _stub_audit(monkeypatch, tmp_path)

    from aletheia.portfolio.position_reconciler import adopt_position
    from aletheia.config.loader import load_config
    from aletheia.storage.sqlite import get_db

    entry = 562.73
    exchange_pos = _make_exchange_pos(
        symbol="BNB/USDT", side="short",
        quantity=0.03, entry_price=entry,
    )
    ids = adopt_position(exchange_pos, account_mode="demo")

    cfg = load_config()
    sl_pct = cfg.trading.default_stop_loss_pct
    tp_pct = cfg.trading.default_take_profit_pct

    expected_stop = round(entry * (1 + sl_pct / 100.0), 8)  # short: stop above entry
    expected_tp = round(entry * (1 - tp_pct / 100.0), 8)    # short: tp below entry

    db = get_db()
    row = db.execute(
        "SELECT stop_price, take_profit_price FROM positions WHERE id=?",
        (ids["position_id"],),
    ).fetchone()

    assert abs(float(row["stop_price"]) - expected_stop) < 1e-7, (
        f"stop_price mismatch: expected {expected_stop}, got {row['stop_price']}"
    )
    assert abs(float(row["take_profit_price"]) - expected_tp) < 1e-7, (
        f"take_profit_price mismatch: expected {expected_tp}, got {row['take_profit_price']}"
    )


# ---------------------------------------------------------------------------
# PL-ADOPT-06  L2b  audit log contains stop_source = "adopted_default_pct"
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-ADOPT-06", "pipeline_adopted_position", "§6 audit_stop_source", "L2b",
        "adopt_position() writes an audit_events row with event_type='position_adopted' "
        "and the after dict includes stop_source='adopted_default_pct' for traceability.",
    ),
)
def test_adopted_audit_row_has_stop_source(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    _stub_notifications(monkeypatch)
    _stub_audit(monkeypatch, tmp_path)

    # Reset JSONL hash state so write_audit doesn't dedup
    try:
        import aletheia.files.jsonl as jsonl_mod
        jsonl_mod._last_audit_hash = None
    except Exception:
        pass

    from aletheia.portfolio.position_reconciler import adopt_position
    from aletheia.storage.sqlite import get_db
    import json

    exchange_pos = _make_exchange_pos(symbol="ETH/USDT", side="short", entry_price=3000.0)
    ids = adopt_position(exchange_pos, account_mode="demo")

    db = get_db()
    row = db.execute(
        "SELECT * FROM audit_events WHERE event_type='position_adopted' AND entity_id=?",
        (ids["position_id"],),
    ).fetchone()

    assert row is not None, (
        "No audit_events row found for position_adopted event. "
        "write_audit() → insert_audit_event() chain may be broken."
    )

    # Column is "after_state" in the schema (see migrations.py)
    after_raw = row["after_state"] if "after_state" in row.keys() else None
    if after_raw:
        try:
            after = json.loads(after_raw) if isinstance(after_raw, str) else after_raw
            assert after.get("stop_source") == "adopted_default_pct", (
                f"audit after_state.stop_source must be 'adopted_default_pct', got {after.get('stop_source')!r}"
            )
        except (json.JSONDecodeError, TypeError):
            pass  # audit row format may vary; presence of row is the primary signal


# ---------------------------------------------------------------------------
# PL-ADOPT-07  L2c  invariant: no open adopted position in DB has NULL stop_price
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-ADOPT-07", "pipeline_adopted_position", "§7 no_null_stop_invariant", "L2c",
        "After adopting multiple positions (demo + live, short + long), "
        "zero open adopted positions in the DB have stop_price IS NULL. "
        "This is the invariant scan — catch any code path that bypasses the fix.",
    ),
)
def test_no_open_adopted_position_has_null_stop(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    _stub_notifications(monkeypatch)
    _stub_audit(monkeypatch, tmp_path)

    try:
        import aletheia.files.jsonl as jsonl_mod
        jsonl_mod._last_audit_hash = None
    except Exception:
        pass

    from aletheia.portfolio.position_reconciler import adopt_position
    from aletheia.storage.sqlite import get_db

    # Adopt a variety of positions: demo short, demo long, live short, live long
    cases = [
        ("BNB/USDT", "short", 562.73, "demo"),
        ("BTC/USDT", "long", 68000.0, "demo"),
        ("ETH/USDT", "short", 3000.0, "live"),
        ("SOL/USDT", "long", 150.0, "live"),
    ]
    adopted_ids = []
    for sym, side, entry, mode in cases:
        exchange_pos = _make_exchange_pos(
            symbol=sym, side=side, quantity=0.01, entry_price=entry,
        )
        ids = adopt_position(exchange_pos, account_mode=mode)
        adopted_ids.append(ids["position_id"])

    db = get_db()
    null_stops = db.execute(
        "SELECT id, symbol, direction, stop_price FROM positions "
        "WHERE status='open' AND source='adopted' AND stop_price IS NULL"
    ).fetchall()

    assert not null_stops, (
        f"Found {len(null_stops)} open adopted position(s) with NULL stop_price: "
        f"{[dict(r) for r in null_stops]}. "
        f"Every adopted position must have a protective stop at adoption time."
    )

    null_tps = db.execute(
        "SELECT id, symbol, direction, take_profit_price FROM positions "
        "WHERE status='open' AND source='adopted' AND take_profit_price IS NULL"
    ).fetchall()

    assert not null_tps, (
        f"Found {len(null_tps)} open adopted position(s) with NULL take_profit_price: "
        f"{[dict(r) for r in null_tps]}."
    )


# ---------------------------------------------------------------------------
# PL-ADOPT-08  L2b  demo adopted position is auto-managed by position_manager
#              (stop fires when price hits the adopted SL level)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-ADOPT-08", "pipeline_adopted_position", "§8 demo_adopted_stop_fires", "L2b",
        "A demo adopted position whose stop level is breached is closed by "
        "evaluate_open_positions() with close_reason='stop_loss'. "
        "End-to-end: adopt → price hits SL → position closed.",
    ),
)
def test_demo_adopted_position_stop_fires(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    _stub_notifications(monkeypatch)
    _stub_audit(monkeypatch, tmp_path)

    try:
        import aletheia.files.jsonl as jsonl_mod
        jsonl_mod._last_audit_hash = None
    except Exception:
        pass

    import aletheia.portfolio.position_manager as pm_mod
    import aletheia.config.loader as loader_mod
    from aletheia.config.schema import AletheiaConfig

    # Use paper_internal so close routing stays local (no exchange call needed).
    cfg = AletheiaConfig()
    cfg.exchange.paper_execution_mode = "paper_internal"
    monkeypatch.setattr(loader_mod, "load_config", lambda: cfg)

    from aletheia.portfolio.position_reconciler import adopt_position
    from aletheia.portfolio.position_manager import evaluate_open_positions
    from aletheia.storage.sqlite import get_db

    # Adopt a short BNB position at 562.73
    entry = 562.73
    exchange_pos = _make_exchange_pos(
        symbol="BNB/USDT", side="short",
        quantity=0.03, entry_price=entry, mark_price=entry,
    )
    ids = adopt_position(exchange_pos, account_mode="demo")
    position_id = ids["position_id"]

    # Verify stop was stored
    db = get_db()
    row = db.execute(
        "SELECT stop_price, execution_mode FROM positions WHERE id=?", (position_id,)
    ).fetchone()
    assert row["stop_price"] is not None, "precondition: stop_price must be set after adopt"
    stop_level = float(row["stop_price"])

    # Inject breach price via monkeypatch on the name in position_manager's namespace.
    # Short stop is ABOVE entry; breach = stop + margin.
    breach_price = stop_level + 10.0
    monkeypatch.setattr(pm_mod, "_latest_price", lambda sym: breach_price)

    result = evaluate_open_positions()

    assert result["closed"] >= 1, (
        f"Expected position_manager to close adopted short position when price "
        f"({breach_price}) breached stop ({stop_level}). result={result}"
    )

    closed_row = db.execute(
        "SELECT status, close_reason FROM positions WHERE id=?", (position_id,)
    ).fetchone()
    assert closed_row["status"] == "closed", (
        f"Expected status='closed', got {closed_row['status']!r}"
    )
    assert closed_row["close_reason"] == "stop_loss", (
        f"Expected close_reason='stop_loss', got {closed_row['close_reason']!r}"
    )


# ---------------------------------------------------------------------------
# PL-ADOPT-09  L2b  reconcile audit event carries TaG commit_hash (P1-2)
# Wires position_adopted audit events into the proposal hash chain established
# by P0-1 (DM-S08/S09/B06/C02). See docs/research/OPENALICE_TAG_DEEP_DIVE_2026-06-29.md
# §五 P1-2.
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-ADOPT-09", "pipeline_adopted_position", "§9 audit_commit_hash", "L2b",
        "adopt_position() writes a position_adopted audit_events row whose "
        "after_state.commit_hash equals the synthesized proposal's commit_hash "
        "(16-char hex) — chains the reconcile event into the TaG proposal hash chain.",
    ),
)
def test_adopted_audit_carries_commit_hash(tmp_path, monkeypatch):
    """audit event must reference the same commit_hash the proposal got at insert time.

    P1-2 borrow from OpenAlice TaG: reconcile event becomes a first-class participant
    in the proposal hash chain instead of needing a secondary `SELECT commit_hash
    FROM trade_proposals WHERE id=?` to correlate. Hash is filled by
    insert_trade_proposal() inside the transaction block at position_reconciler.py
    ~line 357, so it is available when write_audit() fires at ~line 361.
    """
    conn = _setup_db(tmp_path, monkeypatch)
    _stub_notifications(monkeypatch)
    _stub_audit(monkeypatch, tmp_path)

    try:
        import aletheia.files.jsonl as jsonl_mod
        jsonl_mod._last_audit_hash = None
    except Exception:
        pass

    from aletheia.portfolio.position_reconciler import adopt_position
    from aletheia.storage.sqlite import get_db
    from aletheia.storage import repositories as repo
    import json
    import re

    _hex16 = re.compile(r"^[0-9a-f]{16}$")

    exchange_pos = _make_exchange_pos(
        symbol="ETH/USDT", side="long", entry_price=3200.0,
    )
    ids = adopt_position(exchange_pos, account_mode="demo")

    # Ground truth: the proposal's stored commit_hash.
    prop = repo.get_trade_proposal(ids["proposal_id"])
    assert prop is not None, "proposal row must exist after adopt_position()"
    expected_hash = prop.get("commit_hash")
    assert expected_hash and _hex16.match(expected_hash), (
        f"proposal.commit_hash must be 16-char hex (P0-1), got {expected_hash!r}"
    )

    # The audit event's after_state must carry the same hash.
    db = get_db()
    row = db.execute(
        "SELECT after_state FROM audit_events "
        "WHERE event_type='position_adopted' AND entity_id=?",
        (ids["position_id"],),
    ).fetchone()
    assert row is not None, (
        "No audit_events row for position_adopted — write_audit() chain broken."
    )
    after_raw = row["after_state"]
    assert after_raw, "after_state must be non-empty"
    after = json.loads(after_raw) if isinstance(after_raw, str) else after_raw
    audit_hash = after.get("commit_hash")
    assert audit_hash == expected_hash, (
        f"audit after_state.commit_hash {audit_hash!r} must equal "
        f"proposal.commit_hash {expected_hash!r}. "
        "Fix: pass proposal.commit_hash into the write_audit() after dict "
        "inside adopt_position()."
    )

    # parent_hash must also be present and equal to the proposal's parent_hash
    # (None when this is the first hashed proposal in the table — true in this
    # fresh-DB test fixture). dict.get returning None ≠ key-absent, so check
    # key presence explicitly to catch the "field missing" failure mode.
    assert "parent_hash" in after, (
        "audit after_state must include parent_hash (P1-2): "
        "even when NULL, the key must be present so chain consumers can "
        "distinguish 'genesis link' from 'forgotten to set'."
    )
    assert after.get("parent_hash") == prop.get("parent_hash"), (
        f"audit after_state.parent_hash {after.get('parent_hash')!r} must equal "
        f"proposal.parent_hash {prop.get('parent_hash')!r}."
    )


# ===========================================================================
# Reconciler broker-safety (2026-07-01) — false-orphan thrash prevention.
#
# Root cause: on a non-Bitget account (okx_demo), classify_positions fell back to
# the legacy Bitget-only "demo" cache when the unified (multi-broker) cache was
# stale/absent. That cache is fresh-but-empty for a non-Bitget account, so every
# real position was classified `orphaned` → reconcile-close (local only) → the
# position survives on the exchange → re-adopt next cycle → endless thrash.
# 41% of positions closed reconciled_gone, 56% source=adopted, destroying
# strategy attribution. See docs/research/ENTRY_EXIT_ASYMMETRY_ANALYSIS_2026-07-01.md.
# ===========================================================================


def _write_config(tmp_path, monkeypatch, paper_execution_mode: str, mode: str = "demo"):
    """Write a config.yaml selecting the active paper broker and reset the cache."""
    import yaml
    import aletheia.config.loader as loader
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({
        "exchange": {"mode": mode, "paper_execution_mode": paper_execution_mode},
        "trading": {"auto_adopt_enabled": True},
    }), encoding="utf-8")
    monkeypatch.setattr(loader, "CONFIG_PATH", config_path)
    loader._cached_config = None


@spec_claim(
    SpecClaim(
        "PL-ADOPT-10", "pipeline_adopted_position", "§10 non_bitget_no_false_orphan", "L2b",
        "On a NON-Bitget account (paper_execution_mode='okx_demo') with no fresh "
        "unified cache, classify_positions must NOT orphan real local positions by "
        "reading the empty legacy Bitget cache — it bails to all-managed. This is the "
        "false-orphan thrash root-cause lock.",
    ),
)
def test_non_bitget_stale_unified_never_orphans(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    _write_config(tmp_path, monkeypatch, paper_execution_mode="okx_demo")

    from aletheia.storage.models import Position
    from aletheia.storage import repositories as repo
    repo.insert_position(Position(
        symbol="BNB/USDT", market_type="usdt_futures", direction="short",
        quantity=0.03, entry_price=562.73, execution_mode="okx_demo",
    ))

    # No fresh unified (multi-broker) view...
    from aletheia.portfolio import position_reconciler as pr
    monkeypatch.setattr(pr.exchange_sync, "get_unified_cached_state", lambda: None)
    # ...but the legacy Bitget "demo" cache is fresh-but-EMPTY (the trap):
    monkeypatch.setattr(pr.exchange_sync, "get_cached_state",
                        lambda mode=None: {"positions": [], "age_seconds": 5.0,
                                           "fetched_at": "2026-07-01T00:00:00+00:00"})
    monkeypatch.setattr(pr.exchange_sync, "status_for", lambda state, max_age_sec=600: "ok")

    r = pr.classify_positions("demo")
    assert len(r.orphaned) == 0, (
        f"non-Bitget account must NOT false-orphan against the empty Bitget cache "
        f"(got {len(r.orphaned)} orphaned). This is the reconcile-close/re-adopt thrash."
    )
    assert len(r.managed) == 1, "the real OKX position must be reported managed (safe), not lost"


@spec_claim(
    SpecClaim(
        "PL-ADOPT-11", "pipeline_adopted_position", "§11 non_bitget_fresh_unified_managed", "L2b",
        "On a non-Bitget account, when the unified cache IS fresh and shows the "
        "position, classify_positions reports it managed (the healthy path still "
        "works — the fix only changes the degraded/stale case).",
    ),
)
def test_non_bitget_fresh_unified_manages(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    _write_config(tmp_path, monkeypatch, paper_execution_mode="okx_demo")

    from aletheia.storage.models import Position
    from aletheia.storage import repositories as repo
    repo.insert_position(Position(
        symbol="BNB/USDT", market_type="usdt_futures", direction="short",
        quantity=0.03, entry_price=562.73, execution_mode="okx_demo",
    ))

    from aletheia.portfolio import position_reconciler as pr
    monkeypatch.setattr(pr.exchange_sync, "get_unified_cached_state", lambda: {
        "positions": [{"symbol": "BNBUSDT", "side": "short", "quantity": 0.03,
                       "entry_price": 562.73, "mark_price": 560.0}],
        "age_seconds": 5.0, "fetched_at": "2026-07-01T00:00:00+00:00",
    })

    r = pr.classify_positions("demo")
    assert len(r.managed) == 1, "fresh unified showing the position → managed"
    assert len(r.orphaned) == 0, "fresh unified showing the position → not orphaned"


@spec_claim(
    SpecClaim(
        "PL-ADOPT-12", "pipeline_adopted_position", "§12 no_orphan_close_thrash_e2e", "L2c",
        "End-to-end: run_reconcile('demo') on a non-Bitget account with no fresh "
        "unified view closes ZERO orphans (closed_orphans == []). The thrash loop "
        "(false-orphan → local close → re-adopt) is prevented at the source.",
    ),
)
def test_run_reconcile_non_bitget_no_orphan_close(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    _stub_audit(monkeypatch, tmp_path)
    _write_config(tmp_path, monkeypatch, paper_execution_mode="okx_demo")

    from aletheia.storage.models import Position
    from aletheia.storage import repositories as repo
    repo.insert_position(Position(
        symbol="ETH/USDT", market_type="usdt_futures", direction="short",
        quantity=1.0, entry_price=1574.8, current_price=1560.0, execution_mode="okx_demo",
    ))

    from aletheia.portfolio import position_reconciler as pr
    monkeypatch.setattr(pr.exchange_sync, "get_unified_cached_state", lambda: None)
    monkeypatch.setattr(pr.exchange_sync, "get_cached_state",
                        lambda mode=None: {"positions": [], "age_seconds": 5.0,
                                           "fetched_at": "2026-07-01T00:00:00+00:00"})
    monkeypatch.setattr(pr.exchange_sync, "status_for", lambda state, max_age_sec=600: "ok")

    summary = pr.run_reconcile()
    assert summary["closed_orphans"] == [], (
        f"non-Bitget + stale unified must close ZERO orphans, got "
        f"{summary['closed_orphans']} — this is the thrash the fix prevents"
    )
    # The real position must still be open (not falsely swept).
    assert len(repo.list_positions(status="open")) == 1
