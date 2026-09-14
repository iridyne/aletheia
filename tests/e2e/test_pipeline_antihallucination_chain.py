"""Verified external outcome → case study → FTS retrieval, with adopted exclusion.

The shared fixture seeds an attributable external entry and closes it through the
production outcome producer. Adoption, case building and retrieval use their real
implementations. Exchange submission and Risk have separate pipeline coverage.
"""

from datetime import datetime, timezone

from aletheia.storage import repositories as repo


class TestAntiHallucinationChain:
    def test_full_chain_reaches_pool_broken_chain_excluded(self, e2e_db):
        from aletheia.config.loader import load_config, save_config
        from tests.test_outcome_closure import _insert_closed_position_via_producer

        cfg = load_config()
        cfg.exchange.paper_execution_mode = "bitget_demo"
        save_config(cfg)

        btc_pos = "native-btc-close"
        pos_row = _insert_closed_position_via_producer(
            position_id=btc_pos, symbol="BTC/USDT", market_type="spot",
            direction="long", quantity=0.001, entry_price=60000.0,
            execution_mode="bitget_demo", realized_pnl=3.0,
            close_reason="take_profit", scanner_family="momentum",
            confidence="high", confidence_score=80,
            closed_at=datetime.now(timezone.utc).isoformat(),
        )
        assert pos_row["opportunity_id"]
        assert pos_row["proposal_id"]
        assert pos_row["strategy_id"] is None

        # Adoption records a real external position without inventing native
        # entry attribution or historical Fill economics.
        from aletheia.portfolio.position_reconciler import adopt_position
        from aletheia.portfolio.outcome_closure import close_position_from_verified_exit
        from aletheia.storage.models import Position
        from tests.test_outcome_closure import _verified_result

        adopted = adopt_position({
            "symbol": "ETH/USDT", "side": "long", "market_type": "spot",
            "quantity": 2.0, "entry_price": 100.0,
            "mark_price": 100.0, "unrealized_pnl": 0.0,
            "cTime": int(datetime.now(timezone.utc).timestamp() * 1000) - 3_600_000,
        }, account_mode="demo")
        eth_pos = adopted["position_id"]
        evidence = _verified_result(Position.model_validate(repo.get_position(eth_pos)))
        close_position_from_verified_exit(eth_pos, evidence)
        eth_row = repo.get_position(eth_pos)
        assert eth_row["source"] == "adopted"
        assert eth_row["opportunity_id"] is None
        assert eth_row["status"] == "closed"
        assert eth_row["exit_fill_verified"] == 1
        assert eth_row["realized_pnl"] is None
        assert eth_row["outcome_eligible"] == 0
        assert eth_row["outcome_exclusion_reason"] == "incomplete_realized_economics"

        # ── Build case studies from the real closed positions ──
        from extensions.learning.trade_case_builder import build_case_studies
        result = build_case_studies()
        by_pos = {c["position_id"]: c["case_study_id"] for c in result["created"]}
        assert btc_pos in by_pos, "full-chain closed trade must produce a case study"
        assert eth_pos not in by_pos, "broken-chain trade must not enter the learning corpus"

        # ── The gate: only the fully attributed chain becomes a case study ──
        btc_item = repo._select_one("knowledge_items", by_pos[btc_pos])
        assert btc_item["confidence"] == "high_confidence", (
            "a fully-attributed trade (opportunity+proposal) must be high_confidence"
        )

        # ── The pool: only the real, attributable case can surface ──
        from aletheia.knowledge.retrieval import retrieve_lessons
        lessons = retrieve_lessons("momentum win take_profit", k=10,
                                   item_types=["trade_case_study"])
        titles = " | ".join(l.title for l in lessons)
        assert any("BTC" in l.title for l in lessons), (
            f"full-chain case must be retrievable from the pool; got: {titles}"
        )
        assert not any("ETH" in l.title for l in lessons), (
            f"broken-chain outcome must be kept OUT of the pool; got: {titles}"
        )
