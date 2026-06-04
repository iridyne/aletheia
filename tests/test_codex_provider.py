"""Tests for the upgraded Codex CLI subscription provider.

Codex is delegated to the locally-authenticated `codex` binary; we never store a
token. All subprocess interaction is mocked (no real codex calls). Covers the
3-state healthcheck, model listing, stdin/JSONL complete, JSON extraction, and
graceful degradation.
"""

import pytest

from aletheia.model_provider.codex_cli import CodexCLIProvider


class _FakeProc:
    """Stand-in for an asyncio subprocess returned by create_subprocess_exec."""

    def __init__(self, stdout=b"", stderr=b"", returncode=0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.stdin_written = None

    async def communicate(self, data=None):
        self.stdin_written = data
        return self._stdout, self._stderr

    def kill(self):
        pass


def _fake_exec_factory(captured, by_subcommand):
    """Return an async create_subprocess_exec replacement.

    `by_subcommand` maps a token found in argv (e.g. "exec", "status", "models")
    to a _FakeProc. `captured` collects each call's argv for assertions.
    """
    async def fake_exec(*args, **kwargs):
        captured.append({"argv": list(args), "kwargs": kwargs})
        for token, proc in by_subcommand.items():
            if token in args:
                return proc
        return _FakeProc(returncode=0)
    return fake_exec


@pytest.mark.anyio
async def test_healthcheck_not_installed(monkeypatch):
    monkeypatch.setattr("aletheia.model_provider.codex_cli.shutil.which", lambda c: None)
    provider = CodexCLIProvider(enabled=True)
    health = await provider.healthcheck()
    assert health.available is False
    assert health.error == "not_installed"


@pytest.mark.anyio
async def test_healthcheck_not_logged_in(monkeypatch):
    monkeypatch.setattr("aletheia.model_provider.codex_cli.shutil.which", lambda c: "/usr/bin/codex")
    captured = []
    procs = {"status": _FakeProc(stdout=b"Not logged in", returncode=1)}
    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec_factory(captured, procs))
    provider = CodexCLIProvider(enabled=True)
    health = await provider.healthcheck()
    assert health.available is False
    assert health.error == "not_logged_in"


@pytest.mark.anyio
async def test_healthcheck_available_with_models(monkeypatch):
    monkeypatch.setattr("aletheia.model_provider.codex_cli.shutil.which", lambda c: "/usr/bin/codex")
    models_json = b'{"models":[{"slug":"gpt-5.5","display_name":"GPT-5.5","visibility":"list"}]}'
    procs = {
        "status": _FakeProc(stdout=b"Logged in using ChatGPT", returncode=0),
        "models": _FakeProc(stdout=models_json, returncode=0),
    }
    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec_factory([], procs))
    provider = CodexCLIProvider(enabled=True)
    health = await provider.healthcheck()
    assert health.available is True
    assert "gpt-5.5" in health.models


@pytest.mark.anyio
async def test_list_models_filters_visibility(monkeypatch):
    monkeypatch.setattr("aletheia.model_provider.codex_cli.shutil.which", lambda c: "/usr/bin/codex")
    models_json = (
        b'{"models":[{"slug":"gpt-5.5","display_name":"GPT-5.5","visibility":"list"},'
        b'{"slug":"hidden","display_name":"Hidden","visibility":"hidden"}]}'
    )
    procs = {"models": _FakeProc(stdout=models_json, returncode=0)}
    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec_factory([], procs))
    provider = CodexCLIProvider(enabled=True)
    models = await provider.list_models()
    slugs = [m["slug"] for m in models]
    assert slugs == ["gpt-5.5"]  # hidden excluded


@pytest.mark.anyio
async def test_list_models_garbage_returns_empty(monkeypatch):
    monkeypatch.setattr("aletheia.model_provider.codex_cli.shutil.which", lambda c: "/usr/bin/codex")
    procs = {"models": _FakeProc(stdout=b"not json at all", returncode=0)}
    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec_factory([], procs))
    provider = CodexCLIProvider(enabled=True)
    assert await provider.list_models() == []


@pytest.mark.anyio
async def test_complete_via_stdin_jsonl(monkeypatch):
    monkeypatch.setattr("aletheia.model_provider.codex_cli.shutil.which", lambda c: "/usr/bin/codex")
    # JSONL with a stray non-JSON ERROR line interleaved (must be skipped).
    stdout = (
        b'{"type":"item.started","item":{"type":"agent_message"}}\n'
        b'codex_memories_write something ERROR not-json-line\n'
        b'{"type":"item.completed","item":{"type":"agent_message","text":"pong"}}\n'
        b'{"type":"turn.completed","usage":{"input_tokens":3,"output_tokens":1}}\n'
    )
    captured = []
    procs = {"exec": _FakeProc(stdout=stdout, returncode=0)}
    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec_factory(captured, procs))

    from aletheia.model_provider.base import ModelRequest
    provider = CodexCLIProvider(enabled=True, model="gpt-5.5")
    resp = await provider.complete(ModelRequest(messages=[{"role": "user", "content": "ping"}]))
    assert resp.content == "pong"
    assert resp.usage.get("output_tokens") == 1
    # prompt passed on stdin, not argv; model flag present
    exec_call = next(c for c in captured if "exec" in c["argv"])
    assert "-m" in exec_call["argv"] and "gpt-5.5" in exec_call["argv"]
    # the fake proc recorded stdin bytes containing the prompt
    # (communicate(data) stored on the proc)


@pytest.mark.anyio
async def test_complete_nonzero_raises(monkeypatch):
    monkeypatch.setattr("aletheia.model_provider.codex_cli.shutil.which", lambda c: "/usr/bin/codex")
    procs = {"exec": _FakeProc(stdout=b"", stderr=b"boom", returncode=1)}
    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec_factory([], procs))
    from aletheia.model_provider.base import ModelRequest
    provider = CodexCLIProvider(enabled=True)
    with pytest.raises(RuntimeError):
        await provider.complete(ModelRequest(messages=[{"role": "user", "content": "x"}]))


@pytest.mark.anyio
async def test_structured_extracts_json_from_prose(monkeypatch):
    monkeypatch.setattr("aletheia.model_provider.codex_cli.shutil.which", lambda c: "/usr/bin/codex")
    stdout = (
        b'{"type":"item.completed","item":{"type":"agent_message",'
        b'"text":"Here is the result: {\\"direction_bias\\": \\"long\\"} hope it helps"}}\n'
        b'{"type":"turn.completed","usage":{}}\n'
    )
    procs = {"exec": _FakeProc(stdout=stdout, returncode=0)}
    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec_factory([], procs))
    from aletheia.model_provider.base import StructuredModelRequest
    from pydantic import BaseModel

    class S(BaseModel):
        direction_bias: str = "neutral"

    provider = CodexCLIProvider(enabled=True)
    data = await provider.structured(StructuredModelRequest(messages=[{"role": "user", "content": "x"}]), S)
    assert data["direction_bias"] == "long"


@pytest.mark.anyio
async def test_graceful_degradation_when_codex_absent(monkeypatch, tmp_path):
    """With codex absent, llm_complete returns (None, error) and never raises."""
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    from aletheia.model_provider.router import clear_cache
    clear_cache()
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.models.default_provider = "codex_cli"
    cfg.models.providers["codex_cli"].enabled = True
    save_config(cfg)
    monkeypatch.setattr("aletheia.model_provider.codex_cli.shutil.which", lambda c: None)

    from aletheia.agents.helpers import llm_complete
    content, err = await llm_complete(system="s", user="u")
    assert content is None
    assert err is not None
    clear_cache()
