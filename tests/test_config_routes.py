"""Tests for model + bitget config API routes (Phase 1).

Critical security invariant: no GET ever returns a raw secret value.
"""

import pytest
from httpx import AsyncClient, ASGITransport

from aletheia.server.app import app
from aletheia.model_provider.router import clear_cache


@pytest.fixture
async def client(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: None)
    monkeypatch.setattr("aletheia.config.secrets.get_data_dir", lambda: tmp_path)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    clear_cache()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    clear_cache()


@pytest.mark.anyio
async def test_get_model_config(client):
    resp = await client.get("/api/model")
    assert resp.status_code == 200
    data = resp.json()
    assert "openai_compatible" in data["providers"]
    assert data["providers"]["openai_compatible"]["api_key"]["configured"] is False


@pytest.mark.anyio
async def test_set_model_provider(client):
    resp = await client.post("/api/settings/model", json={
        "provider": "openai_compatible",
        "base_url": "http://localhost:9000/v1",
        "model": "my-model",
        "default": True,
    })
    assert resp.status_code == 200
    assert resp.json()["default_provider"] == "openai_compatible"

    resp = await client.get("/api/model")
    assert resp.json()["providers"]["openai_compatible"]["base_url"] == "http://localhost:9000/v1"
    assert resp.json()["providers"]["openai_compatible"]["model"] == "my-model"


@pytest.mark.anyio
async def test_set_model_api_key_is_write_only(client):
    """POST accepts an api_key, but GET must never return the raw value."""
    secret = "sk-super-secret-value-9999"
    resp = await client.post("/api/settings/model", json={
        "provider": "openai_compatible",
        "api_key": secret,
    })
    assert resp.status_code == 200
    body = resp.json()
    # Response reports configured + last4 only
    assert body["api_key"]["configured"] is True
    assert body["api_key"]["last4"] == "9999"
    assert secret not in resp.text

    # GET must not leak it either
    resp = await client.get("/api/model")
    assert secret not in resp.text
    assert resp.json()["providers"]["openai_compatible"]["api_key"]["configured"] is True


@pytest.mark.anyio
async def test_set_model_invalid_provider(client):
    resp = await client.post("/api/settings/model", json={"provider": "nonexistent"})
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_bitget_credentials_write_only(client):
    secret_key = "bg-api-key-abcd"
    resp = await client.post("/api/settings/exchange-credentials", json={
        "profile": "demo",
        "api_key": secret_key,
        "api_secret": "bg-secret-xyz",
        "passphrase": "bg-pass-1234",
    })
    assert resp.status_code == 200
    assert secret_key not in resp.text

    resp = await client.get("/api/bitget/credentials-status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["demo"]["api_key"]["configured"] is True
    assert data["demo"]["api_key"]["last4"] == "abcd"
    # raw secrets never present
    assert secret_key not in resp.text
    assert "bg-secret-xyz" not in resp.text


@pytest.mark.anyio
async def test_exchange_credentials_invalid_profile(client):
    resp = await client.post("/api/settings/exchange-credentials", json={
        "profile": "production", "api_key": "x",
    })
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_model_health_endpoint(client):
    resp = await client.get("/api/model/health")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert all("provider" in h and "available" in h for h in data)


# --- Codex subscription provider routes ---

@pytest.mark.anyio
async def test_get_model_includes_codex_model_no_token(client):
    resp = await client.get("/api/model")
    cx = resp.json()["providers"]["codex_cli"]
    assert "codex_model" in cx
    # no token/key ever leaks for codex
    assert "token" not in resp.text.lower()
    assert cx["api_key"]["configured"] is False


@pytest.mark.anyio
async def test_codex_login_start_returns_url_code_no_token(client, monkeypatch):
    import aletheia.model_provider.codex_auth as auth

    async def fake_start():
        return {"state": "awaiting_authorization",
                "verification_uri": "https://auth.openai.com/device",
                "user_code": "ABCD-1234", "error": None}
    monkeypatch.setattr(auth, "start_login", fake_start)

    resp = await client.post("/api/model/codex/login-start")
    assert resp.status_code == 200
    body = resp.json()
    assert body["verification_uri"].startswith("https://")
    assert body["user_code"] == "ABCD-1234"
    assert "access_token" not in resp.text and "token" not in resp.text.lower()

    # audit written
    from aletheia.files.jsonl import read_jsonl
    assert any(e.get("event_type") == "codex_login_started" for e in read_jsonl("audit.jsonl"))


@pytest.mark.anyio
async def test_codex_login_status_no_token(client, monkeypatch):
    import aletheia.model_provider.codex_auth as auth

    async def fake_poll():
        return {"state": "completed", "verification_uri": None, "user_code": None, "error": None}
    monkeypatch.setattr(auth, "poll_status", fake_poll)

    resp = await client.get("/api/model/codex/login-status")
    assert resp.status_code == 200
    assert resp.json()["state"] == "completed"
    assert "token" not in resp.text.lower()


@pytest.mark.anyio
async def test_codex_models_endpoint(client, monkeypatch):
    import aletheia.model_provider.router as router_mod

    class FakeProvider:
        async def list_models(self):
            return [{"slug": "gpt-5.5", "display_name": "GPT-5.5"}]
    monkeypatch.setattr(router_mod, "get_provider", lambda name=None: FakeProvider())

    resp = await client.get("/api/model/codex/models")
    assert resp.status_code == 200
    assert resp.json() == [{"slug": "gpt-5.5", "display_name": "GPT-5.5"}]


@pytest.mark.anyio
async def test_set_codex_model_persists(client):
    resp = await client.post("/api/settings/model", json={
        "provider": "codex_cli", "codex_model": "gpt-5.5", "enabled": True,
    })
    assert resp.status_code == 200
    resp = await client.get("/api/model")
    assert resp.json()["providers"]["codex_cli"]["codex_model"] == "gpt-5.5"


@pytest.mark.anyio
async def test_codex_health_honest_not_installed(client, monkeypatch):
    """UI honesty: when codex is absent, health reports not_installed (never connected)."""
    monkeypatch.setattr("aletheia.model_provider.codex_cli.shutil.which", lambda c: None)
    # enable codex so healthcheck goes past the 'disabled' branch
    await client.post("/api/settings/model", json={"provider": "codex_cli", "enabled": True})
    resp = await client.get("/api/model/health")
    cx = next(h for h in resp.json() if h["provider"] == "codex_cli")
    assert cx["available"] is False
    assert cx["error"] == "not_installed"


# --- Verified-Connection: model provider candidate-key test (verify-before-commit) ---

@pytest.mark.anyio
async def test_model_test_candidate_pass(client, monkeypatch):
    """A reachable candidate key returns available=true, echoes no key, persists nothing."""
    from aletheia.model_provider.openai_compatible import OpenAICompatibleProvider
    from aletheia.model_provider.base import ModelHealth

    async def fake_health(self):
        return ModelHealth(provider="openai_compatible", available=True, model=self.model)
    monkeypatch.setattr(OpenAICompatibleProvider, "healthcheck", fake_health)

    secret = "sk-candidate-not-saved-7777"
    resp = await client.post("/api/model/test-candidate", json={
        "provider": "openai_compatible",
        "base_url": "http://localhost:9000/v1",
        "model": "probe-model",
        "api_key": secret,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    # candidate key must never be echoed
    assert secret not in resp.text
    # nothing persisted: the provider still has no configured key
    cfg = await client.get("/api/model")
    assert cfg.json()["providers"]["openai_compatible"]["api_key"]["configured"] is False


@pytest.mark.anyio
async def test_model_test_candidate_fail(client, monkeypatch):
    """An unreachable candidate returns available=false + error, persists nothing."""
    from aletheia.model_provider.openai_compatible import OpenAICompatibleProvider
    from aletheia.model_provider.base import ModelHealth

    async def fake_health(self):
        return ModelHealth(provider="openai_compatible", available=False, error="HTTP 401")
    monkeypatch.setattr(OpenAICompatibleProvider, "healthcheck", fake_health)

    resp = await client.post("/api/model/test-candidate", json={
        "provider": "openai_compatible",
        "base_url": "http://localhost:9000/v1",
        "api_key": "sk-bad",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert "401" in (body["error"] or "")
    cfg = await client.get("/api/model")
    assert cfg.json()["providers"]["openai_compatible"]["api_key"]["configured"] is False


@pytest.mark.anyio
async def test_model_test_candidate_codex_rejected(client):
    """codex_cli has its own device-auth; candidate-key test is not applicable."""
    resp = await client.post("/api/model/test-candidate", json={"provider": "codex_cli"})
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_model_test_candidate_invalid_provider(client):
    resp = await client.post("/api/model/test-candidate", json={"provider": "nonexistent"})
    assert resp.status_code == 400


# --- Verified-Connection: bitget candidate-cred test (Option Z, verify-before-commit) ---

@pytest.mark.anyio
async def test_bitget_test_candidate_pass(client, monkeypatch):
    """Reachable candidate creds return connected=true, echo no creds, persist nothing."""
    import aletheia.server.routes.bitget as bitget_routes

    class FakeResp:
        def json(self):
            return {"code": "00000", "data": {}}

    class FakeAsyncClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return FakeResp()

    monkeypatch.setattr(bitget_routes.httpx, "AsyncClient", FakeAsyncClient)

    secret = "bg-candidate-secret-5555"
    resp = await client.post("/api/bitget/test-candidate", json={
        "profile": "demo",
        "api_key": "bg-cand-key",
        "api_secret": secret,
        "passphrase": "bg-cand-pass",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is True
    assert body["profile"] == "demo"
    assert secret not in resp.text
    # nothing persisted: demo creds still unconfigured
    cs = await client.get("/api/bitget/credentials-status")
    assert cs.json()["demo"]["api_key"]["configured"] is False


@pytest.mark.anyio
async def test_bitget_test_candidate_fail(client, monkeypatch):
    """Rejected candidate creds return connected=false + bitget error msg, persist nothing."""
    import aletheia.server.routes.bitget as bitget_routes

    class FakeResp:
        def json(self):
            return {"code": "40012", "msg": "apikey does not match"}

    class FakeAsyncClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return FakeResp()

    monkeypatch.setattr(bitget_routes.httpx, "AsyncClient", FakeAsyncClient)

    resp = await client.post("/api/bitget/test-candidate", json={
        "profile": "demo",
        "api_key": "bad", "api_secret": "bad", "passphrase": "bad",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is False
    assert "apikey" in (body["error"] or "")
    cs = await client.get("/api/bitget/credentials-status")
    assert cs.json()["demo"]["api_key"]["configured"] is False


@pytest.mark.anyio
async def test_bitget_test_candidate_invalid_profile(client):
    resp = await client.post("/api/bitget/test-candidate", json={
        "profile": "production", "api_key": "x", "api_secret": "y", "passphrase": "z",
    })
    assert resp.status_code == 400


# --- Verified-Connection: telegram reachability health (facet A only) ---

@pytest.mark.anyio
async def test_telegram_health_not_configured(client):
    """No bot token configured -> reachable false, honest not_configured, no token echo."""
    resp = await client.get("/api/telegram/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reachable"] is False
    assert body.get("error") == "not_configured"


@pytest.mark.anyio
async def test_telegram_health_reachable(client, monkeypatch):
    """A configured + reachable token reports the bot username, never the token."""
    import aletheia.server.routes.telegram as tg_routes
    from aletheia.config.secrets import set_secret

    token = "123456:super-secret-bot-token"
    set_secret("telegram.bot_token", token)

    class FakeResp:
        def json(self):
            return {"ok": True, "result": {"username": "aletheia_bot"}}

    class FakeAsyncClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return FakeResp()

    monkeypatch.setattr(tg_routes, "httpx_client", lambda **kwargs: FakeAsyncClient())

    resp = await client.get("/api/telegram/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reachable"] is True
    assert body["username"] == "aletheia_bot"
    assert token not in resp.text
