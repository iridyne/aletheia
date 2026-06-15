import pytest
from httpx import ASGITransport, AsyncClient

from aletheia.model_provider.base import ModelRequest, ModelResponse
from aletheia.model_provider.router import clear_cache
from aletheia.server.app import app


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


def _setup_config(tmp_path, monkeypatch, *, fallback_models=None):
    import aletheia.config.loader as loader
    from aletheia.config.schema import AletheiaConfig
    from aletheia.config.loader import save_config
    from aletheia.model_provider.router import clear_cache

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    cfg = AletheiaConfig()
    cfg.models.default_provider = "anthropic_compatible"
    pcfg = cfg.models.providers["anthropic_compatible"]
    pcfg.model = "claude-primary"
    pcfg.fallback_models = fallback_models or []
    save_config(cfg)
    clear_cache()
    return cfg


class FakeProvider:
    name = "anthropic_compatible"

    def __init__(self, *, model="claude-primary", fail_models=None, detected=None):
        self.model = model
        self.fail_models = fail_models or {}
        self.detected = detected or []
        self.calls: list[str] = []

    async def complete(self, request):  # noqa: ARG002
        self.calls.append(self.model)
        err = self.fail_models.get(self.model)
        if err:
            raise RuntimeError(err)
        return ModelResponse(content=f"ok:{self.model}", model=self.model, raw={"ok": True})

    async def list_models(self):
        return [{"slug": m, "display_name": m} for m in self.detected]


@pytest.mark.anyio
async def test_complete_with_fallback_tries_configured_model_on_429(tmp_path, monkeypatch):
    _setup_config(tmp_path, monkeypatch, fallback_models=["claude-fallback"])
    from aletheia.model_provider import fallback

    provider = FakeProvider(fail_models={"claude-primary": "429 Too Many Requests"})
    resp = await fallback.complete_with_fallback(
        ModelRequest(messages=[{"role": "user", "content": "x"}]),
        provider=provider,
    )

    assert resp.content == "ok:claude-fallback"
    assert provider.calls == ["claude-primary", "claude-fallback"]
    meta = resp.raw["aletheia_model_fallback"]
    assert meta["fallback_used"] is True
    assert meta["attempted_models"] == ["claude-primary", "claude-fallback"]


@pytest.mark.anyio
async def test_complete_with_fallback_uses_detected_models_when_config_list_empty(tmp_path, monkeypatch):
    _setup_config(tmp_path, monkeypatch, fallback_models=[])
    from aletheia.model_provider import fallback

    provider = FakeProvider(
        fail_models={"claude-primary": "502 Bad Gateway"},
        detected=["claude-primary", "claude-detected"],
    )
    resp = await fallback.complete_with_fallback(
        ModelRequest(messages=[{"role": "user", "content": "x"}]),
        provider=provider,
    )

    assert resp.content == "ok:claude-detected"
    assert provider.calls == ["claude-primary", "claude-detected"]


@pytest.mark.anyio
async def test_complete_with_fallback_does_not_hide_non_transient_errors(tmp_path, monkeypatch):
    _setup_config(tmp_path, monkeypatch, fallback_models=["claude-fallback"])
    from aletheia.model_provider import fallback

    provider = FakeProvider(fail_models={"claude-primary": "401 Unauthorized"})
    with pytest.raises(RuntimeError, match="401 Unauthorized"):
        await fallback.complete_with_fallback(
            ModelRequest(messages=[{"role": "user", "content": "x"}]),
            provider=provider,
        )

    assert provider.calls == ["claude-primary"]


@pytest.mark.anyio
async def test_settings_model_persists_fallback_models_without_key_echo(client):
    secret = "sk-secret-should-not-echo"
    resp = await client.post("/api/settings/model", json={
        "provider": "anthropic_compatible",
        "model_fallback_enabled": True,
        "fallback_models": ["claude-b", " claude-b ", "claude-c"],
        "api_key": secret,
    })
    assert resp.status_code == 200
    assert secret not in resp.text

    cfg = await client.get("/api/model")
    assert secret not in cfg.text
    provider = cfg.json()["providers"]["anthropic_compatible"]
    assert provider["model_fallback_enabled"] is True
    assert provider["fallback_models"] == ["claude-b", "claude-c"]
