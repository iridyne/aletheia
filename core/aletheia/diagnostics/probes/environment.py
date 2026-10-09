"""Environment probes for doctor reports."""

from __future__ import annotations

import os
import platform
from datetime import datetime, timedelta, timezone

__all__ = [
    "_GATEWAY_RESTART_EVENT_TYPES",
    "_LLM_DEGRADED_SUCCESS_RATE",
    "_LLM_EVIDENCE_MIN_SAMPLE",
    "_LLM_EVIDENCE_WINDOW_MINUTES",
    "_egress_ip",
    "_is_local_model_endpoint",
    "_llm_call_evidence",
    "_probe_code_sync",
    "_probe_config_consistency",
    "_probe_config_gates",
    "_probe_credentials",
    "_probe_frontdoor_process",
    "_probe_gateway_restart_history",
    "_probe_gateway_service",
    "_probe_git_hooks",
    "_probe_legacy_launchd",
    "_probe_llm_inference",
    "_probe_llm_inference_active",
    "_probe_network",
    "_probe_process",
    "_probe_webui_static",
    "_probe_windows_lan_firewall",
    "_restart_event_bucket",
]


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


_GATEWAY_RESTART_EVENT_TYPES = {
    "gateway_restart",
    "gateway_frontdoor_restart",
    "gateway_inner_port_lost",
    "gateway_spawn_failed",
    "gateway_supervisor_gave_up",
    "self_heal_restart",
}



def _probe_code_sync(report: Report) -> None:
    from aletheia.update import git_ops, jj_ops
    # Developer checkouts are driven by jj (git HEAD is detached there); Fedora and
    # tarball runtimes carry no jj and keep the git view the updater uses.
    if jj_ops.is_jj_repo():
        backend, fetch_cmd = jj_ops, "jj git fetch"
        behind_hint = "this machine is behind — `jj git fetch` then `jj rebase -d master@origin` before working"
        dirty_hint = "uncommitted changes present — `jj describe` and `jj new` before switching machines"
        branch = jj_ops.current_bookmark() or "master"
    elif git_ops.is_repo():
        backend, fetch_cmd = git_ops, "git fetch"
        behind_hint = "this machine is behind — `git pull --ff-only` before working"
        dirty_hint = "uncommitted changes present — commit or stash before switching machines"
        branch = git_ops.current_branch() or "master"
    else:
        report.add("code.sync", "info", "not a git repo (or git unavailable)")
        return
    head = backend.current_commit()
    dirty = backend.is_dirty() if backend is jj_ops else backend.is_worktree_dirty()
    # ahead/behind are vs the LAST fetched origin; we do not fetch here (read-only,
    # offline-safe). doctor --fix may fetch. Note that fact in the detail.
    ahead = backend.ahead_count(branch)
    behind = backend.behind_count(branch)

    bits = [f"branch={branch}", f"HEAD={head[:7] or '?'}"]
    if ahead > 0:
        bits.append(f"{ahead} unpushed")
    if behind > 0:
        bits.append(f"{behind} behind origin")
    if dirty:
        bits.append("dirty worktree")
    detail = ", ".join(bits) + f"  (vs last fetch; run `{fetch_cmd}` for live)"

    if behind > 0:
        report.add("code.sync", "warn", detail, behind_hint)
    elif dirty:
        report.add("code.sync", "info", detail, dirty_hint)
    else:
        report.add("code.sync", "ok", detail)



def _probe_legacy_launchd(report: Report) -> None:
    """Warn when a known superseded Aletheia LaunchAgent is still registered."""
    if platform.system() != "Darwin":
        return
    try:
        from aletheia.gateway import autostart
        found = autostart._legacy_launchd_present(
            labels=autostart._SESSION_SERVICE_LEGACY_LAUNCHD_LABELS,
        )
    except Exception:
        report.add(
            "process.legacy_launchd",
            "warn",
            "legacy Aletheia lifecycle state not available",
            "restore launchctl access before changing Gateway lifecycle ownership",
        )
        return
    if found:
        labels = ", ".join(found)
        report.add(
            "process.legacy_launchd",
            "warn",
            f"legacy Aletheia lifecycle job(s) still loaded: {labels}",
            "`aletheia gateway enable` migrates them to the current session service; "
            "`aletheia gateway stop` removes them and stops the current session "
            "(`aletheia gateway disable` first to disable login recovery)",
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
    from aletheia.config.loader import load_config
    from aletheia.gateway.network import local_frontdoor_host
    from aletheia.gateway.pidfile import is_running, port_in_use, read_pid
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
            logger.warning("doctor: code-drift probe unavailable", exc_info=True)



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
        report.add_probe_error("process.restart_history", e)
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
    from pydantic import ValidationError
    try:
        cfg = load_config()
    except ValidationError as exc:
        paths = ", ".join(
            sorted(
                {
                    ".".join(str(part) for part in error.get("loc", ()))
                    for error in exc.errors(include_input=False)
                    if error.get("type") == "extra_forbidden"
                }
            )
        ) or "invalid persisted value"
        report.add(
            "config.validation",
            "blocker",
            f"persisted configuration rejected at: {paths}",
            "repair config.yaml by removing the unknown key or adding an explicit migration; "
            "no source values were rewritten",
        )
        return
    report.add("config.permission", "info", f"permission_mode={cfg.agent.permission_mode}")
    report.add("config.live", "info",
               f"live.state={cfg.live.state}, exchange.mode={cfg.exchange.mode}, "
               f"paper_execution_mode={getattr(cfg.exchange, 'paper_execution_mode', '?')}")

    from aletheia.config.exchanges import DEMO_EXECUTION_MODES

    paper_mode = getattr(cfg.exchange, "paper_execution_mode", "")
    if cfg.exchange.mode == "demo" and paper_mode in DEMO_EXECUTION_MODES:
        # Read the cap admission actually enforces. The flat alias is not mirrored
        # for this field, so a nested-only write (copy / replace / reset-defaults)
        # left this blocker unable to fire after the operator loop reduced the
        # real cap. A mode that is registered as demo but owns no risk policy is
        # reported, never silently treated as a healthy cap.
        from aletheia.risk.policy import UnknownRiskExecutionMode, resolve_risk_policy

        try:
            demo_cap = resolve_risk_policy(cfg, paper_mode).policy.capital.max_position_margin_usdt
        except UnknownRiskExecutionMode:
            report.add(
                "config.demo_margin_cap",
                "blocker",
                f"paper_execution_mode={paper_mode!r} owns no risk policy — its margin cap is unverifiable",
                "register the execution mode's risk policy, or select a registered paper/demo mode",
            )
        else:
            if float(demo_cap) <= 0:
                report.add(
                    "config.demo_margin_cap",
                    "blocker",
                    f"max_position_margin_usdt={float(demo_cap):g} — every paper/demo proposal is rejected",
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
    """Sanitized configured/missing/unavailable credential status.

    `creds.llm` answers "are the credentials shaped correctly", which is NOT
    "can this system do inference". Keep its wording confined to configuration;
    reachability is `llm.inference`'s job.
    """
    from aletheia.config.loader import load_config
    from aletheia.uta.manager import read_credential_presence
    cfg = load_config()

    demo_presence = read_credential_presence("bitget_demo", cfg=cfg)
    mode = cfg.exchange.mode
    pem = getattr(cfg.exchange, "paper_execution_mode", "")

    if demo_presence is None or len(demo_presence) != 3:
        report.add("creds.bitget_demo", "blocker" if (mode == "demo" and pem == "bitget_demo") else "warn",
                   "bitget demo credential status unavailable — configuration not verified")
    elif all(demo_presence):
        report.add("creds.bitget_demo", "ok", "all 3 demo credentials SET")
    elif any(demo_presence):
        report.add("creds.bitget_demo", "warn",
                   f"partial: {sum(demo_presence)}/3 demo credentials SET",
                   "set the missing ones in Settings → Credentials (or `aletheia secrets set bitget.demo.*`)")
    else:
        # Missing demo creds only BLOCKS if the system is configured to place demo orders.
        sev: Severity = "blocker" if (mode == "demo" and pem == "bitget_demo") else "info"
        report.add("creds.bitget_demo", sev,
                   "no bitget demo credentials — bitget_demo orders cannot reach the exchange",
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
    from aletheia.model_provider.registry import PROVIDER_TYPES
    if ptype not in PROVIDER_TYPES:
        report.add("creds.llm", "warn",
                   f"unsupported LLM provider (default={provider}, type={ptype})",
                   "Select a supported HTTP endpoint in Settings -> Model Provider")
        return

    secret_ref = pcfg.api_key_secret_ref
    if secret_ref:
        from aletheia.model_provider.router import provider_credential_status
        configured = provider_credential_status(provider, pcfg).get("configured")
        if configured is True:
            report.add("creds.llm", "ok",
                       f"LLM credentials configured (default={provider}, secret_ref={secret_ref}) "
                       "— inference not verified here, see llm.inference")
        elif configured is False:
            report.add("creds.llm", "warn",
                   f"LLM provider secret missing (default={provider}, secret_ref={secret_ref})",
                   "Settings -> Model Provider, or set the configured model secret")
        else:
            report.add("creds.llm", "warn",
                       f"LLM credential status unavailable (default={provider}) — configuration not verified")
        return

    endpoint = pcfg.base_url
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



def _probe_llm_inference(
    report: Report,
    *,
    active_probe: bool = False,
    probe_requested: bool = False,
) -> None:
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
        report.add_probe_error("llm.inference", e, "LLM call ledger unreadable")
        evidence = None

    if evidence is not None:
        attempts, ok = evidence["attempts"], evidence["ok"]
        window = evidence["window_minutes"]
        if not attempts:
            hint = "" if active_probe or probe_requested else (
                "`aletheia doctor --probe-llm` to verify with a real call"
            )
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
        from aletheia.config.loader import load_config, read_only_config

        # Keep config migrations suppressed across the fallback/provider path,
        # while leaving the SQLite fence open for intentional probe accounting.
        with read_only_config():
            cfg = load_config(persist_migrations=False)
            result = asyncio.run(probe_llm_providers_at_startup(cfg, max_retries=1))
    except Exception as e:
        report.add_probe_error("llm.inference.probe", e, "active probe could not run")
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



def _probe_webui_static(report: Report) -> None:
    """WebUI 静态文件完整性检查（新增诊断维度）。"""
    from pathlib import Path
    project_root = Path(__file__).resolve().parent.parent.parent.parent
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



def _egress_ip(opener) -> str | None:
    """Best-effort external egress IP via an opener. None on any failure."""
    import urllib.request  # imported so custom handlers work

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
        report.add("network.proxy", "info", f"could not read config: {type(e).__name__}")
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

    # urllib's ProxyHandler cannot probe SOCKS; configuration is not reachability proof.
    if str(protocol).startswith("socks"):
        report.add("network.proxy", "info",
                   f"{protocol} proxy configured [{mode}]; "
                   "reachability not probed here")
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
    if enforce:
        # Enforce forbids direct egress, including this comparison probe.
        report.add("network.proxy", "ok",
                   f"proxy reachable [{mode}] ({latency_ms}ms); egress {proxy_ip or 'unknown'}; "
                   "direct egress not probed: enforce")
        return
    direct_ip = _egress_ip(urllib.request.build_opener(urllib.request.ProxyHandler({})))
    if proxy_ip and direct_ip and proxy_ip == direct_ip:
        report.add("network.proxy", "warn",
                   f"proxy reachable [{mode}] ({latency_ms}ms) but egress IP == direct IP "
                   f"({proxy_ip}) — traffic may NOT be routed through the proxy",
                   "verify the proxy forwards to an upstream with a different exit IP")
        return

    leak_note = f"; egress {proxy_ip} ≠ direct {direct_ip}" if (proxy_ip and direct_ip) else ""
    report.add("network.proxy", "ok",
               f"proxy reachable [{mode}] ({latency_ms}ms){leak_note}")



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


# Define exports before importing doctor so either module can be imported first.
from aletheia.diagnostics.doctor import (
    Check,
    Report,
    Severity,
    _age_minutes,
    _fmt_age,
    _log_hint_for_check,
    _parse_iso_datetime,
    logger,
)
