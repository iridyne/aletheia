"""Shared CLI harness behavior across existing agent integrations."""

import json

import pytest  # pyright: ignore[reportMissingImports]

from aletheia.model_provider.base import ModelRequest, StructuredModelRequest
from aletheia.model_provider.claude_cli import ClaudeCLIProvider


class _FakeProc:
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


@pytest.mark.anyio
async def test_claude_prompt_uses_stdin_not_process_arguments(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-cli")
    monkeypatch.setenv("ALETHEIA_INTERNAL_SECRET", "must-not-reach-cli")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example")
    monkeypatch.setattr(
        "aletheia.model_provider.claude_cli.shutil.which",
        lambda _command: "/usr/local/bin/claude",
    )
    proc = _FakeProc(
        stdout=json.dumps(
            {
                "result": "answer",
                "usage": {},
                "modelUsage": {"claude-sonnet": {}},
            }
        ).encode()
    )
    captured = {}

    async def fake_exec(*args, **kwargs):
        captured["argv"] = list(args)
        captured["kwargs"] = kwargs
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    provider = ClaudeCLIProvider(enabled=True, model="sonnet")

    response = await provider.complete(
        ModelRequest(
            system="private system context",
            messages=[{"role": "user", "content": "private user context"}],
        )
    )

    assert response.content == "answer"
    assert proc.stdin_written and b"private system context" in proc.stdin_written
    assert b"private user context" in proc.stdin_written
    argv = captured["argv"]
    assert "private system context" not in argv
    assert "private user context" not in argv
    assert "--disallowed-tools" in argv
    child_env = captured["kwargs"]["env"]
    assert "OPENAI_API_KEY" not in child_env
    assert "ALETHEIA_INTERNAL_SECRET" not in child_env
    assert child_env["HTTPS_PROXY"] == "http://proxy.example"


@pytest.mark.anyio
async def test_claude_structured_fallback_keeps_system_context_on_stdin(monkeypatch):
    monkeypatch.setattr(
        "aletheia.model_provider.claude_cli.shutil.which",
        lambda _command: "/usr/local/bin/claude",
    )
    proc = _FakeProc(stdout=b'{"structured_output":{"answer":"ok"}}')

    async def fake_exec(*_args, **_kwargs):
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    provider = ClaudeCLIProvider(enabled=True)
    result = await provider._structured_schema_mode(
        StructuredModelRequest(
            system="required system boundary",
            messages=[{"role": "user", "content": "question"}],
        ),
        {"type": "object"},
    )

    assert result == {"answer": "ok"}
    assert proc.stdin_written and b"required system boundary" in proc.stdin_written
    assert b"user: question" in proc.stdin_written
