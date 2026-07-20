"""Tests for batch DB query functions used by market_observer parallel prefetch.

Each test verifies that batch_* returns results semantically equivalent to
calling the individual repository function N times.
"""

import pytest


def _prepare_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sqlite
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    sqlite.reset_db()
    run_migrations()


# ---------------------------------------------------------------------------
# batch_perp_structures
# ---------------------------------------------------------------------------

def test_batch_perp_structures_returns_latest_per_symbol(tmp_path, monkeypatch):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.storage.sqlite import get_db
    from aletheia.storage.repositories import batch_perp_structures, latest_perp_structure

    db = get_db()
    # Insert two rows for BTC — different as_of_time; batch should return the newer one.
    db.execute(
        "INSERT INTO perp_structure_snapshots (id, symbol, source, as_of_time, observed_at, raw_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("ps-btc-old", "BTC", "hyperliquid", "2026-07-13T10:00:00", "2026-07-13T10:01:00", "{}"),
    )
    db.execute(
        "INSERT INTO perp_structure_snapshots (id, symbol, source, as_of_time, observed_at, raw_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("ps-btc-new", "BTC", "hyperliquid", "2026-07-14T10:00:00", "2026-07-14T10:01:00", "{}"),
    )
    db.execute(
        "INSERT INTO perp_structure_snapshots (id, symbol, source, as_of_time, observed_at, raw_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("ps-eth", "ETH", "hyperliquid", "2026-07-14T08:00:00", "2026-07-14T08:01:00", "{}"),
    )

    result = batch_perp_structures(["BTC", "ETH", "SOL"])

    # BTC → newest row
    assert "BTC" in result
    assert result["BTC"]["id"] == "ps-btc-new"
    # ETH → its row
    assert "ETH" in result
    assert result["ETH"]["id"] == "ps-eth"
    # SOL → absent (no data), not a KeyError
    assert "SOL" not in result

    # Consistency: matches individual calls
    assert latest_perp_structure("BTC")["id"] == result["BTC"]["id"]
    assert latest_perp_structure("ETH")["id"] == result["ETH"]["id"]


def test_batch_perp_structures_empty_input(tmp_path, monkeypatch):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.storage.repositories import batch_perp_structures

    assert batch_perp_structures([]) == {}


def test_batch_perp_structures_single_symbol(tmp_path, monkeypatch):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.storage.sqlite import get_db
    from aletheia.storage.repositories import batch_perp_structures, latest_perp_structure

    db = get_db()
    db.execute(
        "INSERT INTO perp_structure_snapshots (id, symbol, source, as_of_time, observed_at, raw_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("ps-sol", "SOL", "hyperliquid", "2026-07-14T09:00:00", "2026-07-14T09:01:00", "{}"),
    )

    result = batch_perp_structures(["SOL"])
    assert result["SOL"]["id"] == "ps-sol"
    assert result["SOL"]["id"] == latest_perp_structure("SOL")["id"]


# ---------------------------------------------------------------------------
# batch_funding_rates
# ---------------------------------------------------------------------------

def test_batch_funding_rates_returns_latest_per_symbol(tmp_path, monkeypatch):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.storage.sqlite import get_db
    from aletheia.storage.repositories import batch_funding_rates, latest_funding_rate

    db = get_db()
    # Three rows for BTC@hyperliquid — batch returns the newest.
    for ts, rid in [
        ("2026-07-12T10:00:00", "fr-btc-1"),
        ("2026-07-13T10:00:00", "fr-btc-2"),
        ("2026-07-14T10:00:00", "fr-btc-3"),
    ]:
        db.execute(
            "INSERT INTO funding_rate_snapshots (id, symbol, exchange, funding_rate, funding_rate_annualized, observed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (rid, "BTC", "hyperliquid", 0.0001, 0.365, ts),
        )
    # One row for ETH@hyperliquid
    db.execute(
        "INSERT INTO funding_rate_snapshots (id, symbol, exchange, funding_rate, funding_rate_annualized, observed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("fr-eth", "ETH", "hyperliquid", 0.0002, 0.73, "2026-07-14T08:00:00"),
    )
    # BTC@bybit — should NOT appear in hyperliquid results
    db.execute(
        "INSERT INTO funding_rate_snapshots (id, symbol, exchange, funding_rate, funding_rate_annualized, observed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("fr-btc-bybit", "BTC", "bybit", 0.0003, 1.095, "2026-07-15T00:00:00"),
    )

    result = batch_funding_rates(["BTC", "ETH", "SOL"], "hyperliquid")

    assert result["BTC"]["id"] == "fr-btc-3"
    assert result["ETH"]["id"] == "fr-eth"
    assert "SOL" not in result

    # Consistency: matches individual calls
    assert latest_funding_rate("BTC", exchange="hyperliquid")["id"] == result["BTC"]["id"]
    assert latest_funding_rate("ETH", exchange="hyperliquid")["id"] == result["ETH"]["id"]


def test_batch_funding_rates_empty_input(tmp_path, monkeypatch):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.storage.repositories import batch_funding_rates

    assert batch_funding_rates([], "hyperliquid") == {}


def test_batch_funding_rates_excludes_other_exchanges(tmp_path, monkeypatch):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.storage.sqlite import get_db
    from aletheia.storage.repositories import batch_funding_rates

    db = get_db()
    db.execute(
        "INSERT INTO funding_rate_snapshots (id, symbol, exchange, funding_rate, funding_rate_annualized, observed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("fr-btc-bybit", "BTC", "bybit", 0.001, 3.65, "2026-07-14T10:00:00"),
    )
    # Request for hyperliquid — should not include bybit row
    result = batch_funding_rates(["BTC"], "hyperliquid")
    assert "BTC" not in result


def test_batch_open_interest_returns_latest_hyperliquid_row(tmp_path, monkeypatch):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.storage.repositories import batch_open_interest
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.executemany(
        "INSERT INTO open_interest_snapshots "
        "(id, symbol, exchange, open_interest_usd, oi_change_1h_pct, observed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("hl-old", "BTC", "hyperliquid", 1_000.0, 1.0, "2026-07-17T08:00:00Z"),
            ("hl-new", "BTC", "hyperliquid", 2_000.0, 2.0, "2026-07-17T09:00:00Z"),
            ("okx-new", "BTC", "okx", 9_000.0, 9.0, "2026-07-17T10:00:00Z"),
        ],
    )
    db.commit()

    result = batch_open_interest(["BTC", "ETH"], "hyperliquid")
    assert result["BTC"]["id"] == "hl-new"
    assert "ETH" not in result
    assert batch_open_interest([], "hyperliquid") == {}


# ---------------------------------------------------------------------------
# batch_news
# ---------------------------------------------------------------------------

def test_batch_news_returns_per_symbol_with_limit(tmp_path, monkeypatch):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.storage.sqlite import get_db
    from aletheia.storage.repositories import batch_news

    db = get_db()
    since = "2026-07-13T00:00:00"
    # Insert 4 BTC articles (limit_per_symbol=3 → only 3 returned)
    for i in range(4):
        db.execute(
            "INSERT INTO news_items "
            "(id, title, source, published_at, symbols_mentioned, status, relevance_score) "
            "VALUES (?, ?, ?, ?, ?, 'analyzed', 0.8)",
            (f"n-btc-{i}", f"BTC news {i}", "coindesk",
             f"2026-07-14T{10+i:02d}:00:00", "BTC,ETH"),
        )
    # One ETH article
    db.execute(
        "INSERT INTO news_items "
        "(id, title, source, published_at, symbols_mentioned, status, relevance_score) "
        "VALUES (?, ?, ?, ?, ?, 'analyzed', 0.8)",
        ("n-eth", "ETH update", "decrypt", "2026-07-14T15:00:00", "ETH"),
    )
    # One old article outside the window (should be excluded)
    db.execute(
        "INSERT INTO news_items "
        "(id, title, source, published_at, symbols_mentioned, status, relevance_score) "
        "VALUES (?, ?, ?, ?, ?, 'analyzed', 0.8)",
        ("n-old", "Old BTC news", "coindesk", "2026-07-10T00:00:00", "BTC"),
    )

    result = batch_news(["BTC", "ETH", "SOL"], since)

    assert len(result["BTC"]) == 3        # capped at limit_per_symbol=3
    assert len(result["ETH"]) == 1        # stored tags cannot override exact title entities
    assert len(result["SOL"]) == 0
    # Old article is excluded
    old_titles = [r["title"] for r in result["BTC"]]
    assert "Old BTC news" not in old_titles


def test_batch_news_empty_input(tmp_path, monkeypatch):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.storage.repositories import batch_news

    assert batch_news([], "2026-07-13T00:00:00") == {}


def test_batch_news_returns_exact_ids_in_newest_first_order(tmp_path, monkeypatch):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.storage.sqlite import get_db
    from aletheia.storage.repositories import batch_news

    db = get_db()
    since = "2026-07-13T00:00:00"
    for i in range(5):
        db.execute(
            "INSERT INTO news_items "
            "(id, title, source, published_at, symbols_mentioned, status, relevance_score) "
            "VALUES (?, ?, ?, ?, ?, 'analyzed', 0.8)",
            (f"n-sol-{i}", f"SOL title {i}", "theblock",
             f"2026-07-14T{10+i:02d}:00:00", "SOL"),
        )

    batch_result = batch_news(["SOL"], since)

    assert [row["id"] for row in batch_result["SOL"]] == [
        "n-sol-4", "n-sol-3", "n-sol-2",
    ]


# ---------------------------------------------------------------------------
# Datetime patchability — simulation clock regression guard
# ---------------------------------------------------------------------------

def test_batch_prefetch_db_news_cutoff_respects_patched_datetime(monkeypatch):
    """_batch_prefetch_db must derive its news cutoff from the module-level
    datetime so simulation harnesses can override it via patch.object.

    A local ``from datetime import datetime as _dt`` inside the function would
    bypass any patch applied to ``aletheia.agents.market_observer.datetime``
    and silently inject future news into historical simulations (lookahead bias).
    This test fails immediately if the local re-import is re-introduced.
    """
    from datetime import datetime, timedelta, timezone
    import aletheia.agents.market_observer as mo
    import aletheia.storage.repositories as repo_mod
    import aletheia.data.freshness as freshness_mod

    fixed_now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    expected_cutoff = (fixed_now - timedelta(hours=48)).isoformat()
    captured: dict = {}

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now.replace(tzinfo=None)

    def fake_batch_news(symbols, since):
        captured["since"] = since
        return {s: [] for s in symbols}

    monkeypatch.setattr(mo, "datetime", FixedDatetime)
    monkeypatch.setattr(repo_mod, "batch_perp_structures", lambda syms: {})
    monkeypatch.setattr(repo_mod, "batch_funding_rates", lambda syms, exch: {})
    monkeypatch.setattr(repo_mod, "batch_news", fake_batch_news)
    monkeypatch.setattr(repo_mod, "latest_onchain_snapshots", lambda **kw: [])
    monkeypatch.setattr(repo_mod, "batch_open_interest", lambda syms, exch: {})
    monkeypatch.setattr(freshness_mod, "fresh_rows", lambda rows, kind: rows)

    mo._batch_prefetch_db(["BTC/USDT"])

    assert captured.get("since") == expected_cutoff, (
        f"news cutoff should derive from patched clock ({expected_cutoff!r}), "
        f"got {captured.get('since')!r} — a local datetime re-import bypasses "
        "patch.object and causes lookahead bias in simulation runs"
    )
