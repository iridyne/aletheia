"""Tests for model + bitget config API routes (Phase 1).

Critical security invariant: no GET ever returns a raw secret value.
"""

import pytest
from httpx import AsyncClient, ASGITransport

from aletheia.server.app import app
from aletheia.model_provider.router import clear_cache
from aletheia.config.schema import AletheiaConfig


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


def _flatten_keys(value, prefix=""):
    if isinstance(value, dict):
        keys = set()
        for k, v in value.items():
            path = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                keys |= _flatten_keys(v, path)
            else:
                keys.add(path)
        return keys
    return {prefix} if prefix else set()


@pytest.mark.anyio
async def test_get_model_config(client):
    # Providers ship empty now; add one via the API (mirrors a fresh install
    # where the operator configures a provider through Settings).
    await client.post("/api/settings/model", json={"provider": "openai_compatible"})
    resp = await client.get("/api/model")
    assert resp.status_code == 200
    data = resp.json()
    assert "openai_compatible" in data["providers"]
    assert data["providers"]["openai_compatible"]["api_key"]["configured"] is False


@pytest.mark.anyio
async def test_get_inference_broker_metrics_is_sanitized(client):
    response = await client.get("/api/model/broker")

    assert response.status_code == 200
    assert set(response.json()) == {"running", "providers"}
    assert "api_key" not in response.text
    assert "oauth" not in response.text


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
async def test_set_model_inference_limits_are_visible_and_validated(client):
    response = await client.post("/api/settings/model", json={
        "provider": "openai_compatible",
        "inference_max_concurrency": 3,
        "inference_requests_per_minute": 40,
        "inference_tpm": 90_000,
        "inference_queue_capacity": 20,
        "inference_reserved_high_priority_slots": 4,
        "inference_timeout_sec": 180,
    })
    assert response.status_code == 200

    provider = (await client.get("/api/model")).json()["providers"]["openai_compatible"]
    assert provider["inference_max_concurrency"] == 3
    assert provider["inference_requests_per_minute"] == 40
    assert provider["inference_tpm"] == 90_000
    assert provider["inference_queue_capacity"] == 20
    assert provider["inference_reserved_high_priority_slots"] == 4
    assert provider["inference_timeout_sec"] == 180

    invalid = await client.post("/api/settings/model", json={
        "provider": "openai_compatible",
        "inference_queue_capacity": 2,
        "inference_reserved_high_priority_slots": 3,
    })
    assert invalid.status_code == 422


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


@pytest.mark.anyio
async def test_universe_settings_are_visible_and_persisted(client):
    resp = await client.get("/api/settings")
    assert resp.status_code == 200
    assert resp.json()["universe"]["enabled"] is True

    resp = await client.post(
        "/api/settings/universe",
        json={"enabled": False, "max_candidates": 20, "explore_ratio": 0.15},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["universe"]["enabled"] is False
    assert body["universe"]["max_candidates"] == 20

    resp = await client.get("/api/settings")
    assert resp.json()["universe"]["enabled"] is False
    assert resp.json()["universe"]["max_candidates"] == 20


@pytest.mark.anyio
async def test_learning_lifecycle_defaults_are_visible_and_paper_influence_stays_closed(client):
    response = await client.get("/api/settings")
    assert response.status_code == 200
    learning = response.json()["learning"]
    assert learning == {
        "contribution_evaluation_enabled": True,
        "lifecycle_automation_enabled": False,
        "paper_influence_enabled": False,
        "paper_influence_paths": [],
        "auto_refreeze": True,
        "evaluation_interval_minutes": 60,
        "paper_active_allocation_pct": 0.80,
    }


@pytest.mark.anyio
async def test_learning_settings_persist_and_cap_active_paper_allocation(client):
    response = await client.post("/api/settings/learning", json={
        "lifecycle_automation_enabled": True,
        "paper_influence_enabled": True,
        "paper_influence_paths": [" universe_family_calibration ", "universe_family_calibration"],
        "paper_active_allocation_pct": 0.80,
    })
    assert response.status_code == 200
    learning = response.json()["learning"]
    assert learning["paper_influence_paths"] == ["universe_family_calibration"]
    assert learning["paper_active_allocation_pct"] == 0.80

    rejected = await client.post("/api/settings/learning", json={"paper_active_allocation_pct": 0.91})
    assert rejected.status_code == 400

    unsupported = await client.post(
        "/api/settings/learning", json={"paper_influence_paths": ["advisory_skill"]},
    )
    assert unsupported.status_code == 400
    unknown = await client.post(
        "/api/settings/learning", json={"paper_influence_paths": ["made_up_path"]},
    )
    assert unknown.status_code == 400

    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.learning.paper_influence_enabled = False
    cfg.learning.paper_influence_paths = ["advisory_skill"]
    save_config(cfg)
    toggle_only = await client.post(
        "/api/settings/learning", json={"paper_influence_enabled": True},
    )
    assert toggle_only.status_code == 400


@pytest.mark.anyio
async def test_learning_settings_expose_only_wired_paper_path_options(client):
    response = await client.get("/api/settings")
    assert response.status_code == 200
    options = response.json()["learning_path_options"]
    by_id = {option["path_id"]: option for option in options}
    assert by_id["ranker_learned_guard"]["supported"] is True
    assert by_id["advisory_skill"] == {
        "path_id": "advisory_skill",
        "label": "proven hypothesis skill",
        "supported": False,
        "status": "consumer_unwired",
    }


@pytest.mark.anyio
async def test_settings_response_covers_sanitized_config_schema(client):
    """Every non-secret config.yaml key should be visible on Settings."""
    resp = await client.get("/api/settings")
    assert resp.status_code == 200
    actual_keys = _flatten_keys(resp.json())
    expected_keys = _flatten_keys(AletheiaConfig().model_dump(mode="json"))

    secret_suffixes = (
        ".api_key_secret_ref",
        ".oauth_token_ref",
        ".oauth_refresh_ref",
    )
    hidden_keys = {"tunnel.credentials_path", "webhook.secret"}
    expected_visible = {
        key for key in expected_keys
        if not (key.startswith("models.providers.") and key.endswith(secret_suffixes))
        and key not in hidden_keys
    }

    missing = sorted(expected_visible - actual_keys)
    assert not missing

    raw = resp.text.lower()
    assert "api_key_secret_ref" not in raw
    assert "oauth_token_ref" not in raw
    assert "oauth_refresh_ref" not in raw


@pytest.mark.anyio
async def test_get_settings_masks_inline_secret_fields(client):
    """Regression #1: GET /api/settings must not return coinalyze_api_key or proxy credentials raw."""
    from aletheia.config.loader import save_config
    cfg = AletheiaConfig()
    cfg.market_data.coinalyze_api_key = "COINALYZESECRET12345"
    cfg.network.proxy_url = "http://myuser:mypassword@127.0.0.1:7890"
    save_config(cfg)

    resp = await client.get("/api/settings")
    assert resp.status_code == 200
    raw = resp.text
    assert "COINALYZESECRET12345" not in raw
    assert "mypassword" not in raw and "myuser" not in raw

    data = resp.json()
    # Key stays present (UI reads the section) but the value is masked.
    assert "coinalyze_api_key" in data["market_data"]
    assert data["market_data"]["coinalyze_api_key"] != "COINALYZESECRET12345"
    # Proxy host/port kept for diagnostics; only the user:pass is stripped.
    assert "127.0.0.1:7890" in data["network"]["proxy_url"]


@pytest.mark.anyio
async def test_settings_post_does_not_echo_secrets(client):
    """Regression #2/#4: webhook/network POST responses must not echo the submitted secret/creds."""
    r = await client.post("/api/settings/webhook", json={"secret": "WEBHOOKSECRET999"})
    assert r.status_code == 200
    assert "WEBHOOKSECRET999" not in r.text

    r2 = await client.post("/api/settings/network", json={"proxy_url": "http://uu:ppw@10.0.0.9:1080"})
    assert r2.status_code == 200
    assert "ppw" not in r2.text and "uu:ppw" not in r2.text
    # host still surfaced
    assert "10.0.0.9:1080" in r2.text


@pytest.mark.anyio
async def test_tunnel_guard_protects_risk_config_mutation(client):
    """Regression #137: with the tunnel enabled, POST /settings/risk requires the token."""
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.secrets import set_secret
    cfg = load_config()
    cfg.tunnel.enabled = True
    save_config(cfg)
    set_secret("tunnel_api_token", "TUNNELTOKEN123")

    # No token -> blocked (401): risk config cannot be mutated over a public tunnel.
    r = await client.post("/api/settings/risk", json={"max_leverage_major_live": 3})
    assert r.status_code == 401

    # Correct token -> the auth guard passes (route then handles it, not a 401).
    r2 = await client.post(
        "/api/settings/risk",
        json={"max_leverage_major_live": 3},
        headers={"X-Aletheia-Token": "TUNNELTOKEN123"},
    )
    assert r2.status_code != 401


@pytest.mark.anyio
async def test_tunnel_guard_protects_operator_command_surfaces(client):
    """Regression #42: operator command/pause/resume/wake/trigger and capital
    events must be protected by the tunnel token when the tunnel is enabled."""
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.secrets import set_secret
    cfg = load_config()
    cfg.tunnel.enabled = True
    save_config(cfg)
    set_secret("tunnel_api_token", "TUNNELTOKEN42")

    protected_posts = [
        "/api/operator/command",
        "/api/operator/pause",
        "/api/operator/resume",
        "/api/operator/wake",
        "/api/capital/events",
    ]
    for path in protected_posts:
        r = await client.post(path, json={})
        assert r.status_code == 401, f"{path} should be blocked without token, got {r.status_code}"

    # With correct token, the auth guard passes (downstream may 422/400/403, but not 401).
    for path in protected_posts:
        r = await client.post(path, json={}, headers={"X-Aletheia-Token": "TUNNELTOKEN42"})
        assert r.status_code != 401, f"{path} should pass auth guard with token, got {r.status_code}"


def test_restart_audit_actor_source_maps_automated_triggers():
    """Regression #132: automated restart triggers are not recorded as actor='human'."""
    from aletheia.server.routes.gateway import _restart_audit_actor_source
    assert _restart_audit_actor_source("git_hook") == ("system", "git_hook")
    assert _restart_audit_actor_source("file_change") == ("dev_watcher", "dev_watcher")
    assert _restart_audit_actor_source("manual") == ("human", "dashboard")
    # unknown reasons default to human/dashboard (dashboard-originated)
    assert _restart_audit_actor_source("whatever")[0] == "human"


@pytest.mark.anyio
async def test_settings_update_controls_persist_new_config_fields(client):
    resp = await client.post(
        "/api/settings/exchange-markets",
        json={"spot": True, "usdt_futures": True, "paper_execution_mode": "paper_internal"},
    )
    assert resp.status_code == 200
    assert resp.json()["paper_execution_mode"] == "paper_internal"

    resp = await client.post(
        "/api/settings/trading",
        json={"slippage_bps": 8.5, "execution_fee_pct": 0.07, "auto_adopt_enabled": False},
    )
    assert resp.status_code == 200
    assert resp.json()["trading"]["slippage_bps"] == 8.5
    assert resp.json()["trading"]["execution_fee_pct"] == 0.07
    assert resp.json()["trading"]["auto_adopt_enabled"] is False

    resp = await client.post(
        "/api/settings/health-config",
        json={
            "self_heal_enabled": False,
            "self_heal_min_interval_sec": 2400,
            "self_heal_max_per_hour": 1,
            "self_heal_code_drift": False,
            "self_heal_market_data": False,
            "self_heal_port_cleanup": False,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["updated"]["self_heal_enabled"] is False
    assert resp.json()["updated"]["self_heal_min_interval_sec"] == 2400
    assert resp.json()["updated"]["self_heal_max_per_hour"] == 1

    resp = await client.post(
        "/api/settings/universe",
        json={
            "min_depth_10bps_usd": 50000,
            "blacklist": ["doge/usdt", " DOGE/USDT ", "pepe/usdt"],
            "fallback_symbols": ["btc/usdt", "eth/usdt"],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["universe"]["min_depth_10bps_usd"] == 50000
    assert resp.json()["universe"]["blacklist"] == ["DOGE/USDT", "PEPE/USDT"]
    assert resp.json()["universe"]["fallback_symbols"] == ["BTC/USDT", "ETH/USDT"]

    settings = (await client.get("/api/settings")).json()
    assert settings["exchange"]["paper_execution_mode"] == "paper_internal"
    assert settings["trading"]["auto_adopt_enabled"] is False
    assert settings["health"]["self_heal_enabled"] is False
    assert settings["universe"]["fallback_symbols"] == ["BTC/USDT", "ETH/USDT"]


@pytest.mark.anyio
async def test_settings_rejects_mismatched_or_invalid_config_controls(client):
    """UI/API mismatches must fail loudly instead of silently ignoring fields."""
    resp = await client.post("/api/settings/trading", json={"slip_bps": 3})
    assert resp.status_code == 422

    resp = await client.post(
        "/api/settings/exchange-markets",
        json={"spot": True, "usdt_futures": True, "paper_execution_mode": "unknown"},
    )
    assert resp.status_code == 400

    resp = await client.post("/api/settings/universe", json={"fallback_symbols": []})
    assert resp.status_code == 400

    resp = await client.post("/api/settings/market-data", json={"ccxt_exchange": "unknown"})
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_market_data_validation_is_atomic_before_save(client):
    from aletheia.config.loader import load_config

    before = load_config().market_data.ccxt_fallback
    resp = await client.post(
        "/api/settings/market-data",
        json={"ccxt_fallback": "okx", "ohlcv_cache_ttl_min": 999},
    )
    assert resp.status_code == 400
    assert load_config().market_data.ccxt_fallback == before


@pytest.mark.anyio
async def test_market_data_exchange_divergence_requires_explicit_confirmation(client):
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.exchange.paper_execution_mode = "okx_demo"
    cfg.exchange.name = "okx"
    cfg.market_data.ccxt_exchange = "okx"
    save_config(cfg)

    rejected = await client.post(
        "/api/settings/market-data",
        json={"ccxt_exchange": "binance"},
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "exchange_source_divergence_confirmation_required"
    assert load_config().market_data.ccxt_exchange == "okx"

    confirmed = await client.post(
        "/api/settings/market-data",
        json={"ccxt_exchange": "binance", "confirm_exchange_divergence": True},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["exchange_alignment"]["status"] == "diverged"
    assert load_config().market_data.ccxt_exchange == "binance"


@pytest.mark.anyio
async def test_execution_target_change_also_requires_divergence_confirmation(client):
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.market_data.ccxt_exchange = "binance"
    save_config(cfg)
    body = {"spot": True, "usdt_futures": True, "paper_execution_mode": "okx_demo"}

    rejected = await client.post("/api/settings/exchange-markets", json=body)
    assert rejected.status_code == 409
    assert load_config().exchange.paper_execution_mode == "paper_internal"

    body["confirm_exchange_divergence"] = True
    confirmed = await client.post("/api/settings/exchange-markets", json=body)
    assert confirmed.status_code == 200
    assert confirmed.json()["exchange_alignment"]["status"] == "diverged"
    assert load_config().exchange.paper_execution_mode == "okx_demo"

# --- Codex subscription provider routes ---

@pytest.mark.anyio
async def test_get_model_includes_codex_model_no_token(client):
    await client.post("/api/settings/model", json={"provider": "codex_cli"})
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

    # Seed the provider (no key) so we can prove the probe adds none.
    await client.post("/api/settings/model", json={"provider": "openai_compatible"})

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

    # Seed the provider (no key) so we can prove the probe adds none.
    await client.post("/api/settings/model", json={"provider": "openai_compatible"})

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
