"""Spec: API Reference Contract (docs/design/api/api_reference.md)

Executable claims covering the trust-boundary endpoint safety invariants
documented in the REST API Reference.  These claims are intentionally
structural (L2a) or lightweight behavioral (L2b) so they run in <2 s with
no live gateway, no network, and minimal DB setup.

Claim hierarchy
---------------
  API-S01  L2a  Trust-boundary endpoints call write_audit (structural scan)
  API-S02  L2a  POST /settings/live-unlock handler requires confirm field
  API-S03  L2a  GET credentials-status helpers never include raw 'value' field
  API-S04  L2b  GET /api/risk/boundaries returns 200 with expected risk fields
  API-S05  L2b  POST /api/risk/kill-switch without active param returns 422
  API-S06  L2c  No GET handler under /api/.*/credentials returns plaintext value
  API-S07  L2a  _PROTECTED_PATHS in app.py includes live-unlock and kill-switch
  API-S08  L2b  Tunnel enabled + no token → protected paths return 401 (fail-closed)
  API-S10  L2a  Exchange credential tests use private account endpoints
  API-S11  L2a  _PROTECTED_PATHS includes all five Local Paper account mutation routes
"""

import ast
from pathlib import Path

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


@spec_claim(SpecClaim(
    "API-L01", "api_reference", "§Settings", "L2a",
    "The paper-Learning settings endpoint documents its owner-gated paper-only scope.",
    status="implemented",
))
def test_learning_settings_endpoint_is_documented():
    from pathlib import Path

    reference = (Path(__file__).parents[2] / "docs" / "design" / "api" / "api_reference.md").read_text(
        encoding="utf-8",
    )
    assert "`/settings/learning`" in reference
    assert "paper-only" in reference.lower()

# Repository root — all path lookups are relative to this.
_ROOT = Path(__file__).resolve().parent.parent.parent
_ROUTES = _ROOT / "aletheia" / "server" / "routes"
_APP_PY = _ROOT / "aletheia" / "server" / "app.py"


# ---------------------------------------------------------------------------
# L2a — Structural claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "API-S01", "api_reference", "§trust-boundary audit", "L2a",
        "live-unlock and credentials POST handlers import and call write_audit",
    ),
)
def test_trust_boundary_handlers_call_write_audit():
    """Confirm write_audit is present in every trust-boundary handler source.

    Covered handlers:
      - routes/live.py   (live-unlock / live-lock)
      - routes/bitget.py (exchange-credentials POST for Bitget, Binance, OKX)

    The kill-switch audit is delegated to aletheia.risk.kill_switch.activate /
    deactivate — verified separately (API-S07 covers the middleware guard).
    """
    trust_files = {
        "live.py": _ROUTES / "live.py",
        "bitget.py": _ROUTES / "bitget.py",
    }
    for name, path in trust_files.items():
        src = path.read_text(encoding="utf-8")
        assert "write_audit" in src, (
            f"{name}: write_audit not found — trust-boundary handler must audit every mutation"
        )


@spec_claim(
    SpecClaim(
        "API-S02", "api_reference", "§trust-boundary live-unlock", "L2a",
        "LiveUnlockRequest model has 'confirm' field; handler rejects confirm=False",
    ),
)
def test_live_unlock_requires_confirm_field():
    """AST-parse routes/live.py to confirm 'confirm' is a model field and
    that the handler body checks its truth value before proceeding."""
    src = (_ROUTES / "live.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    # 1. 'confirm' must be a field of the request model (appears as an
    #    annotated assignment inside a ClassDef whose name contains "Request").
    confirm_in_model = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and "Request" in node.name:
            for child in ast.walk(node):
                if (
                    isinstance(child, ast.AnnAssign)
                    and isinstance(child.target, ast.Name)
                    and child.target.id == "confirm"
                ):
                    confirm_in_model = True

    assert confirm_in_model, (
        "LiveUnlockRequest must declare a 'confirm' field — "
        "live-unlock requires explicit human confirmation"
    )

    # 2. The handler must reference req.confirm (attribute access).
    confirm_used = False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "confirm"
            and isinstance(node.value, ast.Name)
            and node.value.id == "req"
        ):
            confirm_used = True

    assert confirm_used, (
        "live_unlock handler must check req.confirm — "
        "gate must be enforced, not just declared"
    )


@spec_claim(
    SpecClaim(
        "API-S03", "api_reference", "§trust-boundary credentials GET", "L2a",
        "Credentials GET helpers (_cred_status, _binance_cred_status, _okx_cred_status) "
        "never include 'value' key in their return dict",
    ),
)
def test_credentials_get_helpers_omit_value_key():
    """AST-parse routes/bitget.py: every return dict in the three
    _*_cred_status helpers must not contain a string key named 'value'
    or 'api_secret'.  They are allowed to include 'configured' and 'last4'.
    """
    tree = ast.parse((_ROUTES / "bitget.py").read_text(encoding="utf-8"))

    forbidden_keys = {"value", "api_secret"}
    helper_names = {"_cred_status", "_binance_cred_status", "_okx_cred_status"}

    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name in helper_names):
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Dict):
                continue
            for key in child.keys:
                if isinstance(key, ast.Constant) and key.value in forbidden_keys:
                    raise AssertionError(
                        f"{node.name}: return dict contains forbidden key '{key.value}' — "
                        "credentials GET must never expose raw secret values"
                    )


@spec_claim(
    SpecClaim(
        "API-S07", "api_reference", "§trust-boundary tunnel guard", "L2a",
        "_PROTECTED_PATHS in app.py includes live-unlock, kill-switch, and the "
        "legacy Autonomous Learning mutation",
    ),
)
def test_protected_paths_contains_live_unlock_and_kill_switch():
    """AST-parse app.py and extract the _PROTECTED_PATHS set literal.

    The tunnel-auth middleware guards this set of paths — if a trust-boundary
    path is absent from it the guard silently bypasses authentication when
    the Cloudflare tunnel is active.
    """
    src = _APP_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)

    protected: set[str] = set()
    for node in ast.walk(tree):
        # Look for: _PROTECTED_PATHS = { ... }
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "_PROTECTED_PATHS"
            and isinstance(node.value, ast.Set)
        ):
            for elt in node.value.elts:
                if isinstance(elt, ast.Constant):
                    protected.add(elt.value)

    assert protected, "_PROTECTED_PATHS not found in app.py — tunnel guard cannot operate"

    assert "/api/settings/live-unlock" in protected, (
        "/api/settings/live-unlock must be in _PROTECTED_PATHS — "
        "live trading unlock is a human-confirmed action that must be tunnel-gated"
    )
    assert "/api/risk/kill-switch" in protected, (
        "/api/risk/kill-switch must be in _PROTECTED_PATHS — "
        "kill-switch is a safety-critical action that must be tunnel-gated"
    )
    assert "/api/settings/autonomous-learning" in protected, (
        "/api/settings/autonomous-learning must be in _PROTECTED_PATHS — "
        "the master Learning gate changes scheduler and lifecycle behavior"
    )


@spec_claim(
    SpecClaim(
        "API-S11", "api_reference", "§trust-boundary paper-account mutations", "L2a",
        "_PROTECTED_PATHS in app.py includes all five Local Paper account mutation routes",
    ),
)
def test_protected_paths_contains_paper_account_mutations():
    """AST-parse app.py and verify all Local Paper mutation paths are guarded.

    These routes mutate the authoritative paper cash ledger or force-close paper
    positions.  An unauthenticated tunnel caller must not be able to fabricate
    deposits, drain capital, or contaminate Learning evidence.
    """
    src = _APP_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)

    protected: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "_PROTECTED_PATHS"
            and isinstance(node.value, ast.Set)
        ):
            for elt in node.value.elts:
                if isinstance(elt, ast.Constant):
                    protected.add(elt.value)

    for path in (
        "/api/settings/paper-account/credit",
        "/api/settings/paper-account/debit",
        "/api/settings/paper-account/set-balance",
        "/api/settings/paper-account/close-all",
        "/api/settings/paper-account/close-spot",
    ):
        assert path in protected, (
            f"{path} must be in _PROTECTED_PATHS — "
            "Local Paper cash mutations and bulk-close are owner-only economic actions"
        )


# ---------------------------------------------------------------------------
# L2b — Behavioral claims (TestClient, no live DB required)
# ---------------------------------------------------------------------------


def _make_test_client():
    """Return a starlette TestClient wrapping the FastAPI app.

    We import app lazily so that module-level side effects (router registration,
    lifespan) do not run during collection.  TestClient with raise_server_exceptions=False
    lets us assert on 4xx/5xx without pytest treating them as errors.
    """
    # Deferred import to avoid triggering lifespan at collection time.
    from fastapi.testclient import TestClient
    from aletheia.server.app import app

    return TestClient(app, raise_server_exceptions=False)


@spec_claim(
    SpecClaim(
        "API-S04", "api_reference", "§risk boundaries GET", "L2b",
        "GET /api/risk/boundaries returns 200 with all RiskConfig fields present",
    ),
)
def test_risk_boundaries_returns_200_with_required_fields(tmp_path, monkeypatch):
    """Spin up a TestClient, call GET /api/risk/boundaries, assert 200 and
    that the known risk limit keys are all present in the JSON body.

    Uses tmp_path to isolate config state so the test is hermetic.
    """
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    client = _make_test_client()
    resp = client.get("/api/risk/boundaries")
    assert resp.status_code == 200, (
        f"GET /api/risk/boundaries returned {resp.status_code}: {resp.text}"
    )

    body = resp.json()
    required_fields = {
        "max_daily_loss_pct",
        "max_single_trade_risk_pct",
        "max_open_positions_live",
        "max_open_positions_demo",
        "max_live_position_notional_usdt",
        "max_demo_position_notional_usdt",
        "max_futures_leverage_live",
        "max_futures_leverage_demo",
    }
    missing = required_fields - set(body.keys())
    assert not missing, (
        f"GET /api/risk/boundaries response missing fields: {missing}"
    )


@spec_claim(
    SpecClaim(
        "API-S05", "api_reference", "§risk kill-switch validation", "L2b",
        "POST /api/risk/kill-switch without 'active' param returns 422",
    ),
)
def test_kill_switch_post_without_active_returns_422(tmp_path, monkeypatch):
    """Pydantic must reject a body that omits the required 'active' boolean.

    KillSwitchRequest.active has no default, so FastAPI returns 422 when it
    is absent.  This locks the endpoint shape — a future refactor that makes
    active optional without adding a safety gate would break this claim.
    """
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    client = _make_test_client()
    resp = client.post("/api/risk/kill-switch", json={})
    assert resp.status_code == 422, (
        f"POST /api/risk/kill-switch with empty body should return 422, got {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# L2c — Invariant claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "API-S06", "api_reference", "§trust-boundary credentials invariant", "L2c",
        "No GET handler in routes/bitget.py returns a dict containing 'value' or 'api_secret'",
    ),
)
def test_no_get_handler_returns_raw_credential_value():
    """Invariant: scan every function in routes/bitget.py whose name starts
    with a GET-handler pattern (*_status) or is decorated for a GET route.
    For each one, confirm it never returns a dict with a 'value' or 'api_secret'
    key literal.

    This is stronger than API-S03: it covers all functions reachable from GET
    routes, not just the named helper functions.
    """
    tree = ast.parse((_ROUTES / "bitget.py").read_text(encoding="utf-8"))
    forbidden_keys = {"value", "api_secret"}

    # Collect all async def functions that are decorated with @router.get
    # or whose name ends in _status (called from GET handlers).
    get_functions: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Attribute)
                    and decorator.attr == "get"
                ):
                    get_functions.add(node.name)
            if node.name.endswith("_status") or node.name.startswith("_") and "cred" in node.name:
                get_functions.add(node.name)

    assert get_functions, "No GET-related functions found in bitget.py — invariant scan is vacuous"

    for node in ast.walk(tree):
        if not (
            isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
            and node.name in get_functions
        ):
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Dict):
                continue
            for key in child.keys:
                if isinstance(key, ast.Constant) and key.value in forbidden_keys:
                    raise AssertionError(
                        f"INVARIANT VIOLATION: GET-reachable function '{node.name}' "
                        f"returns dict with forbidden key '{key.value}' — "
                        "credentials GET must never expose raw secret values"
                    )


# ---------------------------------------------------------------------------
# D6 / D7 — Missing endpoint existence claims (structural, L2a)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "API-D6", "api_reference", "§strategy-performance endpoint", "L2a",
        "GET /analytics/strategy-performance route exists in routes/analytics.py",
    ),
)
def test_strategy_performance_route_exists():
    src = (_ROUTES / "analytics.py").read_text(encoding="utf-8")
    assert "/strategy-performance" in src, (
        "analytics.py must expose GET /strategy-performance endpoint (D6)"
    )


@spec_claim(
    SpecClaim(
        "API-D7", "api_reference", "§serenity-pipeline endpoint", "L2a",
        "GET /serenity/pipeline route exists in routes/data.py",
    ),
)
def test_serenity_pipeline_route_exists():
    src = (_ROUTES / "data.py").read_text(encoding="utf-8")
    assert "/serenity/pipeline" in src, (
        "data.py must expose GET /serenity/pipeline endpoint (D7)"
    )


@spec_claim(
    SpecClaim(
        "API-S08", "api_reference", "§tunnel-auth fail-closed", "L2b",
        "When tunnel is enabled but tunnel_api_token is not set, "
        "protected paths return 401 (fail-closed, not pass-through)",
    ),
)
def test_tunnel_enabled_no_token_returns_401(tmp_path, monkeypatch):
    """Regression guard: tunnel ON + missing token must be rejected, not allowed.

    Before the fix (commit after 2026-06-30), the middleware only checked the
    token when ``get_secret('tunnel_api_token')`` returned a truthy value — so a
    missing token silently bypassed auth.  Now it must fail-closed with 401.
    """
    import aletheia.config.loader as loader
    from aletheia.config.schema import AletheiaConfig

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    # Build a config with tunnel enabled but no token in secrets.
    # Modify the existing TunnelConfig in-place (AletheiaConfig is the root model).
    cfg = AletheiaConfig()
    cfg.tunnel.enabled = True
    monkeypatch.setattr(loader, "load_config", lambda **_: cfg)
    import aletheia.server.app as server_app
    monkeypatch.setattr(server_app, "load_config", lambda **_: cfg)

    # Ensure get_secret returns None for tunnel_api_token.
    import aletheia.config.secrets as secrets_mod
    monkeypatch.setattr(secrets_mod, "get_secret", lambda key: None)

    client = _make_test_client()

    for path in ("/api/settings/live-unlock", "/api/risk/kill-switch"):
        resp = client.post(path, json={"confirm": True, "active": True})
        assert resp.status_code == 401, (
            f"Expected 401 for {path} when tunnel is enabled with no token, "
            f"got {resp.status_code}. "
            "Tunnel enabled + no token must be fail-closed to prevent unauthenticated "
            "public access to trust-boundary endpoints."
        )


@spec_claim(
    SpecClaim(
        "API-S10",
        "api_reference",
        "§exchange credential verification",
        "L2a",
        "Binance and OKX candidate/stored credential checks call private account "
        "endpoints; a public time endpoint cannot produce a verified result.",
        status="implemented",
    ),
)
def test_exchange_credential_checks_use_private_endpoints():
    import inspect

    from aletheia.server.routes.bitget import (
        binance_test_candidate,
        binance_test_connection,
        okx_test_candidate,
        okx_test_connection,
    )

    for handler in (
        binance_test_candidate,
        binance_test_connection,
        okx_test_candidate,
        okx_test_connection,
    ):
        source = inspect.getsource(handler)
        assert "fetch_time(" not in source
        assert "fetch_balance(" in source or "get_positions(" in source
