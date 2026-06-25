"""Integration tests for guard evaluation in paper_trader_ranker.

These exercise the REAL guard stack end-to-end: real guard .md files on disk →
load_trading_guards() → predicate matching → penalty accumulation →
_evaluate_guards_safe()'s context construction and penalty cap. The ONLY thing
mocked is the data dir (tmp_path) and config — never `evaluate_guards` itself,
so a bug in predicate matching, context field wiring, applies_to filtering, or
the penalty cap would actually fail these tests.

NOTE: paper_trader_ranker.py is on the EXPERIMENT FREEZE list — these tests do
NOT modify it, only verify its existing behavior.
"""

import sqlite3

import pytest

from aletheia.agents.paper_trader_ranker import _evaluate_guards_safe, _make_candidate
from aletheia.knowledge.guards import GuardEvaluation, GuardHit


def setup_env(tmp_path, monkeypatch):
    """Point DATA_DIR at tmp and create the guards dir so real guard files load."""
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "knowledge" / "guards").mkdir(parents=True, exist_ok=True)
    from aletheia.storage.migrations import run_migrations
    sq.reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


def _write_guard(tmp_path, guard_id, *, enforcement="scoring", severity="warning",
                 score_penalty=0.0, block_candidate=False, applies_to=None,
                 predicates=None, status="active", scope="paper_entry"):
    """Write a real guard markdown file to the tmp data dir's guards folder."""
    import yaml
    meta = {
        "guard_id": guard_id,
        "title": guard_id.replace("_", " ").title(),
        "status": status,
        "scope": scope,
        "enforcement": enforcement,
        "severity": severity,
        "score_penalty": score_penalty,
        "block_candidate": block_candidate,
        "applies_to": applies_to or {},
        "predicates": predicates or [],
    }
    fm = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)
    path = tmp_path / "knowledge" / "guards" / f"{guard_id}.md"
    path.write_text("---\n" + fm + "---\n\nGuard body.\n", encoding="utf-8")


def _save_config(monkeypatch, enabled=True, enforce=True, max_penalty=40.0):
    """Persist a REAL config (not a MagicMock) so guard_cfg reads true values."""
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.guard.enabled = enabled
    cfg.guard.enforce_in_paper_ranker = enforce
    cfg.guard.max_score_penalty = max_penalty
    save_config(cfg)


def _opp(symbol="BTC/USDT", confidence="high", scanner_family="momentum"):
    return {
        "id": "opp-1",
        "symbol": symbol,
        "confidence": confidence,
        "confidence_score": 75,
        "scanner_family": scanner_family,
        "direction_bias": "long",
        "source_agent": "market_observer",
        "universe_member_id": "um-1",
        "linked_strategy_id": "strat-1",
    }


def _universe_member(late_entry_penalty=10, risk_flags=None, crowding_risk=5):
    return {
        "rank": 1,
        "total_score": 80,
        "tradability_score": 70,
        "late_entry_penalty": late_entry_penalty,
        "crowding_risk": crowding_risk,
        "risk_flags": risk_flags if risk_flags is not None else [],
        "late_entry_bucket": "early",
    }


# ---------------------------------------------------------------------------
# 1. A matching guard's penalty actually flows through the full stack
# ---------------------------------------------------------------------------

def test_matching_guard_produces_penalty(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _save_config(monkeypatch)
    # Guard fires when crowding_risk >= 20. Our member has crowding_risk=25 → match.
    _write_guard(tmp_path, "high_crowding", score_penalty=15.0,
                 predicates=[{"field": "crowding_risk", "op": "gte", "value": 20}])

    result = _evaluate_guards_safe(
        _opp(), _universe_member(crowding_risk=25), {"status": "active"},
        "long", "high", True, True, None,
    )
    assert result is not None
    assert result.total_penalty == 15.0
    assert len(result.hits) == 1
    assert result.hits[0].guard_id == "high_crowding"


# ---------------------------------------------------------------------------
# 2. A non-matching guard produces NO penalty (predicate logic must be exercised)
# ---------------------------------------------------------------------------

def test_non_matching_guard_produces_no_penalty(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _save_config(monkeypatch)
    # Guard requires crowding_risk >= 20, but member has 5 → no match.
    _write_guard(tmp_path, "high_crowding", score_penalty=15.0,
                 predicates=[{"field": "crowding_risk", "op": "gte", "value": 20}])

    result = _evaluate_guards_safe(
        _opp(), _universe_member(crowding_risk=5), {"status": "active"},
        "long", "high", True, True, None,
    )
    assert result is not None
    assert result.total_penalty == 0.0
    assert result.hits == []


# ---------------------------------------------------------------------------
# 3. The penalty cap is enforced against the REAL accumulated penalty
# ---------------------------------------------------------------------------

def test_penalty_capped_at_max(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _save_config(monkeypatch, max_penalty=30.0)
    # Two matching guards summing to 50 penalty — must be capped to 30.
    _write_guard(tmp_path, "g_a", score_penalty=25.0,
                 predicates=[{"field": "crowding_risk", "op": "gte", "value": 20}])
    _write_guard(tmp_path, "g_b", score_penalty=25.0,
                 predicates=[{"field": "confidence", "op": "eq", "value": "high"}])

    result = _evaluate_guards_safe(
        _opp(confidence="high"), _universe_member(crowding_risk=25), {"status": "active"},
        "long", "high", True, True, None,
    )
    # Both fired (50 raw) → capped to 30.
    assert len(result.hits) == 2
    assert result.total_penalty == 30.0


# ---------------------------------------------------------------------------
# 4. block_candidate guard sets blocked=True
# ---------------------------------------------------------------------------

def test_blocking_guard_blocks(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _save_config(monkeypatch)
    _write_guard(tmp_path, "hard_block", enforcement="blocking", severity="critical",
                 block_candidate=True, score_penalty=0.0,
                 predicates=[{"field": "confidence", "op": "eq", "value": "high"}])

    result = _evaluate_guards_safe(
        _opp(confidence="high"), _universe_member(), {"status": "active"},
        "long", "high", True, True, None,
    )
    assert result.blocked is True
    assert result.hits[0].block_candidate is True


# ---------------------------------------------------------------------------
# 5. applies_to filter: guard scoped to a different scanner_family does NOT fire
# ---------------------------------------------------------------------------

def test_applies_to_filter_excludes_other_families(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _save_config(monkeypatch)
    # Guard only applies to "mean_reversion", our opp is "momentum" → excluded.
    _write_guard(tmp_path, "mr_only", score_penalty=20.0,
                 applies_to={"scanner_family": ["mean_reversion"]},
                 predicates=[{"field": "confidence", "op": "eq", "value": "high"}])

    result = _evaluate_guards_safe(
        _opp(scanner_family="momentum", confidence="high"), _universe_member(),
        {"status": "active"}, "long", "high", True, True, None,
    )
    assert result.total_penalty == 0.0
    assert result.hits == []


# ---------------------------------------------------------------------------
# 6. risk_flags from the universe member reach the guard context (contains op)
# ---------------------------------------------------------------------------

def test_risk_flags_reach_context_and_match(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _save_config(monkeypatch)
    # Guard fires when risk_flags contains "wide_spread".
    _write_guard(tmp_path, "spread_guard", score_penalty=10.0,
                 predicates=[{"field": "risk_flags", "op": "contains", "value": "wide_spread"}])

    # Member WITH the flag → fires.
    hit = _evaluate_guards_safe(
        _opp(), _universe_member(risk_flags=["wide_spread", "crowded"]),
        {"status": "active"}, "long", "high", True, True, None,
    )
    assert hit.total_penalty == 10.0

    # Member WITHOUT the flag → no fire (proves the field is really plumbed, not constant).
    miss = _evaluate_guards_safe(
        _opp(), _universe_member(risk_flags=["crowded"]),
        {"status": "active"}, "long", "high", True, True, None,
    )
    assert miss.total_penalty == 0.0


# ---------------------------------------------------------------------------
# 7. risk_flags serialized as a JSON string (the DB shape) is parsed correctly
# ---------------------------------------------------------------------------

def test_risk_flags_json_string_is_parsed(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _save_config(monkeypatch)
    _write_guard(tmp_path, "spread_guard", score_penalty=10.0,
                 predicates=[{"field": "risk_flags", "op": "contains", "value": "wide_spread"}])

    # universe_member.risk_flags as a JSON string (how it comes back from SQLite).
    member = _universe_member()
    member["risk_flags"] = '["wide_spread", "crowded"]'

    result = _evaluate_guards_safe(
        _opp(), member, {"status": "active"}, "long", "high", True, True, None,
    )
    assert result.total_penalty == 10.0


# ---------------------------------------------------------------------------
# 8. Guard subsystem disabled → returns None (no evaluation at all)
# ---------------------------------------------------------------------------

def test_returns_none_when_disabled(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _save_config(monkeypatch, enabled=False)
    _write_guard(tmp_path, "would_fire", score_penalty=99.0,
                 predicates=[{"field": "confidence", "op": "eq", "value": "high"}])

    result = _evaluate_guards_safe(
        _opp(confidence="high"), _universe_member(), {"status": "active"},
        "long", "high", True, True, None,
    )
    assert result is None


def test_returns_none_when_enforce_off(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _save_config(monkeypatch, enforce=False)
    _write_guard(tmp_path, "would_fire", score_penalty=99.0,
                 predicates=[{"field": "confidence", "op": "eq", "value": "high"}])

    result = _evaluate_guards_safe(
        _opp(confidence="high"), _universe_member(), {"status": "active"},
        "long", "high", True, True, None,
    )
    assert result is None


# ---------------------------------------------------------------------------
# 9. Never crashes the ranker — a broken config returns None, not an exception
# ---------------------------------------------------------------------------

def test_never_crashes_on_exception(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    # load_config raising simulates a corrupt config; the wrapper must swallow it.
    monkeypatch.setattr(
        "aletheia.agents.paper_trader_ranker.load_config",
        lambda: (_ for _ in ()).throw(RuntimeError("config broken")),
    )
    result = _evaluate_guards_safe(
        _opp(), _universe_member(), {"status": "active"},
        "long", "high", True, True, None,
    )
    assert result is None


# ---------------------------------------------------------------------------
# 10. factor_signal + strategy_evidence fields are wired into the context
# ---------------------------------------------------------------------------

def test_factor_and_evidence_fields_reach_context(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _save_config(monkeypatch)
    # Guard fires on factor_status == "watch" AND drift_status == "paper_confirming".
    _write_guard(tmp_path, "factor_drift", score_penalty=12.0,
                 predicates=[
                     {"field": "factor_status", "op": "eq", "value": "watch"},
                     {"field": "strategy_drift_status", "op": "eq", "value": "paper_confirming"},
                 ])

    result = _evaluate_guards_safe(
        _opp(), _universe_member(),
        {"status": "watch", "expectancy": -0.5, "trade_count": 5},
        "long", "high", False, None, {"drift_status": "paper_confirming"},
    )
    # Both predicates only match if BOTH fields are correctly plumbed into the ctx.
    assert result.total_penalty == 12.0
    assert result.hits[0].guard_id == "factor_drift"


# ---------------------------------------------------------------------------
# _make_candidate: guard metadata projection onto the candidate dict
# (already good behavior tests — kept, they use real _make_candidate)
# ---------------------------------------------------------------------------

class TestMakeCandidateGuardFields:
    def test_guard_metadata_when_eval_present(self):
        guard_eval = GuardEvaluation(
            blocked=True, total_penalty=20.0,
            hits=[GuardHit(
                guard_id="g1", title="Guard 1", enforcement="blocking",
                severity="critical", score_penalty=0, block_candidate=True,
                reason="Guard 1: predicates matched",
            )],
        )
        c = _make_candidate({"id": "o1", "symbol": "BTC/USDT"}, guard_eval=guard_eval)
        assert c["guard_blocked"] is True
        assert c["guard_penalty"] == 20.0
        assert c["matched_guard_ids"] == ["g1"]
        assert c["guard_reasons"] == ["Guard 1: predicates matched"]

    def test_guard_metadata_when_no_eval(self):
        c = _make_candidate({"id": "o1", "symbol": "BTC/USDT"})
        assert c["guard_blocked"] is False
        assert c["guard_penalty"] == 0.0
        assert c["matched_guard_ids"] == []
        assert c["guard_reasons"] == []

    def test_guard_metadata_empty_eval(self):
        guard_eval = GuardEvaluation(blocked=False, total_penalty=0, hits=[])
        c = _make_candidate({"id": "o1", "symbol": "BTC/USDT"}, guard_eval=guard_eval)
        assert c["guard_blocked"] is False
        assert c["guard_penalty"] == 0.0
        assert c["matched_guard_ids"] == []
