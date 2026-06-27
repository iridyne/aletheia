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


def _probe_profit_loop(report: Report) -> None:
    """Minimum profit loop liveness — the core revenue closed loop.

    The loop is: opportunities -> (paper_trader ranks) -> signal_pending pi_decisions
    -> PI Agent LLM decides -> trade_proposals -> risk -> orders -> positions.

    A silent stall here is the most dangerous failure class: the observe stage
    keeps producing opportunities (so loop.newest_opportunity stays green) while
    NO new proposals are created for hours — the system looks alive but earns and
    learns nothing. This probe catches that decoupling directly by comparing the
    age of the newest opportunity against the age of the newest proposal, and by
    inspecting the recent PI-decision action mix for an all-skip pattern.

    Root precedent: 2026-06-23 the PI Agent saw a phantom $167 balance (wrong
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
        try:
            rows = db.execute(
                "SELECT action, COUNT(*) c FROM pi_decisions "
                "WHERE created_at >= datetime('now','-12 hours') GROUP BY action"
            ).fetchall()
            mix = {r[0]: r[1] for r in rows}
            skips = mix.get("skip", 0)
            trades = mix.get("trade", 0) + mix.get("scale_in", 0) + mix.get("flip", 0)
            if skips > 0 and trades == 0:
                attribution = (f" — PI Agent returned skip×{skips}/trade×0 in 12h "
                               f"(check trading_context balance + regime inputs)")
        except Exception:
            pass
        prop_desc = "none ever" if prop_age is None else _fmt_age(prop_age)
        report.add("loop.profit", "warn",
                   f"PROFIT LOOP STALLED: opportunities fresh ({_fmt_age(opp_age)}) but "
                   f"newest proposal {prop_desc}{attribution}",
                   "opportunities flow but produce no proposals — inspect "
                   "paper_trader_ranker scoring + PI Agent decision inputs "
                   "(see docs/issues/MINIMUM_PROFIT_LOOP_AUDIT.md)")
    elif prop_age is not None and prop_age < PROPOSAL_STALL_MIN:
        report.add("loop.profit", "ok",
                   f"loop healthy — newest proposal {_fmt_age(prop_age)}")
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
    if closed == 0:
        report.add("loop.round1", "info", "no closed trades since 2026-06-22")
    elif gap > 0 and closed < 20:
        report.add("loop.round1", "warn", f"BEHIND G6: {detail}",
                   "review pipeline-funnel for stage where conversion drops")
    elif pf is not None and pf < 1.0 and closed >= 5:
        report.add("loop.round1", "warn", f"PF below break-even: {detail}",
                   "check trading_decision skip bias + signal_accuracy_24h")
    else:
        report.add("loop.round1", "ok", detail)


def _probe_knowledge_flood(report: Report) -> None:
    """PI's Layer3-wisdom limit=30 window is dominated by system_action noise.

    When the window is occupied by high-frequency events the actual lesson and
    guard_candidate rows never make it into PI prompt. Mirrors the
    /api/analytics/learning-funnel observability surface.
    """
    try:
        from aletheia.storage.sqlite import get_db
        from collections import Counter
        db = get_db()
        cnt: Counter = Counter()
        for r in db.execute(
            "SELECT item_type FROM knowledge_items ORDER BY rowid DESC LIMIT 30"
        ):
            cnt[r["item_type"]] += 1
        if not cnt:
            report.add("loop.knowledge_flood", "info", "no knowledge_items yet")
            return
        guards = cnt.get("guard_candidate", 0)
        top_type, top_n = cnt.most_common(1)[0]
        if guards == 0 and top_n >= 25:
            report.add("loop.knowledge_flood", "warn",
                       f"PI guards window 0/30 — flooded by {top_type}×{top_n}",
                       "see Round 2 Fix L-Bug-A' (trading_context.py:398): "
                       "list_knowledge_items(item_type='guard_candidate', limit=30)")
        elif guards == 0:
            report.add("loop.knowledge_flood", "warn",
                       f"PI guards window 0/30 — top types: {dict(cnt.most_common(3))}")
        else:
            report.add("loop.knowledge_flood", "ok",
                       f"guards in window: {guards}/30; top: {top_type}×{top_n}")
    except Exception as e:
        report.add("loop.knowledge_flood", "info", f"probe error: {e}")


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
                    f"retrieval pool flooded by reviews — {detail}",
                    "ranker _lesson_penalty scores against hallucination-prone "
                    "post_trade_review, not real cases. Root cause = R0 "
                    "opportunity_id=None starving trade_case_study has_full_chain. "
                    "Round 2 Fix L-Bug-C (LEARNING_LOOP_AUDIT_2026-06-26.md §R0/R1)")
            else:
                report.add("loop.retrieval_pool", "ok", detail)

        # --- 2. guard promotion stall ---------------------------------------
        candidates = db.execute(
            "SELECT COUNT(*) FROM knowledge_items WHERE item_type='guard_candidate'"
        ).fetchone()[0]
        active_guards = db.execute(
            "SELECT COUNT(*) FROM knowledge_items WHERE item_type='trading_guard' "
            "AND (status='active' OR status IS NULL)"
        ).fetchone()[0]
        if candidates == 0:
            report.add("loop.guard_promotion", "info", "no guard_candidates yet")
        elif active_guards == 0:
            report.add(
                "loop.guard_promotion", "warn",
                f"{candidates} guard_candidate piled up, 0 promoted to trading_guard",
                "promotion chain dead — config auto_promote_enabled=false + "
                "load_trading_guards reads knowledge/guards/ while candidates write "
                "knowledge/guard_candidates/ + missing predicates frontmatter. "
                "Round 2 Fix L-Bug-D (audit 断点 B0/B2/B3)")
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
        rows = list(db.execute(
            "SELECT close_reason FROM positions "
            "WHERE status='closed' AND closed_at >= '2026-06-22' "
            "ORDER BY closed_at DESC LIMIT 50"
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
        recon_pct = round(reconciled / total * 100, 1) if total else 0
        ai_pct = round(ai_driven / total * 100, 1) if total else 0
        detail = (f"last {total}: reconciled={reconciled}({recon_pct}%), "
                  f"units_bug={units_bug}, ai_driven={ai_driven}({ai_pct}%)")
        if recon_pct > 50:
            report.add("loop.reconciled_ratio", "warn",
                       f"reconciler dominates closes: {detail}",
                       "judgement PF/WR is driven by orphan sweep, not AI — "
                       "investigate position_reconciler._try_get_tpsl_fill_price "
                       "and reconciler trigger frequency")
        elif recon_pct > 25:
            report.add("loop.reconciled_ratio", "info",
                       f"reconciled noticeable: {detail}")
        else:
            report.add("loop.reconciled_ratio", "ok", detail)
    except Exception as e:
        report.add("loop.reconciled_ratio", "info", f"probe error: {e}")


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
            "SELECT o.symbol || ':' || o.direction AS bucket, COUNT(*) n "
            "FROM fills f JOIN orders o ON f.order_id = o.id "
            "WHERE f.filled_at >= datetime('now', '-1 day') "
            "GROUP BY bucket ORDER BY n DESC"
        ))
        if not rows:
            report.add("loop.concentration", "info", "no fills in last 24h")
            return
        total = sum(r[1] for r in rows)
        top_bucket, top_n = rows[0][0], rows[0][1]
        top_pct = round(top_n / total * 100, 1) if total else 0
        detail = f"24h fills={total}, top: {top_bucket}={top_n} ({top_pct}%)"
        if top_pct > 60:
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
        _probe_profit_loop,
        _probe_round1,
        _probe_knowledge_flood,
        _probe_learning_retrieval,
        _probe_reconciled_ratio,
        _probe_concentration,
        _probe_health,
        _probe_operator_loop,
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
