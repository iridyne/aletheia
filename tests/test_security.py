"""Security tests (guide §28.3).

Invariants:
- Secrets never appear in logs, agent context, KB, or GET responses.
- Agent cannot invoke restricted tools.
- Live-disabled blocks live execution.
- Risk hard limits cannot be modified by the agent.
- Dashboard permission/live changes write audit events.
"""

import json
from unittest.mock import patch
import pytest
from httpx import AsyncClient, ASGITransport

from aletheia.server.app import app
from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo


def _isolate(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: None)
    monkeypatch.setattr("aletheia.config.secrets.get_data_dir", lambda: tmp_path)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    from aletheia.config.loader import load_config, save_config
    import aletheia.trust.live_unlock as trust
    from datetime import datetime, timedelta, timezone

    cfg = load_config()
    cfg.exchange.name = "bitget"
    cfg.exchange.mode = "live"
    cfg.risk.allowed_exchange = "bitget"
    save_config(cfg)
    from tests.live_identity import bind_live_identity

    bind_live_identity(monkeypatch, lambda ref: f"test-secret:{ref}")
    identity = trust.resolve_live_execution_identity(cfg)
    now = datetime.now(timezone.utc)
    evidence = {
        **identity,
        "ok": True,
        "reason_code": "ok",
        "account_fingerprint": "sha256:test-account",
        "private_authenticated": True,
        "account_readable": True,
        "environment_verified": True,
        "permission_status": "granted",
        "completeness": "complete",
        "fetched_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=300)).isoformat(),
    }
    evidence["evidence_binding"] = trust._evidence_binding(evidence)

    async def _fake_preflight(_cfg):
        return evidence

    monkeypatch.setattr("aletheia.server.routes.live.run_live_preflight", _fake_preflight)
    reset_db()
    sq._db_conn = None
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


@pytest.fixture
async def client(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, tmp_path
    reset_db()


# --- Restricted tools ---

def test_agent_cannot_invoke_restricted_tools():
    from aletheia.agent_runtime.tool_registry import init_tool_registry
    from aletheia.agent_runtime.permissions import is_tool_permitted
    init_tool_registry()
    restricted = ["unlock_live", "place_live_order", "change_hard_risk_limits",
                  "modify_credentials", "modify_execution_gateway"]
    for mode in ["Observe", "Paper", "Full"]:
        for tool in restricted:
            assert not is_tool_permitted(tool, mode), f"{tool} must never be permitted ({mode})"


# --- Live disabled blocks live execution ---

def test_live_disabled_blocks_live_execution(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config(); cfg.agent.permission_mode = "Full"; save_config(cfg)
    from aletheia.execution.gateway import submit_proposal
    result = submit_proposal(symbol="BTC/USDT", market_type="spot", direction="long",
                             quantity=0.001, price=60000, execution_mode="bitget_live_limited")
    assert result["status"] == "rejected"
    # EX-04 (#1004): live Proposals now fail closed at authorization before Risk
    # sees them. Either gate is a valid proof that live execution is blocked.
    reason_blob = str(result.get("risk_violations", [])) + str(result.get("reason_code", ""))
    assert "live_disabled" in reason_blob or "live_authorization_missing" in reason_blob, result
    reset_db()


# --- Risk hard limits unmodifiable by agent ---

def test_agent_has_no_tool_to_change_risk_limits():
    from aletheia.agent_runtime.tool_registry import init_tool_registry, RESTRICTED_TOOLS
    init_tool_registry()
    assert "change_hard_risk_limits" in RESTRICTED_TOOLS


# --- Secrets never in GET responses ---

@pytest.mark.anyio
async def test_secret_never_in_model_get(client, monkeypatch):
    from tests.model_http import mock_model_probe

    mock_model_probe(monkeypatch)
    c, _ = client
    secret = "sk-LEAKCHECK-CONFIDENTIAL-0001"
    created = await c.post("/api/settings/model-profiles", json={
        "profile_id": "openai_compatible", "type": "openai_compatible", "display_name": "HTTP",
        "base_url": "http://model.test/v1", "model": "m1", "api_key": secret,
    })
    assert created.status_code == 201, created.text
    for path in ["/api/model", "/api/settings", "/api/status"]:
        resp = await c.get(path)
        assert secret not in resp.text, f"secret leaked in {path}"


@pytest.mark.anyio
async def test_bitget_secret_never_in_get(client):
    c, _ = client
    secret = "bg-LEAKCHECK-2222"
    await c.post("/api/settings/exchange-credentials", json={"profile": "demo", "api_key": secret})
    resp = await c.get("/api/bitget/credentials-status")
    assert secret not in resp.text


# --- Secrets not written to config.yaml or logs ---

@pytest.mark.anyio
async def test_secret_not_in_config_or_logs(client, monkeypatch):
    from tests.model_http import mock_model_probe

    mock_model_probe(monkeypatch)
    c, tmp_path = client
    secret = "sk-NOTINFILES-3333"
    created = await c.post("/api/settings/model-profiles", json={
        "profile_id": "openai_compatible", "type": "openai_compatible", "display_name": "HTTP",
        "base_url": "http://model.test/v1", "model": "m1", "api_key": secret,
    })
    assert created.status_code == 201, created.text
    cfg_file = tmp_path / "config.yaml"
    if cfg_file.exists():
        assert secret not in cfg_file.read_text(encoding="utf-8")
    audit = tmp_path / "logs" / "audit.jsonl"
    if audit.exists():
        assert secret not in audit.read_text(encoding="utf-8")


# --- Dashboard permission/live changes are audited ---

@pytest.mark.anyio
async def test_permission_and_live_changes_audited(client):
    c, tmp_path = client
    await c.post("/api/settings/permission-mode", json={"mode": "Paper"})
    with patch("aletheia.analytics.go_live_scorecard.compute_scorecard") as sc:
        sc.return_value = {"composite_score": 95.0, "traffic_light": "green", "blockers": []}
        await c.post(
            "/api/settings/live-unlock",
            json={"confirm": True, "acknowledge_first_unlock": True},
        )
    audit = tmp_path / "logs" / "audit.jsonl"
    assert audit.exists()
    events = [json.loads(l) for l in audit.read_text(encoding="utf-8").splitlines() if l.strip()]
    types = {e["event_type"] for e in events}
    assert "permission_changed" in types
    assert "live_unlocked" in types


@pytest.mark.anyio
async def test_live_unlock_requires_confirm(client):
    c, _ = client
    resp = await c.post("/api/settings/live-unlock", json={"confirm": False})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# error-leak 2.4: exchange test endpoints must not echo raw str(e) (which can
# carry the candidate api_key/secret or internal request detail).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("venue,mode,execution_mode", (
    ("bitget", "demo", "bitget_demo"),
    ("bitget", "live", "bitget_live_limited"),
    ("binance", "testnet", "binance_testnet"),
    ("binance", "live", "binance_live_limited"),
    ("okx", "demo", "okx_demo"),
    ("okx", "live", "okx_live_limited"),
))
@pytest.mark.parametrize("complete", (True, False))
async def test_stored_exchange_probe_uses_uta_off_request_loop(monkeypatch, venue, mode, execution_mode, complete):
    import threading
    from types import SimpleNamespace
    from aletheia.server.routes import bitget as routes
    from aletheia.uta import manager
    from aletheia.uta.models import ReadCompleteness

    calls = []
    request_thread = threading.get_ident()

    async def query(selected_mode, method, **kwargs):
        calls.append((selected_mode, method, threading.get_ident() != request_thread))
        return SimpleNamespace(
            completeness=ReadCompleteness.COMPLETE if complete else ReadCompleteness.PARTIAL,
            reason_code=None if complete else "account_read_incomplete",
        )

    def forbidden_client(*args, **kwargs):
        raise AssertionError("legacy private client must not be constructed")

    monkeypatch.setattr(manager, "query_uta", query)
    monkeypatch.setattr(routes, "BitgetClient", forbidden_client, raising=False)
    route, request_type = {
        "bitget": (routes.test_connection, routes.TestConnectionRequest),
        "binance": (routes.binance_test_connection, routes.BinanceTestRequest),
        "okx": (routes.okx_test_connection, routes.OkxTestRequest),
    }[venue]
    result = await route(request_type(mode=mode))
    assert calls == [(execution_mode, "get_account", True)]
    assert result["connected"] is complete
    assert result["mode"] == mode
    if not complete:
        assert result["error"] == "account_read_incomplete"


async def test_bitget_stored_probe_defaults_to_configured_mode_and_rejects_invalid_mode(monkeypatch):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from aletheia.server.routes import bitget as routes

    cfg = SimpleNamespace(exchange=SimpleNamespace(mode="live"))
    calls = []

    def probe(execution_mode, **kwargs):
        calls.append((execution_mode, kwargs["cfg"] is cfg))
        return {"connected": True}

    monkeypatch.setattr(routes, "load_config", lambda: cfg)
    monkeypatch.setattr(routes, "probe_account_reachable", probe)
    assert await routes.test_connection() == {"connected": True, "mode": "live"}
    with pytest.raises(HTTPException) as error:
        await routes.test_connection(routes.TestConnectionRequest(mode="invalid"))
    assert error.value.status_code == 400
    assert calls == [("bitget_live_limited", True)]


async def test_okx_test_connection_does_not_leak_exception_string(monkeypatch):
    """A failing OKX test-connection returns the exception CLASS name, never the
    raw message (which CCXT can populate with the request body / credentials)."""
    from aletheia.uta import manager
    from aletheia.server.routes.bitget import okx_test_connection, OkxTestRequest

    SECRET = "okx-secret-DEADBEEF-should-not-leak"
    async def failed_read(*args, **kwargs):
        raise RuntimeError(f"OKX auth failed apiKey=KEY123 secret={SECRET}")

    monkeypatch.setattr(manager, "query_uta", failed_read)
    result = await okx_test_connection(OkxTestRequest(mode="demo"))
    assert result["connected"] is False
    assert SECRET not in str(result), f"secret leaked in response: {result}"
    assert result["error"] == "RuntimeError"  # class name only


async def test_binance_test_candidate_does_not_leak_exception_string(monkeypatch):
    """A failing Binance candidate test returns the class name, not the raw error
    (which would echo the candidate api_key/secret submitted in the request)."""
    import ccxt.async_support as ccxt
    from extensions.exchanges.credential_routes import binance_test_candidate, BinanceCandidateRequest

    SECRET = "cand-secret-CAFEBABE-should-not-leak"

    def _boom(*a, **k):
        raise RuntimeError(f"binance error secret={SECRET}")

    monkeypatch.setattr(ccxt, "binanceusdm", _boom)
    result = await binance_test_candidate(
        BinanceCandidateRequest(mode="testnet", api_key="KEY123", api_secret=SECRET)
    )
    assert result["connected"] is False
    assert SECRET not in str(result), f"secret leaked in response: {result}"
    assert result["error"] == "RuntimeError"


async def test_exchange_candidate_checks_use_private_balance_endpoint(monkeypatch):
    import aletheia.net as net
    from extensions.exchanges.credential_routes import (
        BinanceCandidateRequest,
        OkxCandidateRequest,
        binance_test_candidate,
        okx_test_candidate,
    )

    calls = []

    class _FakeExchange:
        def set_sandbox_mode(self, enabled):
            calls.append(("sandbox", enabled))

        async def fetch_balance(self):
            calls.append(("private_balance", True))
            return {"total": {"USDT": 1.0}}

        async def fetch_time(self):
            raise AssertionError("public time endpoint cannot verify credentials")

        async def close(self):
            calls.append(("exchange_closed", True))

    class _FakeSession:
        async def close(self):
            calls.append(("session_closed", True))

    monkeypatch.setattr(
        net,
        "make_ccxt",
        lambda *args, **kwargs: (_FakeExchange(), _FakeSession()),
    )

    binance = await binance_test_candidate(
        BinanceCandidateRequest(
            mode="testnet",
            api_key="candidate-key",
            api_secret="candidate-secret",
        )
    )
    okx = await okx_test_candidate(
        OkxCandidateRequest(
            mode="demo",
            api_key="candidate-key",
            api_secret="candidate-secret",
            passphrase="candidate-passphrase",
        )
    )

    assert binance["connected"] is True
    assert okx["connected"] is True
    assert calls.count(("private_balance", True)) == 2
    assert calls.count(("exchange_closed", True)) == 2
    assert calls.count(("session_closed", True)) == 2


async def test_exchange_candidate_cleanup_failure_closes_session_and_fails_closed(monkeypatch):
    import aletheia.net as net
    from extensions.exchanges.credential_routes import BinanceCandidateRequest, binance_test_candidate

    calls = []

    class _FakeExchange:
        async def fetch_balance(self):
            return {"total": {"USDT": 1.0}}

        async def close(self):
            calls.append("exchange")
            raise RuntimeError("close failed")

    class _FakeSession:
        async def close(self):
            calls.append("session")

    monkeypatch.setattr(net, "make_ccxt", lambda *args, **kwargs: (_FakeExchange(), _FakeSession()))

    result = await binance_test_candidate(
        BinanceCandidateRequest(mode="testnet", api_key="candidate-key", api_secret="candidate-secret")
    )

    assert result == {"connected": False, "error": "RuntimeError"}
    assert calls == ["exchange", "session"]


@pytest.mark.parametrize("broker,handler_name,request_name", [
    ("bitget", "set_exchange_credentials", "ExchangeCredentialsRequest"),
    ("binance", "set_binance_credentials", "BinanceCredentialsRequest"),
    ("okx", "set_okx_credentials", "OkxCredentialsRequest"),
])
async def test_exchange_credential_ingress_preserves_scope_lock_and_audit(monkeypatch, broker, handler_name, request_name):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from extensions.exchanges import credential_routes as routes

    cfg = SimpleNamespace(exchange=SimpleNamespace(name=broker), live=SimpleNamespace(state="live_limited"))
    writes, audit = [], []
    monkeypatch.setattr(routes, "load_config", lambda: cfg)
    monkeypatch.setattr(routes, "get_secret", lambda _ref: None)
    monkeypatch.setattr(routes, "set_secret", lambda ref, value: writes.append((ref, value)))
    monkeypatch.setattr(routes, "write_audit", audit.append)
    request = getattr(routes, request_name)(profile="live", api_key="synthetic-key")
    handler = getattr(routes, handler_name)
    with pytest.raises(HTTPException) as error:
        await handler(request)
    assert error.value.status_code == 409
    assert writes == audit == []
    cfg.live.state = "live_disabled"
    result = await handler(request)
    assert writes == [(f"{broker}.live.api_key", "synthetic-key")]
    assert result["updated"] == ["api_key"]
    assert len(audit) == 1
    assert audit[0]["actor"] == "human"
    assert audit[0]["source"] == "dashboard"
    assert audit[0]["event_type"] == "exchange_credentials_changed"
    assert "synthetic-key" not in str(audit)
    assert "synthetic-key" not in str(result)


async def test_close_ccxt_reraises_cancellation_after_session_cleanup():
    import asyncio
    from aletheia.net import close_ccxt

    calls = []

    class _Exchange:
        async def close(self):
            calls.append("exchange")
            raise asyncio.CancelledError

    class _Session:
        async def close(self):
            calls.append("session")

    with pytest.raises(asyncio.CancelledError):
        await close_ccxt(_Exchange(), _Session())

    assert calls == ["exchange", "session"]


@pytest.mark.parametrize("venue", ["okx", "binance"])
async def test_uta_close_attempts_session_cleanup_after_exchange_failure(monkeypatch, venue):
    from unittest.mock import AsyncMock
    from extensions.exchanges.ccxt.adapter import CCXTUTAAdapter
    from tests.uta.test_ccxt_adapter import FakeExchange, _identity, _install, _profile

    profile = _profile(venue)
    exchange = FakeExchange()
    session, _ = _install(monkeypatch, exchange)
    client = CCXTUTAAdapter(_identity(profile), profile)
    await client.connect()
    exchange.close = AsyncMock(side_effect=RuntimeError("synthetic cleanup failure"))

    # UTA teardown is bounded and must not mask the command report; the owned
    # session is still closed even when exchange teardown raises.
    await client.close()
    exchange.close.assert_awaited_once()
    assert session.closed
    await client.close()
    exchange.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# error-leak 2.7: model providers must not leak credentials via raised exceptions.
# raise_for_status_without_secrets scrubs auth headers + sanitises the message.
# ---------------------------------------------------------------------------


def test_raise_for_status_without_secrets_scrubs_credentials():
    import httpx
    from aletheia.net import raise_for_status_without_secrets

    SECRET = "sk-LEAKME-DEADBEEF"
    req = httpx.Request(
        "POST", "https://api.example.com/v1/chat",
        headers={"Authorization": f"Bearer {SECRET}", "x-api-key": SECRET,
                 "Content-Type": "application/json"},
    )
    resp = httpx.Response(401, request=req, json={"error": "unauthorized"})

    try:
        raise_for_status_without_secrets(resp)
        assert False, "should have raised on 401"
    except httpx.HTTPStatusError as exc:
        # status_code preserved for fallback routing
        assert exc.response is not None and exc.response.status_code == 401
        # secret absent from the message AND the (scrubbed) request headers
        assert SECRET not in str(exc)
        assert SECRET not in str(dict(exc.request.headers))
        assert "authorization" not in {k.lower() for k in exc.request.headers}

    # 2xx is a no-op (does not raise)
    ok = httpx.Response(200, request=httpx.Request("GET", "https://x"), json={})
    raise_for_status_without_secrets(ok)


def test_all_model_providers_use_shared_credential_scrubber():
    """No provider may call the bare resp.raise_for_status() (which keeps the
    credential-bearing request on the exception). All route through net's shared
    raise_for_status_without_secrets (error-leak 2.7 — unified)."""
    import pathlib

    provider_dirs = (pathlib.Path("aletheia/model_provider"), pathlib.Path("extensions/model_endpoints"))
    offenders = []
    for provider_dir in provider_dirs:
        assert provider_dir.is_dir(), f"missing model source tree: {provider_dir}"
        for f in provider_dir.glob("*.py"):
            text = f.read_text(encoding="utf-8")
            if "resp.raise_for_status()" in text:
                offenders.append(str(f))
    assert not offenders, f"providers still using bare raise_for_status(): {offenders}"
