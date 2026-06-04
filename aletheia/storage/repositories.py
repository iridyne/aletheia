"""Thin repository functions for SQLite CRUD."""

from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import json
from aletheia.storage.sqlite import get_db
from aletheia.storage.models import (
    AuditEvent, AgentRun, Opportunity, TradeProposal,
    RiskDecision, Order, Fill, Position, StrategyRecord, KnowledgeItem,
)


def _insert(table: str, data: dict) -> None:
    db = get_db()
    was_in_tx = db.in_transaction
    cols = ", ".join(data.keys())
    placeholders = ", ".join(["?"] * len(data))
    db.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", list(data.values()))
    if not was_in_tx and not db.in_transaction:
        db.commit()


def _select_all(table: str, where: str | None = None, params: tuple = (), order: str = "rowid DESC", limit: int = 100) -> list[dict]:
    db = get_db()
    sql = f"SELECT * FROM {table}"
    if where:
        sql += f" WHERE {where}"
    sql += f" ORDER BY {order} LIMIT {limit}"
    rows = db.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def _select_one(table: str, id_val: str) -> dict | None:
    db = get_db()
    row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (id_val,)).fetchone()
    return dict(row) if row else None


def _update(table: str, id_val: str, data: dict) -> None:
    db = get_db()
    was_in_tx = db.in_transaction
    sets = ", ".join(f"{k} = ?" for k in data.keys())
    db.execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*data.values(), id_val])
    if not was_in_tx and not db.in_transaction:
        db.commit()


@contextmanager
def transaction():
    db = get_db()
    if db.in_transaction:
        yield
        return
    db.execute("BEGIN")
    try:
        yield
        db.commit()
    except Exception:
        db.rollback()
        raise


# --- Audit Events ---

def insert_audit_event(event: AuditEvent) -> None:
    _insert("audit_events", event.model_dump())


def list_audit_events(limit: int = 50) -> list[dict]:
    return _select_all("audit_events", limit=limit)


# --- Agent Runs ---

def insert_agent_run(run: AgentRun) -> None:
    _insert("agent_runs", run.model_dump())


def update_agent_run(run_id: str, **kwargs) -> None:
    _update("agent_runs", run_id, kwargs)


def list_agent_runs(limit: int = 50) -> list[dict]:
    return _select_all("agent_runs", limit=limit)


def get_agent_run(run_id: str) -> dict | None:
    return _select_one("agent_runs", run_id)


# --- Opportunities ---

def insert_opportunity(opp: Opportunity) -> None:
    _insert("opportunities", opp.model_dump())


def list_opportunities(limit: int = 50) -> list[dict]:
    return _select_all("opportunities", limit=limit)


def get_opportunity(oid: str) -> dict | None:
    return _select_one("opportunities", oid)


def update_opportunity(oid: str, **kwargs) -> None:
    _update("opportunities", oid, kwargs)


# --- Trade Proposals ---

def insert_trade_proposal(p: TradeProposal) -> None:
    _insert("trade_proposals", p.model_dump())


def update_trade_proposal(pid: str, **kwargs) -> None:
    _update("trade_proposals", pid, kwargs)


def list_trade_proposals(limit: int = 50) -> list[dict]:
    return _select_all("trade_proposals", limit=limit)


def get_trade_proposal(pid: str) -> dict | None:
    return _select_one("trade_proposals", pid)


# --- Risk Decisions ---

def insert_risk_decision(d: RiskDecision) -> None:
    _insert("risk_decisions", d.model_dump())


def list_risk_decisions(limit: int = 50) -> list[dict]:
    return _select_all("risk_decisions", limit=limit)


def list_strategy_risk_decisions(strategy_id: str, limit: int = 1000) -> list[dict]:
    db = get_db()
    rows = db.execute(
        """
        SELECT rd.*
        FROM risk_decisions rd
        JOIN trade_proposals p ON p.id = rd.proposal_id
        WHERE p.strategy_id = ?
        ORDER BY rd.created_at DESC
        LIMIT ?
        """,
        (strategy_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# --- Orders ---

def insert_order(o: Order) -> None:
    _insert("orders", o.model_dump())


def update_order(oid: str, **kwargs) -> None:
    _update("orders", oid, kwargs)


def list_orders(limit: int = 100) -> list[dict]:
    return _select_all("orders", limit=limit)


def list_strategy_orders(
    strategy_id: str,
    execution_mode_prefix: str | None = None,
    limit: int = 1000,
) -> list[dict]:
    db = get_db()
    sql = """
        SELECT o.*
        FROM orders o
        JOIN trade_proposals p ON p.id = o.proposal_id
        WHERE p.strategy_id = ?
    """
    params: list[object] = [strategy_id]
    if execution_mode_prefix:
        if execution_mode_prefix == "paper":
            sql += " AND (o.execution_mode = ? OR o.execution_mode = ?)"
            params.extend(["paper_internal", "bitget_demo"])
        else:
            sql += " AND o.execution_mode LIKE ?"
            params.append(f"{execution_mode_prefix}%")
    sql += " ORDER BY o.created_at ASC LIMIT ?"
    params.append(limit)
    rows = db.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# --- Fills ---

def insert_fill(f: Fill) -> None:
    _insert("fills", f.model_dump())


def list_fills(limit: int = 100) -> list[dict]:
    return _select_all("fills", limit=limit)


def list_strategy_fills(
    strategy_id: str,
    execution_mode_prefix: str | None = None,
    limit: int = 1000,
) -> list[dict]:
    db = get_db()
    sql = """
        SELECT f.*
        FROM fills f
        JOIN orders o ON o.id = f.order_id
        JOIN trade_proposals p ON p.id = o.proposal_id
        WHERE p.strategy_id = ?
    """
    params: list[object] = [strategy_id]
    if execution_mode_prefix:
        if execution_mode_prefix == "paper":
            sql += " AND (o.execution_mode = ? OR o.execution_mode = ?)"
            params.extend(["paper_internal", "bitget_demo"])
        else:
            sql += " AND o.execution_mode LIKE ?"
            params.append(f"{execution_mode_prefix}%")
    sql += " ORDER BY f.filled_at ASC LIMIT ?"
    params.append(limit)
    rows = db.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# --- Positions ---

def insert_position(p: Position) -> None:
    _insert("positions", p.model_dump())


def update_position(pid: str, **kwargs) -> None:
    _update("positions", pid, kwargs)


def list_positions(status: str | None = None, limit: int = 50) -> list[dict]:
    if status:
        return _select_all("positions", where="status = ?", params=(status,), limit=limit)
    return _select_all("positions", limit=limit)


def list_strategy_positions(strategy_id: str, status: str | None = None, limit: int = 1000) -> list[dict]:
    if status:
        return _select_all(
            "positions",
            where="strategy_id = ? AND status = ?",
            params=(strategy_id, status),
            order="COALESCE(closed_at, opened_at) ASC",
            limit=limit,
        )
    return _select_all(
        "positions",
        where="strategy_id = ?",
        params=(strategy_id,),
        order="COALESCE(closed_at, opened_at) ASC",
        limit=limit,
    )


# --- Strategy Records ---

def insert_strategy(s: StrategyRecord) -> None:
    _insert("strategy_records", s.model_dump())


def update_strategy(sid: str, **kwargs) -> None:
    _update("strategy_records", sid, kwargs)


def get_strategy(sid: str) -> dict | None:
    return _select_one("strategy_records", sid)


def list_strategies(limit: int = 50) -> list[dict]:
    return _select_all("strategy_records", limit=limit)


# --- Knowledge Items ---

def insert_knowledge_item(k: KnowledgeItem) -> None:
    _insert("knowledge_items", k.model_dump())


def update_knowledge_item(kid: str, **kwargs) -> None:
    _update("knowledge_items", kid, kwargs)


def list_knowledge_items(item_type: str | None = None, limit: int = 50, strategy_id: str | None = None) -> list[dict]:
    if item_type:
        where = "item_type = ?"
        params: tuple = (item_type,)
        if strategy_id is not None:
            where += " AND strategy_id = ?"
            params = (item_type, strategy_id)
        return _select_all("knowledge_items", where=where, params=params, limit=limit)
    return _select_all("knowledge_items", limit=limit)


# --- Market Snapshots ---

def insert_market_snapshot(snap) -> None:
    _insert("market_snapshots", snap.model_dump())


def list_market_snapshots(symbol: str | None = None, limit: int = 100) -> list[dict]:
    if symbol:
        return _select_all("market_snapshots", where="symbol = ?", params=(symbol,), limit=limit)
    return _select_all("market_snapshots", limit=limit)


# --- Portfolio Snapshots ---

def insert_portfolio_snapshot(snap) -> None:
    _insert("portfolio_snapshots", snap.model_dump())


def list_portfolio_snapshots(limit: int = 100) -> list[dict]:
    return _select_all("portfolio_snapshots", limit=limit)


# --- Agent Tool Calls ---

def insert_tool_call(run_id: str, tool_name: str, tool_class: str, permitted: bool,
                     input_data: str | None = None, output_data: str | None = None) -> None:
    from aletheia.storage.models import new_id
    _insert("agent_tool_calls", {
        "id": new_id(),
        "run_id": run_id,
        "tool_name": tool_name,
        "tool_class": tool_class,
        "input_data": input_data,
        "output_data": output_data,
        "permitted": 1 if permitted else 0,
    })


def list_tool_calls(run_id: str | None = None, limit: int = 100) -> list[dict]:
    if run_id:
        return _select_all("agent_tool_calls", where="run_id = ?", params=(run_id,), limit=limit)
    return _select_all("agent_tool_calls", limit=limit)


# --- Data retention / cleanup (Component 3) ---
#
# Only high-volume TELEMETRY may be pruned. Trust-bearing causal-chain tables
# (trade_proposals, risk_decisions, orders, fills, positions), audit_events, and
# knowledge_items are intentionally absent from this whitelist — their permanence
# is the safety guarantee (trust §1.1 + AGENTS.md "hard deletion forbidden").
_PRUNABLE = {
    "agent_runs": "started_at",
    "agent_run_steps": "created_at",
    "agent_tool_calls": "created_at",
    "market_snapshots": "snapshot_at",
    "portfolio_snapshots": "snapshot_at",
}


def _delete_old_rows(table: str, days: int) -> int:
    """Delete rows in a WHITELISTED telemetry table older than `days`. Returns count."""
    if table not in _PRUNABLE:
        raise ValueError(f"table {table!r} is not prunable")
    ts_col = _PRUNABLE[table]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    was_in_tx = db.in_transaction
    cur = db.execute(f"DELETE FROM {table} WHERE {ts_col} < ?", (cutoff,))
    if not was_in_tx and not db.in_transaction:
        db.commit()
    return cur.rowcount if cur.rowcount is not None else 0


def prune_agent_runs(days: int = 30) -> int:
    return _delete_old_rows("agent_runs", days)


def prune_run_steps(days: int = 30) -> int:
    return _delete_old_rows("agent_run_steps", days)


def prune_tool_calls(days: int = 30) -> int:
    return _delete_old_rows("agent_tool_calls", days)


def prune_market_snapshots(days: int = 30) -> int:
    return _delete_old_rows("market_snapshots", days)


def prune_portfolio_snapshots(days: int = 90) -> int:
    return _delete_old_rows("portfolio_snapshots", days)
