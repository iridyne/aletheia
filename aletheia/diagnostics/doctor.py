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
import os
import platform
from pathlib import Path
import sys
from typing import Literal

Severity = Literal["ok", "info", "warn", "blocker"]

_OUTCOME_SETTLER_GRACE_MINUTES = 20

# `llm.inference` evidence window. Deliberately module constants, not config
# fields: a new config-schema field would owe the Settings UI a control, and an
# operator has no reason to tune how far back doctor looks for LLM outcomes.
_LLM_EVIDENCE_WINDOW_MINUTES = 60
# Below this many attempts in the window, an all-failed run is reported as `warn`
# rather than `blocker`: one or two failures can be a single transient request,
# and doctor's exit code 1 should mean "this system cannot work", not "a call
# failed once".
_LLM_EVIDENCE_MIN_SAMPLE = 3
# A working provider that still drops some calls is worth a warning.
_LLM_DEGRADED_SUCCESS_RATE = 0.5

# A managed Gateway creates its durable Runtime Session during application
# startup, shortly after the supervisor/inner process starts.  Doctor may run in
# a separate CLI process, so its in-memory ``current_session()`` is empty even
# while that Gateway is healthy.  Keep the process/session join bounded: a wide
# heuristic would turn a genuinely stale crash row into false current authority.
_RUNTIME_SESSION_PROCESS_MATCH_SECONDS = 300


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
        "candidate_market_facts": _safe_candidate_market_facts_diagnostic(),
    }


def _safe_candidate_market_facts_diagnostic() -> dict:
    try:
        from aletheia.data.candidate_market_facts import (
            build_pending_candidate_market_report,
        )

        return build_pending_candidate_market_report()
    except Exception as exc:
        return {"status": "unavailable", "reason": str(exc)}


def _safe_alpha_diagnostic() -> dict:
    try:
        from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit

        report = build_alpha_attribution_audit(days=30)
        verdicts = report.get("verdicts") or {}
        return {
            "available": True,
            "main_bottleneck": report.get("main_bottleneck"),
            "universe_twin_evidence": report.get("universe_twin_evidence"),
            "exit_paired_evidence": report.get("exit_paired_evidence"),
            "risk_policy_evidence": report.get("risk_policy_evidence"),
            "execution_cost_evidence": report.get("execution_cost_evidence"),
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
    try:
        from aletheia.config.loader import get_data_dir

        runtime_root = get_data_dir()
    except Exception:
        runtime_root = Path.home() / ".aletheia"
    logs = runtime_root / "logs"
    if name.startswith("process.") or name.startswith("webui."):
        return f"{logs / 'errors.log'} or {logs / 'frontdoor-errors.log'}"
    if name.startswith("data."):
        return f"{logs / 'errors.log'} plus recent scheduler/collector agent_runs"
    if name.startswith("loop.") or name.startswith("content.") or name.startswith("operator."):
        return f"{logs / 'agent_runs.jsonl'} and {runtime_root / 'app.db'} agent_runs/run_steps"
    if name.startswith("trust.") or name.startswith("config."):
        return str(logs / "audit.jsonl")
    if name.startswith("network.") or name.startswith("creds."):
        return f"{logs / 'errors.log'}; secrets are intentionally never printed"
    return str(logs)


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
        from aletheia.agent_runtime.run_view import finished_at, summarize_run_errors
        from aletheia.storage.sqlite import get_db
        db = get_db()
        rows = db.execute(
            "SELECT id, agent_name, status, started_at, "
            "ended_at AS finished_at, errors AS error "
            "FROM agent_runs "
            "WHERE status NOT IN ('completed', 'success') "
            "OR COALESCE(NULLIF(errors, ''), '[]') <> '[]' "
            "ORDER BY COALESCE(finished_at, started_at) DESC LIMIT ?",
            (limit,),
        ).fetchall()
        failures: list[dict] = []
        for row in rows:
            summary = summarize_run_errors(row["error"])
            if row["status"] in ("completed", "success") and not summary:
                continue
            failures.append(
                {
                    "id": row["id"],
                    "agent_name": row["agent_name"],
                    "status": row["status"],
                    "started_at": row["started_at"],
                    "finished_at": finished_at(dict(row)),
                    "error": _shorten_secret_safe(summary),
                }
            )
        return failures
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


def _probe_legacy_launchd(report: Report) -> None:
    """Warn when a known superseded Aletheia LaunchAgent is still registered."""
    if platform.system() != "Darwin":
        return
    try:
        from aletheia.gateway.autostart import _legacy_launchd_present
        found = _legacy_launchd_present()
    except Exception:
        return
    if found:
        labels = ", ".join(found)
        report.add(
            "process.legacy_launchd",
            "warn",
            f"legacy Aletheia lifecycle job(s) still loaded: {labels}",
            "`aletheia gateway enable-autostart` migrates them automatically",
        )
    else:
        report.add("process.legacy_launchd", "ok", "no legacy Aletheia lifecycle jobs")


def _probe_gateway_service(report: Report) -> None:
    """Report installation, enablement, ownership, and runtime independently."""
    try:
        from aletheia.gateway import service_control
        state = service_control.status()
    except Exception as exc:
        report.add("process.gateway_service", "info", f"service state not available: {type(exc).__name__}")
        return
    detail = (
        f"backend={state.get('backend')} registration={state.get('registration')} "
        f"installed={state.get('installed')} enabled={state.get('enabled')} "
        f"runtime={state.get('runtime')} recovery={state.get('recovery_scope')}"
    )
    if state.get("reason") in {"backend_conflict", "ownership_conflict", "access_denied", "config_drift", "partial_install"}:
        report.add("process.gateway_service", "warn", f"{detail}; reason={state.get('reason')}", "run `aletheia gateway service status` for remediation")
    elif state.get("installed"):
        report.add("process.gateway_service", "ok", detail)
    else:
        report.add("process.gateway_service", "info", detail, "optional: `aletheia gateway service install --backend session`")


def _probe_process(report: Report) -> None:
    from aletheia.gateway.pidfile import is_running, read_pid, port_in_use
    from aletheia.config.loader import load_config
    from aletheia.gateway.network import local_frontdoor_host
    cfg = load_config()
    host = local_frontdoor_host()
    runtime_port = os.environ.get("ALETHEIA_RUNTIME_PORT")
    try:
        port = int(runtime_port) if runtime_port else cfg.app.dashboard_port
    except ValueError:
        port = cfg.app.dashboard_port

    # An API Runtime knows its own process and bound port. Prefer that identity
    # over the shared lifecycle pidfile, which belongs to the managed current
    # Gateway and may describe another local instance.
    if runtime_port:
        pid = os.getpid()
        runtime_id = os.environ.get("ALETHEIA_RUNTIME_ID", "runtime-local-current")
        report.add(
            "process.gateway",
            "ok",
            f"serving {runtime_id} (PID {pid}) on {host}:{port}",
        )
        _probe_frontdoor_process(report, host, port)
        running = True
    else:
        running = is_running()
        pid = read_pid()

        if running:
            if port_in_use(port, host):
                report.add("process.gateway", "ok", f"running (PID {pid}) on {host}:{port}")
                _probe_frontdoor_process(report, host, port)
            else:
                report.add("process.gateway", "warn", f"process running (PID {pid}) but no listener on {host}:{port}")
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


def _probe_windows_lan_firewall(report: Report) -> None:
    """Tell the truth when a wildcard bind is still blocked by Windows."""
    if platform.system() != "Windows":
        return
    from aletheia.config.loader import load_config
    from aletheia.gateway.windows_firewall import status
    cfg = load_config()
    if cfg.app.bind_host not in {"0.0.0.0", "::"}:
        return
    state = status(cfg.app.dashboard_port)
    if state.get("configured"):
        report.add(
            "network.windows_firewall",
            "ok",
            f"local-subnet TCP {cfg.app.dashboard_port} allowed; "
            f"backend port {cfg.app.dashboard_port + 1} not opened",
        )
        return
    if state.get("reason"):
        report.add(
            "network.windows_firewall",
            "warn",
            f"Windows LAN firewall state could not be verified: {state['reason']}",
            "run `aletheia gateway firewall status` from an interactive Windows desktop",
        )
        return
    conflicts = state.get("conflicting_block_rules") or []
    detail = f"wildcard bind on TCP {cfg.app.dashboard_port} is not permitted for LAN clients"
    if conflicts:
        detail += f"; conflicting Windows block rules={len(conflicts)}"
    report.add(
        "network.windows_firewall",
        "warn",
        detail,
        "run `aletheia gateway firewall enable-lan` from an interactive Windows desktop",
    )

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
    gateway_ready = any(
        check.name == "process.gateway" and check.severity == "ok"
        for check in report.checks
    )
    backend_checks = [check for check in report.checks if check.name == "process.backend"]
    backend_ready = not backend_checks or any(check.severity == "ok" for check in backend_checks)
    recovered = critical_seen and gateway_ready and backend_ready
    severity: Severity = "warn" if total >= 10 or (critical_seen and not recovered) else "info"
    fix_hint = ""
    if total >= 10:
        fix_hint = "high restart volume — check explicit restarts, frontdoor port loss, and supervisor errors"
    elif critical_seen and not recovered:
        fix_hint = f"spawn/give-up event seen — inspect {_log_hint_for_check('process.gateway')}"
    recovered_text = "; historical failure recovered, current gateway healthy" if recovered else ""
    report.add(
        "process.restart_history",
        severity,
        f"24h restart/liveness events={total}; {top_text}{latest}{recovered_text}",
        fix_hint,
    )


def _probe_config_gates(report: Report) -> None:
    from aletheia.config.loader import load_config
    cfg = load_config()
    report.add("config.permission", "info", f"permission_mode={cfg.agent.permission_mode}")
    report.add("config.live", "info",
               f"live.state={cfg.live.state}, exchange.mode={cfg.exchange.mode}, "
               f"paper_execution_mode={getattr(cfg.exchange, 'paper_execution_mode', '?')}")

    demo_cap = getattr(cfg.risk, "max_demo_position_margin_usdt", None)
    paper_mode = getattr(cfg.exchange, "paper_execution_mode", "")
    if (
        cfg.exchange.mode == "demo"
        and paper_mode in {"paper_internal", "bitget_demo", "okx_demo", "binance_testnet"}
        and demo_cap is not None
        and float(demo_cap) <= 0
    ):
        report.add(
            "config.demo_margin_cap",
            "blocker",
            f"max_demo_position_margin_usdt={float(demo_cap):g} — every paper/demo proposal is rejected",
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

    from aletheia.agent_runtime.pause_ownership import (
        active_pause_blocker_names,
        active_pause_owners,
        pause_blocked_reason,
    )
    owners = active_pause_owners(cfg)
    blockers = active_pause_blocker_names(cfg)
    if cfg.runtime.paused or owners:
        owner_rows = owners
        owner_text = ", ".join(
            (
                f"{row['owner']} ({row['reason']}) [generation={row['generation'] or 'unknown'}; "
                f"age={row['age_seconds'] if row['age_seconds'] is not None else 'unknown'}s; "
                f"source={row['source'] or 'unknown'}; scope={row['scope'] or 'behavior'}]"
            )
            for row in owner_rows
        ) or "unknown"
        blocked_text = ", ".join(blockers) or "none for current execution pool"
        blocked_reason = pause_blocked_reason(cfg) or "none"
        severity = "warn" if blockers else "info"
        report.add(
            "config.paused",
            severity,
            f"runtime.paused={cfg.runtime.paused} — owners: {owner_text}; "
            f"resume blockers: {blocked_text}; reason={blocked_reason}",
            "clear the owning condition, then use `aletheia gateway resume`"
            if blockers else "use `aletheia gateway resume` to reconcile scheduler state",
        )


def _probe_credentials(report: Report) -> None:
    """Which secrets are SET vs MISSING.

    `creds.llm` answers "are the credentials shaped correctly", which is NOT
    "can this system do inference". Keep its wording confined to configuration;
    reachability is `llm.inference`'s job.
    """
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
        report.add("creds.llm", "ok",
                   f"LLM credentials configured (default={provider}, type={ptype}) "
                   "— inference not verified here, see llm.inference")
        return

    secret_ref = pcfg.api_key_secret_ref
    if secret_ref:
        if get_secret(secret_ref):
            report.add("creds.llm", "ok",
                       f"LLM credentials configured (default={provider}, secret_ref={secret_ref}) "
                       "— inference not verified here, see llm.inference")
        else:
            report.add("creds.llm", "warn",
                       f"LLM provider secret missing (default={provider}, secret_ref={secret_ref})",
                       "Settings -> Model Provider, or set the configured model secret")
        return

    endpoint = pcfg.workspace_url or pcfg.base_url
    if _is_local_model_endpoint(endpoint):
        report.add("creds.llm", "ok",
                   f"LLM credentials configured, no API key needed (default={provider}, "
                   f"local endpoint {endpoint}) — inference not verified here, see llm.inference")
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


def _llm_call_evidence(window_minutes: int = _LLM_EVIDENCE_WINDOW_MINUTES) -> dict:
    """Outcomes of the trading loop's own recent LLM calls.

    `runtime_cost_events` is written by the provider tracing wrapper on EVERY
    physical attempt (`status='success'` or `status='error'`), so this is real
    production traffic rather than a synthetic ping — which matters, because a
    reachability ping can pass while every real call fails. It costs one indexed
    aggregate and no latency.

    Windows on `started_at`, which is what `idx_runtime_cost_events_window
    (event_type, started_at)` covers. The tracing wrapper always writes it as a
    UTC-normalized `isoformat()` string for `event_type='llm'`, so the
    lexicographic `>=` is sound; other event types use a different timestamp
    format, which makes the event_type filter load-bearing rather than a mere
    selector.
    """
    from aletheia.storage.sqlite import get_db

    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=window_minutes)).isoformat()
    db = get_db()
    empty = {
        "attempts": 0,
        "ok": 0,
        "window_minutes": window_minutes,
        "error_type": None,
        "provider": None,
    }
    # The economics schema is created lazily on the first traced call, so an
    # absent table means "no LLM call has ever run here" — not a broken probe.
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runtime_cost_events'"
    ).fetchone():
        return empty
    row = db.execute(
        """SELECT COUNT(*) AS attempts,
                  SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS ok
           FROM runtime_cost_events
           WHERE event_type = 'llm' AND started_at >= ?""",
        (cutoff,),
    ).fetchone()
    attempts = int((row["attempts"] if row else 0) or 0)
    ok = int((row["ok"] if row else 0) or 0)
    evidence = {**empty, "attempts": attempts, "ok": ok}
    if attempts and ok < attempts:
        worst = db.execute(
            """SELECT error_type, provider, COUNT(*) AS n
               FROM runtime_cost_events
               WHERE event_type = 'llm' AND started_at >= ? AND status <> 'success'
               GROUP BY error_type, provider
               ORDER BY n DESC LIMIT 1""",
            (cutoff,),
        ).fetchone()
        if worst:
            evidence["error_type"] = worst["error_type"] or "unknown_error"
            evidence["provider"] = worst["provider"]
    return evidence


def _probe_llm_inference(report: Report, *, active_probe: bool = False) -> None:
    """Whether the LLM can actually answer — the question `creds.llm` cannot answer.

    Configured credentials are not a working provider: a broken gateway keeps
    answering its own liveness endpoint while returning 500 to every completion,
    so agent decisions fail continuously behind a green credentials tick. That is
    the 'False confidence' failure mode `agent_runtime/startup_gate` exists to
    catch, and doctor was blind to it.

    Evidence order: the ledger of real calls first (free, real traffic); an
    opt-in real inference call (`doctor --probe-llm`) only when an operator asks,
    because it costs a live completion and up to ~20s per provider and doctor is
    also on the UI and operator-loop paths.
    """
    try:
        evidence = _llm_call_evidence()
    except Exception as e:
        report.add("llm.inference", "info", f"LLM call ledger unreadable: {e}")
        evidence = None

    if evidence is not None:
        attempts, ok = evidence["attempts"], evidence["ok"]
        window = evidence["window_minutes"]
        if not attempts:
            hint = "" if active_probe else "`aletheia doctor --probe-llm` to verify with a real call"
            report.add("llm.inference", "info",
                       f"no LLM call in last {window}min — reachability unverified", hint)
        elif not ok:
            failure = evidence["error_type"]
            provider = evidence["provider"] or "provider"
            detail = (f"0/{attempts} real LLM calls succeeded in last {window}min "
                      f"({failure}) — agent decisions cannot complete")
            if attempts >= _LLM_EVIDENCE_MIN_SAMPLE:
                report.add("llm.inference", "blocker", detail,
                           f"the '{provider}' endpoint is not serving completions — "
                           "check the model gateway, then Settings -> Model Provider")
            else:
                report.add("llm.inference", "warn", detail + " (small sample)",
                           "`aletheia doctor --probe-llm` to confirm")
        elif ok < attempts * _LLM_DEGRADED_SUCCESS_RATE:
            report.add("llm.inference", "warn",
                       f"only {ok}/{attempts} real LLM calls succeeded in last {window}min "
                       f"({evidence['error_type']}) — decisions fail intermittently",
                       "check the model gateway for rate limits or timeouts")
        else:
            report.add("llm.inference", "ok",
                       f"{ok}/{attempts} real LLM calls succeeded in last {window}min")

    if active_probe:
        _probe_llm_inference_active(report)


def _probe_llm_inference_active(report: Report) -> None:
    """Force ground truth with one real minimal completion per enabled provider.

    Reuses the startup gate's prober so `--probe-llm` and the startup verdict can
    never disagree. `max_retries=1` keeps this to a single round.
    """
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass  # no loop on this thread — safe to drive one
    else:
        report.add("llm.inference.probe", "info",
                   "active probe skipped: doctor is running inside an event loop",
                   "run `aletheia doctor --probe-llm` from the CLI instead")
        return

    try:
        from aletheia.agent_runtime.startup_gate import probe_llm_providers_at_startup
        from aletheia.config.loader import load_config

        result = asyncio.run(probe_llm_providers_at_startup(load_config(), max_retries=1))
    except Exception as e:
        report.add("llm.inference.probe", "info", f"active probe could not run: {e}")
        return

    if not result.get("checked"):
        report.add("llm.inference.probe", "warn",
                   "active probe ran against no provider — none is enabled",
                   "Settings -> Model Provider, or `aletheia model set`")
        return

    results = result.get("results") or []
    usable = [r["provider"] for r in results if r.get("usable")]
    if result.get("any_usable"):
        report.add("llm.inference.probe", "ok",
                   f"real inference call succeeded ({', '.join(usable)})")
        return
    failures = ", ".join(f"{r['provider']}: {r.get('error') or 'unknown error'}" for r in results)
    report.add("llm.inference.probe", "blocker",
               f"real inference call failed on all {result['checked']} enabled provider(s) "
               f"— {failures}",
               "repair the model endpoint; agent decisions cannot complete until it answers")


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


def _probe_market_data_venue_health(report: Report) -> None:
    """Report in-process venue cooldowns instead of hiding an active outage."""
    from aletheia.data.venue_health import get_venue_health_snapshot

    cooling = [item for item in get_venue_health_snapshot() if item["cooling_down"]]
    if not cooling:
        return
    detail = ", ".join(
        f"{item['venue']} ({item['cooldown_remaining_sec']:.0f}s remaining)"
        for item in cooling
    )
    report.add(
        "data.market_venue_health",
        "warn",
        f"market-data venue cooldown active: {detail}",
        "wait for the cooldown or resolve the venue's rate-limit/IP-ban response",
    )


def _probe_collector_health(report: Report) -> None:
    """Surface whether scheduled domains ran, timed out, or lost the shared budget."""
    try:
        from aletheia.data.collector_health import get_collector_health_report

        state = get_collector_health_report()
    except Exception as e:
        report.add("data.collectors", "info", f"collector status unavailable: {e}")
        return

    domains = state["domains"]
    status = state["status"]
    if status == "initializing":
        report.add(
            "data.collectors",
            "info",
            "collector status initializing; no domain has completed a scheduled tick yet",
        )
        return

    problem_rows = [
        row for row in domains if row.get("degraded") or row.get("last_status") in {
            "failed", "timed_out"
        }
    ]
    inactive = [
        row["domain"] for row in domains
        if row.get("last_status") in {"disabled", "not_configured"}
    ]
    detail = (
        f"{state['recorded_count']}/{state['domain_count']} domains reporting; "
        f"{state['completed_count']} completed on latest due tick"
    )
    if inactive:
        detail += f"; inactive={','.join(inactive)}"

    if status == "degraded":
        def _fmt_problem(row: dict) -> str:
            s = f"{row['domain']}:{row['last_status']}"
            extras: list[str] = []
            if row.get("last_error_code"):
                extras.append(row["last_error_code"])
            cons = row.get("consecutive_problems") or 0
            if cons > 1:
                extras.append(f"{cons}x")
            if row.get("overdue"):
                extras.append("overdue")
            if extras:
                s += f"[{','.join(extras)}]"
            return s

        problems = ", ".join(_fmt_problem(row) for row in problem_rows)
        report.add(
            "data.collectors",
            "warn",
            f"{detail}; degraded={problems}",
            "inspect GET /api/market/collector-health and gateway errors; keep the 40s budget bounded",
        )
    elif status == "attention":
        problems = ", ".join(
            f"{row['domain']}:{row['last_status']}" for row in problem_rows
        )
        report.add(
            "data.collectors",
            "info",
            f"{detail}; transient={problems}",
        )
    else:
        report.add("data.collectors", "ok", detail)


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


def _probe_pending_candidate_market_coverage(report: Report) -> None:
    """Judge exact pending-carrier facts instead of one global newest row."""
    try:
        from aletheia.data.candidate_market_facts import (
            build_pending_candidate_market_report,
        )

        state = build_pending_candidate_market_report()
    except Exception as e:
        report.add(
            "data.pending_candidate_market_coverage",
            "info",
            f"candidate coverage unavailable: {type(e).__name__}",
            "inspect the read-only candidate market-facts diagnostic",
        )
        return

    total = int(state.get("pending_total") or 0)
    if state.get("status") == "no_pending" or total == 0:
        report.add(
            "data.pending_candidate_market_coverage",
            "info",
            "no active pending carriers — no denominator",
        )
        return

    complete = int(state.get("complete_count") or 0)
    partial = int(state.get("partial_count") or 0)
    unavailable = int(state.get("unavailable_count") or 0)
    detail = (
        f"pending={total}; complete={complete}; partial={partial}; "
        f"unavailable={unavailable}"
    )
    affected = [
        item for item in (state.get("items") or [])
        if item.get("availability") != "complete"
    ][:3]
    if affected:
        samples = ", ".join(
            f"{item.get('venue') or 'unknown'}:{item.get('symbol') or 'unknown'}:"
            f"{item.get('physical_market_type') or 'unknown'}"
            f"[{','.join((item.get('reasons') or ['unknown'])[:2])}]"
            for item in affected
        )
        detail += f"; affected sample={samples}"

    if state.get("status") == "complete":
        report.add("data.pending_candidate_market_coverage", "ok", detail)
        return
    report.add(
        "data.pending_candidate_market_coverage",
        "warn",
        detail,
        "inspect `aletheia doctor --deep --json` candidate_market_facts; "
        "do not relax freshness or exact venue/market constraints",
    )


def _probe_orderbook(report: Report) -> None:
    """Expose whether L2 orderbook snapshots are actually reaching storage."""
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        row = db.execute(
            "SELECT exchange, symbol, market_type, observed_at, spread_bps, total_depth_10bps_usd "
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
        f"newest orderbook {row['exchange']}:{row['symbol']}:{row['market_type']} "
        f"({_fmt_age(age)}), "
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
                "not configured: no trusted automatic perp structure source "
                "(observation-only)",
                "run migrations, then use the owner aggregate import only when "
                "a trustworthy export is available",
            )
            return
        report.add("data.perp_structure", "info", f"could not read perp structure: {e}")
        return

    if not row:
        from aletheia.data.collectors.vergex_structure import structure_source_status
        source_status = structure_source_status()
        report.add(
            "data.perp_structure",
            "info",
            "not configured: no trusted automatic perp structure source; "
            f"mode={source_status['mode']} (observation-only)",
            "import an owner-authorized aggregate with `aletheia data "
            "ingest-perp-structure PATH` when a trustworthy export is available",
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
            report.add("data.liquidations", "info", "Coinalyze liquidation feed disabled in settings")
            return

        from aletheia.data.collectors.coinalyze import is_configured
        if not is_configured():
            report.add(
                "data.coinalyze",
                "info",
                "Coinalyze optional feed is enabled but not configured",
                "add a Coinalyze API key only if this optional cross-venue feed is needed",
            )
            report.add(
                "data.liquidations",
                "info",
                "Coinalyze liquidation feed unavailable while Coinalyze is not configured",
            )
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
                "Coinalyze configured but no OI rows yet",
                "check Coinalyze key/quota and the data_collectors job",
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
                "info",
                "no liquidation events observed yet",
                "event-driven feed is idle until a real liquidation occurs",
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

        # Session uptime (display only) and whether MO has run yet this session.
        # Using agent_runs as the warmup signal avoids false suppression during
        # high-churn restart periods (git hooks, self-heal) where session_uptime
        # would always reset before 30 min and permanently hide real stalls.
        session_uptime = None
        mo_ran = True  # default to True so unknown DB state doesn't suppress warn
        try:
            session_row = db.execute(
                "SELECT started_at FROM runtime_sessions WHERE ended_at IS NULL"
                " ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            if session_row:
                session_uptime = _age_minutes(session_row[0])
                run_row = db.execute(
                    "SELECT id FROM agent_runs WHERE agent_name='market_observer'"
                    " AND started_at >= ? LIMIT 1",
                    (session_row[0],),
                ).fetchone()
                mo_ran = run_row is not None
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
        if not mo_ran:
            # MO hasn't fired yet this session — scheduler is still warming up.
            uptime_str = f", session {_fmt_age(session_uptime)} old" if session_uptime is not None else ""
            report.add("loop.newest_opportunity", "info",
                       f"newest opportunity {_fmt_age(opp_age)}{uptime_str}"
                       " — warming up after restart, first MO run pending")
        else:
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
        # Attribute the stall using the shared opportunity-progress aggregator
        # so old-commit skips can never be blamed on the currently-running code.
        # Six-bucket classification: direct_model_skip / conviction_gate_skip /
        # debate_not_confirmed / operator_defer / context_unavailable / ranker_cooldown.
        attribution = ""
        self_protecting = False
        try:
            from aletheia.diagnostics.pipeline_progress import summarize as _op_summarize
            snapshot = _op_summarize(hours=12)
            operator = snapshot.get("operator") or {}
            epoch = snapshot.get("epoch") or {}
            section_status = snapshot.get("section_status") or {}
            commit_short = (epoch.get("producer_commit") or "unknown")[:7]
            epoch_short = (epoch.get("policy_epoch_id") or "none")[:12]

            # Check availability before treating numeric zeros as measured facts
            op_available = (section_status.get("operator") or {}).get("status") == "available"
            defer_available = (section_status.get("defer_events") or {}).get("status") == "available"
            active_defer_available = (section_status.get("active_defer") or {}).get("status") == "available"

            if not op_available:
                reason_short = (section_status.get("operator") or {}).get("reason", "unknown error")[:60]
                attribution = (
                    f" — epoch commit={commit_short} policy={epoch_short}"
                    f"; operator diagnostics unavailable ({reason_short})"
                )
            else:
                direct_skip = int(operator.get("direct_model_skip") or 0)
                conviction_skip = int(operator.get("conviction_gate_skip") or 0)
                debate_reject = int(operator.get("debate_not_confirmed") or 0)
                defer_raw = operator.get("operator_defer")
                defer = int(defer_raw) if defer_raw is not None else None
                trade_intent = int(operator.get("operator_trade_intent") or 0)
                direction_brake = int(operator.get("direction_brake") or 0)
                total_skip = direct_skip + conviction_skip + debate_reject

                # Optional distress bypass — only meaningful against the current
                # epoch's direct model skips, so read reasons scoped to the current
                # producer_commit rather than the raw 12h window.
                commit_full = epoch.get("producer_commit")
                if total_skip > 0 and commit_full:
                    reason_rows = db.execute(
                        "SELECT reasoning FROM pi_decisions "
                        "WHERE action='skip' AND producer_commit = ? "
                        "AND COALESCE(signal_source, 'market_observer') != 'system_cascade' "
                        "AND julianday(created_at) >= julianday('now','-12 hours') "
                        "ORDER BY julianday(created_at) DESC LIMIT 10",
                        (commit_full,),
                    ).fetchall()
                    distress_kws = ("structural distress", "acute distress", "systematic distress")
                    for r in reason_rows:
                        if any(k in (r[0] or "").lower() for k in distress_kws):
                            self_protecting = True
                            break
                    if not self_protecting and cfg.agent.bypass_distress_gate:
                        for r in reason_rows:
                            if "suppressed" in (r[0] or "").lower():
                                self_protecting = True
                                break

                # Defer breakdown — market_quiet vs candidate_specific + oldest age +
                # 12h defer_rate. Populated by pipeline_progress._active_defer_stats
                # over live rows (defer_until > now); best-effort so an older running
                # gateway with no active defers renders defer×N unmodified.
                if defer is not None and active_defer_available:
                    mq_raw = operator.get("operator_defer_market_quiet")
                    cs_raw = operator.get("operator_defer_candidate_specific")
                    mq = int(mq_raw) if mq_raw is not None else 0
                    cs = int(cs_raw) if cs_raw is not None else 0
                    defer_rate_raw = operator.get("defer_rate")
                    defer_rate = float(defer_rate_raw) if defer_rate_raw is not None else 0.0
                    oldest_seconds = operator.get("oldest_active_defer_age_seconds")
                    if defer > 0 and (mq or cs or oldest_seconds is not None):
                        age_min = (
                            int(oldest_seconds // 60) if isinstance(oldest_seconds, int) else None
                        )
                        age_part = f",age={age_min}m" if age_min is not None else ""
                        rate_part = f",rate={int(round(defer_rate * 100))}%"
                        defer_suffix = f"/defer×{defer}(mq={mq}/cs={cs}{age_part}{rate_part})"
                    else:
                        defer_suffix = f"/defer×{defer}"
                elif defer is None and not defer_available:
                    defer_suffix = "/defer(unavailable)"
                elif defer is None:
                    # operator says defer is None but defer_events section succeeded —
                    # this is the transitional case; treat as zero for compatibility
                    defer_suffix = "/defer×0"
                else:
                    defer_suffix = f"/defer×{defer}"

                attribution = (
                    f" — epoch commit={commit_short} policy={epoch_short}"
                    f"; operator (12h) direct_skip×{direct_skip}"
                    f"/conviction_gate×{conviction_skip}"
                    f"/debate_reject×{debate_reject}"
                    f"{defer_suffix}"
                    f"/direction_brake×{direction_brake}"
                    f"/trade_intent×{trade_intent}"
                )
        except Exception:
            pass
        prop_desc = "none ever" if prop_age is None else _fmt_age(prop_age)
        if self_protecting:
            bypass_note = " (bypass_distress_gate active — deliberate skip)" if cfg.agent.bypass_distress_gate else ""
            report.add("loop.profit", "info",
                       f"System Operator self-protecting{bypass_note}: opportunities fresh ({_fmt_age(opp_age)});"
                       f" newest proposal {prop_desc}{attribution}",
                       "deliberate protective behavior — not a pipeline failure; "
                       "System Operator sees adverse trading_context (low WR / recent SL streak)")
        else:
            report.add("loop.profit", "warn",
                       f"PROPOSAL FLOW STALLED: opportunities fresh ({_fmt_age(opp_age)}) but "
                       f"newest proposal {prop_desc}{attribution}",
                       "opportunities flow but produce no proposals — inspect "
                       "paper_trader_ranker scoring + System Operator decision inputs "
                       "(see docs/archive/legacy/issues/archive/audits/MINIMUM_PROFIT_LOOP_AUDIT.md)")
    elif prop_age is not None and prop_age < PROPOSAL_STALL_MIN:
        report.add("loop.profit", "ok",
                   f"proposal flow healthy — newest proposal {_fmt_age(prop_age)}; "
                   "profitability not assessed")
    else:
        report.add("loop.profit", "info",
                   f"opportunities {_fmt_age(opp_age)}, proposals {_fmt_age(prop_age) if prop_age else 'none'}")


def _probe_skip_halt(report: Report) -> None:
    """Expose candidate-skip evidence gaps without changing trading behavior.

    ``loop.profit`` detects proposal drought.  This companion probe answers the
    necessary next question: are candidate-scoped skips actually auditable and
    waiting for fixed outcomes, or did the Operator consume work without trace
    evidence?  Legacy unscoped heartbeat skips are reported separately and never
    enter the candidate cohort.  Current zero-pending heartbeats are counted from
    their dedicated observation table.  The probe is read-only by design.
    """
    try:
        from aletheia.analytics.skip_diagnosis import diagnose_skip_halt
        from aletheia.storage.sqlite import get_db

        db = get_db()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        row = db.execute(
            """SELECT
                 COUNT(*) AS candidate_skips,
                 SUM(CASE WHEN NOT EXISTS (
                     SELECT 1 FROM filter_decisions fd
                     WHERE fd.candidate_key = 'pi_decision:' || p.id
                       AND fd.filter_name = 'operator_action_assessment'
                 ) THEN 1 ELSE 0 END) AS missing_trace,
                 SUM(CASE WHEN (
                     SELECT COUNT(DISTINCT fco.horizon)
                     FROM filter_candidate_outcomes fco
                     WHERE fco.outcome_candidate_key = 'pi_decision:' || p.id
                 ) < 4 THEN 1 ELSE 0 END) AS missing_outcome_seed
               FROM pi_decisions p
               WHERE p.action = 'skip'
                 AND p.opportunity_id IS NOT NULL
                 AND COALESCE(p.signal_source, 'market_observer') != 'system_cascade'
                 AND julianday(p.created_at) >= julianday(?)""",
            (cutoff,),
        ).fetchone()
        administrative_skips = db.execute(
            """SELECT COUNT(*) FROM pi_decisions
               WHERE action='skip' AND opportunity_id IS NOT NULL
                 AND signal_source='system_cascade'
                 AND julianday(created_at) >= julianday(?)""",
            (cutoff,),
        ).fetchone()[0]
        unscoped = db.execute(
            """SELECT COUNT(*) FROM pi_decisions
               WHERE action = 'skip' AND opportunity_id IS NULL
                 AND julianday(created_at) >= julianday(?)""",
            (cutoff,),
        ).fetchone()[0]
        no_candidate_events = db.execute(
            """SELECT COUNT(*) FROM operator_evaluation_events
               WHERE event_kind = 'no_evaluable_candidate'
                 AND julianday(created_at) >= julianday(?)""",
            (cutoff,),
        ).fetchone()[0]
        deferred_context_events = db.execute(
            """SELECT COUNT(*) FROM operator_evaluation_events
               WHERE event_kind LIKE 'operator_decision_deferred_%'
                 AND julianday(created_at) >= julianday(?)""",
            (cutoff,),
        ).fetchone()[0]
        path_counts = db.execute(
            """SELECT
                 SUM(CASE WHEN fd.reason_code='operator_model_skip' THEN 1 ELSE 0 END)
                   AS direct_model_skips,
                 SUM(CASE WHEN fd.reason_code='operator_model_trade' THEN 1 ELSE 0 END)
                   AS trade_intent_gated_skips,
                 SUM(CASE WHEN fd.reason_code='operator_model_skip'
                           AND COALESCE(json_extract(fd.input_snapshot,
                               '$.confirmation_attempts'), 0) > 0
                          THEN 1 ELSE 0 END) AS confirmed_adverse_skips
               FROM filter_decisions fd
               JOIN pi_decisions p
                 ON fd.candidate_key='pi_decision:' || p.id
               WHERE fd.filter_name='operator_action_assessment'
                 AND p.action='skip'
                 AND p.opportunity_id IS NOT NULL
                 AND COALESCE(p.signal_source, 'market_observer') != 'system_cascade'
                 AND julianday(p.created_at) >= julianday(?)""",
            (cutoff,),
        ).fetchone()
        pending = db.execute(
            """SELECT COUNT(*) FROM pi_decisions
               WHERE action = 'signal_pending'
                 AND julianday(created_at) >= julianday(?)""",
            (cutoff,),
        ).fetchone()[0]
        # A reload can leave pre-remediation decisions inside the rolling 24h
        # window.  Keep those raw counts visible, but judge the current B1
        # behavior only from the active, versioned remediation epoch.  This is
        # a read-only reporting boundary; it never creates/rotates an epoch.
        epoch_row = db.execute(
            """SELECT epoch_id FROM learning_policy_epochs
               WHERE epoch_kind='system_operator_skip_remediation'
                 AND ended_at IS NULL
               ORDER BY started_at DESC LIMIT 1"""
        ).fetchone()
        policy_epoch_id = str(epoch_row[0]) if epoch_row is not None else None
        proposals = db.execute(
            "SELECT COUNT(*) FROM trade_proposals "
            "WHERE julianday(created_at) >= julianday(?)",
            (cutoff,),
        ).fetchone()[0]
    except Exception as exc:
        report.add("loop.skip_halt", "info", f"probe error: {exc}")
        return

    candidate_skips = int((row or [0])[0] or 0)
    missing_trace = int((row or [0, 0])[1] or 0)
    missing_outcome_seed = int((row or [0, 0, 0])[2] or 0)
    direct_model_skips = int((path_counts or [0])[0] or 0)
    trade_intent_gated_skips = int((path_counts or [0, 0])[1] or 0)
    confirmed_adverse_skips = int((path_counts or [0, 0, 0])[2] or 0)
    diagnosis = diagnose_skip_halt(policy_epoch_id=policy_epoch_id)
    scoped_counts = diagnosis.counts
    epoch_label = policy_epoch_id or "none"
    detail = (
        f"raw_24h_candidate_skips={candidate_skips}, raw_24h_missing_trace={missing_trace}, "
        f"raw_24h_missing_outcome_seed={missing_outcome_seed}, "
        f"raw_24h_direct_model_skips={direct_model_skips}, "
        f"raw_24h_trade_intent_gated_skips={trade_intent_gated_skips}, "
        f"raw_24h_confirmed_adverse_skips={confirmed_adverse_skips}, "
        f"administrative_cascade_skips_excluded={int(administrative_skips or 0)}, "
        f"unscoped_heartbeat_skips={int(unscoped or 0)}, "
        f"no_evaluable_candidate_events={int(no_candidate_events or 0)}, "
        f"deferred_unchanged_context_events={int(deferred_context_events or 0)}, "
        f"raw_24h_pending_signals={int(pending or 0)}, proposals={int(proposals or 0)}, "
        f"diagnosis_epoch={epoch_label}, "
        f"current_epoch_candidate_skips={int(scoped_counts.get('candidate_skips', 0))}, "
        f"mature_epoch_candidate_skips={int(scoped_counts.get('mature_candidate_skips', 0))}, "
        f"immature_epoch_candidate_skips={int(scoped_counts.get('immature_candidate_skips', 0))}, "
        f"current_epoch_pending_signals={int(scoped_counts.get('pending_signals', 0))}, "
        f"current_epoch_unreconstructable_adverse_evidence="
        f"{int(scoped_counts.get('unreconstructable_adverse_evidence', 0))}, "
        f"finding={diagnosis.finding}, threshold={diagnosis.threshold_version}"
    )
    fix_hint = (
        "inspect candidate skip trace/outcome coverage and the read-only Skip diagnosis; "
        "does not change PI, config, Risk, or execution"
    )
    if diagnosis.finding in {"recording_incident", "operator_liveness_incident", "diagnosis_unavailable"}:
        report.add("loop.skip_halt", "warn", detail, fix_hint)
    elif unscoped:
        report.add(
            "loop.skip_halt",
            "warn",
            detail + " — legacy unscoped heartbeat skips are excluded from effectiveness cohorts",
            fix_hint,
        )
    elif candidate_skips:
        finding_note = {
            "awaiting_outcomes": (
                "recent candidate evidence complete; await the preceding bounded "
                "cohort's fixed-horizon settlement"
            ),
            "protective_skip_observed": (
                "mature fixed-horizon cohort supports retaining the current policy"
            ),
        }.get(diagnosis.finding, "candidate evidence complete; no behavior change authorized")
        report.add(
            "loop.skip_halt",
            "info",
            detail + f" — {finding_note}",
            fix_hint,
        )
    else:
        report.add(
            "loop.skip_halt", "info",
            detail + " — no candidate-scoped skips in window; no-target heartbeats are observations",
            fix_hint,
        )


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
    (docs/archive/legacy/issues/audits/LEARNING_LOOP_AUDIT_2026-06-26.md):

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

        # --- 2. guard promotion / shadow-candidate pool ---------------------
        # Both counts come from FILES (knowledge/guards + guard_candidates by
        # frontmatter status), never knowledge_items: the DB can retain legacy /
        # cross-platform file paths, and only the frontmatter status is authoritative
        # for the guard lifecycle.
        active_guards = pending_candidates = 0
        try:
            from aletheia.knowledge.guards import load_trading_guards
            active_guards = len(load_trading_guards(status="active"))
            pending_candidates = len(load_trading_guards(status="candidate"))
        except Exception:
            pass
        auto_promote = False
        try:
            from aletheia.config.loader import load_config
            auto_promote = load_config().guard.auto_promote_enabled
        except Exception:
            pass
        # P0 2026-07-12: learned/auto guards are shadow-only and can never auto-promote
        # into blockers (that needs the ranker_learned_guard experiment). A backlog of
        # shadow candidates is expected observation feed, NOT a broken promotion chain.
        detail = f"{active_guards} active guard(s), {pending_candidates} pending shadow candidate(s)"
        fix = None
        if auto_promote:
            fix = ("guard.auto_promote_enabled=true — learned guards still cannot "
                   "auto-promote, but disable it in Settings to match the intended default")
        report.add("loop.guard_promotion", "ok", detail, fix)
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
        rows = [dict(row) for row in db.execute(
            "SELECT * FROM positions "
            "WHERE status='closed' AND closed_at >= ? "
            "ORDER BY closed_at DESC LIMIT 50",
            (cutoff_72h,),
        )]
        if not rows:
            report.add("loop.reconciled_ratio", "info",
                       "no closed positions since 2026-06-22")
            return
        from collections import Counter
        from aletheia.analytics.outcome_integrity import classify_closure
        categories: Counter = Counter(classify_closure(row).category for row in rows)
        total = len(rows)
        reconciled = categories["administrative"]
        maintenance = categories["maintenance"]
        ai_driven = categories["strategy"]
        unknown = categories["unknown"]
        newest_reconciled = next((
            row["closed_at"] for row in rows
            if classify_closure(row).category == "administrative"
        ), None)
        newest_reconciled_age = _age_minutes(newest_reconciled) if newest_reconciled else None
        recon_pct = round(reconciled / total * 100, 1) if total else 0
        ai_pct = round(ai_driven / total * 100, 1) if total else 0
        detail = (f"last {total}: administrative={reconciled}({recon_pct}%), "
                  f"maintenance={maintenance}, strategy={ai_driven}({ai_pct}%), "
                  f"unknown={unknown}")
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


def _probe_outcome_history_integrity(report: Report) -> None:
    """Read-only cleanup readiness for classified history and derived artifacts."""
    try:
        from aletheia.maintenance.outcome_integrity import remediate_outcome_history

        plan = remediate_outcome_history(dry_run=True)
        classifications = int(plan["classification"]["planned"])
        archives = int(plan["archive"]["planned"])
        compensation = len(plan["compensation_candidates"])
        detail = (
            f"anchor={plan['clean_window_anchor_id']}; "
            f"classification_pending={classifications}; archive_pending={archives}; "
            f"cash_compensation_candidates={compensation} (owner approval required)"
        )
        if classifications or archives:
            report.add(
                "loop.outcome_history", "info", detail,
                "preview with `aletheia knowledge remediate-outcomes`; apply is opt-in",
            )
        else:
            report.add("loop.outcome_history", "ok", detail)
    except Exception as e:
        report.add("loop.outcome_history", "info", f"probe error: {e}")


def _probe_paper_funding_accounting(report: Report) -> None:
    """Read-only convergence check for local futures funding economics."""
    try:
        from aletheia.maintenance.paper_funding_reconciliation import (
            paper_funding_diagnostic_summary,
        )

        result = paper_funding_diagnostic_summary(limit=100)
        summary = result["summary"]
        identity = int(summary["funding_identity_mismatch"])
        numeric = int(summary["duplicate_settlement_numeric_mismatch"])
        ledgers = int(summary["close_event_projection_mismatch"]) + int(
            summary["paper_cash_ledger_mismatch"]
        )
        recoverable = int(summary["recoverable_null_pnl"]) + int(
            summary["terminal_now_recoverable"]
        )
        missing_hooks = int(summary["missing_outcome_hooks"])
        outcome_projection = int(summary["outcome_projection_mismatch"])
        incomplete = int(summary["still_incomplete"])
        truncated = bool(result.get("truncated"))
        detail = (
            f"version={result['accounting_version']}; scanned={result['scanned']}; "
            f"truncated={str(truncated).lower()}; "
            f"identity_mismatch={identity}; "
            f"numeric_mismatch={numeric}; ledger_mismatch={ledgers}; "
            f"recoverable={recoverable}; incomplete={incomplete}; "
            f"outcome_mismatch={outcome_projection}; missing_hooks={missing_hooks}"
        )
        if numeric or ledgers:
            report.add(
                "accounting.paper_funding", "blocker", detail,
                "run `aletheia data reconcile-paper-funding` and review the dry run",
            )
        elif (
            identity
            or recoverable
            or incomplete
            or outcome_projection
            or missing_hooks
            or truncated
        ):
            report.add(
                "accounting.paper_funding", "warn", detail,
                (
                    "run the complete dry run before trusting the sampled result"
                    if truncated
                    else "review the dry run; apply requires explicit position IDs "
                    "and fingerprint"
                ),
            )
        else:
            report.add("accounting.paper_funding", "ok", detail)
    except Exception as e:
        report.add("accounting.paper_funding", "info", f"probe error: {e}")


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


def _probe_persona_rollout(report: Report) -> None:
    """Show desired Persona flags separately from durable dynamic authority."""
    try:
        from aletheia.config.loader import load_config
        from aletheia.trading.persona_rollout import persona_rollout_status

        cfg = load_config()
        status = persona_rollout_status(cfg)
        if not status.get("desired_progressive_unlock"):
            report.add(
                "loop.persona_rollout",
                "info",
                "progressive unlock disabled; actual authority is static-only",
            )
            return
        evidence = status.get("evidence") or {}
        actual = ",".join(status.get("actual_authorized_params") or []) or "none"
        detail = (
            f"desired_progressive=on; status={status.get('status')}; actual={actual}; "
            f"samples={evidence.get('sample_count', 'not available')}/"
            f"{evidence.get('required_samples', 'not available')}; "
            f"market_days={evidence.get('market_day_count', 'not available')}/"
            f"{evidence.get('required_market_days', 'not available')}; "
            f"verdict={evidence.get('reason', 'not available')}"
        )
        if status.get("status") == "refrozen" or evidence.get("reason") in {
            "invalid_provenance",
            "negative_contribution",
            "degrading_contribution",
            "unstable_contribution",
            "reconcile_thrash_state_unavailable",
        }:
            report.add("loop.persona_rollout", "warn", detail)
        elif status.get("status") in {"unlocked", "completed"}:
            report.add("loop.persona_rollout", "ok", detail)
        else:
            report.add("loop.persona_rollout", "info", detail)
    except Exception as e:
        report.add("loop.persona_rollout", "info", f"probe error: {e}")


def _probe_reconcile_thrash(report: Report) -> None:
    """Report current churn plus the durable circuit-breaker/recovery state."""
    try:
        from aletheia.portfolio.position_reconciler import detect_reconcile_thrash
        from aletheia.portfolio.reconciliation_integrity import (
            clean_observation_gate,
            get_active_reconciliation_integrity_incident,
        )
        from aletheia.learning.control import global_learning_refreeze_reason
        t = detect_reconcile_thrash(window_minutes=30)
        incident = get_active_reconciliation_integrity_incident()
        if not t.get("detector_available", True):
            report.add(
                "loop.reconcile_thrash", "warn",
                "reconciliation integrity detector unavailable",
                "demo reconcile actions must stay suspended until scoped position "
                "history can be read",
            )
            return
        churned = t["churned_symbols"]
        detail = (f"{t['churn_count']} churned in {t['window_minutes']}min "
                  f"(administrative={t['administrative_close_recent']}, "
                  f"reopened={t['reopened_recent']}, sources={t['reopened_by_source']}); "
                  f"{', '.join(churned[:5]) or 'none'}")
        if incident is not None:
            gate = clean_observation_gate(incident)
            freeze = global_learning_refreeze_reason()
            report.add("loop.reconcile_thrash", "warn",
                       f"reconcile integrity incident {incident['id']} active; {detail}; "
                       f"clean gate={gate['observed']}/{gate['required']}; "
                       f"Learning freeze={freeze}",
                       "demo adopt/orphan-close and behavior-changing Learning remain "
                       "frozen; verify account-scoped broker evidence, collect spaced "
                       "clean observations, then resolve explicitly")
        elif t["is_thrashing"]:
            report.add("loop.reconcile_thrash", "warn",
                       f"current churn has no durable incident yet: {detail}",
                       "the next reconcile cycle must persist the incident before any "
                       "demo adopt/orphan-close action")
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
                           f"Trading Decision bias: {detail}",
                           "highly correlated exposure — System Operator may be making one bet "
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


def _probe_startup_behavior_gate(report: Report) -> None:
    """Surface the Startup Behavior Gate's durable blocker as ground truth.

    When the gate is holding the trading loop, this is the single place an
    operator (or agent) can read WHY new AI decisions and trades are suspended
    while data collection keeps running."""
    try:
        from aletheia.config.loader import load_config
        from aletheia.agent_runtime.startup_behavior_gate import read_gate_state

        cfg = load_config()
        if not cfg.health.startup_behavior_gate_enabled:
            report.add("startup_gate.status", "info", "startup behavior gate disabled")
            return
        state = read_gate_state()
        if not state:
            report.add("startup_gate.status", "ok", "checks clear — AI/trading permitted")
            return
        blockers = ", ".join(state.get("blockers") or []) or "unknown"
        detail_map = {c.get("name"): c.get("detail") for c in state.get("checks") or []}
        detail = "; ".join(
            f"{name}: {detail_map.get(name, '')}" for name in (state.get("blockers") or [])
        ) or blockers
        if state.get("paused_by_gate"):
            report.add(
                "startup_gate.status", "blocker",
                f"AI/trading paused; data collection continues. Blockers: {detail}",
                fix_hint="Resolve the blockers (or fix config); the gate re-checks "
                         "periodically and resumes automatically when clear.",
            )
        else:
            reason = state.get("user_block_reason") or "held"
            report.add(
                "startup_gate.status", "warn",
                f"gate held by operator/system ({reason}); not auto-resumed",
                fix_hint="This is a human-owned pause/kill switch — resume from the "
                         "dashboard or clear the kill switch to lift it.",
            )
    except Exception as e:
        report.add("startup_gate.status", "info", f"startup gate probe error: {e}")


def _probe_operator_loop(report: Report) -> None:
    """System Operator governance loop liveness — checks if it has run within expected cadence."""
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
                       "pi_operator_loop runs every 4h plus a deduplicated startup catch-up; check scheduler/gateway")
        else:
            report.add("operator.loop", "ok", f"last assessment {_fmt_age(age)}")
    except Exception as e:
        report.add("operator.loop", "info", f"probe error: {e}")


def _probe_operator_shell(report: Report) -> None:
    """Report command-shell liveness separately from the four-hour assessment loop."""
    try:
        from aletheia.agent_shell.operator_store import get_latest_heartbeat
        from aletheia.config.loader import load_config

        cfg = load_config()
        if not cfg.operator.enabled:
            report.add("operator.shell", "info", "operator command shell disabled in config")
            return

        heartbeat = get_latest_heartbeat("operator")
        if heartbeat is None:
            gateway_ready = any(
                check.name == "process.gateway" and check.severity == "ok"
                for check in report.checks
            )
            report.add(
                "operator.shell",
                "warn" if gateway_ready else "info",
                "no operator command-shell heartbeat yet",
                "check operator-shell dependencies and restart the gateway" if gateway_ready else "",
            )
            return

        status = str(heartbeat.get("status") or "offline")
        age = _age_minutes(heartbeat.get("last_seen_at"))
        stale_after_minutes = float(cfg.operator.heartbeat_stale_after_sec) / 60.0
        stale = age is None or age > stale_after_minutes
        mode = str(heartbeat.get("mode") or "unknown")
        error = " ".join(str(heartbeat.get("last_error") or "").split())[:240]

        if status == "online" and not stale:
            report.add("operator.shell", "ok", f"online ({mode}), heartbeat {_fmt_age(age)}")
            return

        detail = f"{status} ({mode}), heartbeat {_fmt_age(age)}"
        if stale:
            detail += "; heartbeat stale"
        if error:
            detail += f"; last error: {error}"
        lower_error = error.lower()
        if "node" in lower_error and (
            "no such file" in lower_error or "not found" in lower_error
        ):
            hint = "install Node in a stable fnm/Homebrew location, then restart the gateway"
        else:
            from aletheia.config.loader import get_data_dir

            hint = f"inspect {get_data_dir() / 'logs' / 'operator-shell-errors.log'} and restart the gateway"
        report.add("operator.shell", "warn", detail, hint)
    except Exception as e:
        report.add("operator.shell", "info", f"probe error: {e}")


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
    """Report whether legacy implicit-restart hooks still need removal."""
    from aletheia.gateway.git_hooks import git_hooks_status
    st = git_hooks_status()
    if st["error"]:
        report.add("git.hooks", "info", st["error"])  # tarball deploy / not a repo
        return
    installed, foreign = st["installed"], st["foreign"]
    if installed:
        report.add(
            "git.hooks",
            "warn",
            f"legacy auto-restart hooks installed: {', '.join(installed)}",
            fix_hint="Run: aletheia doctor --fix  (removes only Aletheia-managed hooks)",
        )
    if foreign:
        report.add(
            "git.hooks",
            "info",
            f"foreign hook(s) present and left untouched: {', '.join(foreign)}",
        )
    if not installed and not foreign:
        report.add("git.hooks", "ok", "automatic restart hooks disabled; lifecycle is manual")


# --- content-quality probes (Phase 2: AI-native bug discovery) ---------------
# These differ from the operational probes above: instead of "is the loop turning",
# they ask "is the content the AI decision chain produced coherent / non-hallucinated
# / non-garbage" — exactly what a human used to find by eye in the Web UI's Evolve
# module. Each is read-only and follows the probe-honesty rule (owner-acknowledged /
# config-gated states report info with the numbers, not a false warn). See
# docs/archive/legacy/plans/engineering/ai_native_bugfix_path.md §3. They are deliberately NON-overlapping with
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
    """Shadow trades whose counterfactual pnl never lands — edge-lab analysis
    silently loses that sample.

    The unresolved rows split by `outcome_candidate_key`, and the two halves need
    DIFFERENT remedies, so a bare count would send the operator to the wrong tool:

    * key IS NULL — pre-b9ad957 orphans whose parent position carries no
      opportunity_id. `resolve_stale_shadows` (and `scripts/backfill_shadow_trades.py`
      on top of it) resolve these by their own historical horizon.
    * key IS NOT NULL — resolved only by `settle_linked_shadow_outcomes()`, which
      fires when the linked 24h `filter_candidate_outcomes` row settles. Both
      backfill paths SELECT `outcome_candidate_key IS NULL`, so they cannot move a
      single one of these rows; naming them here would be a no-op fix hint hiding
      the real dependency (a filter-outcome settler that cannot keep up).
    """
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        total = db.execute("SELECT COUNT(*) FROM shadow_trades").fetchone()[0]
        if total == 0:
            report.add("content.shadow_trades", "info", "no shadow trades yet")
            return
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        row = db.execute(
            "SELECT COUNT(*) AS stale, "
            "SUM(CASE WHEN outcome_candidate_key IS NULL THEN 1 ELSE 0 END) AS orphaned "
            "FROM shadow_trades "
            "WHERE resolved_at IS NULL AND created_at < ?",
            (cutoff,),
        ).fetchone()
        stale = int(row["stale"] or 0)
        orphaned = int(row["orphaned"] or 0)
    except Exception as e:
        report.add("content.shadow_trades", "info", f"probe error: {e}")
        return
    linked = stale - orphaned
    stale_pct = stale / total if total else 0.0
    if stale == 0:
        report.add("content.shadow_trades", "ok", f"{total} shadow trades, none stale")
        return
    # Point at the remedy that can actually move the dominant half.
    if linked > orphaned:
        hint = ("these carry an outcome_candidate_key, so they resolve only when their "
                "linked 24h filter_candidate_outcomes row settles — check "
                "data.filter_evidence stale_overdue; the backfill script excludes them")
    else:
        hint = ("run scripts/backfill_shadow_trades.py for the orphaned backlog; the "
                "shadow_resolver job catches up new ones (resolve_stale_shadows)")
    split = f"{orphaned} orphaned, {linked} awaiting 24h filter-outcome settlement"
    if stale_pct > 0.25:
        report.add("content.shadow_trades", "warn",
                   f"{stale} shadow trades unresolved >24h ({stale_pct:.0%} of {total}; "
                   f"{split}) — counterfactual pnl never computed",
                   hint)
    else:
        # Bounded residue: symbols that left the universe have no OHLCV to resolve
        # against, so a small unresolved tail is honest, not a systemic failure.
        report.add("content.shadow_trades", "info",
                   f"{stale} shadow trades unresolved >24h ({stale_pct:.0%} of {total}; "
                   f"{split}) — bounded residue (delisted/out-of-universe symbols lack "
                   "OHLCV to resolve)",
                   hint)


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
        # outcome_settler runs every 15 min; allow one cadence plus scheduling slack.
        if (
            oldest_age is not None
            and oldest_age > _OUTCOME_SETTLER_GRACE_MINUTES
        ):
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
                       "System Operator proposes almost exclusively one direction (pre-risk) — check trading_decision "
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



def _external_gateway_runtime_session_id(open_sessions: list[dict]) -> str | None:
    """Match an open DB session to the managed Gateway process tree.

    This is only for out-of-process Doctor calls.  The supervisor PID is the
    durable lifecycle identity; the ASGI child may restart underneath it, so
    both the supervisor and its live descendants are eligible process starts.
    """
    try:
        import psutil

        from aletheia.gateway.pidfile import is_running, read_pid

        pid = read_pid()
        if not pid or not is_running():
            return None
        root = psutil.Process(pid)
        processes = [root, *root.children(recursive=True)]
        process_starts = [process.create_time() for process in processes]
    except Exception:
        return None

    matches: list[tuple[float, float, str]] = []
    for session in open_sessions:
        started = _parse_iso_datetime(session.get("started_at"))
        session_id = session.get("session_id")
        if started is None or not session_id:
            continue
        started_ts = started.timestamp()
        delta = min(abs(started_ts - process_start) for process_start in process_starts)
        if delta <= _RUNTIME_SESSION_PROCESS_MATCH_SECONDS:
            # Prefer the closest process start, then the newest session.
            matches.append((delta, -started_ts, str(session_id)))
    return min(matches)[2] if matches else None


def _probe_runtime_session_provenance(report: Report) -> None:
    """Runtime Session provenance integrity — four distinct states surfaced explicitly.

    1. active durable session  — session row verified in DB; child rows carry a
       traceable runtime_session_id.
    2. active non-durable (degraded) — session exists in-memory but DB row absent or
       unverifiable; child rows stamp NULL to avoid orphan references.
    3. no active session       — process has not called start_runtime_session() yet;
       normal for CLI / test contexts.
    4. stale open sessions     — sessions with ended_at IS NULL from prior process
       crashes that were never cleanly ended.

    Also reports orphan reference counts (child rows referencing a session_id with no
    parent row) so analytics can know attribution coverage rather than silently
    treating orphan ids as valid.
    """
    try:
        from aletheia.agent_runtime.runtime_session import (
            current_session, current_session_durable,
        )
        from aletheia.storage import repositories as repo
        from aletheia.storage.sqlite import get_db

        sess = current_session()
        db = get_db()
        open_sessions = [
            dict(row)
            for row in db.execute(
                "SELECT session_id, runtime_id, boot_commit, behavior_fingerprint, "
                "started_at FROM runtime_sessions WHERE ended_at IS NULL"
            ).fetchall()
        ]
        external_id = (
            _external_gateway_runtime_session_id(open_sessions)
            if sess is None
            else None
        )
        external_session = next(
            (row for row in open_sessions if row["session_id"] == external_id),
            None,
        )

        # --- 1/2/3: current-process session state ---
        if external_session is not None:
            report.add(
                "trust.runtime_session.current",
                "ok",
                f"active durable external Gateway session "
                f"{external_session['session_id'][:16]}… "
                f"(commit={(external_session.get('boot_commit') or '-')[:12]}, "
                f"fingerprint={(external_session.get('behavior_fingerprint') or '-')[:12]}…)",
            )
        elif sess is None:
            report.add(
                "trust.runtime_session.current",
                "info",
                "no active runtime session (process not started or CLI context)",
            )
        elif current_session_durable():
            report.add(
                "trust.runtime_session.current",
                "ok",
                f"active durable session {sess['session_id'][:16]}… "
                f"(commit={( sess.get('boot_commit') or '-')[:12]}, "
                f"fingerprint={( sess.get('behavior_fingerprint') or '-')[:12]}…)",
            )
        else:
            report.add(
                "trust.runtime_session.current",
                "warn",
                f"active session {sess['session_id'][:16]}… is NON-DURABLE — "
                "DB row absent or fingerprint mismatch; child rows stamped with NULL "
                "runtime_session_id (provenance degraded, trading continues)",
                fix_hint="Call retry_session_durability() or restart the gateway to "
                         "attempt re-registration.",
            )

        # --- 4: stale open sessions from prior crashes ---
        try:
            current_id = sess["session_id"] if sess else external_id
            stale = [
                s for s in open_sessions
                if s["session_id"] != current_id
            ]
            if stale:
                ids = ", ".join(s["session_id"][:16] + "…" for s in stale[:3])
                report.add(
                    "trust.runtime_session.stale_open",
                    "warn",
                    f"{len(stale)} stale open session(s) from prior crash(es): {ids}",
                    fix_hint="These sessions were never cleanly ended — likely a crash. "
                             "They do not affect current trading but inflate the open-session "
                             "count. end_runtime_session() was not called on gateway shutdown.",
                )
            else:
                report.add(
                    "trust.runtime_session.stale_open",
                    "ok",
                    "no stale open sessions from prior crashes",
                )
        except Exception as e:
            report.add("trust.runtime_session.stale_open", "info", f"stale-session probe error: {e}")

        # --- Orphan scan: child rows with no parent session ---
        try:
            scan = repo.scan_orphan_session_ids()
            if "error" in scan:
                report.add("trust.runtime_session.orphans", "info",
                           f"orphan scan failed: {scan['error']}")
            elif scan["total_orphan_refs"] == 0:
                report.add("trust.runtime_session.orphans", "ok",
                           "no orphan runtime_session_id references found")
            else:
                table_summary = "; ".join(
                    f"{t}:{sum(v for v in cols.values())}"
                    for t, cols in scan["by_table"].items()
                )
                n_ids = len(scan["orphan_session_ids"])
                report.add(
                    "trust.runtime_session.orphans",
                    "warn",
                    f"{scan['total_orphan_refs']} orphan reference(s) across "
                    f"{n_ids} unknown session id(s): {table_summary}",
                    fix_hint="These child rows advertise a runtime_session_id that has no "
                             "parent in runtime_sessions. They are not fabricated — the "
                             "session row was simply never written. Use "
                             "scan_orphan_session_ids() for a full breakdown.",
                )
        except Exception as e:
            report.add("trust.runtime_session.orphans", "info", f"orphan scan error: {e}")

    except Exception as e:
        report.add("trust.runtime_session.current", "info", f"probe error: {e}")

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
    """Check FTS5 index coverage vs *rebuildable* eligible knowledge items.

    Coverage denominator must be items whose body is still recoverable — either
    the DB ``content`` column is populated, or the ``file_path`` still resolves
    to a readable Markdown file via ``retrieval._read_body`` (which handles the
    Windows/POSIX legacy-path migration). Rows whose body is unreachable are
    audit stubs, not learnable material, and are reported separately as
    ``knowledge.unindexable_high_confidence`` so users don't misread lost bodies
    as "the reindex job hasn't finished".
    """
    from aletheia.storage.sqlite import get_db
    db = get_db()
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_fts'"
    ).fetchone()
    if not row:
        report.add("fts.coverage", "warn", "knowledge_fts table missing — experience loop disabled",
                   "run: uv run aletheia knowledge reindex")
        return
    try:
        from aletheia.knowledge.retrieval import _read_body
    except Exception:
        _read_body = None  # type: ignore[assignment]

    active_rows = db.execute(
        "SELECT file_path, content FROM knowledge_items "
        "WHERE confidence='high_confidence' AND (status='active' OR status IS NULL)"
    ).fetchall()
    total_active = len(active_rows)
    rebuildable = 0
    unindexable = 0
    for r in active_rows:
        has_db_body = bool((r["content"] or "").strip())
        has_disk_body = False
        if not has_db_body and _read_body is not None:
            try:
                has_disk_body = bool((_read_body(r["file_path"]) or "").strip())
            except Exception:
                has_disk_body = False
        if has_db_body or has_disk_body:
            rebuildable += 1
        else:
            unindexable += 1

    indexed = db.execute("SELECT COUNT(*) FROM knowledge_fts").fetchone()[0]

    # Separate signal for the unrecoverable stubs so recovery/downgrade work
    # is visible without contaminating coverage severity.
    if unindexable > 0:
        report.add(
            "knowledge.unindexable_high_confidence",
            "warn",
            f"{unindexable} of {total_active} high-confidence rows have no reachable body "
            f"(DB content empty and file_path unreadable)",
            "run: uv run aletheia knowledge scan-recovery-sources; "
            "then: uv run aletheia knowledge downgrade-unindexable-high-confidence",
        )
    else:
        report.add(
            "knowledge.unindexable_high_confidence",
            "ok",
            "all high-confidence bodies reachable",
        )

    if total_active == 0:
        # No high-confidence rows exist yet. Legacy behavior: green.
        report.add("fts.coverage", "ok", "no eligible items yet")
        return
    if rebuildable == 0:
        report.add(
            "fts.coverage",
            "warn",
            f"{indexed}/{rebuildable} rebuildable indexed (no reachable bodies)",
            "recover bodies before reindex — see knowledge.unindexable_high_confidence",
        )
        return

    pct = indexed / rebuildable
    detail = f"{indexed}/{rebuildable} rebuildable indexed ({pct:.0%})"
    if pct >= 0.8:
        report.add("fts.coverage", "ok", detail)
    elif pct >= 0.5:
        report.add("fts.coverage", "warn", detail, "restart gateway to trigger reindex")
    else:
        report.add(
            "fts.coverage", "blocker", detail,
            "experience loop severely degraded — restart gateway or run: uv run aletheia knowledge reindex",
        )


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
        gap_identities: list[tuple[str, str, str]] = []
        for m in members:
            sym = m.get("symbol", "")
            if not sym:
                continue
            from aletheia.storage.repositories import get_universe_asset
            asset = get_universe_asset(m.get("asset_id")) or {}
            venue = asset.get("venue", "")
            market_type = m.get("market_type") or asset.get("market_type", "")
            bars = db.execute(
                "SELECT COUNT(*) FROM market_ohlcv "
                "WHERE exchange=? AND symbol=? AND market_type=? AND timeframe='1h'",
                (venue, sym, market_type),
            ).fetchone()[0]
            if bars < min_bars:
                gaps.append(f"{venue}:{sym}:{market_type}({bars})")
                gap_identities.append((venue, sym, market_type))

        symbols_covered = db.execute(
            "SELECT COUNT(DISTINCT exchange || ':' || symbol || ':' || market_type) "
            "FROM market_ohlcv WHERE timeframe='1h'"
        ).fetchone()[0]

        if gaps:
            catchup_counts: dict[str, int] = {}
            persistent_failures = 0
            for venue, sym, market_type in gap_identities:
                state = db.execute(
                    "SELECT status,consecutive_failures FROM ohlcv_backfill_state "
                    "WHERE exchange=? AND symbol=? AND market_type=? AND timeframe='1h'",
                    (venue, sym, market_type),
                ).fetchone()
                status = state["status"] if state else "awaiting"
                catchup_counts[status] = catchup_counts.get(status, 0) + 1
                if state and int(state["consecutive_failures"] or 0) >= 3:
                    persistent_failures += 1
            auto_enabled = bool(
                cfg.historical_data.enabled
                and cfg.historical_data.active_universe_backfill_enabled
            )
            detail = (
                f"{len(gaps)}/{len(members)} active members below {min_bars} bars: "
                + ", ".join(gaps[:5])
                + ("…" if len(gaps) > 5 else "")
                + f"; catch-up={catchup_counts}"
            )
            if persistent_failures:
                detail += f", persistent_failures={persistent_failures}"
            action = (
                "automatic active-Universe catch-up is enabled; check gateway logs if "
                "persistent_failures remains non-zero"
                if auto_enabled
                else "enable Active Universe catch-up in Settings or run: "
                "uv run aletheia data backfill"
            )
            report.add(
                "data.ohlcv", "warn",
                detail,
                action,
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
                      SUM(CASE WHEN net_return_pct > COALESCE(min_tradable_edge_pct, 0)
                               THEN 1 ELSE 0 END),
                      AVG(net_return_pct)
               FROM universe_forward_outcomes
               WHERE status = 'settled'
                 AND horizon = '24h'
                 AND settled_at >= ?
                 AND net_return_pct IS NOT NULL
                 AND ABS(net_return_pct) <= 100""",
            (cutoff,),
        ).fetchone()

        total, wins, avg_ret = row if row else (0, None, None)

        if not total:
            report.add("data.signal", "info",
                       "insufficient cost-aware net outcomes in last 7d — signal effectiveness unknown")
            return

        win_rate = (wins or 0) / total * 100
        avg_str = f"{avg_ret:.3f}%" if avg_ret is not None else "n/a"

        if win_rate < 40:
            report.add(
                "data.signal", "warn",
                f"24h net signal win_rate {win_rate:.1f}% ({total} settled, avg_net={avg_str}) — "
                "aggregate observation requires cohort diagnosis",
                "inspect outcome coverage, candidate-skip evidence, and cohort splits; do not auto-tune PI",
            )
        elif win_rate >= 55:
            report.add("data.signal", "ok",
                       f"24h net signal win_rate {win_rate:.1f}% ({total} settled, avg_net={avg_str})")
        else:
            report.add("data.signal", "info",
                       f"24h net signal win_rate {win_rate:.1f}% ({total} settled, avg_net={avg_str}) — near-random")
    except Exception:
        pass


def _probe_outcome_economics(report: Report) -> None:
    """Expose whether post-migration outcomes have complete net economics."""
    try:
        from aletheia.storage.sqlite import get_db

        db = get_db()
        row = db.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN net_return_pct IS NOT NULL THEN 1 ELSE 0 END) AS complete, "
            "SUM(CASE WHEN net_return_pct IS NULL "
            "  AND market_type IN ('perp','futures','swap','usdt_futures') "
            "  AND funding_rate_at_entry IS NULL THEN 1 ELSE 0 END) AS missing_funding, "
            "SUM(CASE WHEN net_return_pct IS NULL "
            "  AND settlement_price_source = 'late_ticker' THEN 1 ELSE 0 END) AS late_price "
            "FROM universe_forward_outcomes "
            "WHERE status='settled' AND cost_model_version='v1'"
        ).fetchone()
        total = int(row["total"] or 0) if row else 0
        complete = int(row["complete"] or 0) if row else 0
        missing_funding = int(row["missing_funding"] or 0) if row else 0
        late_price = int(row["late_price"] or 0) if row else 0
        if total == 0:
            report.add(
                "data.outcome_economics",
                "info",
                "cost-aware outcome model is warming up; no settled v1 rows yet",
            )
            return
        ratio = complete / total
        detail = (
            f"cost-aware outcomes complete={complete}/{total} ({ratio:.0%}); "
            f"missing_funding={missing_funding}, late_price={late_price}"
        )
        if total >= 10 and ratio < 0.5:
            report.add(
                "data.outcome_economics",
                "warn",
                detail,
                "inspect derivative funding coverage and outcome settlement lag",
            )
        elif complete == total:
            report.add("data.outcome_economics", "ok", detail)
        else:
            report.add("data.outcome_economics", "info", detail)
    except Exception:
        pass


def _probe_filter_evidence(report: Report) -> None:
    """Report whether filter pass/reject facts and their net outcomes are connected."""
    try:
        from aletheia.storage.sqlite import get_db

        db = get_db()
        decisions = db.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN decision='passed' THEN 1 ELSE 0 END) AS passed, "
            "SUM(CASE WHEN decision='rejected' THEN 1 ELSE 0 END) AS rejected, "
            "COUNT(DISTINCT CASE WHEN outcome_candidate_key IS NOT NULL "
            "  THEN outcome_candidate_key END) AS outcome_candidates "
            "FROM filter_decisions"
        ).fetchone()
        batches = db.execute(
            "SELECT COUNT(*) AS total, "
            "COALESCE(SUM(candidate_count), 0) AS candidates, "
            "COUNT(DISTINCT filter_name) AS filters, "
            "SUM(CASE WHEN candidate_count != "
            "  passed_count + rejected_count + not_applicable_count + error_count "
            "  THEN 1 ELSE 0 END) AS malformed "
            "FROM filter_decision_batches"
        ).fetchone()
        total = int(decisions["total"] or 0) if decisions else 0
        batch_total = int(batches["total"] or 0) if batches else 0
        batch_candidates = int(batches["candidates"] or 0) if batches else 0
        batch_filters = int(batches["filters"] or 0) if batches else 0
        malformed_batches = int(batches["malformed"] or 0) if batches else 0
        if total == 0:
            if batch_total:
                detail = (
                    f"decisions=0; batches={batch_total}, "
                    f"batch_candidates={batch_candidates}, "
                    f"batch_filters={batch_filters}, malformed={malformed_batches}; "
                    "directional outcome evidence is warming up"
                )
                if malformed_batches:
                    report.add(
                        "data.filter_evidence",
                        "warn",
                        detail,
                        "inspect incomplete directionless filter batch writes",
                    )
                else:
                    report.add("data.filter_evidence", "info", detail)
                return
            report.add(
                "data.filter_evidence",
                "info",
                "filter evidence is warming up; no pass/reject facts recorded yet",
            )
            return

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        stale_before = (
            now - timedelta(minutes=_OUTCOME_SETTLER_GRACE_MINUTES)
        ).isoformat()
        outcomes = db.execute(
            "SELECT COUNT(DISTINCT outcome_candidate_key) AS registered, "
            "SUM(CASE WHEN status='settled' THEN 1 ELSE 0 END) AS settled, "
            "SUM(CASE WHEN status='settled' AND net_return_pct IS NOT NULL "
            "  THEN 1 ELSE 0 END) AS net_complete, "
            "SUM(CASE WHEN status='pending' AND datetime(due_at) < datetime(?) "
            "  THEN 1 ELSE 0 END) AS overdue, "
            "SUM(CASE WHEN status='pending' AND datetime(due_at) < datetime(?) "
            "  THEN 1 ELSE 0 END) AS stale_overdue "
            "FROM filter_candidate_outcomes",
            (now_iso, stale_before),
        ).fetchone()
        outcome_candidates = int(decisions["outcome_candidates"] or 0)
        registered = int(outcomes["registered"] or 0) if outcomes else 0
        settled = int(outcomes["settled"] or 0) if outcomes else 0
        net_complete = int(outcomes["net_complete"] or 0) if outcomes else 0
        overdue = int(outcomes["overdue"] or 0) if outcomes else 0
        stale_overdue = int(outcomes["stale_overdue"] or 0) if outcomes else 0
        awaiting_cadence = max(0, overdue - stale_overdue)
        registration = registered / outcome_candidates if outcome_candidates else 1.0
        # Rows already written off without any observation behind them, across all
        # three settlement queues. Every other number in this probe describes work
        # that is still pending and will drain on its own; this one describes
        # evidence that is gone and stays gone until someone reclaims it, so it is
        # the one that has to be visible.
        from aletheia.maintenance.settlement_requeue import (
            count_unobserved_failed_outcomes,
        )

        unobserved_failed = sum(count_unobserved_failed_outcomes().values())
        detail = (
            f"decisions={total} (pass={int(decisions['passed'] or 0)}, "
            f"reject={int(decisions['rejected'] or 0)}); "
            f"batches={batch_total}, batch_candidates={batch_candidates}, "
            f"batch_filters={batch_filters}, malformed={malformed_batches}; "
            f"outcome_candidates={registered}/{outcome_candidates}; "
            f"settled={settled}, net_complete={net_complete}, overdue={overdue}, "
            f"awaiting_cadence={awaiting_cadence}, stale_overdue={stale_overdue}, "
            f"unobserved_failed={unobserved_failed}"
        )
        if unobserved_failed:
            report.add(
                "data.filter_evidence",
                "warn",
                detail,
                f"{unobserved_failed} settlement rows are 'failed' with no price and no "
                "price source — they were written off before any observation was taken, "
                "and nothing revisits them. Reclaim with "
                "`aletheia data requeue-unobserved-settlements --apply` "
                "(dry-run without --apply)",
            )
        elif malformed_batches:
            report.add(
                "data.filter_evidence",
                "warn",
                detail,
                "inspect incomplete directionless filter batch writes",
            )
        elif registration < 0.95:
            report.add(
                "data.filter_evidence",
                "warn",
                detail,
                "inspect filter evidence write failures; eligible decisions are missing outcomes",
            )
        elif stale_overdue > 50:
            report.add(
                "data.filter_evidence",
                "warn",
                detail,
                "inspect outcome settler logs and exact-instrument market coverage; "
                "recently due rows inside the cadence are excluded from this warning",
            )
        else:
            report.add("data.filter_evidence", "ok", detail)
    except Exception:
        pass


def _probe_serenity_pipeline(report: Report) -> None:
    """Serenity freshness is based on successful completion, not mere attempts."""
    try:
        from aletheia.config.loader import load_config as _lcfg
        if not getattr(_lcfg().serenity, "enabled", True):
            report.add("data.serenity_pipeline", "info", "Serenity research pipeline disabled in settings")
            return
    except Exception:
        pass
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        successful = db.execute(
            "SELECT status, COALESCE(finished_at, created_at) newest "
            "FROM serenity_research_runs "
            "WHERE status IN ('completed', 'completed_empty') "
            "ORDER BY COALESCE(finished_at, created_at) DESC LIMIT 1"
        ).fetchone()
        attempted = db.execute(
            "SELECT status, error_type, COALESCE(finished_at, created_at) attempted_at "
            "FROM serenity_research_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        active = db.execute(
            "SELECT COUNT(*) FROM serenity_opportunities WHERE status = 'active'"
        ).fetchone()[0]
    except Exception as e:
        report.add("data.serenity_pipeline", "info", f"probe error: {e}")
        return
    if not successful:
        attempt_detail = ""
        if attempted:
            attempt_detail = (
                f"; latest attempt={attempted['status']}"
                + (f" ({attempted['error_type']})" if attempted["error_type"] else "")
            )
        report.add("data.serenity_pipeline", "warn",
                   f"no successful Serenity research run{attempt_detail}",
                   "check dreaming_daily and serenity_startup_catchup scheduler jobs")
        return
    age = _age_minutes(successful["newest"])
    detail = f"last successful run {_fmt_age(age)}, {active} active opportunities"
    if attempted and attempted["status"] not in ("completed", "completed_empty"):
        detail += f"; latest attempt={attempted['status']}"
        if attempted["error_type"]:
            detail += f" ({attempted['error_type']})"
    if age is not None and age > 1560:  # 26h — serenity runs once per 24h; 12h fired daily false-positives
        report.add("data.serenity_pipeline", "warn",
                   f"Serenity research stale: {detail}",
                   "dreaming_daily missed 02:00 CST and startup catch-up has not completed")
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


def _classify_db_capacity(
    *,
    db_mb: float,
    free_mb: float,
    reclaimable_mb: float,
    capacity_mb: float | None = None,
    auto_vacuum: int | None = None,
    wal_mb: float = 0.0,
) -> tuple[str, str, str | None]:
    """Classify storage pressure separately from normal retained database growth."""
    minimum_free_mb = max(500.0, db_mb * 1.2)
    if free_mb < minimum_free_mb:
        return (
            "warn",
            "low disk space",
            "free disk space before SQLite writes fail; database compaction also needs temporary free space",
        )

    if wal_mb > 64.0:
        return (
            "warn",
            "WAL above retained limit",
            "inspect `logs/database_capacity.jsonl` for repeated checkpoint backlog; "
            "a long-lived reader or external process may be pinning an old snapshot",
        )

    if capacity_mb and capacity_mb > 0 and db_mb >= capacity_mb:
        return (
            "warn",
            "capacity budget exceeded",
            "run `uv run aletheia data capacity --deep`; compact offline only when reusable space is material",
        )

    if auto_vacuum == 0 and reclaimable_mb >= 20:
        return (
            "info",
            "incremental reclaim disabled",
            "new databases enable incremental vacuum automatically; for this database plan `uv run aletheia data compact --apply` with the Gateway stopped",
        )

    reclaim_ratio = reclaimable_mb / db_mb if db_mb > 0 else 0.0
    if reclaimable_mb >= 100 and reclaim_ratio >= 0.20:
        return (
            "info",
            "maintenance candidate",
            f"{reclaim_ratio:.0%} of the database is reusable freelist space; plan offline compaction only if disk recovery is needed",
        )
    if db_mb > 500:
        return "info", "large but compact", None
    if db_mb > 200:
        return "info", "growing", None
    return "ok", "normal", None


def _probe_infra_db_size(report: Report) -> None:
    """Report actual disk pressure and freelist waste, not size alone."""
    try:
        import shutil
        from aletheia.storage.sqlite import db_path, get_db
        path = db_path()
        database_bytes = path.stat().st_size if path.exists() else 0
        wal_path = path.with_name(path.name + "-wal")
        wal_bytes = wal_path.stat().st_size if wal_path.exists() else 0
        db_mb = (database_bytes + wal_bytes) / (1024 * 1024)
        du = shutil.disk_usage(str(path.parent))
        free_mb = du.free / (1024 * 1024)
        db = get_db()
        page_size = int(db.execute("PRAGMA page_size").fetchone()[0])
        freelist_count = int(db.execute("PRAGMA freelist_count").fetchone()[0])
        auto_vacuum = int(db.execute("PRAGMA auto_vacuum").fetchone()[0])
        reclaimable_mb = page_size * freelist_count / (1024 * 1024)
        wal_mb = wal_bytes / (1024 * 1024)
        from aletheia.config.loader import load_config

        capacity_mb = float(
            load_config(persist_migrations=False).retention.app_db_capacity_mb
        )
    except Exception as e:
        report.add("infra.db_size", "info", f"probe error: {e}")
        return
    severity, label, hint = _classify_db_capacity(
        db_mb=db_mb,
        free_mb=free_mb,
        reclaimable_mb=reclaimable_mb,
        capacity_mb=capacity_mb,
        auto_vacuum=auto_vacuum,
        wal_mb=wal_mb,
    )
    detail = (
        f"{label}: app.db + WAL {db_mb:.0f} MB, reusable {reclaimable_mb:.1f} MB, "
        f"disk free {free_mb:.0f} MB"
    )
    report.add("infra.db_size", severity, detail, hint)


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


def build_report(*, probe_llm: bool = False) -> Report:
    """Probe every dimension of ground truth. Pure read — changes nothing.

    `probe_llm` adds a REAL inference call to verify the model endpoint instead of
    reading past call outcomes. Off by default: it costs a live completion and up
    to ~20s per provider, and this function is also on the UI and operator-loop
    paths. Only `aletheia doctor --probe-llm` turns it on.
    """
    import functools

    report = Report(generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    for probe in (
        _probe_code_sync,
        _probe_legacy_launchd,
        _probe_gateway_service,
        _probe_process,
        _probe_windows_lan_firewall,
        _probe_gateway_restart_history,
        _probe_config_gates,
        _probe_config_consistency,
        _probe_credentials,
        functools.partial(_probe_llm_inference, active_probe=probe_llm),
        _probe_market_data,
        _probe_market_data_venue_health,
        _probe_collector_health,
        _probe_universe_market_coverage,
        _probe_pending_candidate_market_coverage,
        _probe_orderbook,
        _probe_perp_structure,
        _probe_macro_signals,
        _probe_coinalyze,
        _probe_prediction_markets,
        _probe_serenity_pipeline,
        _probe_volume_ranking,
        _probe_data_quality,
        _probe_ohlcv_coverage,
        _probe_outcome_economics,
        _probe_filter_evidence,
        _probe_signal_effectiveness,
        _probe_trading_loop,
        _probe_profit_loop,
        _probe_skip_halt,
        _probe_round1,
        _probe_knowledge_flood,
        _probe_learning_retrieval,
        _probe_outcome_history_integrity,
        _probe_paper_funding_accounting,
        _probe_reconciled_ratio,
        _probe_reconcile_thrash,
        _probe_sizing_policy,
        _probe_persona_rollout,
        _probe_concentration,
        # content-quality (Phase 2): "is the AI's output garbage", not "is the loop alive"
        *_CONTENT_PROBES,
        _probe_health,
        _probe_startup_behavior_gate,
        _probe_operator_shell,
        _probe_operator_loop,
        _probe_webui_static,
        _probe_git_hooks,
        _probe_network,
        _probe_infra_db_size,
        _probe_audit_chain,
        _probe_runtime_session_provenance,
        _probe_fts_coverage,
    ):
        try:
            probe(report)
        except Exception as e:  # a broken probe must never sink the whole report
            # functools.partial has no __name__; fall back to the wrapped probe.
            name = getattr(probe, "__name__", None) or getattr(
                getattr(probe, "func", None), "__name__", "probe")
            report.add(name, "info", f"probe error: {e}")
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

    # Remove legacy implicit-restart hooks before the running-check early return.
    from aletheia.gateway.git_hooks import remove_managed_git_hooks
    actions.extend(remove_managed_git_hooks())

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
