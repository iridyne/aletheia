"""Every terminal failure inside complete_with_fallback surfaces exactly one
sanitized infra event with an honest severity.

Complements the existing test_model_fallback.py coverage (which asserts
behaviour) by locking down the observability contract: an operator
scanning `list_infra_events` after an incident must see the actual class
of failure (`llm_broker_timeout`, `llm_call_non_transient_error`, …) at
the severity the 3-level scheme in `aletheia.infra.events` supports.
"""

from __future__ import annotations

import httpx
import pytest

from aletheia.infra.events import clear_infra_events, list_infra_events
from aletheia.model_provider.base import ModelRequest, ModelResponse
from aletheia.model_provider.broker import (
    InferenceBrokerClosed,
    InferenceQueueFull,
    InferenceRateLimitExceeded,
    InferenceTimeout,
)
from aletheia.model_provider.fallback import (
    _emit_call_failure,
    complete_with_fallback,
    reset_model_health,
)

pytestmark = pytest.mark.real_llm


class _FailProvider:
    """A minimal provider whose complete() raises a configurable exception.

    Bypasses the tracing decorator (does not subclass ModelProvider) so we
    can exercise the fallback-loop failure path without touching the ledger.
    """

    def __init__(self, *, model: str, exc: BaseException) -> None:
        self.name = "fallback_test"
        self.model = model
        self._exc = exc
        self.calls: list[str] = []

    async def complete(self, request):  # noqa: ARG002
        self.calls.append(self.model)
        raise self._exc

    async def list_models(self):
        return []


@pytest.fixture(autouse=True)
def _fresh_infra_events():
    clear_infra_events()
    reset_model_health()
    yield
    clear_infra_events()
    reset_model_health()


def _find_events(code: str) -> list[dict]:
    return [e for e in list_infra_events(limit=100) if e["code"] == code]


# ── _emit_call_failure — unit-test each branch of the severity ladder ────────


def test_emit_broker_closed_is_critical():
    _emit_call_failure(
        InferenceBrokerClosed("draining"),
        provider_name="anthropic_native", model="claude-x",
        attempted=["claude-x"], transient=False, exhausted=False,
    )
    events = _find_events("llm_broker_closed")
    assert len(events) == 1
    assert events[0]["severity"] == "critical"
    assert events[0]["entity_id"] == "anthropic_native/claude-x"


def test_emit_broker_queue_full_is_warning():
    _emit_call_failure(
        InferenceQueueFull("64/64"),
        provider_name="openai_native", model="gpt-5",
        attempted=["gpt-5"], transient=False, exhausted=False,
    )
    events = _find_events("llm_broker_queue_full")
    assert len(events) == 1
    assert events[0]["severity"] == "warning"
    assert events[0]["action"] == "raise_queue_capacity_or_reduce_load"


def test_emit_broker_rate_limit_exceeded_is_warning():
    _emit_call_failure(
        InferenceRateLimitExceeded("200000 > 120000 TPM"),
        provider_name="anthropic_native", model="claude-x",
        attempted=["claude-x"], transient=False, exhausted=False,
    )
    events = _find_events("llm_broker_rate_limit_exceeded")
    assert len(events) == 1
    assert events[0]["severity"] == "warning"


def test_emit_broker_timeout_is_warning():
    _emit_call_failure(
        InferenceTimeout("300s"),
        provider_name="anthropic_native", model="claude-x",
        attempted=["claude-x"], transient=False, exhausted=False,
    )
    events = _find_events("llm_broker_timeout")
    assert len(events) == 1
    assert events[0]["severity"] == "warning"


def test_emit_exhausted_is_warning_and_includes_attempted_list():
    _emit_call_failure(
        RuntimeError("429 last one"),
        provider_name="anthropic_native", model="claude-fallback",
        attempted=["claude-primary", "claude-fallback"],
        transient=True, exhausted=True,
    )
    events = _find_events("llm_call_exhausted")
    assert len(events) == 1
    assert events[0]["severity"] == "warning"
    assert events[0]["metadata"]["attempted_models"] == ["claude-primary", "claude-fallback"]


def test_emit_non_transient_is_warning():
    _emit_call_failure(
        ValueError("401 unauthorized"),
        provider_name="anthropic_native", model="claude-x",
        attempted=["claude-x"], transient=False, exhausted=False,
    )
    events = _find_events("llm_call_non_transient_error")
    assert len(events) == 1
    assert events[0]["severity"] == "warning"


def test_emit_transient_but_not_exhausted_yields_no_event():
    """Mid-loop transient failures are not final; ledger owns per-attempt evidence."""
    _emit_call_failure(
        RuntimeError("429 retry"),
        provider_name="anthropic_native", model="claude-primary",
        attempted=["claude-primary"], transient=True, exhausted=False,
    )
    assert _find_events("llm_call_non_transient_error") == []
    assert _find_events("llm_call_exhausted") == []


# ── Integration: complete_with_fallback triggers the right emit path ─────────


def _setup_provider_config(tmp_path, monkeypatch, *, fallback_models=None):
    import aletheia.config.loader as loader
    from aletheia.config.loader import save_config
    from aletheia.config.schema import AletheiaConfig, ProviderConfig
    from aletheia.model_provider.router import clear_cache

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    cfg = AletheiaConfig()
    cfg.models.default_provider = "anthropic_compatible"
    pcfg = cfg.models.providers["anthropic_compatible"] = ProviderConfig(
        type="anthropic_compatible", enabled=True
    )
    pcfg.model = "claude-primary"
    pcfg.fallback_models = fallback_models or []
    save_config(cfg)
    clear_cache()


@pytest.mark.anyio
async def test_non_transient_error_emits_infra_event(tmp_path, monkeypatch):
    _setup_provider_config(tmp_path, monkeypatch, fallback_models=[])
    provider = _FailProvider(
        model="claude-primary", exc=RuntimeError("401 unauthorized"),
    )

    with pytest.raises(RuntimeError, match="401 unauthorized"):
        await complete_with_fallback(
            ModelRequest(messages=[{"role": "user", "content": "x"}]),
            provider=provider,
        )

    events = _find_events("llm_call_non_transient_error")
    assert len(events) == 1
    assert events[0]["metadata"]["provider"] == "anthropic_compatible"
    assert events[0]["metadata"]["model"] == "claude-primary"


@pytest.mark.anyio
async def test_exhausted_transient_errors_emit_infra_event(tmp_path, monkeypatch):
    _setup_provider_config(tmp_path, monkeypatch, fallback_models=["claude-secondary"])
    provider = _FailProvider(
        model="claude-primary",
        exc=httpx.HTTPStatusError(
            "429",
            request=httpx.Request("POST", "https://example/"),
            response=httpx.Response(429),
        ),
    )

    # The real provider only has one model; monkeypatch fallback candidates
    # to include a second so the loop runs twice and exhausts.
    import aletheia.model_provider.fallback as _fb

    async def _both_models(name, prov):  # noqa: ARG001
        return ["claude-primary", "claude-secondary"]

    monkeypatch.setattr(_fb, "_candidate_models", _both_models)

    with pytest.raises(httpx.HTTPStatusError):
        await complete_with_fallback(
            ModelRequest(messages=[{"role": "user", "content": "x"}]),
            provider=provider,
        )

    events = _find_events("llm_call_exhausted")
    assert len(events) == 1
    assert events[0]["severity"] == "warning"
    assert events[0]["metadata"]["attempted_models"] == [
        "claude-primary",
        "claude-secondary",
    ]


@pytest.mark.anyio
async def test_broker_timeout_emits_infra_event(tmp_path, monkeypatch):
    """When brokered_complete raises InferenceTimeout, the emit path fires."""
    _setup_provider_config(tmp_path, monkeypatch, fallback_models=[])
    import aletheia.model_provider.fallback as _fb

    async def _raise_timeout(provider_name, provider, request, **kwargs):  # noqa: ARG001
        raise InferenceTimeout("300s")

    monkeypatch.setattr(_fb, "brokered_complete", _raise_timeout)
    provider = _FailProvider(model="claude-primary", exc=RuntimeError("unreachable"))

    with pytest.raises(InferenceTimeout):
        await complete_with_fallback(
            ModelRequest(messages=[{"role": "user", "content": "x"}]),
            provider=provider,
        )

    events = _find_events("llm_broker_timeout")
    assert len(events) == 1
    assert events[0]["severity"] == "warning"
