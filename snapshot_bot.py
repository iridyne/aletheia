#!/usr/bin/env python3
"""aletheia snapshot_bot — read-only trade-activity snapshot.

Connects to the local SQLite ``app.db`` in read-only mode and prints a summary of
recent trading activity:

    * trade_proposals  (near-window + 24h context by status)
    * risk_decisions   (near-window + 24h context approved/rejected)
    * open positions   (status='open')
    * pi_decisions      (near-window signal/trade dynamics + 24h context)
    * orders            (near-window fills/errors)

It also runs a small regression check: within the window, a ``rejected`` trade
proposal whose risk-rejection reason contains ``account_loss_unavailable`` or a
missing ``parameter_rationale`` is flagged as a possible bot regression.

The script performs NO writes against the database, the filesystem, or any
configuration. It is safe to run repeatedly.

Usage:
    python3 snapshot_bot.py                 # 1h window, 24h context
    SNAPSHOT_WINDOW_HOURS=6 python3 ...     # custom window
    SNAPSHOT_CTX_HOURS=72 python3 ...       # custom context window
"""
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

WINDOW_HOURS = int(os.environ.get("SNAPSHOT_WINDOW_HOURS", "1"))
CTX_HOURS = int(os.environ.get("SNAPSHOT_CTX_HOURS", "24"))

# Regression markers: a rejected proposal whose rejection reason contains any of
# these substrings indicates the previously-seen regression may have returned.
REGRESSION_MARKERS = ("account_loss_unavailable", "parameter_rationale")


def find_db():
    """Locate app.db in the standard deployment / repo locations (read-only)."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        "/home/ubuntu/aletheia/data/app.db",
        os.path.join(os.getcwd(), "data", "app.db"),
        os.path.join(here, "data", "app.db"),
        os.path.abspath("app.db"),
        os.path.join(here, "aletheia", "data", "app.db"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def rejection_reason(cur, proposal_id, proposal_rationale):
    """Build the full rejection reason text for a proposal from its risk decision
    (message + violations) and the proposal's own agent_rationale."""
    parts = []
    rd = cur.execute(
        "SELECT message, violations FROM risk_decisions "
        "WHERE proposal_id=? ORDER BY created_at DESC LIMIT 1",
        (proposal_id,),
    ).fetchone()
    if rd:
        if rd["message"]:
            parts.append(str(rd["message"]))
        if rd["violations"]:
            parts.append(str(rd["violations"]))
    if proposal_rationale:
        parts.append(str(proposal_rationale))
    return " ".join(parts)


def main():
    db = find_db()
    if not db:
        print("ERROR: app.db not found in standard locations.", file=sys.stderr)
        print("Searched: /home/ubuntu/aletheia/data/app.db, ./data/app.db, "
              "<script>/data/app.db, ./app.db", file=sys.stderr)
        sys.exit(2)

    uri = "file:%s?mode=ro" % db
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    now = datetime.now(timezone.utc)
    since = (now - timedelta(hours=WINDOW_HOURS)).isoformat()
    since_ctx = (now - timedelta(hours=CTX_HOURS)).isoformat()

    # --- trade proposals ---
    tp_1h = cur.execute(
        "SELECT id, symbol, market_type, direction, order_type, quantity, leverage, "
        "status, source, created_at, agent_rationale "
        "FROM trade_proposals WHERE created_at>=? ORDER BY created_at DESC",
        (since,),
    ).fetchall()
    tp_ctx = cur.execute(
        "SELECT status, COUNT(*) AS c FROM trade_proposals "
        "WHERE created_at>=? GROUP BY status",
        (since_ctx,),
    ).fetchall()

    # --- risk decisions ---
    rd_1h = cur.execute(
        "SELECT id, proposal_id, approved, risk_score, violations, message, created_at "
        "FROM risk_decisions WHERE created_at>=? ORDER BY created_at DESC",
        (since,),
    ).fetchall()
    rd_ctx = cur.execute(
        "SELECT approved, COUNT(*) AS c FROM risk_decisions "
        "WHERE created_at>=? GROUP BY approved",
        (since_ctx,),
    ).fetchall()

    # --- open positions ---
    pos = cur.execute(
        "SELECT symbol, direction, quantity, entry_price, current_price, "
        "unrealized_pnl, status, opened_at "
        "FROM positions WHERE status='open' ORDER BY opened_at DESC"
    ).fetchall()

    # --- pi decisions ---
    pi_1h = cur.execute(
        "SELECT created_at, symbol, direction, action, conviction, "
        "proposal_status, proposal_reason, signal_source "
        "FROM pi_decisions WHERE created_at>=? ORDER BY created_at DESC",
        (since,),
    ).fetchall()
    pi_ctx = cur.execute(
        "SELECT action, COUNT(*) AS c FROM pi_decisions "
        "WHERE created_at>=? GROUP BY action",
        (since_ctx,),
    ).fetchall()

    # --- orders (recent) ---
    od_1h = cur.execute(
        "SELECT symbol, direction, status, error_reason, created_at, filled_at "
        "FROM orders WHERE created_at>=? ORDER BY created_at DESC",
        (since,),
    ).fetchall()

    # --- regression scan ---
    rejected_1h = cur.execute(
        "SELECT id, symbol, status, agent_rationale, created_at FROM trade_proposals "
        "WHERE created_at>=? AND status='rejected' ORDER BY created_at DESC",
        (since,),
    ).fetchall()
    regression_hits = []
    for r in rejected_1h:
        reason = rejection_reason(cur, r["id"], r["agent_rationale"])
        for marker in REGRESSION_MARKERS:
            if marker.lower() in (reason or "").lower():
                regression_hits.append((r["symbol"], marker, r["created_at"]))

    rejected_ctx = cur.execute(
        "SELECT id, symbol, agent_rationale FROM trade_proposals "
        "WHERE created_at>=? AND status='rejected'",
        (since_ctx,),
    ).fetchall()
    regression_ctx = []
    for r in rejected_ctx:
        reason = rejection_reason(cur, r["id"], r["agent_rationale"])
        for marker in REGRESSION_MARKERS:
            if marker.lower() in (reason or "").lower():
                regression_ctx.append((r["symbol"], marker))

    conn.close()

    # ---------------- render report ----------------
    def line(c="-"):
        return c * 64

    out = []
    out.append(line("="))
    out.append("aletheia bot 交易活动快照")
    out.append("生成时间(UTC): %s" % now.strftime("%Y-%m-%d %H:%M:%S UTC"))
    out.append("快照窗口: 近 %dh | 上下文: 近 %dh" % (WINDOW_HOURS, CTX_HOURS))
    out.append("数据源: %s (read-only)" % db)
    out.append(line("="))

    # trade proposals
    out.append("")
    out.append("[交易提案 trade_proposals] 窗口内 %d 笔" % len(tp_1h))
    if tp_1h:
        for r in tp_1h:
            out.append("  - %s | %s %s | %s | qty=%s lev=%s | status=%s | src=%s | %s" % (
                r["created_at"], r["direction"], r["symbol"],
                r["market_type"], r["quantity"], r["leverage"],
                r["status"], r["source"], r["id"][:10]))
    else:
        out.append("  (无)")
    ctx_map = {row["status"]: row["c"] for row in tp_ctx}
    out.append("  上下文(近%dh)按 status: %s" % (CTX_HOURS, dict(ctx_map)))

    # risk decisions
    out.append("")
    out.append("[风控决策 risk_decisions] 窗口内 %d 笔" % len(rd_1h))
    if rd_1h:
        for r in rd_1h:
            verdict = "APPROVED" if r["approved"] else "REJECTED"
            v = r["violations"] or ""
            vshort = (v[:80] + "...") if len(v) > 80 else v
            out.append("  - %s | %s | score=%.3f | %s | %s" % (
                r["created_at"], verdict, r["risk_score"],
                vshort, r["id"][:10]))
    else:
        out.append("  (无)")
    rd_ctx_map = {}
    for row in rd_ctx:
        rd_ctx_map["approved" if row["approved"] else "rejected"] = row["c"]
    out.append("  上下文(近%dh): %s" % (CTX_HOURS, rd_ctx_map))

    # open positions
    out.append("")
    out.append("[持仓 positions (open)] %d 笔" % len(pos))
    if pos:
        for r in pos:
            pnl = r["unrealized_pnl"]
            pnl_s = ("%.4f" % pnl) if pnl is not None else "—"
            out.append("  - %s %s | qty=%s | entry=%s cur=%s | uPnL=%s | opened=%s" % (
                r["direction"], r["symbol"], r["quantity"],
                r["entry_price"], r["current_price"], pnl_s, r["opened_at"]))
    else:
        out.append("  (无未平仓)")

    # pi decisions
    out.append("")
    out.append("[pi 决策动态 pi_decisions] 窗口内 %d 条" % len(pi_1h))
    if pi_1h:
        for r in pi_1h:
            out.append("  - %s | %s %s | action=%s | conv=%s | prop_status=%s | src=%s" % (
                r["created_at"], r["direction"], r["symbol"], r["action"],
                r["conviction"], r["proposal_status"], r["signal_source"]))
    else:
        out.append("  (无)")
    pi_ctx_map = {row["action"]: row["c"] for row in pi_ctx}
    out.append("  上下文(近%dh)按 action: %s" % (CTX_HOURS, dict(pi_ctx_map)))

    # orders
    out.append("")
    out.append("[订单 orders] 窗口内 %d 笔" % len(od_1h))
    if od_1h:
        for r in od_1h:
            err = (" | ERR=%s" % r["error_reason"]) if r["error_reason"] else ""
            out.append("  - %s | %s %s | status=%s | filled=%s%s" % (
                r["created_at"], r["direction"], r["symbol"], r["status"],
                r["filled_at"], err))
    else:
        out.append("  (无)")

    # regression verdict
    out.append("")
    out.append(line("="))
    out.append("[回归扫描 regression check]")
    if regression_hits:
        out.append("  ⚠ 窗口内发现 %d 处疑似回归(rejected + 命中标记):" % len(regression_hits))
        for sym, marker, ts in regression_hits:
            out.append("    * %s @ %s — 命中 '%s'" % (sym, ts, marker))
        out.append("  => bot 可能再现回归，需人工关注。")
    else:
        out.append("  窗口内无 rejected/命中标记 (account_loss_unavailable / parameter_rationale)。")
        if regression_ctx:
            out.append("  注: 上下文(近%dh)内有 %d 处命中(非窗口内)，供参考: %s" % (
                CTX_HOURS, len(regression_ctx), regression_ctx))
        out.append("  => 窗口内交易活动正常(无回归特征)。")
    out.append(line("="))

    print("\n".join(out))
    sys.exit(0)


if __name__ == "__main__":
    main()
