from __future__ import annotations


def _seed_paired_position(
    db,
    index: int,
    *,
    close_reference: float | None = 110.0,
) -> None:
    position_id = f"execution-position-{index}"
    proposal_id = f"execution-proposal-{index}"
    risk_id = f"execution-risk-{index}"
    entry_order_id = f"execution-entry-order-{index}"
    close_order_id = f"execution-close-order-{index}"
    opened_at = f"2026-08-08T00:{index:02d}:00+00:00"
    closed_at = f"2026-08-08T12:{index:02d}:00+00:00"
    commit_hash = f"execution-commit-{index}"
    entry_fee = 0.1001
    exit_fee = 0.10989
    realized_pnl = 9.58001
    db.execute(
        "INSERT INTO opportunities "
        "(id, symbol, market, direction_bias, created_at) "
        "VALUES (?, 'BTC/USDT', 'spot', 'long', ?)",
        (f"execution-opportunity-{index}", opened_at),
    )
    db.execute(
        "INSERT INTO trade_proposals "
        "(id, opportunity_id, symbol, market_type, direction, order_type, "
        "quantity, price, execution_mode, status, source, created_at, commit_hash, "
        "runtime_session_id, producer_commit, behavior_fingerprint) "
        "VALUES (?, ?, 'BTC/USDT', 'spot', 'long', 'market', 1, 100, "
        "'paper_internal', 'executed', 'native', ?, ?, 'proposal-session', "
        "'build-a', 'behavior-a')",
        (
            proposal_id,
            f"execution-opportunity-{index}",
            opened_at,
            commit_hash,
        ),
    )
    db.execute(
        "INSERT INTO risk_decisions "
        "(id, proposal_id, approved, allowed_execution_mode, "
        "proposal_execution_mode, decision_role, evaluated_quantity, "
        "proposal_commit_hash, created_at, runtime_session_id, producer_commit, "
        "behavior_fingerprint) "
        "VALUES (?, ?, 1, 'paper_internal', 'paper_internal', 'authoritative', "
        "1, ?, ?, 'entry-session', 'build-a', 'behavior-a')",
        (risk_id, proposal_id, commit_hash, opened_at),
    )
    db.execute(
        "INSERT INTO orders "
        "(id, proposal_id, risk_decision_id, symbol, market_type, direction, "
        "order_type, quantity, price, reference_price, reference_price_source, "
        "execution_mode, position_id, status, source, created_at, filled_at, "
        "runtime_session_id, producer_commit, behavior_fingerprint) "
        "VALUES (?, ?, ?, 'BTC/USDT', 'spot', 'long', 'market', 1, 100.1, "
        "100, 'risk_verified_proposal_price', 'paper_internal', ?, 'filled', "
        "'native', ?, ?, 'entry-session', 'build-a', 'behavior-a')",
        (
            entry_order_id,
            proposal_id,
            risk_id,
            position_id,
            opened_at,
            opened_at,
        ),
    )
    db.execute(
        "INSERT INTO fills "
        "(id, order_id, fill_price, fill_quantity, fee, fee_currency, fee_source, "
        "filled_at, runtime_session_id, producer_commit, behavior_fingerprint) "
        "VALUES (?, ?, 100.1, 1, ?, 'USDT', 'estimated', ?, 'entry-session', "
        "'build-a', 'behavior-a')",
        (f"execution-entry-fill-{index}", entry_order_id, entry_fee, opened_at),
    )
    db.execute(
        "INSERT INTO positions "
        "(id, symbol, market_type, direction, quantity, entry_price, current_price, "
        "realized_pnl, execution_mode, proposal_id, order_id, opportunity_id, "
        "status, close_reason, close_category, entry_price_source, "
        "entry_fill_verified, exit_price_source, exit_fill_verified, "
        "outcome_eligible, outcome_exclusion_reason, outcome_semantics_version, "
        "source, opened_at, closed_at, entry_fee, exit_fee, funding_cost, "
        "funding_source, runtime_session_id, producer_commit, behavior_fingerprint, "
        "closed_runtime_session_id, closed_producer_commit, "
        "closed_behavior_fingerprint) "
        "VALUES (?, 'BTC/USDT', 'spot', 'long', 1, 100.1, 109.89, ?, "
        "'paper_internal', ?, ?, ?, 'closed', 'take_profit', 'strategy', "
        "'local_fill', 1, 'local_fill', 1, 1, 'eligible', 'oi-v1', 'native', "
        "?, ?, ?, ?, 0, 'not_applicable', 'entry-session', 'build-a', "
        "'behavior-a', 'close-session', 'build-a', 'behavior-a')",
        (
            position_id,
            realized_pnl,
            proposal_id,
            entry_order_id,
            f"execution-opportunity-{index}",
            opened_at,
            closed_at,
            entry_fee,
            exit_fee,
        ),
    )
    db.execute(
        "INSERT INTO orders "
        "(id, proposal_id, symbol, market_type, direction, order_type, quantity, "
        "price, reference_price, reference_price_source, execution_mode, "
        "position_id, close_reason, status, source, created_at, filled_at, "
        "runtime_session_id, producer_commit, behavior_fingerprint) "
        "VALUES (?, ?, 'BTC/USDT', 'spot', 'long', 'market', 1, 109.89, ?, "
        "'paper_close_mark', 'paper_internal', ?, 'take_profit', 'filled', "
        "'native', ?, ?, 'close-session', 'build-a', 'behavior-a')",
        (
            close_order_id,
            proposal_id,
            close_reference,
            position_id,
            closed_at,
            closed_at,
        ),
    )
    db.execute(
        "INSERT INTO fills "
        "(id, order_id, fill_price, fill_quantity, fee, fee_currency, fee_source, "
        "filled_at, runtime_session_id, producer_commit, behavior_fingerprint) "
        "VALUES (?, ?, 109.89, 1, ?, 'USDT', 'estimated', ?, 'close-session', "
        "'build-a', 'behavior-a')",
        (f"execution-close-fill-{index}", close_order_id, exit_fee, closed_at),
    )


def test_execution_cost_is_paired_against_frictionless_reference(monkeypatch):
    import aletheia.analytics.alpha_attribution as alpha_attribution
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for index in range(5):
        _seed_paired_position(db, index)
    monkeypatch.setattr(
        alpha_attribution,
        "_utcnow",
        lambda: alpha_attribution.datetime.fromisoformat(
            "2026-08-09T12:00:00+00:00"
        ),
    )

    evidence = alpha_attribution.build_alpha_attribution_audit(days=7)[
        "execution_cost_evidence"
    ]

    assert evidence["measurement_status"] == "measured_contribution"
    assert evidence["contribution_verdict"] == "negative"
    assert evidence["causal_grade"] == "strong_prospective_paired_accounting"
    assert evidence["sample_size"] == 5
    assert evidence["baseline"] == 10.0
    assert evidence["observed"] == 9.58
    assert evidence["delta"] == -0.42
    assert evidence["incremental_net_pnl_total"] == -2.09995
    assert evidence["fee_total"] == 1.04995
    assert evidence["price_impact_total"] == -1.05
    assert evidence["behavior_authority"] is False
    assert evidence["promotion_eligible"] is False
    assert evidence["details"]["routing_value_measured"] is False


def test_execution_cost_reports_multi_entry_as_unsupported():
    from aletheia.analytics.execution_contribution import _paired_position_cost

    rows = [
        {"order_id": "entry-1", "order_close_reason": None},
        {"order_id": "entry-2", "order_close_reason": None},
        {"order_id": "close", "order_close_reason": "take_profit"},
    ]

    class _Db:
        def execute(self, *_args):
            return self

        def fetchall(self):
            return rows

    position = {
        "id": "position",
        "source": "native",
        "closed_runtime_session_id": "session",
        "closed_producer_commit": "commit",
        "closed_behavior_fingerprint": "behavior",
    }
    _record, reason = _paired_position_cost(_Db(), position)
    assert reason == "unsupported_execution_multi_entry"


def test_execution_cost_fails_closed_without_prospective_close_reference():
    from aletheia.analytics.execution_contribution import (
        build_execution_cost_contribution,
    )
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _seed_paired_position(db, 0, close_reference=None)

    evidence = build_execution_cost_contribution(
        db,
        cutoff="2026-08-01T00:00:00+00:00",
        min_sample_size=1,
    )

    assert evidence["measurement_status"] == "not_measured"
    assert evidence["eligible_count"] == 0
    assert evidence["exclusion_counts"] == {
        "incomplete_execution_fill_economics": 1,
    }


def test_execution_cost_fails_closed_when_fill_fee_disagrees_with_position():
    from aletheia.analytics.execution_contribution import (
        build_execution_cost_contribution,
    )
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _seed_paired_position(db, 0)
    db.execute(
        "UPDATE fills SET fee=0 WHERE id='execution-close-fill-0'"
    )

    evidence = build_execution_cost_contribution(
        db,
        cutoff="2026-08-01T00:00:00+00:00",
        min_sample_size=1,
    )

    assert evidence["measurement_status"] == "not_measured"
    assert evidence["exclusion_counts"] == {
        "execution_position_fee_mismatch": 1,
    }


def test_execution_cost_revalidates_funding_provenance(monkeypatch):
    import aletheia.analytics.execution_contribution as execution_contribution
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _seed_paired_position(db, 0)
    position = dict(
        db.execute(
            "SELECT * FROM positions WHERE id='execution-position-0'"
        ).fetchone()
    )
    position["funding_cost"] = 1.0
    monkeypatch.setattr(
        execution_contribution,
        "eligible_strategy_outcomes",
        lambda **_filters: [position],
    )

    evidence = execution_contribution.build_execution_cost_contribution(
        db,
        cutoff="2026-08-01T00:00:00+00:00",
        min_sample_size=1,
    )

    assert evidence["measurement_status"] == "not_measured"
    assert evidence["exclusion_counts"] == {
        "incomplete_execution_funding_economics": 1,
    }
