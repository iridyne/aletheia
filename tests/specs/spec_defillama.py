"""Spec: DefiLlama collector — stablecoin mcap, chain TVL, DEX volume.

Claims attach to data_layer_contract.

WARNING — CONFORMANCE COLLECTION TRAP:
Add this file to _import_all_specs() in tests/test_spec_conformance.py.
Without that registration the conformance matrix collects zero DFL-* cases.
"""

from __future__ import annotations

import ast
import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from aletheia.data.collectors.defillama import _DL_STABLECOINS_URL
from tests.specs.spec_meta import SpecClaim, spec_claim

_ROOT = Path(__file__).parent.parent.parent
_DL = _ROOT / "aletheia" / "data" / "collectors" / "defillama.py"
_MIGRATIONS = _ROOT / "aletheia" / "storage" / "migrations.py"
_REPOSITORIES = _ROOT / "aletheia" / "storage" / "repositories.py"

_FIXTURE_STABLE = _ROOT / "tests" / "fixtures" / "dl_stablecoins.json"
_FIXTURE_CHAINS = _ROOT / "tests" / "fixtures" / "dl_chains.json"
_FIXTURE_DEXS = _ROOT / "tests" / "fixtures" / "dl_dexs.json"

_CONTRACT = "data_layer_contract"


# ---------------------------------------------------------------------------
# DFL-S01 — File + class exist
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "DFL-S01", _CONTRACT, "§defillama collector", "L2a",
        "defillama.py exists and exports DefiLlamaCollector with collect()",
    ),
)
def test_defillama_collector_exists():
    assert _DL.exists(), "defillama.py not found"
    from aletheia.data.collectors import defillama as mod
    assert hasattr(mod, "DefiLlamaCollector"), "DefiLlamaCollector not exported"
    assert callable(getattr(mod.DefiLlamaCollector, "collect")), (
        "DefiLlamaCollector.collect must be callable"
    )


# ---------------------------------------------------------------------------
# DFL-S02 — Module-level URL constants
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "DFL-S02", _CONTRACT, "§defillama collector", "L2a",
        "_DL_STABLECOINS_URL, _DL_CHAINS_URL, _DL_DEXS_URL defined in defillama.py",
    ),
)
def test_defillama_url_constants_defined():
    tree = ast.parse(_DL.read_text(encoding="utf-8"))
    assigned: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    assigned.add(tgt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assigned.add(node.target.id)
    for const in ("_DL_STABLECOINS_URL", "_DL_CHAINS_URL", "_DL_DEXS_URL"):
        assert const in assigned, (
            f"defillama.py must define {const}; found: {sorted(assigned)[:20]}"
        )


# ---------------------------------------------------------------------------
# DFL-S03 — collect() return shape
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "DFL-S03", _CONTRACT, "§defillama collector", "L2a",
        "DefiLlamaCollector.collect() returns dict with 'snapshots' and 'errors' keys",
    ),
)
def test_defillama_collect_return_shape():
    src = _DL.read_text(encoding="utf-8")
    for key in ("snapshots", "errors"):
        assert f'"{key}"' in src or f"'{key}'" in src, (
            f"collect() must return dict with key '{key}'"
        )


# ---------------------------------------------------------------------------
# DFL-S04 — migrations.py has onchain_snapshots DDL
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "DFL-S04", _CONTRACT, "§defillama migration", "L2a",
        "migrations.py _DATA_COLLECTORS_DDL contains onchain_snapshots CREATE TABLE DDL",
    ),
)
def test_onchain_snapshots_table_in_schema():
    src = _MIGRATIONS.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS onchain_snapshots" in src, (
        "migrations.py must define onchain_snapshots table in _DATA_COLLECTORS_DDL"
    )


# ---------------------------------------------------------------------------
# DFL-S05 — repositories.py exports insert/query helpers
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "DFL-S05", _CONTRACT, "§defillama repo helpers", "L2a",
        "repositories.py exports insert_onchain_snapshot and latest_onchain_snapshots",
    ),
)
def test_defillama_repo_helpers_exist():
    from aletheia.storage import repositories as repo
    for name in ("insert_onchain_snapshot", "latest_onchain_snapshots"):
        assert callable(getattr(repo, name, None)), (
            f"repositories.py must export callable {name}()"
        )


# ---------------------------------------------------------------------------
# DFL-S06 — stablecoin total from real fixture > 100B USD
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "DFL-S06", _CONTRACT, "§defillama collector", "L2b",
        "Parsing real stablecoin fixture → total_usd > 1e11 (USDT alone is >184B)",
    ),
)
def test_defillama_stablecoin_total_usd():
    assert _FIXTURE_STABLE.exists(), f"Fixture missing: {_FIXTURE_STABLE}"
    raw = json.loads(_FIXTURE_STABLE.read_text(encoding="utf-8"))

    from aletheia.data.collectors.defillama import _fetch_stablecoin_mcap
    import aletheia.data.collectors.defillama as mod
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()

    class _MockResp:
        def __init__(self, data):
            self._data = data
        def raise_for_status(self):
            pass
        def json(self):
            return self._data

    class _MockCtx:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, url, **kwargs):
            return _MockResp(raw)

    original = mod.httpx_client
    def _mock_client(*args, **kwargs):
        return _MockCtx()
    mod.httpx_client = _mock_client
    try:
        snap = asyncio.run(_fetch_stablecoin_mcap(now))
    finally:
        mod.httpx_client = original

    assert snap is not None, "stablecoin_mcap snap must not be None"
    assert snap.total_usd is not None
    assert snap.total_usd > 1e11, (
        f"Total stablecoin mcap should be >$100B from fixture; got {snap.total_usd}"
    )
    assert snap.metric_type == "stablecoin_mcap"
    assert snap.source == "defillama"
    top = json.loads(snap.top_entries)
    assert len(top) > 0, "top_entries must not be empty"
    assert top[0]["symbol"] in ("USDT", "USDC"), (
        f"Top stablecoin should be USDT or USDC; got {top[0]['symbol']}"
    )


# ---------------------------------------------------------------------------
# DFL-S07 — DB write: three metric rows persisted
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "DFL-S07", _CONTRACT, "§defillama db write", "L2b",
        "After collect() with mocked network, three metric_type rows exist in "
        "onchain_snapshots with correct source and non-null total_usd",
    ),
)
def test_defillama_db_write_three_rows(tmp_path, monkeypatch):
    assert _FIXTURE_STABLE.exists(), f"Fixture missing: {_FIXTURE_STABLE}"
    assert _FIXTURE_CHAINS.exists(), f"Fixture missing: {_FIXTURE_CHAINS}"
    assert _FIXTURE_DEXS.exists(), f"Fixture missing: {_FIXTURE_DEXS}"

    raw_stable = json.loads(_FIXTURE_STABLE.read_text(encoding="utf-8"))
    raw_chains = json.loads(_FIXTURE_CHAINS.read_text(encoding="utf-8"))
    raw_dexs = json.loads(_FIXTURE_DEXS.read_text(encoding="utf-8"))

    from aletheia.config import loader as cfg_loader
    monkeypatch.setattr(cfg_loader, "DATA_DIR", tmp_path)
    from aletheia.storage import sqlite as sq
    from aletheia.storage import migrations as mig
    sq.close_db()
    mig.run_migrations()

    import aletheia.data.collectors.defillama as mod

    class _MockResp:
        def __init__(self, data):
            self._data = data
        def raise_for_status(self):
            pass
        def json(self):
            return self._data

    class _MockCtx:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, url, **kwargs):
            if "stablecoins" in url:
                return _MockResp(raw_stable)
            if "chains" in url:
                return _MockResp(raw_chains)
            if "dexs" in url:
                return _MockResp(raw_dexs)
            raise ValueError(f"Unexpected URL: {url}")

    original = mod.httpx_client
    def _mock_client(*args, **kwargs):
        return _MockCtx()
    mod.httpx_client = _mock_client
    try:
        result = asyncio.run(mod.DefiLlamaCollector().collect())
    finally:
        mod.httpx_client = original

    assert not any("db_write" in e for e in result["errors"]), (
        f"DB write must succeed; errors: {result['errors']}"
    )
    assert len(result["snapshots"]) == 3, (
        f"Expected 3 snapshots (stable+tvl+dex); got {len(result['snapshots'])}"
    )

    from aletheia.storage import repositories as repo
    for mt in ("stablecoin_mcap", "chain_tvl", "dex_volume"):
        rows = repo.latest_onchain_snapshots(metric_type=mt)
        assert rows, f"No onchain_snapshots row for metric_type={mt}"
        assert rows[0]["source"] == "defillama"
        assert rows[0]["total_usd"] is not None
        assert float(rows[0]["total_usd"]) > 0, (
            f"metric_type={mt} total_usd must be > 0"
        )

    sq.close_db()


# ---------------------------------------------------------------------------
# DFL-C01 — Independent endpoint isolation
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "DFL-C01", _CONTRACT, "§defillama degraded", "L2c",
        "Each of the three endpoints is isolated; one failing does not prevent "
        "the other two from writing. Result has 2 snapshots and 1 error.",
    ),
)
def test_defillama_one_endpoint_failure_isolated(tmp_path, monkeypatch):
    assert _FIXTURE_CHAINS.exists(), f"Fixture missing: {_FIXTURE_CHAINS}"
    assert _FIXTURE_DEXS.exists(), f"Fixture missing: {_FIXTURE_DEXS}"

    raw_chains = json.loads(_FIXTURE_CHAINS.read_text(encoding="utf-8"))
    raw_dexs = json.loads(_FIXTURE_DEXS.read_text(encoding="utf-8"))

    from aletheia.config import loader as cfg_loader
    monkeypatch.setattr(cfg_loader, "DATA_DIR", tmp_path)
    from aletheia.storage import sqlite as sq
    from aletheia.storage import migrations as mig
    sq.close_db()
    mig.run_migrations()

    import aletheia.data.collectors.defillama as mod

    class _MockResp:
        def __init__(self, data):
            self._data = data
        def raise_for_status(self):
            pass
        def json(self):
            return self._data

    class _MockCtx:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, url, **kwargs):
            if "stablecoins" in url:
                raise RuntimeError("stablecoins endpoint down")
            if "chains" in url:
                return _MockResp(raw_chains)
            if "dexs" in url:
                return _MockResp(raw_dexs)
            raise ValueError(f"Unexpected URL: {url}")

    original = mod.httpx_client
    def _mock_client(*args, **kwargs):
        return _MockCtx()
    mod.httpx_client = _mock_client
    try:
        result = asyncio.run(mod.DefiLlamaCollector().collect())
    finally:
        mod.httpx_client = original

    assert len(result["snapshots"]) == 2, (
        f"Expected 2 snapshots when one endpoint fails; got {len(result['snapshots'])}"
    )
    assert len(result["errors"]) >= 1, "Expected at least 1 error entry"
    assert any("stablecoin_mcap" in e for e in result["errors"]), (
        f"Error for stablecoins not reported; errors: {result['errors']}"
    )

    # The other two metric_types should still be in DB
    from aletheia.storage import repositories as repo
    for mt in ("chain_tvl", "dex_volume"):
        rows = repo.latest_onchain_snapshots(metric_type=mt)
        assert rows, f"No row for {mt} despite that endpoint succeeding"

    sq.close_db()


# ---------------------------------------------------------------------------
# DFL-E2E — Live API (network-gated, marks online)
# ---------------------------------------------------------------------------

@pytest.mark.online
@pytest.mark.asyncio
@spec_claim(
    SpecClaim(
        "DFL-E2E", _CONTRACT, "§defillama live", "L2c",
        "Live DefiLlama API returns three metric snapshots with non-trivial values; "
        "data writes into a real SQLite DB",
    ),
)
async def test_defillama_live_e2e(tmp_path, monkeypatch):
    """Calls the real DefiLlama API; skipped when the API is unreachable."""
    import httpx as _httpx

    # Network reachability gate — skip cleanly if the API is not reachable.
    try:
        async with _httpx.AsyncClient(timeout=5) as _c:
            _r = await _c.get(_DL_STABLECOINS_URL)
            _r.raise_for_status()
    except Exception as _e:
        pytest.skip(f"DefiLlama API unreachable ({type(_e).__name__}): {_e}")

    from aletheia.config import loader as cfg_loader
    monkeypatch.setattr(cfg_loader, "DATA_DIR", tmp_path)
    from aletheia.storage import sqlite as sq
    from aletheia.storage import migrations as mig
    sq.close_db()
    mig.run_migrations()

    from aletheia.data.collectors.defillama import DefiLlamaCollector
    result = await DefiLlamaCollector().collect()

    assert len(result["snapshots"]) == 3, (
        f"Expected 3 metric snapshots from live API; got {len(result['snapshots'])}. "
        f"errors: {result['errors']}"
    )

    # Stablecoin total should be >$100B (USDT alone is >$180B)
    stable_snap = next(
        (s for s in result["snapshots"] if s.metric_type == "stablecoin_mcap"),
        None,
    )
    assert stable_snap is not None
    assert stable_snap.total_usd > 1e11, (
        f"Live stablecoin mcap too small: {stable_snap.total_usd}"
    )

    from aletheia.storage import repositories as repo
    for mt in ("stablecoin_mcap", "chain_tvl", "dex_volume"):
        rows = repo.latest_onchain_snapshots(metric_type=mt)
        assert rows, f"No DB row for {mt} after live collect"

    sq.close_db()
