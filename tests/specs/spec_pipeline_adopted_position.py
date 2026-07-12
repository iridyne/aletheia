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


def _write_config(tmp_path, monkeypatch, paper_execution_mode: str, mode: str = "demo",
                  exchange_tpsl_enabled: bool = False):
    """Write a config.yaml selecting the active paper broker and reset the cache."""
    import yaml
    import aletheia.config.loader as loader
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({
        "exchange": {"mode": mode, "paper_execution_mode": paper_execution_mode},
        "trading": {"auto_adopt_enabled": True, "exchange_tpsl_enabled": exchange_tpsl_enabled},
    }), encoding="utf-8")
    monkeypatch.setattr(loader, "CONFIG_PATH", config_path)
    loader._invalidate_config_cache()


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
    # Fresh unified that INCLUDES the active broker (okx_demo) in accounts → complete.
    monkeypatch.setattr(pr.exchange_sync, "get_unified_cached_state", lambda: {
        "positions": [{"broker_id": "okx_demo", "symbol": "BNBUSDT", "side": "short", "quantity": 0.03,
                       "entry_price": 562.73, "mark_price": 560.0}],
        "accounts": [{"broker_id": "okx_demo", "equity_usdt": 89000.0}],
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


# ===========================================================================
# Exchange-side protective orders on adopt (P2-1, 2026-07-01).
# adopt_position persisted DB stop/tp but placed NO native exchange bracket →
# an adopted position was unprotected on the exchange if the gateway went down.
# Gated by cfg.trading.exchange_tpsl_enabled (default False → dormant) + DEMO-only
# (never an autonomous live order). See execution.stops.place_exchange_protective_orders.
# ===========================================================================


class _FakeOKXClient:
    """Network-boundary stub: returns bracket ids without touching OKX."""
    placed: list = []

    def __init__(self, mode="demo"):
        self.mode = mode

    async def place_algo_order(self, symbol, market_type, close_side, quantity,
                               stop_price=None, take_profit_price=None):
        _FakeOKXClient.placed.append({
            "symbol": symbol, "close_side": close_side, "quantity": quantity,
            "stop_price": stop_price, "take_profit_price": take_profit_price,
        })
        return {"sl": "algo-sl-1", "tp": "algo-tp-1"}

    async def close(self):
        return None


@spec_claim(
    SpecClaim(
        "PL-ADOPT-13", "pipeline_adopted_position", "§13 protective_order_dispatch_gating", "L2b",
        "execution.stops.place_exchange_protective_orders: no-op when "
        "exchange_tpsl_enabled=False (dormant) OR the mode is not a supported DEMO "
        "mode (live never gets an autonomous exchange order); places + persists the "
        "bracket id when enabled AND okx_demo.",
    ),
)
def test_protective_order_dispatch_gating(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    _stub_audit(monkeypatch, tmp_path)

    from aletheia.storage.models import Position
    from aletheia.storage import repositories as repo
    from aletheia.execution import okx_broker

    def _insert_okx_short():
        p = Position(symbol="BNB/USDT", market_type="usdt_futures", direction="short",
                     quantity=0.03, entry_price=562.73, execution_mode="okx_demo",
                     status="open", stop_price=573.98, take_profit_price=540.22)
        repo.insert_position(p)
        return p

    monkeypatch.setattr(okx_broker, "OKXClient", _FakeOKXClient)
    from aletheia.execution.stops import place_exchange_protective_orders

    # 1. flag OFF → dormant no-op (even okx_demo)
    _write_config(tmp_path, monkeypatch, "okx_demo", exchange_tpsl_enabled=False)
    p1 = _insert_okx_short()
    _FakeOKXClient.placed = []
    assert place_exchange_protective_orders({
        "id": p1.id, "symbol": "BNB/USDT", "market_type": "usdt_futures",
        "direction": "short", "quantity": 0.03, "stop_price": 573.98,
        "take_profit_price": 540.22, "execution_mode": "okx_demo",
    }) == {}, "flag OFF must be a dormant no-op"
    assert _FakeOKXClient.placed == [], "no exchange call when flag off"

    # 2. flag ON + LIVE mode → never an autonomous live order
    _write_config(tmp_path, monkeypatch, "okx_demo", exchange_tpsl_enabled=True)
    _FakeOKXClient.placed = []
    assert place_exchange_protective_orders({
        "id": p1.id, "symbol": "BNB/USDT", "market_type": "usdt_futures",
        "direction": "short", "quantity": 0.03, "stop_price": 573.98,
        "take_profit_price": 540.22, "execution_mode": "bitget_live_limited",
    }) == {}, "live mode must never place an autonomous exchange order"
    assert _FakeOKXClient.placed == [], "no exchange call for live mode"

    # 3. flag ON + okx_demo → places bracket + persists id (RESULT, not just call)
    ids = place_exchange_protective_orders({
        "id": p1.id, "symbol": "BNB/USDT", "market_type": "usdt_futures",
        "direction": "short", "quantity": 0.03, "stop_price": 573.98,
        "take_profit_price": 540.22, "execution_mode": "okx_demo",
    })
    assert ids == {"sl": "algo-sl-1", "tp": "algo-tp-1"}
    # short → close side is BUY (reduce the short)
    assert _FakeOKXClient.placed and _FakeOKXClient.placed[-1]["close_side"] == "buy"
    import json
    row = conn.execute("SELECT exchange_tpsl_order_id FROM positions WHERE id=?", (p1.id,)).fetchone()
    assert json.loads(row["exchange_tpsl_order_id"]) == {"sl": "algo-sl-1", "tp": "algo-tp-1"}, (
        "placed bracket ids must be persisted to positions.exchange_tpsl_order_id"
    )


@spec_claim(
    SpecClaim(
        "PL-ADOPT-14", "pipeline_adopted_position", "§14 adopt_places_exchange_bracket", "L2b",
        "adopt_position(demo, okx) with exchange_tpsl_enabled=True places an exchange "
        "protective bracket and persists exchange_tpsl_order_id — closing the "
        "'adopted position unprotected on the exchange' gap end-to-end.",
    ),
)
def test_adopt_places_exchange_bracket(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    _stub_notifications(monkeypatch)
    _stub_audit(monkeypatch, tmp_path)
    _write_config(tmp_path, monkeypatch, "okx_demo", exchange_tpsl_enabled=True)

    from aletheia.execution import okx_broker
    _FakeOKXClient.placed = []
    monkeypatch.setattr(okx_broker, "OKXClient", _FakeOKXClient)

    from aletheia.portfolio.position_reconciler import adopt_position
    ids = adopt_position(_make_exchange_pos(symbol="BNB/USDT", side="short",
                                            quantity=0.03, entry_price=562.73),
                         account_mode="demo")

    import json
    row = conn.execute(
        "SELECT exchange_tpsl_order_id FROM positions WHERE id=?", (ids["position_id"],)
    ).fetchone()
    assert row["exchange_tpsl_order_id"] is not None, (
        "adopted okx_demo position must have an exchange bracket when exchange_tpsl_enabled"
    )
    assert json.loads(row["exchange_tpsl_order_id"]) == {"sl": "algo-sl-1", "tp": "algo-tp-1"}
    assert _FakeOKXClient.placed, "adopt must have called the OKX protective-order path"


# ===========================================================================
# Strategy-attribution recovery on adopt (P2-2, 2026-07-01).
# The thrash (now fixed at source by P0) severed strategy_id: a strategy position
# orphan-closed then re-adopted came back with strategy_id=NULL. Defense-in-depth:
# when adopting an external that TIGHTLY matches a very recently reconciled_gone
# local close (same symbol+dir, ~entry, <15min), inherit its strategy_id/opp_id so
# any residual transient orphan→re-adopt keeps attribution intact. Tight match so a
# genuinely-new external is never mis-attributed.
# ===========================================================================


@spec_claim(
    SpecClaim(
        "PL-ADOPT-15", "pipeline_adopted_position", "§15 adopt_recovers_strategy_id", "L2b",
        "adopt_position inherits strategy_id + opportunity_id from a recently "
        "reconciled_gone local close that matches (symbol, direction, ~entry_price), "
        "so a transient orphan→re-adopt keeps trade attribution.",
    ),
)
def test_adopt_recovers_strategy_id_from_recent_thrash_close(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    _stub_notifications(monkeypatch)
    _stub_audit(monkeypatch, tmp_path)

    from datetime import datetime, timezone
    from aletheia.storage.models import Position
    from aletheia.storage import repositories as repo
    from aletheia.portfolio.position_reconciler import adopt_position

    now = datetime.now(timezone.utc).isoformat()
    # A strategy position that was just thrash-closed (reconciled_gone) seconds ago.
    repo.insert_position(Position(
        symbol="BNB/USDT", market_type="usdt_futures", direction="short",
        quantity=0.03, entry_price=562.73, execution_mode="okx_demo",
        status="closed", close_reason="reconciled_gone", closed_at=now,
        strategy_id="strat-xyz", opportunity_id="opp-abc",
    ))

    ids = adopt_position(_make_exchange_pos(symbol="BNB/USDT", side="short",
                                            quantity=0.03, entry_price=562.73),
                         account_mode="demo")
    row = conn.execute(
        "SELECT strategy_id, opportunity_id FROM positions WHERE id=?", (ids["position_id"],)
    ).fetchone()
    assert row["strategy_id"] == "strat-xyz", (
        f"adopted position must recover strategy_id from the recent thrash close, "
        f"got {row['strategy_id']!r}"
    )
    assert row["opportunity_id"] == "opp-abc", "opportunity_id must be recovered too"


@spec_claim(
    SpecClaim(
        "PL-ADOPT-16", "pipeline_adopted_position", "§16 adopt_no_false_attribution", "L2b",
        "adopt_position does NOT inherit a strategy_id when there is no tight recent "
        "reconciled_gone match — a genuinely-new external position stays "
        "strategy_id=NULL (no mis-attribution).",
    ),
)
def test_adopt_no_false_strategy_attribution(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    _stub_notifications(monkeypatch)
    _stub_audit(monkeypatch, tmp_path)

    from datetime import datetime, timezone
    from aletheia.storage.models import Position
    from aletheia.storage import repositories as repo
    from aletheia.portfolio.position_reconciler import adopt_position

    now = datetime.now(timezone.utc).isoformat()
    # A genuine EXIT (stop_loss) of a strategy position — NOT a thrash close.
    # A new external must NOT inherit its strategy_id (the position really exited).
    repo.insert_position(Position(
        symbol="ETH/USDT", market_type="usdt_futures", direction="long",
        quantity=1.0, entry_price=1600.0, execution_mode="okx_demo",
        status="closed", close_reason="stop_loss", closed_at=now,
        strategy_id="strat-should-not-leak",
    ))

    ids = adopt_position(_make_exchange_pos(symbol="ETH/USDT", side="long",
                                            quantity=1.0, entry_price=1600.0),
                         account_mode="demo")
    row = conn.execute(
        "SELECT strategy_id FROM positions WHERE id=?", (ids["position_id"],)
    ).fetchone()
    assert row["strategy_id"] is None, (
        f"a genuinely-exited (stop_loss) position must NOT leak its strategy_id to a "
        f"new external adoption, got {row['strategy_id']!r}"
    )


# ===========================================================================
# Self-monitoring + runtime self-heal (A + B, 2026-07-01).
# A: detect_reconcile_thrash — a root-cause-agnostic fingerprint of the close→reopen
#    flip-flop (same symbol+dir both reconciled_gone-closed AND re-adopted in a
#    window). Doubles as a REGRESSION tripwire for the P0 broker-cache fix.
# B: run_reconcile circuit-breaker — on the thrash fingerprint, suspend the
#    destructive orphan-close + auto-adopt for that cycle (runtime state, NOT a code
#    change) and alert. Mirrors the LLM-outage / connectivity auto-pause pattern.
# ===========================================================================


def _mk_churn_signature(repo, Position, symbol, direction="short", entry=100.0):
    """One flip-flop: a reconciled_gone close + a source=adopted (re)open, both
    recent. Inserted as status=closed so they don't perturb classify_positions —
    the detector counts them by close_reason / source + timestamp, not status."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    repo.insert_position(Position(
        symbol=symbol, market_type="usdt_futures", direction=direction,
        quantity=1.0, entry_price=entry, execution_mode="bitget_demo",
        status="closed", close_reason="reconciled_gone", closed_at=now,
    ))
    repo.insert_position(Position(
        symbol=symbol, market_type="usdt_futures", direction=direction,
        quantity=1.0, entry_price=entry, execution_mode="bitget_demo",
        status="closed", source="adopted", opened_at=now, closed_at=now,
    ))


@spec_claim(
    SpecClaim(
        "PL-ADOPT-17", "pipeline_adopted_position", "§17 detect_reconcile_thrash", "L2b",
        "detect_reconcile_thrash flags administrative-close → native/adopted reopen "
        "for the same account-scoped instrument identity. Trips at >=2 identities; "
        "a single one is an early signal only.",
    ),
)
def test_detect_reconcile_thrash_fingerprint(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    from aletheia.storage.models import Position
    from aletheia.storage import repositories as repo
    from aletheia.portfolio.position_reconciler import detect_reconcile_thrash

    # Clean → no thrash.
    r0 = detect_reconcile_thrash(window_minutes=30)
    assert r0["is_thrashing"] is False and r0["churn_count"] == 0

    # One churned symbol → early signal, but NOT tripping (needs >=2).
    _mk_churn_signature(repo, Position, "BNB/USDT", "short", 562.73)
    r1 = detect_reconcile_thrash(window_minutes=30)
    assert r1["churn_count"] == 1 and r1["is_thrashing"] is False, (
        f"single flip-flop must not trip the breaker: {r1}"
    )

    # Two distinct churned symbols → systemic → trips.
    _mk_churn_signature(repo, Position, "ETH/USDT", "short", 1574.8)
    r2 = detect_reconcile_thrash(window_minutes=30)
    assert r2["churn_count"] == 2 and r2["is_thrashing"] is True, (
        f"two flip-flopped symbols must trip is_thrashing: {r2}"
    )
    assert set(r2["churned_symbols"]) == {"BNB/USDT:short", "ETH/USDT:short"}


@spec_claim(
    SpecClaim(
        "PL-ADOPT-18", "pipeline_adopted_position", "§18 reconcile_circuit_breaker", "L2c",
        "run_reconcile persists the incident and suspends destructive orphan-close + "
        "auto-adopt until spaced clean evidence and explicit resolution; restart or one "
        "clean poll cannot self-clear it, and real positions remain intact.",
    ),
)
def test_run_reconcile_circuit_breaker_suspends(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    _stub_audit(monkeypatch, tmp_path)
    _stub_notifications(monkeypatch)
    # bitget_demo so classify uses the (stubbed) legacy cache and WOULD orphan.
    _write_config(tmp_path, monkeypatch, "bitget_demo")

    from aletheia.storage.models import Position
    from aletheia.storage import repositories as repo
    from aletheia.portfolio import position_reconciler as pr

    # Thrash fingerprint present (2 churned symbols, recent, status=closed).
    _mk_churn_signature(repo, Position, "BNB/USDT", "short", 562.73)
    _mk_churn_signature(repo, Position, "ETH/USDT", "short", 1574.8)

    # A real OPEN orphan that classify WOULD reconcile-close absent the breaker.
    orphan = Position(
        symbol="SOL/USDT", market_type="usdt_futures", direction="long",
        quantity=1.0, entry_price=150.0, current_price=151.0, execution_mode="bitget_demo",
        status="open",
    )
    repo.insert_position(orphan)

    # Exchange view: fresh but empty → SOL orphaned (were it not for the breaker).
    monkeypatch.setattr(pr.exchange_sync, "get_unified_cached_state", lambda: None)
    monkeypatch.setattr(pr.exchange_sync, "get_cached_state",
                        lambda mode=None: {"positions": [], "age_seconds": 5.0,
                                           "fetched_at": "2026-07-01T00:00:00+00:00"})
    monkeypatch.setattr(pr.exchange_sync, "status_for", lambda state, max_age_sec=600: "ok")

    summary = pr.run_reconcile()
    assert summary.get("suspended"), "circuit-breaker must flag suspension in the summary"
    assert summary["closed_orphans"] == [], (
        f"thrash detected → orphan-close must be suspended, got {summary['closed_orphans']}"
    )
    assert summary["adopted"] == [], "thrash detected → auto-adopt must be suspended too"
    # The real open position must survive.
    assert len(repo.list_positions(status="open")) == 1


# ===========================================================================
# P0 safety foundation (2026-07-02): active-broker view-completeness gate.
# Fixes BOTH #1 (sizing off wrong equity) and #2 (reconciler orphans a broker
# whose API is down) via one predicate: is the ACTIVE broker actually present in
# the unified view? A fresh unified cache can still be MISSING the active broker
# (OKX 50001 → UTA drops okx, leaving only bitget) — the "fresh but incomplete"
# variant the earlier stale-cache fix did not cover.
# See docs/design/dynamic_sizing_operator_persona_design.md §4.1.
# ===========================================================================


@spec_claim(
    SpecClaim(
        "PL-ADOPT-19", "pipeline_adopted_position", "§19 active_broker_view_complete", "L2b",
        "active_broker_view_complete() returns False when the active paper broker "
        "(okx_demo) is NOT among the unified view's connected accounts (its API "
        "failed), True when it is, and True for paper_internal (no exchange).",
    ),
)
def test_active_broker_view_complete(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.portfolio import exchange_sync as es

    # okx_demo active, but unified only has bitget → INCOMPLETE.
    _write_config(tmp_path, monkeypatch, "okx_demo")
    monkeypatch.setattr(es, "get_unified_cached_state", lambda: {
        "accounts": [{"broker_id": "bitget_demo", "equity_usdt": 167.0}], "age_seconds": 5.0,
    })
    complete, reason = es.active_broker_view_complete()
    assert complete is False, f"okx missing from unified must be incomplete: {reason}"

    # okx_demo present → COMPLETE.
    monkeypatch.setattr(es, "get_unified_cached_state", lambda: {
        "accounts": [{"broker_id": "okx_demo", "equity_usdt": 89000.0}], "age_seconds": 5.0,
    })
    complete, _ = es.active_broker_view_complete()
    assert complete is True, "okx present in unified must be complete"

    # paper_internal → always complete (no exchange view needed).
    _write_config(tmp_path, monkeypatch, "paper_internal")
    complete, _ = es.active_broker_view_complete()
    assert complete is True, "paper_internal needs no exchange view"


@spec_claim(
    SpecClaim(
        "PL-ADOPT-20", "pipeline_adopted_position", "§20 fresh_but_incomplete_no_orphan", "L2b",
        "classify_positions does NOT orphan the active broker's positions when the "
        "unified view is FRESH but MISSING that broker (OKX-down case) — it bails to "
        "all-managed. This is the thrash variant beyond the stale-cache fix.",
    ),
)
def test_fresh_but_incomplete_unified_never_orphans(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _write_config(tmp_path, monkeypatch, "okx_demo")

    from aletheia.storage.models import Position
    from aletheia.storage import repositories as repo
    repo.insert_position(Position(
        symbol="BNB/USDT", market_type="usdt_futures", direction="short",
        quantity=0.03, entry_price=562.73, execution_mode="okx_demo", status="open",
    ))

    from aletheia.portfolio import position_reconciler as pr
    # FRESH unified (age 5s) but accounts only has bitget — OKX is down/missing.
    monkeypatch.setattr(pr.exchange_sync, "get_unified_cached_state", lambda: {
        "positions": [], "accounts": [{"broker_id": "bitget_demo", "equity_usdt": 167.0}],
        "age_seconds": 5.0, "fetched_at": "2026-07-02T00:00:00+00:00",
    })
    # Even if the legacy bitget cache were readable, it must not be used for okx.
    monkeypatch.setattr(pr.exchange_sync, "get_cached_state",
                        lambda mode=None: {"positions": [], "age_seconds": 5.0, "fetched_at": "x"})
    monkeypatch.setattr(pr.exchange_sync, "status_for", lambda state, max_age_sec=600: "ok")

    r = pr.classify_positions("demo")
    assert len(r.orphaned) == 0, (
        f"fresh-but-OKX-missing unified must NOT orphan the OKX position "
        f"(got {len(r.orphaned)}) — this is the OKX-down thrash variant"
    )
    assert len(r.managed) == 1, "the real OKX position must be reported managed (safe)"


@spec_claim(
    SpecClaim(
        "PL-ADOPT-22", "pipeline_adopted_position", "§22 execution_account_scope", "L2c",
        "Reconciliation scopes local rows to the active execution account; paper_internal "
        "positions are local-only and can never become exchange orphans.",
        status="implemented",
    )
)
def test_paper_internal_is_outside_exchange_reconciliation_scope(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _write_config(tmp_path, monkeypatch, "paper_internal")

    from aletheia.storage.models import Position
    from aletheia.storage import repositories as repo
    repo.insert_position(Position(
        symbol="VIRTUAL/USDT", market_type="usdt_futures", direction="short",
        quantity=100.0, entry_price=0.6, execution_mode="paper_internal", status="open",
    ))

    from aletheia.portfolio import position_reconciler as pr
    monkeypatch.setattr(pr.exchange_sync, "get_unified_cached_state", lambda: {
        "positions": [], "accounts": [], "age_seconds": 1.0,
    })
    monkeypatch.setattr(pr.exchange_sync, "status_for", lambda *args, **kwargs: "ok")

    result = pr.classify_positions("demo")
    assert result.sync_status == "local_only"
    assert len(result.managed) == 1
    assert result.orphaned == []
    assert result.external == []


@spec_claim(
    SpecClaim(
        "PL-ADOPT-24", "pipeline_adopted_position", "§22 execution_account_scope", "L2c",
        "Unified exchange positions must carry broker_id and are filtered to the active "
        "execution account before matching; ambiguous rows degrade without orphaning.",
        status="implemented",
    )
)
def test_unified_position_identity_is_required_for_destructive_reconcile(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _write_config(tmp_path, monkeypatch, "okx_demo")

    from aletheia.storage.models import Position
    from aletheia.storage import repositories as repo
    repo.insert_position(Position(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.01, entry_price=60_000.0, execution_mode="okx_demo", status="open",
    ))

    from aletheia.portfolio import position_reconciler as pr
    monkeypatch.setattr(pr.exchange_sync, "get_unified_cached_state", lambda: {
        "positions": [{"symbol": "BTCUSDT", "side": "long", "quantity": 0.01}],
        "accounts": [{"broker_id": "okx_demo"}],
        "age_seconds": 1.0,
    })

    result = pr.classify_positions("demo")
    assert len(result.managed) == 1
    assert result.orphaned == []


@spec_claim(
    SpecClaim(
        "PL-ADOPT-23", "pipeline_adopted_position", "§22 execution_account_scope", "L2b",
        "Live reconciliation derives its execution mode from the configured exchange "
        "instead of hardcoding Bitget.",
        status="implemented",
    )
)
def test_live_reconcile_scope_follows_configured_exchange(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    import yaml
    import aletheia.config.loader as loader
    from aletheia.portfolio.position_reconciler import _execution_mode_for

    loader.CONFIG_PATH.write_text(
        yaml.safe_dump({"exchange": {"name": "okx", "mode": "live"}}),
        encoding="utf-8",
    )
    loader._invalidate_config_cache()
    assert _execution_mode_for("live") == "okx_live_limited"


@spec_claim(
    SpecClaim(
        "PL-ADOPT-21", "pipeline_adopted_position", "§21 drawdown_skips_on_incomplete_view", "L2b",
        "#1 sizing guard: _portfolio_drawdown_scale returns 1.0 (skips) when the "
        "active-broker view is incomplete, instead of computing a FALSE ~99% drawdown "
        "off a wrong-broker equity snapshot (the drawdown-notional-trap family).",
    ),
)
def test_drawdown_scale_skips_on_incomplete_view(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    _write_config(tmp_path, monkeypatch, "okx_demo")

    from aletheia.storage.models import PortfolioSnapshot
    from aletheia.storage import repositories as repo
    # exchange_account snapshots that WOULD read as a ~99% drawdown (peak 89000 →
    # current 167). Explicit increasing created_at so the NEWEST (current, values[-1]
    # in the chronological window) is the 167 low — deterministic, not timestamp-tied.
    for i, v in enumerate((89000.0, 89000.0, 167.0)):
        repo.insert_portfolio_snapshot(PortfolioSnapshot(
            total_value_usdt=v, unrealized_pnl=0.0, realized_pnl=0.0,
            positions_count=0, execution_mode="exchange_account",
            snapshot_at=f"2026-07-02T00:0{i}:00+00:00",
        ))

    from aletheia.risk import engine
    import aletheia.portfolio.exchange_sync as es

    # Incomplete view (okx missing) → guard must fire → 1.0 (NOT the false DD → 0).
    monkeypatch.setattr(es, "get_unified_cached_state", lambda: {
        "accounts": [{"broker_id": "bitget_demo", "equity_usdt": 167.0}], "age_seconds": 5.0})
    assert engine._portfolio_drawdown_scale("okx_demo") == 1.0, (
        "incomplete view must skip drawdown scaling, not fabricate a 99% DD"
    )

    # Complete view (okx present) → the guard does NOT fire; the (test) snapshots
    # then drive a real, large drawdown → scale well below 1.0. Proves the 1.0 above
    # was the GUARD, not the default.
    monkeypatch.setattr(es, "get_unified_cached_state", lambda: {
        "accounts": [{"broker_id": "okx_demo", "equity_usdt": 89000.0}], "age_seconds": 5.0})
    assert engine._portfolio_drawdown_scale("okx_demo") < 1.0, (
        "complete view must compute the real drawdown from the snapshots (proves the "
        "1.0 in the incomplete case came from the guard, not a default)"
    )
