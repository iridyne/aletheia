"""Tests for the fail-loud LLM startup gate (aletheia/agent_runtime/startup_gate.py
+ the lifespan wiring in aletheia/server/app.py).

Core guarantee: the gate probes providers with a REAL inference call, so it cannot
be fooled by claude_cli's false-green healthcheck (binary exists → available=True
even when every real call times out). If no provider can complete a real call, the
trading loop is paused (process stays alive) and the state is auditable + visible
via /api/health.

All LLM calls are mocked — no real network or subprocess here.
"""

import asyncio
import sqlite3

import pytest

from aletheia.config.schema import AletheiaConfig, ProviderConfig


def setup_env(tmp_path, monkeypatch):
    """Point DATA_DIR/CONFIG_PATH at tmp and init the DB so write_audit works."""
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    from aletheia.storage.migrations import run_migrations
    sq.reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


def _cfg_with(providers: dict, default: str) -> AletheiaConfig:
    cfg = AletheiaConfig()
    cfg.models.providers = {
        name: ProviderConfig(type=name, enabled=en) for name, en in providers.items()
    }
    cfg.models.default_provider = default
    return cfg


class _FakeResp:
    def __init__(self, content):
        self.content = content


class _FakeProvider:
    """Stand-in provider whose complete() either returns content or raises."""
    def __init__(self, content=None, exc=None, hang=False):
        self._content = content
        self._exc = exc
        self._hang = hang

    async def complete(self, request):
        if self._hang:
            await asyncio.sleep(999)  # forces the wait_for timeout path
        if self._exc:
            raise self._exc
        return _FakeResp(self._content)


def _patch_providers(monkeypatch, mapping: dict):
    """Patch get_provider INSIDE startup_gate to return our fakes by name."""
    from aletheia.agent_runtime import startup_gate

    def fake_get_provider(name):
        if name not in mapping:
            raise ValueError(f"Unknown provider: {name}")
        return mapping[name]

    # startup_gate imports get_provider lazily from router; patch the source.
    import aletheia.model_provider.router as router
    monkeypatch.setattr(router, "get_provider", fake_get_provider)


# --- 1. all providers fail → any_usable False, each error reported ---

def test_all_providers_fail(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import startup_gate
    cfg = _cfg_with({"openai_compatible": True, "anthropic_compatible": True}, "anthropic_compatible")
    _patch_providers(monkeypatch, {
        "openai_compatible": _FakeProvider(exc=RuntimeError("conn refused")),
        "anthropic_compatible": _FakeProvider(exc=RuntimeError("401")),
    })
    result = asyncio.run(startup_gate.probe_llm_providers_at_startup(cfg))
    assert result["any_usable"] is False
    assert result["checked"] == 2
    errs = {r["provider"]: r["error"] for r in result["results"]}
    assert "conn refused" in errs["openai_compatible"]
    assert "401" in errs["anthropic_compatible"]


# --- 2. one provider works → any_usable True ---

def test_one_provider_works(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import startup_gate
    cfg = _cfg_with({"openai_compatible": True, "anthropic_compatible": True}, "anthropic_compatible")
    _patch_providers(monkeypatch, {
        "openai_compatible": _FakeProvider(exc=RuntimeError("down")),
        "anthropic_compatible": _FakeProvider(content="PONG"),
    })
    result = asyncio.run(startup_gate.probe_llm_providers_at_startup(cfg))
    assert result["any_usable"] is True
    # default (anthropic_compatible) is probed first
    assert result["results"][0]["provider"] == "anthropic_compatible"
    assert result["results"][0]["usable"] is True


# --- 3. claude_cli false-green guard: healthcheck would say OK, real call raises ---

def test_claude_cli_false_green_is_not_usable(tmp_path, monkeypatch):
    """The whole reason this gate uses a real complete() instead of healthcheck():
    a provider can pass healthcheck (binary present) yet time out on real inference."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import startup_gate
    cfg = _cfg_with({"claude_cli": True}, "claude_cli")
    # complete() raises like the real claude CLI timeout — NOT usable, despite a
    # healthcheck that would have returned available=True.
    _patch_providers(monkeypatch, {
        "claude_cli": _FakeProvider(exc=RuntimeError("Claude CLI timed out")),
    })
    result = asyncio.run(startup_gate.probe_llm_providers_at_startup(cfg))
    assert result["any_usable"] is False
    assert "timed out" in result["results"][0]["error"]


# --- 3b. empty content counts as NOT usable ---

def test_empty_response_not_usable(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import startup_gate
    cfg = _cfg_with({"openai_compatible": True}, "openai_compatible")
    _patch_providers(monkeypatch, {"openai_compatible": _FakeProvider(content="   ")})
    result = asyncio.run(startup_gate.probe_llm_providers_at_startup(cfg))
    assert result["any_usable"] is False
    assert result["results"][0]["error"] == "empty_response"


# --- 4. probe never raises on unknown provider type ---

def test_probe_never_raises_on_bad_provider(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import startup_gate
    cfg = _cfg_with({"weird": True}, "weird")
    # get_provider raises ValueError for unknown → captured as honest error, no crash.
    _patch_providers(monkeypatch, {})  # mapping empty → every name unknown
    result = asyncio.run(startup_gate.probe_llm_providers_at_startup(cfg))
    assert result["any_usable"] is False
    assert result["checked"] == 1
    assert "Unknown provider" in result["results"][0]["error"]


# --- 4b. no enabled providers → honest empty verdict ---

def test_no_enabled_providers(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import startup_gate
    cfg = _cfg_with({"openai_compatible": False, "claude_cli": False}, "openai_compatible")
    result = asyncio.run(startup_gate.probe_llm_providers_at_startup(cfg))
    assert result["any_usable"] is False
    assert result["checked"] == 0
    assert result["results"] == []


# --- 5. lifespan gate: all-fail pauses + audits + surfaces llm_unavailable ---

def test_startup_gate_pauses_and_audits(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.server import app as appmod
    from aletheia.agent_runtime.health import reset_llm_failures, compute_health

    reset_llm_failures()
    cfg = _cfg_with({"anthropic_compatible": True}, "anthropic_compatible")
    cfg.runtime.paused = False
    _patch_providers(monkeypatch, {
        "anthropic_compatible": _FakeProvider(exc=RuntimeError("connection failed")),
    })

    asyncio.run(appmod._llm_startup_gate(cfg))

    # Loop paused, persisted, and the decision audited.
    assert cfg.runtime.paused is True
    from aletheia.files.jsonl import read_jsonl
    evts = [e for e in read_jsonl("audit.jsonl") if e.get("event_type") == "llm_startup_gate_paused"]
    assert evts
    # /api/health now reports llm_unavailable (no extra plumbing).
    issues = compute_health()["issues"]
    assert any(i.startswith("llm_unavailable") for i in issues)
    reset_llm_failures()


# --- 5b. lifespan gate: a usable provider leaves the loop running + clears counter ---

def test_startup_gate_passes_when_usable(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.server import app as appmod
    from aletheia.agent_runtime.health import reset_llm_failures, get_llm_consecutive_failures

    reset_llm_failures()
    cfg = _cfg_with({"anthropic_compatible": True}, "anthropic_compatible")
    cfg.runtime.paused = False
    _patch_providers(monkeypatch, {"anthropic_compatible": _FakeProvider(content="PONG")})

    asyncio.run(appmod._llm_startup_gate(cfg))

    assert cfg.runtime.paused is False
    assert get_llm_consecutive_failures() == 0


# --- 5c. gate disabled by config → no-op even if all providers are dead ---

def test_startup_gate_respects_disable_flag(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.server import app as appmod

    cfg = _cfg_with({"anthropic_compatible": True}, "anthropic_compatible")
    cfg.runtime.paused = False
    cfg.health.require_llm_at_startup = False
    _patch_providers(monkeypatch, {
        "anthropic_compatible": _FakeProvider(exc=RuntimeError("down")),
    })

    asyncio.run(appmod._llm_startup_gate(cfg))
    assert cfg.runtime.paused is False  # gate skipped entirely
