"""Tests for the experience loop — read prior lessons back into decisions (§6).

Governance under test:
- READ-ONLY + QUALITY GATE: only high_confidence + active items are retrievable.
- ADVISORY: the injected block is labelled advisory and is pure prompt text.
- DEGRADES: missing FTS / no lessons => agents still run (no crash, no behavior break).
- OBSERVABLE: retrieval is logged as a tool call.
"""

import sqlite3
from datetime import datetime, timezone

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import MarketSnapshot
from aletheia.storage import repositories as repo
from aletheia.agent_runtime.tool_registry import init_tool_registry
from aletheia.agent_runtime.orchestrator import run_agent


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    for sub in ("logs", "knowledge/research_notes", "knowledge/post_trade_reviews",
                "knowledge/strategies", "knowledge/dreaming_logs", "playbooks"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    init_tool_registry()


# --- retrieval correctness + quality gate ---

def test_retrieve_only_high_confidence_active(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.knowledge.manager import create_research_note
    from aletheia.knowledge.retrieval import retrieve_lessons

    create_research_note("High conf funding lesson",
                         "Extreme perp funding rates signal crowded long positioning; reducing spot exposure is prudent risk management ahead of potential liquidation cascade.",
                         confidence="high_confidence")
    create_research_note("Medium conf note",
                         "Some speculative observation about funding rates and their potential impact on exposure management during volatile market conditions.",
                         confidence="medium_confidence")
    create_research_note("Speculative note",
                         "Speculative hypothesis: funding rate divergence between BTC and ETH perps may signal sector rotation rather than broad deleveraging event.",
                         confidence="speculative")

    res = retrieve_lessons("funding exposure positioning risk", k=5)
    assert len(res) == 1
    assert res[0].confidence == "high_confidence"
    reset_db()


def test_archived_item_not_recalled(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.knowledge.manager import create_research_note, archive_item
    from aletheia.knowledge.retrieval import retrieve_lessons

    item = create_research_note("Drawdown lesson",
                                "Tighten stops after three consecutive losses to limit drawdown exposure; max consecutive loss streak triggers mandatory position size reduction.",
                                confidence="high_confidence")
    assert len(retrieve_lessons("drawdown stops losses", k=5)) == 1
    archive_item(item.id)
    assert len(retrieve_lessons("drawdown stops losses", k=5)) == 0
    reset_db()


def test_memory_block_has_provenance(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.knowledge.manager import create_research_note
    from aletheia.knowledge.retrieval import retrieve_lessons, format_memory_block

    create_research_note("Volatility lesson",
                         "Volatility expands after contraction periods; sizing down into squeezes protects capital and preserves powder for the eventual breakout move.",
                         confidence="high_confidence")
    block = format_memory_block(retrieve_lessons("volatility squeeze size", k=3))
    assert "ADVISORY" in block
    assert "not instructions" in block.lower()
    assert "research_note" in block
    assert "high_confidence" in block


def test_empty_query_and_empty_index_return_nothing(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.knowledge.retrieval import retrieve_lessons, format_memory_block
    assert retrieve_lessons("", k=3) == []
    assert retrieve_lessons("anything at all", k=3) == []  # empty index
    assert format_memory_block([]) == ""
    reset_db()


def test_reindex_backfills_existing(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.knowledge.manager import create_research_note
    from aletheia.knowledge.retrieval import retrieve_lessons, reindex_all
    import aletheia.storage.sqlite as sq

    create_research_note("Trend lesson",
                         "In a confirmed uptrend, buying pullbacks to the 50-period moving average offers favorable risk-reward with clear invalidation level below the MA.",
                         confidence="high_confidence")
    # Wipe the FTS index to simulate pre-existing data written before the loop existed.
    sq.get_db().execute("DELETE FROM knowledge_fts")
    sq.get_db().commit()
    assert len(retrieve_lessons("uptrend pullback moving average", k=5)) == 0
    n = reindex_all()
    assert n >= 1
    assert len(retrieve_lessons("uptrend pullback moving average", k=5)) == 1
    reset_db()


# --- playbooks (read-only) ---

def test_load_playbooks_reads_seeds(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.trading.playbooks import seed_playbooks, load_playbooks, format_playbook_block
    seed_playbooks(tmp_path)
    pbs = load_playbooks()
    assert len(pbs) == 5
    block = format_playbook_block(pbs)
    assert "Playbooks" in block
    assert "read-only" in block.lower()
    reset_db()


# --- injection: observable + degrades safely ---

async def test_observer_logs_retrieval_and_survives_offline_llm(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config(); cfg.agent.permission_mode = "Observe"; save_config(cfg)

    from aletheia.knowledge.manager import create_research_note
    create_research_note("BTC regime lesson",
                         "When BTC loses the 200-day, treat rallies as distribution.",
                         confidence="high_confidence")
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTCUSDT", market_type="spot", price=60000.0))

    # LLM is offline in tests → observer must still complete (injection is prompt text).
    result = await run_agent("market_observer", trigger_type="manual")
    assert result["status"] in ("completed", "completed_with_errors")

    calls = repo.list_tool_calls(run_id=result["run_id"])
    assert any(c["tool_name"] == "retrieve_lessons" for c in calls), "retrieval must be observable"
    reset_db()


# --- producer-side promotion: substantive lessons reach the retrieval gate (§6) ---
# Producers write medium_confidence by default; only LLM-produced, evidence-backed
# reviews/critiques are promoted to high_confidence so they actually become retrievable.

def _seed_filled_short(strategy_id: str = "s1") -> None:
    from aletheia.storage.models import Position
    from tests.test_outcome_closure import _insert_entry_chain

    opportunity_id, proposal_id, order_id = _insert_entry_chain(
        position_id="position-experience-review",
        symbol="SOL/USDT",
        market_type="usdt_futures",
        direction="short",
        quantity=0.3,
        price=140.0,
        execution_mode="bitget_demo",
        strategy_id=strategy_id,
    )
    repo.insert_position(Position(
        id="position-experience-review",
        symbol="SOL/USDT",
        market_type="usdt_futures",
        direction="short",
        quantity=0.3,
        entry_price=140.0,
        current_price=130.0,
        realized_pnl=3.0,
        execution_mode="bitget_demo",
        strategy_id=strategy_id,
        proposal_id=proposal_id,
        order_id=order_id,
        opportunity_id=opportunity_id,
        status="closed",
        close_reason="take_profit",
        closed_at=datetime.now(timezone.utc).isoformat(),
        entry_fee=0.0,
        exit_fee=0.0,
        funding_cost=0.0,
        funding_source="broker_ledger",
        close_category="strategy",
        entry_price_source="broker_fill",
        entry_fill_verified=True,
        exit_price_source="broker_fill",
        exit_fill_verified=True,
        outcome_eligible=True,
        outcome_exclusion_reason="eligible",
        outcome_semantics_version="oi-v1",
    ))


def test_post_trade_review_confidence_gates_retrieval(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.knowledge.manager import create_post_trade_review
    from aletheia.knowledge.retrieval import retrieve_lessons

    # Identical bodies; only the high_confidence one may be recalled.
    body = "Trailing stop on the SOL short would have captured more downside; the exit was early."
    create_post_trade_review("High conf exit review", body, confidence="high_confidence")
    create_post_trade_review("Medium conf exit review", body, confidence="medium_confidence")

    res = retrieve_lessons("trailing stop downside exit", k=5)
    assert len(res) == 1
    assert res[0].confidence == "high_confidence"
    assert res[0].item_type == "post_trade_review"
    reset_db()


async def test_post_trade_reviewer_writes_medium_confidence_reviews(tmp_path, monkeypatch):
    """post_trade_review is a SOFT reflection → medium_confidence, never auto-high.

    Changed 2026-06-27 (LEARNING_LOOP_AUDIT §R1 / fix C1): reviews used to
    auto-upgrade to high_confidence and flooded the retrieval pool ~118:1 vs real
    trade_case_study cases, so the experience loop scored decisions against
    templated reflections. Reviews now stay medium so the high_confidence gate is
    led by attributed trade_case_study cases (which earn high via has_full_chain
    once the R0 opportunity_id fix lets the chain complete). This test locks the
    new contract: a substantive review is written, stays medium, and therefore
    does NOT enter the high_confidence retrieval gate.
    """
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config(); cfg.agent.permission_mode = "Observe"; save_config(cfg)
    from aletheia.knowledge.retrieval import retrieve_lessons
    _seed_filled_short()

    async def ok_llm(*a, **k):
        return ("Trailing stop on the SOL short would have captured more downside; exit was early.", None)
    monkeypatch.setattr("aletheia.agents.post_trade_reviewer.llm_complete", ok_llm)

    result = await run_agent("post_trade_reviewer", trigger_type="manual")
    assert result["status"] in ("completed", "completed_with_errors")

    items = repo.list_knowledge_items(item_type="post_trade_review")
    assert items and all(i["confidence"] == "medium_confidence" for i in items)
    # Medium reviews must NOT enter the high_confidence retrieval gate — that gate
    # is reserved for attributed trade_case_study cases.
    assert len(retrieve_lessons("trailing stop downside exit", item_types=["post_trade_review"], k=5)) == 0
    reset_db()


async def test_post_trade_reviewer_keeps_fallback_lessons_medium(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config(); cfg.agent.permission_mode = "Observe"; save_config(cfg)
    _seed_filled_short()

    async def down_llm(*a, **k):
        return ("", "LLM unavailable")
    monkeypatch.setattr("aletheia.agents.post_trade_reviewer.llm_complete", down_llm)

    result = await run_agent("post_trade_reviewer", trigger_type="manual")
    assert result["status"] in ("completed", "completed_with_errors")
    items = repo.list_knowledge_items(item_type="post_trade_review")
    # When LLM is offline, the agent skips writing reviews (no placeholder noise).
    # Any items that do exist must be medium_confidence (not retrieval-eligible).
    assert all(i["confidence"] == "medium_confidence" for i in items)
    reset_db()


async def test_risk_critic_writes_medium_confidence_only(tmp_path, monkeypatch):
    """risk_critic macro observations never enter the retrieval pool.

    Updated 2026-07-18: prior gate promoted the note to ``high_confidence`` when
    ANY proposal existed, which produced 39 vacuous "1/1 approved (100%)" stubs
    in production before this cleanup. Aggregate risk stats are advisory, not
    candidate-specific evidence — the audit row is kept as medium_confidence so
    ``retrieve_lessons`` (which gates on high_confidence) cannot serve it to a
    trading decision. Real trading lessons travel through ``trade_case_study``.
    """
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config(); cfg.agent.permission_mode = "Observe"; save_config(cfg)
    from aletheia.storage.models import TradeProposal
    from aletheia.knowledge.retrieval import retrieve_lessons
    repo.insert_trade_proposal(TradeProposal(id="p1", strategy_id="s1", symbol="BTC/USDT",
                                             market_type="futures", direction="long"))

    async def ok_llm(*a, **k):
        return ("Position sizing crept up after wins; cap risk per trade to limit drawdown clustering.", None)
    monkeypatch.setattr("aletheia.agents.risk_critic.llm_complete", ok_llm)

    result = await run_agent("risk_critic", trigger_type="manual")
    assert result["status"] in ("completed", "completed_with_errors")
    items = repo.list_knowledge_items(item_type="research_note")
    # Row exists (audit trail preserved) but medium_confidence, not retrieval-eligible.
    assert items, "risk_critic must still record an auditable research_note"
    assert all(i["confidence"] == "medium_confidence" for i in items), (
        "risk_critic macro observations must never be high_confidence — that gate "
        "produced audit-stub pollution before 2026-07-18"
    )
    # High-confidence retrieval finds nothing (the quality gate protects the pool).
    assert retrieve_lessons(
        "position sizing risk drawdown", item_types=["research_note"], k=5
    ) == []
    reset_db()
