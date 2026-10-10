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

`build_report()` is a pure read when called without `probe_llm`; the explicit
`probe_llm` option performs and records a real diagnostic inference. It is safe
to run the passive report anywhere, anytime, by you or an agent.
`apply_fixes()` is the opt-in conservative repair
(clear orphan processes, start the gateway); it never clears the kill switch and
never touches trading state — those stay human decisions per the safety rules.
"""

from __future__ import annotations

import logging
import os
import platform
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import typer

logger = logging.getLogger("aletheia.diagnostics.doctor")

Severity = Literal["ok", "info", "warn", "blocker"]

_OUTCOME_SETTLER_GRACE_MINUTES = 20

@contextmanager
def _read_only_diagnostics():
    """Fence passive Doctor reports against config and SQLite writes."""
    from aletheia.config.loader import read_only_config
    from aletheia.storage.sqlite import readonly_db

    with read_only_config(), readonly_db(allow_missing=True):
        yield


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

    def add_probe_error(self, name: str, exc: BaseException, prefix: str = "probe error") -> None:
        """Record a probe that raised. A probe that cannot run cannot vouch for health,
        so it is a warn (healthy=False). Absence is not breakage and stays info: no DB
        file or no table yet on a fresh data dir, or an optional extension not installed."""
        missing = (
            isinstance(exc, FileNotFoundError)
            or (isinstance(exc, sqlite3.OperationalError) and "no such table" in str(exc))
            or (isinstance(exc, ModuleNotFoundError) and (exc.name or "").startswith("extensions."))
        )
        self.add(name, "info" if missing else "warn", f"{prefix}: {exc}")

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


@_read_only_diagnostics()
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
        "candidate_planning": _safe_candidate_planning_diagnostic(),  # noqa: F405
    }


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
            logger.debug("doctor: extra error-log tails unavailable", exc_info=True)
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
        from aletheia.agent_runtime.run_view import (
            FAILED_AGENT_RUN_STATUSES,
            SUCCESS_AGENT_RUN_STATUSES,
            finished_at,
            is_failed_agent_run,
            summarize_run_errors,
        )
        from aletheia.storage.sqlite import get_db
        db = get_db()
        failed_statuses = tuple(sorted(FAILED_AGENT_RUN_STATUSES))
        success_statuses = tuple(sorted(SUCCESS_AGENT_RUN_STATUSES))
        params = (*failed_statuses, *success_statuses, limit)
        failed_marks = ",".join("?" for _ in failed_statuses)
        success_marks = ",".join("?" for _ in success_statuses)
        rows = db.execute(
            f"""SELECT id, agent_name, status, started_at,
                       ended_at AS finished_at, errors AS error
                FROM agent_runs
                WHERE status IN ({failed_marks})
                   OR (
                       status IN ({success_marks})
                       AND COALESCE(NULLIF(errors, ''), '[]') <> '[]'
                   )
                ORDER BY COALESCE(ended_at, started_at) DESC
                LIMIT ?""",
            params,
        ).fetchall()
        failures: list[dict] = []
        for row in rows:
            summary = summarize_run_errors(row["error"])
            if not is_failed_agent_run(row["status"], row["error"]):
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
        logger.warning("doctor: log redaction unavailable — values may be unredacted", exc_info=True)
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


# Shared types and helpers must exist before probes import them.
from aletheia.diagnostics.probes.content import *  # noqa: F403
from aletheia.diagnostics.probes.content import _CONTENT_PROBES
from aletheia.diagnostics.probes.storage_trust import *  # noqa: F403
from aletheia.diagnostics.probes.environment import *  # noqa: F403
from aletheia.diagnostics.probes.data_quality import *  # noqa: F403
from aletheia.diagnostics.probes.trading import *  # noqa: F403


@_read_only_diagnostics()
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
            report.add_probe_error(probe.__name__, e)
    return report


@_read_only_diagnostics()
def _build_report(*, probe_requested: bool = False) -> Report:
    """Probe every dimension of ground truth without mutating runtime state.

    The explicit active LLM probe is intentionally kept outside this fence by
    :func:`build_report`, because its provider attempt and logical call are
    durable diagnostic evidence.
    """

    import functools

    report = Report(generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    for probe in (
        _probe_code_sync,  # noqa: F405
        _probe_legacy_launchd,  # noqa: F405
        _probe_gateway_service,  # noqa: F405
        _probe_process,  # noqa: F405
        _probe_windows_lan_firewall,  # noqa: F405
        _probe_gateway_restart_history,  # noqa: F405
        _probe_config_gates,  # noqa: F405
        _probe_config_consistency,  # noqa: F405
        _probe_credentials,  # noqa: F405
        functools.partial(
            _probe_llm_inference,  # noqa: F405
            active_probe=False,
            probe_requested=probe_requested,
        ),
        _probe_market_data,  # noqa: F405
        _probe_market_data_venue_health,  # noqa: F405
        _probe_collector_health,  # noqa: F405
        _probe_universe_market_coverage,  # noqa: F405
        _probe_candidate_planning,  # noqa: F405
        _probe_orderbook,  # noqa: F405
        _probe_perp_structure,  # noqa: F405
        _probe_macro_signals,  # noqa: F405
        _probe_coinalyze,  # noqa: F405
        _probe_serenity_pipeline,  # noqa: F405
        _probe_volume_ranking,  # noqa: F405
        _probe_data_quality,  # noqa: F405
        _probe_ohlcv_coverage,  # noqa: F405
        _probe_outcome_economics,  # noqa: F405
        _probe_filter_evidence,  # noqa: F405
        _probe_signal_effectiveness,  # noqa: F405
        _probe_trading_loop,  # noqa: F405
        _probe_skip_halt,  # noqa: F405
        _probe_round1,  # noqa: F405
        _probe_knowledge_flood,  # noqa: F405
        _probe_learning_retrieval,  # noqa: F405
        _probe_outcome_history_integrity,  # noqa: F405
        _probe_reconciled_ratio,  # noqa: F405
        _probe_reconcile_thrash,  # noqa: F405
        _probe_execution_liveness,  # noqa: F405
        _probe_sizing_policy,  # noqa: F405
        _probe_persona_rollout,  # noqa: F405
        _probe_concentration,  # noqa: F405
        # content-quality (Phase 2): "is the AI's output garbage", not "is the loop alive"
        *_CONTENT_PROBES,
        _probe_health,  # noqa: F405
        _probe_startup_behavior_gate,  # noqa: F405
        _probe_operator_shell,  # noqa: F405
        _probe_pi_daemon,  # noqa: F405
        _probe_webui_static,  # noqa: F405
        _probe_git_hooks,  # noqa: F405
        _probe_network,  # noqa: F405
        _probe_infra_db_size,  # noqa: F405
        _probe_infra_schema_current,  # noqa: F405
        _probe_audit_chain,  # noqa: F405
        _probe_funding_audit_mirror,  # noqa: F405
        _probe_runtime_session_provenance,  # noqa: F405
        _probe_fts_coverage,  # noqa: F405
    ):
        try:
            probe(report)
        except Exception as e:  # a broken probe must never sink the whole report
            # functools.partial has no __name__; fall back to the wrapped probe.
            name = getattr(probe, "__name__", None) or getattr(
                getattr(probe, "func", None), "__name__", "probe")
            report.add_probe_error(name, e)
    return report


def build_report(*, probe_llm: bool = False) -> Report:
    """Probe every dimension of ground truth.

    `probe_llm` adds a REAL inference call to verify the model endpoint instead of
    reading past call outcomes. Off by default: it costs a live completion,
    records the diagnostic logical/physical call, and can take up to ~20s per
    provider. Passive report reads remain fenced; only `aletheia doctor
    --probe-llm` turns on this deliberate persisted diagnostic action.
    """
    report = _build_report(probe_requested=probe_llm)
    if probe_llm:
        _probe_llm_inference_active(report)  # noqa: F405
    return report


# --- conservative auto-repair (opt-in, --fix) ---

def _gateway_start_exit_reason(exit_code: int, lifecycle_output: str) -> str:
    text = lifecycle_output.lower()
    if exit_code == 0 and "already running" in text:
        return "already_running"
    if "preflight" in text:
        return "preflight_failed"
    if "foreign process" in text or "port" in text and "in use" in text:
        return "foreign_port_holder"
    if "readiness" in text or "did not come up" in text:
        return "readiness_failed"
    if "machine service failed" in text:
        return "machine_service_start_failed"
    if "service manager failed" in text:
        return "service_manager_start_failed"
    return "gateway_start_exited"


def apply_fixes() -> list[dict[str, object]]:
    """Deterministic, reversible, non-trading repairs only.

    Per the project safety rules, this NEVER clears the kill switch and NEVER
    touches trading state — it only fixes infrastructure: clears orphan gateway
    processes / stale pidfiles and starts the gateway if it is down. Returns
    structured, secret-safe actions for Doctor to render.
    """
    actions: list[dict[str, object]] = []
    # Remove legacy implicit-restart hooks before the running-check early return.
    from aletheia.gateway.git_hooks import remove_managed_git_hooks
    from aletheia.gateway.lifecycle import do_start
    from aletheia.gateway.pidfile import is_running
    for action in remove_managed_git_hooks():
        actions.append({"action": "remove_managed_git_hook", "status": "ok", "detail": action})

    if is_running():
        actions.append({"action": "gateway_start", "status": "skipped", "reason": "already_running"})
        return actions

    # do_start already self-heals: it sweeps orphan/ghost-socket processes holding
    # the port before spawning, and refuses if a foreign process holds it. We reuse
    # that rather than duplicating the cleanup logic.
    import contextlib
    import io

    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            started = do_start(no_browser=True, foreground=False)
        actions.append({
            "action": "gateway_start",
            "status": "ok" if started is not False else "pending",
            "reason": "started" if started is not False else "start_requested_readiness_unconfirmed",
        })
    except typer.Exit as exc:
        exit_code = int(getattr(exc, "exit_code", 1) or 0)
        lifecycle_output = stdout.getvalue() + stderr.getvalue()
        actions.append({
            "action": "gateway_start",
            "status": "skipped" if exit_code == 0 else "failed",
            "reason": _gateway_start_exit_reason(exit_code, lifecycle_output),
            "exit_code": exit_code,
        })
    except Exception as e:
        actions.append({
            "action": "gateway_start",
            "status": "failed",
            "reason": "unexpected_error",
            "error_type": type(e).__name__,
        })
    return actions
