"""Thin repository functions for SQLite CRUD."""

import json
from aletheia.storage.sqlite import get_db
from aletheia.storage.models import (
    AuditEvent, AgentRun, Opportunity, TradeProposal,
    RiskDecision, Order, Fill, Position, StrategyRecord, KnowledgeItem,
)


def _insert(table: str, data: dict) -> None:
    db = get_db()
    cols = ", ".join(data.keys())
    placeholders = ", ".join(["?"] * len(data))
    db.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", list(data.values()))
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
    sets = ", ".join(f"{k} = ?" for k in data.keys())
    db.execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*data.values(), id_val])
    db.commit()


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


# --- Orders ---

def insert_order(o: Order) -> None:
    _insert("orders", o.model_dump())


def update_order(oid: str, **kwargs) -> None:
    _update("orders", oid, kwargs)


def list_orders(limit: int = 100) -> list[dict]:
    return _select_all("orders", limit=limit)


# --- Fills ---

def insert_fill(f: Fill) -> None:
    _insert("fills", f.model_dump())


def list_fills(limit: int = 100) -> list[dict]:
    return _select_all("fills", limit=limit)


# --- Positions ---

def insert_position(p: Position) -> None:
    _insert("positions", p.model_dump())


def update_position(pid: str, **kwargs) -> None:
    _update("positions", pid, kwargs)


def list_positions(status: str | None = None, limit: int = 50) -> list[dict]:
    if status:
        return _select_all("positions", where="status = ?", params=(status,), limit=limit)
    return _select_all("positions", limit=limit)


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


def list_knowledge_items(item_type: str | None = None, limit: int = 50) -> list[dict]:
    if item_type:
        return _select_all("knowledge_items", where="item_type = ?", params=(item_type,), limit=limit)
    return _select_all("knowledge_items", limit=limit)
