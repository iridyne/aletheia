"""Core routing is importable without concrete model transports."""

from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("entrypoint", ["aletheia.model_provider.router", "aletheia.server.app"])
def test_model_router_does_not_import_concrete_transports(entrypoint):
    script = f"""
import importlib
import sys
blocked = ('aletheia.model_provider.http_runtime', 'aletheia.model_provider.openai_compatible', 'aletheia.model_provider.anthropic_compatible', 'openai', 'anthropic')
class RejectTransport:
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == item or fullname.startswith(item + '.') for item in blocked):
            raise ModuleNotFoundError('transport unavailable', name=fullname)
sys.meta_path.insert(0, RejectTransport())
importlib.import_module({entrypoint!r})
from aletheia.model_provider import router
assert not any(name == item or name.startswith(item + '.') for name in sys.modules for item in blocked)
from aletheia.config.schema import AletheiaConfig, ProviderConfig
cfg = AletheiaConfig()
cfg.models.providers['endpoint'] = ProviderConfig(type='openai_compatible', base_url='https://model.test', enabled=True)
profile = cfg.models.providers['endpoint']
profile.api_key_secret_ref = 'synthetic-reference'
profile.verification_identity = 'historical-identity'
profile.verification_verified = True
router.load_config = lambda: cfg
assert not hasattr(router, 'get_secret')
try:
    router.get_provider('endpoint')
except ValueError as exc:
    assert str(exc) == 'model_adapter_unavailable'
else:
    raise AssertionError('missing implementation fabricated a provider')
import asyncio
health = asyncio.run(router.healthcheck_all(force=True))
selected = next(row for row in health if row.provider == 'endpoint')
assert selected.available is False and selected.error == 'model_adapter_unavailable'
assert selected.verified is None and selected.verification_identity is None
assert selected.verification_detail == 'model_adapter_unavailable'
assert router.provider_credential_status('endpoint', profile) == {{'configured': None, 'last4': None, 'unavailable_reason': 'model_adapter_unavailable'}}
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_core_constructor_port_preserves_cache_and_model_selection_without_reading_keys(monkeypatch):
    from types import SimpleNamespace
    from aletheia.config.schema import AletheiaConfig, ModelSelection, ProviderConfig
    from aletheia.model_provider import router

    cfg = AletheiaConfig()
    cfg.models.providers['selected'] = ProviderConfig(
        type='openai_compatible', base_url='https://model.test', model='preferred',
        enabled=True, api_key_secret_ref='model.profile.selected.api_key',
    )
    calls = []

    def factory(name, pcfg):
        calls.append((name, pcfg.api_key_secret_ref))
        return SimpleNamespace(model=pcfg.model)

    def forbidden(_ref):
        pytest.fail('Core construction must not resolve credential values')

    monkeypatch.setattr(router, 'load_config', lambda: cfg)
    monkeypatch.setattr('aletheia.config.secrets.get_secret', forbidden)
    monkeypatch.setattr(router, '_provider_factory', factory)
    router.clear_cache()
    try:
        original = router.get_provider('selected')
        assert router.get_provider('selected') is original
        clone = router.get_provider(ModelSelection(endpoint_id='selected', model_id='another'))
        assert original.model == 'preferred' and clone.model == 'another'
        assert clone._aletheia_provider_name == 'selected'
        assert calls == [('selected', 'model.profile.selected.api_key')]
        monkeypatch.setattr(router, '_provider_factory', None)
        with pytest.raises(ValueError, match='model_adapter_unavailable'):
            router.get_provider('selected')
    finally:
        router.clear_cache()


@pytest.mark.parametrize("provider_type,protocol", [
    ("openai_compatible", "chat_completions"),
    ("openai_compatible", "responses"),
    ("anthropic_compatible", "anthropic_messages"),
])
def test_http_factory_resolves_only_selected_endpoint_reference(monkeypatch, provider_type, protocol):
    from aletheia.config.schema import ProviderConfig
    from aletheia.model_provider import http_runtime

    refs = []

    def secret(ref):
        refs.append(ref)
        return "synthetic-selected-value"

    monkeypatch.setattr(http_runtime, "get_secret", secret)
    cfg = ProviderConfig(
        type=provider_type, protocol=protocol, base_url="https://selected.test", model="chosen",
        api_key_secret_ref="model.profile.selected.api_key", auth_scheme="bearer",
    )
    provider = http_runtime.build_http_provider("selected", cfg)
    assert refs == ["model.profile.selected.api_key"]
    assert (provider.base_url, provider.model, provider.protocol, provider.api_key) == (
        "https://selected.test", "chosen", protocol, "synthetic-selected-value",
    )
    if provider_type == "anthropic_compatible":
        assert provider.auth_scheme == "bearer"


def test_cli_composition_binds_without_constructing_clients_or_resolving_keys(monkeypatch):
    from aletheia import main
    from aletheia.model_provider import http_runtime, router

    def forbidden(_ref):
        pytest.fail("Startup binding must not resolve credentials")

    monkeypatch.setattr(router, "_provider_factory", None)
    monkeypatch.setattr(router, "_provider_identity_reader", None)
    monkeypatch.setattr(router, "_provider_status_reader", None)
    monkeypatch.setattr(router, "_selection_verifier", None)
    monkeypatch.setattr(http_runtime, "get_secret", forbidden)
    monkeypatch.setattr("aletheia.exchanges.composition.bind_uta_provider", lambda: None)
    main._configure_runtime()
    assert router._provider_factory is http_runtime.build_http_provider
    assert router._provider_identity_reader is http_runtime.http_configuration_identity
    assert router._provider_status_reader is http_runtime.http_credential_status
    assert router._selection_verifier is http_runtime.verify_http_model_selection
