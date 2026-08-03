"""Pi CLI provider harness tests.

Pi owns its local authentication. Aletheia invokes only the documented print
surface with tools and project resources disabled, and never stores Pi tokens.
"""

import pytest  # pyright: ignore[reportMissingImports]

from aletheia.model_provider.pi_cli import PiCLIProvider  # pyright: ignore[reportMissingImports]


class _FakeProc:
    def __init__(self, stdout=b"", stderr=b"", returncode=0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.stdin_written = None
        self.killed = False

    async def communicate(self, data=None):
        self.stdin_written = data
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True


@pytest.mark.anyio
async def test_pi_health_disabled():
    health = await PiCLIProvider(enabled=False).healthcheck()

    assert not health.available
    assert health.error == "disabled"


@pytest.mark.anyio
async def test_pi_health_not_installed(monkeypatch):
    monkeypatch.setattr("aletheia.model_provider.pi_cli.shutil.which", lambda _command: None)

    health = await PiCLIProvider(enabled=True).healthcheck()

    assert not health.available
    assert health.error == "not_installed"


@pytest.mark.anyio
async def test_pi_health_lists_configured_models(monkeypatch):
    monkeypatch.setattr(
        "aletheia.model_provider.pi_cli.shutil.which",
        lambda _command: "/usr/local/bin/pi",
    )
    outputs = [
        _FakeProc(stdout=b"0.83.0\n"),
        _FakeProc(
            stdout=(
                b"provider      model           context  max-out  thinking  images\n"
                b"openai-codex gpt-5.6-terra   200K     64K      yes       yes\n"
                b"anthropic    claude-sonnet-4 200K     64K      yes       yes\n"
            )
        ),
    ]

    async def fake_exec(*_args, **_kwargs):
        return outputs.pop(0)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    health = await PiCLIProvider(enabled=True, model="openai-codex/gpt-5.6-terra").healthcheck()

    assert health.available
    assert health.model == "openai-codex/gpt-5.6-terra"
    assert "openai-codex/gpt-5.6-terra" in health.models


@pytest.mark.anyio
async def test_pi_complete_uses_stdin_and_disables_agent_tools(monkeypatch):
    monkeypatch.setattr(
        "aletheia.model_provider.pi_cli.shutil.which",
        lambda _command: "/usr/local/bin/pi",
    )
    captured = {}
    proc = _FakeProc(stdout=b"answer from pi\n")

    async def fake_exec(*args, **kwargs):
        captured["argv"] = list(args)
        captured["kwargs"] = kwargs
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    from aletheia.model_provider.base import ModelRequest

    response = await PiCLIProvider(
        enabled=True,
        model="openai-codex/gpt-5.6-terra",
    ).complete(
        ModelRequest(
            system="System boundary",
            messages=[{"role": "user", "content": "Question"}],
        )
    )

    assert response.content == "answer from pi"
    assert proc.stdin_written and b"System boundary" in proc.stdin_written
    assert b"Question" in proc.stdin_written
    argv = captured["argv"]
    assert "-p" in argv
    assert "--no-tools" in argv
    assert "--no-context-files" in argv
    assert "--no-extensions" in argv
    assert "--no-skills" in argv
    assert "--no-prompt-templates" in argv
    assert "Question" not in argv


def test_router_supports_pi_singleton(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.schema import ProviderConfig
    from aletheia.model_provider.router import clear_cache, get_provider

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    cfg = load_config()
    cfg.models.providers["pi_cli"] = ProviderConfig(
        type="pi_cli",
        enabled=True,
        model="openai-codex/gpt-5.6-terra",
    )
    save_config(cfg)
    clear_cache()

    provider = get_provider("pi_cli")

    assert isinstance(provider, PiCLIProvider)
    clear_cache()
