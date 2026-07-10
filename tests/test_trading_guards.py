"""Tests for the deterministic trading guard engine."""

from aletheia.knowledge.guards import (
    TradingGuard,
    GuardPredicate,
    GuardAppliesTo,
    GuardEvaluation,
    evaluate_guards,
    _applies_to_from_meta,
    _predicates_match,
    _eval_predicate,
    ALLOWED_GUARD_FIELDS,
)


def _make_guard(
    guard_id="test_guard",
    scope="paper_entry",
    enforcement="scoring",
    score_penalty=10.0,
    block_candidate=False,
    predicates=None,
    applies_to=None,
) -> TradingGuard:
    return TradingGuard(
        guard_id=guard_id,
        title=f"Test {guard_id}",
        status="active",
        scope=scope,
        enforcement=enforcement,
        score_penalty=score_penalty,
        block_candidate=block_candidate,
        predicates=predicates or [],
        applies_to=applies_to or GuardAppliesTo(),
    )


class TestPredicateEval:
    def test_gte_match(self):
        assert _eval_predicate("gte", 25, 20) is True

    def test_gte_no_match(self):
        assert _eval_predicate("gte", 15, 20) is False

    def test_eq_match(self):
        assert _eval_predicate("eq", "blocked_candidate", "blocked_candidate") is True

    def test_eq_no_match(self):
        assert _eval_predicate("eq", "active", "blocked_candidate") is False

    def test_contains_list(self):
        assert _eval_predicate("contains", ["wide_spread", "crowded"], "wide_spread") is True

    def test_contains_list_no_match(self):
        assert _eval_predicate("contains", ["crowded"], "wide_spread") is False

    def test_in_list(self):
        assert _eval_predicate("in", "late", ["late", "very_late"]) is True

    def test_in_list_no_match(self):
        assert _eval_predicate("in", "early", ["late", "very_late"]) is False

    def test_none_context_value(self):
        assert _eval_predicate("gte", None, 20) is False

    def test_eq_bool(self):
        assert _eval_predicate("eq", False, False) is True
        assert _eval_predicate("eq", True, False) is False


class TestPredicatesMatch:
    def test_empty_predicates_no_match(self):
        assert _predicates_match([], {"symbol": "BTC/USDT"}) is False

    def test_disallowed_field_no_match(self):
        preds = [GuardPredicate(field="secret_field", op="eq", value="x")]
        assert _predicates_match(preds, {"secret_field": "x"}) is False

    def test_valid_predicate_matches(self):
        preds = [GuardPredicate(field="late_entry_penalty", op="gte", value=20)]
        assert _predicates_match(preds, {"late_entry_penalty": 25}) is True

    def test_all_predicates_must_match(self):
        preds = [
            GuardPredicate(field="late_entry_penalty", op="gte", value=20),
            GuardPredicate(field="factor_status", op="eq", value="watch"),
        ]
        ctx = {"late_entry_penalty": 25, "factor_status": "active"}
        assert _predicates_match(preds, ctx) is False


class TestEvaluateGuards:
    def test_flat_auto_guard_metadata_scopes_to_symbol(self):
        applies_to = _applies_to_from_meta({
            "symbol": "SOL/USDT",
            "scanner_family": "momentum",
            "direction": "long",
            "market_type": "perp",
        })

        assert applies_to == {
            "symbol": ["SOL/USDT"],
            "scanner_family": ["momentum"],
            "direction": ["long"],
            "market_type": ["perp"],
        }

    def test_market_type_scope_does_not_cross_spot_and_perp(self):
        guard = _make_guard(
            predicates=[GuardPredicate(field="scanner_family", op="eq", value="momentum")],
            applies_to=GuardAppliesTo(market_type=["perp"]),
        )

        perp = evaluate_guards(
            {"scanner_family": "momentum", "market_type": "usdt_futures"},
            scope="paper_entry",
            guards=[guard],
        )
        spot = evaluate_guards(
            {"scanner_family": "momentum", "market_type": "spot"},
            scope="paper_entry",
            guards=[guard],
        )

        assert len(perp.hits) == 1
        assert spot.hits == []

    def test_scoring_guard_produces_penalty(self):
        guard = _make_guard(
            predicates=[GuardPredicate(field="late_entry_penalty", op="gte", value=20)],
            score_penalty=20.0,
            applies_to=GuardAppliesTo(asset_class=["crypto"]),
        )
        ctx = {"asset_class": "crypto", "late_entry_penalty": 30}
        result = evaluate_guards(ctx, scope="paper_entry", guards=[guard])
        assert result.blocked is False
        assert result.total_penalty == 20.0
        assert len(result.hits) == 1

    def test_blocking_guard_blocks(self):
        guard = _make_guard(
            enforcement="blocking",
            block_candidate=True,
            predicates=[GuardPredicate(field="factor_status", op="eq", value="blocked_candidate")],
            applies_to=GuardAppliesTo(asset_class=["crypto"]),
        )
        ctx = {"asset_class": "crypto", "factor_status": "blocked_candidate"}
        result = evaluate_guards(ctx, scope="paper_entry", guards=[guard])
        assert result.blocked is True

    def test_candidate_status_guard_ignored(self):
        guard = _make_guard(predicates=[GuardPredicate(field="late_entry_penalty", op="gte", value=20)])
        guard.status = "candidate"
        ctx = {"asset_class": "crypto", "late_entry_penalty": 30}
        # When passing explicitly, status doesn't filter (loader does that)
        # But production code loads only active guards
        result = evaluate_guards(ctx, scope="paper_entry", guards=[guard])
        assert len(result.hits) == 1  # guard still passed explicitly

    def test_no_guards_no_penalty(self):
        ctx = {"asset_class": "crypto", "late_entry_penalty": 30}
        result = evaluate_guards(ctx, scope="paper_entry", guards=[])
        assert result.blocked is False
        assert result.total_penalty == 0.0
        assert result.hits == []

    def test_applies_to_filters(self):
        guard = _make_guard(
            predicates=[GuardPredicate(field="late_entry_penalty", op="gte", value=20)],
            score_penalty=15.0,
            applies_to=GuardAppliesTo(asset_class=["equity"]),
        )
        ctx = {"asset_class": "crypto", "late_entry_penalty": 30}
        result = evaluate_guards(ctx, scope="paper_entry", guards=[guard])
        assert len(result.hits) == 0

    def test_multiple_guards_accumulate(self):
        g1 = _make_guard(
            guard_id="g1",
            predicates=[GuardPredicate(field="late_entry_penalty", op="gte", value=20)],
            score_penalty=15.0,
            applies_to=GuardAppliesTo(asset_class=["crypto"]),
        )
        g2 = _make_guard(
            guard_id="g2",
            predicates=[GuardPredicate(field="risk_flags", op="contains", value="wide_spread")],
            score_penalty=10.0,
            applies_to=GuardAppliesTo(asset_class=["crypto"]),
        )
        ctx = {"asset_class": "crypto", "late_entry_penalty": 25, "risk_flags": ["wide_spread"]}
        result = evaluate_guards(ctx, scope="paper_entry", guards=[g1, g2])
        assert result.total_penalty == 25.0
        assert len(result.hits) == 2
