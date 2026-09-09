"""E2E: guard self-evolution chain under the P0 shadow-only regime (2026-07-12).

History: the Tier-1 guard chain was once dead (L-Bug-D: 105 candidates, 0 promotions);
it was repaired, then over-corrected — auto guards auto-promoted from bare historical
loss-rate correlation into active HARD BLOCKS while their bodies claimed "requires human
review before promotion", bypassing the Learning experiment/authority chain entirely.

The corrected chain (verified end-to-end here, real DB, no module mocking):
  1. an auto-generated candidate is reachable by load_trading_guards(status="candidate")
     and is created SHADOW-ONLY (observe-only);
  2. with a repeated losing pattern AND auto_promote_enabled=True, run_promotion_check
     still does NOT promote it — a learned guard needs experiment authority
     (ranker_learned_guard: AT-01 verdict + AT-02 path authority), which is fail-closed;
  3. the promotion MECHANISM itself is intact: an owner-authored (manual, non-shadow)
     candidate can still be promoted via the explicit owner route.
"""

from datetime import datetime, timezone



def _losing_momentum_long_trade(i: int):
    """One closed momentum/long trade that lost money, with the full causal chain
    so build_trade_attribution_rows surfaces scanner_family + direction."""
    from tests.test_outcome_closure import _insert_closed_position_via_producer

    return _insert_closed_position_via_producer(
        position_id=f"loss-{i}", symbol="SOL/USDT", market_type="usdt_futures",
        direction="long", quantity=1.0, entry_price=100.0,
        execution_mode="bitget_demo", realized_pnl=-5.0,
        close_reason="stop_loss", scanner_family="momentum",
        closed_at=datetime.now(timezone.utc).isoformat(),
    )


class TestGuardPromotionChain:
    def test_auto_candidate_is_shadow_and_not_auto_promoted(self, e2e_db):
        from aletheia.knowledge.manager import create_guard_candidate
        from aletheia.knowledge.guards import load_trading_guards, guard_origin
        from aletheia.analytics.guard_promotion import run_promotion_check

        # 1) create an auto-style candidate (mirrors outcome_recorder's metadata)
        guard_id = "auto_momentum_long_solusdt"
        create_guard_candidate(
            title="Auto guard candidate: SOL/USDT momentum 4h loss",
            content="momentum/long on SOL/USDT showed repeated 4h losses",
            metadata={"guard_id": guard_id, "status": "candidate", "symbol": "SOL/USDT",
                      "scanner_family": "momentum", "direction": "long", "auto_generated": True,
                      "predicates": [{"field": "scanner_family", "op": "eq", "value": "momentum"}]},
        )

        # 2) reachable by the promoter, classified learned, and FORCED shadow-only at creation
        cands = load_trading_guards(status="candidate")
        mine = [g for g in cands if g.guard_id == guard_id]
        assert mine, f"candidate not reachable: {[g.guard_id for g in cands]}"
        g = mine[0]
        assert guard_origin(g) == "learned"
        assert g.shadow_only is True, "auto candidate must be created shadow-only"
        assert g.applies_to.symbol == ["SOL/USDT"]

        # 3) a repeated losing momentum/long pattern (>= min_matching_trades=3, 100% loss)
        for i in range(3):
            _losing_momentum_long_trade(i)

        # 4) a healthy durable reviewer checkpoint (so refreeze does not mask the result)
        from aletheia.learning.control import record_learning_job_success
        record_learning_job_success("lesson_reviewer")

        # 5) even with auto_promote_enabled=True and strong loss evidence, a learned
        #    shadow guard is NOT auto-promoted — it lacks experiment authority (fail-closed).
        from aletheia.config.loader import load_config, save_config
        cfg = load_config()
        cfg.exchange.paper_execution_mode = "bitget_demo"
        cfg.guard.auto_promote_enabled = True
        save_config(cfg)
        result = run_promotion_check()
        assert result.get("promotions", 0) == 0, f"learned guard must not auto-promote: {result}"

        # 6) it stays a shadow candidate; it never became an active blocker
        assert not any(x.guard_id == guard_id for x in load_trading_guards(status="active"))
        still = [x for x in load_trading_guards(status="candidate") if x.guard_id == guard_id]
        assert still and still[0].shadow_only is True

    def test_manual_candidate_can_still_be_promoted_by_owner(self, e2e_db):
        """The promotion MECHANISM is intact: an owner-authored (non-shadow, non-learned)
        candidate is promotable via the explicit owner route."""
        from datetime import datetime, timedelta, timezone

        from aletheia.analytics.guard_promotion import (
            _deprecate_expired_learned_guards,
            promote_guard_candidate_to_active,
        )
        from aletheia.knowledge.document import (
            canonicalize_written_document,
            read_knowledge_document,
            resolve_knowledge_path,
        )
        from aletheia.knowledge.frontmatter import parse_frontmatter, render_frontmatter
        from aletheia.knowledge.guards import load_trading_guards, guard_origin
        from aletheia.knowledge.manager import create_guard_candidate
        from aletheia.storage import repositories as repo
        from aletheia.storage.repositories import get_knowledge_item

        guard_id = "manual-sol-momentum-block"
        candidate = create_guard_candidate(
            title="Owner guard: block SOL momentum",
            content="Owner-authored SolGuardFtsNeedle block on SOL/USDT momentum longs.",
            metadata={"guard_id": guard_id, "status": "candidate", "symbol": "SOL/USDT",
                      "scanner_family": "momentum", "direction": "long",
                      "enforcement": "blocking", "block_candidate": True,
                      "predicates": [{"field": "scanner_family", "op": "eq", "value": "momentum"}]},
        )
        cand = [g for g in load_trading_guards(status="candidate") if g.guard_id == guard_id]
        assert cand and guard_origin(cand[0]) == "manual"
        assert cand[0].shadow_only is False  # manual candidate not forced shadow

        assert promote_guard_candidate_to_active(
            guard_id, actor_type="human", actor_id="owner", reason="owner_review") is True
        active = [g for g in load_trading_guards(status="active") if g.guard_id == guard_id]
        assert active, "owner-authored guard should promote to active"
        assert active[0].block_candidate is True
        assert candidate is not None
        candidate_row = get_knowledge_item(candidate.id)
        assert candidate_row is not None
        assert candidate_row["status"] == "deprecated"
        assert read_knowledge_document(candidate_row).state == "available"
        active_rows = repo._select_all(
            "knowledge_items",
            where="item_type = ? AND status = ?",
            params=("trading_guard", "active"),
            limit=10,
        )
        assert len(active_rows) == 1
        active_row = active_rows[0]
        indexed = e2e_db.execute(
            "SELECT body FROM knowledge_fts WHERE item_id = ?", (active_row["id"],)
        ).fetchone()
        assert indexed is not None
        assert "SolGuardFtsNeedle" in indexed["body"]
        active_path = resolve_knowledge_path(active_row["file_path"])
        assert active_path is not None
        metadata, body = parse_frontmatter(active_path)
        metadata["auto_generated"] = True
        metadata["tags"] = ["auto-generated", "shadow-only"]
        metadata["expires_at"] = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
        active_path.write_text(render_frontmatter(metadata, body), encoding="utf-8")
        repo.update_knowledge_item(
            active_row["id"], **canonicalize_written_document(active_path)
        )

        assert _deprecate_expired_learned_guards() == 1
        expired_row = get_knowledge_item(active_row["id"])
        assert expired_row is not None
        assert expired_row["status"] == "deprecated"
        assert read_knowledge_document(expired_row).state == "available"

    def test_event_failure_does_not_publish_guard_state(self, e2e_db, monkeypatch):
        """A failed durable provenance write leaves the candidate untouched."""
        from aletheia.knowledge.manager import create_guard_candidate
        from aletheia.knowledge.guards import load_trading_guards
        from aletheia.analytics.guard_promotion import promote_guard_candidate_to_active
        from aletheia.storage import repositories as repo
        from aletheia.storage.event_store import get_event_store

        guard_id = "manual-event-failure-guard"
        candidate = create_guard_candidate(
            title="Owner guard with failed audit",
            content="Owner-authored guard used to prove failed promotion is invisible.",
            metadata={
                "guard_id": guard_id,
                "status": "candidate",
                "scope": "paper_entry",
                "symbol": "SOL/USDT",
            },
        )

        def fail_append(_event):
            raise OSError("event store unavailable")

        monkeypatch.setattr(get_event_store(), "append", fail_append)

        assert promote_guard_candidate_to_active(guard_id, actor_type="human") is False
        assert not any(g.guard_id == guard_id for g in load_trading_guards(status="active"))
        source = [g for g in load_trading_guards(status="candidate") if g.guard_id == guard_id]
        assert source and source[0].status == "candidate"

        row = repo._select_one("knowledge_items", candidate.id)
        assert row["item_type"] == "guard_candidate"
        assert row["status"] == "active"
        assert row["confidence"] == "medium_confidence"
        assert not repo._select_all(
            "knowledge_items",
            where="item_type = ?",
            params=("trading_guard",),
            limit=10,
        )
        assert not get_event_store().list_by_aggregate("trading_guard", guard_id)
