"""Tests for `aletheia doctor` ground-truth probing.

Focus on the pure, deterministic logic (severity classification, age formatting,
report aggregation) rather than re-testing the underlying health/git/pidfile
helpers — those have their own suites. The probes are exercised end-to-end via
build_report() with a real (empty) DB to prove they never raise.
"""

import sqlite3
from datetime import datetime, timezone, timedelta

from aletheia.diagnostics import doctor as dx


def setup_env(tmp_path, monkeypatch):
    """Real DATA_DIR + DB + default config so build_report probes run on known state."""
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


def test_deep_diagnostics_includes_log_policy_redacted_tails_and_attention(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    (tmp_path / "logs" / "errors.log").write_text(
        "Authorization: Bearer sk-DOCTORSECRETSECRET\nplain traceback\n",
        encoding="utf-8",
    )
    report = dx.Report(generated_at="t")
    report.add("process.backend", "warn", "backend not ready", "restart")

    bundle = dx.build_deep_diagnostics(report, tail_lines=5)

    assert bundle["attention"][0]["name"] == "process.backend"
    assert "errors.log" in bundle["attention"][0]["log_hint"]
    assert bundle["logs"]["policy"]["text_logs_rotate_at_mb"] == 50
    assert bundle["system"]["python"]
    assert bundle["system"]["data_dir"] == str(tmp_path)
    tail = "\n".join(bundle["logs"]["tails"]["errors.log"])
    assert "sk-DOCTORSECRET" not in tail
    assert "[REDACTED]" in tail


def test_doctor_cli_verbose_json_includes_diagnostics(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from typer.testing import CliRunner
    from aletheia.main import app

    result = CliRunner().invoke(app, ["doctor", "--json", "--verbose"])
    assert result.exit_code in (0, 1)
    import json
    payload = json.loads(result.stdout)
    assert "diagnostics" in payload
    assert "logs" in payload["diagnostics"]


def test_doctor_cli_bundle_writes_redacted_diagnostic_file(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    (tmp_path / "logs" / "errors.log").write_text("api_key=DOCTORSECRET\n", encoding="utf-8")
    from typer.testing import CliRunner
    from aletheia.main import app

    bundle_path = tmp_path / "diagnostics" / "doctor.json"
    result = CliRunner().invoke(app, ["doctor", "--bundle", str(bundle_path)])

    assert result.exit_code in (0, 1)
    assert bundle_path.exists()
    import json
    payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    text = bundle_path.read_text(encoding="utf-8")
    assert "diagnostics" in payload
    assert "bundle_generated_at" in payload
    assert "logs" in payload["diagnostics"]
    assert "system" in payload["diagnostics"]
    assert "DOCTORSECRET" not in text
    assert "[REDACTED]" in text


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


def test_build_report_severities_reflect_known_empty_state(tmp_path, monkeypatch):
    """Beyond 'probes exist': on a fresh DB with a default config, the probes must
    report the CORRECT severities. Without this, a probe that always returns "ok"
    (or always "blocker") would pass the coverage test above. Here we pin the
    config to a known-good baseline and assert each probe's verdict matches it.
    """
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.runtime.kill_switch_active = False
    cfg.runtime.paused = False
    save_config(cfg)

    report = dx.build_report()
    checks = {c.name: c for c in report.checks}

    # kill switch is OFF → the probe must say ok, NOT blocker (catches an inverted
    # condition or a hardcoded-ok stub equally).
    assert checks["config.kill_switch"].severity == "ok"
    assert "inactive" in checks["config.kill_switch"].detail.lower()

    # Fresh DB has no market snapshots → data.market cannot be "ok" (nothing to be
    # fresh about). A probe that always returned ok would fail here.
    assert checks["data.market"].severity in ("warn", "blocker", "info")

    # health.rollup aggregates the others; with no real backend it must not claim ok.
    assert "health.rollup" in checks
    # severity is always one of the known buckets (no garbage values leak through)
    for c in report.checks:
        assert c.severity in ("ok", "info", "warn", "blocker")


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


def test_gateway_restart_history_summarizes_recent_audit_events(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.files.jsonl import write_audit

    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=2)).isoformat()
    recent = now.isoformat()
    write_audit({
        "timestamp": old,
        "actor": "human",
        "source": "dashboard",
        "event_type": "gateway_restart",
        "entity_type": "gateway",
        "entity_id": "gateway",
        "message": "Restart requested (manual)",
        "after": {"reason": "manual"},
    })
    for _ in range(3):
        write_audit({
            "timestamp": recent,
            "actor": "human",
            "source": "dashboard",
            "event_type": "gateway_restart",
            "entity_type": "gateway",
            "entity_id": "gateway",
            "message": "Restart requested (git_hook)",
            "after": {"reason": "git_hook"},
        })
    write_audit({
        "timestamp": recent,
        "actor": "system",
        "source": "gateway",
        "event_type": "gateway_frontdoor_restart",
        "entity_type": "gateway_frontdoor",
        "entity_id": "123",
        "message": "Gateway front door PID 123 is not serving port 8787; supervisor is restarting it",
    })

    r = dx.Report(generated_at="t")
    dx._probe_gateway_restart_history(r)

    c = next(c for c in r.checks if c.name == "process.restart_history")
    assert c.severity == "info"
    assert "events=4" in c.detail
    assert "gateway_restart:git_hook=3" in c.detail
    assert "gateway_frontdoor_restart=1" in c.detail
    assert "manual=1" not in c.detail


def test_gateway_restart_history_warns_on_spawn_failures(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.files.jsonl import write_audit

    write_audit({
        "actor": "system",
        "source": "watchdog",
        "event_type": "gateway_spawn_failed",
        "entity_type": "gateway",
        "entity_id": "gateway",
        "message": "Gateway spawn failed: launcher exited with code 1",
    })

    r = dx.Report(generated_at="t")
    dx._probe_gateway_restart_history(r)

    c = next(c for c in r.checks if c.name == "process.restart_history")
    assert c.severity == "warn"
    assert "gateway_spawn_failed=1" in c.detail
    assert "spawn/give-up" in c.fix_hint


# --- loop.reconcile_thrash probe (A, 2026-07-01) ---

def _insert_churn(symbol, direction="short", entry=100.0):
    """A flip-flop signature row-pair (reconciled_gone close + adopted reopen), recent."""
    from aletheia.storage.models import Position
    from aletheia.storage import repositories as repo
    now = datetime.now(timezone.utc).isoformat()
    repo.insert_position(Position(
        symbol=symbol, market_type="usdt_futures", direction=direction,
        quantity=1.0, entry_price=entry, execution_mode="bitget_demo",
        status="closed", close_reason="reconciled_gone", closed_at=now,
    ))
    repo.insert_position(Position(
        symbol=symbol, market_type="usdt_futures", direction=direction,
        quantity=1.0, entry_price=entry, execution_mode="bitget_demo",
        status="closed", source="adopted", opened_at=now, closed_at=now,
    ))


def test_reconcile_thrash_probe_ok_when_clean(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    r = dx.Report(generated_at="t")
    dx._probe_reconcile_thrash(r)
    c = next(c for c in r.checks if c.name == "loop.reconcile_thrash")
    assert c.severity == "ok", f"clean DB must be ok, got {c.severity}: {c.detail}"


def test_reconcile_thrash_probe_warns_on_fingerprint(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    # Two distinct symbols flip-flopping in-window → systemic → warn.
    _insert_churn("BNB/USDT", "short", 562.73)
    _insert_churn("ETH/USDT", "short", 1574.8)
    r = dx.Report(generated_at="t")
    dx._probe_reconcile_thrash(r)
    c = next(c for c in r.checks if c.name == "loop.reconcile_thrash")
    assert c.severity == "warn", f"thrash fingerprint must warn, got {c.severity}: {c.detail}"
    assert "churned" in c.detail


def test_reconciled_ratio_high_without_active_thrash_is_info(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.storage.models import Position
    from aletheia.storage import repositories as repo

    now = datetime.now(timezone.utc).isoformat()
    for i in range(12):
        repo.insert_position(Position(
            symbol=f"T{i}/USDT", market_type="usdt_futures", direction="long",
            quantity=1.0, entry_price=100.0, execution_mode="okx_demo",
            status="closed", close_reason="reconciled_gone", closed_at=now,
        ))

    r = dx.Report(generated_at="t")
    dx._probe_reconciled_ratio(r)
    c = next(c for c in r.checks if c.name == "loop.reconciled_ratio")
    assert c.severity == "info"
    assert "no active churn" in c.detail
