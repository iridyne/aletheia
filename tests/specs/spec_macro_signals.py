"""Spec: Macro Signals — Round 2 zero-cost data integration.

Covers structural and invariant claims for the five new data sources:
  Step 1 — Binance Long/Short Ratio (MS-S01..S03, MS-C01)
  Step 2 — Alternative.me Fear & Greed (MS-S04..S07, MS-C02, MS-C07)
  Step 3 — CoinGecko Top-N markets (MS-S08..S12, MS-C03..C04)
  Step 4 — Historical funding/OI backfill (MS-S13, MS-C05..C06)
  + Settings UI coupling (MS-S14) and prompt-stability marker (MS-S15)

All claims attach to ``data_layer_contract`` (reused — not a new trust boundary).

WARNING — CONFORMANCE COLLECTION TRAP ([[aletheia-sdd-spec-collection-trap]]):
This file is registered in tests/test_spec_conformance.py via the
``_import_all_specs()`` import list. Without that registration the conformance
matrix collects zero MS-* cases despite this file existing on disk. Run
``pytest tests/specs/spec_macro_signals.py --collect-only -q`` as a canary
before declaring any Step done.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim

_ROOT = Path(__file__).parent.parent.parent
_DERIVATIVES = _ROOT / "aletheia" / "data" / "collectors" / "derivatives.py"
_MACRO = _ROOT / "aletheia" / "data" / "collectors" / "macro.py"
_CG_TOP = _ROOT / "aletheia" / "data" / "collectors" / "coingecko_top.py"
_MODELS = _ROOT / "aletheia" / "data" / "models.py"
_MIGRATIONS = _ROOT / "aletheia" / "storage" / "migrations.py"
_REPOSITORIES = _ROOT / "aletheia" / "storage" / "repositories.py"
_SCHEDULER = _ROOT / "aletheia" / "agent_runtime" / "scheduler.py"
_CONTEXT_BUILDER = _ROOT / "aletheia" / "data" / "context_builder.py"
_MARKET_OBSERVER = _ROOT / "aletheia" / "agents" / "market_observer.py"
_SETTINGS_TSX = _ROOT / "frontend" / "src" / "pages" / "Settings.tsx"

_CONTRACT = "data_layer_contract"


# ---------------------------------------------------------------------------
# Step 1 — Binance Long/Short Ratio
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "MS-S01", _CONTRACT, "§3.1 LSR collector", "L2a",
        "derivatives.py defines _BINANCE_LSR_URL module-level constant",
    ),
)
def test_lsr_url_constant_defined():
    """AST walk: _BINANCE_LSR_URL must be an Assign at module scope (not a comment match)."""
    tree = ast.parse(_DERIVATIVES.read_text(encoding="utf-8"))
    assigned: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    assigned.add(tgt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assigned.add(node.target.id)
    assert "_BINANCE_LSR_URL" in assigned, (
        "derivatives.py must define _BINANCE_LSR_URL module-level constant; "
        f"found assignments: {sorted(assigned)[:20]}"
    )


@spec_claim(
    SpecClaim(
        "MS-S02", _CONTRACT, "§3.1 LSR write path", "L2a",
        "_parse_oi() accepts lsr parameter and threads it into "
        "OpenInterestSnapshot.long_short_ratio at construction time "
        "(single insert, no follow-up UPDATE)",
    ),
)
def test_parse_oi_threads_lsr_at_construction():
    """AST: _parse_oi signature has lsr kwarg AND OpenInterestSnapshot()
    constructor call inside _parse_oi passes long_short_ratio=lsr.
    """
    tree = ast.parse(_DERIVATIVES.read_text(encoding="utf-8"))
    parse_oi_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_parse_oi":
            parse_oi_fn = node
            break
    assert parse_oi_fn, "_parse_oi function not found"

    arg_names = {a.arg for a in parse_oi_fn.args.args} | {
        a.arg for a in parse_oi_fn.args.kwonlyargs
    }
    assert "lsr" in arg_names, (
        f"_parse_oi must accept an 'lsr' parameter; got: {sorted(arg_names)}"
    )

    found_construction = False
    for node in ast.walk(parse_oi_fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "OpenInterestSnapshot"
        ):
            kwargs = {kw.arg for kw in node.keywords}
            assert "long_short_ratio" in kwargs, (
                "OpenInterestSnapshot() inside _parse_oi must include "
                f"long_short_ratio kwarg; got: {sorted(kwargs)}"
            )
            found_construction = True
    assert found_construction, "No OpenInterestSnapshot() construction inside _parse_oi"


@spec_claim(
    SpecClaim(
        "MS-S03", _CONTRACT, "§3.1 derivatives render", "L2a",
        "context_builder.render_enriched_context_md derivatives table header "
        "contains an 'LSR' column",
    ),
)
def test_derivatives_table_has_lsr_column():
    src = _CONTEXT_BUILDER.read_text(encoding="utf-8")
    # Header must contain "| LSR |" literally (the column separator pattern).
    assert "| LSR |" in src, (
        "context_builder.py derivatives table header must contain '| LSR |' column; "
        "without it the LLM never sees the ratio."
    )


@spec_claim(
    SpecClaim(
        "MS-C01", _CONTRACT, "§3.1 LSR degraded", "L2c",
        "LSR batch fetch failure does NOT prevent OpenInterestSnapshot "
        "persistence; the OI write path runs regardless and lsr_map.get(sym) "
        "yields None for missing entries",
    ),
)
def test_lsr_failure_does_not_block_oi():
    """AST: in DerivativesCollector.collect(), the try/except wrapping the
    _fetch_lsr_binance_batch call must NOT contain the OI write loop —
    i.e. the for-loop over symbols must live AFTER and OUTSIDE the LSR try block,
    so an exception in LSR fetch leaves lsr_map empty and OI inserts proceed.
    """
    tree = ast.parse(_DERIVATIVES.read_text(encoding="utf-8"))
    collect_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "collect":
            collect_fn = node
            break
    assert collect_fn, "DerivativesCollector.collect not found"

    # Find the try block that contains _fetch_lsr_binance_batch call.
    lsr_try_node = None
    for node in ast.walk(collect_fn):
        if isinstance(node, ast.Try):
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Name)
                    and sub.func.id == "_fetch_lsr_binance_batch"
                ):
                    lsr_try_node = node
                    break
                if (
                    isinstance(sub, ast.Await)
                    and isinstance(sub.value, ast.Call)
                    and isinstance(sub.value.func, ast.Name)
                    and sub.value.func.id == "_fetch_lsr_binance_batch"
                ):
                    lsr_try_node = node
                    break
            if lsr_try_node:
                break
    assert lsr_try_node, (
        "DerivativesCollector.collect must wrap _fetch_lsr_binance_batch in a try/except "
        "so LSR failure cannot escape and abort the OI write loop."
    )

    # The OI-write loop must NOT appear inside lsr_try_node.body.
    for sub in ast.walk(lsr_try_node):
        if isinstance(sub, ast.For):
            # If a for-loop appears inside the LSR try, it must NOT contain insert_open_interest_snapshot.
            for inner in ast.walk(sub):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id == "insert_open_interest_snapshot"
                ):
                    pytest.fail(
                        "OI write path lives inside the LSR try/except — "
                        "an LSR failure would skip OI persistence. Move the OI loop outside."
                    )


# ---------------------------------------------------------------------------
# Step 2 — Alternative.me Fear & Greed
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "MS-S04", _CONTRACT, "§3.2 F&G collector", "L2a",
        "macro.py defines _ALT_FNG_URL module-level constant",
    ),
)
def test_fng_url_constant_defined():
    tree = ast.parse(_MACRO.read_text(encoding="utf-8"))
    assigned: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    assigned.add(tgt.id)
    assert "_ALT_FNG_URL" in assigned, (
        f"macro.py must define _ALT_FNG_URL; found: {sorted(assigned)[:20]}"
    )


@spec_claim(
    SpecClaim(
        "MS-S05", _CONTRACT, "§3.2 schema", "L2a",
        "MacroSnapshot Pydantic model has fng_value (int|None) and "
        "fng_classification (str|None) fields",
    ),
)
def test_macro_snapshot_has_fng_fields():
    from aletheia.data.models import MacroSnapshot
    fields = MacroSnapshot.model_fields
    assert "fng_value" in fields, f"MacroSnapshot missing fng_value; have: {list(fields)}"
    assert "fng_classification" in fields, f"MacroSnapshot missing fng_classification"


@spec_claim(
    SpecClaim(
        "MS-S06", _CONTRACT, "§3.2 migration", "L2a",
        "migrations.py _ADDITIVE_COLUMNS list contains "
        "('fng_value', 'macro_snapshots', 'INTEGER') and "
        "('fng_classification', 'macro_snapshots', 'TEXT') tuples",
    ),
)
def test_additive_columns_has_fng():
    src = _MIGRATIONS.read_text(encoding="utf-8")
    # Two tuples must appear inside _ADDITIVE_COLUMNS — string-match is sufficient
    # because the list literal is the only place those tuples can live.
    assert '("fng_value", "macro_snapshots", "INTEGER")' in src, (
        "migrations.py must register fng_value in _ADDITIVE_COLUMNS"
    )
    assert '("fng_classification", "macro_snapshots", "TEXT")' in src, (
        "migrations.py must register fng_classification in _ADDITIVE_COLUMNS"
    )


@spec_claim(
    SpecClaim(
        "MS-S07", _CONTRACT, "§3.2 macro render", "L2a",
        "render_enriched_context_md outputs a 'Fear & Greed:' line when "
        "MacroSnapshot.fng_value is non-None",
    ),
)
def test_fng_render_when_present():
    from aletheia.data.context_builder import render_enriched_context_md
    from aletheia.data.models import MacroSnapshot
    snap = MacroSnapshot(fng_value=15, fng_classification="Extreme Fear",
                          observed_at="2026-06-27T00:00:00Z")
    out = render_enriched_context_md(["BTC/USDT"], {}, {}, snap)
    assert "Fear & Greed:" in out, (
        f"render_enriched_context_md must include 'Fear & Greed:' when fng_value set; got: {out!r}"
    )
    assert "15" in out, "render output must contain the fng value"


@spec_claim(
    SpecClaim(
        "MS-C02", _CONTRACT, "§3.2 F&G degraded", "L2c",
        "MacroCollector.collect() isolates _fetch_fng failure — other macro "
        "fields (btc_dominance/total_cap/vix/...) populate via independent paths "
        "and fng_value remains None without raising",
    ),
)
def test_fng_failure_isolated_from_other_fields():
    """AST: the _fetch_fng call must live inside a try/except block whose
    body does NOT contain calls to _fetch_coingecko or _fetch_fred or _fetch_tradfi.
    """
    tree = ast.parse(_MACRO.read_text(encoding="utf-8"))
    collect_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "collect":
            collect_fn = node
            break
    assert collect_fn, "MacroCollector.collect not found"

    found_isolated_try = False
    for node in ast.walk(collect_fn):
        if not isinstance(node, ast.Try):
            continue
        calls_inside = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                calls_inside.add(sub.func.id)
        if "_fetch_fng" in calls_inside:
            forbidden = {"_fetch_coingecko", "_fetch_fred", "_fetch_tradfi"}
            assert not (calls_inside & forbidden), (
                f"_fetch_fng try block must not contain {forbidden & calls_inside} "
                "— other field collection must be in separate try blocks"
            )
            found_isolated_try = True
    assert found_isolated_try, (
        "_fetch_fng must be wrapped in its OWN try/except so its failure cannot "
        "regress other macro fields (per MS-C02 invariant)."
    )


# ---------------------------------------------------------------------------
# Step 3 — CoinGecko Top-N markets
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "MS-S08", _CONTRACT, "§3.3 CoinGecko collector", "L2a",
        "aletheia/data/collectors/coingecko_top.py exists and exports CoinGeckoTopCollector",
    ),
)
def test_coingecko_top_collector_exists():
    assert _CG_TOP.exists(), "coingecko_top.py not found"
    from aletheia.data.collectors import coingecko_top as mod
    assert hasattr(mod, "CoinGeckoTopCollector")
    assert callable(getattr(mod.CoinGeckoTopCollector, "collect"))


@spec_claim(
    SpecClaim(
        "MS-S09", _CONTRACT, "§3.3 scheduler", "L2a",
        "scheduler.py Phase B references CoinGeckoTopCollector within the "
        "data_collectors job body",
    ),
)
def test_scheduler_registers_coingecko_top():
    src = _SCHEDULER.read_text(encoding="utf-8")
    assert "CoinGeckoTopCollector" in src, (
        "scheduler.py must reference CoinGeckoTopCollector in Phase B"
    )


@spec_claim(
    SpecClaim(
        "MS-S10", _CONTRACT, "§3.3 migration", "L2a",
        "migrations.py SCHEMA_SQL contains CREATE TABLE IF NOT EXISTS "
        "coingecko_top_snapshots DDL",
    ),
)
def test_coingecko_top_table_in_schema():
    src = _MIGRATIONS.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS coingecko_top_snapshots" in src, (
        "migrations.py must define coingecko_top_snapshots table"
    )


@spec_claim(
    SpecClaim(
        "MS-S11", _CONTRACT, "§3.3 repo helpers", "L2a",
        "repositories.py exports insert_coingecko_top_rows, latest_coingecko_top, "
        "and prune_coingecko_top_snapshots",
    ),
)
def test_coingecko_top_repo_helpers_exist():
    from aletheia.storage import repositories as repo
    for name in ("insert_coingecko_top_rows", "latest_coingecko_top",
                  "prune_coingecko_top_snapshots"):
        assert callable(getattr(repo, name, None)), (
            f"repositories.py must export callable {name}()"
        )


@spec_claim(
    SpecClaim(
        "MS-S12", _CONTRACT, "§3.3 bulk pattern", "L2a",
        "insert_coingecko_top_rows uses canonical BEGIN/COMMIT bulk pattern "
        "(not per-row autocommit storm under sqlite.py isolation_level=None)",
    ),
)
def test_coingecko_top_bulk_uses_begin_commit():
    src = _REPOSITORIES.read_text(encoding="utf-8")
    # Locate the function body — grab from def to next def or module end.
    idx = src.find("def insert_coingecko_top_rows")
    assert idx > 0, "insert_coingecko_top_rows not found"
    next_def = src.find("\ndef ", idx + 1)
    body = src[idx : next_def if next_def > 0 else len(src)]
    # Must contain an explicit BEGIN; otherwise it's a per-row commit storm under autocommit.
    assert 'db.execute("BEGIN")' in body or "db.execute('BEGIN')" in body, (
        "insert_coingecko_top_rows must use explicit db.execute(\"BEGIN\") for bulk "
        "atomicity. Per-row _insert() under isolation_level=None is an N-commit storm."
    )


@spec_claim(
    SpecClaim(
        "MS-C03", _CONTRACT, "§3.3 CoinGecko degraded", "L2c",
        "CoinGeckoTopCollector.collect() returns {rows:[], errors:[...]} on "
        "httpx error without raising",
    ),
)
def test_coingecko_top_degraded_does_not_raise():
    import asyncio

    from aletheia.data.collectors.coingecko_top import CoinGeckoTopCollector

    async def run():
        # Monkeypatch httpx_client to raise inside the body.
        import aletheia.data.collectors.coingecko_top as mod
        original = mod.httpx_client

        class FailingCtx:
            async def __aenter__(self):
                raise RuntimeError("simulated network failure")

            async def __aexit__(self, *a):
                return False

        def fake_client(*args, **kwargs):
            return FailingCtx()

        mod.httpx_client = fake_client
        try:
            result = await CoinGeckoTopCollector().collect(top_n=10)
        finally:
            mod.httpx_client = original
        return result

    result = asyncio.run(run())
    assert isinstance(result, dict), "collect() must return a dict even on failure"
    assert result.get("rows") == [], (
        f"rows must be empty on failure; got: {result.get('rows')}"
    )
    assert result.get("errors"), "errors list must be non-empty on failure"


@spec_claim(
    SpecClaim(
        "MS-C04", _CONTRACT, "§3.3 latest_top batch isolation", "L2c",
        "latest_coingecko_top(N) returns N rows that all share a single "
        "observed_at value — no cross-batch mixing",
    ),
)
def test_latest_coingecko_top_pins_to_single_batch(tmp_path, monkeypatch):
    """Insert two distinct batches into a fresh DB and ensure latest_coingecko_top
    returns only rows from the more recent batch.
    """
    # Point the data dir at tmp_path so a fresh app.db is created.
    from aletheia.config import loader as cfg_loader
    monkeypatch.setattr(cfg_loader, "DATA_DIR", tmp_path)
    from aletheia.storage import sqlite as sq
    from aletheia.storage import repositories as repo
    from aletheia.storage import migrations as mig
    sq.close_db()  # drop any cached connection bound to the production DB

    mig.run_migrations()  # no-arg — picks up DATA_DIR via cfg_loader

    # Two batches with distinct observed_at.
    batch_a = [
        {"coin_id": f"a{i}", "symbol": f"A{i}", "name": "A", "price_usd": 1.0,
         "market_cap_rank": i, "observed_at": "2026-06-27T01:00:00+00:00"}
        for i in range(1, 6)
    ]
    batch_b = [
        {"coin_id": f"b{i}", "symbol": f"B{i}", "name": "B", "price_usd": 1.0,
         "market_cap_rank": i, "observed_at": "2026-06-27T02:00:00+00:00"}
        for i in range(1, 6)
    ]
    repo.insert_coingecko_top_rows(batch_a)
    repo.insert_coingecko_top_rows(batch_b)

    rows = repo.latest_coingecko_top(top_n=10)
    assert rows, "latest_coingecko_top returned empty"
    observed_ats = {r["observed_at"] for r in rows}
    assert len(observed_ats) == 1, (
        f"latest_coingecko_top must return a single observed_at batch; "
        f"got mixed: {observed_ats}"
    )
    # And it should be the newer one.
    assert "02:00:00" in next(iter(observed_ats)), (
        "latest_coingecko_top must return the MOST RECENT batch"
    )
    sq.close_db()


# ---------------------------------------------------------------------------
# Step 4 — Historical funding / OI backfill
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "MS-S13", _CONTRACT, "§3.4 history backfill", "L2a",
        "derivatives.py defines _maybe_backfill_funding_history and "
        "_maybe_backfill_oi_history async functions",
    ),
)
def test_backfill_functions_defined():
    tree = ast.parse(_DERIVATIVES.read_text(encoding="utf-8"))
    async_fns = {
        node.name for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
    }
    for name in ("_maybe_backfill_funding_history", "_maybe_backfill_oi_history"):
        assert name in async_fns, (
            f"derivatives.py must define async {name}; have async fns: {sorted(async_fns)}"
        )


@spec_claim(
    SpecClaim(
        "MS-C05", _CONTRACT, "§3.4 backfill idempotency", "L2c",
        "_maybe_backfill_funding_history returns 0 when ≥3 rows already exist "
        "in the since_hours window (idempotent re-runs)",
    ),
)
def test_backfill_idempotent_with_existing_rows(tmp_path, monkeypatch):
    import asyncio

    from aletheia.config import loader as cfg_loader
    monkeypatch.setattr(cfg_loader, "DATA_DIR", tmp_path)
    from aletheia.storage import sqlite as sq
    from aletheia.storage import repositories as repo
    from aletheia.storage import migrations as mig
    sq.close_db()
    mig.run_migrations()

    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    sym = "BTC/USDT"
    # Seed 3 recent rows.
    for offset in (1, 2, 3):
        repo.insert_funding_rate_snapshot({
            "symbol": sym, "exchange": "binance",
            "funding_rate": 0.0001, "funding_rate_annualized": 0.1095,
            "observed_at": (now - timedelta(minutes=offset)).isoformat(),
        })

    from aletheia.data.collectors.derivatives import _maybe_backfill_funding_history

    class _FakeExchange:
        async def fetch_funding_rate_history(self, *args, **kwargs):
            raise AssertionError(
                "backfill should NOT call fetch_funding_rate_history when ≥3 rows exist"
            )

    result = asyncio.run(_maybe_backfill_funding_history(
        _FakeExchange(), sym, "BTC/USDT:USDT", "binance", since_hours=24,
    ))
    assert result == 0, f"backfill must be a no-op with existing rows; got {result} inserts"
    sq.close_db()


@spec_claim(
    SpecClaim(
        "MS-C06", _CONTRACT, "§3.4 Bitget NotSupported", "L2c",
        "_maybe_backfill_oi_history does NOT raise when the exchange raises "
        "NotSupported (Bitget); errors list is populated instead",
    ),
)
def test_oi_history_swallows_not_supported(tmp_path, monkeypatch):
    import asyncio

    from aletheia.config import loader as cfg_loader
    monkeypatch.setattr(cfg_loader, "DATA_DIR", tmp_path)
    from aletheia.storage import sqlite as sq
    from aletheia.storage import migrations as mig
    sq.close_db()
    mig.run_migrations()

    from aletheia.data.collectors.derivatives import _maybe_backfill_oi_history

    class _FakeBitget:
        async def fetch_open_interest_history(self, *args, **kwargs):
            # Mirror CCXT's NotSupported (a regular Exception subclass at runtime).
            raise Exception("bitget fetchOpenInterestHistory() is not supported yet")

    errors: list[str] = []
    # Must not raise.
    result = asyncio.run(_maybe_backfill_oi_history(
        _FakeBitget(), "BTC/USDT", "BTC/USDT:USDT", "bitget",
        since_hours=24, errors=errors,
    ))
    assert result == 0
    assert any("not supported" in e.lower() for e in errors), (
        f"errors list must record the NotSupported failure; got: {errors}"
    )
    sq.close_db()


# ---------------------------------------------------------------------------
# Step 4 — Settings UI coupling (asserted now; populated by later Steps)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "MS-S14", _CONTRACT, "§6 Settings UI coupling", "L2a",
        "frontend/src/pages/Settings.tsx contains the literal config field name "
        "'enable_long_short_ratio' so toggle state and persistence are wired",
    ),
)
def test_settings_tsx_has_long_short_ratio_field():
    src = _SETTINGS_TSX.read_text(encoding="utf-8")
    assert "enable_long_short_ratio" in src, (
        "Settings.tsx must reference 'enable_long_short_ratio' — without it the "
        "toggle either does not exist or does not save (Config↔UI rule violation)."
    )


@spec_claim(
    SpecClaim(
        "MS-S14b", _CONTRACT, "§6 Settings UI coupling", "L2a",
        "frontend/src/pages/Settings.tsx contains 'enable_fear_greed' literal and "
        "the 'Macro & Sentiment Signals' Section, with a matching SETTINGS_INDEX entry",
    ),
)
def test_settings_tsx_has_fear_greed_and_macro_section():
    src = _SETTINGS_TSX.read_text(encoding="utf-8")
    assert "enable_fear_greed" in src, (
        "Settings.tsx must reference 'enable_fear_greed' (Config↔UI rule violation)."
    )
    assert "Macro & Sentiment Signals" in src, (
        "Settings.tsx must contain the 'Macro & Sentiment Signals' Section title"
    )
    # SETTINGS_INDEX entry must match the Section title (file invariant — comment at line 63-64).
    assert src.count("Macro & Sentiment Signals") >= 2, (
        "SETTINGS_INDEX entry missing for 'Macro & Sentiment Signals' — inline search will miss it"
    )


@spec_claim(
    SpecClaim(
        "MS-S14c", _CONTRACT, "§6 Settings UI coupling", "L2a",
        "Settings.tsx contains 'enable_coingecko_top' and 'coingecko_top_n' literals",
    ),
)
def test_settings_tsx_has_coingecko_fields():
    src = _SETTINGS_TSX.read_text(encoding="utf-8")
    for key in ("enable_coingecko_top", "coingecko_top_n"):
        assert key in src, (
            f"Settings.tsx must reference '{key}' (Config↔UI rule violation)."
        )


@spec_claim(
    SpecClaim(
        "MS-S14d", _CONTRACT, "§6 Settings UI coupling", "L2a",
        "Settings.tsx contains 'derivatives_history_hours' literal",
    ),
)
def test_settings_tsx_has_history_hours_field():
    src = _SETTINGS_TSX.read_text(encoding="utf-8")
    assert "derivatives_history_hours" in src, (
        "Settings.tsx must reference 'derivatives_history_hours' (Config↔UI rule)."
    )


# ---------------------------------------------------------------------------
# §8.6.2 — _SYSTEM_PROMPT stability marker (Round 1 anti-hallucination)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "MS-S15", _CONTRACT, "§8.6.2 system prompt stability", "L2a",
        "market_observer.py contains a source comment ABOVE the _SYSTEM_PROMPT "
        "assignment explaining the Round 2 decision NOT to inject field-specific "
        "priors for LSR/F&G into the prompt (Round 1 anti-hallucination contract). "
        "Future maintainers must see the reasoning before editing the prompt.",
    ),
)
def test_system_prompt_round2_decision_comment():
    """AST-locate _SYSTEM_PROMPT module-level assignment; assert a comment block
    immediately above it mentions Round 2 + LSR + F&G + the deliberate non-injection."""
    src = _MARKET_OBSERVER.read_text(encoding="utf-8")
    # Locate the assignment line for _SYSTEM_PROMPT.
    lines = src.splitlines()
    assign_line: int | None = None
    for i, ln in enumerate(lines):
        if ln.startswith("_SYSTEM_PROMPT") and "=" in ln:
            assign_line = i
            break
    assert assign_line is not None, (
        "market_observer.py must contain a module-level _SYSTEM_PROMPT assignment"
    )
    # The previous up-to-15 lines must be a contiguous comment block referencing
    # the Round 2 decision keywords. (Plan §8.6.2 + MS-S15 lock this in.)
    window_start = max(0, assign_line - 15)
    window = "\n".join(lines[window_start:assign_line])
    required_tokens = ("ROUND 2", "LSR", "F&G", "_SYSTEM_PROMPT")
    missing = [tok for tok in required_tokens if tok not in window]
    assert not missing, (
        f"market_observer.py is missing the Round 2 _SYSTEM_PROMPT decision comment "
        f"above the assignment. Missing tokens: {missing}. "
        f"Per plan §8.6.2 / MS-S15 the comment must record the deliberate decision "
        f"NOT to inject LSR/F&G priors into the prompt — future readers must see why."
    )
