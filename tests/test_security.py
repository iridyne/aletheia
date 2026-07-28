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
    assert "live_disabled" in str(result.get("risk_violations", []))
    reset_db()


# --- Risk hard limits unmodifiable by agent ---

def test_agent_has_no_tool_to_change_risk_limits():
    from aletheia.agent_runtime.tool_registry import init_tool_registry, RESTRICTED_TOOLS
    init_tool_registry()
    assert "change_hard_risk_limits" in RESTRICTED_TOOLS


# --- Secrets never in GET responses ---

@pytest.mark.anyio
async def test_secret_never_in_model_get(client):
    c, _ = client
    secret = "sk-LEAKCHECK-CONFIDENTIAL-0001"
    await c.post("/api/settings/model", json={"provider": "openai_compatible", "api_key": secret})
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
async def test_secret_not_in_config_or_logs(client):
    c, tmp_path = client
    secret = "sk-NOTINFILES-3333"
    await c.post("/api/settings/model", json={"provider": "openai_compatible", "api_key": secret})
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


async def test_okx_test_connection_does_not_leak_exception_string(monkeypatch):
    """A failing OKX test-connection returns the exception CLASS name, never the
    raw message (which CCXT can populate with the request body / credentials)."""
    import aletheia.exchanges.okx.client as okx_mod
    from aletheia.server.routes.bitget import okx_test_connection, OkxTestRequest

    SECRET = "okx-secret-DEADBEEF-should-not-leak"
    closed = []

    class _FakeOKX:
        def __init__(self, mode):
            pass
        async def fetch_balance(self):
            raise RuntimeError(f"OKX auth failed apiKey=KEY123 secret={SECRET}")
        async def close(self):
            closed.append(True)

    monkeypatch.setattr(okx_mod, "OKXClient", _FakeOKX)
    result = await okx_test_connection(OkxTestRequest(mode="demo"))
    assert result["connected"] is False
    assert SECRET not in str(result), f"secret leaked in response: {result}"
    assert result["error"] == "RuntimeError"  # class name only
    assert closed == [True]


async def test_binance_test_candidate_does_not_leak_exception_string(monkeypatch):
    """A failing Binance candidate test returns the class name, not the raw error
    (which would echo the candidate api_key/secret submitted in the request)."""
    import ccxt.async_support as ccxt
    from aletheia.server.routes.bitget import binance_test_candidate, BinanceCandidateRequest

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
    from aletheia.server.routes.bitget import (
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

    provider_dir = pathlib.Path("aletheia/model_provider")
    offenders = []
    for f in provider_dir.glob("*.py"):
        text = f.read_text(encoding="utf-8")
        if "resp.raise_for_status()" in text:
            offenders.append(f.name)
    assert not offenders, f"providers still using bare raise_for_status(): {offenders}"
