"""Thin repository functions for SQLite CRUD."""

from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import json
from aletheia.storage.sqlite import get_db
from aletheia.storage.models import (
    AuditEvent, AgentRun, Opportunity, TradeProposal,
    RiskDecision, Order, Fill, Position, StrategyRecord, KnowledgeItem,
    SerenityTheme, SerenityEvidence, SerenityOpportunity, SerenityRiskGate,
    SerenityReplaySnapshot, SerenityPostMortem, SerenityClaim,
    PaperAccountEvent,
)


def _insert(table: str, data: dict) -> None:
    db = get_db()
    cols = ", ".join(data.keys())
    placeholders = ", ".join(["?"] * len(data))
    db.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", list(data.values()))


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


def list_opportunities_by_asset_class(asset_class: str, limit: int = 50) -> list[dict]:
    return _select_all("opportunities", where="asset_class = ?", params=(asset_class,), limit=limit)


def find_open_opportunity_by_symbol_snapshot(symbol: str, snapshot_id: str, market: str | None = None) -> dict | None:
    """Find an existing open opportunity for the same symbol in the same universe snapshot.

    When ``market`` is given, also require ``opportunities.market = ?`` so a
    same-symbol opp from a different market (e.g., spot vs perp) does not
    annotate / block the other.  A missing/None market keeps the prior
    behavior (any open opp for the symbol+snapshot is a hit), preserving
    backward compatibility for callers that predate the market dimension.
    """
    db = get_db()
    sql = ("SELECT * FROM opportunities WHERE symbol = ? AND universe_snapshot_id = ? "
           "AND status = 'open'")
    params: list = [symbol, snapshot_id]
    if market:
        sql += " AND market = ?"
        params.append(market)
    sql += " LIMIT 1"
    row = db.execute(sql, params).fetchone()
    return dict(row) if row else None


# --- PI Decisions ---

def insert_pi_decision(decision) -> None:
    """Persist a System Operator trading decision to the database."""
    _insert("pi_decisions", decision.model_dump() if hasattr(decision, "model_dump") else decision)


def latest_pi_decision(symbol: str, max_age_minutes: int = 30) -> dict | None:
    """Get the most recent PI decision for a symbol within max_age_minutes."""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)).isoformat()
    db = get_db()
    row = db.execute(
        "SELECT * FROM pi_decisions WHERE symbol = ? AND created_at >= ? ORDER BY created_at DESC LIMIT 1",
        (symbol, cutoff),
    ).fetchone()
    return dict(row) if row else None


def list_pi_decisions(limit: int = 20) -> list[dict]:
    return _select_all("pi_decisions", order="created_at DESC", limit=limit)


# --- Capital Events ---

def insert_capital_event(event) -> None:
    _insert("capital_events", event.model_dump() if hasattr(event, "model_dump") else event)


def list_capital_events(limit: int = 200) -> list[dict]:
    return _select_all("capital_events", order="detected_at DESC", limit=limit)


def get_net_deposits() -> float:
    """Sum of all deposits minus withdrawals."""
    db = get_db()
    row = db.execute(
        "SELECT COALESCE(SUM(CASE WHEN event_type='deposit' THEN amount_usdt ELSE -amount_usdt END), 0) AS net FROM capital_events"
    ).fetchone()
    return float(row["net"]) if row else 0.0


def has_capital_event_for_broker(broker_id: str) -> bool:
    db = get_db()
    row = db.execute("SELECT 1 FROM capital_events WHERE broker_id = ? LIMIT 1", (broker_id,)).fetchone()
    return row is not None


def get_first_capital_event_time() -> str | None:
    """Earliest detected_at across all capital events (ISO string or None)."""
    db = get_db()
    row = db.execute("SELECT MIN(detected_at) AS t FROM capital_events").fetchone()
    return row["t"] if row and row["t"] else None


# --- Local paper account events ---

def insert_paper_account_event(event: PaperAccountEvent) -> None:
    _insert("paper_account_events", event.model_dump())


def list_paper_account_events(limit: int = 50) -> list[dict]:
    return _select_all("paper_account_events", order="created_at DESC, rowid DESC", limit=limit)


def get_paper_account_cash_balance() -> float:
    db = get_db()
    row = db.execute("SELECT COALESCE(SUM(amount_usdt), 0) AS cash FROM paper_account_events").fetchone()
    return float(row["cash"]) if row else 0.0


def has_paper_account_event() -> bool:
    db = get_db()
    return db.execute("SELECT 1 FROM paper_account_events LIMIT 1").fetchone() is not None


def has_paper_account_position_event(action: str, position_id: str) -> bool:
    db = get_db()
    return db.execute(
        "SELECT 1 FROM paper_account_events WHERE action = ? AND position_id = ? LIMIT 1",
        (action, position_id),
    ).fetchone() is not None


# --- Prediction Observer Runs ---

def insert_prediction_run(run_id: str, categories: list) -> None:
    import json as _json
    from aletheia.storage.models import _utcnow
    _insert("prediction_observer_runs", {
        "id": run_id,
        "started_at": _utcnow(),
        "status": "running",
        "categories": _json.dumps(categories),
    })


def update_prediction_run(run_id: str, *, status: str = "completed",
                          markets_scanned: int = 0, opportunities_created: int = 0,
                          errors: list | None = None) -> None:
    import json as _json
    from aletheia.storage.models import _utcnow
    data: dict = {
        "status": status,
        "completed_at": _utcnow(),
        "markets_scanned": markets_scanned,
        "opportunities_created": opportunities_created,
    }
    if errors:
        data["errors"] = _json.dumps(errors)
    _update("prediction_observer_runs", run_id, data)


def list_prediction_runs(limit: int = 20) -> list[dict]:
    return _select_all("prediction_observer_runs", limit=limit)


def get_latest_prediction_run() -> dict | None:
    rows = _select_all("prediction_observer_runs", order="started_at DESC", limit=1)
    return rows[0] if rows else None


# --- Prediction Normalized Tables ---

def upsert_prediction_event(data: dict) -> str:
    """Insert or update a prediction event by external_event_id."""
    from aletheia.storage.models import new_id, _utcnow
    db = get_db()
    ext_id = data["external_event_id"]
    existing = db.execute(
        "SELECT id FROM prediction_events WHERE external_event_id = ? AND source = ?",
        (ext_id, data.get("source", "polymarket")),
    ).fetchone()
    if existing:
        row_id = existing["id"]
        data["updated_at"] = _utcnow()
        data.pop("id", None)
        data.pop("created_at", None)
        sets = ", ".join(f"{k} = ?" for k in data.keys())
        db.execute(f"UPDATE prediction_events SET {sets} WHERE id = ?", [*data.values(), row_id])
    else:
        row_id = data.get("id") or new_id()
        data["id"] = row_id
        data.setdefault("created_at", _utcnow())
        data.setdefault("updated_at", _utcnow())
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" * len(data))
        db.execute(f"INSERT INTO prediction_events ({cols}) VALUES ({placeholders})", list(data.values()))
    return row_id


def upsert_prediction_market(data: dict) -> str:
    """Insert or update a prediction market by external_market_id."""
    from aletheia.storage.models import new_id, _utcnow
    db = get_db()
    ext_id = data["external_market_id"]
    existing = db.execute(
        "SELECT id FROM prediction_markets WHERE external_market_id = ? AND source = ?",
        (ext_id, data.get("source", "polymarket")),
    ).fetchone()
    if existing:
        row_id = existing["id"]
        data["updated_at"] = _utcnow()
        data.pop("id", None)
        sets = ", ".join(f"{k} = ?" for k in data.keys())
        db.execute(f"UPDATE prediction_markets SET {sets} WHERE id = ?", [*data.values(), row_id])
    else:
        row_id = data.get("id") or new_id()
        data["id"] = row_id
        data.setdefault("updated_at", _utcnow())
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" * len(data))
        db.execute(f"INSERT INTO prediction_markets ({cols}) VALUES ({placeholders})", list(data.values()))
    return row_id


def insert_probability_snapshot(snapshot: dict) -> None:
    from aletheia.storage.models import new_id, _utcnow
    snapshot.setdefault("id", new_id())
    snapshot.setdefault("created_at", _utcnow())
    _insert("prediction_probability_snapshots", snapshot)


def list_prediction_events(category: str | None = None, status: str = "active", limit: int = 50) -> list[dict]:
    parts = []
    params: list = []
    if status:
        parts.append("status = ?")
        params.append(status)
    if category:
        parts.append("category LIKE ?")
        params.append(f"%{category}%")
    where = " AND ".join(parts) if parts else None
    return _select_all("prediction_events", where=where, params=tuple(params), order="updated_at DESC", limit=limit)


def get_prediction_event(event_id: str) -> dict | None:
    return _select_one("prediction_events", event_id)


def list_prediction_markets_for_event(event_id: str, limit: int = 50) -> list[dict]:
    return _select_all("prediction_markets", where="event_id = ?", params=(event_id,), limit=limit)


def get_prediction_market_by_id(market_id: str) -> dict | None:
    return _select_one("prediction_markets", market_id)


def list_probability_snapshots(market_id: str, since: str | None = None, limit: int = 200) -> list[dict]:
    parts = ["market_id = ?"]
    params: list = [market_id]
    if since:
        parts.append("ts >= ?")
        params.append(since)
    where = " AND ".join(parts)
    return _select_all("prediction_probability_snapshots", where=where, params=tuple(params), order="ts ASC", limit=limit)


def insert_prediction_claim(data: dict) -> None:
    from aletheia.storage.models import new_id, _utcnow
    data.setdefault("id", new_id())
    data.setdefault("created_at", _utcnow())
    _insert("prediction_market_claims", data)


def list_prediction_claims(
    market_id: str | None = None,
    claim_type: str | None = None,
    since: str | None = None,
    limit: int = 100,
) -> list[dict]:
    parts = []
    params: list = []
    if market_id:
        parts.append("market_id = ?")
        params.append(market_id)
    if claim_type:
        parts.append("claim_type = ?")
        params.append(claim_type)
    if since:
        parts.append("datetime(created_at) >= datetime(?)")
        params.append(since)
    where = " AND ".join(parts) if parts else None
    return _select_all("prediction_market_claims", where=where, params=tuple(params), order="created_at DESC", limit=limit)


# --- Trade Proposals ---

# TaG hash chain (P0-1, 2026-06-29). See:
#   docs/research/OPENALICE_TAG_DEEP_DIVE_2026-06-29.md §五 P0-1
# Fields excluded from the hash because they mutate after insert:
#   - status (pending → executed / rejected via update_trade_proposal)
#   - commit_hash (the field being computed)
# Everything else is treated as immutable; mutating any of these post-insert
# would invalidate the chain (caught by the L2c spec re-compute check).
_PROPOSAL_HASH_EXCLUDES = {"status", "commit_hash"}


def _compute_proposal_hash(p: TradeProposal) -> str:
    """Return 16-char SHA-256 prefix of the proposal's immutable content + parent_hash.

    Stable for a given proposal as long as no excluded field is mutated. 16 hex
    chars = 64-bit collision space (OpenAlice's reference impl uses 8; we widen
    to 16 because the cost is trivial and false-collision risk drops by ~2^32).
    """
    from aletheia.storage.hashing import sha256_json

    payload = p.model_dump(exclude=_PROPOSAL_HASH_EXCLUDES)
    return sha256_json(payload)[:16]


def insert_trade_proposal(p: TradeProposal) -> None:
    if p.commit_hash is None:
        # Wire parent_hash to the most-recent already-hashed proposal (global
        # single chain). rowid DESC matches insertion order. NULL parent_hash
        # is correct for the very first hashed proposal in the table.
        db = get_db()
        row = db.execute(
            "SELECT commit_hash FROM trade_proposals "
            "WHERE commit_hash IS NOT NULL "
            "ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        p.parent_hash = row[0] if row else None
        p.commit_hash = _compute_proposal_hash(p)
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

def get_order(oid: str) -> dict | None:
    return _select_one("orders", oid)


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


def list_knowledge_items(item_type: str | None = None, limit: int = 50, strategy_id: str | None = None, status: str = "active") -> list[dict]:
    conditions = []
    params_list: list = []
    if item_type:
        conditions.append("item_type = ?")
        params_list.append(item_type)
    if strategy_id is not None:
        conditions.append("strategy_id = ?")
        params_list.append(strategy_id)
    if status != "all":
        conditions.append("(status = ? OR status IS NULL)")
        params_list.append(status)
    where = " AND ".join(conditions) if conditions else None
    return _select_all("knowledge_items", where=where, params=tuple(params_list), limit=limit)


def knowledge_hash_exists(item_type: str, content_hash: str) -> bool:
    """Check if a knowledge item with this content hash already exists."""
    rows = _select_all(
        "knowledge_items",
        where="item_type = ? AND content_hash = ?",
        params=(item_type, content_hash),
        limit=1,
    )
    return len(rows) > 0


def knowledge_title_exists_within(item_type: str, title: str, hours: int = 6) -> bool:
    """Check if a knowledge item with same type+title was created within N hours."""
    from aletheia.storage.sqlite import get_db
    db = get_db()
    row = db.execute(
        "SELECT 1 FROM knowledge_items "
        "WHERE item_type = ? AND title = ? AND created_at > datetime('now', ?)"
        " LIMIT 1",
        (item_type, title, f"-{hours} hours"),
    ).fetchone()
    return row is not None


# --- Serenity Lens ---

def insert_serenity_theme(t: SerenityTheme) -> None:
    _insert("serenity_themes", t.model_dump())


def get_serenity_theme(theme_id: str) -> dict | None:
    return _select_one("serenity_themes", theme_id)


def list_serenity_themes(status: str | None = "active", limit: int = 100) -> list[dict]:
    where: str | None = None
    params: list = []
    if status:
        where = "status = ?"
        params.append(status)
    return _select_all("serenity_themes", where=where, params=tuple(params), limit=limit)


def update_serenity_theme(theme_id: str, **kwargs) -> None:
    _update("serenity_themes", theme_id, kwargs)


def insert_serenity_evidence(e: SerenityEvidence) -> None:
    _insert("serenity_evidence", e.model_dump())


def list_serenity_evidence(theme_id: str | None = None, limit: int = 100) -> list[dict]:
    where: str | None = None
    params: list = []
    if theme_id is not None:
        where = "theme_id = ?"
        params.append(theme_id)
    return _select_all("serenity_evidence", where=where, params=tuple(params), limit=limit)


def insert_serenity_opportunity(o: SerenityOpportunity) -> None:
    data = o.model_dump()
    _insert("serenity_opportunities", data)
    try:
        from aletheia.analytics.causal_controls import record_serenity_control_cohort
        record_serenity_control_cohort(data)
    except Exception:
        pass


def get_serenity_opportunity(opportunity_id: str) -> dict | None:
    return _select_one("serenity_opportunities", opportunity_id)


def list_serenity_opportunities(
    theme_id: str | None = None,
    status: str | None = None,
    stage: str | None = None,
    limit: int = 100,
) -> list[dict]:
    conditions: list[str] = []
    params: list = []
    if theme_id is not None:
        conditions.append("theme_id = ?")
        params.append(theme_id)
    if status is not None:
        conditions.append("status = ?")
        params.append(status)
    if stage is not None:
        conditions.append("stage = ?")
        params.append(stage)
    where = " AND ".join(conditions) if conditions else None
    return _select_all("serenity_opportunities", where=where, params=tuple(params), limit=limit)


def update_serenity_opportunity(opp_id: str, **kwargs) -> None:
    _update("serenity_opportunities", opp_id, kwargs)


def insert_serenity_risk_gate(g: SerenityRiskGate) -> None:
    _insert("serenity_risk_gates", g.model_dump())


def list_serenity_risk_gates(opportunity_id: str, limit: int = 100) -> list[dict]:
    return _select_all("serenity_risk_gates", where="opportunity_id = ?", params=(opportunity_id,), limit=limit)


def insert_serenity_replay_snapshot(s: SerenityReplaySnapshot) -> None:
    _insert("serenity_replay_snapshots", s.model_dump())


def list_serenity_replay_snapshots(opportunity_id: str, limit: int = 200) -> list[dict]:
    return _select_all("serenity_replay_snapshots", where="opportunity_id = ?", params=(opportunity_id,), order="created_at DESC", limit=limit)


def insert_serenity_post_mortem(p: SerenityPostMortem) -> None:
    _insert("serenity_post_mortems", p.model_dump())


def list_serenity_post_mortems(opportunity_id: str | None = None, limit: int = 100) -> list[dict]:
    where: str | None = None
    params: list = []
    if opportunity_id is not None:
        where = "opportunity_id = ?"
        params.append(opportunity_id)
    return _select_all("serenity_post_mortems", where=where, params=tuple(params), limit=limit)


# --- Serenity Research Runs ---

def insert_serenity_research_run(r) -> None:
    _insert("serenity_research_runs", r.model_dump())


def get_serenity_research_run(run_id: str) -> dict | None:
    return _select_one("serenity_research_runs", run_id)


def list_serenity_research_runs(
    status: str | None = None,
    trigger_type: str | None = None,
    limit: int = 20,
) -> list[dict]:
    conditions: list[str] = []
    params: list = []
    if status:
        conditions.append("status = ?")
        params.append(status)
    if trigger_type:
        conditions.append("trigger_type = ?")
        params.append(trigger_type)
    where = " AND ".join(conditions) if conditions else None
    return _select_all("serenity_research_runs", where=where, params=tuple(params),
                       order="created_at DESC", limit=limit)


def update_serenity_research_run(run_id: str, **kwargs) -> None:
    _update("serenity_research_runs", run_id, kwargs)


def get_active_serenity_research_run() -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT * FROM serenity_research_runs WHERE status IN ('queued', 'running', 'waiting_retry') "
        "ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def get_serenity_research_run_by_idempotency_key(key: str) -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT * FROM serenity_research_runs WHERE idempotency_key = ?", (key,)
    ).fetchone()
    return dict(row) if row else None


# --- Serenity Run Events ---

def insert_serenity_run_event(e) -> None:
    _insert("serenity_run_events", e.model_dump())


def list_serenity_run_events(run_id: str, limit: int = 200) -> list[dict]:
    return _select_all("serenity_run_events", where="run_id = ?", params=(run_id,),
                       order="created_at ASC", limit=limit)


# --- Serenity User Profiles ---

def get_serenity_user_profile() -> dict | None:
    rows = _select_all("serenity_user_profiles", order="updated_at DESC", limit=1)
    return rows[0] if rows else None


def upsert_serenity_user_profile(profile) -> None:
    existing = get_serenity_user_profile()
    if existing:
        _update("serenity_user_profiles", existing["id"], profile.model_dump())
    else:
        _insert("serenity_user_profiles", profile.model_dump())


def update_serenity_user_profile_fields(profile_id: str, **kwargs) -> None:
    _update("serenity_user_profiles", profile_id, kwargs)


# --- Serenity Profile Snapshots ---

def insert_serenity_profile_snapshot(snap) -> None:
    _insert("serenity_profile_snapshots", snap.model_dump())


def get_serenity_profile_snapshot(snapshot_id: str) -> dict | None:
    return _select_one("serenity_profile_snapshots", snapshot_id)


# --- Serenity Feedback Events ---

def insert_serenity_feedback_event(e) -> None:
    _insert("serenity_feedback_events", e.model_dump())


def list_serenity_feedback_events(target_type: str, target_id: str, limit: int = 50) -> list[dict]:
    return _select_all("serenity_feedback_events",
                       where="target_type = ? AND target_id = ?",
                       params=(target_type, target_id), limit=limit)


# --- Serenity Claims ---

def insert_serenity_claim(c) -> None:
    data = c.model_dump()
    data["price_relevant"] = int(data.get("price_relevant", False))
    _insert("serenity_claims", data)


def get_serenity_claim(claim_id: str) -> dict | None:
    return _select_one("serenity_claims", claim_id)


def insert_serenity_claims_bulk(claims: list) -> None:
    if not claims:
        return
    db = get_db()
    already_in_tx = db.in_transaction
    if not already_in_tx:
        db.execute("BEGIN")
    try:
        for c in claims:
            data = c.model_dump()
            data["price_relevant"] = int(data.get("price_relevant", False))
            cols = ", ".join(data.keys())
            placeholders = ", ".join(["?"] * len(data))
            db.execute(f"INSERT INTO serenity_claims ({cols}) VALUES ({placeholders})", list(data.values()))
        if not already_in_tx:
            db.commit()
    except Exception:
        if not already_in_tx:
            db.rollback()
        raise


def list_serenity_claims(run_id: str | None = None, price_relevant: bool | None = None, limit: int = 200) -> list[dict]:
    where_parts = []
    params: list = []
    if run_id:
        where_parts.append("run_id = ?")
        params.append(run_id)
    if price_relevant is not None:
        where_parts.append("price_relevant = ?")
        params.append(int(price_relevant))
    where = " AND ".join(where_parts) if where_parts else None
    return _select_all("serenity_claims", where=where, params=tuple(params), limit=limit)


# --- Serenity Theme-Claims Link ---

def insert_serenity_theme_claim(theme_id: str, claim_id: str, relevance_score: float | None = None) -> None:
    db = get_db()
    was_in_tx = db.in_transaction
    db.execute(
        "INSERT OR IGNORE INTO serenity_theme_claims (theme_id, claim_id, relevance_score) VALUES (?, ?, ?)",
        (theme_id, claim_id, relevance_score),
    )
    if not was_in_tx:
        db.commit()


def insert_serenity_theme_claims_bulk(links: list[tuple[str, str, float | None]]) -> None:
    db = get_db()
    was_in_tx = db.in_transaction
    for theme_id, claim_id, relevance_score in links:
        db.execute(
            "INSERT OR IGNORE INTO serenity_theme_claims (theme_id, claim_id, relevance_score) VALUES (?, ?, ?)",
            (theme_id, claim_id, relevance_score),
        )
    if not was_in_tx:
        db.commit()


def list_serenity_claims_for_theme(theme_id: str, limit: int = 200) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT c.*, tc.relevance_score FROM serenity_claims c "
        "JOIN serenity_theme_claims tc ON tc.claim_id = c.id "
        "WHERE tc.theme_id = ? ORDER BY c.created_at DESC LIMIT ?",
        (theme_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# --- Serenity Agent Locks ---

def acquire_serenity_lock(lock_key: str, owner_run_id: str, ttl_seconds: int = 600) -> bool:
    from datetime import datetime, timezone, timedelta
    db = get_db()
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat()

    # Atomic acquire: purge expired, then INSERT OR IGNORE (PK = lock_key).
    # Two concurrent threads both DELETE expired + INSERT OR IGNORE: only one
    # INSERT succeeds (the other is a no-op due to PK constraint). No TOCTOU.
    db.execute(
        "DELETE FROM serenity_agent_locks WHERE lock_key = ? AND expires_at < ?",
        (lock_key, now.isoformat()),
    )
    db.execute(
        "INSERT OR IGNORE INTO serenity_agent_locks "
        "(lock_key, owner_run_id, acquired_at, expires_at) VALUES (?, ?, ?, ?)",
        (lock_key, owner_run_id, now.isoformat(), expires_at),
    )
    acquired = db.execute("SELECT changes()").fetchone()[0] > 0
    return acquired


def release_serenity_lock(lock_key: str, owner_run_id: str) -> bool:
    db = get_db()
    result = db.execute(
        "DELETE FROM serenity_agent_locks WHERE lock_key = ? AND owner_run_id = ?",
        (lock_key, owner_run_id),
    )
    return result.rowcount > 0


# --- Market Snapshots ---

def insert_market_snapshot(snap) -> None:
    _insert("market_snapshots", snap.model_dump())


def list_market_snapshots(
    symbol: str | None = None,
    limit: int = 100,
    exchange: str = "",
    market_type: str | None = None,
) -> list[dict]:
    """Return market snapshots, optionally filtered by symbol, exchange, and/or market_type.

    ``market_type`` uses the snapshot-storage vocabulary ("spot"/"perp"/"futures"),
    which is the InstrumentId physical vocabulary — NOT the DB/execution string
    "usdt_futures". Callers holding a proposal/DB market_type must map it first
    (see ``market_data._snapshot_market_type``). None preserves prior behavior.
    """
    conditions: list[str] = []
    params: list = []
    if symbol:
        conditions.append("symbol = ?")
        params.append(symbol)
    if exchange:
        conditions.append("exchange = ?")
        params.append(exchange)
    if market_type:
        conditions.append("market_type = ?")
        params.append(market_type)
    where = " AND ".join(conditions) if conditions else None
    return _select_all(
        "market_snapshots",
        where=where,
        params=tuple(params),
        order="COALESCE(snapshot_at, '') DESC, rowid DESC",
        limit=limit,
    )


# --- Portfolio Snapshots ---

def insert_portfolio_snapshot(snap) -> None:
    _insert("portfolio_snapshots", snap.model_dump())


def list_portfolio_snapshots(limit: int = 100, days: int | None = None) -> list[dict]:
    """Return the latest snapshot window in chronological order.

    UI chart consumers need ascending timestamps, but the window itself must be
    selected from the newest rows so long-lived databases do not show stale
    history forever once the table grows beyond `limit`.

    If `days` is provided, only return snapshots within the last N days.
    """
    db = get_db()
    if days is not None and days > 0:
        rows = db.execute(
            """
            SELECT * FROM (
                SELECT *
                FROM portfolio_snapshots
                WHERE snapshot_at >= datetime('now', ?)
                ORDER BY snapshot_at DESC, id DESC
                LIMIT ?
            )
            ORDER BY snapshot_at ASC, id ASC
            """,
            (f"-{days} days", limit),
        ).fetchall()
    else:
        rows = db.execute(
            """
            SELECT *
            FROM (
                SELECT *
                FROM portfolio_snapshots
                ORDER BY snapshot_at DESC, id DESC
                LIMIT ?
            )
            ORDER BY snapshot_at ASC, id ASC
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_latest_portfolio_snapshot() -> dict | None:
    rows = _select_all("portfolio_snapshots", order="snapshot_at DESC, id DESC", limit=1)
    return rows[0] if rows else None


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


def insert_run_step(run_id: str, step_number: int, action: str,
                    input_data: str | None = None, output_data: str | None = None) -> None:
    """Record one reasoning/action step of an agent run (ordered by step_number)."""
    from aletheia.storage.models import new_id
    _insert("agent_run_steps", {
        "id": new_id(),
        "run_id": run_id,
        "step_number": step_number,
        "action": action,
        "input_data": input_data,
        "output_data": output_data,
    })


def list_run_steps(run_id: str, limit: int = 500) -> list[dict]:
    """Return a run's steps in execution order (step_number ASC)."""
    return _select_all("agent_run_steps", where="run_id = ?", params=(run_id,),
                       order="step_number ASC", limit=limit)


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
    # Analytics/telemetry tables that grew unbounded with no prune path (DG-1):
    # serenity_claims (research evidence) + shadow_trades (counterfactual analysis).
    # NOT causal-chain tables — safe to prune old rows. 90-day retention.
    "serenity_claims": "created_at",
    "shadow_trades": "created_at",
    # Filter evidence is analytical and reproducible from source decisions. Keep
    # enough history for paired cohorts without allowing 10-minute batches to
    # grow the SQLite file forever. Authoritative RiskDecision/order rows remain
    # outside this whitelist.
    "filter_decision_batches": "evaluated_at",
    "filter_decisions": "evaluated_at",
    "filter_candidate_outcomes": "entry_ts",
}

_CONDITIONALLY_PRUNABLE = {
    # pi_decisions contains both transient queue rows and causal decision rows.
    # It is prunable only with an explicit action filter supplied by the caller.
    "pi_decisions": "created_at",
    # knowledge_items is a causal table, but system_action rows are pure
    # telemetry noise (1200+/day) that floods the limit-30 window. Pruning
    # ONLY item_type='system_action' older than N days is safe because A2
    # fix made PI query with explicit item_type filters.
    "knowledge_items": "created_at",
}

def _delete_old_rows(
    table: str,
    days: int,
    extra_where: str | None = None,
    extra_params: tuple = (),
) -> int:
    """Delete rows in a WHITELISTED telemetry table older than `days`. Returns count."""
    if table in _PRUNABLE:
        ts_col = _PRUNABLE[table]
    elif table in _CONDITIONALLY_PRUNABLE and extra_where:
        ts_col = _CONDITIONALLY_PRUNABLE[table]
    else:
        raise ValueError(f"table {table!r} is not prunable")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    # See _insert: commit when not nested in a transaction(). The old
    # `and not db.in_transaction` guard never fired (DELETE auto-opens a tx).
    was_in_tx = db.in_transaction
    where = f"{ts_col} < ?"
    params = (cutoff,)
    if extra_where:
        where = f"({where}) AND ({extra_where})"
        params = (*params, *extra_params)
    cur = db.execute(f"DELETE FROM {table} WHERE {where}", params)
    if not was_in_tx:
        db.commit()
    return cur.rowcount if cur.rowcount is not None else 0


def prune_agent_runs(days: int = 30) -> int:
    # agent_run_steps and agent_tool_calls have FK → agent_runs with no ON DELETE CASCADE.
    # Delete children first in the same transaction to avoid IntegrityError.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    run_ids = [row[0] for row in db.execute(
        "SELECT id FROM agent_runs WHERE started_at < ?", (cutoff,)
    ).fetchall()]
    if not run_ids:
        return 0
    placeholders = ",".join("?" * len(run_ids))
    with transaction():
        db.execute(f"DELETE FROM agent_run_steps WHERE run_id IN ({placeholders})", run_ids)
        db.execute(f"DELETE FROM agent_tool_calls WHERE run_id IN ({placeholders})", run_ids)
        db.execute("DELETE FROM agent_runs WHERE started_at < ?", (cutoff,))
    return len(run_ids)


def prune_run_steps(days: int = 30) -> int:
    return _delete_old_rows("agent_run_steps", days)


def prune_tool_calls(days: int = 30) -> int:
    return _delete_old_rows("agent_tool_calls", days)


def prune_market_snapshots(days: int = 30) -> int:
    return _delete_old_rows("market_snapshots", days)


def prune_portfolio_snapshots(days: int = 90) -> int:
    return _delete_old_rows("portfolio_snapshots", days)


def prune_serenity_claims(days: int = 90) -> int:
    # serenity_theme_claims has FK → serenity_claims with no ON DELETE CASCADE.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    claim_ids = [row[0] for row in db.execute(
        "SELECT id FROM serenity_claims WHERE created_at < ?", (cutoff,)
    ).fetchall()]
    if not claim_ids:
        return 0
    placeholders = ",".join("?" * len(claim_ids))
    with transaction():
        db.execute(f"DELETE FROM serenity_theme_claims WHERE claim_id IN ({placeholders})", claim_ids)
        db.execute("DELETE FROM serenity_claims WHERE created_at < ?", (cutoff,))
    return len(claim_ids)


def prune_shadow_trades(days: int = 90) -> int:
    return _delete_old_rows("shadow_trades", days)


def prune_filter_decision_batches(days: int = 30) -> int:
    return _delete_old_rows("filter_decision_batches", days)


def prune_filter_decisions(days: int = 90) -> int:
    return _delete_old_rows("filter_decisions", days)


def prune_filter_candidate_outcomes(days: int = 90) -> int:
    return _delete_old_rows("filter_candidate_outcomes", days)


# Transient PI-decision actions: pre-decision signals + non-trade outcomes that
# carry no causal-chain reference (proposal_id is NULL). Decision-bearing actions
# (trade / close / recommend_promote) are KEPT forever — they are the System Operator's
# audit trail. signal_pending in particular accumulated to 1600+ rows (PT-1 bug)
# and, when duplicated, made the PI read its own pending queue as "data corrupted"
# and skip every decision. Pruning the transient rows keeps that queue clean.
_PRUNABLE_PI_ACTIONS = ("signal_pending", "skip", "watch")


def prune_pi_decisions(days: int = 7) -> int:
    """Delete old TRANSIENT pi_decisions (signal_pending/skip/watch), keeping
    decision-bearing rows (trade/close/recommend_promote) forever. Returns count.
    """
    placeholders = ",".join("?" for _ in _PRUNABLE_PI_ACTIONS)
    return _delete_old_rows(
        "pi_decisions",
        days,
        extra_where=f"action IN ({placeholders})",
        extra_params=_PRUNABLE_PI_ACTIONS,
    )


def prune_system_actions(days: int = 7) -> int:
    """Delete old system_action rows from knowledge_items.

    system_action is pure telemetry noise (~1200 rows/day) that used to flood
    the limit-30 window before the A2 fix added explicit item_type filters.
    Safe to prune because no learning/PI path queries system_action anymore.

    #44 note (2026-07-08): knowledge_items is otherwise governed by the
    no-hard-delete rule (audit reconstruction). This function is the ONE
    documented exception — telemetry rows are not causal/knowledge content
    and their unbounded growth would swamp the audit path itself. To keep
    the accountability trail honest despite the hard delete, we emit an
    audit event summarising the sweep (row count + oldest cutoff) so an
    operator can grep pruning history without keeping the row bodies.
    """
    deleted = _delete_old_rows(
        "knowledge_items",
        days,
        extra_where="item_type = ?",
        extra_params=("system_action",),
    )
    if deleted > 0:
        try:
            from aletheia.files.jsonl import write_audit
            write_audit({
                "actor": "system",
                "source": "retention",
                "event_type": "knowledge_items_pruned",
                "entity_type": "knowledge_items",
                "message": (
                    f"prune_system_actions: hard-deleted {deleted} system_action rows "
                    f"older than {days} days (documented exception to no-hard-delete)"
                ),
                "after": {
                    "deleted_count": deleted,
                    "cutoff_days": days,
                    "item_type": "system_action",
                },
            })
        except Exception:
            # Retention MUST NOT crash if the audit write fails — the point of
            # the audit hook is best-effort observability, not the retention
            # decision itself.
            pass
    return deleted


# ---------------------------------------------------------------------------
# OHLCV rolling window store
# ---------------------------------------------------------------------------

_OHLCV_WINDOW_DEFAULT = 50_000  # max bars kept per (symbol, timeframe) pair


def _ohlcv_window() -> int:
    """Read max_bars_per_pair from config, fallback to default."""
    try:
        from aletheia.config.loader import load_config
        return load_config().historical_data.max_bars_per_pair
    except Exception:
        return _OHLCV_WINDOW_DEFAULT


def upsert_ohlcv_candles(
    symbol: str,
    timeframe: str,
    candles: list,  # list of Candle dataclass from aletheia.data.providers
    provider: str = "ccxt",
    exchange: str = "",
    window: int | None = None,
) -> None:
    """Insert or replace OHLCV bars, then prune to rolling window.

    window: explicit bar limit; None reads from config (default 50000).
    exchange: source exchange name (e.g. "binance"). Stored for query filtering;
    does not affect the PRIMARY KEY (symbol, timeframe, ts) — last-writer wins
    for the same bar across exchanges (acceptable MVP tradeoff).
    """
    if not candles:
        return
    effective_exchange = exchange or provider
    effective_window = window if window is not None else _ohlcv_window()
    db = get_db()
    db.executemany(
        "INSERT OR REPLACE INTO market_ohlcv "
        "(symbol, timeframe, ts, open, high, low, close, volume, provider, exchange) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (symbol, timeframe, c.timestamp,
             c.open, c.high, c.low, c.close, c.volume, provider, effective_exchange)
            for c in candles
        ],
    )
    db.execute(
        "DELETE FROM market_ohlcv "
        "WHERE symbol = ? AND timeframe = ? "
        "AND ts NOT IN ("
        "  SELECT ts FROM market_ohlcv "
        "  WHERE symbol = ? AND timeframe = ? "
        "  ORDER BY ts DESC LIMIT ?"
        ")",
        (symbol, timeframe, symbol, timeframe, effective_window),
    )


def get_ohlcv_coverage(symbol: str, timeframe: str) -> dict:
    """Return coverage stats for a symbol+timeframe pair."""
    db = get_db()
    row = db.execute(
        "SELECT COUNT(*) as cnt, MIN(ts) as oldest_ts, MAX(ts) as newest_ts "
        "FROM market_ohlcv WHERE symbol = ? AND timeframe = ?",
        (symbol, timeframe),
    ).fetchone()
    return {
        "bar_count": row[0] or 0,
        "oldest_ts": row[1],
        "newest_ts": row[2],
    }


def get_ohlcv_newest_ts(symbol: str, timeframe: str) -> int | None:
    """Return the newest timestamp stored for a symbol+timeframe, or None."""
    db = get_db()
    row = db.execute(
        "SELECT MAX(ts) FROM market_ohlcv WHERE symbol = ? AND timeframe = ?",
        (symbol, timeframe),
    ).fetchone()
    return row[0] if row else None


def get_ohlcv_candles(
    symbol: str,
    timeframe: str,
    limit: int = 100,
    exchange: str = "",
) -> list[dict]:
    """Return up to `limit` most-recent OHLCV bars, oldest-first.

    exchange: if given, filter to rows from that exchange only.
    """
    db = get_db()
    if exchange:
        rows = db.execute(
            "SELECT ts, open, high, low, close, volume, provider, exchange "
            "FROM market_ohlcv "
            "WHERE symbol = ? AND timeframe = ? AND exchange = ? "
            "ORDER BY ts DESC LIMIT ?",
            (symbol, timeframe, exchange, limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT ts, open, high, low, close, volume, provider, exchange "
            "FROM market_ohlcv "
            "WHERE symbol = ? AND timeframe = ? "
            "ORDER BY ts DESC LIMIT ?",
            (symbol, timeframe, limit),
        ).fetchall()
    # Reverse so callers get oldest→newest
    return [dict(r) for r in reversed(rows)]


# --- Universe Generator ---

from aletheia.storage.models import (
    UniverseAsset, UniverseFeatureSnapshot, UniverseScannerRun,
    UniverseSnapshot, UniverseMember, UniversePolicy,
)


def insert_universe_asset(asset: UniverseAsset) -> None:
    _insert("universe_registry", asset.model_dump())


def upsert_universe_asset(asset: UniverseAsset) -> None:
    db = get_db()
    was_in_tx = db.in_transaction
    row = db.execute(
        "SELECT id FROM universe_registry WHERE venue = ? AND symbol = ? AND market_type = ?",
        (asset.venue, asset.symbol, asset.market_type),
    ).fetchone()
    if row:
        db.execute(
            "UPDATE universe_registry SET status = ?, last_seen_at = ?, "
            "min_notional = ?, price_precision = ?, amount_precision = ?, metadata = ? "
            "WHERE id = ?",
            (asset.status, asset.last_seen_at, asset.min_notional,
             asset.price_precision, asset.amount_precision, asset.metadata, row["id"]),
        )
    else:
        data = asset.model_dump()
        cols = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        db.execute(f"INSERT INTO universe_registry ({cols}) VALUES ({placeholders})", list(data.values()))
    if not was_in_tx:
        db.commit()


def deactivate_universe_assets_not_in_venue(active_venue: str) -> int:
    """Mark all registry rows from other venues as inactive."""
    db = get_db()
    was_in_tx = db.in_transaction
    result = db.execute(
        "UPDATE universe_registry SET status='inactive' WHERE venue != ? AND status='active'",
        (active_venue,),
    )
    if not was_in_tx:
        db.commit()
    return result.rowcount


def deactivate_stale_universe_assets(venue: str, cutoff_iso: str) -> int:
    """Mark registry rows for this venue not seen since cutoff as inactive."""
    db = get_db()
    was_in_tx = db.in_transaction
    result = db.execute(
        "UPDATE universe_registry SET status='inactive' "
        "WHERE venue = ? AND status='active' AND last_seen_at < ?",
        (venue, cutoff_iso),
    )
    if not was_in_tx:
        db.commit()
    return result.rowcount


def list_universe_assets(venue: str | None = None, active_only: bool = True, limit: int = 500) -> list[dict]:
    db = get_db()
    sql = "SELECT * FROM universe_registry"
    params: list = []
    conditions = []
    if venue:
        conditions.append("venue = ?")
        params.append(venue)
    if active_only:
        conditions.append("status = 'active'")
        conditions.append("is_blacklisted = 0")
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += f" ORDER BY last_seen_at DESC LIMIT {limit}"
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def get_universe_asset(asset_id: str) -> dict | None:
    return _select_one("universe_registry", asset_id)


def insert_universe_feature_snapshot(snap: UniverseFeatureSnapshot) -> None:
    _insert("universe_feature_snapshots", snap.model_dump())


def insert_universe_scanner_run(run: UniverseScannerRun) -> None:
    _insert("universe_scanner_runs", run.model_dump())


def update_universe_scanner_run(run_id: str, **kwargs) -> None:
    _update("universe_scanner_runs", run_id, kwargs)


def insert_universe_snapshot(snap: UniverseSnapshot) -> None:
    _insert("universe_snapshots", snap.model_dump())


def get_active_universe_snapshot(market_type: str = "crypto") -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT * FROM universe_snapshots WHERE status = 'active' AND market_type = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (market_type,),
    ).fetchone()
    return dict(row) if row else None


def expire_universe_snapshots(market_type: str = "crypto", except_id: str | None = None) -> None:
    db = get_db()
    was_in_tx = db.in_transaction
    if except_id:
        db.execute(
            "UPDATE universe_snapshots SET status = 'superseded' "
            "WHERE status = 'active' AND market_type = ? AND id != ?",
            (market_type, except_id),
        )
    else:
        db.execute(
            "UPDATE universe_snapshots SET status = 'superseded' "
            "WHERE status = 'active' AND market_type = ?",
            (market_type,),
        )
    if not was_in_tx:
        db.commit()


def insert_universe_member(member: UniverseMember) -> None:
    _insert("universe_members", member.model_dump())


def list_universe_members(snapshot_id: str, limit: int = 50) -> list[dict]:
    return _select_all(
        "universe_members",
        where="snapshot_id = ?",
        params=(snapshot_id,),
        order="rank ASC",
        limit=limit,
    )


def get_universe_member(member_id: str) -> dict | None:
    return _select_one("universe_members", member_id)


def get_active_universe_members(limit: int = 20) -> list[dict]:
    """Return top-ranked members from the currently active universe snapshot."""
    snap = get_active_universe_snapshot()
    if not snap:
        return []
    return list_universe_members(snap["id"], limit=limit)


def get_latest_feature_snapshot(asset_id: str) -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT * FROM universe_feature_snapshots WHERE asset_id = ? ORDER BY ts DESC LIMIT 1",
        (asset_id,),
    ).fetchone()
    return dict(row) if row else None


def list_opportunities_by_member(member_id: str, limit: int = 5) -> list[dict]:
    return _select_all(
        "opportunities",
        where="universe_member_id = ?",
        params=(member_id,),
        order="created_at DESC",
        limit=limit,
    )


def insert_universe_policy(policy: UniversePolicy) -> None:
    _insert("universe_policies", policy.model_dump())


def get_active_universe_policy(market_type: str = "crypto") -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT * FROM universe_policies WHERE active = 1 AND market_type = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (market_type,),
    ).fetchone()
    return dict(row) if row else None


def list_universe_snapshots(market_type: str = "crypto", limit: int = 20) -> list[dict]:
    return _select_all(
        "universe_snapshots",
        where="market_type = ?",
        params=(market_type,),
        order="created_at DESC",
        limit=limit,
    )


# ---------------------------------------------------------------------------
# Universe Forward Outcomes
# ---------------------------------------------------------------------------

_OUTCOME_MARKET_SQL = (
    "CASE WHEN market_type IN ('perp','futures','swap','usdt_futures') "
    "THEN 'perp' ELSE COALESCE(market_type, 'spot') END"
)

def insert_forward_outcome(outcome) -> None:
    _insert("universe_forward_outcomes", outcome.model_dump())


def insert_forward_outcomes_batch(outcomes: list) -> None:
    db = get_db()
    was_in_tx = db.in_transaction
    for o in outcomes:
        data = o.model_dump()
        cols = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        db.execute(
            f"INSERT INTO universe_forward_outcomes ({cols}) VALUES ({placeholders})",
            list(data.values()),
        )
    if not was_in_tx:
        db.commit()


def list_pending_forward_outcomes(before_ts: str, limit: int = 500) -> list[dict]:
    return _select_all(
        "universe_forward_outcomes",
        where="status = 'pending' AND due_at <= ?",
        params=(before_ts,),
        order="due_at ASC",
        limit=limit,
    )


def settle_forward_outcome(
    outcome_id: str,
    exit_price: float,
    forward_return: float,
    settled_at: str,
    *,
    economics: dict | None = None,
    settlement_price_source: str | None = None,
    settlement_lag_sec: float | None = None,
) -> None:
    updates = {
        "exit_price_usd": exit_price,
        "forward_return_pct": forward_return,
        "settled_at": settled_at,
        "status": "settled",
    }
    if economics:
        updates.update(economics)
    if settlement_price_source is not None:
        updates["settlement_price_source"] = settlement_price_source
    if settlement_lag_sec is not None:
        updates["settlement_lag_sec"] = settlement_lag_sec
    _update("universe_forward_outcomes", outcome_id, updates)


def fail_forward_outcome(outcome_id: str, settled_at: str) -> None:
    _update("universe_forward_outcomes", outcome_id, {
        "settled_at": settled_at,
        "status": "failed",
    })


def cancel_all_pending_forward_outcomes() -> int:
    """Batch-cancel all pending forward outcomes (used on venue/strategy switch)."""
    db = get_db()
    was_in_tx = db.in_transaction
    result = db.execute(
        "UPDATE universe_forward_outcomes SET status = 'cancelled' WHERE status = 'pending'",
    )
    if not was_in_tx:
        db.commit()
    return result.rowcount


def list_forward_outcomes_by_member(member_id: str) -> list[dict]:
    return _select_all(
        "universe_forward_outcomes",
        where="member_id = ?",
        params=(member_id,),
        order="horizon ASC",
    )


# ---------------------------------------------------------------------------
# Opportunity filter evidence
# ---------------------------------------------------------------------------

def insert_filter_decision(decision) -> bool:
    """Insert one immutable filter fact; duplicate dedup keys are ignored."""
    data = decision.model_dump()
    db = get_db()
    was_in_tx = db.in_transaction
    cols = ", ".join(data.keys())
    placeholders = ", ".join("?" for _ in data)
    cur = db.execute(
        f"INSERT OR IGNORE INTO filter_decisions ({cols}) VALUES ({placeholders})",
        list(data.values()),
    )
    if not was_in_tx:
        db.commit()
    return bool(cur.rowcount)


def insert_filter_decision_batch(batch) -> bool:
    """Insert one immutable compressed filter batch."""
    data = batch.model_dump()
    db = get_db()
    was_in_tx = db.in_transaction
    cols = ", ".join(data.keys())
    placeholders = ", ".join("?" for _ in data)
    cur = db.execute(
        f"INSERT OR IGNORE INTO filter_decision_batches ({cols}) VALUES ({placeholders})",
        list(data.values()),
    )
    if not was_in_tx:
        db.commit()
    return bool(cur.rowcount)


def insert_filter_candidate_outcomes_batch(outcomes: list) -> int:
    """Register fixed-horizon candidate outcomes without duplicating a candidate."""
    if not outcomes:
        return 0
    db = get_db()
    was_in_tx = db.in_transaction
    inserted = 0
    for outcome in outcomes:
        data = outcome.model_dump()
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" for _ in data)
        cur = db.execute(
            f"INSERT OR IGNORE INTO filter_candidate_outcomes ({cols}) VALUES ({placeholders})",
            list(data.values()),
        )
        inserted += max(0, cur.rowcount or 0)
    if not was_in_tx:
        db.commit()
    return inserted


def list_pending_filter_candidate_outcomes(
    before_ts: str,
    limit: int = 200,
) -> list[dict]:
    return _select_all(
        "filter_candidate_outcomes",
        where="status = 'pending' AND due_at <= ?",
        params=(before_ts,),
        order="due_at ASC",
        limit=limit,
    )


def settle_filter_candidate_outcome(
    outcome_id: str,
    *,
    exit_price: float,
    settled_at: str,
    economics: dict,
    settlement_price_source: str | None,
    settlement_lag_sec: float | None,
) -> None:
    updates = {
        "exit_price_usd": exit_price,
        "settled_at": settled_at,
        "status": "settled",
        **economics,
    }
    if settlement_price_source is not None:
        updates["settlement_price_source"] = settlement_price_source
    if settlement_lag_sec is not None:
        updates["settlement_lag_sec"] = settlement_lag_sec
    _update("filter_candidate_outcomes", outcome_id, updates)


def fail_filter_candidate_outcome(outcome_id: str, settled_at: str) -> None:
    _update("filter_candidate_outcomes", outcome_id, {
        "settled_at": settled_at,
        "status": "failed",
    })


def settle_linked_shadow_outcomes(
    outcome_candidate_key: str,
    *,
    exit_price: float,
    settled_at: str,
    economics: dict,
    settlement_price_source: str | None,
    settlement_lag_sec: float | None,
) -> int:
    """Project a settled shared 24h result onto linked shadow rows."""
    db = get_db()
    was_in_tx = db.in_transaction
    cur = db.execute(
        "UPDATE shadow_trades SET exit_price=?, pnl_pct=?, gross_return_pct=?, "
        "net_pnl_pct=?, estimated_fee_pct=?, estimated_spread_slippage_pct=?, "
        "estimated_funding_pct=?, min_tradable_edge_pct=?, "
        "settlement_price_source=?, settlement_lag_sec=?, cost_model_version=?, "
        "resolved_at=? WHERE outcome_candidate_key=? AND resolved_at IS NULL",
        (
            exit_price,
            economics.get("directional_return_pct"),
            economics.get("gross_return_pct"),
            economics.get("net_return_pct"),
            economics.get("estimated_fee_pct"),
            economics.get("estimated_spread_slippage_pct"),
            economics.get("estimated_funding_pct"),
            economics.get("min_tradable_edge_pct"),
            settlement_price_source,
            settlement_lag_sec,
            economics.get("cost_model_version"),
            settled_at,
            outcome_candidate_key,
        ),
    )
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0


def mark_outlier_outcomes_failed(max_abs_return_pct: float = 1_000_000) -> int:
    """Mark settled rows with physically impossible returns as failed.

    Catches cross-symbol price contamination (e.g. BTC's price applied to PEPE
    when ccxt returns wrong data without raising). Idempotent — safe to call on
    every refresh; after the first pass it finds 0 rows and returns immediately.
    """
    db = get_db()
    result = db.execute(
        "UPDATE universe_forward_outcomes "
        "SET status = 'failed' "
        "WHERE status = 'settled' "
        "AND (ABS(forward_return_pct) > ? OR ABS(net_return_pct) > ?)",
        (max_abs_return_pct, max_abs_return_pct),
    )
    return result.rowcount


# --- Filter feedback controller (shadow recommendations) ---

def insert_controller_recommendation(rec: dict) -> None:
    """Append one shadow recommendation row. Never updates existing rows."""
    _insert("controller_recommendations", rec)


def list_controller_recommendations(limit: int = 200, run_id: str | None = None) -> list[dict]:
    """Most recent recommendations, optionally scoped to one observe run."""
    if run_id:
        return _select_all(
            "controller_recommendations",
            where="run_id = ?", params=(run_id,),
            order="created_at DESC", limit=limit,
        )
    return _select_all("controller_recommendations", order="created_at DESC", limit=limit)


def latest_controller_run_id() -> str | None:
    """run_id of the most recent observe cycle, or None if the table is empty."""
    db = get_db()
    row = db.execute(
        "SELECT run_id FROM controller_recommendations "
        "WHERE run_id IS NOT NULL ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    return row["run_id"] if row else None


def calibration_stats() -> list[dict]:
    """Aggregate cost-aware returns by family + direction + market + horizon.

    Legacy gross-only rows are intentionally excluded. A win must clear the
    persisted minimum tradable edge after fees, spread/slippage and funding.
    """
    db = get_db()
    rows = db.execute(
        "SELECT scanner_family, direction, "
        f"{_OUTCOME_MARKET_SQL} AS market_type, horizon, "
        "COUNT(*) as n, "
        "AVG(net_return_pct) as avg_return, "
        "SUM(CASE WHEN net_return_pct > COALESCE(min_tradable_edge_pct, 0) "
        "  THEN 1 ELSE 0 END) as win_count, "
        "MIN(net_return_pct) as min_return, "
        "MAX(net_return_pct) as max_return "
        "FROM universe_forward_outcomes "
        "WHERE status = 'settled' "
        "AND net_return_pct IS NOT NULL "
        "AND ABS(net_return_pct) <= 100 "
        "AND direction IN ('long', 'short') "
        f"GROUP BY scanner_family, direction, {_OUTCOME_MARKET_SQL}, horizon "
        "ORDER BY scanner_family, direction, market_type, horizon"
    ).fetchall()
    return [dict(r) for r in rows]


def family_direction_edge(horizon: str = "24h", within_days: int = 7) -> list[dict]:
    """Per-(scanner_family, direction, market_type) cost-aware edge.

    Thesis evidence for the System Operator. Like calibration_stats(), this keeps
    long/short and spot/perpetual evidence separate; unlike calibration_stats(),
    it selects one horizon and recent time window for decision-time context.
    Reuses the existing universe_forward_outcomes store and excludes legacy
    gross-only rows.

    Returns rows: {scanner_family, direction, market_type, n, win_rate, avg_return}.
    Only families with n >= 1 in the window are returned.
    """
    db = get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=within_days)).isoformat()
    rows = db.execute(
        "SELECT scanner_family, direction, "
        f"{_OUTCOME_MARKET_SQL} AS market_type, "
        "COUNT(*) as n, "
        "AVG(net_return_pct) as avg_return, "
        "SUM(CASE WHEN net_return_pct > COALESCE(min_tradable_edge_pct, 0) "
        "  THEN 1 ELSE 0 END) as win_count "
        "FROM universe_forward_outcomes "
        "WHERE status = 'settled' AND horizon = ? "
        "AND settled_at >= ? AND net_return_pct IS NOT NULL "
        "AND ABS(net_return_pct) <= 100 "
        "AND scanner_family IS NOT NULL "
        "AND direction IN ('long', 'short') "
        f"GROUP BY scanner_family, direction, {_OUTCOME_MARKET_SQL} "
        "ORDER BY n DESC",
        (horizon, cutoff),
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        n = d.get("n") or 0
        d["win_rate"] = round((d.get("win_count") or 0) / n * 100, 1) if n else None
        d["avg_return"] = round(d["avg_return"], 3) if d.get("avg_return") is not None else None
        out.append(d)
    return out


def guard_candidate_exists_recent(
    symbol: str,
    scanner_family: str,
    within_days: int = 7,
    *,
    direction: str | None = None,
    market_type: str | None = None,
) -> bool:
    """Return whether the same scoped auto-guard candidate already exists."""
    db = get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=within_days)).isoformat()
    title_pattern = f"%{symbol}%{scanner_family}%"
    if direction:
        title_pattern += f"{direction}%"
    if market_type:
        title_pattern += f"{market_type}%"
    row = db.execute(
        "SELECT 1 FROM knowledge_items "
        "WHERE item_type = 'guard_candidate' "
        "AND title LIKE ? "
        "AND created_at >= ? "
        "LIMIT 1",
        (title_pattern, cutoff),
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Data Collectors (market data)
# ---------------------------------------------------------------------------

def insert_orderbook_snapshot(data: dict) -> None:
    from aletheia.storage.models import new_id
    if "id" not in data:
        data["id"] = new_id()
    _insert("orderbook_snapshots", data)


def latest_orderbook(symbol: str) -> dict | None:
    rows = _select_all(
        "orderbook_snapshots",
        where="symbol = ?",
        params=(symbol,),
        order="observed_at DESC",
        limit=1,
    )
    return rows[0] if rows else None


def insert_funding_rate_snapshot(data: dict) -> None:
    from aletheia.storage.models import new_id
    if "id" not in data:
        data["id"] = new_id()
    _insert("funding_rate_snapshots", data)


# Cross-venue / secondary feeds (Hyperliquid spot venue + its PREDICTED funding)
# share the funding/OI tables via the `exchange` tag, but must NOT win the
# per-symbol "latest" read that the LLM context, universe features and the
# primary collector's own delta all consume as the PRIMARY trading venue's
# view. Excluded by default; pass exchange=<name> to target one venue exactly.
_CROSS_VENUE_TAGS = ("hyperliquid", "hyperliquid_predicted")


def _latest_deriv_where(exchange: str | None) -> tuple[str, tuple]:
    """Build (where, params) for a per-symbol latest derivatives read.

    exchange=None  → primary view: any venue EXCEPT the cross-venue secondaries.
    exchange="x"   → exactly that venue's rows.
    """
    if exchange is not None:
        return "symbol = ? AND exchange = ?", (exchange,)
    placeholders = ",".join("?" * len(_CROSS_VENUE_TAGS))
    return (
        f"symbol = ? AND (exchange IS NULL OR exchange NOT IN ({placeholders}))",
        _CROSS_VENUE_TAGS,
    )


def latest_funding_rate(symbol: str, exchange: str | None = None) -> dict | None:
    where_tail, extra = _latest_deriv_where(exchange)
    rows = _select_all(
        "funding_rate_snapshots",
        where=where_tail,
        params=(symbol, *extra),
        order="observed_at DESC",
        limit=1,
    )
    return rows[0] if rows else None


def insert_open_interest_snapshot(data: dict) -> None:
    from aletheia.storage.models import new_id
    if "id" not in data:
        data["id"] = new_id()
    _insert("open_interest_snapshots", data)


def latest_open_interest(symbol: str, exchange: str | None = None) -> dict | None:
    where_tail, extra = _latest_deriv_where(exchange)
    rows = _select_all(
        "open_interest_snapshots",
        where=where_tail,
        params=(symbol, *extra),
        order="observed_at DESC",
        limit=1,
    )
    return rows[0] if rows else None


def insert_liquidation_event(data: dict) -> None:
    from aletheia.storage.models import new_id
    if "id" not in data:
        data["id"] = new_id()
    _insert("liquidation_events", data)


def recent_liquidations(symbol: str, hours: int = 1) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    return _select_all(
        "liquidation_events",
        where="symbol = ? AND event_time > ?",
        params=(symbol, cutoff),
        order="event_time DESC",
        limit=200,
    )


def insert_perp_structure_snapshot(data: dict) -> None:
    """Persist one observation-only perp structure snapshot."""
    from aletheia.storage.models import new_id
    if "id" not in data:
        data["id"] = new_id()
    if "raw_json" not in data:
        data["raw_json"] = "{}"
    _insert("perp_structure_snapshots", data)


def latest_perp_structure(symbol: str, source: str | None = None) -> dict | None:
    """Return the newest perp structure row for a symbol."""
    if source is not None:
        rows = _select_all(
            "perp_structure_snapshots",
            where="symbol = ? AND source = ?",
            params=(symbol, source),
            order="as_of_time DESC, observed_at DESC",
            limit=1,
        )
    else:
        rows = _select_all(
            "perp_structure_snapshots",
            where="symbol = ?",
            params=(symbol,),
            order="as_of_time DESC, observed_at DESC",
            limit=1,
        )
    return rows[0] if rows else None


def insert_macro_snapshot(data: dict) -> None:
    from aletheia.storage.models import new_id
    if "id" not in data:
        data["id"] = new_id()
    _insert("macro_snapshots", data)


def latest_macro_snapshot() -> dict | None:
    rows = _select_all("macro_snapshots", order="observed_at DESC", limit=1)
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# Round 2 — Step 3: CoinGecko Top-N
# Bulk insert uses canonical BEGIN/COMMIT (mirrors insert_serenity_claims_bulk).
# DO NOT replace with per-row _insert() — sqlite.py opens connections with
# isolation_level=None (autocommit), so a per-row loop becomes an N-commit storm.
# ---------------------------------------------------------------------------

def insert_coingecko_top_rows(rows: list[dict]) -> int:
    if not rows:
        return 0
    from aletheia.storage.models import new_id
    db = get_db()
    already_in_tx = db.in_transaction
    if not already_in_tx:
        db.execute("BEGIN")
    try:
        n = 0
        for row in rows:
            if "id" not in row:
                row["id"] = new_id()
            cols = ", ".join(row.keys())
            placeholders = ", ".join(["?"] * len(row))
            db.execute(
                f"INSERT INTO coingecko_top_snapshots ({cols}) VALUES ({placeholders})",
                list(row.values()),
            )
            n += 1
        if not already_in_tx:
            db.commit()
        return n
    except Exception:
        if not already_in_tx:
            db.rollback()
        raise


def latest_coingecko_top(top_n: int = 50) -> list[dict]:
    """Return the most recent batch's top_n rows, all sharing one observed_at.

    Two-step read pins to MAX(observed_at) first — otherwise a top_n=50 read
    could mix two adjacent batches if 100 rows landed in one tick (per spec MS-C04).
    """
    db = get_db()
    latest_row = db.execute(
        "SELECT MAX(observed_at) AS m FROM coingecko_top_snapshots"
    ).fetchone()
    if not latest_row or not latest_row["m"]:
        return []
    return _select_all(
        "coingecko_top_snapshots",
        where="observed_at = ?",
        params=(latest_row["m"],),
        order="market_cap_rank ASC",
        limit=top_n,
    )


def prune_coingecko_top_snapshots(days: int = 14) -> int:
    """~14k rows/day * 14d default ≈ 200k row ceiling; canonical was_in_tx pattern."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    was_in_tx = db.in_transaction
    cur = db.execute(
        "DELETE FROM coingecko_top_snapshots WHERE observed_at < ?", (cutoff,))
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0


def prune_orderbook_snapshots(days: int = 7) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    was_in_tx = db.in_transaction
    cur = db.execute("DELETE FROM orderbook_snapshots WHERE observed_at < ?", (cutoff,))
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0


def prune_perp_structure_snapshots(days: int = 30) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    was_in_tx = db.in_transaction
    cur = db.execute("DELETE FROM perp_structure_snapshots WHERE observed_at < ?", (cutoff,))
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0


# ---------------------------------------------------------------------------
# Round 2 — Step 5: DefiLlama on-chain snapshots
# ---------------------------------------------------------------------------

def insert_onchain_snapshot(data: dict) -> None:
    """Insert one onchain_snapshots row (stablecoin_mcap | chain_tvl | dex_volume).

    Follows the was_in_tx pattern: sqlite.py opens connections with
    isolation_level=None (autocommit), so we must explicitly BEGIN/COMMIT
    only when NOT already inside a transaction started by the caller.
    """
    from aletheia.storage.models import new_id
    if "id" not in data:
        data["id"] = new_id()
    _insert("onchain_snapshots", data)


def latest_onchain_snapshots(
    metric_type: str | None = None,
    source: str = "defillama",
    limit: int = 1,
) -> list[dict]:
    """Return the most recent onchain_snapshots rows.

    metric_type: filter to a specific type (stablecoin_mcap / chain_tvl / dex_volume).
                 None = return latest of all types (may be mixed).
    source: defaults to 'defillama'.
    limit: number of rows to return per (source, metric_type) pair.
    """
    if metric_type:
        return _select_all(
            "onchain_snapshots",
            where="source = ? AND metric_type = ?",
            params=(source, metric_type),
            order="observed_at DESC",
            limit=limit,
        )
    return _select_all(
        "onchain_snapshots",
        where="source = ?",
        params=(source,),
        order="observed_at DESC",
        limit=limit * 3,  # up to 3 metric types × limit rows each
    )


def prune_onchain_snapshots(days: int = 30) -> int:
    """Prune onchain_snapshots older than `days`. Three rows/cycle × 15min cadence
    ≈ 8640 rows/day — capped at 30d default (≈ 259k rows max).
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    was_in_tx = db.in_transaction
    cur = db.execute(
        "DELETE FROM onchain_snapshots WHERE observed_at < ?", (cutoff,)
    )
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0


def prune_prediction_probability_snapshots(days: int = 90) -> int:
    """#150: prune prediction_probability_snapshots older than `days`. Observation-only
    telemetry (prediction_observer, 30-min cadence, gated on prediction_enabled) that
    previously had no retention path."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    was_in_tx = db.in_transaction
    cur = db.execute("DELETE FROM prediction_probability_snapshots WHERE ts < ?", (cutoff,))
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0


def prune_liquidation_events(days: int = 7) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    was_in_tx = db.in_transaction
    cur = db.execute("DELETE FROM liquidation_events WHERE observed_at < ?", (cutoff,))
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0


def prune_funding_rate_snapshots(days: int = 30) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    was_in_tx = db.in_transaction
    cur = db.execute("DELETE FROM funding_rate_snapshots WHERE observed_at < ?", (cutoff,))
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0


def prune_open_interest_snapshots(days: int = 30) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    was_in_tx = db.in_transaction
    cur = db.execute("DELETE FROM open_interest_snapshots WHERE observed_at < ?", (cutoff,))
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0

def prune_universe_feature_snapshots(days: int = 30) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    was_in_tx = db.in_transaction
    cur = db.execute("DELETE FROM universe_feature_snapshots WHERE ts < ?", (cutoff,))
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0


def prune_universe_forward_outcomes(days: int = 30) -> int:
    """Prune forward-return outcome labels older than `days` (by entry_ts).

    Analytics/telemetry — NOT a causal-chain table. Rows reference universe
    snapshots that are themselves pruned at 30d, so older outcomes are orphaned
    anyway; 30d keeps the retention windows aligned. (DB-SIZE / B-sweep gap #1.)
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    was_in_tx = db.in_transaction
    cur = db.execute("DELETE FROM universe_forward_outcomes WHERE entry_ts < ?", (cutoff,))
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0

def prune_macro_snapshots(days: int = 90) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    was_in_tx = db.in_transaction
    cur = db.execute("DELETE FROM macro_snapshots WHERE observed_at < ?", (cutoff,))
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0


def save_volume_ranking_snapshot(symbols: list, observed_at: str) -> None:
    import json
    db = get_db()
    was_in_tx = db.in_transaction
    db.execute(
        "INSERT INTO volume_ranking_snapshots (symbols_json, observed_at) VALUES (?, ?)",
        (json.dumps(symbols), observed_at),
    )
    if not was_in_tx:
        db.commit()
    db.execute(
        "DELETE FROM volume_ranking_snapshots WHERE id NOT IN ("
        "  SELECT id FROM volume_ranking_snapshots ORDER BY observed_at DESC LIMIT 5"
        ")"
    )
    if not was_in_tx:
        db.commit()


def get_volume_ranking_snapshot() -> dict:
    import json
    db = get_db()
    row = db.execute(
        "SELECT symbols_json, observed_at FROM volume_ranking_snapshots"
        " ORDER BY observed_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return {}
    return {"symbols": json.loads(row[0]), "observed_at": row[1]}


# agent_shell_* telemetry pruning. These tables are liveness/working-memory
# telemetry, NOT causal-chain tables (so they are intentionally NOT in the
# _PRUNABLE causal whitelist — see test_causal_tables_not_in_prunable_whitelist).
# Wired into scheduler._run_cleanup; the prune call-sites existed but the repo
# functions were omitted (incomplete refactor ba80a62), which raised
# AttributeError mid-cleanup and silently skipped the data_pruned audit write.
def prune_agent_shell_heartbeats(days: int = 2) -> int:
    """Prune old agent_shell_heartbeats rows (latest-row-only liveness telemetry)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    was_in_tx = db.in_transaction
    cur = db.execute("DELETE FROM agent_shell_heartbeats WHERE created_at < ?", (cutoff,))
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0


def prune_agent_shell_events(days: int = 14) -> int:
    """Prune old agent_shell_events rows (telemetry, no causal-chain value)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    was_in_tx = db.in_transaction
    cur = db.execute("DELETE FROM agent_shell_events WHERE created_at < ?", (cutoff,))
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0


def prune_agent_shell_messages(days: int = 30) -> int:
    """Prune old agent_shell_messages rows (working-memory conversation log)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    was_in_tx = db.in_transaction
    cur = db.execute("DELETE FROM agent_shell_messages WHERE created_at < ?", (cutoff,))
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0


def prune_agent_shell_artifacts(days: int = 30) -> int:
    """Prune old agent_shell_artifacts rows (task output telemetry)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    was_in_tx = db.in_transaction
    cur = db.execute("DELETE FROM agent_shell_artifacts WHERE created_at < ?", (cutoff,))
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0


def expire_stale_opportunities(ttl_hours: int = 48) -> int:
    """Mark open opportunities older than ttl_hours as 'expired'.

    Also expires any opportunity whose valid_until has passed, regardless of
    age — IB-24-13: short-TTL families (e.g. momentum 30 min) were piling up
    as 'open' for 48h before the age cutoff fired.

    Traded/reviewed opportunities are not affected.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=ttl_hours)).isoformat()
    db = get_db()
    was_in_tx = db.in_transaction
    cur = db.execute(
        "UPDATE opportunities SET status = 'expired'"
        " WHERE status = 'open'"
        " AND (created_at < ? OR (valid_until IS NOT NULL AND valid_until < ?))",
        (cutoff, now_iso),
    )
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0


# ── Edge Lab: Shadow Trades ──────────────────────────────────────────────────

def insert_shadow_trade(shadow) -> None:
    from aletheia.storage.models import ShadowTrade
    data = shadow.model_dump() if hasattr(shadow, "model_dump") else dict(shadow)
    _insert("shadow_trades", data)


def update_shadow_trade(shadow_id: str, **kwargs) -> None:
    _update("shadow_trades", shadow_id, kwargs)


def list_shadow_trades(
    batch_id: str | None = None,
    scanner_family: str | None = None,
    resolved: bool | None = None,
    limit: int = 200,
) -> list[dict]:
    conditions: list[str] = []
    params: list = []
    if batch_id is not None:
        conditions.append("batch_id = ?")
        params.append(batch_id)
    if scanner_family is not None:
        conditions.append("scanner_family = ?")
        params.append(scanner_family)
    if resolved is True:
        conditions.append("resolved_at IS NOT NULL")
    elif resolved is False:
        conditions.append("resolved_at IS NULL")
    where = " AND ".join(conditions) if conditions else None
    return _select_all("shadow_trades", where=where, params=tuple(params), limit=limit)


# ── Market Regime ─────────────────────────────────────────────────────────────

def insert_market_regime(record) -> None:
    from aletheia.storage.models import MarketRegimeRecord
    data = record.model_dump() if hasattr(record, "model_dump") else dict(record)
    if isinstance(data.get("signals"), dict):
        data["signals"] = json.dumps(data["signals"])
    _insert("market_regimes", data)


def get_latest_market_regime() -> dict | None:
    rows = _select_all("market_regimes", order="detected_at DESC", limit=1)
    if not rows:
        return None
    row = rows[0]
    if row.get("signals"):
        try:
            row["signals"] = json.loads(row["signals"])
        except (json.JSONDecodeError, TypeError):
            pass
    return row


def list_market_regimes(limit: int = 50) -> list[dict]:
    rows = _select_all("market_regimes", order="detected_at DESC", limit=limit)
    for row in rows:
        if row.get("signals"):
            try:
                row["signals"] = json.loads(row["signals"])
            except (json.JSONDecodeError, TypeError):
                pass
    return rows


# ── Universe Feature Snapshots (batch read) ───────────────────────────────────

def list_universe_feature_snapshots(limit: int = 50) -> list[dict]:
    """Return recent feature snapshots across all assets (newest first)."""
    return _select_all("universe_feature_snapshots", order="ts DESC", limit=limit)
