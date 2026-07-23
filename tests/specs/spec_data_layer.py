"""Spec: Data Layer Contract (data_layer_contract)

Executable claims for canonical-symbol convention, per-call provider factory,
and provider chain. Implements zero-to-coverage for the data layer trust boundary.

Contract source: aletheia/data/ (ccxt_provider.py, providers.py, symbols.py, cache.py)
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim

_ROOT = Path(__file__).parent.parent.parent
_DATA_PKG = _ROOT / "aletheia" / "data"


# ---------------------------------------------------------------------------
# L2a — Structural
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "DL-S01",
        "data_layer_contract",
        "§2 per-call factory",
        "L2a",
        "get_market_data_provider() exists in aletheia/data/ccxt_provider.py "
        "and is NOT a singleton (no @lru_cache, no module-level cache variable)",
    )
)
def test_get_market_data_provider_is_not_cached():
    """AST-scan ccxt_provider.py: function must exist, must carry no caching decorator."""
    src_path = _DATA_PKG / "ccxt_provider.py"
    assert src_path.exists(), f"Expected {src_path}"

    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    fn_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "get_market_data_provider":
            fn_node = node
            break

    assert fn_node is not None, "get_market_data_provider() not found in ccxt_provider.py"

    # Collect decorator names (handles ast.Name and ast.Attribute).
    decorator_names: list[str] = []
    for dec in fn_node.decorator_list:
        if isinstance(dec, ast.Name):
            decorator_names.append(dec.id)
        elif isinstance(dec, ast.Attribute):
            decorator_names.append(dec.attr)
        elif isinstance(dec, ast.Call):
            func = dec.func
            if isinstance(func, ast.Name):
                decorator_names.append(func.id)
            elif isinstance(func, ast.Attribute):
                decorator_names.append(func.attr)

    caching_decorators = {"lru_cache", "cache", "cached_property"}
    found = caching_decorators & set(decorator_names)
    assert not found, (
        f"get_market_data_provider() must not use caching decorators; found: {found}"
    )

    # Also assert no module-level cache variable that could act as a singleton.
    module_targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    module_targets.add(t.id)

    singleton_names = {"_provider", "_instance", "_singleton", "_cached_provider"}
    found_singletons = singleton_names & module_targets
    assert not found_singletons, (
        f"Module-level singleton variables detected in ccxt_provider.py: {found_singletons}"
    )


@spec_claim(
    SpecClaim(
        "DL-S02",
        "data_layer_contract",
        "§1 provider protocol",
        "L2a",
        "MarketDataProvider Protocol exists in aletheia/data/providers.py "
        "with async get_ticker and async get_ohlcv methods; runtime_checkable",
    )
)
def test_market_data_provider_protocol():
    """Import check: Protocol is runtime_checkable; async methods present by name."""
    from aletheia.data.providers import MarketDataProvider
    from typing import Protocol, runtime_checkable

    # runtime_checkable means isinstance() works structurally.
    assert hasattr(MarketDataProvider, "__protocol_attrs__") or (
        # Python 3.11 uses __protocol_attrs__; older versions expose via _is_protocol
        getattr(MarketDataProvider, "_is_protocol", False)
    ), "MarketDataProvider must be a Protocol"

    # Confirm the two key async methods are declared.
    annotations_source = Path(_DATA_PKG / "providers.py").read_text(encoding="utf-8")
    assert "async def get_ticker" in annotations_source, (
        "MarketDataProvider must declare async get_ticker"
    )
    assert "async def get_ohlcv" in annotations_source, (
        "MarketDataProvider must declare async get_ohlcv"
    )

    # Runtime-checkable: a class with those two methods + close satisfies the Protocol.
    class _FakeProvider:
        async def get_ticker(self, symbol): ...
        async def get_ohlcv(self, symbol, timeframe="1h", limit=100, since=None): ...
        async def close(self): ...

    assert isinstance(_FakeProvider(), MarketDataProvider), (
        "A structurally matching class must satisfy MarketDataProvider via isinstance()"
    )


@spec_claim(
    SpecClaim(
        "DL-S03",
        "data_layer_contract",
        "§1 symbols exports",
        "L2a",
        "aletheia/data/symbols.py exports to_canonical, to_bitget, to_ccxt, InstrumentId",
    )
)
def test_symbols_exports():
    from aletheia.data.symbols import InstrumentId, to_bitget, to_canonical, to_ccxt

    assert callable(to_canonical)
    assert callable(to_bitget)
    assert callable(to_ccxt)
    assert isinstance(InstrumentId, type)


@spec_claim(
    SpecClaim(
        "DL-S06",
        "data_layer_contract",
        "§2 fallback chain",
        "L2a",
        "FallbackChainProvider exists in aletheia/data/sources/chain.py "
        "and is returned by get_market_data_provider() when enable_crypto_fallback is True",
    )
)
def test_fallback_chain_provider_exists_and_is_used():
    """Structural: class exists; source text of factory references FallbackChainProvider."""
    from aletheia.data.sources.chain import FallbackChainProvider

    assert callable(FallbackChainProvider), "FallbackChainProvider must be importable"

    factory_src = (_DATA_PKG / "ccxt_provider.py").read_text(encoding="utf-8")
    assert "FallbackChainProvider" in factory_src, (
        "get_market_data_provider() must reference FallbackChainProvider"
    )
    # The factory must import from the chain submodule.
    assert "from aletheia.data.sources.chain import FallbackChainProvider" in factory_src, (
        "ccxt_provider.py must import FallbackChainProvider from aletheia.data.sources.chain"
    )


@spec_claim(
    SpecClaim(
        "DL-S07",
        "data_layer_contract",
        "§3 no BitgetClient for market data",
        "L2a",
        "No module under aletheia/agents/ or aletheia/server/routes/ "
        "imports BitgetClient except execution-dedicated routes",
    )
)
def test_no_bitget_client_for_market_data():
    """AST-scan all .py files in agents/ and server/routes/; BitgetClient import
    is only permitted in routes/bitget.py (the execution adapter boundary).
    """
    allowed_files = {"bitget.py"}

    violations: list[str] = []
    scan_dirs = [
        _ROOT / "aletheia" / "agents",
        _ROOT / "aletheia" / "server" / "routes",
    ]
    for scan_dir in scan_dirs:
        if not scan_dir.exists():
            continue
        for py_file in scan_dir.glob("*.py"):
            if py_file.name in allowed_files:
                continue
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                # catch: from X import BitgetClient  OR  import BitgetClient
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name == "BitgetClient" or alias.asname == "BitgetClient":
                            violations.append(str(py_file.relative_to(_ROOT)))
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if "BitgetClient" in alias.name:
                            violations.append(str(py_file.relative_to(_ROOT)))

    assert not violations, (
        f"BitgetClient imported outside execution boundary in: {violations}"
    )


@spec_claim(
    SpecClaim(
        "DL-S08",
        "data_layer_contract",
        "§4 ohlcv cache",
        "L2a",
        "aletheia/data/cache.py exists with an in-memory TTL cache (OHLCVCache) "
        "and a process-global get_cache() factory",
    )
)
def test_ohlcv_cache_module_exists():
    """File existence + structural check: OHLCVCache class and get_cache() present."""
    cache_path = _DATA_PKG / "cache.py"
    assert cache_path.exists(), f"Expected {cache_path}"

    tree = ast.parse(cache_path.read_text(encoding="utf-8"))

    class_names = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
    }
    fn_names = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }

    assert "OHLCVCache" in class_names, "OHLCVCache class must exist in cache.py"
    assert "get_cache" in fn_names, "get_cache() factory must exist in cache.py"

    # get() and set() are the required cache interface methods.
    assert "get" in fn_names, "OHLCVCache.get() must be defined"
    assert "set" in fn_names, "OHLCVCache.set() must be defined"


@spec_claim(
    SpecClaim(
        "DL-S09",
        "data_layer_contract",
        "§4 ohlcv instrument identity",
        "L2c",
        "market_ohlcv stores and queries candles by exchange + canonical symbol + "
        "market_type + timeframe + timestamp, so spot/perp and venues cannot overwrite.",
        status="implemented",
    )
)
def test_ohlcv_storage_uses_full_instrument_identity():
    migrations = (_ROOT / "aletheia/storage/migrations.py").read_text(encoding="utf-8")
    repositories = (_ROOT / "aletheia/storage/repositories.py").read_text(encoding="utf-8")
    downloader = (_ROOT / "aletheia/data/history_downloader.py").read_text(encoding="utf-8")

    assert "PRIMARY KEY (exchange, symbol, market_type, timeframe, ts)" in migrations
    assert "market_type" in repositories
    assert "InstrumentId.from_parts" in downloader


@spec_claim(
    SpecClaim(
        "DL-S10",
        "data_layer_contract",
        "§4 active universe ohlcv catch-up",
        "L2c",
        "Active Universe instruments with insufficient OHLCV are repaired by an "
        "independent bounded job using exact exchange + symbol + market_type identity, "
        "durable retry state, failure backoff, and a wall-clock pair timeout that "
        "does not wait for CCXT cancellation cleanup.",
        status="implemented",
    )
)
def test_active_universe_ohlcv_catchup_is_bounded_and_durable():
    migrations = (_ROOT / "aletheia/storage/migrations.py").read_text(encoding="utf-8")
    scheduler = (_ROOT / "aletheia/agent_runtime/scheduler.py").read_text(encoding="utf-8")
    module = (_ROOT / "aletheia/data/active_ohlcv_backfill.py")
    source = module.read_text(encoding="utf-8")

    assert module.exists()
    assert "ohlcv_backfill_state" in migrations
    assert "ohlcv_active_backfill" in scheduler
    assert "active_universe_backfill_max_pairs_per_run" in scheduler
    assert "_wait_without_cancel_drain" in source
    assert "asyncio.shield" in source


# ---------------------------------------------------------------------------
# L2b — Behavioral
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "DL-B04",
        "data_layer_contract",
        "§1 to_canonical BTCUSDT",
        "L2b",
        "to_canonical('BTCUSDT') returns 'BTC/USDT'",
    )
)
def test_to_canonical_btcusdt():
    from aletheia.data.symbols import to_canonical

    assert to_canonical("BTCUSDT") == "BTC/USDT"


@spec_claim(
    SpecClaim(
        "DL-B05",
        "data_layer_contract",
        "§1 to_bitget BTC/USDT",
        "L2b",
        "to_bitget('BTC/USDT') returns 'BTCUSDT'",
    )
)
def test_to_bitget_canonical():
    from aletheia.data.symbols import to_bitget

    assert to_bitget("BTC/USDT") == "BTCUSDT"


# ---------------------------------------------------------------------------
# L2c — Invariants
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "DL-C01",
        "data_layer_contract",
        "§1 round-trip invariant",
        "L2c",
        "to_canonical(to_bitget(x)) == to_canonical(x) for common symbols",
    )
)
def test_symbol_round_trip_invariant():
    from aletheia.data.symbols import to_bitget, to_canonical

    test_symbols = [
        "BTC/USDT",
        "ETH/USDT",
        "BTCUSDT",
        "ETHUSDT",
        "BTC-USDT",
        "btc/usdt",
        "BTC/USDT:USDT",
    ]
    for sym in test_symbols:
        canon = to_canonical(sym)
        bitget_form = to_bitget(sym)
        round_tripped = to_canonical(bitget_form)
        assert round_tripped == canon, (
            f"Round-trip failed for {sym!r}: "
            f"to_canonical={canon!r}, to_bitget={bitget_form!r}, "
            f"to_canonical(to_bitget)={round_tripped!r}"
        )


@spec_claim(
    SpecClaim(
        "DL-C02",
        "data_layer_contract",
        "§1 InstrumentId canonical enforcement",
        "L2c",
        "InstrumentId rejects non-canonical symbol strings",
    )
)
def test_instrument_id_rejects_non_canonical():
    import pytest
    from aletheia.data.symbols import InstrumentId

    # These must raise ValueError (no slash in symbol).
    bad_symbols = ["BTCUSDT", "BTC-USDT", "btcusdt"]
    for sym in bad_symbols:
        try:
            InstrumentId(exchange="bitget", symbol=sym, market_type="spot")
            raise AssertionError(
                f"InstrumentId should have rejected non-canonical symbol {sym!r}"
            )
        except ValueError:
            pass  # expected

    # Canonical form must succeed.
    iid = InstrumentId(exchange="bitget", symbol="BTC/USDT", market_type="spot")
    assert iid.symbol == "BTC/USDT"


@spec_claim(
    SpecClaim(
        "DL-C03",
        "data_layer_contract",
        "§1 InstrumentId-aware canonicalisation",
        "L2c",
        "canonical_of(InstrumentId) returns the instrument's canonical symbol, "
        "never str(iid) fed through to_canonical (which collapses to the exchange name)",
    )
)
def test_canonical_of_instrument_id_does_not_collapse_to_exchange():
    from aletheia.data.symbols import InstrumentId, canonical_of, to_canonical

    iid = InstrumentId(exchange="okx", symbol="AAVE/USDT", market_type="spot")
    # The trap the helper exists to avoid: stringify-then-canonicalise yields "OKX".
    assert to_canonical(str(iid)) == "OKX"
    # The contract: canonical_of returns the real canonical symbol.
    assert canonical_of(iid) == "AAVE/USDT"
    # Plain strings still pass through to_canonical unchanged.
    assert canonical_of("BTCUSDT") == "BTC/USDT"
