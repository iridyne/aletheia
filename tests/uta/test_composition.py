"""Entrypoint composition binds factories without taking credential authority."""

import pytest

from aletheia.exchanges import composition
from aletheia.uta import manager


@pytest.fixture(autouse=True)
def unbound_provider(monkeypatch):
    monkeypatch.setattr(manager, "_registry_factory", None)
    monkeypatch.setattr(manager, "_retention_contract", None)


def test_binding_does_not_resolve_credentials(monkeypatch):
    def forbidden(*_args):
        pytest.fail("binding must not resolve credentials or create clients")

    monkeypatch.setattr(composition, "get_secret", forbidden)
    monkeypatch.setattr(composition, "get_credential_generation", forbidden)
    monkeypatch.setattr(composition, "CCXTUTAAdapter", forbidden)
    composition.bind_uta_provider()
    assert manager._registry_factory is composition.build_uta_registry
    assert manager._retention_contract is composition.funding_history_retention_contract


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
