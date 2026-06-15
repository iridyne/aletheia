"""Tests for `aletheia doctor` ground-truth probing.

Focus on the pure, deterministic logic (severity classification, age formatting,
report aggregation) rather than re-testing the underlying health/git/pidfile
helpers — those have their own suites. The probes are exercised end-to-end via
build_report() with a real (empty) DB to prove they never raise.
"""

from datetime import datetime, timezone, timedelta

from aletheia.diagnostics import doctor as dx


class _FakeStdout:
    def __init__(self, encoding: str):
        self.encoding = encoding


def _iso_ago(minutes: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


# --- age helpers ---

def test_age_minutes_parses_aware_and_naive():
    assert dx._age_minutes(None) is None
    assert dx._age_minutes("not-a-date") is None
    aware = dx._age_minutes(_iso_ago(10))
    assert aware is not None and 9 < aware < 12
    # naive timestamp is treated as UTC
    naive = (datetime.now(timezone.utc) - timedelta(minutes=10)).replace(tzinfo=None).isoformat()
    assert dx._age_minutes(naive) is not None


def test_fmt_age_buckets():
    assert dx._fmt_age(None) == "unknown"
    assert dx._fmt_age(5) == "5min ago"
    assert dx._fmt_age(90).endswith("h ago")
    assert dx._fmt_age(60 * 24 * 2).endswith("d ago")


def test_doctor_cli_glyphs_fall_back_when_stdout_encoding_cannot_encode(monkeypatch):
    import aletheia.cli.doctor_cmd as doctor_cmd
    import sys

    monkeypatch.setattr(sys, "stdout", _FakeStdout("gbk"))

    assert doctor_cmd._glyph("ok") == "OK"
    assert doctor_cmd._glyph("blocker") == "X"


def test_doctor_cli_arrow_falls_back_when_stdout_encoding_cannot_encode(monkeypatch):
    import aletheia.cli.doctor_cmd as doctor_cmd
    import sys

    monkeypatch.setattr(sys, "stdout", _FakeStdout("ascii"))

    assert doctor_cmd._arrow() == "->"


def test_doctor_cli_glyphs_use_unicode_when_stdout_supports_it(monkeypatch):
    import aletheia.cli.doctor_cmd as doctor_cmd
    import sys

    monkeypatch.setattr(sys, "stdout", _FakeStdout("utf-8"))

    assert doctor_cmd._glyph("ok") == "✓"
    assert doctor_cmd._glyph("blocker") == "✗"
    assert doctor_cmd._arrow() == "→"


# --- Report aggregation ---

def test_report_severity_partitions():
    r = dx.Report(generated_at="t")
    r.add("a", "ok", "fine")
    r.add("b", "warn", "hmm")
    r.add("c", "blocker", "stop")
    r.add("d", "info", "fyi")
    assert [c.name for c in r.blockers] == ["c"]
    assert [c.name for c in r.warns] == ["b"]


# --- end-to-end: probes must never raise, even on a fresh/empty environment ---

def test_build_report_runs_and_covers_all_dimensions():
    report = dx.build_report()
    names = {c.name for c in report.checks}
    # every dimension contributes at least its primary check
    for expected in (
        "code.sync", "process.gateway", "config.permission",
        "config.kill_switch", "creds.bitget_demo", "data.market",
        "loop.activity", "health.rollup",
    ):
        # process.autostart is also expected on Windows, but stays optional so the
        # suite passes on CI/Linux where Task Scheduler is absent.
        assert expected in names, f"missing probe: {expected}"
    # generated_at is a real ISO timestamp
    assert "T" in report.generated_at


def test_kill_switch_active_is_a_blocker(monkeypatch):
    """An active kill switch must surface as a blocker (it rejects every trade)."""
    import aletheia.config.loader as loader
    cfg = loader.load_config()
    cfg.runtime.kill_switch_active = True
    # _probe_config_gates imports load_config from aletheia.config.loader at call time
    monkeypatch.setattr(loader, "load_config", lambda: cfg)

    r = dx.Report(generated_at="t")
    dx._probe_config_gates(r)
    ks = [c for c in r.checks if c.name == "config.kill_switch"]
    assert ks and ks[0].severity == "blocker"


def test_llm_credentials_use_current_models_secret_ref(monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    import aletheia.config.loader as loader
    import aletheia.config.secrets as secrets

    cfg = AletheiaConfig()
    cfg.models.default_provider = "anthropic_compatible"
    cfg.models.providers["anthropic_compatible"].enabled = True
    cfg.models.providers["anthropic_compatible"].api_key_secret_ref = "model.gateway.api_key"
    monkeypatch.setattr(loader, "load_config", lambda: cfg)
    monkeypatch.setattr(secrets, "get_secret", lambda key: "secret" if key == "model.gateway.api_key" else None)

    r = dx.Report(generated_at="t")
    dx._probe_credentials(r)

    llm = next(c for c in r.checks if c.name == "creds.llm")
    assert llm.severity == "ok"
    assert "anthropic_compatible" in llm.detail


def test_llm_credentials_warn_when_enabled_http_provider_secret_missing(monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    import aletheia.config.loader as loader
    import aletheia.config.secrets as secrets

    cfg = AletheiaConfig()
    cfg.models.default_provider = "anthropic_compatible"
    cfg.models.providers["anthropic_compatible"].enabled = True
    cfg.models.providers["anthropic_compatible"].api_key_secret_ref = "model.gateway.api_key"
    monkeypatch.setattr(loader, "load_config", lambda: cfg)
    monkeypatch.setattr(secrets, "get_secret", lambda key: None)

    r = dx.Report(generated_at="t")
    dx._probe_credentials(r)

    llm = next(c for c in r.checks if c.name == "creds.llm")
    assert llm.severity == "warn"
    assert "model.gateway.api_key" in llm.detail


def test_llm_credentials_do_not_require_api_key_for_cli_provider(monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    import aletheia.config.loader as loader
    import aletheia.config.secrets as secrets

    cfg = AletheiaConfig()
    cfg.models.default_provider = "codex_cli"
    cfg.models.providers["codex_cli"].enabled = True
    cfg.models.providers["codex_cli"].api_key_secret_ref = None
    monkeypatch.setattr(loader, "load_config", lambda: cfg)
    monkeypatch.setattr(secrets, "get_secret", lambda key: None)

    r = dx.Report(generated_at="t")
    dx._probe_credentials(r)

    llm = next(c for c in r.checks if c.name == "creds.llm")
    assert llm.severity == "ok"
    assert "codex_cli" in llm.detail


def test_process_autostart_warns_when_watchdog_never_ran(monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    import aletheia.config.loader as loader
    import aletheia.gateway.pidfile as pidfile
    import aletheia.gateway.autostart as autostart

    cfg = AletheiaConfig()
    monkeypatch.setattr(loader, "load_config", lambda: cfg)
    monkeypatch.setattr(pidfile, "is_running", lambda: False)
    monkeypatch.setattr(pidfile, "read_pid", lambda: None)
    monkeypatch.setattr(pidfile, "port_in_use", lambda port, host: False)
    monkeypatch.setattr(
        autostart,
        "status",
        lambda: {
            "supported": True,
            "enabled": True,
            "task_name": autostart.TASK_NAME,
            "status": "Ready",
            "last_run_time": "1999/11/30 0:00:00",
            "last_result": "267011",
            "primed": False,
        },
    )

    r = dx.Report(generated_at="t")
    dx._probe_process(r)

    autostart_check = next(c for c in r.checks if c.name == "process.autostart")
    assert autostart_check.severity == "warn"
    assert "not run" in autostart_check.detail.lower()


def test_frontdoor_probe_reports_backend_readiness(monkeypatch):
    import json
    import urllib.request

    payload = {
        "role": "frontdoor",
        "pid": 1234,
        "static_index": True,
        "backend_base_url": "http://127.0.0.1:8788",
        "backend": {"ready": True, "status_code": 200},
    }

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    class FakeOpener:
        def open(self, url, timeout=0):  # noqa: ARG002
            return FakeResponse()

    monkeypatch.setattr(urllib.request, "build_opener", lambda *args, **kwargs: FakeOpener())

    r = dx.Report(generated_at="t")
    dx._probe_frontdoor_process(r, "127.0.0.1", 8787)

    checks = {c.name: c for c in r.checks}
    assert checks["process.frontdoor"].severity == "ok"
    assert checks["process.backend"].severity == "ok"
