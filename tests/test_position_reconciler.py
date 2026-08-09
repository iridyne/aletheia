"""Tests for position_reconciler.classify_positions().

Classification joins the cached exchange position view against the local open
positions table and labels each position managed / external / orphaned. These
tests pin the load-bearing behavior:
  - hedge mode: a symbol can be long AND short at once → match on the PAIR.
  - symbol canonicalization: exchange 'SOLUSDT' matches local 'SOL/USDT'.
  - honest degradation: no usable exchange cache → never infer orphans.
"""

import json
import sqlite3

import pytest

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import Position
from aletheia.storage import repositories as repo


def setup_env(tmp_path, monkeypatch):
    import yaml
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"exchange": {"mode": "demo", "paper_execution_mode": "bitget_demo"}}),
        encoding="utf-8",
    )
    loader._cached_config = None
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


def _ex(
    symbol,
    side,
    qty,
    entry=100.0,
    upnl=0.0,
    *,
    market_type="usdt_futures",
    leverage=2,
):
    return {"symbol": symbol, "side": side, "quantity": qty,
            "entry_price": entry, "unrealized_pnl": upnl, "mark_price": entry,
            "market_type": market_type, "leverage": leverage,
            "leverage_source": "exchange_reported"}


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


def test_paper_internal_never_reconciles_against_exchange_cache(tmp_path, monkeypatch):
    """A local-only position has no exchange counterpart by design, not by drift."""
    setup_env(tmp_path, monkeypatch)
    import yaml
    import aletheia.config.loader as loader

    loader.CONFIG_PATH.write_text(
        yaml.safe_dump({"exchange": {"mode": "demo", "paper_execution_mode": "paper_internal"}}),
        encoding="utf-8",
    )
    loader._cached_config = None
    repo.insert_position(Position(
        symbol="VIRTUAL/USDT", market_type="usdt_futures", direction="short",
        quantity=100.0, entry_price=0.6, execution_mode="paper_internal",
    ))

    from aletheia.portfolio import position_reconciler as pr
    monkeypatch.setattr(
        pr.exchange_sync,
        "get_unified_cached_state",
        lambda: {"positions": [], "accounts": [], "age_seconds": 1.0},
    )
    monkeypatch.setattr(pr.exchange_sync, "status_for", lambda *a, **kw: "ok")

    result = pr.classify_positions("demo")
    assert len(result.managed) == 1
    assert result.managed[0].local["execution_mode"] == "paper_internal"
    assert result.external == []
    assert result.orphaned == []


def test_reconcile_scopes_locals_to_active_execution_account(tmp_path, monkeypatch):
    """External demo reconciliation excludes local-only and other-account rows."""
    setup_env(tmp_path, monkeypatch)
    import yaml
    import aletheia.config.loader as loader

    loader.CONFIG_PATH.write_text(
        yaml.safe_dump({"exchange": {"mode": "demo", "paper_execution_mode": "bitget_demo"}}),
        encoding="utf-8",
    )
    loader._cached_config = None
    repo.insert_position(Position(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.01, entry_price=60_000.0, execution_mode="paper_internal",
    ))
    external_local = Position(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.02, entry_price=60_000.0, execution_mode="bitget_demo",
    )
    repo.insert_position(external_local)
    _stub_cache(monkeypatch, [])

    from aletheia.portfolio.position_reconciler import classify_positions
    result = classify_positions("demo")
    assert result.managed == []
    assert [item.local["id"] for item in result.orphaned] == [external_local.id]


def test_live_reconcile_execution_mode_follows_active_exchange(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    import yaml
    import aletheia.config.loader as loader
    from aletheia.portfolio.position_reconciler import _execution_mode_for

    for exchange, expected in (
        ("bitget", "bitget_live_limited"),
        ("okx", "okx_live_limited"),
        ("binance", "binance_live_limited"),
    ):
        loader.CONFIG_PATH.write_text(
            yaml.safe_dump({"exchange": {"name": exchange, "mode": "live"}}),
            encoding="utf-8",
        )
        loader._invalidate_config_cache()
        assert _execution_mode_for("live") == expected


def test_unified_positions_are_scoped_to_active_broker(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    import yaml
    import aletheia.config.loader as loader

    loader.CONFIG_PATH.write_text(
        yaml.safe_dump({"exchange": {"mode": "demo", "paper_execution_mode": "okx_demo"}}),
        encoding="utf-8",
    )
    loader._invalidate_config_cache()
    repo.insert_position(Position(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.01, entry_price=60_000.0, execution_mode="okx_demo",
    ))

    from aletheia.portfolio import position_reconciler as pr
    monkeypatch.setattr(pr.exchange_sync, "get_unified_cached_state", lambda: {
        "accounts": [
            {"broker_id": "okx_demo"},
            {"broker_id": "bitget_demo"},
        ],
        "positions": [
            {**_ex("BTCUSDT", "long", 0.01, 60_000.0), "broker_id": "bitget_demo"},
        ],
        "age_seconds": 1.0,
    })

    result = pr.classify_positions("demo")
    assert result.managed == []
    assert len(result.orphaned) == 1
    assert result.external == []


def test_unified_position_without_broker_id_never_infers_orphan(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    import yaml
    import aletheia.config.loader as loader

    loader.CONFIG_PATH.write_text(
        yaml.safe_dump({"exchange": {"mode": "demo", "paper_execution_mode": "okx_demo"}}),
        encoding="utf-8",
    )
    loader._invalidate_config_cache()
    repo.insert_position(Position(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.01, entry_price=60_000.0, execution_mode="okx_demo",
    ))

    from aletheia.portfolio import position_reconciler as pr
    monkeypatch.setattr(pr.exchange_sync, "get_unified_cached_state", lambda: {
        "accounts": [{"broker_id": "okx_demo"}],
        "positions": [_ex("BTCUSDT", "long", 0.01, 60_000.0)],
        "age_seconds": 1.0,
    })

    result = pr.classify_positions("demo")
    assert result.sync_status == "unconfigured"
    assert len(result.managed) == 1
    assert result.orphaned == []


def test_malformed_unified_quantity_degrades_view_without_inferring_orphan(
    tmp_path,
    monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    import yaml
    import aletheia.config.loader as loader

    loader.CONFIG_PATH.write_text(
        yaml.safe_dump({"exchange": {"mode": "demo", "paper_execution_mode": "okx_demo"}}),
        encoding="utf-8",
    )
    loader._invalidate_config_cache()
    repo.insert_position(Position(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.01, entry_price=60_000.0, execution_mode="okx_demo",
    ))

    from aletheia.portfolio import position_reconciler as pr
    monkeypatch.setattr(pr.exchange_sync, "get_unified_cached_state", lambda: {
        "accounts": [{"broker_id": "okx_demo"}],
        "positions": [
            {**_ex("BTCUSDT", "long", "not-a-number", 60_000.0),
             "broker_id": "okx_demo"},
        ],
        "age_seconds": 1.0,
    })

    result = pr.classify_positions("demo")

    assert result.sync_status == "unconfigured"
    assert len(result.managed) == 1
    assert result.external == []
    assert result.orphaned == []


def test_zero_quantity_exchange_rows_ignored(tmp_path, monkeypatch):
    """Bitget sometimes returns zero-qty slots for closed positions — skip them."""
    setup_env(tmp_path, monkeypatch)
    _stub_cache(monkeypatch, [_ex("BTCUSDT", "long", 0.0, 60000.0)])
    from aletheia.portfolio.position_reconciler import classify_positions
    r = classify_positions()
    assert len(r.external) == 0
    assert len(r.managed) == 0


def test_invalid_local_quantity_degrades_identity_without_classification(
    tmp_path,
    monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    _stub_cache(monkeypatch, [_ex("BTCUSDT", "long", 0.01, 60_000.0)])

    from aletheia.portfolio import position_reconciler as pr
    monkeypatch.setattr(pr, "list_all_open_positions", lambda: [{
        "id": "position-invalid-quantity",
        "symbol": "BTC/USDT",
        "market_type": "usdt_futures",
        "direction": "long",
        "quantity": "not-a-number",
        "entry_price": 60_000.0,
        "execution_mode": "bitget_demo",
    }])

    result = pr.classify_positions("demo")

    assert result.managed == []
    assert result.external == []
    assert result.orphaned == []
    assert [issue.reason for issue in result.integrity_issues] == [
        "invalid_local_quantity"
    ]


def _stub_okx_unified(monkeypatch, positions):
    import yaml
    import aletheia.config.loader as loader
    from aletheia.portfolio import position_reconciler as pr

    loader.CONFIG_PATH.write_text(
        yaml.safe_dump({
            "exchange": {"mode": "demo", "paper_execution_mode": "okx_demo"}
        }),
        encoding="utf-8",
    )
    loader._invalidate_config_cache()
    monkeypatch.setattr(pr.exchange_sync, "get_unified_cached_state", lambda: {
        "accounts": [{"broker_id": "okx_demo"}],
        "positions": [
            {**position, "broker_id": "okx_demo"}
            for position in positions
        ],
        "age_seconds": 1.0,
        "fetched_at": "2026-08-07T00:00:00+00:00",
    })


@pytest.mark.parametrize("reverse", (False, True))
def test_spot_and_futures_same_symbol_are_independent_and_order_stable(
    tmp_path,
    monkeypatch,
    reverse,
):
    setup_env(tmp_path, monkeypatch)
    locals_ = [
        Position(
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            quantity=0.2,
            entry_price=60_000,
            execution_mode="okx_demo",
            leverage=None,
        ),
        Position(
            symbol="BTC/USDT",
            market_type="usdt_futures",
            direction="long",
            quantity=0.1,
            entry_price=60_000,
            execution_mode="okx_demo",
            leverage=3,
        ),
    ]
    exchange = [
        _ex("BTCUSDT", "long", 0.2, 60_000, market_type="spot", leverage=1),
        _ex(
            "BTCUSDT",
            "long",
            0.1,
            60_000,
            market_type="perp",
            leverage=3,
        ),
    ]
    for position in reversed(locals_) if reverse else locals_:
        repo.insert_position(position)
    _stub_okx_unified(
        monkeypatch,
        list(reversed(exchange)) if reverse else exchange,
    )

    from aletheia.portfolio.position_reconciler import classify_positions
    result = classify_positions("demo")

    assert [item.to_dict()["market_type"] for item in result.managed] == [
        "spot",
        "usdt_futures",
    ]
    assert result.external == []
    assert result.orphaned == []
    assert result.integrity_issues == []


def test_missing_market_is_classified_independently(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    spot = Position(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.2,
        entry_price=60_000,
        execution_mode="okx_demo",
    )
    futures = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.1,
        entry_price=60_000,
        execution_mode="okx_demo",
        leverage=3,
    )
    repo.insert_position(spot)
    repo.insert_position(futures)
    _stub_okx_unified(monkeypatch, [
        _ex("BTCUSDT", "long", 0.1, 60_000, leverage=3),
    ])

    from aletheia.portfolio.position_reconciler import classify_positions
    result = classify_positions("demo")

    assert [item.local["id"] for item in result.managed] == [futures.id]
    assert [item.local["id"] for item in result.orphaned] == [spot.id]
    assert result.external == []


def test_run_reconcile_keeps_same_symbol_spot_disappearance_and_futures_adoption_separate(
    tmp_path,
    monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    spot = Position(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.2,
        entry_price=60_000,
        execution_mode="okx_demo",
        leverage=None,
    )
    repo.insert_position(spot)
    _stub_okx_unified(monkeypatch, [
        _ex("BTCUSDT", "long", 0.1, 60_000, leverage=3),
    ])
    # _stub_okx_unified rewrites the config file, so disable the per-symbol
    # notional adoption cap AFTER it: the 6000-USDT external must be judged on
    # identity semantics, not refused by the concentration guard.
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.risk.demo.capital.max_per_symbol_notional_usdt = 0.0
    save_config(cfg)
    monkeypatch.setattr("aletheia.files.jsonl.write_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "aletheia.notifications.notify_operator",
        lambda *_args, **_kwargs: None,
    )

    from aletheia.portfolio.position_reconciler import run_reconcile
    summary = run_reconcile("demo")

    assert summary["counts"] == {
        "managed": 0,
        "external": 1,
        "orphaned": 1,
        "degraded": 0,
    }
    assert summary["disappearance"]["observations"][0]["position_id"] == spot.id
    assert len(summary["adopted"]) == 1
    open_positions = repo.list_positions(status="open")
    assert {(row["market_type"], row["source"]) for row in open_positions} == {
        ("spot", "native"),
        ("usdt_futures", "adopted"),
    }


@pytest.mark.parametrize("duplicate_source", ("local", "exchange"))
def test_duplicate_exact_identity_fails_closed(tmp_path, monkeypatch, duplicate_source):
    setup_env(tmp_path, monkeypatch)
    local_count = 2 if duplicate_source == "local" else 1
    for quantity in range(1, local_count + 1):
        repo.insert_position(Position(
            symbol="ETH/USDT",
            market_type="usdt_futures",
            direction="short",
            quantity=float(quantity),
            entry_price=3_000,
            execution_mode="okx_demo",
            leverage=2,
        ))
    exchange = [
        _ex("ETHUSDT", "short", 1, 3_000),
        _ex("ETHUSDT", "short", 2, 3_000),
    ] if duplicate_source == "exchange" else [
        _ex("ETHUSDT", "short", 1, 3_000),
    ]
    _stub_okx_unified(monkeypatch, exchange)

    from aletheia.portfolio.position_reconciler import classify_positions
    result = classify_positions("demo")

    assert result.managed == []
    assert result.external == []
    assert result.orphaned == []
    assert [issue.reason for issue in result.integrity_issues] == [
        f"duplicate_{duplicate_source}_identity"
    ]


@pytest.mark.parametrize("unknown_source", ("local", "exchange"))
def test_unknown_market_type_blocks_same_symbol_direction_identity(
    tmp_path,
    monkeypatch,
    unknown_source,
):
    setup_env(tmp_path, monkeypatch)
    repo.insert_position(Position(
        symbol="SOL/USDT",
        market_type=("legacy-unknown" if unknown_source == "local" else "usdt_futures"),
        direction="long",
        quantity=1,
        entry_price=150,
        execution_mode="okx_demo",
        leverage=2,
    ))
    _stub_okx_unified(monkeypatch, [
        _ex(
            "SOLUSDT",
            "long",
            1,
            150,
            market_type=(
                "legacy-unknown" if unknown_source == "exchange" else "usdt_futures"
            ),
        ),
    ])

    from aletheia.portfolio.position_reconciler import classify_positions
    result = classify_positions("demo")

    assert result.managed == []
    assert result.external == []
    assert result.orphaned == []
    assert any(
        issue.reason == f"unknown_{unknown_source}_market_type"
        for issue in result.integrity_issues
    )


def test_account_fingerprint_mismatch_blocks_identity(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    position = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.1,
        entry_price=60_000,
        execution_mode="okx_demo",
        leverage=2,
        reconciliation_identity=json.dumps({
            "account_fingerprint": "sha256:different-logical-account",
        }),
    )
    repo.insert_position(position)
    spot = Position(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.2,
        entry_price=60_000,
        execution_mode="okx_demo",
        leverage=None,
    )
    repo.insert_position(spot)
    _stub_okx_unified(monkeypatch, [
        _ex("BTCUSDT", "long", 0.1, 60_000),
        _ex("BTCUSDT", "long", 0.2, 60_000, market_type="spot", leverage=1),
    ])

    from aletheia.portfolio.position_reconciler import classify_positions
    result = classify_positions("demo")

    assert [item.local["id"] for item in result.managed] == [spot.id]
    assert result.external == []
    assert result.orphaned == []
    assert [issue.reason for issue in result.integrity_issues] == [
        "local_account_fingerprint_mismatch"
    ]


# --- P3 tests: orphan close + run_reconcile orchestration ---


def test_legacy_fallback_orphan_close_is_removed():
    """No caller can turn current/entry fallback price into a synthetic close."""
    import inspect
    from aletheia.portfolio import position_reconciler

    source = inspect.getsource(position_reconciler)
    assert "def reconcile_close_orphan" not in source
    assert "def _try_get_tpsl_fill_price" not in source


def test_run_reconcile_demo_adopts_and_records_disappearance(tmp_path, monkeypatch):
    """Demo may adopt externals, but one missing view only creates evidence."""
    setup_env(tmp_path, monkeypatch)

    import yaml
    import aletheia.config.loader as loader
    loader.CONFIG_PATH.write_text(yaml.safe_dump({
        "exchange": {"mode": "demo", "paper_execution_mode": "bitget_demo"},
        "trading": {"auto_adopt_enabled": True},
    }), encoding="utf-8")
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
    assert summary["closed_orphans"] == []
    # SOL 0.5 @ 150 = 75 USDT — under the default 400 cap.
    assert summary["oversize_adopted"] == []
    assert summary["disappearance"]["created"] == 1
    assert summary["disappearance"]["observations"][0]["position_id"] == orphan.id
    assert summary["disappearance"]["observations"][0]["confirmation_count"] == 1
    assert orphan.id in {row["id"] for row in repo.list_positions(status="open")}


def test_run_reconcile_live_never_adopts(tmp_path, monkeypatch):
    """run_reconcile in live mode: classifies only, never adopts or closes."""
    setup_env(tmp_path, monkeypatch)

    import yaml
    import aletheia.config.loader as loader
    loader.CONFIG_PATH.write_text(yaml.safe_dump({
        "exchange": {"mode": "live", "name": "bitget"},
        "trading": {"auto_adopt_enabled": True},
    }), encoding="utf-8")
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
    import aletheia.config.loader as loader
    loader.CONFIG_PATH.write_text(yaml.safe_dump({
        "exchange": {"mode": "demo", "paper_execution_mode": "bitget_demo"},
        "trading": {"auto_adopt_enabled": False},
    }), encoding="utf-8")
    loader._cached_config = None

    _stub_cache(monkeypatch, [_ex("BTCUSDT", "short", 0.001, 62000.0)])

    monkeypatch.setattr("aletheia.files.jsonl.write_audit", lambda *a, **kw: None)

    from aletheia.portfolio.position_reconciler import run_reconcile
    summary = run_reconcile()

    # No adoption (disabled), but orphan close still runs
    assert summary["adopted"] == []
    assert summary["counts"]["external"] == 1


def test_run_reconcile_refuses_oversized_external_adoption(tmp_path, monkeypatch):
    """An external position over the per-symbol notional cap (UNI martingale
    scenario) must NOT be adopted: the auto-adopt loop records the refusal in
    summary['errors'] and the local positions table stays untouched."""
    setup_env(tmp_path, monkeypatch)

    import yaml
    import aletheia.config.loader as loader
    loader.CONFIG_PATH.write_text(yaml.safe_dump({
        "exchange": {"mode": "demo", "paper_execution_mode": "bitget_demo"},
        "trading": {"auto_adopt_enabled": True},
    }), encoding="utf-8")
    loader._cached_config = None

    # 3072 UNI @ 4.45 ≈ 13.7k USDT — far above the default 400 cap.
    _stub_cache(monkeypatch, [_ex("UNIUSDT", "long", 3072.0, 4.45, upnl=0.0)])

    monkeypatch.setattr("aletheia.files.jsonl.write_audit", lambda *a, **kw: None)

    from aletheia.portfolio.position_reconciler import run_reconcile
    summary = run_reconcile()

    assert summary["adopted"] == []
    assert summary["counts"]["external"] == 1
    assert len(summary["errors"]) == 1
    assert "per-symbol notional" in summary["errors"][0]
    assert repo.list_positions(status="open") == []


def test_run_reconcile_flags_oversize_adopted_exposure(tmp_path, monkeypatch):
    """Positions adopted before the notional guard must be surfaced as
    summary['oversize_adopted'] (alert-only; no automatic orders), while
    under-cap adopted positions stay out of the list."""
    setup_env(tmp_path, monkeypatch)

    import yaml
    import aletheia.config.loader as loader
    loader.CONFIG_PATH.write_text(yaml.safe_dump({
        "exchange": {"mode": "demo", "paper_execution_mode": "bitget_demo"},
        "trading": {"auto_adopt_enabled": True},
    }), encoding="utf-8")
    loader._cached_config = None

    # Pre-guard legacy adoptions in the local book: UNI 3072 @ 4.4 ≈ 13.5k USDT
    # (over the default 400 cap) and DOT 24 @ 0.75 = 18 USDT (under).
    repo.insert_position(Position(
        symbol="UNI/USDT", market_type="usdt_futures", direction="long",
        quantity=3072.0, entry_price=4.4, current_price=4.4,
        execution_mode="bitget_demo", source="adopted",
    ))
    repo.insert_position(Position(
        symbol="DOT/USDT", market_type="usdt_futures", direction="long",
        quantity=24.0, entry_price=0.75, current_price=0.75,
        execution_mode="bitget_demo", source="adopted",
    ))

    _stub_cache(monkeypatch, [
        _ex("UNIUSDT", "long", 3072.0, 4.4),
        _ex("DOTUSDT", "long", 24.0, 0.75),
    ])
    monkeypatch.setattr("aletheia.files.jsonl.write_audit", lambda *a, **kw: None)

    from aletheia.portfolio.position_reconciler import run_reconcile
    summary = run_reconcile()

    assert summary["counts"]["managed"] == 2
    flags = summary["oversize_adopted"]
    assert len(flags) == 1
    assert flags[0]["symbol"] == "UNI/USDT"
    assert flags[0]["direction"] == "long"
    assert flags[0]["notional_usdt"] > 400
    assert flags[0]["cap_usdt"] == 400
    # Alert-only: no adoption or close activity triggered by the flag.
    assert summary["adopted"] == []
    assert summary["closed_orphans"] == []
