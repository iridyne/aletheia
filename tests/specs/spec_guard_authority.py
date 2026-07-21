"""Spec: Learned-guard authority (guard_authority contract).

P0 2026-07-12. An auto-generated ("learned") trading guard turned historical
loss-rate correlation into an active hard block — the auto-promotion chain ran with
``auto_promote_enabled=True`` by default, while the candidate body claimed it "requires
human review before promotion". These claims lock the corrected invariants:

  - auto-promotion is OFF by default;
  - every auto-generated candidate is created shadow-only;
  - the promotion job never auto-promotes or un-shadows a learned guard;
  - at enforcement, learned/shadow guards observe only (no block, no penalty) until
    an experiment (ranker_learned_guard, AT-01 + AT-02) grants authority — fail-closed;
  - seed/manual safety guards are a separate class and keep full authority;
  - learned guards have bounded lifetimes and expire by soft deprecation;
  - a read-only audit surfaces learned guards the old chain already activated.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


@spec_claim(SpecClaim(
    "GA-S01", "guard_authority", "§auto_promote default", "L2a",
    "guard.auto_promote_enabled defaults to False (no autonomous promotion)."))
def test_auto_promote_disabled_by_default():
    from aletheia.config.schema import GuardConfig
    assert GuardConfig().auto_promote_enabled is False


@spec_claim(SpecClaim(
    "GA-S02", "guard_authority", "§shadow-only creation", "L2a",
    "Every auto-generated guard candidate is created shadow-only (no block); manual is untouched."))
def test_auto_candidates_forced_shadow_only():
    from aletheia.knowledge.manager import _force_shadow_only_if_auto

    auto = _force_shadow_only_if_auto(
        {"guard_id": "auto_momentum_long_x", "block_candidate": True, "tags": ["auto-generated"]}
    )
    assert auto["shadow_only"] is True
    assert auto["block_candidate"] is False
    assert auto["proposed_block_candidate"] is True
    assert "shadow-only" in auto["tags"]
    created_at = datetime.fromisoformat(auto["created_at"])
    expires_at = datetime.fromisoformat(auto["expires_at"])
    assert expires_at - created_at == timedelta(days=30)

    # auto_generated flag alone (no auto_ id) also triggers it
    flagged = _force_shadow_only_if_auto({"guard_id": "x", "auto_generated": True})
    assert flagged["shadow_only"] is True

    # owner-authored (manual) candidate is left alone
    manual = _force_shadow_only_if_auto({"guard_id": "manual-btc-guard", "block_candidate": True})
    assert not manual.get("shadow_only")
    assert manual["block_candidate"] is True
    assert not manual.get("expires_at")


@spec_claim(SpecClaim(
    "GA-S09", "guard_authority", "§bounded learned lifetime", "L2b",
    "Expired learned Guards are excluded immediately; the daily lifecycle pass assigns legacy deadlines and soft-deprecates stale items."))
def test_learned_guard_expiry_is_fail_closed_and_soft_deprecated(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from aletheia.analytics import guard_promotion as gp
    from aletheia.knowledge.frontmatter import parse_frontmatter, render_frontmatter
    from aletheia.knowledge.guards import load_trading_guards

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(gp, "_record_guard_lifecycle_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(gp, "_notify_ranker_guard_change", lambda: None)
    guards_dir = tmp_path / "knowledge" / "guards"
    candidates_dir = tmp_path / "knowledge" / "guard_candidates"
    guards_dir.mkdir(parents=True)
    candidates_dir.mkdir(parents=True)

    expired_active = {
        "guard_id": "auto_expired_active", "title": "expired active",
        "status": "active", "auto_generated": True,
        "expires_at": "2026-07-20T00:00:00+00:00",
        "predicates": [{"field": "scanner_family", "op": "eq", "value": "momentum"}],
    }
    future_active = {
        "guard_id": "auto_future_active", "title": "future active",
        "status": "active", "auto_generated": True,
        "expires_at": "2026-08-20T00:00:00+00:00",
        "predicates": [{"field": "scanner_family", "op": "eq", "value": "momentum"}],
    }
    seed_active = {
        "guard_id": "late_entry_chase_v1", "title": "seed",
        "status": "active",
        "predicates": [{"field": "late_entry_penalty", "op": "gte", "value": 20}],
    }
    legacy_candidate = {
        "guard_id": "auto_legacy_candidate", "title": "legacy",
        "status": "candidate", "auto_generated": True,
        "predicates": [{"field": "scanner_family", "op": "eq", "value": "momentum"}],
    }
    (guards_dir / "expired.md").write_text(
        render_frontmatter(expired_active, "body"), encoding="utf-8"
    )
    (guards_dir / "future.md").write_text(
        render_frontmatter(future_active, "body"), encoding="utf-8"
    )
    (guards_dir / "seed.md").write_text(
        render_frontmatter(seed_active, "body"), encoding="utf-8"
    )
    legacy_path = candidates_dir / "20260101-000000-legacy.md"
    legacy_path.write_text(render_frontmatter(legacy_candidate, "body"), encoding="utf-8")

    loaded_ids = {guard.guard_id for guard in load_trading_guards(status="active")}
    assert loaded_ids == {"auto_future_active", "late_entry_chase_v1"}

    result = gp.expire_stale_learned_guards(
        now=datetime(2026, 7, 21, tzinfo=timezone.utc)
    )
    assert result == {
        "scanned_learned": 3,
        "deadlines_assigned": 1,
        "expired": 2,
        "expired_active": 1,
        "guard_ids": ["auto_expired_active", "auto_legacy_candidate"],
    }
    assert parse_frontmatter(guards_dir / "expired.md")[0]["status"] == "deprecated"
    assert parse_frontmatter(guards_dir / "future.md")[0]["status"] == "active"
    assert parse_frontmatter(guards_dir / "seed.md")[0]["status"] == "active"
    legacy_meta = parse_frontmatter(legacy_path)[0]
    assert legacy_meta["status"] == "deprecated"
    assert legacy_meta["expires_at"] == "2026-01-31T00:00:00+00:00"


def test_direct_promotion_refuses_expired_candidate(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from aletheia.analytics.guard_promotion import promote_guard_candidate_to_active
    from aletheia.knowledge.frontmatter import render_frontmatter

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    candidates_dir = tmp_path / "knowledge" / "guard_candidates"
    candidates_dir.mkdir(parents=True)
    path = candidates_dir / "expired.md"
    path.write_text(render_frontmatter({
        "guard_id": "auto_expired_candidate", "title": "expired",
        "status": "candidate", "auto_generated": True,
        "expires_at": "2020-01-01T00:00:00+00:00",
        "predicates": [{"field": "scanner_family", "op": "eq", "value": "momentum"}],
    }, "body"), encoding="utf-8")

    assert promote_guard_candidate_to_active("auto_expired_candidate") is False
    assert not (tmp_path / "knowledge" / "guards").exists()


@spec_claim(SpecClaim(
    "GA-S05", "guard_authority", "§origin classification", "L2a",
    "guard_origin separates seed / manual / learned guards."))
def test_guard_origin_classifies_provenance():
    from aletheia.knowledge.guards import guard_origin

    assert guard_origin({"guard_id": "late_entry_chase_v1"}) == "seed"
    assert guard_origin({"guard_id": "backtest_false_positive_v1"}) == "seed"
    assert guard_origin({"guard_id": "manual-btc-guard", "tags": []}) == "manual"
    assert guard_origin({"guard_id": "auto_momentum_long_solusdt"}) == "learned"
    assert guard_origin({"guard_id": "x", "tags": ["shadow-only"]}) == "learned"
    assert guard_origin({"guard_id": "y", "auto_generated": True}) == "learned"


@spec_claim(
    SpecClaim(
        "GA-S04", "guard_authority", "§enforcement seam", "L2b",
        "evaluate_guards: learned/shadow guards never block or penalize (fail-closed); "
        "seed/manual safety guards enforce as authored."),
    SpecClaim(
        "GA-B01", "guard_authority", "§block authority fail-closed", "L2b",
        "learned_guard_block_authorized is False on master (no experiment authority yet)."),
)
def test_learned_guard_enforcement_suppressed_seed_honored():
    from aletheia.knowledge.guards import (
        evaluate_guards, TradingGuard, GuardAppliesTo, GuardPredicate,
        learned_guard_block_authorized,
    )

    ctx = {"symbol": "SOL/USDT", "scanner_family": "momentum"}
    applies = GuardAppliesTo(symbol=["SOL/USDT"])
    preds = [GuardPredicate(field="scanner_family", op="eq", value="momentum")]

    # Fail-closed seam.
    assert learned_guard_block_authorized(
        TradingGuard(guard_id="auto_x", title="x")) is False

    # Learned blocking guard: matched but suppressed → no block, no penalty.
    learned = TradingGuard(
        guard_id="auto_momentum_long_solusdt", title="learned", status="active",
        enforcement="blocking", block_candidate=True, score_penalty=10.0,
        applies_to=applies, predicates=preds)
    res = evaluate_guards(ctx, guards=[learned])
    assert res.blocked is False
    assert res.total_penalty == 0.0
    assert res.hits and res.hits[0].block_candidate is False  # hit recorded, effect zeroed

    # Manual guard explicitly marked shadow_only is also suppressed.
    shadow_manual = TradingGuard(
        guard_id="manual-x", title="manual shadow", status="active",
        enforcement="blocking", block_candidate=True, score_penalty=5.0,
        shadow_only=True, applies_to=applies, predicates=preds)
    assert evaluate_guards(ctx, guards=[shadow_manual]).blocked is False

    # Seed safety guard keeps full blocking authority.
    seed = TradingGuard(
        guard_id="repeated_losing_factor_v1", title="seed block", status="active",
        enforcement="blocking", block_candidate=True,
        applies_to=applies, predicates=preds)
    assert evaluate_guards(ctx, guards=[seed]).blocked is True

    # Manual (non-shadow) scoring guard keeps its penalty.
    manual = TradingGuard(
        guard_id="manual-score", title="manual score", status="active",
        enforcement="scoring", score_penalty=15.0,
        applies_to=applies, predicates=preds)
    assert evaluate_guards(ctx, guards=[manual]).total_penalty == 15.0


@spec_claim(SpecClaim(
    "GA-B03", "guard_authority", "§ranker static guard independence", "L2b",
    "Disabling autonomous Learning or paper Learning influence does not disable owner-authored seed/manual Guard enforcement."))
def test_ranker_static_guards_remain_applicable_when_learning_is_disabled(monkeypatch):
    from aletheia.agents import paper_trader_ranker
    from aletheia.config.schema import AletheiaConfig

    cfg = AletheiaConfig()
    cfg.agent.autonomous_learning = False
    cfg.learning.paper_influence_enabled = False
    cfg.guard.enabled = True
    cfg.guard.enforce_in_paper_ranker = True
    monkeypatch.setattr(paper_trader_ranker, "load_config", lambda: cfg)
    monkeypatch.setattr(
        "aletheia.learning.control.learning_influence_enabled", lambda: False,
    )

    assert paper_trader_ranker._guard_evaluation_applicability() == (
        "enabled", "guard_evaluation_enabled",
    )


def test_ranker_threads_every_real_opportunity_identity_to_guard_authority(monkeypatch):
    from types import SimpleNamespace

    from aletheia.agents import paper_trader_ranker
    from aletheia.knowledge.guards import GuardEvaluation

    captured = {}

    def capture(context, *, scope):
        captured.update(context)
        assert scope == "paper_entry"
        return GuardEvaluation()

    monkeypatch.setattr(
        paper_trader_ranker,
        "load_config",
        lambda: SimpleNamespace(guard=SimpleNamespace(
            enabled=True, enforce_in_paper_ranker=True, max_score_penalty=40.0,
        )),
    )
    monkeypatch.setattr(paper_trader_ranker, "evaluate_guards", capture)
    result = paper_trader_ranker._evaluate_guards_safe(
        {"id": "opp-without-universe-key", "symbol": "BTC/USDT", "scanner_family": "momentum"},
        None,
        None,
        "long",
        "high",
        False,
        None,
        None,
    )

    assert result is not None
    assert captured["_candidate_key"] == "opportunity:opp-without-universe-key"
    assert captured["_outcome_candidate_key"] is None
    assert captured["_opportunity_id"] == "opp-without-universe-key"


@spec_claim(SpecClaim(
    "GA-B04", "guard_authority", "§per-candidate prospective authority", "L2b",
    "A learned Guard applies its preserved proposed effect only for an exact authorized treatment assignment and records the candidate identity."))
def test_learned_guard_uses_per_candidate_treatment_authority(monkeypatch):
    from aletheia.knowledge.guards import (
        GuardAppliesTo,
        GuardPredicate,
        TradingGuard,
        evaluate_guards,
    )
    from aletheia.learning.experiments import InfluenceDecision

    captured = {}

    def authorized_treatment(**kwargs):
        captured.update(kwargs)
        return InfluenceDecision(
            1.0, True, "treatment", "authorized",
            "hypothesis-1", "experiment-1", "exposure-1",
        )

    monkeypatch.setattr(
        "aletheia.learning.experiments.apply_numeric_treatment",
        authorized_treatment,
    )
    guard = TradingGuard(
        guard_id="auto_momentum_guard",
        title="learned momentum guard",
        status="active",
        enforcement="advisory",
        block_candidate=False,
        score_penalty=0.0,
        proposed_block_candidate=True,
        proposed_score_penalty=12.0,
        shadow_only=True,
        tags=["auto-generated", "shadow-only"],
        applies_to=GuardAppliesTo(symbol=["SOL/USDT"]),
        predicates=[GuardPredicate(field="scanner_family", op="eq", value="momentum")],
    )
    context = {
        "symbol": "SOL/USDT",
        "scanner_family": "momentum",
        "_candidate_key": "opportunity:opp-guard-1",
        "_outcome_candidate_key": "universe-member-1:4h",
        "_opportunity_id": "opp-guard-1",
    }

    result = evaluate_guards(context, guards=[guard])

    assert result.blocked is True
    assert result.total_penalty == 12.0
    assert captured["path_id"] == "ranker_learned_guard"
    assert captured["candidate_key"] == "opportunity:opp-guard-1"
    assert captured["outcome_candidate_key"] == "universe-member-1:4h"
    assert captured["opportunity_id"] == "opp-guard-1"
    assert captured["baseline"] == 0.0
    assert captured["treatment"] == 1.0


@spec_claim(SpecClaim(
    "GA-C01", "guard_authority", "§durable authorized exposure", "L2c",
    "A real temporary-SQLite prospective experiment records linked treatment/control exposures and only its authorized treatment applies the learned Guard proposal."))
def test_real_authorized_learned_guard_exposure_applies_only_treatment(monkeypatch):
    from datetime import datetime, timedelta, timezone

    from aletheia.knowledge.guards import (
        GuardAppliesTo,
        GuardPredicate,
        TradingGuard,
        evaluate_guards,
    )
    from aletheia.learning.ledger import get_record, insert_record
    from aletheia.learning.models import (
        LearningContributionVerdict,
        LearningExperiment,
    )
    from aletheia.learning.experiments import stable_assignment
    from aletheia.storage.sqlite import get_db
    from tests.specs.spec_learning_authority import (
        _enable_legacy_paper_authority,
        _insert_settled_outcome,
    )

    _enable_legacy_paper_authority(monkeypatch, "ranker_learned_guard")
    guard = TradingGuard(
        guard_id="auto_real_authority_guard",
        title="real authority guard",
        status="active",
        enforcement="advisory",
        proposed_block_candidate=True,
        proposed_score_penalty=9.0,
        shadow_only=True,
        tags=["auto-generated", "shadow-only"],
        applies_to=GuardAppliesTo(symbol=["BTC/USDT"]),
        predicates=[GuardPredicate(field="scanner_family", op="eq", value="momentum")],
    )

    def evaluate(candidate_key: str, outcome_key: str | None = None):
        return evaluate_guards({
            "symbol": "BTC/USDT",
            "scanner_family": "momentum",
            "_candidate_key": candidate_key,
            "_outcome_candidate_key": outcome_key,
            "_opportunity_id": candidate_key.replace(":", "-"),
        }, guards=[guard])

    # First real call creates the durable shadow hypothesis/experiment.
    assert evaluate("guard-seed").blocked is False
    db = get_db()
    experiment_row = db.execute(
        "SELECT * FROM learning_experiments ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    assert experiment_row is not None
    was_in_tx = db.in_transaction
    db.execute(
        "UPDATE learning_experiments SET stage='paper_10', status='active', "
        "min_samples_per_arm=1 WHERE experiment_id=?",
        (experiment_row["experiment_id"],),
    )
    if not was_in_tx:
        db.commit()
    experiment = LearningExperiment(**get_record(
        "learning_experiments", experiment_row["experiment_id"],
    ))
    treatment_key = next(
        f"guard-treatment:{i}" for i in range(1000)
        if stable_assignment(experiment, f"guard-treatment:{i}") == "treatment"
    )
    control_key = next(
        f"guard-control:{i}" for i in range(1000)
        if stable_assignment(experiment, f"guard-control:{i}") == "control"
    )
    assert evaluate(
        treatment_key, _insert_settled_outcome(treatment_key),
    ).blocked is False
    assert evaluate(
        control_key, _insert_settled_outcome(control_key),
    ).blocked is False

    exposure = db.execute(
        "SELECT concurrent_policy_epoch FROM learning_exposures "
        "WHERE candidate_key=? ORDER BY rowid DESC LIMIT 1",
        (treatment_key,),
    ).fetchone()
    now = datetime.now(timezone.utc)
    insert_record("learning_contribution_verdicts", LearningContributionVerdict(
        experiment_id=experiment.experiment_id,
        hypothesis_id=experiment.hypothesis_id,
        window_start=(now - timedelta(days=14)).isoformat(),
        window_end=now.isoformat(),
        unique_treatment_n=1,
        unique_control_n=1,
        verdict="promote",
        reason="prospective net improvement",
        policy_epoch=exposure["concurrent_policy_epoch"],
        stage="paper_10",
    ))

    applied_key = next(
        f"guard-applied:{i}" for i in range(1000)
        if stable_assignment(experiment, f"guard-applied:{i}") == "treatment"
    )
    applied_outcome = _insert_settled_outcome(applied_key)
    applied = evaluate(applied_key, applied_outcome)
    assert applied.blocked is True
    assert applied.total_penalty == 9.0
    stored = db.execute(
        "SELECT candidate_key, opportunity_id, outcome_candidate_key, assignment, "
        "before_json, after_json FROM learning_exposures WHERE candidate_key=? "
        "ORDER BY rowid DESC LIMIT 1",
        (applied_key,),
    ).fetchone()
    assert dict(stored) == {
        "candidate_key": applied_key,
        "opportunity_id": applied_key.replace(":", "-"),
        "outcome_candidate_key": applied_outcome,
        "assignment": "treatment",
        "before_json": '{"value": 0.0}',
        "after_json": '{"value": 1.0}',
    }


@spec_claim(SpecClaim(
    "GA-S03", "guard_authority", "§no auto-promotion of learned/shadow", "L2b",
    "run_promotion_check never auto-promotes a shadow-only learned candidate."))
def test_promotion_never_auto_promotes_shadow_learned():
    from aletheia.knowledge.guards import TradingGuard, GuardAppliesTo, GuardPredicate
    from aletheia.analytics import guard_promotion as gp

    shadow_candidate = TradingGuard(
        guard_id="auto_momentum_long_solusdt", title="learned", status="candidate",
        enforcement="advisory", block_candidate=False, shadow_only=True,
        applies_to=GuardAppliesTo(symbol=["SOL/USDT"]),
        predicates=[GuardPredicate(field="scanner_family", op="eq", value="momentum")],
        tags=["auto-generated", "shadow-only"])

    cfg = MagicMock()
    cfg.guard.auto_promote_enabled = True
    cfg.guard.min_matching_trades = 3

    with patch.object(gp, "load_config", return_value=cfg), \
         patch.object(gp, "load_trading_guards", return_value=[shadow_candidate]), \
         patch("aletheia.learning.control.promotion_refreeze_reason", return_value=None), \
         patch("aletheia.analytics.trade_attribution.build_trade_attribution_rows", return_value=[]), \
         patch("aletheia.analytics.guard_effectiveness._clean_rows", return_value=[]), \
         patch.object(gp, "_promote_guard_to_active") as promote_spy:
        result = gp.run_promotion_check()

    assert result["promotions"] == 0
    promote_spy.assert_not_called()
    assert result.get("skipped_unauthorized", 0) >= 1


@spec_claim(
    SpecClaim(
        "GA-S06", "guard_authority", "§audit existing", "L2b",
        "audit_auto_guards lists learned active guards and flags those still enforcing."),
    SpecClaim(
        "GA-S07", "guard_authority", "§reversible downgrade", "L2b",
        "downgrade_learned_guards_to_shadow makes learned guards observe-only, preserves "
        "originals for rollback, and never touches seed/manual guards."),
)
def test_audit_and_reversible_downgrade(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from aletheia.knowledge.frontmatter import parse_frontmatter, render_frontmatter
    from aletheia.analytics.guard_promotion import audit_auto_guards, downgrade_learned_guards_to_shadow

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    guards_dir = tmp_path / "knowledge" / "guards"
    guards_dir.mkdir(parents=True)

    # A learned guard the old chain promoted to a hard block.
    learned_meta = {
        "type": "Trading Guard", "guard_id": "auto_momentum_long_solusdt",
        "title": "Auto block", "status": "active", "scope": "paper_entry",
        "enforcement": "blocking", "block_candidate": True, "score_penalty": 10.0,
        "applies_to": {"symbol": ["SOL/USDT"]},
        "predicates": [{"field": "scanner_family", "op": "eq", "value": "momentum"}],
        "tags": ["auto_generated"],
    }
    (guards_dir / "auto.md").write_text(render_frontmatter(learned_meta, "body"), encoding="utf-8")

    # A seed safety guard that must be left alone.
    seed_meta = {
        "type": "Trading Guard", "guard_id": "repeated_losing_factor_v1",
        "title": "Seed block", "status": "active", "scope": "paper_entry",
        "enforcement": "blocking", "block_candidate": True,
        "applies_to": {"asset_class": ["crypto"]},
        "predicates": [{"field": "scanner_family", "op": "eq", "value": "momentum"}],
        "tags": [],
    }
    (guards_dir / "seed.md").write_text(render_frontmatter(seed_meta, "body"), encoding="utf-8")

    # Audit: only the learned guard, flagged as still enforcing.
    rows = audit_auto_guards()
    assert [r["guard_id"] for r in rows] == ["auto_momentum_long_solusdt"]
    assert rows[0]["still_enforcing"] is True

    # Dry-run: reports, writes nothing.
    dry = downgrade_learned_guards_to_shadow(dry_run=True)
    assert dry["would_downgrade"] == 1
    meta_after_dry, _ = parse_frontmatter(guards_dir / "auto.md")
    assert meta_after_dry["block_candidate"] is True  # untouched

    # Apply: learned guard becomes observe-only, originals preserved, seed untouched.
    applied = downgrade_learned_guards_to_shadow(dry_run=False)
    assert applied["downgraded"] == 1
    meta_learned, _ = parse_frontmatter(guards_dir / "auto.md")
    assert meta_learned["shadow_only"] is True
    assert meta_learned["block_candidate"] is False
    assert meta_learned["enforcement"] == "advisory"
    assert meta_learned["score_penalty"] == 0  # fully observe-only, even under old code
    assert meta_learned["pre_shadow_enforcement"] == "blocking"
    assert meta_learned["pre_shadow_block_candidate"] is True
    assert meta_learned["pre_shadow_score_penalty"] == 10.0
    assert "shadow-only" in meta_learned["tags"]

    meta_seed, _ = parse_frontmatter(guards_dir / "seed.md")
    assert meta_seed["block_candidate"] is True  # seed safety guard never touched
    assert "shadow_only" not in meta_seed

    # Idempotent: a second run finds nothing left to downgrade.
    assert downgrade_learned_guards_to_shadow(dry_run=True)["would_downgrade"] == 0

    # Post-downgrade audit shows nothing still enforcing.
    assert all(not r["still_enforcing"] for r in audit_auto_guards())


@spec_claim(SpecClaim(
    "GA-S08", "guard_authority", "§candidate backlog cleanup", "L2b",
    "prune_learned_guard_candidates deprecates learned candidates (soft, reversible) "
    "and never touches manual candidates awaiting owner review."))
def test_prune_deprecates_learned_candidates_only(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from aletheia.knowledge.frontmatter import parse_frontmatter, render_frontmatter
    from aletheia.analytics.guard_promotion import prune_learned_guard_candidates

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    cand_dir = tmp_path / "knowledge" / "guard_candidates"
    cand_dir.mkdir(parents=True)

    learned = {"type": "Trading Guard", "guard_id": "auto_momentum_long_x",
               "status": "candidate", "scope": "paper_entry", "auto_generated": True,
               "predicates": [{"field": "scanner_family", "op": "eq", "value": "momentum"}]}
    (cand_dir / "learned.md").write_text(render_frontmatter(learned, "body"), encoding="utf-8")

    manual = {"type": "Trading Guard", "guard_id": "manual-btc-guard",
              "status": "candidate", "scope": "paper_entry",
              "predicates": [{"field": "scanner_family", "op": "eq", "value": "momentum"}]}
    (cand_dir / "manual.md").write_text(render_frontmatter(manual, "body"), encoding="utf-8")

    # Old pre-fix candidate: auto_generated but NO guard_id — must still be pruned,
    # identified by file stem in the audit trail.
    legacy = {"type": "Trading Guard", "status": "candidate", "scope": "paper_entry",
              "auto_generated": True,
              "predicates": [{"field": "scanner_family", "op": "eq", "value": "momentum"}]}
    (cand_dir / "legacy_no_id.md").write_text(render_frontmatter(legacy, "body"), encoding="utf-8")

    # Dry-run: both learned candidates targeted (incl. the id-less one via stem); manual skipped.
    dry = prune_learned_guard_candidates(dry_run=True)
    assert dry["would_deprecate"] == 2
    assert set(dry["guard_ids"]) == {"auto_momentum_long_x", "legacy_no_id"}
    assert parse_frontmatter(cand_dir / "learned.md")[0]["status"] == "candidate"

    # Apply: learned deprecated (files kept), manual untouched.
    applied = prune_learned_guard_candidates(dry_run=False)
    assert applied["deprecated"] == 2
    assert parse_frontmatter(cand_dir / "learned.md")[0]["status"] == "deprecated"
    assert parse_frontmatter(cand_dir / "legacy_no_id.md")[0]["status"] == "deprecated"
    assert (cand_dir / "learned.md").exists()  # soft delete — file kept for audit
    assert parse_frontmatter(cand_dir / "manual.md")[0]["status"] == "candidate"

    # Idempotent: nothing left to prune.
    assert prune_learned_guard_candidates(dry_run=True)["would_deprecate"] == 0
