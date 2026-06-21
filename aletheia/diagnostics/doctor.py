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
from datetime import datetime, timezone
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


def _probe_config_gates(report: Report) -> None:
    from aletheia.config.loader import load_config
    cfg = load_config()
    report.add("config.permission", "info", f"permission_mode={cfg.agent.permission_mode}")
    report.add("config.live", "info",
               f"live.state={cfg.live.state}, exchange.mode={cfg.exchange.mode}, "
               f"paper_execution_mode={getattr(cfg.exchange, 'paper_execution_mode', '?')}")

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


def _probe_network(report: Report) -> None:
    try:
        from aletheia.config.loader import load_config
        cfg = load_config()
        proxy_url = cfg.network.proxy_url
    except Exception as e:
        report.add("network.proxy", "info", f"could not read config: {e}")
        return

    if not proxy_url:
        report.add("network.proxy", "ok", "not configured (direct connection)")
        return

    import time
    import urllib.request
    url = "https://api.binance.com/api/v3/ping"
    t0 = time.monotonic()
    try:
        import os
        proxies = {"https": proxy_url, "http": proxy_url}
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
        opener.open(url, timeout=8).read()
        latency_ms = int((time.monotonic() - t0) * 1000)
        report.add("network.proxy", "ok", f"proxy configured, reachable ({latency_ms}ms) — {proxy_url}")
    except Exception as e:
        latency_ms = int((time.monotonic() - t0) * 1000)
        report.add("network.proxy", "warn", f"proxy configured but unreachable ({latency_ms}ms): {e}",
                   "check proxy is running and proxy_url is correct in Settings → Infrastructure → Network")


def _probe_audit_chain(report: Report) -> None:
    """Verify audit JSONL hash chain integrity."""
    from aletheia.files.jsonl import verify_audit_chain
    result = verify_audit_chain()
    records = result.get("records", 0)
    if records == 0:
        report.add("trust.audit_chain", "info", "no audit records yet")
        return
    if result["valid"]:
        reanchors = result.get("reanchors", 0)
        extra = f", {reanchors} rotation re-anchors" if reanchors else ""
        report.add("trust.audit_chain", "ok", f"hash chain valid ({records} records{extra})")
    else:
        errors = result.get("errors", [])
        detail = f"chain BROKEN — {len(errors)} error(s) in {records} records: {errors[:3]}"
        report.add("trust.audit_chain", "warn", detail,
                   "audit log may have been tampered with or corrupted")


def _probe_config_consistency(report: Report) -> None:
    """Cross-field config consistency — detects contradictions like disabled default provider."""
    from aletheia.config.loader import load_config
    from aletheia.diagnostics.config_consistency import check_config_consistency
    cfg = load_config()
    findings = check_config_consistency(cfg)
    if not findings:
        report.add("config.consistency", "ok", "no cross-field inconsistencies")
        return
    for f in findings:
        sev: Severity = "warn" if f.severity == "warning" else "blocker"  # type: ignore[assignment]
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


def build_report() -> Report:
    """Probe every dimension of ground truth. Pure read — changes nothing."""
    report = Report(generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    for probe in (
        _probe_code_sync,
        _probe_process,
        _probe_config_gates,
        _probe_config_consistency,
        _probe_credentials,
        _probe_market_data,
        _probe_data_quality,
        _probe_ohlcv_coverage,
        _probe_signal_effectiveness,
        _probe_trading_loop,
        _probe_health,
        _probe_webui_static,
        _probe_network,
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
