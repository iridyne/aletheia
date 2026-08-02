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
from aletheia.config.loader import save_config, _invalidate_config_cache


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
    # The startup/resume gate is off by default now (fresh installs ship with no
    # providers). These tests exercise the gate's ACTIVE behavior, so opt in;
    # individual tests that check the disabled path flip it back to False.
    cfg.health.require_llm_at_startup = True
    cfg.models.providers = {
        name: ProviderConfig(type=name, enabled=en) for name, en in providers.items()
    }
    cfg.models.default_provider = default
    # Persist to disk so the lifespan gate's fresh on-disk reload (app.py reads a
    # fresh config, not the caller's snapshot) sees require_llm_at_startup=True.
    # Without this the reload would fall back to the blank AletheiaConfig() default
    # (require_llm_at_startup=False), and the auto-resume loop would immediately
    # un-pause a system the test expects to stay paused. Production always has a
    # config.yaml on disk, so this mirrors reality. Callers that probe cfg directly
    # are unaffected; callers that flip require_llm_at_startup=False re-persist.
    save_config(cfg)
    _invalidate_config_cache()
    return cfg


class _FakeResp:
    def __init__(self, content, raw=None):
        self.content = content
        self.raw = raw  # mirrors ModelResponse.raw (Any = None); empty-content path reads it


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

    # Loop paused, persisted, and the decision audited. The gate persists via a
    # fresh on-disk read-modify-write (not by mutating the boot-snapshot cfg it
    # was handed), so assert the PERSISTED state, not the caller's stale object.
    from aletheia.config.loader import load_config
    assert load_config().runtime.paused is True
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


# --- 6. retry: first attempt fails, second succeeds → any_usable True ---

def test_retry_succeeds_on_second_attempt(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import startup_gate

    cfg = _cfg_with({"anthropic_compatible": True}, "anthropic_compatible")
    cfg.health.startup_gate_max_retries = 3
    cfg.health.startup_gate_retry_delay_sec = 0

    call_count = {"n": 0}
    orig_probe_one = startup_gate._probe_one

    async def flaky_probe_one(name):
        call_count["n"] += 1
        if call_count["n"] <= 1:
            return {"provider": name, "usable": False, "error": "timeout>20s"}
        return {"provider": name, "usable": True, "error": None}

    monkeypatch.setattr(startup_gate, "_probe_one", flaky_probe_one)

    result = asyncio.run(startup_gate.probe_llm_providers_at_startup(cfg))
    assert result["any_usable"] is True
    assert result["attempts"] == 2
    assert call_count["n"] == 2


# --- 7. retry: all attempts fail → any_usable False, attempts == max ---

def test_retry_all_attempts_fail(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import startup_gate

    cfg = _cfg_with({"anthropic_compatible": True}, "anthropic_compatible")
    cfg.health.startup_gate_max_retries = 3
    cfg.health.startup_gate_retry_delay_sec = 0

    _patch_providers(monkeypatch, {
        "anthropic_compatible": _FakeProvider(exc=RuntimeError("timeout")),
    })

    result = asyncio.run(startup_gate.probe_llm_providers_at_startup(cfg))
    assert result["any_usable"] is False
    assert result["attempts"] == 3


# --- 8. notification is called on pause ---

def test_notification_called_on_pause(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.server import app as appmod

    cfg = _cfg_with({"anthropic_compatible": True}, "anthropic_compatible")
    cfg.runtime.paused = False
    cfg.health.startup_gate_max_retries = 1
    cfg.health.startup_gate_retry_delay_sec = 0
    _patch_providers(monkeypatch, {
        "anthropic_compatible": _FakeProvider(exc=RuntimeError("connection failed")),
    })

    toast_calls = []
    monkeypatch.setattr(
        "aletheia.notifications.send_windows_toast",
        lambda title, msg: toast_calls.append((title, msg)),
    )

    asyncio.run(appmod._llm_startup_gate(cfg))

    assert len(toast_calls) == 1
    assert "Paused" in toast_calls[0][0]


def test_resume_gate_blocks_when_no_provider_usable(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import resume_gate
    from aletheia.config.loader import load_config, save_config

    cfg = _cfg_with({"anthropic_compatible": True}, "anthropic_compatible")
    cfg.runtime.paused = True
    save_config(cfg)

    async def fake_probe(_cfg):
        return {
            "any_usable": False,
            "checked": 1,
            "results": [
                {"provider": "anthropic_compatible", "usable": False, "error": "429 Too Many Requests"}
            ],
        }

    monkeypatch.setattr(resume_gate, "probe_llm_providers_at_startup", fake_probe)

    result = asyncio.run(resume_gate.resume_if_llm_available(actor="human", source="test"))

    assert result["allowed"] is False
    assert result["reason"] == "llm_unavailable"
    assert load_config().runtime.paused is True
    from aletheia.files.jsonl import read_jsonl
    events = [e for e in read_jsonl("audit.jsonl") if e.get("event_type") == "gateway_resume_blocked"]
    assert events


def test_resume_gate_allows_when_one_provider_usable(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import resume_gate
    from aletheia.config.loader import load_config, save_config

    cfg = _cfg_with({"anthropic_compatible": True, "openai_compatible": True}, "anthropic_compatible")
    cfg.runtime.paused = True
    save_config(cfg)

    async def fake_probe(_cfg):
        return {
            "any_usable": True,
            "checked": 2,
            "results": [
                {"provider": "anthropic_compatible", "usable": True, "error": None},
                {"provider": "openai_compatible", "usable": False, "error": "down"},
            ],
        }

    monkeypatch.setattr(resume_gate, "probe_llm_providers_at_startup", fake_probe)

    result = asyncio.run(resume_gate.resume_if_llm_available(actor="human", source="test"))

    assert result["allowed"] is True
    assert load_config().runtime.paused is False


def test_resume_gate_keeps_startup_behavior_blocker_paused(tmp_path, monkeypatch):
    """A usable LLM cannot override an active Startup Behavior Gate blocker.

    This is the manual-resume counterpart of the startup gate's durable pause:
    an active reconciliation incident must stay paused until the gate itself
    evaluates cleanly.  Before the regression fix, this path resumed solely on
    the LLM probe and silently cleared the gate-owned pause.
    """
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import resume_gate
    from aletheia.agent_runtime import startup_behavior_gate
    from aletheia.config.loader import load_config, save_config

    cfg = _cfg_with({"anthropic_compatible": True}, "anthropic_compatible")
    cfg.health.startup_behavior_gate_enabled = True
    cfg.runtime.paused = True
    save_config(cfg)

    probe_called = False

    async def usable_probe(_cfg):
        nonlocal probe_called
        probe_called = True
        return {"any_usable": True, "checked": 1, "results": []}

    monkeypatch.setattr(resume_gate, "probe_llm_providers_at_startup", usable_probe)
    monkeypatch.setattr(
        startup_behavior_gate,
        "evaluate_startup_gate",
        lambda _cfg: {
            "passed": False,
            "blockers": ["reconciliation_incident"],
            "checks": [],
            "user_blocked": False,
            "user_block_reason": None,
        },
    )

    result = asyncio.run(resume_gate.resume_if_llm_available(actor="human", source="test"))

    assert result["allowed"] is False
    assert result["reason"] == "startup_behavior_gate_blocked"
    assert result["blockers"] == ["reconciliation_incident"]
    assert probe_called is False
    assert load_config().runtime.paused is True


def test_resume_gate_keeps_real_reconciliation_incident_paused(tmp_path, monkeypatch):
    """A durable SQLite incident blocks manual resume before the LLM probe."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import resume_gate
    from aletheia.config.loader import load_config, save_config
    from aletheia.portfolio import reconciliation_integrity as integrity

    cfg = _cfg_with({"anthropic_compatible": True}, "anthropic_compatible")
    cfg.health.startup_behavior_gate_enabled = True
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.runtime.paused = True
    cfg.learning.paper_influence_enabled = False
    cfg.learning.lifecycle_automation_enabled = False
    save_config(cfg)
    _invalidate_config_cache()
    integrity.record_reconciliation_integrity_incident(
        {
            "is_thrashing": True,
            "window_minutes": 30,
            "churn_count": 9,
            "churned_identities": ["BTC/USDT"],
            "reopened_by_source": {"reconcile": 9},
        }
    )

    probe_called = False

    async def usable_probe(_cfg):
        nonlocal probe_called
        probe_called = True
        return {"any_usable": True, "checked": 1, "results": []}

    monkeypatch.setattr(resume_gate, "probe_llm_providers_at_startup", usable_probe)

    result = asyncio.run(resume_gate.resume_if_llm_available(actor="human", source="test"))

    assert result["allowed"] is False
    assert result["reason"] == "startup_behavior_gate_blocked"
    assert "reconciliation_incident" in result["blockers"]
    assert probe_called is False
    assert load_config().runtime.paused is True


# --- 9. DNS failure short-circuits the retry loop ---

def test_dns_failure_short_circuits_retries(tmp_path, monkeypatch):
    """When ALL providers fail with DNS-class errors, retries are skipped.

    A getaddrinfo failure won't resolve within retry_delay; burning
    max_retries × retry_delay of startup time serves no purpose. The gate
    must return after exactly 1 attempt and never await the retry sleep.

    Patches at the provider boundary (not _probe_one) so the REAL _probe_one
    catch path runs and tags the result with dns_error=True.
    """
    setup_env(tmp_path, monkeypatch)
    import httpx
    from aletheia.agent_runtime import startup_gate

    cfg = _cfg_with({"openai_compatible": True}, "openai_compatible")
    cfg.health.startup_gate_max_retries = 3
    cfg.health.startup_gate_retry_delay_sec = 99  # would hang the test if not short-circuited

    # httpx.ConnectError wrapping a gaierror — _is_dns_error matches the message.
    _patch_providers(monkeypatch, {
        "openai_compatible": _FakeProvider(exc=httpx.ConnectError("[Errno 11001] getaddrinfo failed")),
    })

    slept: list = []
    async def fake_sleep(s):
        slept.append(s)
    monkeypatch.setattr(startup_gate.asyncio, "sleep", fake_sleep)

    result = asyncio.run(startup_gate.probe_llm_providers_at_startup(cfg))
    assert result["any_usable"] is False
    assert result["attempts"] == 1   # short-circuited, not 3
    assert slept == []               # the 99s retry sleep was never awaited


# --- 10. mixed DNS + transient failures still retry (don't over-short-circuit) ---

def test_mixed_dns_and_non_dns_failures_still_retry(tmp_path, monkeypatch):
    """If one provider fails DNS but another fails transiently (503), retries
    must continue — only an ALL-DNS round is hopeless."""
    setup_env(tmp_path, monkeypatch)
    import httpx
    from aletheia.agent_runtime import startup_gate

    cfg = _cfg_with({"a": True, "b": True}, "a")
    cfg.health.startup_gate_max_retries = 2
    cfg.health.startup_gate_retry_delay_sec = 0

    _patch_providers(monkeypatch, {
        "a": _FakeProvider(exc=httpx.ConnectError("getaddrinfo failed")),
        "b": _FakeProvider(exc=RuntimeError("503 Service Unavailable")),  # transient, not DNS
    })

    result = asyncio.run(startup_gate.probe_llm_providers_at_startup(cfg))
    assert result["any_usable"] is False
    assert result["attempts"] == 2   # ran all retries — no short-circuit


# --- 11. Issue #390: probe orchestration exception must not skip the gate ---

def test_startup_gate_probe_exception_causes_pause(tmp_path, monkeypatch):
    """Issue #390: when probe_llm_providers_at_startup raises an unexpected
    exception the scheduler must be paused (fail-safe), not silently skipped
    (fail-open).  The old code returned early, leaving the scheduler running."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.server import app as appmod
    from aletheia.agent_runtime import startup_gate
    from aletheia.agent_runtime.health import reset_llm_failures
    import aletheia.notifications as notif_mod

    reset_llm_failures()
    cfg = _cfg_with({"anthropic_compatible": True}, "anthropic_compatible")
    cfg.runtime.paused = False

    # Simulate a probe-level orchestration crash (not a per-provider failure).
    monkeypatch.setattr(
        startup_gate, "probe_llm_providers_at_startup",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("unexpected orchestration error")),
    )
    # Prevent notify_operator from trying to connect to Telegram (no I/O in tests).
    monkeypatch.setattr(notif_mod, "notify_operator", lambda *a, **kw: None)
    # Prevent start_auto_resume_task from spawning an asyncio Task that outlives the
    # asyncio.run() call and causes the event-loop teardown to hang.
    import aletheia.agent_runtime.auto_resume as ar_mod
    monkeypatch.setattr(ar_mod, "start_auto_resume_task", lambda *a, **kw: None)

    asyncio.run(appmod._llm_startup_gate(cfg))

    from aletheia.config.loader import load_config
    assert load_config().runtime.paused is True, (
        "Startup gate probe exception must pause the scheduler (fail-safe), not skip the gate"
    )
    reset_llm_failures()
