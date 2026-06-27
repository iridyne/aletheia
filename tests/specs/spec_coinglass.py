"""Spec: CoinGlass collector — Round 2 Step 5 second batch.

Covers the key-loading contract (dual-path: keyring preferred, env fallback)
and the no-key safety invariant (no HTTP request constructed when key absent).

All claims attach to ``data_layer_contract`` (reused — not a new trust boundary).

WARNING — CONFORMANCE COLLECTION TRAP ([[aletheia-sdd-spec-collection-trap]]):
This file is registered in tests/test_spec_conformance.py via the
``_import_all_specs()`` import list. Without that registration the conformance
matrix collects zero CG-* cases despite this file existing on disk.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim

_ROOT = Path(__file__).parent.parent.parent
_COINGLASS = _ROOT / "aletheia" / "data" / "collectors" / "coinglass.py"
_DOCTOR = _ROOT / "aletheia" / "diagnostics" / "doctor.py"

_CONTRACT = "data_layer_contract"


@spec_claim(
    SpecClaim(
        "CG-S01", _CONTRACT, "§4.1 key loader", "L2a",
        "_get_coinglass_key() function reads get_secret('coinglass.api_key') "
        "FIRST, falls back to os.environ.get for COINGLASS_API_KEY (canonical) "
        "or COINGLASS_API (operator's shorter form). Keyring always wins.",
    ),
)
def test_coinglass_dual_path_key_loader():
    src = _COINGLASS.read_text(encoding="utf-8")
    # Both references must appear, and keyring must come first by source order.
    keyring_idx = src.find('get_secret("coinglass.api_key")')
    env_canonical_idx = src.find('os.environ.get("COINGLASS_API_KEY")')
    env_short_idx = src.find('os.environ.get("COINGLASS_API")')
    assert keyring_idx > 0, (
        "coinglass.py must call get_secret('coinglass.api_key')"
    )
    assert env_canonical_idx > 0, (
        "coinglass.py must call os.environ.get('COINGLASS_API_KEY') (canonical)"
    )
    assert env_short_idx > 0, (
        "coinglass.py must also accept os.environ.get('COINGLASS_API') (shorter form)"
    )
    assert keyring_idx < env_canonical_idx, (
        "key resolution order is wrong — get_secret must be checked BEFORE os.environ "
        "(keyring takes precedence per CG-S01)"
    )


@spec_claim(
    SpecClaim(
        "CG-C01", _CONTRACT, "§4.1 no-key safety", "L2c",
        "When _get_coinglass_key() returns None, CoinGlassCollector.collect() "
        "returns early with rows=[] BEFORE constructing any httpx request. "
        "Verified by lexical order in collect() AST: the key check + early "
        "return must precede any httpx_client(...) call.",
    ),
)
def test_collect_returns_early_when_no_key():
    """AST: in CoinGlassCollector.collect(), the lexically-first occurrence of
    httpx_client must appear AFTER an `if not key: return ...` guard.
    """
    tree = ast.parse(_COINGLASS.read_text(encoding="utf-8"))

    collect_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "CoinGlassCollector":
            for member in node.body:
                if isinstance(member, ast.AsyncFunctionDef) and member.name == "collect":
                    collect_fn = member
                    break
    assert collect_fn, "CoinGlassCollector.collect not found"

    # Find the line number of the first "if not key" branch with a Return inside
    # and the line number of the first httpx_client(...) call. The former must come first.
    guard_line = None
    httpx_line = None
    for node in ast.walk(collect_fn):
        if isinstance(node, ast.If) and guard_line is None:
            # Look for `if not key:` shape (UnaryOp Not on Name 'key')
            test = node.test
            if (
                isinstance(test, ast.UnaryOp)
                and isinstance(test.op, ast.Not)
                and isinstance(test.operand, ast.Name)
                and test.operand.id == "key"
            ):
                # Confirm a Return statement lives in this branch.
                if any(isinstance(s, ast.Return) for s in node.body):
                    guard_line = node.lineno
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "httpx_client"
            and httpx_line is None
        ):
            httpx_line = node.lineno

    assert guard_line is not None, (
        "collect() must contain `if not key: return ...` no-key guard"
    )
    if httpx_line is not None:
        assert guard_line < httpx_line, (
            f"no-key guard at line {guard_line} must lexically precede the first "
            f"httpx_client call at line {httpx_line}; otherwise a missing key still "
            "constructs the HTTP request (CG-C01 violation)."
        )


@spec_claim(
    SpecClaim(
        "CG-C03", _CONTRACT, "§4.1 tier-detect", "L2c",
        "When CoinGlass responds with the 'Upgrade plan' paywall marker, "
        "CoinGlassCollector.collect() caches tier_status='free_paywalled', "
        "records the error, and short-circuits future calls in the same process "
        "(per [[aletheia-coinglass-free-tier-paywalled]]).",
    ),
)
def test_coinglass_tier_paywalled_short_circuit():
    """Behavioral: first call probes + caches; second call short-circuits without httpx."""
    import asyncio
    from aletheia.data.collectors import coinglass as mod
    from aletheia.data.collectors.coinglass import CoinGlassCollector

    # Reset cache so the test is deterministic.
    CoinGlassCollector._tier_status = None

    async def run():
        # Monkeypatch httpx_client to a fake whose .get() returns a "Upgrade plan" body.
        class _FakeResp:
            text = '{"code":"401","msg":"Upgrade plan"}'
            status_code = 200

        class _FakeClient:
            calls = []
            async def get(self, url, **kwargs):
                _FakeClient.calls.append(url)
                return _FakeResp()

        class _FakeCtx:
            async def __aenter__(self):
                return _FakeClient()
            async def __aexit__(self, *args):
                return False

        def _fake_httpx(*args, **kwargs):
            return _FakeCtx()

        # httpx_client is imported lazily inside collect() — patch the source module.
        from aletheia import net as net_mod
        original = net_mod.httpx_client
        net_mod.httpx_client = _fake_httpx
        # Also need to override the key resolver so the no-key gate doesn't trip.
        original_key = mod._get_coinglass_key
        mod._get_coinglass_key = lambda: "fake_key_xyz"
        try:
            # First call probes and caches paywall status.
            r1 = await CoinGlassCollector().collect(["BTC/USDT"])
            assert "coinglass_free_tier_paywalled" in r1["errors"], (
                f"first call must record paywall; got: {r1}"
            )
            assert CoinGlassCollector._tier_status == "free_paywalled", (
                f"tier_status must be cached after first probe; got: {CoinGlassCollector._tier_status}"
            )
            calls_after_first = len(_FakeClient.calls)
            # Second call must short-circuit without ANY new httpx call.
            r2 = await CoinGlassCollector().collect(["BTC/USDT"])
            assert "coinglass_free_tier_paywalled" in r2["errors"]
            assert len(_FakeClient.calls) == calls_after_first, (
                "second call must NOT hit httpx — cached status should short-circuit"
            )
        finally:
            net_mod.httpx_client = original
            mod._get_coinglass_key = original_key
            CoinGlassCollector._tier_status = None

    asyncio.run(run())


@spec_claim(
    SpecClaim(
        "CG-C02", _CONTRACT, "§4.1 doctor redact", "L2c",
        "doctor's CoinGlass credential probe wraps the key value in redact() — "
        "the raw key value MUST NEVER appear in the report message",
    ),
)
def test_doctor_redacts_coinglass_key():
    """AST: in _probe_credentials, any report.add(...) line that references
    a coinglass key variable must pass that variable through redact()."""
    src = _DOCTOR.read_text(encoding="utf-8")
    # The coinglass block must call redact(...) with the key value, not include
    # the raw variable in an f-string message.
    cg_block_start = src.find("creds.coinglass")
    assert cg_block_start > 0, "doctor.py must contain a creds.coinglass probe"
    cg_block = src[max(0, cg_block_start - 200):cg_block_start + 800]
    assert "redact(" in cg_block, (
        "doctor.py creds.coinglass probe must use redact() for the key value"
    )
    # And the raw env/keyring value must NOT appear directly in the report message
    # without redact (i.e. no `f"...{cg_env}..."` outside redact wrapping).
    # Heuristic: ensure the key variables (cg_env, cg_keyring) never appear inside
    # the message string without redact(). We check that any {cg_env} or {cg_keyring}
    # in f-string context is preceded by redact( and ends with ).
    import re
    bad = re.findall(r"\{cg_(?:env|keyring)\}", cg_block)
    assert not bad, (
        f"doctor.py must redact cg_env/cg_keyring in messages; raw refs found: {bad}"
    )
