"""Issue #412: selected-account live trust is broker-specific and fail-closed."""

from datetime import datetime, timedelta, timezone

import pytest

from aletheia.config.schema import AletheiaConfig
from tests.live_identity import bind_live_identity


def _config(monkeypatch, broker: str):
    import aletheia.trust.live_unlock as trust

    cfg = AletheiaConfig()
    cfg.exchange.name = broker
    cfg.exchange.mode = "live"
    cfg.risk.allowed_exchange = broker
    bind_live_identity(monkeypatch, lambda ref: f"secret:{ref}")
    return cfg, trust


def _account_result(*, uid="account-1", environment="live", permissions=("trade",)):
    return {
        "info": {
            "uid": uid,
            "environment": environment,
            "permissions": list(permissions),
        },
    }


@pytest.mark.parametrize("broker,profile,generation,revision,config_revision", [
    ("bitget", "classic_v2", 2, "e5cc2bc4fa7947e245c77d4f81ab3e1acacfca62aa4b7fedb1a457cdf3dc1434", "62abe39f52570e304fd81a7d75e01642eb2c4b3edb8da40103be8db3b8ee262c"),
    ("bitget", "uta_v3", 5, "e5cc2bc4fa7947e245c77d4f81ab3e1acacfca62aa4b7fedb1a457cdf3dc1434", "e36dc17008c5a8bb79a513ca416db0a706be3494b4e9fbb8b27f7499a740b46b"),
    ("okx", "okx_live", 0, "a672a19babf643410474e084618f6b92839b0e4cce3df8d3f0b16b72068aff76", "a8de0f070a66658dae23156f110f460f8ff390f0b852b663d36d538b16aea693"),
    ("binance", "binance_live", 0, "c3373675b3aec36cecc8ea26e7c020eca214023cce91c9af2385918cbfd2cc60", "2113bd513e61e0fee2f1678f6baec045e52225886107b6850da5d200536527b2"),
])
def test_live_identity_preserves_pre_extraction_hashes(monkeypatch, broker, profile, generation, revision, config_revision):
    """Synthetic golden identities measured on a6ed49f74 before extraction."""
    cfg, trust = _config(monkeypatch, broker)
    cfg.exchange.paper_execution_mode = ""
    if broker == "bitget":
        cfg.exchange.bitget_profile = profile
    fields = ("api_key", "api_secret") if broker == "binance" else ("api_key", "api_secret", "passphrase")
    assert trust.resolve_live_execution_identity(cfg) == {
        "broker": broker,
        "execution_mode": f"{broker}_live_limited",
        "account_environment": "live",
        "credential_refs": [f"{broker}.live.{field}" for field in fields],
        "credential_revision": "sha256:" + revision,
        "credential_generation": generation,
        "uta_profile": profile,
        "config_revision": "sha256:" + config_revision,
        "adapter_capability_version": "live-private-account-v1",
    }


@pytest.mark.anyio
async def test_missing_credentials_fail_without_probe(monkeypatch):
    cfg, trust = _config(monkeypatch, "bitget")
    monkeypatch.setattr("aletheia.exchanges.composition.get_secret", lambda _ref: None)

    async def forbidden(*_a, **_k):
        pytest.fail("missing credentials cannot reach the private probe")

    monkeypatch.setattr(trust, "_private_account_probe", forbidden)
    result = await trust.run_live_preflight(cfg)
    assert result["reason_code"] == "credentials_not_configured"
    assert result["ok"] is False


@pytest.mark.parametrize("requested,configured_mode", [("okx_live_limited", "live"), ("bitget_live_limited", "demo")])
def test_revision_reader_cannot_read_unselected_scope(monkeypatch, requested, configured_mode):
    from aletheia.exchanges import composition

    cfg, _trust = _config(monkeypatch, "bitget")
    cfg.exchange.mode = configured_mode

    def forbidden(_ref):
        pytest.fail("unselected account must not resolve credentials")

    monkeypatch.setattr(composition, "get_secret", forbidden)
    with pytest.raises(ValueError, match="selected_live_account_required"):
        composition.read_credential_revision(requested, cfg=cfg)


@pytest.mark.anyio
async def test_credential_revision_failure_never_echoes_secret_store_error(monkeypatch):
    cfg, trust = _config(monkeypatch, "bitget")

    def unavailable(_ref):
        raise ValueError("synthetic-sensitive-store-error")

    monkeypatch.setattr("aletheia.exchanges.composition.get_secret", unavailable)
    result = await trust.run_live_preflight(cfg)
    assert result["reason_code"] == "credential_revision_unavailable"
    assert "synthetic-sensitive-store-error" not in str(result)
    assert result["ok"] is False


@pytest.mark.parametrize("broker,profile", [
    ("bitget", "classic_v2"), ("bitget", "uta_v3"),
    ("okx", "okx_live"), ("binance", "binance_live"),
])
def test_live_identity_uses_scoped_uta_metadata_without_client(monkeypatch, broker, profile):
    from aletheia.exchanges import composition
    from aletheia.uta import manager

    cfg, trust = _config(monkeypatch, broker)
    if broker == "bitget":
        cfg.exchange.bitget_profile = profile
    mode = f"{broker}_live_limited"
    calls = []

    def registry(config, *, execution_modes):
        calls.append(execution_modes)
        return composition.build_uta_registry(config, execution_modes=execution_modes)

    def forbidden(*_a, **_k):
        pytest.fail("identity projection must not construct a client")

    monkeypatch.setattr(manager, "_registry_factory", registry)
    monkeypatch.setattr(composition, "CCXTUTAAdapter", forbidden)
    monkeypatch.setattr(composition, "get_credential_generation", lambda scope: 7)
    identity = trust.resolve_live_execution_identity(cfg)
    assert calls == [(mode,)]
    assert identity["uta_profile"] == profile
    assert identity["credential_generation"] == composition._account_generation(cfg, mode, f"{broker}.live", profile)
    assert identity["execution_mode"] == mode
    assert cfg.live.state == "live_disabled"


@pytest.mark.anyio
async def test_unbound_live_identity_fails_before_private_probe(monkeypatch):
    from aletheia.uta import manager

    cfg, trust = _config(monkeypatch, "bitget")
    monkeypatch.setattr(manager, "_registry_factory", None)

    async def forbidden(*_a, **_k):
        pytest.fail("missing adapter cannot authorize a private probe")

    monkeypatch.setattr(trust, "_private_account_probe", forbidden)
    result = await trust.run_live_preflight(cfg)
    assert result["ok"] is False
    assert result["reason_code"] == "exchange_adapter_unavailable"
    assert cfg.live.state == "live_disabled"


@pytest.mark.parametrize("change", [
    {"execution_mode": "bitget_demo"}, {"venue": "okx"}, {"environment": "demo"},
])
def test_live_identity_rejects_mismatched_uta_account(monkeypatch, change):
    from types import SimpleNamespace
    from aletheia.exchanges import composition
    from aletheia.uta import manager

    cfg, trust = _config(monkeypatch, "bitget")
    account = composition.build_uta_registry(
        cfg, execution_modes=("bitget_live_limited",),
    ).configured_identities()[0].model_copy(update=change)
    monkeypatch.setattr(manager, "_registry_factory", lambda *_a, **_k: SimpleNamespace(configured_identities=lambda: (account,)))
    with pytest.raises(ValueError, match="exchange_adapter_unavailable"):
        trust.resolve_live_execution_identity(cfg)


def _install_uta_probe(
    monkeypatch,
    trust,
    *,
    expected_mode: str,
    uid: str = "account-1",
    environment: str = "live",
    permission: str = "granted",
    error: Exception | None = None,
):
    async def probe(identity, cfg=None):
        assert identity["execution_mode"] == expected_mode
        if error:
            raise error
        return trust._digest({"account_id": uid}), environment, permission

    monkeypatch.setattr(trust, "_private_account_probe", probe)


@pytest.mark.anyio
async def test_okx_preflight_does_not_use_bitget_cache_or_client(monkeypatch):
    cfg, trust = _config(monkeypatch, "okx")
    _install_uta_probe(
        monkeypatch, trust, expected_mode="okx_live_limited", uid="okx-user"
    )

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
    _install_uta_probe(
        monkeypatch,
        trust,
        expected_mode="okx_live_limited",
        error=RuntimeError("selected OKX authentication failed"),
    )

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
    _install_uta_probe(
        monkeypatch,
        trust,
        expected_mode="bitget_live_limited",
        uid="bitget-user",
    )

    class FakeBitget:
        def __init__(self, mode, **_):
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
    _install_uta_probe(
        monkeypatch,
        trust,
        expected_mode="bitget_live_limited",
        uid="bitget-user",
        permission="denied",
    )

    class FakeBitget:
        def __init__(self, mode, **_):
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
    _install_uta_probe(
        monkeypatch,
        trust,
        expected_mode="okx_live_limited",
        uid="okx-user",
        environment="demo",
    )

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
    _install_uta_probe(
        monkeypatch,
        trust,
        expected_mode="binance_live_limited",
        uid="binance-user",
    )

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
    _install_uta_probe(
        monkeypatch, trust, expected_mode="okx_live_limited", uid="okx-user"
    )

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
    _install_uta_probe(
        monkeypatch,
        trust,
        expected_mode="okx_live_limited",
        uid="okx-user",
        permission="unknown",
    )

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

    monkeypatch.setattr("aletheia.exchanges.composition.get_secret", lambda ref: f"rotated:{ref}")
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

    bind_live_identity(monkeypatch, lambda ref: f"secret:{ref}")
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
