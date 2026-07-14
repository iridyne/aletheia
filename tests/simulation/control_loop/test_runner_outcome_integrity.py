"""Economic and ownership boundaries for deterministic control-loop facts."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from aletheia.simulation.control_loop import ControlLoopHarness


def test_control_loop_harness_refuses_durable_project_data_dir(
    tmp_path, monkeypatch,
):
    import aletheia.config.loader as loader

    project_root = tmp_path / "project"
    durable_data_dir = project_root / "data"
    simulation_subdir = durable_data_dir / "sim"
    monkeypatch.setattr(loader, "_PROJECT_ROOT", project_root)

    with pytest.raises(ValueError, match="isolated data directory"):
        ControlLoopHarness(simulation_subdir)

    assert not durable_data_dir.exists()


@pytest.mark.real_db
def test_control_loop_harness_refuses_current_active_data_dir(
    tmp_path, monkeypatch,
):
    import aletheia.config.loader as loader

    active_data_dir = tmp_path / "active-runtime-data"
    active_data_dir.mkdir()
    active_config = active_data_dir / "config.yaml"
    active_config.write_text("owner: external-runtime\n", encoding="utf-8")
    monkeypatch.setattr(loader, "DATA_DIR", active_data_dir)
    monkeypatch.setattr(loader, "CONFIG_PATH", active_config)
    simulation_subdir = active_data_dir / "dcls"

    harness = None
    try:
        with pytest.raises(ValueError, match="isolated data directory"):
            harness = ControlLoopHarness(simulation_subdir)
    finally:
        if harness is not None:
            harness.close()

    assert active_config.read_text(encoding="utf-8") == "owner: external-runtime\n"
    assert not simulation_subdir.exists()
    assert not (active_data_dir / "app.db").exists()


def test_trade_performance_uses_reconciled_paper_closes_and_exact_fill_age(tmp_path):
    now = datetime(2026, 7, 13, 4, tzinfo=timezone.utc)
    with ControlLoopHarness(tmp_path, now=now) as harness:
        harness.change_trade_performance(
            win_rate=50.0,
            total=2,
            hours_since_last_fill=2.0,
        )

        from aletheia.analytics.outcome_integrity import eligible_strategy_outcomes
        from aletheia.portfolio import account_loss
        from aletheia.portfolio.account_loss import authoritative_account_realized_loss

        outcomes = eligible_strategy_outcomes()
        assert len(outcomes) == 2
        position_ids = {str(row["id"]) for row in outcomes}

        events = [
            dict(row) for row in harness.db.execute(
                "SELECT action, position_id, created_at FROM paper_account_events "
                "WHERE position_id LIKE 'dcls-position-%' ORDER BY rowid"
            ).fetchall()
        ]
        assert {(row["action"], row["position_id"]) for row in events} == {
            (action, position_id)
            for position_id in position_ids
            for action in ("position_opened", "position_closed")
        }
        positions_by_id = {str(row["id"]): row for row in outcomes}
        for event in events:
            expected_time = (
                positions_by_id[str(event["position_id"])]["opened_at"]
                if event["action"] == "position_opened"
                else positions_by_id[str(event["position_id"])]["closed_at"]
            )
            assert event["created_at"] == expected_time
        funding_at = harness.db.execute(
            "SELECT created_at FROM paper_account_events "
            "WHERE action = 'initial_funding'"
        ).fetchone()["created_at"]
        assert datetime.fromisoformat(funding_at) <= min(
            datetime.fromisoformat(str(row["opened_at"])) for row in outcomes
        )

        with patch.object(account_loss, "datetime", harness._clocked_datetime()):
            snapshot = authoritative_account_realized_loss("paper_internal")
        assert snapshot.available is True
        assert snapshot.realized_pnl_usdt == pytest.approx(0.0)
        assert snapshot.event_count == 2

        latest_fill = max(
            datetime.fromisoformat(str(row["filled_at"]).replace("Z", "+00:00"))
            for row in harness.db.execute("SELECT filled_at FROM fills").fetchall()
        )
        fill_age_hours = (harness.clock.now() - latest_fill).total_seconds() / 3600
        assert fill_age_hours == pytest.approx(2.0)


def test_trade_performance_replacement_only_deletes_dcls_owned_facts(tmp_path):
    with ControlLoopHarness(tmp_path) as harness:
        from aletheia.portfolio.paper_account import adjust_cash
        from aletheia.storage import repositories as repo
        from aletheia.storage.models import (
            Fill,
            Opportunity,
            Order,
            Position,
            RiskDecision,
            TradeProposal,
        )

        harness.record_outcome(won=True)
        [old_dcls_position] = harness.db.execute(
            "SELECT id FROM positions WHERE id LIKE 'dcls-position-%'"
        ).fetchall()
        adjust_cash(
            7.0,
            note="external retained credit",
            actor="external-operator",
            source="external-fixture",
        )
        external_ledger_before = dict(harness.db.execute(
            "SELECT * FROM paper_account_events "
            "WHERE source = 'external-fixture'"
        ).fetchone())
        assert external_ledger_before["cash_balance_before_usdt"] == 201.0
        assert external_ledger_before["cash_balance_after_usdt"] == 208.0
        harness.seed_candidates(high=1, medium=1)
        candidate_ids = {
            str(row["id"]) for row in harness.db.execute(
                "SELECT id FROM opportunities "
                "WHERE id LIKE 'dcls-high-candidate-%' "
                "OR id LIKE 'dcls-medium-candidate-%'"
            ).fetchall()
        }

        external_opportunity = Opportunity(
            id="external-opportunity",
            symbol="ETH/USDT",
            market="spot",
            direction_bias="long",
            source_agent="external-fixture",
        )
        repo.insert_opportunity(external_opportunity)
        external_proposal = TradeProposal(
            id="external-proposal",
            opportunity_id=external_opportunity.id,
            symbol="ETH/USDT",
            market_type="spot",
            direction="long",
            quantity=1.0,
            price=100.0,
            execution_mode="paper_internal",
            status="executed",
        )
        repo.insert_trade_proposal(external_proposal)
        external_decision = RiskDecision(
            id="external-risk",
            proposal_id=external_proposal.id,
            approved=True,
            allowed_execution_mode="paper_internal",
            proposal_execution_mode="paper_internal",
            decision_role="authoritative",
            evaluated_quantity=1.0,
            proposal_commit_hash=external_proposal.commit_hash,
        )
        repo.insert_risk_decision(external_decision)
        external_order = Order(
            id="external-order",
            proposal_id=external_proposal.id,
            risk_decision_id=external_decision.id,
            symbol="ETH/USDT",
            market_type="spot",
            direction="long",
            order_type="market",
            quantity=1.0,
            price=100.0,
            execution_mode="paper_internal",
            position_id="external-position",
            status="filled",
        )
        repo.insert_order(external_order)
        repo.insert_fill(Fill(
            id="external-fill",
            order_id=external_order.id,
            fill_price=100.0,
            fill_quantity=1.0,
            fee=0.0,
        ))
        repo.insert_position(Position(
            id="external-position",
            symbol="ETH/USDT",
            market_type="spot",
            direction="long",
            quantity=1.0,
            entry_price=100.0,
            execution_mode="paper_internal",
            opportunity_id=external_opportunity.id,
            proposal_id=external_proposal.id,
            order_id=external_order.id,
        ))
        external_job = repo.enqueue_outcome_hook_job(
            "external-position", "shadow_trades"
        )

        harness.change_trade_performance(
            win_rate=100.0,
            total=2,
            hours_since_last_fill=1.0,
        )

        assert repo.get_opportunity(external_opportunity.id) is not None
        assert repo.get_trade_proposal(external_proposal.id) is not None
        assert repo._select_one("risk_decisions", external_decision.id) is not None
        assert repo.get_order(external_order.id) is not None
        assert repo._select_one("fills", "external-fill") is not None
        assert repo._select_one("positions", "external-position") is not None
        assert repo._select_one("outcome_hook_jobs", external_job["id"]) is not None

        external_ledger_after = dict(harness.db.execute(
            "SELECT * FROM paper_account_events WHERE id = ?",
            (external_ledger_before["id"],),
        ).fetchone())
        for field in (
            "id", "action", "amount_usdt", "actor", "source", "position_id", "note",
        ):
            assert external_ledger_after[field] == external_ledger_before[field]
        assert external_ledger_after["cash_balance_before_usdt"] == 202.0
        assert external_ledger_after["cash_balance_after_usdt"] == 209.0
        ledger = [
            dict(row) for row in harness.db.execute(
                "SELECT * FROM paper_account_events "
                "ORDER BY datetime(created_at) ASC, rowid ASC"
            ).fetchall()
        ]
        for previous, current in zip(ledger, ledger[1:]):
            assert current["cash_balance_before_usdt"] == pytest.approx(
                previous["cash_balance_after_usdt"]
            )
        assert ledger[-1]["cash_balance_after_usdt"] == pytest.approx(209.0)

        surviving_candidates = {
            str(row["id"]) for row in harness.db.execute(
                "SELECT id FROM opportunities "
                "WHERE id LIKE 'dcls-high-candidate-%' "
                "OR id LIKE 'dcls-medium-candidate-%'"
            ).fetchall()
        }
        assert surviving_candidates == candidate_ids
        assert repo._select_one("positions", old_dcls_position["id"]) is None
        assert harness.db.execute(
            "SELECT COUNT(*) FROM paper_account_events "
            "WHERE position_id = ?",
            (old_dcls_position["id"],),
        ).fetchone()[0] == 0
        assert harness.db.execute(
            "SELECT COUNT(*) FROM outcome_hook_jobs WHERE position_id = ?",
            (old_dcls_position["id"],),
        ).fetchone()[0] == 0


def test_trade_performance_cleanup_scopes_all_position_fk_children(tmp_path):
    with ControlLoopHarness(tmp_path) as harness:
        from aletheia.storage import repositories as repo
        from aletheia.storage.models import (
            Position,
            PositionDisappearanceCandidate,
            PositionDisappearanceObservation,
            SerenityOpportunity,
            SerenityPostMortem,
            SerenityTheme,
        )

        harness.record_outcome(won=True)
        dcls_position = dict(harness.db.execute(
            "SELECT * FROM positions WHERE id LIKE 'dcls-position-%'"
        ).fetchone())
        external_position = Position(
            id="external-fk-position",
            symbol="ETH/USDT",
            market_type="spot",
            direction="long",
            quantity=1.0,
            entry_price=100.0,
            execution_mode="paper_internal",
        )
        repo.insert_position(external_position)

        for owner, position in (
            ("dcls", dcls_position),
            ("external", repo._select_one("positions", external_position.id)),
        ):
            candidate = PositionDisappearanceCandidate(
                id=f"{owner}-disappearance-candidate",
                position_id=str(position["id"]),
                identity_key=f"{owner}-identity",
                broker_id="paper_internal",
                account_environment="local",
                account_fingerprint=f"{owner}-account",
                execution_mode="paper_internal",
                symbol=str(position["symbol"]),
                market_type=str(position["market_type"]),
                direction=str(position["direction"]),
                first_observed_at=str(position["opened_at"]),
                last_observed_at=str(position.get("closed_at") or position["opened_at"]),
                semantics_version="oi-v1",
            )
            repo.insert_position_disappearance_candidate(candidate)
            repo.insert_position_disappearance_observation(
                PositionDisappearanceObservation(
                    id=f"{owner}-disappearance-observation",
                    candidate_id=candidate.id,
                    observation_hash=f"{owner}-observation-hash",
                    view_identity=f"{owner}-view",
                    view_complete=True,
                    position_absent=True,
                )
            )

        theme = SerenityTheme(id="external-serenity-theme", name="External theme")
        serenity_opportunity = SerenityOpportunity(
            id="external-serenity-opportunity",
            theme_id=theme.id,
            symbol="ETH/USDT",
            direction="long",
        )
        repo.insert_serenity_theme(theme)
        repo.insert_serenity_opportunity(serenity_opportunity)
        for owner, position_id in (
            ("dcls", str(dcls_position["id"])),
            ("external", external_position.id),
        ):
            assert repo.insert_serenity_post_mortem(SerenityPostMortem(
                id=f"{owner}-serenity-post-mortem",
                position_id=position_id,
                opportunity_id=serenity_opportunity.id,
                outcome="win",
                summary=f"{owner} retained-scope fixture",
            ))

        harness.change_trade_performance(
            win_rate=50.0,
            total=2,
            hours_since_last_fill=1.0,
        )

        assert repo._select_one("positions", str(dcls_position["id"])) is None
        assert repo._select_one(
            "position_disappearance_candidates", "dcls-disappearance-candidate"
        ) is None
        assert repo._select_one(
            "position_disappearance_observations", "dcls-disappearance-observation"
        ) is None
        assert repo._select_one(
            "serenity_post_mortems", "dcls-serenity-post-mortem"
        ) is None

        assert repo._select_one("positions", external_position.id) is not None
        assert repo._select_one(
            "position_disappearance_candidates", "external-disappearance-candidate"
        ) is not None
        assert repo._select_one(
            "position_disappearance_observations", "external-disappearance-observation"
        ) is not None
        assert repo._select_one(
            "serenity_post_mortems", "external-serenity-post-mortem"
        ) is not None
