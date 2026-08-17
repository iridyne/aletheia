"""Issue #412: selected-account live trust is broker-specific and fail-closed."""

from datetime import datetime, timedelta, timezone

import pytest

from aletheia.config.schema import AletheiaConfig


def _config(monkeypatch, broker: str):
    import aletheia.trust.live_unlock as trust

    cfg = AletheiaConfig()
    cfg.exchange.name = broker
    cfg.exchange.mode = "live"
    cfg.risk.allowed_exchange = broker
    monkeypatch.setattr(trust, "get_secret", lambda ref: f"secret:{ref}")
    return cfg, trust


def _account_result(*, uid="account-1", environment="live", permissions=("trade",)):
    return {
        "info": {
            "uid": uid,
            "environment": environment,
            "permissions": list(permissions),
        },
    }


@pytest.mark.anyio
async def test_okx_preflight_does_not_use_bitget_cache_or_client(monkeypatch):
    cfg, trust = _config(monkeypatch, "okx")

    class FakeOKX:
        def __init__(self, mode):
            assert mode == "live"

        async def fetch_balance(self):
            return _account_result(uid="okx-user")

        async def close(self):
            pass

    class UnexpectedBitget:
        def __init__(self, mode):
            raise AssertionError("selected OKX preflight must not construct Bitget")

    import aletheia.exchanges.okx.client as okx_client
    import aletheia.exchanges.bitget.client as bitget_client

    monkeypatch.setattr(okx_client, "OKXClient", FakeOKX)
    monkeypatch.setattr(bitget_client, "BitgetClient", UnexpectedBitget)

    result = await trust.run_live_preflight(cfg)
    assert result["ok"] is True
    assert result["broker"] == "okx"
    assert result["execution_mode"] == "okx_live_limited"
    assert result["account_environment"] == "live"
    assert result["account_fingerprint"].startswith("sha256:")
    assert "okx-user" not in result["account_fingerprint"]


@pytest.mark.anyio
async def test_valid_bitget_does_not_rescue_invalid_selected_okx(monkeypatch):
    cfg, trust = _config(monkeypatch, "okx")

    class InvalidOKX:
        def __init__(self, mode):
            pass

        async def fetch_balance(self):
            raise RuntimeError("selected OKX authentication failed")

        async def close(self):
            pass

    class ValidBitget:
        def __init__(self, mode):
            pass

        async def get_account(self):
            return type("Account", (), {"raw": {"userId": "bitget-user", "permissions": ["trade"]}, "user_id": "bitget-user"})()

        async def close(self):
            pass

    import aletheia.exchanges.okx.client as okx_client
    import aletheia.exchanges.bitget.client as bitget_client

    monkeypatch.setattr(okx_client, "OKXClient", InvalidOKX)
    monkeypatch.setattr(bitget_client, "BitgetClient", ValidBitget)
    result = await trust.run_live_preflight(cfg)
    assert result["ok"] is False
    assert result["reason_code"] == "connection_failed"


@pytest.mark.anyio
async def test_bitget_live_account_uses_same_private_contract(monkeypatch):
    cfg, trust = _config(monkeypatch, "bitget")

    class FakeBitget:
        def __init__(self, mode):
            assert mode == "live"

        async def get_account(self):
            return type("Account", (), {"raw": {"userId": "bitget-user", "authorities": ["read", "trade"]}, "user_id": "bitget-user"})()

        async def close(self):
            pass

    import aletheia.exchanges.bitget.client as bitget_client

    monkeypatch.setattr(bitget_client, "BitgetClient", FakeBitget)
    result = await trust.run_live_preflight(cfg)
    assert result["ok"] is True
    assert result["broker"] == "bitget"
    assert result["execution_mode"] == "bitget_live_limited"
    assert result["permission_status"] == "granted"


@pytest.mark.anyio
async def test_bitget_denied_authority_is_reported_without_losing_private_facts(monkeypatch):
    cfg, trust = _config(monkeypatch, "bitget")

    class FakeBitget:
        def __init__(self, mode):
            assert mode == "live"

        async def get_account(self):
            return type("Account", (), {
                "raw": {"userId": "bitget-user", "authorities": ["read"]},
                "user_id": "bitget-user",
            })()

        async def close(self):
            pass

    import aletheia.exchanges.bitget.client as bitget_client

    monkeypatch.setattr(bitget_client, "BitgetClient", FakeBitget)
    result = await trust.run_live_preflight(cfg)
    assert result["ok"] is False
    assert result["reason_code"] == "permission_denied"
    assert result["account_environment"] == "live"
    assert result["permission_status"] == "denied"
    assert result["private_authenticated"] is True
    assert result["account_readable"] is True


@pytest.mark.anyio
async def test_demo_payload_fails_live_preflight(monkeypatch):
    cfg, trust = _config(monkeypatch, "okx")

    class FakeOKX:
        def __init__(self, mode):
            pass

        async def fetch_balance(self):
            return _account_result(environment="demo")

        async def close(self):
            pass

    import aletheia.exchanges.okx.client as okx_client

    monkeypatch.setattr(okx_client, "OKXClient", FakeOKX)
    result = await trust.run_live_preflight(cfg)
    assert result["ok"] is False
    assert result["reason_code"] == "environment_mismatch"


@pytest.mark.anyio
async def test_binance_selected_account_uses_binance_private_probe(monkeypatch):
    cfg, trust = _config(monkeypatch, "binance")

    class FakeBinance:
        def __init__(self, mode):
            assert mode == "live"

        async def fetch_balance(self):
            return _account_result(uid="binance-user")

        async def close(self):
            pass

    import aletheia.exchanges.binance.client as binance_client

    monkeypatch.setattr(binance_client, "BinanceClient", FakeBinance)
    result = await trust.run_live_preflight(cfg)
    assert result["ok"] is True
    assert result["broker"] == "binance"
    assert result["execution_mode"] == "binance_live_limited"


@pytest.mark.anyio
async def test_account_fingerprint_change_invalidates_bound_evidence(monkeypatch):
    cfg, trust = _config(monkeypatch, "okx")

    class FakeOKX:
        def __init__(self, mode):
            pass

        async def fetch_balance(self):
            return _account_result(uid="okx-user")

        async def close(self):
            pass

    import aletheia.exchanges.okx.client as okx_client

    monkeypatch.setattr(okx_client, "OKXClient", FakeOKX)
    evidence = await trust.run_live_preflight(cfg)
    assert evidence["ok"] is True
    assert trust.evidence_matches_config(cfg, evidence) == (True, "ok")
    evidence["account_fingerprint"] = "sha256:another-account"
    assert trust.evidence_matches_config(cfg, evidence) == (False, "account_fingerprint_mismatch")


@pytest.mark.anyio
async def test_permission_unknown_is_explicitly_denied(monkeypatch):
    cfg, trust = _config(monkeypatch, "okx")

    class FakeOKX:
        def __init__(self, mode):
            pass

        async def fetch_balance(self):
            return {"info": {"uid": "okx-user", "environment": "live"}}

        async def close(self):
            pass

    import aletheia.exchanges.okx.client as okx_client

    monkeypatch.setattr(okx_client, "OKXClient", FakeOKX)
    result = await trust.run_live_preflight(cfg)
    assert result["ok"] is False
    assert result["reason_code"] == "permission_unknown"
    assert result["private_authenticated"] is True
    assert result["account_readable"] is True


def test_evidence_expires_and_binds_credential_revision(monkeypatch):
    cfg, trust = _config(monkeypatch, "bitget")
    identity = trust.resolve_live_execution_identity(cfg)
    now = datetime.now(timezone.utc)
    evidence = {
        **identity,
        "ok": True,
        "permission_status": "granted",
        "fetched_at": (now - timedelta(seconds=301)).isoformat(),
        "expires_at": (now + timedelta(seconds=1)).isoformat(),
    }
    valid, reason = trust.evidence_matches_config(cfg, evidence)
    assert valid is False
    assert reason == "stale_unlock_evidence"

    monkeypatch.setattr(trust, "get_secret", lambda ref: f"rotated:{ref}")
    valid, reason = trust.evidence_matches_config(cfg, evidence)
    assert valid is False
    assert reason == "credential_revision_mismatch"


def test_active_selected_live_profile_is_locked(monkeypatch):
    cfg, trust = _config(monkeypatch, "okx")
    cfg.live.state = "live_limited"
    assert trust.active_live_profile_conflict(cfg, "okx", "live") is True
    assert trust.active_live_profile_conflict(cfg, "okx", "demo") is False
    assert trust.active_live_profile_conflict(cfg, "bitget", "live") is False


def _route_evidence(cfg, monkeypatch):
    from datetime import datetime, timedelta, timezone
    import aletheia.trust.live_unlock as trust

    monkeypatch.setattr(trust, "get_secret", lambda ref: f"secret:{ref}")
    identity = trust.resolve_live_execution_identity(cfg)
    now = datetime.now(timezone.utc)
    evidence = {
        **identity,
        "ok": True,
        "reason_code": "ok",
        "account_fingerprint": "sha256:route-account",
        "private_authenticated": True,
        "account_readable": True,
        "environment_verified": True,
        "permission_status": "granted",
        "completeness": "complete",
        "fetched_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=300)).isoformat(),
    }
    evidence["evidence_binding"] = trust._evidence_binding(evidence)
    return evidence


def test_live_unlock_persists_and_lock_clears_selected_evidence(tmp_path, monkeypatch):
    import yaml
    import aletheia.config.loader as loader
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from aletheia.config.loader import load_config

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    sq.reset_db()
    run_migrations()
    cfg = AletheiaConfig()
    cfg.exchange.name = "okx"
    cfg.exchange.mode = "live"
    cfg.risk.allowed_exchange = "okx"
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg.model_dump(mode="json")), encoding="utf-8")
    evidence = _route_evidence(cfg, monkeypatch)

    async def fake_preflight(_cfg):
        return evidence

    monkeypatch.setattr("aletheia.server.routes.live.run_live_preflight", fake_preflight)
    monkeypatch.setattr(
        "aletheia.analytics.go_live_scorecard.compute_scorecard",
        lambda **kwargs: {"composite_score": 95.0, "traffic_light": "green", "blockers": []},
    )
    from aletheia.server.routes.live import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/api/settings/live-unlock",
        json={"confirm": True, "acknowledge_first_unlock": True},
    )
    assert response.status_code == 200, response.text
    saved = load_config()
    assert saved.live.unlock_evidence["broker"] == "okx"
    assert saved.live.unlock_evidence["account_fingerprint"] == "sha256:route-account"

    from aletheia.storage.repositories import list_audit_events
    import json as json_module

    unlock_event = next(
        event for event in list_audit_events(limit=20)
        if event["event_type"] == "live_unlocked"
    )
    after = json_module.loads(unlock_event["after_state"])
    assert after["live_execution_identity"]["broker"] == "okx"
    assert after["live_execution_identity"]["account_fingerprint"] == "sha256:route-account"

    lock = client.post("/api/settings/live-lock")
    assert lock.status_code == 200
    assert load_config().live.unlock_evidence is None


def test_live_unlock_rechecks_config_revision_after_preflight(tmp_path, monkeypatch):
    import yaml
    import aletheia.config.loader as loader
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    cfg = AletheiaConfig()
    cfg.exchange.name = "okx"
    cfg.exchange.mode = "live"
    cfg.risk.allowed_exchange = "okx"
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg.model_dump(mode="json")), encoding="utf-8")
    evidence = _route_evidence(cfg, monkeypatch)

    async def fake_preflight(_cfg):
        changed = loader.load_config()
        changed.exchange.name = "binance"
        loader.save_config(changed)
        return evidence

    monkeypatch.setattr("aletheia.server.routes.live.run_live_preflight", fake_preflight)
    monkeypatch.setattr(
        "aletheia.analytics.go_live_scorecard.compute_scorecard",
        lambda **kwargs: {"composite_score": 95.0, "traffic_light": "green", "blockers": []},
    )
    from aletheia.server.routes.live import router

    app = FastAPI()
    app.include_router(router)
    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/settings/live-unlock",
        json={"confirm": True, "acknowledge_first_unlock": True},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "live_unlock_config_revision_conflict"
