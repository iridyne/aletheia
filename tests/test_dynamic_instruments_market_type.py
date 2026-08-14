"""Focused regression for #14: _build_dynamic_instruments must honour the source
market_type instead of hardcoding spot, so a futures universe member / open
position collects its futures ticker rather than the spot one.
"""

import aletheia.trading.market_data as md
from aletheia.config.schema import AletheiaConfig


def _cfg():
    cfg = AletheiaConfig()
    cfg.market_data.observed_symbols = []
    cfg.market_data.observed_xstocks = []
    cfg.market_data.ccxt_exchange = "binance"
    return cfg


def test_universe_member_perp_yields_perp_instrument(monkeypatch):
    monkeypatch.setattr(md, "observed_instruments_from_config", lambda cfg: [])
    import aletheia.storage.repositories as repo
    monkeypatch.setattr(
        repo, "get_active_universe_members",
        lambda limit: [{"symbol": "SOL/USDT", "market_type": "perp"}],
    )
    monkeypatch.setattr(repo, "list_all_open_positions", lambda: [])

    insts = md._build_dynamic_instruments(_cfg())
    by_sym = {i.symbol: i.market_type for i in insts}
    assert by_sym.get("SOL/USDT") == "perp", (
        f"perp universe member forced to spot (#14): {by_sym}"
    )


def test_open_futures_position_yields_perp_instrument(monkeypatch):
    monkeypatch.setattr(md, "observed_instruments_from_config", lambda cfg: [])
    import aletheia.storage.repositories as repo
    monkeypatch.setattr(repo, "get_active_universe_members", lambda limit: [])
    # positions store the execution vocabulary "usdt_futures"; InstrumentId needs "perp".
    monkeypatch.setattr(
        repo, "list_all_open_positions",
        lambda: [{"symbol": "BTC/USDT", "market_type": "usdt_futures"}],
    )

    insts = md._build_dynamic_instruments(_cfg())
    by_sym = {i.symbol: i.market_type for i in insts}
    assert by_sym.get("BTC/USDT") == "perp", (
        f"usdt_futures position not mapped to perp instrument (#14): {by_sym}"
    )


def test_spot_and_missing_market_type_default_to_spot(monkeypatch):
    monkeypatch.setattr(md, "observed_instruments_from_config", lambda cfg: [])
    import aletheia.storage.repositories as repo
    monkeypatch.setattr(
        repo, "get_active_universe_members",
        lambda limit: [{"symbol": "ETH/USDT", "market_type": "spot"},
                       {"symbol": "ADA/USDT"}],  # missing → spot default
    )
    monkeypatch.setattr(repo, "list_all_open_positions", lambda: [])

    insts = md._build_dynamic_instruments(_cfg())
    by_sym = {i.symbol: i.market_type for i in insts}
    assert by_sym.get("ETH/USDT") == "spot"
    assert by_sym.get("ADA/USDT") == "spot"


def test_pending_candidate_precedes_config_and_universe_discovery(monkeypatch):
    from aletheia.data import candidate_market_facts
    from aletheia.data.symbols import InstrumentId
    import aletheia.storage.repositories as repo

    monkeypatch.setattr(
        candidate_market_facts,
        "pending_candidate_instruments",
        lambda: [InstrumentId.from_parts("binance", "PEND/USDT", "perp")],
    )
    monkeypatch.setattr(
        md,
        "observed_instruments_from_config",
        lambda cfg: [InstrumentId.from_parts("binance", "CORE/USDT", "spot")],
    )
    monkeypatch.setattr(repo, "list_all_open_positions", lambda: [])
    monkeypatch.setattr(
        repo,
        "get_active_universe_members",
        lambda limit: [{"symbol": "ALT/USDT", "market_type": "spot"}],
    )

    assert [str(item) for item in md._build_dynamic_instruments(_cfg())][:2] == [
        "binance:PEND/USDT:perp",
        "binance:CORE/USDT:spot",
    ]
