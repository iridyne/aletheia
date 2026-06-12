"""Tests for position_reconciler.classify_positions().

Classification joins the cached exchange position view against the local open
positions table and labels each position managed / external / orphaned. These
tests pin the load-bearing behavior:
  - hedge mode: a symbol can be long AND short at once → match on the PAIR.
  - symbol canonicalization: exchange 'SOLUSDT' matches local 'SOL/USDT'.
  - honest degradation: no usable exchange cache → never infer orphans.
"""

import sqlite3

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import Position
from aletheia.storage import repositories as repo


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


def _stub_cache(monkeypatch, positions, status="ok"):
    """Patch exchange_sync so classify_positions reads our fake exchange view."""
    from aletheia.portfolio import position_reconciler as pr
    cached = {"positions": positions, "open_positions_count": len(positions),
              "age_seconds": 1.0, "fetched_at": "2026-06-12T00:00:00+00:00"}
    monkeypatch.setattr(pr.exchange_sync, "get_cached_state",
                        lambda mode=None: cached if status not in ("unconfigured",) else None)
    monkeypatch.setattr(pr.exchange_sync, "status_for",
                        lambda state, max_age_sec=600: status)


def _ex(symbol, side, qty, entry=100.0, upnl=0.0):
    return {"symbol": symbol, "side": side, "quantity": qty,
            "entry_price": entry, "unrealized_pnl": upnl, "mark_price": entry}


def test_managed_when_local_and_exchange_agree(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    repo.insert_position(Position(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=1.0, entry_price=60000.0, execution_mode="bitget_demo",
    ))
    _stub_cache(monkeypatch, [_ex("BTCUSDT", "long", 1.0, 60000.0)])

    from aletheia.portfolio.position_reconciler import classify_positions
    r = classify_positions()
    assert len(r.managed) == 1
    assert len(r.external) == 0
    assert len(r.orphaned) == 0
    assert r.managed[0].symbol == "BTC/USDT"
    assert r.managed[0].qty_drift == 0.0


def test_external_when_on_exchange_not_local(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    # No local positions; exchange holds two.
    _stub_cache(monkeypatch, [
        _ex("BTCUSDT", "short", 0.0004, 61524.3, upnl=-0.87),
        _ex("SOLUSDT", "short", 0.2, 63.36, upnl=-0.72),
    ])
    from aletheia.portfolio.position_reconciler import classify_positions
    r = classify_positions()
    assert len(r.external) == 2
    assert len(r.managed) == 0
    syms = {(i.symbol, i.direction) for i in r.external}
    assert ("BTC/USDT", "short") in syms
    assert ("SOL/USDT", "short") in syms
    # External items carry the exchange row (with the real entry price), no local row.
    btc = next(i for i in r.external if i.symbol == "BTC/USDT")
    assert btc.local is None
    assert btc.exchange["entry_price"] == 61524.3


def test_orphaned_when_local_open_not_on_exchange(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    repo.insert_position(Position(
        symbol="ETH/USDT", market_type="usdt_futures", direction="long",
        quantity=2.0, entry_price=3000.0, execution_mode="bitget_demo",
    ))
    _stub_cache(monkeypatch, [])  # exchange has nothing
    from aletheia.portfolio.position_reconciler import classify_positions
    r = classify_positions()
    assert len(r.orphaned) == 1
    assert r.orphaned[0].symbol == "ETH/USDT"
    assert r.orphaned[0].local is not None
    assert r.orphaned[0].exchange is None


def test_hedge_mode_long_and_short_same_symbol(tmp_path, monkeypatch):
    """The demo account holds SOL long AND SOL short simultaneously — they must be
    classified independently by the (symbol, direction) pair, not collapsed."""
    setup_env(tmp_path, monkeypatch)
    # Local tracks only the long leg; exchange has both legs.
    repo.insert_position(Position(
        symbol="SOL/USDT", market_type="usdt_futures", direction="long",
        quantity=1.8, entry_price=67.28, execution_mode="bitget_demo",
    ))
    _stub_cache(monkeypatch, [
        _ex("SOLUSDT", "long", 1.8, 67.28),
        _ex("SOLUSDT", "short", 0.2, 63.36),
    ])
    from aletheia.portfolio.position_reconciler import classify_positions
    r = classify_positions()
    assert len(r.managed) == 1   # long leg matched
    assert len(r.external) == 1  # short leg unmanaged
    assert r.managed[0].direction == "long"
    assert r.external[0].direction == "short"


def test_symbol_canonicalization_matches(tmp_path, monkeypatch):
    """Exchange concatenated 'SOLUSDT' must match local canonical 'SOL/USDT'."""
    setup_env(tmp_path, monkeypatch)
    repo.insert_position(Position(
        symbol="SOL/USDT", market_type="usdt_futures", direction="short",
        quantity=0.2, entry_price=63.36, execution_mode="bitget_demo",
    ))
    _stub_cache(monkeypatch, [_ex("SOLUSDT", "short", 0.2, 63.36)])
    from aletheia.portfolio.position_reconciler import classify_positions
    r = classify_positions()
    assert len(r.managed) == 1
    assert len(r.external) == 0


def test_qty_drift_still_managed(tmp_path, monkeypatch):
    """A size difference (exchange aggregates fills) is reported as drift, not a
    new external position — the pair still matches."""
    setup_env(tmp_path, monkeypatch)
    repo.insert_position(Position(
        symbol="SOL/USDT", market_type="usdt_futures", direction="long",
        quantity=1.2, entry_price=67.0, execution_mode="bitget_demo",
    ))
    _stub_cache(monkeypatch, [_ex("SOLUSDT", "long", 1.8, 67.0)])
    from aletheia.portfolio.position_reconciler import classify_positions
    r = classify_positions()
    assert len(r.managed) == 1
    assert len(r.external) == 0
    assert abs(r.managed[0].qty_drift - 0.6) < 1e-9


def test_degraded_cache_never_infers_orphans(tmp_path, monkeypatch):
    """No usable exchange view → locals reported managed, NEVER orphaned.
    'No exchange data' must not look like 'the exchange has no positions'."""
    setup_env(tmp_path, monkeypatch)
    repo.insert_position(Position(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=1.0, entry_price=60000.0, execution_mode="bitget_demo",
    ))
    _stub_cache(monkeypatch, [], status="unconfigured")
    from aletheia.portfolio.position_reconciler import classify_positions
    r = classify_positions()
    assert r.sync_status == "unconfigured"
    assert len(r.orphaned) == 0
    assert len(r.managed) == 1  # local position reported, not lost


def test_zero_quantity_exchange_rows_ignored(tmp_path, monkeypatch):
    """Bitget sometimes returns zero-qty slots for closed positions — skip them."""
    setup_env(tmp_path, monkeypatch)
    _stub_cache(monkeypatch, [_ex("BTCUSDT", "long", 0.0, 60000.0)])
    from aletheia.portfolio.position_reconciler import classify_positions
    r = classify_positions()
    assert len(r.external) == 0
    assert len(r.managed) == 0


# --- P3 tests: orphan close + run_reconcile orchestration ---


def test_reconcile_close_orphan_closes_local_position(tmp_path, monkeypatch):
    """Orphaned position (local open, exchange gone) gets closed with reason='reconciled_gone'."""
    setup_env(tmp_path, monkeypatch)
    pos = Position(
        symbol="ETH/USDT", market_type="usdt_futures", direction="long",
        quantity=2.0, entry_price=3000.0, current_price=3100.0,
        execution_mode="bitget_demo",
    )
    repo.insert_position(pos)
    _stub_cache(monkeypatch, [])

    from aletheia.portfolio.position_reconciler import classify_positions, reconcile_close_orphan
    result = classify_positions()
    assert len(result.orphaned) == 1

    # Stub the audit writer so it doesn't hit real filesystem
    monkeypatch.setattr("aletheia.files.jsonl.write_audit", lambda *a, **kw: None)

    close_result = reconcile_close_orphan(result.orphaned[0])
    assert close_result["position_id"] == pos.id
    assert close_result["reason"] == "reconciled_gone"
    assert close_result["exit_price"] == 3100.0

    # Verify position is now closed in DB
    closed = repo.list_positions(status="closed")
    assert len(closed) == 1
    assert closed[0]["close_reason"] == "reconciled_gone"


def test_run_reconcile_demo_adopts_and_closes(tmp_path, monkeypatch):
    """run_reconcile in demo mode: auto-adopts externals + closes orphans."""
    setup_env(tmp_path, monkeypatch)

    # Write a config file so load_config works
    import yaml
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({
        "exchange": {"mode": "demo"},
        "trading": {"auto_adopt_enabled": True},
    }), encoding="utf-8")
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "CONFIG_PATH", config_path)
    loader._cached_config = None

    # Local orphan: open locally, gone from exchange
    orphan = Position(
        symbol="ETH/USDT", market_type="usdt_futures", direction="long",
        quantity=1.0, entry_price=3000.0, current_price=3050.0,
        execution_mode="bitget_demo",
    )
    repo.insert_position(orphan)

    # Exchange has an external position not tracked locally
    _stub_cache(monkeypatch, [_ex("SOLUSDT", "short", 0.5, 150.0, upnl=-2.0)])

    # Stub audit/jsonl so it doesn't hit real filesystem
    monkeypatch.setattr("aletheia.files.jsonl.write_audit", lambda *a, **kw: None)
    monkeypatch.setattr("aletheia.files.jsonl.write_audit", lambda *a, **kw: None)

    from aletheia.portfolio.position_reconciler import run_reconcile
    summary = run_reconcile()

    assert summary["account_mode"] == "demo"
    assert len(summary["adopted"]) == 1
    assert summary["adopted"][0]["symbol"] == "SOL/USDT"
    assert summary["adopted"][0]["execution_mode"] == "bitget_demo"
    assert len(summary["closed_orphans"]) == 1
    assert summary["closed_orphans"][0]["position_id"] == orphan.id
    assert summary["closed_orphans"][0]["reason"] == "reconciled_gone"


def test_run_reconcile_live_never_adopts(tmp_path, monkeypatch):
    """run_reconcile in live mode: classifies only, never adopts or closes."""
    setup_env(tmp_path, monkeypatch)

    import yaml
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({
        "exchange": {"mode": "live"},
        "trading": {"auto_adopt_enabled": True},
    }), encoding="utf-8")
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "CONFIG_PATH", config_path)
    loader._cached_config = None

    # Local orphan
    repo.insert_position(Position(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.01, entry_price=60000.0, current_price=61000.0,
        execution_mode="bitget_live_limited",
    ))
    # External on exchange
    _stub_cache(monkeypatch, [_ex("ETHUSDT", "long", 1.0, 3000.0)])

    from aletheia.portfolio.position_reconciler import run_reconcile
    summary = run_reconcile()

    assert summary["account_mode"] == "live"
    assert summary["adopted"] == []
    assert summary["closed_orphans"] == []
    assert summary["counts"]["external"] == 1
    assert summary["counts"]["orphaned"] == 1


def test_run_reconcile_respects_auto_adopt_disabled(tmp_path, monkeypatch):
    """When auto_adopt_enabled=False, externals are NOT adopted (kill switch)."""
    setup_env(tmp_path, monkeypatch)

    import yaml
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({
        "exchange": {"mode": "demo"},
        "trading": {"auto_adopt_enabled": False},
    }), encoding="utf-8")
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "CONFIG_PATH", config_path)
    loader._cached_config = None

    _stub_cache(monkeypatch, [_ex("BTCUSDT", "short", 0.001, 62000.0)])

    monkeypatch.setattr("aletheia.files.jsonl.write_audit", lambda *a, **kw: None)

    from aletheia.portfolio.position_reconciler import run_reconcile
    summary = run_reconcile()

    # No adoption (disabled), but orphan close still runs
    assert summary["adopted"] == []
    assert summary["counts"]["external"] == 1
