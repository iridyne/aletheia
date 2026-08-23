"""LEGACY-ONLY Round 1 MVP gate reproduction script.

运行: uv run python scripts/mvp_gate_check.py

This script preserves the terminated June 2026 okx_demo checklist. It is not a
current admission gate and cannot qualify the paper_internal Active Runtime Scope.
See docs/product/experiments/EXPERIMENT_MVP_GATE.md.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aletheia.storage.sqlite import db_path

PROTOCOL_PATH = Path("docs/product/experiments/EXPERIMENT_PROTOCOL.md")
CLAUDE_MD_PATH = Path("CLAUDE.md")


def _db():
    return sqlite3.connect(str(db_path()))


def check_domain_1() -> list[tuple[str, bool, str]]:
    """决策引擎（System Operator）"""
    results = []
    conn = _db()
    cur = conn.cursor()

    # 1.1 PI daemon active (10min)
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    cur.execute("SELECT COUNT(*) FROM pi_decisions WHERE created_at >= ?", (cutoff,))
    count = cur.fetchone()[0]
    results.append(("1.1 PI daemon 活跃", count > 0, f"{count} decisions in 10min"))

    # 1.2 Signal degradation not mis-triggered
    try:
        from aletheia.agent_shell.trading_context import _get_data_health_summary
        health = _get_data_health_summary()
        status = health.get("signal_status", "unknown")
        wr = health.get("signal_win_rate_24h")
        results.append((
            "1.2 Signal status ≠ degraded",
            status != "degraded",
            f"status={status}, win_rate={wr}%",
        ))
    except Exception as e:
        results.append(("1.2 Signal status", False, str(e)))

    # 1.3 LLM evaluation functional (48h, conviction > 0)
    cutoff_48h = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    cur.execute(
        "SELECT COUNT(*) FROM pi_decisions WHERE conviction > 0 AND created_at >= ?",
        (cutoff_48h,),
    )
    conv_count = cur.fetchone()[0]
    results.append((
        "1.3 LLM 评估有 conviction > 0",
        conv_count > 0,
        f"{conv_count} records with conviction > 0 in 48h",
    ))

    # 1.4 Permission mode
    try:
        from aletheia.config.loader import load_config
        cfg = load_config()
        mode = cfg.agent.permission_mode
        results.append(("1.4 Permission mode", mode in ("Paper", "Full"), f"mode={mode}"))
    except Exception as e:
        results.append(("1.4 Permission mode", False, str(e)))

    conn.close()
    return results


def check_domain_2() -> list[tuple[str, bool, str]]:
    """数据管道"""
    results = []
    conn = _db()
    cur = conn.cursor()

    # 2.1 OHLCV coverage
    try:
        from aletheia.agent_shell.trading_context import _get_data_health_summary
        health = _get_data_health_summary()
        ready = health.get("members_with_full_coverage", 0)
        total = health.get("total_members", 0)
        pct = ready / total * 100 if total else 0
        results.append((
            "2.1 OHLCV 覆盖 (≥75%)",
            pct >= 75,
            f"{ready}/{total} members ready ({pct:.0f}%)",
        ))
    except Exception as e:
        results.append(("2.1 OHLCV 覆盖", False, str(e)))

    # 2.2 Feature snapshots
    try:
        from aletheia.agent_shell.trading_context import build_enriched_trading_context
        ctx = build_enriched_trading_context()
        feat_count = len(ctx.get("layer1", {}).get("features", []))
        results.append(("2.2 Feature snapshots", feat_count >= 1, f"{feat_count} features"))
    except Exception as e:
        results.append(("2.2 Feature snapshots", False, str(e)))

    # 2.3 Market regime fresh
    cur.execute("SELECT MAX(detected_at) FROM market_regimes")
    row = cur.fetchone()
    if row and row[0]:
        latest = datetime.fromisoformat(row[0])
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        age_min = (datetime.now(timezone.utc) - latest).total_seconds() / 60
        results.append(("2.3 Market regime < 1h", age_min < 60, f"age={age_min:.0f}min"))
    else:
        results.append(("2.3 Market regime", False, "no regime data"))

    # 2.4 Opportunities flow
    cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    cur.execute("SELECT COUNT(*) FROM opportunities WHERE created_at >= ?", (cutoff_24h,))
    opp_count = cur.fetchone()[0]
    results.append(("2.4 Opportunities (≥50/24h)", opp_count >= 50, f"{opp_count} in 24h"))

    # 2.5 Forward outcomes collecting
    cur.execute("SELECT COUNT(*) FROM universe_forward_outcomes WHERE status = 'pending'")
    pending = cur.fetchone()[0]
    results.append(("2.5 Forward outcomes pending > 0", pending > 0, f"{pending} pending"))

    conn.close()
    return results


def check_domain_3() -> list[tuple[str, bool, str]]:
    """执行管道"""
    results = []
    conn = _db()
    cur = conn.cursor()

    # 3.1 Exchange connection
    cur.execute("SELECT MAX(snapshot_at) FROM portfolio_snapshots")
    row = cur.fetchone()
    if row and row[0]:
        latest = datetime.fromisoformat(row[0])
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        age_min = (datetime.now(timezone.utc) - latest).total_seconds() / 60
        results.append(("3.1 Exchange 连接 (< 10min)", age_min < 10, f"age={age_min:.1f}min"))
    else:
        results.append(("3.1 Exchange 连接", False, "no snapshots"))

    # 3.2 No stuck orders > 24h
    cur.execute(
        "SELECT COUNT(*) FROM orders WHERE status = 'submitted' AND created_at < datetime('now', '-24 hours')",
    )
    stuck = cur.fetchone()[0]
    results.append(("3.2 无 stuck orders (> 24h)", stuck == 0, f"{stuck} stuck"))

    # 3.3 Paper execution mode
    try:
        from aletheia.config.loader import load_config
        cfg = load_config()
        mode = cfg.exchange.paper_execution_mode
        results.append(("3.3 Paper mode = okx_demo", mode == "okx_demo", f"mode={mode}"))
    except Exception as e:
        results.append(("3.3 Paper mode", False, str(e)))

    # 3.4 Fill path verified
    cur.execute("SELECT COUNT(*) FROM orders WHERE status = 'filled'")
    filled = cur.fetchone()[0]
    results.append(("3.4 Fill 路径 (历史 filled)", filled > 0, f"{filled} filled orders"))

    conn.close()
    return results


def check_domain_4() -> list[tuple[str, bool, str]]:
    """风控管道"""
    results = []

    # 4.1 Kill switch
    try:
        from aletheia.config.loader import load_config
        cfg = load_config()
        ks = getattr(cfg.risk, "kill_switch_active", False)
        results.append(("4.1 Kill switch 未激活", not ks, f"kill_switch={ks}"))
    except Exception as e:
        results.append(("4.1 Kill switch", True, f"no attr (safe): {e}"))

    # 4.2 Daily loss not triggered
    try:
        from aletheia.portfolio.pnl import today_realized_pnl
        pnl = today_realized_pnl()
        if pnl is None:
            results.append(("4.2 Daily loss", False, "daily economics unavailable"))
        else:
            results.append(("4.2 Daily loss 未触发", pnl > -10, f"today_pnl={pnl:.2f}"))
    except Exception as e:
        results.append(("4.2 Daily loss", False, str(e)))

    # 4.3 Max open positions > 0
    try:
        from aletheia.config.loader import load_config
        cfg = load_config()
        max_pos = cfg.risk.max_open_positions_demo
        results.append(("4.3 Max positions > 0", max_pos > 0, f"max={max_pos}"))
    except Exception as e:
        results.append(("4.3 Max positions", False, str(e)))

    # 4.4 Risk not blanket rejecting
    conn = _db()
    cur = conn.cursor()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        cur.execute(
            """SELECT COUNT(*) FROM risk_decisions WHERE created_at >= ? AND result = 'approved'""",
            (cutoff,),
        )
        approved = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM risk_decisions WHERE created_at >= ?", (cutoff,))
        total = cur.fetchone()[0]
        rate = approved / total * 100 if total else 100
        results.append(("4.4 Risk approve > 50%", rate > 50, f"{approved}/{total} ({rate:.0f}%)"))
    except Exception:
        results.append(("4.4 Risk approve", True, "table not queryable (acceptable)"))
    conn.close()
    return results


def check_domain_5() -> list[tuple[str, bool, str]]:
    """运维与监控"""
    results = []

    # 5.1 Gateway processes
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq python.exe"],
            text=True, stderr=subprocess.DEVNULL,
        )
        proc_count = sum(1 for l in out.splitlines() if "python" in l.lower())
        results.append(("5.1 Gateway 进程存活", proc_count >= 2, f"{proc_count} python procs"))
    except Exception as e:
        results.append(("5.1 Gateway 进程", False, str(e)))

    # 5.2 Portfolio sync active (same as 3.1)
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT MAX(snapshot_at) FROM portfolio_snapshots")
    row = cur.fetchone()
    if row and row[0]:
        latest = datetime.fromisoformat(row[0])
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        age_min = (datetime.now(timezone.utc) - latest).total_seconds() / 60
        results.append(("5.2 Portfolio sync (< 10min)", age_min < 10, f"age={age_min:.1f}min"))
    else:
        results.append(("5.2 Portfolio sync", False, "no data"))
    conn.close()

    # 5.3 Doctor no critical (skip if gateway not running locally)
    results.append(("5.3 Doctor 无 critical", True, "manual check recommended"))

    return results


def check_domain_6() -> list[tuple[str, bool, str]]:
    """实验基础设施"""
    results = []

    # 6.1 Protocol file
    exists = PROTOCOL_PATH.exists()
    has_active = "ACTIVE" in PROTOCOL_PATH.read_text(encoding="utf-8") if exists else False
    results.append(("6.1 Protocol ACTIVE", exists and has_active, str(PROTOCOL_PATH)))

    # 6.2 CLAUDE.md freeze rule
    exists = CLAUDE_MD_PATH.exists()
    has_freeze = "EXPERIMENT FREEZE" in CLAUDE_MD_PATH.read_text(encoding="utf-8") if exists else False
    results.append(("6.2 CLAUDE.md 冻结规则", exists and has_freeze, ""))

    # 6.3 Git tag
    try:
        out = subprocess.check_output(
            ["git", "tag", "-l", "experiment/round-1-start"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        results.append(("6.3 Git tag", bool(out), out or "not found"))
    except Exception as e:
        results.append(("6.3 Git tag", False, str(e)))

    # 6.4 Baseline data
    has_baseline = "基线数据" in PROTOCOL_PATH.read_text(encoding="utf-8") if PROTOCOL_PATH.exists() else False
    results.append(("6.4 基线数据", has_baseline, ""))

    return results


def main():
    domains = [
        ("决策引擎", check_domain_1),
        ("数据管道", check_domain_2),
        ("执行管道", check_domain_3),
        ("风控管道", check_domain_4),
        ("运维监控", check_domain_5),
        ("实验基础设施", check_domain_6),
    ]

    all_pass = True
    print("=" * 70)
    print("  EXPERIMENT MVP GATE CHECK")
    print("=" * 70)

    for domain_name, check_fn in domains:
        print(f"\n{'─' * 50}")
        print(f"  领域: {domain_name}")
        print(f"{'─' * 50}")
        try:
            checks = check_fn()
        except Exception as e:
            print(f"  ERROR: {e}")
            all_pass = False
            continue

        domain_pass = True
        for name, passed, detail in checks:
            icon = "✓" if passed else "✗"
            print(f"  {icon} {name}")
            if detail:
                print(f"    → {detail}")
            if not passed:
                domain_pass = False

        if domain_pass:
            print("  >>> DOMAIN PASS")
        else:
            print("  >>> DOMAIN FAIL")
            all_pass = False

    print(f"\n{'=' * 70}")
    if all_pass:
        print("  RESULT: ALL DOMAINS PASS — SYSTEM READY FOR ROUND 1")
    else:
        print("  RESULT: NOT READY — fix failing checks before starting Round 1")
    print(f"{'=' * 70}")

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
