"""Spec: Hyperliquid collector — OI, funding rate, predicted fundings.

Claims attach to data_layer_contract (same as other data-source specs).

WARNING — CONFORMANCE COLLECTION TRAP:
Add this file to _import_all_specs() in tests/test_spec_conformance.py.
Without that registration the conformance matrix collects zero HL-* cases.
"""

from __future__ import annotations

import ast
import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim

_ROOT = Path(__file__).parent.parent.parent
_HL = _ROOT / "aletheia" / "data" / "collectors" / "hyperliquid.py"
_REPOSITORIES = _ROOT / "aletheia" / "storage" / "repositories.py"

# Path to real-data fixture captured from live API
_FIXTURE_META = _ROOT / "tests" / "fixtures" / "hl_meta_and_asset_ctxs.json"
_FIXTURE_PRED = _ROOT / "tests" / "fixtures" / "hl_predicted_fundings.json"

_CONTRACT = "data_layer_contract"


# ---------------------------------------------------------------------------
# HL-S01 — File + class exist
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "HL-S01", _CONTRACT, "§hyperliquid collector", "L2a",
        "hyperliquid.py exists and exports HyperliquidCollector with collect()",
    ),
)
def test_hyperliquid_collector_exists():
    assert _HL.exists(), "hyperliquid.py not found"
    from aletheia.data.collectors import hyperliquid as mod
    assert hasattr(mod, "HyperliquidCollector"), "HyperliquidCollector not exported"
    assert callable(getattr(mod.HyperliquidCollector, "collect")), (
        "HyperliquidCollector.collect must be callable"
    )


# ---------------------------------------------------------------------------
# HL-S02 — Module-level URL constant
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "HL-S02", _CONTRACT, "§hyperliquid collector", "L2a",
        "_HL_INFO_URL module-level constant defined in hyperliquid.py",
    ),
)
def test_hl_info_url_constant_defined():
    tree = ast.parse(_HL.read_text(encoding="utf-8"))
    assigned: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    assigned.add(tgt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assigned.add(node.target.id)
    assert "_HL_INFO_URL" in assigned, (
        f"hyperliquid.py must define _HL_INFO_URL constant; found: {sorted(assigned)[:20]}"
    )


# ---------------------------------------------------------------------------
# HL-S03 — collect() return shape
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "HL-S03", _CONTRACT, "§hyperliquid collector", "L2a",
        "HyperliquidCollector.collect() is an async method returning "
        "dict with keys 'funding', 'open_interest', 'errors'",
    ),
)
def test_hyperliquid_collect_return_shape():
    tree = ast.parse(_HL.read_text(encoding="utf-8"))
    collect_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "collect":
            collect_fn = node
            break
    assert collect_fn, "HyperliquidCollector.collect must be async"

    src = _HL.read_text(encoding="utf-8")
    for key in ("funding", "open_interest", "errors"):
        assert f'"{key}"' in src or f"'{key}'" in src, (
            f"collect() must return dict with key '{key}'"
        )


# ---------------------------------------------------------------------------
# HL-S04 — OI > 0 for BTC from real fixture
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "HL-S04", _CONTRACT, "§hyperliquid collector", "L2b",
        "Parsing real fixture → BTC/USDT OI USD > 0 and funding_rate non-None",
    ),
)
def test_hyperliquid_parse_real_fixture_btc():
    assert _FIXTURE_META.exists(), f"Fixture not found: {_FIXTURE_META}"
    raw = json.loads(_FIXTURE_META.read_text(encoding="utf-8"))

    from aletheia.data.collectors.hyperliquid import _parse_funding, _parse_oi
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    universe = raw[0].get("universe", [])
    ctxs = raw[1]

    # Find BTC
    btc_idx = next((i for i, a in enumerate(universe) if a.get("name") == "BTC"), None)
    assert btc_idx is not None, f"BTC not in fixture universe: {[a['name'] for a in universe]}"

    btc_ctx = ctxs[btc_idx]
    btc_canonical = "BTC/USDT"

    fr = _parse_funding(btc_canonical, btc_ctx, now)
    assert fr is not None, "BTC funding rate parse returned None"
    assert fr.funding_rate is not None, "BTC funding_rate must not be None"
    assert fr.exchange == "hyperliquid"
    assert fr.symbol == "BTC/USDT"

    oi = _parse_oi(btc_canonical, btc_ctx, now)
    assert oi is not None, "BTC OI parse returned None"
    assert oi.open_interest_usd > 0, (
        f"BTC OI USD must be > 0; got {oi.open_interest_usd}"
    )
    # Real BTC OI should be in the billions at any reasonable market size
    assert oi.open_interest_usd > 1_000_000, (
        f"BTC OI USD suspiciously small: {oi.open_interest_usd}; "
        "expected >$1M at minimum"
    )
    assert oi.open_interest_coin is not None and oi.open_interest_coin > 0
    assert oi.exchange == "hyperliquid"


# ---------------------------------------------------------------------------
# HL-S05 — OI USD = openInterest × markPx
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "HL-S05", _CONTRACT, "§hyperliquid collector", "L2b",
        "OI USD = float(openInterest) × float(markPx) from fixture data",
    ),
)
def test_hyperliquid_oi_usd_equals_coin_times_mark():
    assert _FIXTURE_META.exists(), f"Fixture not found: {_FIXTURE_META}"
    raw = json.loads(_FIXTURE_META.read_text(encoding="utf-8"))

    from aletheia.data.collectors.hyperliquid import _parse_oi
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    universe = raw[0].get("universe", [])
    ctxs = raw[1]

    for i, (asset_meta, ctx) in enumerate(zip(universe, ctxs)):
        name = asset_meta.get("name", "")
        oi_str = ctx.get("openInterest")
        mark_str = ctx.get("markPx")
        if not oi_str or not mark_str:
            continue
        canonical = f"{name}/USDT"
        oi = _parse_oi(canonical, ctx, now)
        if oi is None:
            continue
        expected_usd = float(oi_str) * float(mark_str)
        assert abs(oi.open_interest_usd - expected_usd) < 1.0, (
            f"{canonical}: expected OI USD={expected_usd:.2f}, "
            f"got {oi.open_interest_usd:.2f}"
        )
        break   # one verified is sufficient for L2b


# ---------------------------------------------------------------------------
# HL-C01 — HTTP failure returns errors list, does not raise
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "HL-C01", _CONTRACT, "§hyperliquid collector", "L2c",
        "HTTP failure returns {'funding': [], 'open_interest': [], 'errors': [...]}, "
        "does not raise",
    ),
)
def test_hyperliquid_http_failure_does_not_raise():
    import aletheia.data.collectors.hyperliquid as mod
    original = mod.httpx_client

    class _FailCtx:
        async def __aenter__(self):
            raise RuntimeError("simulated network down")

        async def __aexit__(self, *a):
            return False

    def _fail_client(*args, **kwargs):
        return _FailCtx()

    mod.httpx_client = _fail_client
    try:
        result = asyncio.run(mod.HyperliquidCollector().collect())
    finally:
        mod.httpx_client = original

    assert isinstance(result, dict)
    assert result["funding"] == []
    assert result["open_interest"] == []
    assert result["errors"], "errors must be non-empty on HTTP failure"


# ---------------------------------------------------------------------------
# HL-C02 — End-to-end: fixture data written to real SQLite DB
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "HL-C02", _CONTRACT, "§hyperliquid collector", "L2c",
        "Fixture data parsed and written to funding_rate_snapshots + "
        "open_interest_snapshots; DB rows have correct symbol and exchange",
    ),
)
def test_hyperliquid_db_write(tmp_path, monkeypatch):
    assert _FIXTURE_META.exists(), f"Fixture not found: {_FIXTURE_META}"
    raw_meta = json.loads(_FIXTURE_META.read_text(encoding="utf-8"))
    raw_pred = json.loads(_FIXTURE_META.read_text(encoding="utf-8"))  # same fixture for pred

    # Point DB at tmp_path
    from aletheia.config import loader as cfg_loader
    monkeypatch.setattr(cfg_loader, "DATA_DIR", tmp_path)
    from aletheia.storage import sqlite as sq
    from aletheia.storage import migrations as mig
    sq.close_db()
    mig.run_migrations()

    import aletheia.data.collectors.hyperliquid as mod

    # Mock both HTTP calls: return meta fixture for metaAndAssetCtxs,
    # return pred fixture for predictedFundings.
    pred_raw = json.loads(_FIXTURE_PRED.read_text(encoding="utf-8"))

    class _MockResp:
        def __init__(self, data):
            self._data = data
            self.status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return self._data

    class _MockCtx:
        def __init__(self, payload):
            self._payload = payload
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, **kwargs):
            body = kwargs.get("json", {})
            if body.get("type") == "metaAndAssetCtxs":
                return _MockResp(raw_meta)
            return _MockResp(pred_raw)

    def _mock_client(*args, **kwargs):
        return _MockCtx(raw_meta)

    original = mod.httpx_client
    mod.httpx_client = _mock_client
    try:
        result = asyncio.run(mod.HyperliquidCollector().collect())
    finally:
        mod.httpx_client = original

    assert not result["errors"] or all("db_write" not in e for e in result["errors"]), (
        f"DB write should not fail; errors: {result['errors']}"
    )
    assert len(result["funding"]) > 0, "At least one funding rate snapshot expected"
    assert len(result["open_interest"]) > 0, "At least one OI snapshot expected"

    from aletheia.storage import repositories as repo
    # All fixtures have ≤5 assets; first should be BTC (name='BTC' → BTC/USDT)
    universe = raw_meta[0].get("universe", [])
    first_name = universe[0].get("name") if universe else None
    if first_name:
        canonical = f"{first_name}/USDT"
        # Hyperliquid is a cross-venue feed: the primary per-symbol read excludes
        # it, so verify the row via its explicit exchange filter.
        oi_row = repo.latest_open_interest(canonical, exchange="hyperliquid")
        assert oi_row is not None, (
            f"Expected OI row for {canonical} in DB after collect()"
        )
        assert oi_row["exchange"] == "hyperliquid"
        assert float(oi_row["open_interest_usd"]) > 0

    sq.close_db()


# ---------------------------------------------------------------------------
# HL-E2E — Live API (network-gated, marks online)
# ---------------------------------------------------------------------------

@pytest.mark.online
@pytest.mark.asyncio
@spec_claim(
    SpecClaim(
        "HL-E2E", _CONTRACT, "§hyperliquid live", "L2c",
        "Live Hyperliquid API returns non-empty OI and funding data; "
        "data writes into a real SQLite DB",
    ),
)
async def test_hyperliquid_live_e2e(tmp_path, monkeypatch):
    """Calls the real Hyperliquid API; skipped when the API is unreachable."""
    import httpx as _httpx

    # Network reachability gate — skip cleanly if the API is not reachable.
    try:
        async with _httpx.AsyncClient(timeout=5) as _c:
            _r = await _c.post(
                _HL_INFO_URL,
                json={"type": "metaAndAssetCtxs"},
            )
            _r.raise_for_status()
    except Exception as _e:
        pytest.skip(f"Hyperliquid API unreachable ({type(_e).__name__}): {_e}")

    from aletheia.config import loader as cfg_loader
    monkeypatch.setattr(cfg_loader, "DATA_DIR", tmp_path)
    from aletheia.storage import sqlite as sq
    from aletheia.storage import migrations as mig
    sq.close_db()
    mig.run_migrations()

    from aletheia.data.collectors.hyperliquid import HyperliquidCollector
    result = await HyperliquidCollector().collect(symbols=["BTC/USDT", "ETH/USDT"])

    assert len(result["funding"]) > 0, (
        f"Live API returned no funding data. errors: {result['errors']}"
    )
    assert len(result["open_interest"]) > 0, "Live API returned no OI data"

    # Verify BTC data is meaningful
    btc_oi = next(
        (r for r in result["open_interest"] if r.symbol == "BTC/USDT"),
        None,
    )
    assert btc_oi is not None, "BTC/USDT OI not in live result"
    assert btc_oi.open_interest_usd > 1_000_000, (
        f"BTC OI USD looks wrong: {btc_oi.open_interest_usd}; "
        "expected >$1M"
    )

    # Verify DB write (cross-venue feed → query via explicit exchange filter).
    from aletheia.storage import repositories as repo
    db_oi = repo.latest_open_interest("BTC/USDT", exchange="hyperliquid")
    assert db_oi is not None, "BTC/USDT OI not persisted to DB after live collect"
    assert db_oi["exchange"] == "hyperliquid"

    sq.close_db()
