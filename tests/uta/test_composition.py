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

    def command():
        assert manager._registry_factory is composition.build_uta_registry

    monkeypatch.setattr(main, "app", command)
    monkeypatch.setattr(main.atexit, "register", lambda *_args: None)
    main._entry_point()


@pytest.mark.asyncio
async def test_server_binds_before_background_startup(monkeypatch):
    import importlib

    server = importlib.import_module("aletheia.server.app")
    monkeypatch.setattr(server, "bootstrap", lambda: object())
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
