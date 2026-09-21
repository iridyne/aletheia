"""Model credential ingress is optional; Core retains selection authority."""

from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("missing", ["ingress", "extensions.model_endpoints", "extensions"])
def test_absent_model_ingress_keeps_core_routes_and_cli_importable(missing):
    script = f"missing = {missing!r}\n" + """
import importlib
import sys
blocked = {'extensions.model_endpoints.credential_routes', 'extensions.model_endpoints.credential_cli'}
if missing != 'ingress':
    blocked = {missing}
class MissingIngress:
    def find_spec(self, fullname, path=None, target=None):
        if fullname in blocked:
            raise ModuleNotFoundError('absent implementation', name=fullname)
sys.meta_path.insert(0, MissingIngress())
from aletheia.server.app import app
from aletheia import main
from aletheia.server.routes import model
from aletheia.model_provider import router
assert not any(name in sys.modules for name in blocked)
assert not any(hasattr(model, name) for name in ('get_secret', 'set_secret', 'delete_secret', 'TestCandidateRequest'))
paths = {route.path for route in app.routes}
assert {'/api/model', '/api/model/health', '/api/settings/model-routing'} <= paths
assert '/api/model/test-candidate' not in paths
assert '/api/model/detect-models' not in paths
assert '/api/settings/model-profiles' not in paths
main._configure_runtime()
import asyncio
try:
    asyncio.run(router.verify_model_selection('selected', 'openai_compatible', 'chosen'))
except router.ModelAdapterUnavailable as exc:
    assert str(exc) == 'model_adapter_unavailable'
else:
    raise AssertionError('absent ingress fabricated verification')
from typer.testing import CliRunner
result = CliRunner().invoke(main.app, ['model', 'status'])
assert result.exit_code == 0, result.output
assert 'set' not in {command.name for command in main.model_app.registered_commands}
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.anyio
async def test_core_routing_requires_exact_selection_verification_and_keeps_writer(monkeypatch):
    from fastapi import HTTPException
    from aletheia.config.schema import AletheiaConfig, ProviderConfig
    from aletheia.model_provider import router
    from aletheia.server.routes import model

    cfg = AletheiaConfig()
    cfg.models.providers['selected'] = ProviderConfig(type='openai_compatible')
    monkeypatch.setattr(model, 'load_config', lambda: cfg)
    monkeypatch.setattr(model, 'mutate_config', lambda apply: apply(cfg))
    monkeypatch.setattr(model, 'write_audit', lambda record: None)
    monkeypatch.setattr(router, '_provider_identity_reader', lambda name, pcfg: pcfg.model)
    calls = []

    async def verify(endpoint_id, provider_type, model_id):
        calls.append((endpoint_id, provider_type, model_id))
        assert cfg.models.fast is None
        return {'identity': model_id}

    monkeypatch.setattr(router, '_selection_verifier', verify)
    req = model.ModelRoutingRequest(fast={'endpoint_id': 'selected', 'model_id': 'chosen'})
    await model.update_model_routing(req)
    assert calls == [('selected', 'openai_compatible', 'chosen')]
    assert cfg.models.fast.model_id == 'chosen'
    monkeypatch.setattr(router, '_selection_verifier', None)
    with pytest.raises(HTTPException) as error:
        await model.update_model_routing(req)
    assert (error.value.status_code, error.value.detail) == (503, 'model_adapter_unavailable')
    assert cfg.models.fast.model_id == 'chosen'
    await model.update_model_routing(model.ModelRoutingRequest(fast=None))
    assert cfg.models.fast is None
