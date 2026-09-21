"""Entrypoint composition binds factories without taking credential authority."""

import pytest

from extensions.exchanges import composition
from aletheia.uta import manager


@pytest.fixture(autouse=True)
def unbound_provider(monkeypatch):
    monkeypatch.setattr(manager, "_registry_factory", None)
    monkeypatch.setattr(manager, "_retention_contract", None)
    monkeypatch.setattr(manager, "_credential_revision_reader", None)
    monkeypatch.setattr(manager, "_credential_presence_reader", None)
    monkeypatch.setattr(manager, "_native_protection_reader_factory", None)
    monkeypatch.setattr(manager, "_native_protection_modes", ())


def test_binding_does_not_resolve_credentials(monkeypatch):
    def forbidden(*_args):
        pytest.fail("binding must not resolve credentials or create clients")

    monkeypatch.setattr(composition, "get_secret", forbidden)
    monkeypatch.setattr(composition, "get_credential_generation", forbidden)
    monkeypatch.setattr(composition, "CCXTUTAAdapter", forbidden)
    composition.bind_uta_provider()
    assert manager._registry_factory is composition.build_uta_registry
    assert manager._retention_contract is composition.funding_history_retention_contract
    assert manager._credential_revision_reader is composition.read_credential_revision
    assert manager._credential_presence_reader is composition.read_credential_presence


@pytest.mark.parametrize("mode,scope,fields", [
    ("bitget_demo", "bitget.demo", ("api_key", "api_secret", "passphrase")),
    ("bitget_live_limited", "bitget.live", ("api_key", "api_secret", "passphrase")),
    ("okx_demo", "okx.demo", ("api_key", "api_secret", "passphrase")),
    ("okx_live_limited", "okx.live", ("api_key", "api_secret", "passphrase")),
    ("binance_testnet", "binance.testnet", ("api_key", "api_secret")),
    ("binance_live_limited", "binance.live", ("api_key", "api_secret")),
])
def test_presence_reads_only_requested_references(monkeypatch, mode, scope, fields):
    from aletheia.config.schema import AletheiaConfig

    refs = []
    def secret(ref, *, strict):
        assert strict is True
        refs.append(ref)
        return "synthetic-private-value" if ref.endswith("api_key") else None
    monkeypatch.setattr(composition, "get_secret", secret)
    composition.bind_uta_provider()
    assert manager.read_credential_presence(mode, cfg=AletheiaConfig()) == tuple(field == "api_key" for field in fields)
    assert refs == [f"{scope}.{field}" for field in fields]
    assert manager.read_credential_presence("unknown", cfg=AletheiaConfig()) is None
    assert refs == [f"{scope}.{field}" for field in fields]


def test_presence_keeps_unavailable_and_malformed_evidence_unknown(monkeypatch):
    from aletheia.config.schema import AletheiaConfig

    assert manager.read_credential_presence("bitget_demo") is None
    def failed(*args, **kwargs):
        raise RuntimeError("synthetic-sensitive-detail")
    composition.bind_uta_provider()
    monkeypatch.setattr(composition, "get_secret", failed)
    assert manager.read_credential_presence("bitget_demo", cfg=AletheiaConfig()) is None
    monkeypatch.setattr(manager, "_credential_presence_reader", failed)
    assert manager.read_credential_presence("bitget_demo") is None
    for invalid in (None, (), [], (1,), ("synthetic-private-value",), {"configured": True}):
        monkeypatch.setattr(manager, "_credential_presence_reader", lambda *args, **kwargs: invalid)
        assert manager.read_credential_presence("bitget_demo") is None


@pytest.mark.parametrize("error_type", [ImportError, ValueError])
def test_profile_import_failure_does_not_resolve_secrets_or_echo_error(monkeypatch, caplog, error_type):
    def unavailable(*_args):
        raise error_type("sensitive-import-detail")

    def forbidden(*_args):
        pytest.fail("unavailable profile must not resolve credentials")

    monkeypatch.setattr(composition, "_profile_spec", unavailable)
    monkeypatch.setattr(composition, "get_secret", forbidden)
    if error_type is ImportError:
        assert composition.build_uta_registry(object(), execution_modes=("okx_demo",)).configured_identities() == ()
        assert "exchange_profile_unavailable execution_mode=okx_demo" in caplog.text
        assert "sensitive-import-detail" not in caplog.text
    else:
        with pytest.raises(ValueError, match="sensitive-import-detail"):
            composition.build_uta_registry(object(), execution_modes=("okx_demo",))


def test_core_delegates_only_requested_account_scope():
    calls = []
    registry = manager.UTARegistry()

    def factory(cfg, *, execution_modes):
        calls.append((cfg, execution_modes))
        return registry

    manager.configure_uta_provider(registry_factory=factory, retention_contract=None)
    config = object()
    assert manager.build_uta_registry(config, execution_modes=("bitget_demo",)) is registry
    assert calls == [(config, ("bitget_demo",))]
    with pytest.raises(RuntimeError, match="exchange_adapter_unavailable"):
        manager.funding_history_retention_contract("bitget_demo")


def test_cli_entrypoint_binds_before_dispatch(monkeypatch):
    from aletheia import main
    from typer.testing import CliRunner

    assert CliRunner().invoke(main.app, ["version"]).exit_code == 0
    assert manager._registry_factory is composition.build_uta_registry
    assert manager._credential_presence_reader is composition.read_credential_presence


def test_existing_packaged_dispatch_uses_same_cli_binding(monkeypatch):
    import sys
    from tests.test_packaged_runtime_contract import _entrypoint_module

    entrypoint = _entrypoint_module()
    monkeypatch.setattr(entrypoint, "_install_process_adapter", lambda: None)
    monkeypatch.setattr(sys, "argv", ["aletheia", "version"])
    with pytest.raises(SystemExit) as result:
        entrypoint.main()
    assert result.value.code == 0
    assert manager._registry_factory is composition.build_uta_registry


@pytest.mark.asyncio
async def test_server_binds_before_background_startup(monkeypatch):
    import importlib

    server = importlib.import_module("aletheia.server.app")
    def bootstrap():
        assert manager._registry_factory is composition.build_uta_registry
        assert manager._credential_presence_reader is composition.read_credential_presence
        return object()

    monkeypatch.setattr(server, "bootstrap", bootstrap)
    monkeypatch.setattr("aletheia.logging_config.configure_logging", lambda: None)

    class StopBeforeRuntime(Exception):
        pass

    def stop_before_runtime():
        assert manager._registry_factory is composition.build_uta_registry
        raise StopBeforeRuntime

    monkeypatch.setattr("aletheia.net.apply_proxy_env", stop_before_runtime)
    with pytest.raises(StopBeforeRuntime):
        async with server.lifespan(server.app):
            pytest.fail("no runtime startup is authorized in this test")


@pytest.mark.parametrize("module_name", ["repair_administrative_close", "repair_doge_adoption_overlap"])
def test_maintenance_entrypoints_bind_before_help(monkeypatch, module_name):
    import importlib
    import sys

    module = importlib.import_module("aletheia.maintenance." + module_name)
    monkeypatch.setattr(sys, "argv", [module_name, "--help"])
    with pytest.raises(SystemExit) as result:
        module.main()
    assert result.value.code == 0
    assert manager._registry_factory is composition.build_uta_registry
