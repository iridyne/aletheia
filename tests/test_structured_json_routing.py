"""JSON-mode and glm-first routing for structured trade LLM calls."""

from __future__ import annotations

import json

import pytest

from aletheia.model_provider.base import ModelRequest, ModelResponse


class FakeProvider:
    name = "openai_compatible"

    def __init__(self, *, model="deepseek-v4-flash", fail_models=None):
        self.model = model
        self.fail_models = fail_models or {}
        self.calls: list[str] = []

    async def complete(self, request):  # noqa: ARG002
        self.calls.append(self.model)
        err = self.fail_models.get(self.model)
        if err:
            if isinstance(err, BaseException):
                raise err
            raise RuntimeError(err)
        return ModelResponse(content=f"ok:{self.model}", model=self.model, raw={"ok": True})

    async def list_models(self):
        return []


def _patch_candidates(monkeypatch, models, *, extra_glm=()):
    from aletheia.infra.events import clear_infra_events
    from aletheia.model_provider import fallback

    async def fake_candidates(_provider_name, _provider):
        return list(models)

    monkeypatch.setattr(fallback, "_candidate_models", fake_candidates)
    monkeypatch.setattr(fallback, "_enabled_json_preferred_models", lambda: list(extra_glm))
    fallback.reset_model_health()
    clear_infra_events()


class _ObjectSchema:
    @staticmethod
    def model_json_schema():
        return {"type": "object", "properties": {"value": {"type": "integer"}}}


def test_prefers_json_reliable_models_only_for_trade_json_callers():
    from aletheia.agents.helpers import _prefers_json_reliable_models

    assert _prefers_json_reliable_models("system_operator.assess_opportunity") is True
    assert _prefers_json_reliable_models("system_operator.debate_bull") is True
    assert _prefers_json_reliable_models("risk_critic.review") is True
    assert _prefers_json_reliable_models("agent_shell.portfolio_review") is True
    assert _prefers_json_reliable_models("news_analyzer.analysis") is False
    assert _prefers_json_reliable_models("market_observer.market_read") is False


def test_order_structured_json_candidates_puts_glm_first_without_sinking_flash(monkeypatch):
    from aletheia.model_provider import fallback
    from aletheia.model_provider.fallback import _json_model_rank, _order_structured_json_candidates

    monkeypatch.setattr(fallback, "_enabled_json_preferred_models", lambda: [])
    assert _json_model_rank("glm-5.2") < _json_model_rank("deepseek-v4-flash")
    ordered = _order_structured_json_candidates(
        [
            ("sensenova", "deepseek-v4-flash"),
            ("sensenova", "sensenova-6.8-flash-lite"),
            ("sensenova_glm", "glm-5.2"),
            ("openrouter", "cohere/north-mini-code:free"),
        ]
    )
    assert ordered[0] == ("sensenova_glm", "glm-5.2")
    assert [item[1] for item in ordered[1:]] == [
        "deepseek-v4-flash",
        "sensenova-6.8-flash-lite",
        "cohere/north-mini-code:free",
    ]


@pytest.mark.asyncio
async def test_complete_with_fallback_prefers_glm_for_trade_json(monkeypatch):
    _patch_candidates(monkeypatch, ["deepseek-v4-flash", "glm-5.2"])
    from aletheia.model_provider import fallback

    provider = FakeProvider(model="deepseek-v4-flash")
    resp = await fallback.complete_with_fallback(
        ModelRequest(
            messages=[{"role": "user", "content": "x"}],
            prefer_json_reliable_models=True,
        ),
        provider=provider,
        provider_name="sensenova",
    )

    assert resp.content == "ok:glm-5.2"
    assert provider.calls == ["glm-5.2"]
    assert resp.raw["aletheia_model_fallback"]["requested_model"] == "sensenova/glm-5.2"


@pytest.mark.asyncio
async def test_complete_with_fallback_keeps_flash_first_without_prefer_flag(monkeypatch):
    _patch_candidates(monkeypatch, ["deepseek-v4-flash", "glm-5.2"])
    from aletheia.model_provider import fallback

    provider = FakeProvider(model="deepseek-v4-flash")
    resp = await fallback.complete_with_fallback(
        ModelRequest(messages=[{"role": "user", "content": "x"}]),
        provider=provider,
        provider_name="sensenova",
    )

    assert resp.content == "ok:deepseek-v4-flash"
    assert provider.calls == ["deepseek-v4-flash"]


@pytest.mark.asyncio
async def test_complete_with_fallback_skips_excluded_flash_model(monkeypatch):
    _patch_candidates(monkeypatch, ["deepseek-v4-flash", "glm-5.2"])
    from aletheia.model_provider import fallback

    provider = FakeProvider(model="deepseek-v4-flash")
    resp = await fallback.complete_with_fallback(
        ModelRequest(
            messages=[{"role": "user", "content": "x"}],
            exclude_models=("deepseek-v4-flash",),
        ),
        provider=provider,
        provider_name="sensenova",
    )

    assert resp.content == "ok:glm-5.2"
    assert provider.calls == ["glm-5.2"]


@pytest.mark.asyncio
async def test_complete_with_fallback_falls_back_to_flash_when_glm_unavailable(monkeypatch):
    _patch_candidates(monkeypatch, ["deepseek-v4-flash", "glm-5.2"])
    from aletheia.model_provider import fallback

    provider = FakeProvider(
        model="deepseek-v4-flash",
        fail_models={"glm-5.2": "429 Too Many Requests"},
    )
    resp = await fallback.complete_with_fallback(
        ModelRequest(
            messages=[{"role": "user", "content": "x"}],
            prefer_json_reliable_models=True,
        ),
        provider=provider,
        provider_name="sensenova",
    )

    assert resp.content == "ok:deepseek-v4-flash"
    assert provider.calls == ["glm-5.2", "deepseek-v4-flash"]


@pytest.mark.asyncio
async def test_complete_with_fallback_injects_enabled_glm_provider(monkeypatch):
    _patch_candidates(
        monkeypatch,
        ["deepseek-v4-flash"],
        extra_glm=[("sensenova_glm", "glm-5.2")],
    )
    from aletheia.model_provider import fallback

    flash = FakeProvider(model="deepseek-v4-flash")
    glm = FakeProvider(model="glm-5.2")

    def get_provider(name):
        return {"sensenova": flash, "sensenova_glm": glm}[name]

    monkeypatch.setattr(fallback.provider_router, "get_provider", get_provider)
    resp = await fallback.complete_with_fallback(
        ModelRequest(
            messages=[{"role": "user", "content": "x"}],
            prefer_json_reliable_models=True,
        ),
        provider=flash,
        provider_name="sensenova",
    )

    assert resp.content == "ok:glm-5.2"
    assert glm.calls == ["glm-5.2"]
    assert flash.calls == []


@pytest.mark.asyncio
async def test_llm_structured_requests_json_object_and_skips_failed_flash_model(tmp_path, monkeypatch):
    monkeypatch.setenv("ALETHEIA_DATA_DIR", str(tmp_path))
    from aletheia.agents import helpers

    captured = []
    responses = iter(
        [
            ModelResponse(
                content="not json",
                model="deepseek-v4-flash",
                raw={"usage": {"completion_tokens": 2}},
            ),
            ModelResponse(
                content='{"value": 7}',
                model="glm-5.2",
                raw={"usage": {"completion_tokens": 5}},
            ),
        ]
    )

    async def fake_complete_with_fallback(request, *, provider=None, provider_name=None):
        captured.append(request)
        return next(responses)

    monkeypatch.setattr(helpers, "complete_with_fallback", fake_complete_with_fallback)
    monkeypatch.setattr(helpers, "get_provider", lambda _name: object())
    data, err = await helpers.llm_structured(
        "sys",
        "user",
        schema=_ObjectSchema,
        caller="system_operator.assess_opportunity",
    )

    assert err is None
    assert data == {"value": 7}
    assert len(captured) == 2
    assert all(request.response_format == "json_object" for request in captured)
    assert all(request.prefer_json_reliable_models is True for request in captured)
    assert captured[0].exclude_models == ()
    assert captured[1].exclude_models == ("deepseek-v4-flash",)


@pytest.mark.asyncio
async def test_llm_structured_news_keeps_default_model_order_but_still_uses_json_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("ALETHEIA_DATA_DIR", str(tmp_path))
    from aletheia.agents import helpers

    captured = []

    async def fake_complete_with_fallback(request, *, provider=None, provider_name=None):
        captured.append(request)
        return ModelResponse(
            content='{"value": 1}',
            model="deepseek-v4-flash",
            raw={"usage": {"completion_tokens": 3}},
        )

    monkeypatch.setattr(helpers, "complete_with_fallback", fake_complete_with_fallback)
    monkeypatch.setattr(helpers, "get_provider", lambda _name: object())
    data, err = await helpers.llm_structured(
        "sys",
        "user",
        schema=_ObjectSchema,
        caller="news_analyzer.analysis",
    )

    assert err is None
    assert data == {"value": 1}
    assert captured[0].response_format == "json_object"
    assert captured[0].prefer_json_reliable_models is False


class _FakeHttpResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.url = "http://model.test/v1/chat/completions"
        self.request = type("Req", (), {"headers": {}})()

    def json(self):
        return self._payload

    @property
    def text(self):
        return json.dumps(self._payload)


@pytest.mark.asyncio
async def test_openai_compatible_sends_json_object_response_format(monkeypatch):
    from aletheia.model_provider.openai_compatible import OpenAICompatibleProvider

    payloads = []

    class FakeClient:
        async def post(self, url, json=None, headers=None):  # noqa: ARG002
            payloads.append(json)
            return _FakeHttpResponse(
                200,
                {
                    "choices": [{"message": {"content": '{"ok": true}'}}],
                    "model": "glm-5.2",
                    "usage": {},
                },
            )

        async def aclose(self):
            return None

    monkeypatch.setattr(
        "aletheia.model_provider.openai_compatible.httpx_client",
        lambda *, timeout: FakeClient(),
    )
    provider = OpenAICompatibleProvider(base_url="http://model.test/v1", model="glm-5.2")
    resp = await provider.complete(
        ModelRequest(
            messages=[{"role": "user", "content": "x"}],
            response_format="json_object",
        )
    )
    assert resp.content == '{"ok": true}'
    assert payloads[0]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_openai_compatible_retries_without_json_object_when_unsupported(monkeypatch):
    from aletheia.model_provider.openai_compatible import OpenAICompatibleProvider

    payloads = []

    class FakeClient:
        async def post(self, url, json=None, headers=None):  # noqa: ARG002
            payloads.append(json)
            if len(payloads) == 1:
                return _FakeHttpResponse(
                    400,
                    {
                        "error": {
                            "param": "response_format",
                            "message": "response_format is unsupported",
                        }
                    },
                )
            return _FakeHttpResponse(
                200,
                {
                    "choices": [{"message": {"content": '{"ok": true}'}}],
                    "model": "m1",
                    "usage": {},
                },
            )

        async def aclose(self):
            return None

    monkeypatch.setattr(
        "aletheia.model_provider.openai_compatible.httpx_client",
        lambda *, timeout: FakeClient(),
    )
    provider = OpenAICompatibleProvider(base_url="http://model.test/v1", model="m1")
    resp = await provider.complete(
        ModelRequest(
            messages=[{"role": "user", "content": "x"}],
            response_format="json_object",
        )
    )
    assert resp.content == '{"ok": true}'
    assert payloads[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in payloads[1]
