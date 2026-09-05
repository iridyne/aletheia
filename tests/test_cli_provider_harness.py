"""Shared CLI harness behavior across existing agent integrations."""

import asyncio
import json
import sys
import threading

import pytest  # pyright: ignore[reportMissingImports]

from aletheia.model_provider.base import ModelRequest, StructuredModelRequest
from aletheia.model_provider.claude_cli import ClaudeCLIProvider
from aletheia.model_provider import cli_harness


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


@pytest.mark.anyio
async def test_run_cli_uses_sync_worker_on_windows(monkeypatch):
    monkeypatch.setattr(cli_harness.platform, "system", lambda: "Windows")
    monkeypatch.setattr(cli_harness, "_no_window_kwargs", lambda: {})

    async def _forbidden_async_subprocess(*_args, **_kwargs):
        raise AssertionError("asyncio subprocess path must not run on Windows")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _forbidden_async_subprocess)
    result = await cli_harness.run_cli(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read().upper())",
        ],
        stdin=b"ok",
        timeout=5,
    )

    assert result.returncode == 0
    assert result.stdout == b"OK"


@pytest.mark.anyio
async def test_windows_sync_worker_timeout_kills_process(monkeypatch):
    monkeypatch.setattr(cli_harness.platform, "system", lambda: "Windows")
    monkeypatch.setattr(cli_harness, "_no_window_kwargs", lambda: {})
    killed = []

    class _TimeoutProc:
        returncode = None

        def communicate(self, _stdin=None, timeout=None):
            if self.returncode is not None:
                return b"", b""
            raise cli_harness.subprocess.TimeoutExpired("fake-cli", timeout)

        def kill(self):
            killed.append(True)
            self.returncode = -9

        def poll(self):
            return self.returncode

    monkeypatch.setattr(cli_harness.subprocess, "Popen", lambda *_args, **_kwargs: _TimeoutProc())

    with pytest.raises(asyncio.TimeoutError):
        await cli_harness.run_cli(["fake-cli"], timeout=0.01)

    assert killed == [True]


@pytest.mark.anyio
async def test_windows_sync_worker_cancellation_kills_process(monkeypatch):
    monkeypatch.setattr(cli_harness.platform, "system", lambda: "Windows")
    monkeypatch.setattr(cli_harness, "_no_window_kwargs", lambda: {})
    killed = threading.Event()

    class _BlockingProc:
        returncode = None

        def communicate(self, _stdin=None, timeout=None):
            killed.wait(timeout=5)
            return b"", b""

        def kill(self):
            self.returncode = -9
            killed.set()

        def poll(self):
            return self.returncode

    monkeypatch.setattr(cli_harness.subprocess, "Popen", lambda *_args, **_kwargs: _BlockingProc())

    task = asyncio.create_task(cli_harness.run_cli(["fake-cli"], timeout=30))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert killed.wait(timeout=1)


@pytest.mark.anyio
async def test_pi_health_uses_windows_sync_harness(monkeypatch):
    from aletheia.model_provider.pi_cli import PiCLIProvider

    monkeypatch.setattr(cli_harness.platform, "system", lambda: "Windows")
    monkeypatch.setattr(cli_harness, "_no_window_kwargs", lambda: {})

    async def _forbidden_async_subprocess(*_args, **_kwargs):
        raise AssertionError("asyncio subprocess path must not run on Windows")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _forbidden_async_subprocess)
    monkeypatch.setattr(
        "aletheia.model_provider.pi_cli.shutil.which",
        lambda command: command,
    )

    health = await PiCLIProvider(command=sys.executable, enabled=True).healthcheck()

    assert health.available is False
    assert health.error == "no_configured_models"


@pytest.mark.parametrize("provider", ["claude_cli", "codex_cli", "pi_cli"])
def test_cli_json_extraction_handles_quoted_delimiters(provider):
    from importlib import import_module

    extract = import_module(f"aletheia.model_provider.{provider}")._extract_json
    expected = {"message": "a } brace and an escaped \"quote\"", "nested": {"ok": True}}
    assert extract(f"Result: {json.dumps(expected)} trailing prose") == expected
    with pytest.raises(RuntimeError):
        extract("Result: {not valid JSON}")
