"""`aletheia doctor` — single-command ground-truth probe.

The problem this solves: the project is developed on two machines synced via git.
Git carries *intent* (code + docs); it does NOT carry *runtime facts* (the local
app.db, config.yaml, secrets, the live gateway process). When a handoff doc says
"gateway running / observation window in progress", that sentence was true on the
machine that wrote it and is almost always false after a pull on the other machine.

`doctor` makes the machine itself the single source of truth. It probes — never
narrates from a doc — the seven things that actually determine "where is this
project right now", reusing the existing health/pidfile/git/secrets helpers rather
than reinventing them:

  1. code sync     — git HEAD vs origin (ahead/behind/dirty); the two-machine drift
  2. process       — is the gateway actually running; code-drift (running stale code)
  3. config gates  — permission_mode / live.state / exchange.mode / kill_switch
  4. credentials   — which secrets are SET vs MISSING (what blocks demo/live)
  5. market data    — freshness of the newest snapshot (is the feed actually updating)
  6. trading loop  — orders / fills / open+closed positions / newest opportunity age
  7. health         — compute_health() roll-up (the same signal the self-heal layer uses)

`build_report()` is a pure read — it changes nothing and is safe to run anywhere,
anytime, by you or an agent. `apply_fixes()` is the opt-in conservative repair
(clear orphan processes, start the gateway); it never clears the kill switch and
never touches trading state — those stay human decisions per the safety rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import platform
from pathlib import Path
import sys
from typing import Literal

Severity = Literal["ok", "info", "warn", "blocker"]


@dataclass
class Check:
    """One probed fact. `severity` colours it; `fix_hint` is the human next step."""
    name: str
    severity: Severity
    detail: str
    fix_hint: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    generated_at: str = ""

    def add(self, name: str, severity: Severity, detail: str, fix_hint: str = "") -> None:
        self.checks.append(Check(name, severity, detail, fix_hint))

    @property
    def blockers(self) -> list[Check]:
        return [c for c in self.checks if c.severity == "blocker"]

    @property
    def warns(self) -> list[Check]:
        return [c for c in self.checks if c.severity == "warn"]

    def to_dict(self) -> dict:
        """Machine-readable form for `doctor --json` and GET /_infra/doctor.

        This is the AI agent's eyes: a stable JSON contract so an agent can read
        severity/name/detail/fix_hint as fields instead of scraping printed text.
        """
        from dataclasses import asdict
        return {
            "generated_at": self.generated_at,
            "healthy": not self.blockers and not self.warns,
            "checks": [asdict(c) for c in self.checks],
            "summary": {
                "total": len(self.checks),
                "blockers": [c.name for c in self.blockers],
                "warns": [c.name for c in self.warns],
            },
        }


def build_deep_diagnostics(report: Report | None = None, *, tail_lines: int = 20) -> dict:
    """Build a read-only diagnostic bundle for `doctor --verbose/--deep`.

    This intentionally contains pointers and redacted tails, not secrets or full
    logs. It is the "where do I look next" layer above the health summary.
    """
    report = report or build_report()
    bundle = {
        "attention": [
            {
                "name": c.name,
                "severity": c.severity,
                "detail": c.detail,
                "fix_hint": c.fix_hint,
                "log_hint": _log_hint_for_check(c.name),
            }
            for c in report.checks
            if c.severity in {"warn", "blocker"}
        ],
        "system": _system_info(),
        "logs": _diagnostic_logs(tail_lines=tail_lines),
        "recent_failures": _recent_failures(),
        "recent_audit": _recent_audit_events(),
        "trading": _trading_diagnostics(),
    }
    return bundle


def _trading_diagnostics() -> dict:
    """Compact read-only trading diagnostics for doctor --deep.

    Trading quality is intentionally separate from infrastructure health. These
    diagnostics never add blockers and never trigger auto-fixes.
    """
    return {
        "alpha": _safe_alpha_diagnostic(),
        "funnel": _safe_funnel_diagnostic(),
        "sizing": _safe_sizing_diagnostic(),
        "thesis": _safe_thesis_diagnostic(),
    }


def _safe_alpha_diagnostic() -> dict:
    try:
        from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit

        report = build_alpha_attribution_audit(days=30)
        verdicts = report.get("verdicts") or {}
        return {
            "available": True,
            "main_bottleneck": report.get("main_bottleneck"),
            "verdicts": {
                key: value.get("verdict")
                for key, value in verdicts.items()
            },
        }
    except Exception as exc:
        return {"available": False, "reason": str(exc)}


def _safe_funnel_diagnostic() -> dict:
    try:
        from aletheia.analytics.trade_funnel import (
            build_trade_funnel_audit,
            compact_trade_funnel_summary,
        )

        return compact_trade_funnel_summary(build_trade_funnel_audit(hours=24))
    except Exception as exc:
        return {"available": False, "reason": str(exc)}


def _safe_sizing_diagnostic() -> dict:
    try:
        from aletheia.analytics.sizing_trace import (
            build_sizing_trace_audit,
            compact_sizing_trace_summary,
        )

        return compact_sizing_trace_summary(build_sizing_trace_audit(hours=24))
    except Exception as exc:
        return {"available": False, "reason": str(exc)}


def _safe_thesis_diagnostic() -> dict:
    try:
        from aletheia.analytics.thesis_lineage import (
            build_thesis_lineage_audit,
            compact_thesis_lineage_summary,
        )

        return compact_thesis_lineage_summary(build_thesis_lineage_audit(days=7))
    except Exception as exc:
        return {"available": False, "reason": str(exc)}


def _log_hint_for_check(name: str) -> str:
    if name.startswith("process.") or name.startswith("webui."):
        return "data/logs/errors.log or data/logs/frontdoor-errors.log"
    if name.startswith("data."):
        return "data/logs/errors.log plus recent scheduler/collector agent_runs"
    if name.startswith("loop.") or name.startswith("content.") or name.startswith("operator."):
        return "data/logs/agent_runs.jsonl and app.db agent_runs/run_steps"
    if name.startswith("trust.") or name.startswith("config."):
        return "data/logs/audit.jsonl"
    if name.startswith("network.") or name.startswith("creds."):
        return "data/logs/errors.log; secrets are intentionally never printed"
    return "data/logs/"


def _system_info() -> dict:
    """Return non-secret environment facts useful for support/debugging."""
    info = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "executable": sys.executable,
        "cwd": str(Path.cwd()),
    }
    try:
        from aletheia.config.loader import get_data_dir
        info["data_dir"] = str(get_data_dir())
    except Exception as e:
        info["data_dir_error"] = str(e)
    try:
        from aletheia.update import git_ops
        if git_ops.is_repo():
            info["git_branch"] = git_ops.current_branch()
            info["git_head"] = git_ops.current_commit()[:12]
    except Exception as e:
        info["git_error"] = str(e)
    return info


def _diagnostic_logs(*, tail_lines: int) -> dict:
    try:
        from aletheia.gateway.logs import (
            errors_log_path,
            log_inventory,
            log_path,
            tail_file_redacted,
        )

        inventory = log_inventory()
        total_bytes = sum(int(row.get("bytes") or 0) for row in inventory)
        tails = {
            "gateway.log": tail_file_redacted(log_path(), tail_lines),
            "errors.log": tail_file_redacted(errors_log_path(), tail_lines),
        }
        try:
            from aletheia.config.loader import get_data_dir
            log_dir = get_data_dir() / "logs"
            tails["frontdoor-errors.log"] = tail_file_redacted(log_dir / "frontdoor-errors.log", tail_lines)
            tails["operator-shell-errors.log"] = tail_file_redacted(log_dir / "operator-shell-errors.log", tail_lines)
        except Exception:
            pass
        return {
            "policy": {
                "text_logs_rotate_at_mb": 50,
                "text_logs_keep_generations": 100,
                "jsonl_rotate_at_mb": "config.retention.jsonl_max_mb (default 50)",
                "deletion": "doctor does not delete logs; telemetry DB pruning is separate",
            },
            "total_bytes": total_bytes,
            "files": inventory,
            "tails": tails,
        }
    except Exception as e:
        return {"error": str(e)}


def _recent_failures(limit: int = 12) -> list[dict]:
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        rows = db.execute(
            "SELECT id, agent_name, status, started_at, finished_at, error "
            "FROM agent_runs "
            "WHERE status NOT IN ('completed', 'success') OR error IS NOT NULL "
            "ORDER BY COALESCE(finished_at, started_at) DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "agent_name": row["agent_name"],
                "status": row["status"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "error": _shorten_secret_safe(row["error"]),
            }
            for row in rows
        ]
    except Exception as e:
        return [{"error": f"could not read agent_runs: {e}"}]


def _recent_audit_events(limit: int = 12) -> list[dict]:
    try:
        from aletheia.files.jsonl import read_jsonl
        events = read_jsonl("audit.jsonl", limit=limit)
        result = []
        for event in events[-limit:]:
            result.append({
                "timestamp": event.get("timestamp"),
                "actor": event.get("actor"),
                "source": event.get("source"),
                "event_type": event.get("event_type"),
                "entity_type": event.get("entity_type"),
                "entity_id": event.get("entity_id"),
                "message": _shorten_secret_safe(event.get("message")),
            })
        return result
    except Exception as e:
        return [{"error": f"could not read audit.jsonl: {e}"}]


def _shorten_secret_safe(value: object, max_len: int = 240) -> str:
    if value is None:
        return ""
    text = str(value)
    try:
        from aletheia.gateway.logs import redact_log_text
        text = redact_log_text(text)
    except Exception:
        pass
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def _age_minutes(iso_ts: str | None) -> float | None:
    """Minutes since an ISO-8601 timestamp (tz-aware or naive-UTC). None if unparseable."""
    if not iso_ts:
        return None
    try:
        ts = datetime.fromisoformat(iso_ts)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds() / 60.0
    except (ValueError, TypeError):
        return None


def _parse_iso_datetime(iso_ts: str | None) -> datetime | None:
    if not iso_ts:
        return None
    try:
        text = iso_ts.replace("Z", "+00:00")
        ts = datetime.fromisoformat(text)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    except (ValueError, TypeError):
        return None


def _fmt_age(minutes: float | None) -> str:
    if minutes is None:
        return "unknown"
    if minutes < 60:
        return f"{minutes:.0f}min ago"
    if minutes < 1440:
        return f"{minutes / 60:.1f}h ago"
    return f"{minutes / 1440:.1f}d ago"


# --- individual probes (each best-effort; never raises into build_report) ---

def _probe_code_sync(report: Report) -> None:
    from aletheia.update import git_ops
    if not git_ops.is_repo():
        report.add("code.sync", "info", "not a git repo (or git unavailable)")
        return
    branch = git_ops.current_branch() or "master"
    head = git_ops.current_commit()
    dirty = git_ops.is_worktree_dirty()
    # ahead/behind are vs the LAST fetched origin; we do not fetch here (read-only,
    # offline-safe). doctor --fix may fetch. Note that fact in the detail.
    ahead = git_ops.ahead_count(branch)
    behind = git_ops.behind_count(branch)

    bits = [f"branch={branch}", f"HEAD={head[:7] or '?'}"]
    if ahead > 0:
        bits.append(f"{ahead} unpushed")
    if behind > 0:
        bits.append(f"{behind} behind origin")
    if dirty:
        bits.append("dirty worktree")
    detail = ", ".join(bits) + "  (vs last fetch; run `git fetch` for live)"

    if behind > 0:
        report.add("code.sync", "warn", detail,
                   "this machine is behind — `git pull --ff-only` before working")
    elif dirty:
        report.add("code.sync", "info", detail,
                   "uncommitted changes present — commit or stash before switching machines")
    else:
        report.add("code.sync", "ok", detail)


def _probe_process(report: Report) -> None:
    from aletheia.gateway.pidfile import is_running, read_pid, port_in_use
    from aletheia.config.loader import load_config
    cfg = load_config()
    running = is_running()
    pid = read_pid()
    port = cfg.app.dashboard_port
    host = cfg.app.bind_host

    if running:
        report.add("process.gateway", "ok", f"running (PID {pid}) on {host}:{port}")
        _probe_frontdoor_process(report, host, port)
    elif port_in_use(port, host):
        report.add("process.gateway", "warn",
                   f"port {port} held but no tracked PID (orphan / ghost socket)",
                   "`aletheia doctor --fix` (clears orphans) or `aletheia gateway stop`")
    else:
        report.add("process.gateway", "info", "stopped",
                   "`aletheia gateway start` (or `aletheia doctor --fix`)")

    # Only meaningful while running: is the live process on stale code?
    if running:
        try:
            from aletheia.agent_runtime.build_info import code_drift_issue
            drift = code_drift_issue()
            if drift:
                report.add("process.code_drift", "warn", drift,
                           "gateway is running stale code — `aletheia gateway restart`")
        except Exception:
            pass

    # Autostart watchdog: logon-trigger + 2-min repetition that resurrects a dead
    # supervisor. status() does a live `schtasks /Query`, so this is real state,
    # not a persisted flag. enabled=None means the query itself failed (unknown).
    try:
        from aletheia.gateway.autostart import status as autostart_status, WATCHDOG_INTERVAL
        a = autostart_status()
        interval_desc = WATCHDOG_INTERVAL.replace("PT", "").replace("M", "-min")
        if not a.get("supported"):
            report.add("process.autostart", "info", f"not supported ({a.get('reason')})")
        elif a.get("enabled") is True:
            if a.get("primed") is False:
                last_run = a.get("last_run_time") or "unknown"
                last_result = a.get("last_result") or "unknown"
                report.add("process.autostart", "warn",
                           f"watchdog registered but not run successfully yet "
                           f"(last_run={last_run}, last_result={last_result})",
                           "`aletheia gateway enable-autostart` to re-prime, or `schtasks /Run /TN \"Aletheia Trading OS Gateway\"`")
            else:
                detail = f"watchdog registered (logon + time-triggered {interval_desc} repetition)"
                if a.get("last_run_time"):
                    detail += f", last_run={a.get('last_run_time')}"
                report.add("process.autostart", "ok", detail)
        elif a.get("enabled") is False:
            report.add("process.autostart", "warn",
                       "watchdog NOT registered — a dead supervisor won't be resurrected",
                       "`aletheia gateway enable-autostart` (or start the gateway — it auto-registers)")
        else:
            report.add("process.autostart", "warn",
                       f"watchdog state unknown ({a.get('reason')})",
                       "run `aletheia gateway enable-autostart` or check Task Scheduler access")
    except Exception:
        pass


def _probe_frontdoor_process(report: Report, host: str, port: int) -> None:
    """Probe the stable front-door layer when present.

    This is best-effort and local-only. Older/direct gateway builds will answer
    `/_infra/status` without `role=frontdoor`, in which case we stay quiet.
    """
    try:
        import json
        import urllib.request

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://{host}:{port}/_infra/status", timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return

    if payload.get("role") != "frontdoor":
        return

    static_state = "static ok" if payload.get("static_index") else "static missing"
    backend = payload.get("backend") or {}
    report.add(
        "process.frontdoor",
        "ok" if payload.get("static_index") else "warn",
        f"front door PID {payload.get('pid')} on {host}:{port}, {static_state}",
        "Run: cd frontend && npm run build" if not payload.get("static_index") else "",
    )
    if backend.get("ready"):
        report.add("process.backend", "ok", f"backend ready behind front door ({payload.get('backend_base_url')})")
    else:
        detail = backend.get("error") or f"status_code={backend.get('status_code')}"
        report.add(
            "process.backend",
            "warn",
            f"backend not ready behind front door ({payload.get('backend_base_url')}): {detail}",
            "wait for supervisor restart, or run `aletheia gateway restart --no-browser`",
        )


_GATEWAY_RESTART_EVENT_TYPES = {
    "gateway_restart",
    "gateway_frontdoor_restart",
    "gateway_inner_port_lost",
    "gateway_spawn_failed",
    "gateway_supervisor_gave_up",
    "self_heal_restart",
}


def _restart_event_bucket(event: dict) -> str:
    event_type = str(event.get("event_type") or "unknown")
    reason = ""
    after = event.get("after")
    if isinstance(after, dict):
        reason = str(after.get("reason") or "")
    if not reason:
        msg = str(event.get("message") or "")
        if "(" in msg and ")" in msg:
            reason = msg.split("(", 1)[1].split(")", 1)[0]
        elif ":" in msg and event_type == "self_heal_restart":
            reason = msg.rsplit(":", 1)[-1].strip()
    return f"{event_type}:{reason}" if reason else event_type


def _probe_gateway_restart_history(report: Report) -> None:
    """Summarize recent gateway restart/liveness events from audit.jsonl.

    This is intentionally read-only. It gives the operator the answer to
    "why did the backend disappear?" without manual log archaeology.
    """
    try:
        from aletheia.files.jsonl import read_jsonl
        events = read_jsonl("audit.jsonl", limit=500)
    except Exception as e:
        report.add("process.restart_history", "info", f"probe error: {e}")
        return

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    counts: dict[str, int] = {}
    latest_ts: datetime | None = None
    latest_bucket = ""
    critical_seen = False
    for event in events:
        event_type = str(event.get("event_type") or "")
        if event_type not in _GATEWAY_RESTART_EVENT_TYPES:
            continue
        ts = _parse_iso_datetime(event.get("timestamp"))
        if ts is not None and ts < cutoff:
            continue
        bucket = _restart_event_bucket(event)
        counts[bucket] = counts.get(bucket, 0) + 1
        if ts is not None and (latest_ts is None or ts > latest_ts):
            latest_ts = ts
            latest_bucket = bucket
        if event_type in {"gateway_spawn_failed", "gateway_supervisor_gave_up"}:
            critical_seen = True

    total = sum(counts.values())
    if total == 0:
        report.add("process.restart_history", "info", "no gateway restart/liveness events in last 24h")
        return

    top = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:4]
    top_text = ", ".join(f"{name}={n}" for name, n in top)
    latest = f", latest={latest_bucket} {_fmt_age(_age_minutes(latest_ts.isoformat()) if latest_ts else None)}"
    severity: Severity = "warn" if critical_seen or total >= 10 else "info"
    fix_hint = ""
    if total >= 10:
        fix_hint = "high restart volume — check git hook churn, frontdoor port loss, and supervisor errors"
    if critical_seen:
        fix_hint = "spawn/give-up event seen — inspect data/logs/errors.log and frontdoor-errors.log"
    report.add("process.restart_history", severity, f"24h restart/liveness events={total}; {top_text}{latest}", fix_hint)


def _probe_config_gates(report: Report) -> None:
    from aletheia.config.loader import load_config
    cfg = load_config()
    report.add("config.permission", "info", f"permission_mode={cfg.agent.permission_mode}")
    report.add("config.live", "info",
               f"live.state={cfg.live.state}, exchange.mode={cfg.exchange.mode}, "
               f"paper_execution_mode={getattr(cfg.exchange, 'paper_execution_mode', '?')}")

    demo_cap = getattr(cfg.risk, "max_demo_position_notional_usdt", None)
    paper_mode = getattr(cfg.exchange, "paper_execution_mode", "")
    if (
        cfg.exchange.mode == "demo"
        and paper_mode in {"paper_internal", "bitget_demo", "okx_demo", "binance_testnet"}
        and demo_cap is not None
        and float(demo_cap) <= 0
    ):
        report.add(
            "config.demo_notional_cap",
            "blocker",
            f"max_demo_position_notional_usdt={float(demo_cap):g} — every paper/demo proposal is rejected",
            "set a positive paper/demo notional cap in Settings → Risk Limits after human review",
        )

    if cfg.runtime.kill_switch_active:
        report.add("config.kill_switch", "blocker",
                   "kill_switch ACTIVE — every trade proposal is rejected",
                   "if tripped by a past daily-loss and it is now a NEW UTC day, release it "
                   "from the dashboard Risk panel (or `aletheia risk resume`) — human decision, "
                   "--fix will not auto-clear")
    else:
        report.add("config.kill_switch", "ok", "kill_switch inactive")

    if cfg.runtime.paused:
        report.add("config.paused", "warn", "runtime.paused=True — autonomous jobs suspended",
                   "`aletheia gateway resume`")


def _probe_credentials(report: Report) -> None:
    from aletheia.config.secrets import get_secret
    from aletheia.config.loader import load_config
    cfg = load_config()

    demo_keys = ["bitget.demo.api_key", "bitget.demo.api_secret", "bitget.demo.passphrase"]
    demo_set = [k for k in demo_keys if get_secret(k)]
    mode = cfg.exchange.mode
    pem = getattr(cfg.exchange, "paper_execution_mode", "")

    if len(demo_set) == len(demo_keys):
        report.add("creds.bitget_demo", "ok", "all 3 demo credentials SET")
    elif demo_set:
        report.add("creds.bitget_demo", "warn",
                   f"partial: {len(demo_set)}/3 demo credentials SET",
                   "set the missing ones in Settings → Credentials (or `aletheia secrets set bitget.demo.*`)")
    else:
        # Missing demo creds only BLOCKS if the system is configured to place demo orders.
        sev: Severity = "blocker" if (mode == "demo" and pem == "bitget_demo") else "info"
        report.add("creds.bitget_demo", sev,
                   "no bitget demo credentials — bitget_demo orders cannot reach the exchange "
                   "(paper_internal local fill still works)",
                   "Settings → Credentials (or `aletheia secrets set bitget.demo.api_key` + api_secret, passphrase)")

    provider = getattr(cfg.models, "default_provider", "")
    pcfg = cfg.models.providers.get(provider) if provider else None
    if pcfg is None:
        report.add("creds.llm", "warn",
                   f"default LLM provider not found: {provider or 'not configured'}",
                   "Settings -> Model Provider, or `aletheia model set`")
        return
    if not pcfg.enabled:
        report.add("creds.llm", "warn",
                   f"default LLM provider disabled (default={provider})",
                   "Settings -> Model Provider, or `aletheia model set`")
        return

    ptype = pcfg.type or provider
    if ptype in {"codex_cli", "claude_cli"}:
        report.add("creds.llm", "ok", f"LLM provider configured (default={provider}, type={ptype})")
        return

    secret_ref = pcfg.api_key_secret_ref
    if secret_ref:
        if get_secret(secret_ref):
            report.add("creds.llm", "ok",
                       f"LLM provider configured (default={provider}, secret_ref={secret_ref})")
        else:
            report.add("creds.llm", "warn",
                       f"LLM provider secret missing (default={provider}, secret_ref={secret_ref})",
                       "Settings -> Model Provider, or set the configured model secret")
        return

    endpoint = pcfg.workspace_url or pcfg.base_url
    if _is_local_model_endpoint(endpoint):
        report.add("creds.llm", "ok",
                   f"LLM provider configured without API key (default={provider}, local endpoint)")
    else:
        report.add("creds.llm", "warn",
                   f"LLM provider has no api_key_secret_ref (default={provider}, type={ptype})",
                   "Settings -> Model Provider, or configure api_key_secret_ref for the provider")


def _is_local_model_endpoint(endpoint: str | None) -> bool:
    if not endpoint:
        return False
    lowered = endpoint.lower()
    return (
        "localhost" in lowered
        or "127.0.0.1" in lowered
        or lowered.startswith("http://0.0.0.0")
    )


def _probe_market_data(report: Report) -> None:
    try:
        from aletheia.storage.repositories import list_market_snapshots
        rows = list_market_snapshots(limit=1)
    except Exception as e:
        report.add("data.market", "info", f"could not read snapshots: {e}")
        return
    if not rows:
        report.add("data.market", "info", "no market snapshots yet (feed never started)")
        return
    age = _age_minutes(rows[0].get("snapshot_at"))
    detail = f"newest snapshot {_fmt_age(age)}"
    if age is not None and age > 30:
        report.add("data.market", "warn", detail + " — feed appears stale",
                   "check gateway is running and scheduler not paused")
    else:
        report.add("data.market", "ok", detail)


def _probe_universe_market_coverage(report: Report) -> None:
    """Verify each enabled instrument market reaches the recent feature layer."""
    try:
        from aletheia.config.loader import load_config
        from aletheia.storage.sqlite import get_db

        cfg = load_config()
        enabled = cfg.exchange.enabled_markets or {}
        expected: list[str] = []
        if enabled.get("spot"):
            expected.append("spot")
        if enabled.get("usdt_futures"):
            expected.append("perp")
        if not expected:
            report.add(
                "data.universe_market_coverage", "info",
                "no spot/perp markets enabled for Universe feature collection",
            )
            return

        db = get_db()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        rows = db.execute(
            "SELECT ur.market_type, COUNT(*) AS n "
            "FROM universe_feature_snapshots ufs "
            "JOIN universe_registry ur ON ur.id = ufs.asset_id "
            "WHERE datetime(ufs.ts) >= datetime(?) "
            "GROUP BY ur.market_type",
            (cutoff,),
        ).fetchall()
        feature_counts = {str(row["market_type"]): int(row["n"]) for row in rows}

        active_rows = db.execute(
            "SELECT um.market_type, COUNT(*) AS n FROM universe_members um "
            "WHERE um.snapshot_id = ("
            "  SELECT id FROM universe_snapshots WHERE status = 'active' "
            "  ORDER BY datetime(created_at) DESC LIMIT 1"
            ") GROUP BY um.market_type"
        ).fetchall()
        member_counts = {str(row["market_type"]): int(row["n"]) for row in active_rows}
    except Exception as e:
        report.add("data.universe_market_coverage", "info", f"probe error: {e}")
        return

    detail = (
        "recent features "
        f"spot={feature_counts.get('spot', 0)} perp={feature_counts.get('perp', 0)}; "
        "active members "
        f"spot={member_counts.get('spot', 0)} perp={member_counts.get('perp', 0)}"
    )
    if sum(feature_counts.values()) == 0:
        report.add(
            "data.universe_market_coverage", "info", detail,
            "wait for Universe refresh before judging per-market coverage",
        )
        return

    missing = [market for market in expected if feature_counts.get(market, 0) == 0]
    if missing:
        report.add(
            "data.universe_market_coverage", "warn", detail,
            "enabled market type(s) missing from recent features: " + ", ".join(missing),
        )
        return
    report.add("data.universe_market_coverage", "ok", detail)


def _probe_orderbook(report: Report) -> None:
    """Expose whether L2 orderbook snapshots are actually reaching storage."""
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        row = db.execute(
            "SELECT symbol, observed_at, spread_bps, total_depth_10bps_usd "
            "FROM orderbook_snapshots "
            "ORDER BY observed_at DESC LIMIT 1"
        ).fetchone()
    except Exception as e:
        report.add("data.orderbook", "info", f"could not read orderbook snapshots: {e}")
        return

    if not row:
        report.add(
            "data.orderbook",
            "warn",
            "no orderbook rows yet",
            "check microstructure collector logs and exchange orderbook support",
        )
        return

    age = _age_minutes(row["observed_at"])
    sev = "warn" if age is not None and age > 15 else "ok"
    spread = row["spread_bps"]
    depth = row["total_depth_10bps_usd"]
    detail = (
        f"newest orderbook {row['symbol']} ({_fmt_age(age)}), "
        f"spread={spread:.2f}bps, depth10=${depth:,.0f}"
    )
    report.add(
        "data.orderbook",
        sev,
        detail,
        "check data_collectors microstructure job if stale > 15m"
        if sev == "warn" else None,
    )


def _probe_perp_structure(report: Report) -> None:
    """Expose whether observation-only perp structure rows are fresh."""
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        row = db.execute(
            "SELECT symbol, source, venue, as_of_time, observed_at, data_quality "
            "FROM perp_structure_snapshots "
            "ORDER BY as_of_time DESC, observed_at DESC LIMIT 1"
        ).fetchone()
    except Exception as e:
        if "no such table: perp_structure_snapshots" in str(e):
            report.add(
                "data.perp_structure",
                "info",
                "no perp structure rows yet",
                "no action unless this observation-only feed is expected to be populated",
            )
            return
        report.add("data.perp_structure", "info", f"could not read perp structure: {e}")
        return

    if not row:
        report.add(
            "data.perp_structure",
            "info",
            "no perp structure rows yet",
            "no action unless this observation-only feed is expected to be populated",
        )
        return

    age = _age_minutes(row["as_of_time"] or row["observed_at"])
    sev = "warn" if age is None or age > 60 else "ok"
    detail = (
        f"newest perp structure {row['symbol']} "
        f"{row['source']}/{row['venue']} quality={row['data_quality']} ({_fmt_age(age)})"
    )
    report.add(
        "data.perp_structure",
        sev,
        detail,
        "perp structure stale > 60m; check the offline importer/feed job"
        if sev == "warn" else None,
    )


def _probe_macro_signals(report: Report) -> None:
    """Round 2 macro signal freshness probes — LSR / F&G / CoinGecko top.

    Per plan §9.4: doctor must expose the age of the last LSR fill, the latest
    F&G value + age, and the CoinGecko top table row count + newest observed_at.
    All three signals enter the LLM enriched_context, so a stale signal silently
    poisons the prompt — surfacing the age is the operator's only signal.
    """
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()

        # --- LSR: most recent row where long_short_ratio IS NOT NULL ---
        try:
            row = db.execute(
                "SELECT symbol, long_short_ratio, observed_at "
                "FROM open_interest_snapshots "
                "WHERE long_short_ratio IS NOT NULL "
                "ORDER BY observed_at DESC LIMIT 1"
            ).fetchone()
            if row:
                age = _age_minutes(row["observed_at"])
                detail = (
                    f"newest LSR {row['symbol']}={row['long_short_ratio']:.2f} "
                    f"({_fmt_age(age)})"
                )
                sev = "warn" if age is not None and age > 30 else "ok"
                report.add("data.lsr", sev, detail,
                           "check Binance fapi reachability if stale > 30m"
                           if sev == "warn" else None)
            else:
                report.add("data.lsr", "info",
                           "no LSR rows yet — enable in Settings → Market Data Sources")
        except Exception as e:
            report.add("data.lsr", "info", f"could not read LSR: {e}")

        # --- F&G: latest macro snapshot's fng_value + age ---
        try:
            row = db.execute(
                "SELECT fng_value, fng_classification, observed_at "
                "FROM macro_snapshots "
                "WHERE fng_value IS NOT NULL "
                "ORDER BY observed_at DESC LIMIT 1"
            ).fetchone()
            if row:
                age = _age_minutes(row["observed_at"])
                cls = f" ({row['fng_classification']})" if row["fng_classification"] else ""
                # F&G updates ~daily, so 6h staleness is normal; warn at 30h.
                sev = "warn" if age is not None and age > 30 * 60 else "ok"
                report.add("data.fear_greed", sev,
                           f"F&G {row['fng_value']}{cls} ({_fmt_age(age)})",
                           "Alt.me may be down if stale > 30h"
                           if sev == "warn" else None)
            else:
                report.add("data.fear_greed", "info",
                           "no F&G rows yet — enable in Settings → Macro & Sentiment Signals")
        except Exception as e:
            report.add("data.fear_greed", "info", f"could not read F&G: {e}")

        # --- CoinGecko top: count + newest observed_at ---
        try:
            cnt_row = db.execute(
                "SELECT COUNT(*) AS n, MAX(observed_at) AS newest "
                "FROM coingecko_top_snapshots"
            ).fetchone()
            n = cnt_row["n"] if cnt_row else 0
            newest = cnt_row["newest"] if cnt_row else None
            if n == 0:
                report.add("data.coingecko_top", "info",
                           "no CoinGecko top rows — enable in Settings → Macro & Sentiment Signals")
            else:
                age = _age_minutes(newest)
                # Phase B is 10min cadence; >30m stale = collector probably failing.
                sev = "warn" if age is not None and age > 30 else "ok"
                report.add("data.coingecko_top", sev,
                           f"{n:,} rows total; newest batch {_fmt_age(age)}",
                           "check CoinGecko reachability (429? rate limit?)"
                           if sev == "warn" else None)
        except Exception as e:
            report.add("data.coingecko_top", "info", f"could not read CoinGecko top: {e}")

        # --- Hyperliquid: newest cross-venue OI row (exchange='hyperliquid') ---
        try:
            row = db.execute(
                "SELECT symbol, observed_at FROM open_interest_snapshots "
                "WHERE exchange = 'hyperliquid' "
                "ORDER BY observed_at DESC LIMIT 1"
            ).fetchone()
            if row:
                age = _age_minutes(row["observed_at"])
                sev = "warn" if age is not None and age > 30 else "ok"
                report.add("data.hyperliquid", sev,
                           f"newest HL cross-venue OI {row['symbol']} ({_fmt_age(age)})",
                           "check api.hyperliquid.xyz reachability if stale > 30m"
                           if sev == "warn" else None)
            else:
                report.add("data.hyperliquid", "info",
                           "no Hyperliquid rows yet — enable in Settings → Macro & Sentiment Signals")
        except Exception as e:
            report.add("data.hyperliquid", "info", f"could not read Hyperliquid: {e}")

        # --- DefiLlama on-chain: row count + newest observed_at ---
        try:
            cnt_row = db.execute(
                "SELECT COUNT(*) AS n, MAX(observed_at) AS newest FROM onchain_snapshots"
            ).fetchone()
            n = cnt_row["n"] if cnt_row else 0
            newest = cnt_row["newest"] if cnt_row else None
            if n == 0:
                report.add("data.onchain", "info",
                           "no on-chain rows yet — enable DefiLlama in Settings → Macro & Sentiment Signals")
            else:
                age = _age_minutes(newest)
                # Phase B ~10min cadence; >45m stale = collector probably failing.
                sev = "warn" if age is not None and age > 45 else "ok"
                report.add("data.onchain", sev,
                           f"{n:,} on-chain rows; newest {_fmt_age(age)}",
                           "check DefiLlama (api.llama.fi) reachability if stale > 45m"
                           if sev == "warn" else None)
        except Exception as e:
            report.add("data.onchain", "info", f"could not read on-chain: {e}")

    except Exception as e:
        report.add("data.macro_signals", "info", f"macro signals probe failed: {e}")


def _probe_coinalyze(report: Report) -> None:
    """Expose whether the optional Coinalyze OI/liquidation feed is actually live."""
    try:
        from aletheia.config.loader import load_config
        cfg = load_config()
        enabled = bool(getattr(cfg.market_data, "enable_coinalyze", False))
        if not enabled:
            report.add("data.coinalyze", "info", "Coinalyze disabled in settings")
            return

        from aletheia.storage.sqlite import get_db
        db = get_db()
        oi_row = db.execute(
            "SELECT symbol, observed_at FROM open_interest_snapshots "
            "WHERE exchange = 'coinalyze' "
            "ORDER BY observed_at DESC LIMIT 1"
        ).fetchone()
        liq_row = db.execute(
            "SELECT symbol, observed_at FROM liquidation_events "
            "WHERE exchange = 'coinalyze' "
            "ORDER BY observed_at DESC LIMIT 1"
        ).fetchone()

        if oi_row:
            age = _age_minutes(oi_row["observed_at"])
            sev = "warn" if age is not None and age > 60 else "ok"
            report.add(
                "data.coinalyze",
                sev,
                f"newest Coinalyze OI {oi_row['symbol']} ({_fmt_age(age)})",
                "check Coinalyze key/quota or scheduler if stale > 60m"
                if sev == "warn" else None,
            )
        else:
            report.add(
                "data.coinalyze",
                "warn",
                "Coinalyze enabled but no OI rows yet",
                "configure coinalyze_api_key or COINALYZE_API_KEY, then wait for the data_collectors tick",
            )

        if liq_row:
            age = _age_minutes(liq_row["observed_at"])
            report.add(
                "data.liquidations",
                "info",
                f"newest Coinalyze liquidation {liq_row['symbol']} ({_fmt_age(age)})",
            )
        else:
            report.add(
                "data.liquidations",
                "warn" if not oi_row else "info",
                "no Coinalyze liquidation rows yet",
                "same key path as Coinalyze OI; no rows is expected until a real liquidation occurs"
                if oi_row else "Coinalyze has not produced any OI/liquidation rows yet",
            )
    except Exception as e:
        report.add("data.coinalyze", "info", f"could not read Coinalyze state: {e}")


def _probe_prediction_markets(report: Report) -> None:
    """Expose whether prediction-market data is enabled and fresh enough to use."""
    try:
        from aletheia.config.loader import load_config
        from aletheia.storage.sqlite import get_db

        cfg = load_config()
        enabled = bool(getattr(cfg.market_data, "prediction_enabled", False))
        db = get_db()
        market_row = db.execute(
            "SELECT question, updated_at FROM prediction_markets "
            "ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        claim_row = db.execute(
            "SELECT claim_text, created_at FROM prediction_market_claims "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    except Exception as e:
        report.add("data.prediction_markets", "info", f"could not read prediction market state: {e}")
        return

    market_age = _age_minutes(market_row["updated_at"]) if market_row else None
    claim_age = _age_minutes(claim_row["created_at"]) if claim_row else None
    newest_age = min(age for age in (market_age, claim_age) if age is not None) if (
        market_age is not None or claim_age is not None
    ) else None
    newest_detail = _fmt_age(newest_age)

    if not enabled:
        report.add(
            "data.prediction_markets",
            "info",
            f"prediction markets disabled in settings; newest stored signal {newest_detail}",
        )
        return

    if not market_row and not claim_row:
        report.add(
            "data.prediction_markets",
            "warn",
            "prediction markets enabled but no rows yet",
            "wait for prediction_observer or trigger a manual prediction refresh",
        )
        return

    sev = "warn" if newest_age is None or newest_age > 90 else "ok"
    detail = f"prediction markets enabled; newest stored signal {newest_detail}"
    if market_row:
        detail += f"; latest market={market_row['question'][:80]}"
    if claim_row:
        detail += f"; latest claim={claim_row['claim_text'][:80]}"
    report.add(
        "data.prediction_markets",
        sev,
        detail,
        "prediction_observer runs every 30m; stale data is ignored by agent context"
        if sev == "warn" else None,
    )


def _probe_data_quality(report: Report) -> None:
    """Check data provenance: are prices coming from fallback sources?"""
    try:
        from aletheia.storage.repositories import list_market_snapshots
        rows = list_market_snapshots(limit=50)
    except Exception:
        return
    if not rows:
        return

    recent = [r for r in rows if _age_minutes(r.get("snapshot_at") or "") is not None
              and _age_minutes(r.get("snapshot_at") or "") < 30]  # type: ignore[operator]
    if not recent:
        return

    fallback_providers = {"coingecko", "binance_public"}
    fallback_count = sum(1 for r in recent if (r.get("provider") or "").lower() in fallback_providers)
    total = len(recent)
    ratio = fallback_count / total if total else 0

    if ratio > 0.2:
        report.add(
            "data.quality", "warn",
            f"{fallback_count}/{total} recent snapshots from fallback sources "
            f"({ratio:.0%}) — CCXT primary may be degraded",
            "check network/exchange API status; consider disabling cross_source_check temporarily",
        )
    else:
        report.add("data.quality", "ok",
                   f"data sources healthy ({fallback_count}/{total} fallback)")

    # Price cache freshness
    try:
        from aletheia.data.price_feed import cache_stats
        stats = cache_stats()
        if stats.get("oldest_sec") and stats["oldest_sec"] > 80:
            report.add("data.cache_freshness", "info",
                       f"price cache oldest entry {stats['oldest_sec']:.0f}s (threshold 90s)")
    except Exception:
        pass


def _probe_trading_loop(report: Report) -> None:
    """The 'is the closed loop actually turning' probe — counts, not narrative."""
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()

        def _count(table: str) -> int:
            try:
                return int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            except Exception:
                return -1

        orders = _count("orders")
        fills = _count("fills")
        opps = _count("opportunities")
        ki = _count("knowledge_items")
        try:
            open_pos = int(db.execute("SELECT COUNT(*) FROM positions WHERE status='open'").fetchone()[0])
            closed_pos = int(db.execute("SELECT COUNT(*) FROM positions WHERE status='closed'").fetchone()[0])
        except Exception:
            open_pos = closed_pos = -1

        # Age of newest opportunity — the liveness signal for the observe stage.
        newest_opp = None
        try:
            row = db.execute("SELECT created_at FROM opportunities ORDER BY created_at DESC LIMIT 1").fetchone()
            newest_opp = row[0] if row else None
        except Exception:
            pass
    except Exception as e:
        report.add("loop.activity", "info", f"could not read trading tables: {e}")
        return

    opp_age = _age_minutes(newest_opp)
    report.add("loop.activity", "info",
               f"orders={orders} fills={fills} positions(open={open_pos},closed={closed_pos}) "
               f"opportunities={opps} knowledge={ki}")
    if opp_age is None:
        report.add("loop.newest_opportunity", "info", "no opportunities yet")
    elif opp_age > 30:
        report.add("loop.newest_opportunity", "warn",
                   f"newest opportunity {_fmt_age(opp_age)} — observe stage not producing",
                   "observer should fire every ~15min when gateway runs unpaused")
    else:
        report.add("loop.newest_opportunity", "ok", f"newest opportunity {_fmt_age(opp_age)}")


def _probe_profit_loop(report: Report) -> None:
    """Proposal-flow liveness for the trading pipeline.

    The loop is: opportunities -> (paper_trader ranks) -> signal_pending pi_decisions
    -> System Operator LLM decides -> trade_proposals -> risk -> orders -> positions.

    This probe does not measure profitability. A silent stall here is still a
    dangerous failure class: the observe stage
    keeps producing opportunities (so loop.newest_opportunity stays green) while
    NO new proposals are created for hours — the system looks alive but earns and
    learns nothing. This probe catches that decoupling directly by comparing the
    age of the newest opportunity against the age of the newest proposal, and by
    inspecting the recent PI-decision action mix for an all-skip pattern.

    Root precedent: 2026-06-23 the System Operator saw a phantom $167 balance (wrong
    broker cache) and skipped every signal for 20h. This probe exists so that
    class of stall surfaces in `aletheia doctor` instead of reaching the user.
    """
    try:
        from aletheia.config.loader import load_config
        from aletheia.storage.sqlite import get_db
        cfg = load_config()
        db = get_db()

        def _newest(table: str) -> str | None:
            try:
                row = db.execute(
                    f"SELECT created_at FROM {table} ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
                return row[0] if row else None
            except Exception:
                return None

        opp_age = _age_minutes(_newest("opportunities"))
        prop_age = _age_minutes(_newest("trade_proposals"))
    except Exception as e:
        report.add("loop.profit", "info", f"could not read loop tables: {e}")
        return

    # No opportunities at all → upstream handles it (loop.newest_opportunity).
    if opp_age is None:
        report.add("loop.profit", "info", "no opportunities yet — loop not started")
        return

    # The stall signature: opportunities are flowing (< 60min) but proposals are
    # stale (> 6h) or absent. This is the decoupling that looks alive but isn't.
    PROPOSAL_STALL_MIN = 360  # 6h — well beyond the 5min paper_trader cadence
    # Day-0 grace: if no proposals have EVER been created AND the oldest
    # opportunity is itself < 6h old, the pipeline simply hasn't had time to
    # complete its first cycle — report info, not warn (avoids false STALLED).
    if prop_age is None and opp_age < 60:
        oldest_opp = _age_minutes(db.execute(
            "SELECT created_at FROM opportunities ORDER BY created_at ASC LIMIT 1"
        ).fetchone()[0]) if db.execute("SELECT 1 FROM opportunities LIMIT 1").fetchone() else 0
        if oldest_opp is not None and oldest_opp < PROPOSAL_STALL_MIN:
            report.add("loop.profit", "info",
                       f"pipeline warming up — opportunities flowing ({_fmt_age(opp_age)}) "
                       f"but no proposals yet (first opp {_fmt_age(oldest_opp)} ago)")
            return
    if opp_age < 60 and (prop_age is None or prop_age > PROPOSAL_STALL_MIN):
        # Inspect recent PI-decision action mix to attribute the stall.
        attribution = ""
        self_protecting = False
        skip_count = 0
        try:
            rows = db.execute(
                "SELECT action, COUNT(*) c FROM pi_decisions "
                "WHERE created_at >= datetime('now','-12 hours') GROUP BY action"
            ).fetchall()
            mix = {r[0]: r[1] for r in rows}
            skips = mix.get("skip", 0)
            trades = mix.get("trade", 0) + mix.get("scale_in", 0) + mix.get("flip", 0)
            skip_count = skips
            if skips > 0:
                # Check if System Operator is deliberately self-protecting (distress pattern).
                # Don't require trades==0: stale trades (>PROPOSAL_STALL_MIN ago) count as
                # pipeline stalled even if they appear in the 12h window.
                reason_rows = db.execute(
                    "SELECT reasoning FROM pi_decisions WHERE action='skip' "
                    "AND created_at >= datetime('now','-12 hours') LIMIT 10"
                ).fetchall()
                distress_kws = ("structural distress", "acute distress", "systematic distress")
                for r in reason_rows:
                    if any(k in (r[0] or "").lower() for k in distress_kws):
                        self_protecting = True
                        break
                # bypass_distress_gate=True: stats suppressed — LLM skip is a
                # deliberate real-market decision, not a stale-WR hallucination.
                # Treat as self-protecting when bypass is active.
                if not self_protecting and cfg.agent.bypass_distress_gate:
                    for r in reason_rows:
                        if "suppressed" in (r[0] or "").lower():
                            self_protecting = True
                            break
                if not self_protecting:
                    attribution = (f" — System Operator returned skip×{skips}/trade×{trades} in 12h "
                                   f"(check trading_context balance + regime inputs)")
        except Exception:
            pass
        prop_desc = "none ever" if prop_age is None else _fmt_age(prop_age)
        if self_protecting:
            bypass_note = " (bypass_distress_gate active — deliberate skip)" if cfg.agent.bypass_distress_gate else ""
            report.add("loop.profit", "info",
                       f"System Operator self-protecting{bypass_note}: opportunities fresh ({_fmt_age(opp_age)}), "
                       f"skip×{skip_count}/trade×0 in 12h (distress pattern); "
                       f"newest proposal {prop_desc}",
                       "deliberate protective behavior — not a pipeline failure; "
                       "System Operator sees adverse trading_context (low WR / recent SL streak)")
        else:
            report.add("loop.profit", "warn",
                       f"PROPOSAL FLOW STALLED: opportunities fresh ({_fmt_age(opp_age)}) but "
                       f"newest proposal {prop_desc}{attribution}",
                       "opportunities flow but produce no proposals — inspect "
                       "paper_trader_ranker scoring + System Operator decision inputs "
                       "(see docs/issues/MINIMUM_PROFIT_LOOP_AUDIT.md)")
    elif prop_age is not None and prop_age < PROPOSAL_STALL_MIN:
        report.add("loop.profit", "ok",
                   f"proposal flow healthy — newest proposal {_fmt_age(prop_age)}; "
                   "profitability not assessed")
    else:
        report.add("loop.profit", "info",
                   f"opportunities {_fmt_age(opp_age)}, proposals {_fmt_age(prop_age) if prop_age else 'none'}")


def _probe_round1(report: Report) -> None:
    """Round 1 experiment progress vs Go/Kill thresholds.

    Mirrors the SQL in /api/analytics/round1-progress so both surfaces share
    one source of truth. Read-only; reports gap toward the 60-trade G6 line.
    """
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        row = db.execute(
            "SELECT COUNT(*) closed, "
            "  COALESCE(ROUND(SUM(realized_pnl),3),0) realized, "
            "  COALESCE(SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END),0) wins, "
            "  COALESCE(SUM(CASE WHEN realized_pnl>0 THEN realized_pnl ELSE 0 END),0) gp, "
            "  COALESCE(-SUM(CASE WHEN realized_pnl<0 THEN realized_pnl ELSE 0 END),0) gl "
            "FROM positions WHERE status='closed' AND closed_at >= ?",
            ("2026-06-22",),
        ).fetchone()
    except Exception as e:
        report.add("loop.round1", "info", f"could not read positions: {e}")
        return
    closed = row["closed"] or 0
    wins = row["wins"] or 0
    gp = float(row["gp"] or 0)
    gl = float(row["gl"] or 0)
    wr = round(wins / closed * 100, 1) if closed else None
    pf = round(gp / gl, 3) if gl > 0 else (None if gp == 0 else float("inf"))
    realized = float(row["realized"] or 0)
    target = 60
    gap = max(0, target - closed)
    detail = (f"closed={closed}/{target} (gap={gap}), realized={realized:+.2f}, "
              f"WR={wr if wr is not None else '?'}%, PF={pf if pf is not None else '?'}")
    # Round 1 terminated 2026-06-27 by owner decision. Report historical result as
    # info-only so it doesn't pollute the WARN list with a closed experiment's scores.
    if closed == 0:
        report.add("loop.round1", "info", "no closed trades since 2026-06-22 (Round 1 terminated)")
    else:
        report.add("loop.round1", "info", f"Round 1 final (terminated 2026-06-27): {detail}")


def _probe_knowledge_flood(report: Report) -> None:
    """Guard-candidate pipeline health.

    L-Bug-A' (trading_context.py) was fixed: Layer3 now queries guard_candidates
    directly by item_type, so table-level flooding no longer affects PI prompts.
    This probe now checks whether the pipeline is *producing* guard_candidates at all.
    """
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        total_guards = db.execute(
            "SELECT COUNT(*) FROM knowledge_items WHERE item_type='guard_candidate'"
        ).fetchone()[0]
        recent_guards = db.execute(
            "SELECT COUNT(*) FROM knowledge_items "
            "WHERE item_type='guard_candidate' AND created_at >= datetime('now','-7 days')"
        ).fetchone()[0]
    except Exception as e:
        report.add("loop.knowledge_flood", "info", f"probe error: {e}")
        return

    if total_guards == 0:
        report.add("loop.knowledge_flood", "warn",
                   "no guard_candidates produced — self-evolution pipeline not generating guards",
                   "check outcome_recorder and guard candidate generation")
    else:
        report.add("loop.knowledge_flood", "ok",
                   f"guard_candidates: {total_guards} total, {recent_guards} in last 7d")


def _probe_learning_retrieval(report: Report) -> None:
    """Learning retrieval-pool health — signal/noise + guard promotion stall.

    Two read-only checks surfaced by the 2026-06-26 full-lifecycle audit
    (docs/issues/LEARNING_LOOP_AUDIT_2026-06-26.md):

    1. retrieval_pool: the eligible pool (high_confidence + active) that the
       FROZEN ranker scores via _lesson_penalty is dominated by
       post_trade_review rows. Those reviews auto-upgrade to high_confidence on
       any fill, while trade_case_study needs has_full_chain — which is starved
       by the opportunity_id=None root cause (R0). A lopsided review:case ratio
       means the only live feedback loop is scoring against hallucination
       templates, not real attributed cases.
    2. guard_promotion: guard_candidate rows pile up while 0 trading_guard are
       active — the promotion chain is dead (config switch off + dir mismatch +
       missing frontmatter, audit 断点 B0/B2/B3).

    Both are MONITORING ONLY — the fixes touch the frozen decision chain and are
    deferred to Round 2 (round2_week1_decision_chain_fixes.md Fix L-Bug-C/-D).
    """
    try:
        from aletheia.storage.sqlite import get_db
        from collections import Counter
        db = get_db()

        # --- 1. eligible retrieval pool composition --------------------------
        pool: Counter = Counter()
        for r in db.execute(
            "SELECT item_type, COUNT(*) n FROM knowledge_items "
            "WHERE confidence='high_confidence' AND (status='active' OR status IS NULL) "
            "GROUP BY item_type"
        ):
            pool[r["item_type"]] = r["n"]
        total = sum(pool.values())
        if total == 0:
            report.add("loop.retrieval_pool", "info", "eligible pool empty yet")
        else:
            reviews = pool.get("post_trade_review", 0)
            cases = pool.get("trade_case_study", 0)
            review_pct = round(reviews / total * 100, 1)
            ratio = round(reviews / cases, 1) if cases else float("inf")
            detail = (f"pool={total}: post_trade_review={reviews}({review_pct}%), "
                      f"trade_case_study={cases}, review:case={ratio}")
            # warn when reviews flood the pool AND real attributed cases are starved
            if reviews > 0 and (ratio == float("inf") or ratio >= 20):
                report.add(
                    "loop.retrieval_pool", "warn",
                    f"retrieval pool skewed to historical reviews — {detail}",
                    "Stored-data debt only — this skew NO LONGER reaches AI decisions: "
                    "ranker _lesson_penalty (B1) and the advisory path "
                    "router._ITEM_TYPES_BY_ROLE (dbce73e) both now exclude "
                    "post_trade_review; new reviews are medium_confidence (C1) so they "
                    "never enter this high_confidence pool. Root cause R0 (opportunity_id "
                    "starving trade_case_study has_full_chain) is fixed — the residual "
                    "1.8k high-confidence reviews are Round-1 legacy that dilutes as "
                    "Round 2 emits full-chain cases, or clear via one-time reclassification "
                    "(LEARNING_LOOP_AUDIT_2026-06-26.md §R0/R1; L-Bug-C closed)")
            else:
                report.add("loop.retrieval_pool", "ok", detail)

        # --- 2. guard promotion stall ---------------------------------------
        # Active guards live in the file system (knowledge/guards/*.md with status=active),
        # not in knowledge_items. Count from files for an honest signal.
        candidates = db.execute(
            "SELECT COUNT(*) FROM knowledge_items WHERE item_type='guard_candidate'"
        ).fetchone()[0]
        active_guards = 0
        try:
            from aletheia.knowledge.guards import load_trading_guards
            active_guards = len(load_trading_guards(status="active"))
        except Exception:
            pass
        auto_promote = True
        try:
            from aletheia.config.loader import load_config
            auto_promote = load_config().guard.auto_promote_enabled
        except Exception:
            pass
        if candidates == 0:
            report.add("loop.guard_promotion", "info", "no guard_candidates yet")
        elif active_guards == 0:
            if not auto_promote:
                report.add("loop.guard_promotion", "info",
                           f"{candidates} guard_candidate(s) pending, 0 promoted "
                           f"(auto_promote_enabled=false — promotion disabled in config)",
                           "set guard.auto_promote_enabled=true in Settings to enable promotion")
            else:
                report.add(
                    "loop.guard_promotion", "warn",
                    f"{candidates} guard_candidate piled up, 0 promoted to trading_guard",
                    "promotion chain may be broken — check predicates frontmatter in "
                    "guard_candidates files and knowledge/guards/ directory. "
                    "Round 2 Fix L-Bug-D (audit 断点 B2/B3)")
        else:
            report.add("loop.guard_promotion", "ok",
                       f"{active_guards} active guards, {candidates} candidates")
    except Exception as e:
        report.add("loop.retrieval_pool", "info", f"probe error: {e}")


def _probe_reconciled_ratio(report: Report) -> None:
    """Fraction of recent closes that came from reconciler (positions vanished
    exchange-side) rather than AI-driven stop_loss / take_profit / pi_close.

    A high reconciled ratio means the Round 1 sample is dominated by orphan
    sweeping rather than genuine AI exits — judgement numbers (PF / WR) are
    being driven by data-integrity events instead of strategy performance.
    Read-only. See ROUND1_JUDGMENT_PREVIEW.md §1D for context.
    """
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        cutoff_72h = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
        rows = list(db.execute(
            "SELECT close_reason, closed_at FROM positions "
            "WHERE status='closed' AND closed_at >= ? "
            "ORDER BY closed_at DESC LIMIT 50",
            (cutoff_72h,),
        ))
        if not rows:
            report.add("loop.reconciled_ratio", "info",
                       "no closed positions since 2026-06-22")
            return
        from collections import Counter
        cnt: Counter = Counter(r[0] for r in rows)
        total = sum(cnt.values())
        reconciled = cnt.get("reconciled_gone", 0)
        units_bug = cnt.get("units_bug_cleanup", 0)
        ai_driven = total - reconciled - units_bug
        newest_reconciled = next((r[1] for r in rows if r[0] == "reconciled_gone"), None)
        newest_reconciled_age = _age_minutes(newest_reconciled) if newest_reconciled else None
        recon_pct = round(reconciled / total * 100, 1) if total else 0
        ai_pct = round(ai_driven / total * 100, 1) if total else 0
        detail = (f"last {total}: reconciled={reconciled}({recon_pct}%), "
                  f"units_bug={units_bug}, ai_driven={ai_driven}({ai_pct}%)")
        if recon_pct > 50:
            active_thrash = False
            try:
                from aletheia.portfolio.position_reconciler import detect_reconcile_thrash
                active_thrash = bool(detect_reconcile_thrash(window_minutes=30).get("churned_symbols"))
            except Exception:
                active_thrash = False
            if active_thrash:
                report.add("loop.reconciled_ratio", "warn",
                           f"reconciler dominates closes: {detail}",
                           "current close→reopen churn is present; judgement PF/WR is driven by "
                           "orphan sweep rather than AI exits")
            else:
                age_note = (
                    f"; newest reconciled {_fmt_age(newest_reconciled_age)}"
                    if newest_reconciled_age is not None else ""
                )
                report.add("loop.reconciled_ratio", "info",
                           f"historical reconciler-dominated closes: {detail}{age_note}; "
                           "no active churn fingerprint",
                           "recent judgement PF/WR remains polluted by historical orphan sweeps, but "
                           "loop.reconcile_thrash is the current-fault detector")
        elif recon_pct > 25:
            report.add("loop.reconciled_ratio", "info",
                       f"reconciled noticeable: {detail}")
        else:
            report.add("loop.reconciled_ratio", "ok", detail)
    except Exception as e:
        report.add("loop.reconciled_ratio", "info", f"probe error: {e}")


def _probe_sizing_policy(report: Report) -> None:
    """Persona-learning (P3) health: is the sizing learner active, and is any active
    regime bucket bleeding (negative clean reward while nudging appetite)? Dormant by
    default → info. A bucket that is active AND has negative mean reward is flagged
    warn (the learner is defensive there, but the operator should see the bleed).
    Read-only."""
    try:
        from aletheia.trading.sizing_policy import sizing_policy_health
        h = sizing_policy_health()
        if not h["enabled"]:
            report.add("loop.sizing_policy", "info", "persona learning dormant (persona_learning_enabled=False)")
            return
        active = {b: v for b, v in h["buckets"].items() if v["active"]}
        if not active:
            report.add("loop.sizing_policy", "info",
                       f"persona learning ON, no bucket past sample gate ({h['min_samples']})")
            return
        bleeding = {b: v for b, v in active.items() if (v["mean_reward_pct"] or 0) < 0}
        summary = ", ".join(f"{b}:n={v['clean_samples']} r={v['mean_reward_pct']}% adj={v['appetite_adj']}"
                            for b, v in active.items())
        if bleeding:
            report.add("loop.sizing_policy", "warn",
                       f"active learning buckets bleeding: {summary}",
                       "the learner is sizing DOWN there (defensive), but a negative-reward "
                       "regime bucket may warrant refreezing the param or reviewing the strategy")
        else:
            report.add("loop.sizing_policy", "ok", f"active learning buckets: {summary}")
    except Exception as e:
        report.add("loop.sizing_policy", "info", f"probe error: {e}")


def _probe_reconcile_thrash(report: Report) -> None:
    """Precise detector for the reconciler false-orphan THRASH (distinct from the
    ambiguous reconciled_ratio): the close→reopen flip-flop fingerprint — the same
    (symbol, direction) both reconciled_gone-closed AND re-adopted within 30 min.

    Unlike reconciled_ratio (which can be high for legitimate orphan cleanup too),
    this fingerprint only appears when the reconciler is fighting itself. It is the
    live REGRESSION tripwire for the 2026-07-01 broker-cache fix + a self-heal
    signal: run_reconcile's circuit-breaker suspends on the same condition. Read-only.
    """
    try:
        from aletheia.portfolio.position_reconciler import detect_reconcile_thrash
        t = detect_reconcile_thrash(window_minutes=30)
        churned = t["churned_symbols"]
        detail = (f"{t['churn_count']} churned in {t['window_minutes']}min "
                  f"(reconciled_gone={t['reconciled_gone_recent']}, "
                  f"adopted={t['adopted_recent']}); {', '.join(churned[:5]) or 'none'}")
        if t["is_thrashing"]:
            report.add("loop.reconcile_thrash", "warn",
                       f"reconciler thrash fingerprint: {detail}",
                       "same symbol both reconciled_gone-closed AND re-adopted in-window "
                       "= reconciler fighting itself. The run_reconcile circuit-breaker "
                       "should have suspended; investigate position_reconciler.classify_"
                       "positions (broker-cache mismatch?) — see ENTRY_EXIT_ASYMMETRY_"
                       "ANALYSIS_2026-07-01.md")
        elif t["churn_count"] == 1:
            report.add("loop.reconcile_thrash", "info",
                       f"single flip-flop (early signal, not tripping): {detail}")
        else:
            report.add("loop.reconcile_thrash", "ok", f"no thrash fingerprint: {detail}")
    except Exception as e:
        report.add("loop.reconcile_thrash", "info", f"probe error: {e}")


def _probe_concentration(report: Report) -> None:
    """24h fills concentration — flag if a single symbol×direction dominates.

    A >60% single-bucket concentration means PI is making essentially one bet
    repeatedly (e.g. \"ETH short\"), which is a 5x-correlated risk exposure
    instead of a diversified strategy basket. Read-only; observation only.
    """
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        rows = list(db.execute(
            "SELECT o.symbol || ':' || o.direction AS bucket, COUNT(*) n, MAX(f.filled_at) newest "
            "FROM fills f JOIN orders o ON f.order_id = o.id "
            "WHERE f.filled_at >= datetime('now', '-1 day') "
            "GROUP BY bucket ORDER BY n DESC"
        ))
        if not rows:
            report.add("loop.concentration", "info", "no fills in last 24h")
            return
        total = sum(r[1] for r in rows)
        top_bucket, top_n, top_newest = rows[0][0], rows[0][1], rows[0][2]
        top_age = _age_minutes(top_newest)
        top_pct = round(top_n / total * 100, 1) if total else 0
        detail = f"24h fills={total}, top: {top_bucket}={top_n} ({top_pct}%)"
        if top_pct > 60 and total >= 10:
            if top_age is not None and top_age > 360:
                report.add("loop.concentration", "info",
                           f"historical concentration residue: {detail}; newest top-bucket fill {_fmt_age(top_age)}")
            else:
                report.add("loop.concentration", "warn",
                           f"PI single-bet bias: {detail}",
                           "highly correlated exposure — PI may be making one bet "
                           "repeatedly; check trade_proposals for direction diversity")
        elif top_pct > 40:
            report.add("loop.concentration", "info",
                       f"moderate concentration: {detail}")
        else:
            report.add("loop.concentration", "ok", detail)
    except Exception as e:
        report.add("loop.concentration", "info", f"probe error: {e}")


def _probe_health(report: Report) -> None:
    try:
        from aletheia.agent_runtime.health import compute_health
        h = compute_health()
    except Exception as e:
        report.add("health.rollup", "info", f"compute_health failed: {e}")
        return
    if h.get("healthy"):
        report.add("health.rollup", "ok", "healthy (no issues)")
    else:
        issues = ", ".join(h.get("issues", []))
        report.add("health.rollup", "warn", f"degraded: {issues}")


def _probe_operator_loop(report: Report) -> None:
    """PI Operator Loop liveness — checks if it has run within expected cadence."""
    try:
        from aletheia.config.loader import load_config
        cfg = load_config()
        if not cfg.operator.loop_enabled:
            report.add("operator.loop", "info", "operator loop disabled in config")
            return

        from aletheia.storage.sqlite import get_db
        db = get_db()
        row = db.execute(
            "SELECT tags, created_at FROM knowledge_items "
            "WHERE item_type='operator_assessment' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            report.add("operator.loop", "info", "no operator assessment yet (first run pending)")
            return

        age = _age_minutes(row["created_at"])
        if age is None:
            report.add("operator.loop", "info", "cannot parse last assessment timestamp")
        elif age > 300:  # 5h — missed at least one 4h cadence
            report.add("operator.loop", "warn",
                       f"last operator assessment {_fmt_age(age)} — missed scheduled cadence",
                       "operator loop should run every 4h; check scheduler/gateway")
        else:
            report.add("operator.loop", "ok", f"last assessment {_fmt_age(age)}")
    except Exception as e:
        report.add("operator.loop", "info", f"probe error: {e}")


def _probe_webui_static(report: Report) -> None:
    """WebUI 静态文件完整性检查（新增诊断维度）。"""
    from pathlib import Path
    project_root = Path(__file__).resolve().parent.parent.parent
    static_dir = project_root / "aletheia" / "dashboard_static"
    index_html = static_dir / "index.html"

    if not static_dir.exists():
        report.add("webui.static", "blocker", "dashboard_static/ missing",
                   fix_hint="Run: cd frontend && npm install && npm run build")
        return
    if not index_html.exists():
        report.add("webui.static", "blocker", "index.html missing",
                   fix_hint="Run: cd frontend && npm run build")
        return

    size = index_html.stat().st_size
    if size < 100:
        report.add("webui.static", "warn", f"index.html suspiciously small ({size} bytes)",
                   fix_hint="Rebuild: cd frontend && npm run build")
        return

    assets_dir = static_dir / "assets"
    js_count = len(list(assets_dir.glob("*.js"))) if assets_dir.exists() else 0
    if js_count == 0:
        report.add("webui.static", "warn", "No JS bundles in assets/",
                   fix_hint="Run: cd frontend && npm run build")
        return

    report.add("webui.static", "ok", f"{size} bytes index, {js_count} JS bundles")


def _probe_git_hooks(report: Report) -> None:
    """Auto-restart git hooks — installed → a merge/pull/rebase respawns the gateway
    on fresh code with no manual restart (closes the code_updated_pending_restart gap).
    Pure read; `apply_fixes()` / gateway startup do the install."""
    from aletheia.gateway.git_hooks import git_hooks_status
    st = git_hooks_status()
    if st["error"]:
        report.add("git.hooks", "info", st["error"])  # tarball deploy / not a repo
        return
    missing, foreign = st["missing"], st["foreign"]
    if not missing and not foreign:
        report.add("git.hooks", "ok", f"auto-restart hooks installed ({', '.join(st['installed'])})")
        return
    if foreign:
        report.add("git.hooks", "warn",
                   f"foreign hook(s) present, not managed: {', '.join(foreign)}",
                   fix_hint="Inspect .git/hooks; auto-restart will skip these to avoid clobbering")
    if missing:
        report.add("git.hooks", "warn", f"auto-restart hooks missing: {', '.join(missing)}",
                   fix_hint="Run: aletheia doctor --fix  (or just restart the gateway — it self-installs)")


# --- content-quality probes (Phase 2: AI-native bug discovery) ---------------
# These differ from the operational probes above: instead of "is the loop turning",
# they ask "is the content the AI decision chain produced coherent / non-hallucinated
# / non-garbage" — exactly what a human used to find by eye in the Web UI's Evolve
# module. Each is read-only and follows the probe-honesty rule (owner-acknowledged /
# config-gated states report info with the numbers, not a false warn). See
# docs/plans/ai_native_bugfix_path.md §3. They are deliberately NON-overlapping with
# loop.knowledge_flood / loop.retrieval_pool / loop.reconciled_ratio / loop.concentration.

def _probe_content_strategy_health(report: Report) -> None:
    """Legacy strategy records (read-only). The rules engine was deleted (Position C
    2026-07-02): no new strategies are generated or promoted, so strategy_records is a
    frozen legacy table kept only for historical attribution. Probe-honesty: report the
    count as info — the old zombie / PI-approval WARN no longer applies (there is no
    engine to act on a stuck strategy)."""
    try:
        from aletheia.storage.sqlite import get_db
        row = get_db().execute("SELECT COUNT(*) AS n FROM strategy_records").fetchone()
        n = (row["n"] if row else 0) or 0
    except Exception as e:
        report.add("content.strategy_health", "info", f"probe error: {e}")
        return
    report.add("content.strategy_health", "info",
               f"{n} legacy strategy records (rules engine removed 2026-07-02; "
               "frozen legacy table, historical attribution only)")


def _probe_content_memo_quality(report: Report) -> None:
    """Trading memo (the regime playbook injected into PI context): absent vs
    expired vs (when memo_effectiveness has scored it) empirically overturned."""
    try:
        from aletheia.analytics.insight_synthesizer import get_latest_memo
        from aletheia.storage.repositories import list_knowledge_items
    except Exception as e:
        report.add("content.memo_quality", "info", f"probe error: {e}")
        return
    memo = None
    try:
        memo = get_latest_memo()  # None if absent OR all expired
    except Exception:
        pass
    if memo:
        # accuracy_score is written by memo_effectiveness (not yet on master); light
        # up only when the tag exists, so this probe sharpens automatically once it lands.
        try:
            rows = list_knowledge_items(item_type="trading_memo", limit=1)
            import json as _json
            tags = _json.loads(rows[0].get("tags") or "{}") if rows else {}
            acc = tags.get("accuracy_score")
        except Exception:
            acc = None
        if isinstance(acc, (int, float)) and acc < 0.4:
            report.add("content.memo_quality", "warn",
                       f"latest trading_memo accuracy_score={acc:.2f} (<0.40) — its rules were "
                       f"contradicted by outcomes (likely hallucinated playbook)",
                       "invalidate + re-synthesize the memo (insight_synthesizer.synthesize_trading_memo)")
        else:
            n = len(memo.get("regime_playbook") or [])
            report.add("content.memo_quality", "ok",
                       f"valid memo present ({n} regime rules, valid_until={memo.get('valid_until','?')[:16]})")
        return
    # No valid memo — distinguish expired from never-created.
    try:
        any_memo = list_knowledge_items(item_type="trading_memo", limit=1)
    except Exception:
        any_memo = []
    if any_memo:
        report.add("content.memo_quality", "warn",
                   "trading_memo exists but is expired — ranker _memo_scoring contributes 0 to every decision",
                   "re-synthesize: insight_synthesizer.synthesize_trading_memo()")
    else:
        report.add("content.memo_quality", "info", "no trading_memo yet")


def _probe_content_lesson_chain(report: Report) -> None:
    """ps_lessons learning→policy chain: lessons that never get accepted or applied
    are a dead loop (post-trade conclusions reach nothing)."""
    try:
        import importlib.util
        from aletheia.storage.sqlite import get_db
        from collections import Counter
        db = get_db()
        cnt: Counter = Counter()
        for r in db.execute("SELECT status, COUNT(*) n FROM ps_lessons GROUP BY status"):
            cnt[r["status"]] = r["n"]
        applied = db.execute(
            "SELECT COUNT(*) FROM ps_lessons WHERE applied_policy_version IS NOT NULL"
        ).fetchone()[0]
        # Is the evidence-gated promotion machinery wired? (find_spec does not import.)
        reviewer_wired = importlib.util.find_spec("aletheia.agents.lesson_reviewer") is not None
        # Backlog that is old enough to have been reviewed (cron runs daily).
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        stale_proposed = db.execute(
            "SELECT COUNT(*) FROM ps_lessons WHERE status='proposed' AND created_at < ?",
            (cutoff,),
        ).fetchone()[0]
    except Exception as e:
        report.add("content.lesson_chain", "info", f"probe error: {e}")
        return
    total = sum(cnt.values())
    if total == 0:
        report.add("content.lesson_chain", "info", "no lessons yet")
        return
    # The reviewer "acted" if any lesson left 'proposed' via accept/reject/apply.
    progressed = cnt.get("accepted", 0) + cnt.get("applied", 0) + cnt.get("rejected", 0)
    detail = f"lessons={total} ({dict(cnt)}), applied_to_policy={applied}"
    if progressed > 0 or applied > 0:
        report.add("content.lesson_chain", "ok", detail)
    elif not reviewer_wired:
        report.add("content.lesson_chain", "warn",
                   f"lesson→policy chain not built: {detail}",
                   "no lesson_reviewer agent — lessons stuck 'proposed', none promoted "
                   "(Round 2 fix L-Bug-B)")
    elif stale_proposed > 0:
        # Machinery exists but the backlog is not draining — a real failure.
        report.add("content.lesson_chain", "warn",
                   f"lesson reviewer wired but not draining backlog: {stale_proposed} proposed "
                   f">48h still unreviewed: {detail}",
                   "lesson_reviewer is registered but has promoted nothing — check the "
                   "lesson_reviewer cron job / LLM availability")
    else:
        # Wired, and the only proposed lessons are recent — honestly awaiting review.
        report.add("content.lesson_chain", "info",
                   f"lesson reviewer wired; {cnt.get('proposed', 0)} proposed awaiting "
                   f"evidence-gated review (cron 03:00 CST): {detail}")


def _probe_content_attribution(report: Report) -> None:
    """opportunity_id NULL rate on the RECENT ranker output (last ~200 signal_pending
    decisions) — the live attribution-health signal (LP-S07).

    Deliberately NOT a 7d / all-time aggregate: those are dominated by pre-fix NULL
    backlog that auto-prunes (observed 89% NULL over 7d while the most-recent 200 are
    0% NULL), so a window-based aggregate would false-alarm forever. signal_pending is
    the row where the ranker links the opportunity; a healthy ranker leaves it set.
    """
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        rows = list(db.execute(
            "SELECT opportunity_id FROM pi_decisions WHERE action='signal_pending' "
            "AND (reasoning IS NULL OR reasoning NOT LIKE 'RuleSet entry:%') "
            "ORDER BY created_at DESC LIMIT 200"
        ))
    except Exception as e:
        report.add("content.attribution", "info", f"probe error: {e}")
        return
    total = len(rows)
    if total < 20:
        report.add("content.attribution", "info",
                   f"only {total} recent signal_pending decisions — too few to judge attribution")
        return
    nulls = sum(1 for r in rows if r["opportunity_id"] is None)
    rate = nulls / total
    detail = f"recent {total} signal_pending: {nulls} missing opportunity_id ({rate:.0%})"
    if rate > 0.10:
        report.add("content.attribution", "warn",
                   f"broken attribution chain: {detail}",
                   "recent ranker output is dropping opportunity_id — learning can't attribute "
                   "outcomes (regression of the b9ad957 fix; check trading_decision opportunity_id wiring)")
    else:
        report.add("content.attribution", "ok", detail)


def _probe_content_shadow_trades(report: Report) -> None:
    """Orphan shadow trades never resolved — their counterfactual pnl stays NULL and
    edge-lab analysis silently loses that sample."""
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        total = db.execute("SELECT COUNT(*) FROM shadow_trades").fetchone()[0]
        if total == 0:
            report.add("content.shadow_trades", "info", "no shadow trades yet")
            return
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        stale = db.execute(
            "SELECT COUNT(*) FROM shadow_trades "
            "WHERE resolved_at IS NULL AND created_at < ?",
            (cutoff,),
        ).fetchone()[0]
    except Exception as e:
        report.add("content.shadow_trades", "info", f"probe error: {e}")
        return
    stale_pct = stale / total if total else 0.0
    if stale == 0:
        report.add("content.shadow_trades", "ok", f"{total} shadow trades, none stale")
    elif stale_pct > 0.25:
        report.add("content.shadow_trades", "warn",
                   f"{stale} shadow trades unresolved >24h ({stale_pct:.0%} of {total}) — "
                   "counterfactual pnl never computed",
                   "run scripts/backfill_shadow_trades.py for the historical backlog; the "
                   "shadow_resolver job catches up new ones (resolve_stale_shadows)")
    else:
        # Bounded residue: symbols that left the universe have no OHLCV to resolve
        # against, so a small unresolved tail is honest, not a systemic failure.
        report.add("content.shadow_trades", "info",
                   f"{stale} shadow trades unresolved >24h ({stale_pct:.0%} of {total}) — "
                   "bounded residue (delisted/out-of-universe symbols lack OHLCV to resolve)",
                   "shadow_resolver job handles new ones; older un-backfillable rows are expected")


def _probe_content_signal_settlement(report: Report) -> None:
    """universe_forward_outcomes that are past due_at but still pending — the outcome
    settler job is stalled, so signal effectiveness silently stops updating."""
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        total = db.execute("SELECT COUNT(*) FROM universe_forward_outcomes").fetchone()[0]
        if total == 0:
            report.add("content.signal_settlement", "info", "no forward outcomes yet")
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        overdue = db.execute(
            "SELECT COUNT(*) FROM universe_forward_outcomes "
            "WHERE status='pending' AND due_at IS NOT NULL AND due_at < ?",
            (now_iso,),
        ).fetchone()[0]
        oldest_due = None
        if overdue > 0:
            oldest_due = db.execute(
                "SELECT MIN(due_at) FROM universe_forward_outcomes "
                "WHERE status='pending' AND due_at IS NOT NULL AND due_at < ?",
                (now_iso,),
            ).fetchone()[0]
    except Exception as e:
        report.add("content.signal_settlement", "info", f"probe error: {e}")
        return
    if overdue > 0:
        oldest_age = _age_minutes(oldest_due)
        # outcome_settler runs every 15 min — tolerate up to 20 min before alarming
        if oldest_age is not None and oldest_age > 20:
            report.add("content.signal_settlement", "warn",
                       f"{overdue} forward outcomes past due_at, oldest {_fmt_age(oldest_age)} — settler stalled",
                       "outcome settler job is not closing due outcomes; check its scheduler cadence")
        else:
            report.add("content.signal_settlement", "info",
                       f"{overdue} outcomes past due_at, oldest {_fmt_age(oldest_age)} — within settler cadence")
    else:
        report.add("content.signal_settlement", "ok", f"{total} forward outcomes, settlement current")


def _probe_content_proposal_bias(report: Report) -> None:
    """Directional bias at the PROPOSAL layer (before risk). loop.concentration only
    sees fills (after risk rejection); a model that proposes only 'short' is invisible
    there if risk happens to reject a mixed subset."""
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        rows = list(db.execute(
            "SELECT direction, COUNT(*) n FROM trade_proposals "
            "WHERE created_at >= ? AND (source IS NULL OR source != 'adopted') "
            "GROUP BY direction ORDER BY n DESC",
            (cutoff,),
        ))
    except Exception as e:
        report.add("content.proposal_bias", "info", f"probe error: {e}")
        return
    total = sum(r["n"] for r in rows)
    if total < 20:
        report.add("content.proposal_bias", "info", f"only {total} proposals in 24h — too few to judge bias")
        return
    top_dir, top_n = rows[0]["direction"], rows[0]["n"]
    top_pct = top_n / total
    detail = f"24h proposals={total}, top: {top_dir}={top_n} ({top_pct:.0%})"
    if top_pct > 0.80:
        try:
            from aletheia.agent_shell.trading_decision import _proposal_direction_overheated
            brake = _proposal_direction_overheated(top_dir, 70)
        except Exception:
            brake = None
        if brake:
            report.add("content.proposal_bias", "info",
                       f"proposal-layer directional bias mitigated: {detail}; direction brake active",
                       brake)
        else:
            report.add("content.proposal_bias", "warn",
                       f"proposal-layer directional bias: {detail}",
                       "PI proposes almost exclusively one direction (pre-risk) — check trading_decision "
                       "for a stuck regime read or one-sided signal inputs")
    else:
        report.add("content.proposal_bias", "ok", detail)


def _egress_ip(opener) -> str | None:
    """Best-effort external egress IP via an opener. None on any failure."""
    import urllib.request  # noqa: F401 — opener carries its own handlers

    try:
        return opener.open("https://api.ipify.org", timeout=6).read().decode("utf-8").strip()
    except Exception:
        return None


def _probe_network(report: Report) -> None:
    try:
        from aletheia.config.loader import load_config
        net = load_config().network
        proxy_url = net.proxy_url
        enforce = bool(getattr(net, "enforce", False))
        protocol = getattr(net, "proxy_protocol", "http")
    except Exception as e:
        report.add("network.proxy", "info", f"could not read config: {e}")
        return

    mode = "ENFORCE/fail-closed" if enforce else "fail-open"

    if not proxy_url:
        if enforce:
            report.add("network.proxy", "warn",
                       "enforce is ON but no proxy is configured — all outbound traffic is blocked",
                       "set a proxy host in Settings → Network, or turn enforce off")
        else:
            report.add("network.proxy", "ok", "not configured (direct connection)")
        return

    import time
    import urllib.request

    # SOCKS can't be probed with urllib's ProxyHandler — defer reachability to the
    # exchange connectivity monitor (which rides the aiohttp_socks path).
    if str(protocol).startswith("socks"):
        report.add("network.proxy", "info",
                   f"{protocol} proxy configured [{mode}] — {proxy_url}; "
                   "reachability verified by the exchange connectivity monitor")
        return

    proxy_opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"https": proxy_url, "http": proxy_url})
    )
    t0 = time.monotonic()
    try:
        proxy_opener.open("https://api.binance.com/api/v3/ping", timeout=8).read()
        latency_ms = int((time.monotonic() - t0) * 1000)
    except Exception as e:
        latency_ms = int((time.monotonic() - t0) * 1000)
        hint = "check the proxy is running and host/port are correct in Settings → Network"
        if enforce:
            hint += "; enforce is ON, so trading auto-pauses until the proxy recovers"
        report.add("network.proxy", "warn",
                   f"proxy configured but unreachable [{mode}] ({latency_ms}ms): {type(e).__name__}",
                   hint)
        return

    # Leak check: confirm traffic actually egresses via the proxy (exit IP differs
    # from a direct connection). A match means requests may NOT be routed through
    # the proxy despite it being reachable.
    proxy_ip = _egress_ip(proxy_opener)
    direct_ip = _egress_ip(urllib.request.build_opener(urllib.request.ProxyHandler({})))
    if proxy_ip and direct_ip and proxy_ip == direct_ip:
        report.add("network.proxy", "warn",
                   f"proxy reachable [{mode}] ({latency_ms}ms) but egress IP == direct IP "
                   f"({proxy_ip}) — traffic may NOT be routed through the proxy",
                   "verify the proxy forwards to an upstream with a different exit IP")
        return

    leak_note = f"; egress {proxy_ip} ≠ direct {direct_ip}" if (proxy_ip and direct_ip) else ""
    report.add("network.proxy", "ok",
               f"proxy reachable [{mode}] ({latency_ms}ms) — {proxy_url}{leak_note}")


def _probe_audit_chain(report: Report) -> None:
    """Verify audit JSONL hash chain integrity."""
    from aletheia.files.jsonl import verify_audit_chain
    result = verify_audit_chain()
    records = result.get("records", 0)
    if records == 0:
        report.add("trust.audit_chain", "info", "no audit records yet")
        return
    reanchors = result.get("reanchors", 0)
    reanchor_extra = f", {reanchors} rotation re-anchors" if reanchors else ""
    if result["valid"]:
        report.add("trust.audit_chain", "ok", f"hash chain valid ({records} records{reanchor_extra})")
    else:
        errors = result.get("errors", [])
        error_timestamps = result.get("error_timestamps", [])
        # Split at the current effective write-path fix. The original file lock
        # serialized appends, but each gateway process still cached its own chain
        # tail; frontdoor/backend interleaving could therefore continue from a
        # stale _prev_hash. The writer now re-reads the chain tail while holding
        # the cross-process lock, so only breaks after this timestamp indicate
        # an active write-path or tamper problem.
        EFFECTIVE_CHAIN_FIX_TS = "2026-07-03T08:00:00+00:00"
        effective_ts = _parse_iso_datetime(EFFECTIVE_CHAIN_FIX_TS)
        ts_padded = list(error_timestamps) + [""] * len(errors)
        # #179 fix: undateable breaks (no parseable timestamp on the surrounding
        # audit event) used to be silently excluded from `post_fix`; when every
        # break was undateable the code declared "all historical, info" and
        # masked a real post-fix corruption that happened to also break the
        # timestamp. Now we split into three bins so an undateable break can't
        # hide behind the historical label.
        post_fix: list[str] = []
        undateable: list[str] = []
        pre_fix_count = 0
        for e, t in zip(errors, ts_padded):
            parsed = _parse_iso_datetime(t)
            if parsed is None:
                undateable.append(e)
            elif effective_ts is not None and parsed > effective_ts:
                post_fix.append(e)
            else:
                pre_fix_count += 1

        if not post_fix and not undateable:
            report.add("trust.audit_chain", "info",
                       f"{len(errors)} historical chain break(s), all before current "
                       f"cross-process cache fix ({EFFECTIVE_CHAIN_FIX_TS}); "
                       f"zero post-lock breaks ({records} records{reanchor_extra})")
        elif post_fix:
            detail = f"chain BROKEN — {len(post_fix)} post-fix error(s) in {records} records: {post_fix[:3]}"
            report.add("trust.audit_chain", "warn", detail,
                       "audit log may have been tampered with or corrupted (post hash-cache fix)")
        else:
            # Only undateable breaks — can't confirm they're historical, so
            # surface them as warn (not info) rather than hiding them.
            detail = (
                f"{len(undateable)} chain break(s) with unparseable timestamp — "
                f"cannot confirm historical vs post-fix; treat as WARN: {undateable[:3]}"
            )
            report.add("trust.audit_chain", "warn", detail,
                       "inspect audit log manually; missing timestamps prevent automatic classification")


def _probe_config_consistency(report: Report) -> None:
    """Cross-field config consistency — detects contradictions like disabled default provider."""
    from aletheia.config.loader import load_config
    from aletheia.diagnostics.config_consistency import check_config_consistency
    cfg = load_config()
    findings = check_config_consistency(cfg)
    if not findings:
        report.add("config.consistency", "ok", "no cross-field inconsistencies")
        return
    # #178 fix: config_consistency emits three severities — "info", "warning",
    # "blocker" — but this branch previously mapped only "warning"→"warn" and
    # collapsed everything else (including the info-severity ones) to "blocker".
    # An info finding (e.g. a hint about optional demo credentials) escalated
    # to a doctor BLOCKER drove `doctor` exit code 1, which downstream tooling
    # reads as "system is broken". Three-way map matches source severity honestly.
    _SEV_MAP: dict[str, Severity] = {"info": "info", "warning": "warn", "blocker": "blocker"}
    for f in findings:
        sev: Severity = _SEV_MAP.get(f.severity, "blocker")
        report.add(f"config.consistency.{f.field_path}", sev, f.message, f.fix_hint)


def _probe_fts_coverage(report: Report) -> None:
    """Check FTS5 index coverage vs eligible knowledge items."""
    from aletheia.storage.sqlite import get_db
    db = get_db()
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_fts'"
    ).fetchone()
    if not row:
        report.add("fts.coverage", "warn", "knowledge_fts table missing — experience loop disabled",
                   "run: uv run aletheia kb reindex")
        return
    eligible = db.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE confidence='high_confidence' AND (status='active' OR status IS NULL)"
    ).fetchone()[0]
    indexed = db.execute("SELECT COUNT(*) FROM knowledge_fts").fetchone()[0]
    if eligible == 0:
        report.add("fts.coverage", "ok", "no eligible items yet")
        return
    pct = indexed / eligible
    detail = f"{indexed}/{eligible} indexed ({pct:.0%})"
    if pct >= 0.8:
        report.add("fts.coverage", "ok", detail)
    elif pct >= 0.5:
        report.add("fts.coverage", "warn", detail, "restart gateway to trigger reindex")
    else:
        report.add("fts.coverage", "blocker", detail,
                   "experience loop severely degraded — restart gateway or run: uv run aletheia kb reindex")


def _probe_ohlcv_coverage(report: Report) -> None:
    """Check OHLCV historical data coverage for active universe members."""
    try:
        from aletheia.storage.repositories import get_active_universe_members
        from aletheia.storage.sqlite import get_db
        from aletheia.config.loader import load_config

        db = get_db()
        total = db.execute("SELECT COUNT(*) FROM market_ohlcv").fetchone()[0]
        if total == 0:
            report.add("data.ohlcv", "warn",
                       "market_ohlcv is empty — backtest and Optuna cannot run",
                       "run: uv run aletheia data backfill")
            return

        cfg = load_config()
        min_bars = cfg.historical_data.min_bars_for_backtest
        members = get_active_universe_members()
        if not members:
            report.add("data.ohlcv", "info",
                       f"market_ohlcv: {total} total bars (no active members to verify coverage)")
            return

        gaps: list[str] = []
        for m in members:
            sym = m.get("symbol", "")
            if not sym:
                continue
            bars = db.execute(
                "SELECT COUNT(*) FROM market_ohlcv WHERE symbol=? AND timeframe='1h'",
                (sym,),
            ).fetchone()[0]
            if bars < min_bars:
                gaps.append(f"{sym}({bars})")

        symbols_covered = db.execute(
            "SELECT COUNT(DISTINCT symbol) FROM market_ohlcv WHERE timeframe='1h'"
        ).fetchone()[0]

        if gaps:
            report.add(
                "data.ohlcv", "warn",
                f"{len(gaps)}/{len(members)} active members below {min_bars} bars: "
                + ", ".join(gaps[:5]) + ("…" if len(gaps) > 5 else ""),
                "run: uv run aletheia data backfill",
            )
        else:
            report.add(
                "data.ohlcv", "ok",
                f"OHLCV: {total} total bars, {symbols_covered} symbols, "
                f"all {len(members)} active members ≥{min_bars} bars",
            )
    except Exception:
        pass


def _probe_signal_effectiveness(report: Report) -> None:
    """Check universe signal win-rate over the last 7 days."""
    try:
        from aletheia.storage.sqlite import get_db
        from datetime import timedelta

        db = get_db()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

        # 24h horizon — most meaningful for strategy effectiveness
        row = db.execute(
            """SELECT COUNT(*),
                      SUM(CASE WHEN forward_return_pct > 0 THEN 1 ELSE 0 END),
                      AVG(forward_return_pct)
               FROM universe_forward_outcomes
               WHERE status = 'settled'
                 AND horizon = '24h'
                 AND settled_at >= ?
                 AND ABS(forward_return_pct) <= 100""",
            (cutoff,),
        ).fetchone()

        total, wins, avg_ret = row if row else (0, None, None)

        if not total:
            report.add("data.signal", "info",
                       "insufficient settled outcomes in last 7d — signal effectiveness unknown")
            return

        win_rate = (wins or 0) / total * 100
        avg_str = f"{avg_ret:.3f}%" if avg_ret is not None else "n/a"

        if win_rate < 40:
            report.add(
                "data.signal", "warn",
                f"24h signal win_rate {win_rate:.1f}% ({total} settled, avg={avg_str}) — "
                "universe selection may be degraded",
                "review scanner weights; consider updating Optuna strategy params",
            )
        elif win_rate >= 55:
            report.add("data.signal", "ok",
                       f"24h signal win_rate {win_rate:.1f}% ({total} settled, avg={avg_str})")
        else:
            report.add("data.signal", "info",
                       f"24h signal win_rate {win_rate:.1f}% ({total} settled, avg={avg_str}) — near-random")
    except Exception:
        pass


def _probe_serenity_pipeline(report: Report) -> None:
    """Serenity research pipeline freshness — last run age + active opportunity count."""
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        row = db.execute(
            "SELECT MAX(created_at) newest FROM serenity_research_runs"
        ).fetchone()
        active = db.execute(
            "SELECT COUNT(*) FROM serenity_opportunities WHERE status = 'active'"
        ).fetchone()[0]
    except Exception as e:
        report.add("data.serenity_pipeline", "info", f"probe error: {e}")
        return
    if not row["newest"]:
        report.add("data.serenity_pipeline", "warn",
                   "no serenity research runs recorded",
                   "check serenity_research_runner scheduler job is registered and running")
        return
    age = _age_minutes(row["newest"])
    detail = f"last run {_fmt_age(age)}, {active} active opportunities"
    if age is not None and age > 1560:  # 26h — serenity runs once per 24h; 12h fired daily false-positives
        report.add("data.serenity_pipeline", "warn",
                   f"Serenity research stale: {detail}",
                   "serenity_research_runner has not run in >26h — check scheduler health")
    else:
        report.add("data.serenity_pipeline", "ok", detail)


def _probe_volume_ranking(report: Report) -> None:
    """Volume ranking snapshot freshness — drives universe ordering for paper_trader."""
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        row = db.execute(
            "SELECT MAX(observed_at) newest FROM volume_ranking_snapshots"
        ).fetchone()
    except Exception as e:
        report.add("data.volume_ranking", "info", f"probe error: {e}")
        return
    if not row["newest"]:
        report.add("data.volume_ranking", "warn",
                   "no volume ranking snapshots recorded",
                   "universe ordering falls back to static list; check volume_scanner scheduler job")
        return
    age = _age_minutes(row["newest"])
    detail = f"volume ranking snapshot {_fmt_age(age)}"
    if age is not None and age > 60:
        report.add("data.volume_ranking", "warn",
                   f"volume ranking stale: {detail}",
                   "volume_scanner job may have failed — universe ordering falls back to static list")
    else:
        report.add("data.volume_ranking", "ok", detail)


def _probe_infra_db_size(report: Report) -> None:
    """app.db file size and available disk space — a full disk silently kills SQLite writes."""
    try:
        import os
        import shutil
        from aletheia.storage.sqlite import db_path
        path = db_path()
        db_mb = path.stat().st_size / (1024 * 1024) if path.exists() else 0
        du = shutil.disk_usage(str(path.parent))
        free_mb = du.free / (1024 * 1024)
    except Exception as e:
        report.add("infra.db_size", "info", f"probe error: {e}")
        return
    detail = f"app.db {db_mb:.0f} MB, disk free {free_mb:.0f} MB"
    if free_mb < 500:
        report.add("infra.db_size", "warn",
                   f"low disk space: {detail}",
                   "disk nearly full — SQLite writes may fail; free space or run "
                   "uv run python scripts/db_maintenance.py --vacuum")
    elif db_mb > 500:
        report.add("infra.db_size", "warn",
                   f"app.db large: {detail}",
                   "run uv run python scripts/db_maintenance.py --vacuum to reclaim space")
    elif db_mb > 200:
        report.add("infra.db_size", "info", f"app.db growing: {detail}")
    else:
        report.add("infra.db_size", "ok", detail)


def _probe_content_lesson_guards(report: Report) -> None:
    """Accepted ps_lessons: dry-run vs active-blocking vs expired guard status.

    Lessons with block_* actions directly affect what trades the risk engine allows.
    Showing guard activation state makes risk behavior changes observable in doctor.
    """
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        lessons = db.execute(
            "SELECT lesson_id, dry_run_until, expires_at FROM ps_lessons WHERE status = 'accepted'"
        ).fetchall()
    except Exception as e:
        report.add("content.lesson_guards", "info", f"probe error: {e}")
        return
    if not lessons:
        report.add("content.lesson_guards", "ok", "no accepted lessons (no active risk guards)")
        return
    now_iso = datetime.now(timezone.utc).isoformat()
    dry_run, blocking, expired = 0, 0, 0
    for row in lessons:
        if row["expires_at"] and now_iso > row["expires_at"]:
            expired += 1
        elif row["dry_run_until"] and now_iso < row["dry_run_until"]:
            dry_run += 1
        else:
            blocking += 1
    total = len(lessons)
    detail = f"{total} accepted: {blocking} blocking, {dry_run} dry-run, {expired} expired"
    if blocking > 0:
        report.add("content.lesson_guards", "info",
                   detail,
                   f"{blocking} lesson(s) are actively blocking trades via the risk engine")
    else:
        report.add("content.lesson_guards", "ok", detail)


# The content-quality probe set (Phase 2). Named once so build_report() and the
# Operator's System Health Monitor (build_content_report) cannot drift apart.
_CONTENT_PROBES = (
    _probe_content_strategy_health,
    _probe_content_memo_quality,
    _probe_content_lesson_chain,
    _probe_content_lesson_guards,
    _probe_content_attribution,
    _probe_content_shadow_trades,
    _probe_content_signal_settlement,
    _probe_content_proposal_bias,
)


def build_content_report() -> Report:
    """Run ONLY the content-quality probes — the cheap subset of build_report() that
    asks "is the AI's output garbage", not "is the loop alive". Used by the PI
    Operator to ingest content-health into its assessment without the full 20+ probe
    sweep (which includes a localhost HTTP probe and other heavier checks). Pure read."""
    report = Report(generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    for probe in _CONTENT_PROBES:
        try:
            probe(report)
        except Exception as e:
            report.add(probe.__name__, "info", f"probe error: {e}")
    return report


def build_report() -> Report:
    """Probe every dimension of ground truth. Pure read — changes nothing."""
    report = Report(generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    for probe in (
        _probe_code_sync,
        _probe_process,
        _probe_gateway_restart_history,
        _probe_config_gates,
        _probe_config_consistency,
        _probe_credentials,
        _probe_market_data,
        _probe_universe_market_coverage,
        _probe_orderbook,
        _probe_perp_structure,
        _probe_macro_signals,
        _probe_coinalyze,
        _probe_prediction_markets,
        _probe_serenity_pipeline,
        _probe_volume_ranking,
        _probe_data_quality,
        _probe_ohlcv_coverage,
        _probe_signal_effectiveness,
        _probe_trading_loop,
        _probe_profit_loop,
        _probe_round1,
        _probe_knowledge_flood,
        _probe_learning_retrieval,
        _probe_reconciled_ratio,
        _probe_reconcile_thrash,
        _probe_sizing_policy,
        _probe_concentration,
        # content-quality (Phase 2): "is the AI's output garbage", not "is the loop alive"
        *_CONTENT_PROBES,
        _probe_health,
        _probe_operator_loop,
        _probe_webui_static,
        _probe_git_hooks,
        _probe_network,
        _probe_infra_db_size,
        _probe_audit_chain,
        _probe_fts_coverage,
    ):
        try:
            probe(report)
        except Exception as e:  # a broken probe must never sink the whole report
            report.add(probe.__name__, "info", f"probe error: {e}")
    return report


# --- conservative auto-repair (opt-in, --fix) ---

def apply_fixes() -> list[str]:
    """Deterministic, reversible, non-trading repairs only.

    Per the project safety rules, this NEVER clears the kill switch and NEVER
    touches trading state — it only fixes infrastructure: clears orphan gateway
    processes / stale pidfiles and starts the gateway if it is down. Returns a
    list of human-readable actions taken.
    """
    actions: list[str] = []
    from aletheia.gateway.pidfile import is_running
    from aletheia.gateway.lifecycle import do_start

    # Wire the auto-restart git hooks (idempotent; independent of gateway state, so
    # do it before the running-check early return).
    from aletheia.gateway.git_hooks import ensure_git_hooks
    actions.extend(ensure_git_hooks())

    if is_running():
        actions.append("gateway already running — nothing to start")
        return actions

    # do_start already self-heals: it sweeps orphan/ghost-socket processes holding
    # the port before spawning, and refuses if a foreign process holds it. We reuse
    # that rather than duplicating the cleanup logic.
    try:
        do_start(no_browser=True, foreground=False)
        actions.append("started gateway in background (with orphan/port cleanup)")
    except SystemExit:
        # do_start raises typer.Exit on port-still-held; surface it as a non-fix.
        actions.append("could not start gateway — port still held by a foreign process "
                       "(see `aletheia gateway start` output)")
    except Exception as e:
        actions.append(f"gateway start failed: {e}")
    return actions
