"""Tests for the Codex device-auth login flow (web-triggered, in-memory, no token).

Real OAuth is owned by the codex CLI; we spawn `codex login --device-auth`, parse
the verification URL + user code from its stdout to show in the web UI, and poll
`codex login status` for the authoritative result. We never store a token.
All subprocess interaction is mocked.
"""

import asyncio

import pytest


class _FakeStdout:
    """Async readline() over a fixed list of byte lines, then EOF (b'')."""

    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
        if self._lines:
            return self._lines.pop(0)
        return b""


class _FakeProc:
    def __init__(self, stdout_lines=None, returncode=0):
        self.stdout = _FakeStdout(stdout_lines or [])
        self.returncode = returncode
        self.killed = False

    async def communicate(self, data=None):
        # drain remaining lines
        out = b""
        while True:
            line = await self.stdout.readline()
            if not line:
                break
            out += line
        return out, b""

    async def wait(self):
        return self.returncode

    def kill(self):
        self.killed = True


def _factory(device_proc=None, status_rc=0):
    async def fake_exec(*args, **kwargs):
        if "--device-auth" in args:
            return device_proc
        if "status" in args:
            return _FakeProc(returncode=status_rc)
        return _FakeProc(returncode=0)
    return fake_exec


@pytest.fixture(autouse=True)
def _reset_session(monkeypatch):
    # Each test starts from a clean in-memory session.
    import aletheia.model_provider.codex_auth as auth
    auth._session = auth.CodexLoginSession()
    yield


@pytest.mark.anyio
async def test_start_login_parses_url_and_code(monkeypatch, tmp_path):
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    import aletheia.model_provider.codex_auth as auth
    monkeypatch.setattr(auth.shutil, "which", lambda c: "/usr/bin/codex")

    device = _FakeProc(stdout_lines=[
        b"Starting device authorization...\n",
        b"Visit https://auth.openai.com/device to authorize\n",
        b"Enter the code: ABCD-1234\n",
    ])
    monkeypatch.setattr("asyncio.create_subprocess_exec", _factory(device_proc=device))

    result = await auth.start_login()
    assert result["state"] == "awaiting_authorization"
    assert result["verification_uri"].startswith("https://auth.openai.com/device")
    assert result["user_code"] == "ABCD-1234"
    # never any token field
    assert "token" not in str(result).lower()
    auth.cancel()


@pytest.mark.anyio
async def test_start_login_unparseable_fails(monkeypatch, tmp_path):
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    import aletheia.model_provider.codex_auth as auth
    monkeypatch.setattr(auth.shutil, "which", lambda c: "/usr/bin/codex")

    device = _FakeProc(stdout_lines=[b"some unexpected output\n", b"no url here\n"], returncode=1)
    monkeypatch.setattr("asyncio.create_subprocess_exec", _factory(device_proc=device))

    result = await auth.start_login()
    assert result["state"] == "failed"
    assert result["error"] == "device_auth_parse_failed"


@pytest.mark.anyio
async def test_start_login_codex_absent(monkeypatch, tmp_path):
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    import aletheia.model_provider.codex_auth as auth
    monkeypatch.setattr(auth.shutil, "which", lambda c: None)
    result = await auth.start_login()
    assert result["state"] == "failed"
    assert result["error"] == "not_installed"


@pytest.mark.anyio
async def test_poll_status_completes_then_cancel(monkeypatch, tmp_path):
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    import aletheia.model_provider.codex_auth as auth
    monkeypatch.setattr(auth.shutil, "which", lambda c: "/usr/bin/codex")
    # login status returns rc 0 → completed
    monkeypatch.setattr("asyncio.create_subprocess_exec", _factory(status_rc=0))

    status = await auth.poll_status()
    assert status["state"] == "completed"
    assert "token" not in str(status).lower()

    cancelled = auth.cancel()
    assert cancelled["state"] == "cancelled"


# --- subprocess creationflags (no wt window on Win11) ---

@pytest.mark.anyio
async def test_codex_auth_passes_create_no_window_on_windows(monkeypatch, tmp_path):
    """Both start_login and poll_status subprocess calls must pass creationflags on Win."""
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    import aletheia.model_provider.codex_auth as auth
    import aletheia.gateway.pidfile as pidfile
    monkeypatch.setattr(pidfile.platform, "system", lambda: "Windows")
    monkeypatch.setattr(auth.shutil, "which", lambda c: "/usr/bin/codex")

    captured = []

    async def capturing_exec(*args, **kwargs):
        captured.append({"argv": list(args), "kwargs": kwargs})
        if "--device-auth" in args:
            return _FakeProc(stdout_lines=[
                b"Visit https://auth.openai.com/device to authorize\n",
                b"Enter the code: ABCD-1234\n",
            ])
        return _FakeProc(returncode=0)

    monkeypatch.setattr("asyncio.create_subprocess_exec", capturing_exec)

    await auth.start_login()
    # start_login spawns one process
    assert len(captured) >= 1
    flags = captured[0]["kwargs"].get("creationflags", 0)
    assert flags & 0x08000000 == 0x08000000

    # poll_status spawns another
    captured.clear()
    await auth.poll_status()
    assert len(captured) >= 1
    flags = captured[0]["kwargs"].get("creationflags", 0)
    assert flags & 0x08000000 == 0x08000000
