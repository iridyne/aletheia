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
  - a read-only audit surfaces learned guards the old chain already activated.
"""

from __future__ import annotations

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

    # auto_generated flag alone (no auto_ id) also triggers it
    flagged = _force_shadow_only_if_auto({"guard_id": "x", "auto_generated": True})
    assert flagged["shadow_only"] is True

    # owner-authored (manual) candidate is left alone
    manual = _force_shadow_only_if_auto({"guard_id": "manual-btc-guard", "block_candidate": True})
    assert not manual.get("shadow_only")
    assert manual["block_candidate"] is True


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


@spec_claim(SpecClaim(
    "GA-S12", "guard_authority", "§owner read model", "L2a",
    "Guard API projection separates lifecycle, provenance, proposed effect, experiment context, assignment authority, and baseline effect."))
def test_guard_api_projection_names_active_learned_guard_as_observe_only():
    from aletheia.knowledge.guards import TradingGuard
    from aletheia.server.routes.data import _guard_response

    guard = TradingGuard(
        guard_id="auto_momentum_btc",
        title="Momentum losses",
        status="active",
        enforcement="blocking",
        block_candidate=False,
        score_penalty=0.0,
        proposed_block_candidate=True,
        proposed_score_penalty=12.0,
        shadow_only=True,
        tags=["auto-generated", "shadow-only"],
    )

    row = _guard_response(guard, {
        "experiment_id": "experiment-1",
        "stage": "paper_10",
        "status": "active",
    })

    assert row["lifecycle_status"] == "active_observe"
    assert row["origin"] == "learned"
    assert row["shadow_only"] is True
    assert row["proposed_effect"] == {"block_candidate": True, "score_penalty": 12.0}
    assert row["experiment_id"] is None
    assert row["experiment_stage"] is None
    assert row["assignment"] is None
    assert row["authority_granted"] is False
    assert row["authority_scope"] == "unsupported"
    assert row["actual_effect"] == {"block_candidate": False, "score_penalty": 0.0}
    assert row["activation_effect"] == {"block_candidate": False, "score_penalty": 0.0}


@spec_claim(SpecClaim(
    "GA-S13", "guard_authority", "§owner read model", "L2a",
    "Owner-authored active guards remain visibly distinct from learned experiment-gated guards."))
def test_guard_api_projection_keeps_owner_guard_effect_truthful():
    from aletheia.knowledge.guards import TradingGuard
    from aletheia.server.routes.data import _guard_response

    row = _guard_response(TradingGuard(
        guard_id="manual-btc-guard",
        title="Owner guard",
        status="active",
        enforcement="blocking",
        block_candidate=True,
        score_penalty=5.0,
    ))

    assert row["lifecycle_status"] == "active_enforced"
    assert row["origin"] == "manual"
    assert row["authority_granted"] is True
    assert row["authority_scope"] == "guard"
    assert row["actual_effect"] == {"block_candidate": True, "score_penalty": 5.0}

    disabled = _guard_response(TradingGuard(
        guard_id="manual-btc-guard",
        title="Owner guard",
        status="active",
        enforcement="blocking",
        block_candidate=True,
        score_penalty=5.0,
    ), evaluation_enabled=False)
    assert disabled["lifecycle_status"] == "active_observe"
    assert disabled["authority_granted"] is True
    assert disabled["actual_effect"] == {"block_candidate": False, "score_penalty": 0.0}

    candidate = _guard_response(TradingGuard(
        guard_id="manual-new-guard",
        title="Owner candidate",
        status="candidate",
    ), evaluation_enabled=False)
    assert candidate["proposed_effect"] == {
        "block_candidate": False,
        "score_penalty": 0.0,
    }
    assert candidate["activation_effect"] == {
        "block_candidate": True,
        "score_penalty": 10.0,
    }

    manual_shadow = _guard_response(TradingGuard(
        guard_id="manual-shadow",
        title="Owner shadow candidate",
        status="candidate",
        shadow_only=True,
        block_candidate=True,
        score_penalty=7.0,
    ))
    assert manual_shadow["activation_effect"] == {
        "block_candidate": False,
        "score_penalty": 0.0,
    }

    negative = _guard_response(TradingGuard(
        guard_id="manual-negative",
        title="Owner negative candidate",
        status="candidate",
        score_penalty=-5.0,
    ))
    assert negative["proposed_effect"]["score_penalty"] == -5.0
    assert negative["activation_effect"] == {
        "block_candidate": True,
        "score_penalty": -5.0,
    }


@spec_claim(SpecClaim(
    "GA-S14", "guard_authority", "§owner read model", "L2a",
    "Guard API exposes no experiment context while ranker_learned_guard is measurement-unsupported."))
def test_guard_api_projection_has_no_unsupported_experiment_context():
    from aletheia.server.routes.data import _guard_experiments_by_id

    assert _guard_experiments_by_id() == {}


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


# ---------------------------------------------------------------------------
# Issue #195 — Learned guard TTL lifecycle
# ---------------------------------------------------------------------------

@spec_claim(SpecClaim(
    "GA-S09", "guard_authority", "§TTL stamping at creation", "L2a",
    "Every auto-generated guard candidate is stamped with created_at and expires_at "
    "(now + LEARNED_GUARD_TTL_DAYS) at creation (#195). Manual candidates are never stamped."))
def test_auto_candidate_gets_ttl_stamps():
    from aletheia.knowledge.manager import _force_shadow_only_if_auto
    from aletheia.knowledge.guards import LEARNED_GUARD_TTL_DAYS
    from datetime import datetime, timedelta, timezone

    before = datetime.now(timezone.utc)
    meta = _force_shadow_only_if_auto({"guard_id": "auto_momentum_btc", "auto_generated": True})
    after = datetime.now(timezone.utc)

    assert meta.get("created_at") is not None, "created_at must be stamped"
    assert meta.get("expires_at") is not None, "expires_at must be stamped"

    created = datetime.fromisoformat(str(meta["created_at"]).replace("Z", "+00:00"))
    expires = datetime.fromisoformat(str(meta["expires_at"]).replace("Z", "+00:00"))
    assert before <= created <= after

    expected_delta = timedelta(days=LEARNED_GUARD_TTL_DAYS)
    assert abs((expires - created) - expected_delta).total_seconds() < 2

    # Manual candidates: no stamps added.
    manual = _force_shadow_only_if_auto({"guard_id": "manual-btc-guard"})
    assert manual.get("created_at") is None
    assert manual.get("expires_at") is None


@spec_claim(SpecClaim(
    "GA-S10", "guard_authority", "§loader excludes expired learned guards", "L2a",
    "load_trading_guards silently skips learned guards past their expires_at at load time (#195). "
    "Non-expired and seed/manual guards are always visible."))
def test_loader_excludes_expired_learned_guards(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from datetime import datetime, timedelta, timezone
    from aletheia.knowledge.frontmatter import render_frontmatter
    from aletheia.knowledge.guards import load_trading_guards

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    guards_dir = tmp_path / "knowledge" / "guards"
    guards_dir.mkdir(parents=True)

    now = datetime.now(timezone.utc)

    expired_meta = {
        "type": "Trading Guard", "guard_id": "auto_expired",
        "title": "Expired learned", "status": "active", "scope": "paper_entry",
        "enforcement": "advisory", "block_candidate": False, "score_penalty": 0,
        "applies_to": {}, "predicates": [],
        "auto_generated": True, "tags": ["auto-generated", "shadow-only"],
        "created_at": (now - timedelta(days=45)).isoformat(),
        "expires_at": (now - timedelta(days=15)).isoformat(),  # in the past
    }
    (guards_dir / "expired.md").write_text(render_frontmatter(expired_meta, ""), encoding="utf-8")

    fresh_meta = {
        "type": "Trading Guard", "guard_id": "auto_fresh",
        "title": "Fresh learned", "status": "active", "scope": "paper_entry",
        "enforcement": "advisory", "block_candidate": False, "score_penalty": 0,
        "applies_to": {}, "predicates": [],
        "auto_generated": True, "tags": ["auto-generated", "shadow-only"],
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=30)).isoformat(),  # future
    }
    (guards_dir / "fresh.md").write_text(render_frontmatter(fresh_meta, ""), encoding="utf-8")

    seed_meta = {
        "type": "Trading Guard", "guard_id": "repeated_losing_factor_v1",
        "title": "Seed guard", "status": "active", "scope": "paper_entry",
        "enforcement": "blocking", "block_candidate": True, "score_penalty": 0,
        "applies_to": {}, "predicates": [], "tags": [],
    }
    (guards_dir / "seed.md").write_text(render_frontmatter(seed_meta, ""), encoding="utf-8")

    loaded_ids = {g.guard_id for g in load_trading_guards(status="active")}
    assert "auto_expired" not in loaded_ids, "expired learned guard must be excluded"
    assert "auto_fresh" in loaded_ids, "non-expired learned guard must be visible"
    assert "repeated_losing_factor_v1" in loaded_ids, "seed guard must always be visible"


@spec_claim(SpecClaim(
    "GA-S11", "guard_authority", "§legacy migration deadline", "L2a",
    "Learned guards without created_at or expires_at receive the bounded migration deadline "
    "LEARNED_GUARD_MIGRATION_DEADLINE_ISO rather than living forever (#195)."))
def test_effective_expires_at_precedence():
    from aletheia.knowledge.guards import (
        LEARNED_GUARD_MIGRATION_DEADLINE_ISO,
        LEARNED_GUARD_TTL_DAYS,
        _effective_learned_expires_at,
    )
    from datetime import datetime, timedelta, timezone

    # Explicit expires_at wins.
    explicit = "2027-01-01T00:00:00+00:00"
    assert _effective_learned_expires_at({"expires_at": explicit}) == explicit

    # created_at fallback: result is created_at + TTL days.
    created = datetime(2026, 6, 1, tzinfo=timezone.utc)
    result = _effective_learned_expires_at({"created_at": created.isoformat()})
    expected = (created + timedelta(days=LEARNED_GUARD_TTL_DAYS)).isoformat()
    assert result == expected

    # No dates: migration deadline.
    assert _effective_learned_expires_at({}) == LEARNED_GUARD_MIGRATION_DEADLINE_ISO
    assert _effective_learned_expires_at({"guard_id": "auto_old"}) == LEARNED_GUARD_MIGRATION_DEADLINE_ISO


@spec_claim(SpecClaim(
    "GA-B05", "guard_authority", "§daily soft-deprecation pass", "L2b",
    "_deprecate_expired_learned_guards marks expired learned guards deprecated in their markdown "
    "files. Manual and seed guards are never touched. Returns the count deprecated (#195)."))
def test_deprecate_expired_learned_guards(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from datetime import datetime, timedelta, timezone
    from aletheia.knowledge.frontmatter import parse_frontmatter, render_frontmatter
    from aletheia.analytics.guard_promotion import _deprecate_expired_learned_guards

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    guards_dir = tmp_path / "knowledge" / "guards"
    candidates_dir = tmp_path / "knowledge" / "guard_candidates"
    guards_dir.mkdir(parents=True)
    candidates_dir.mkdir(parents=True)

    now = datetime.now(timezone.utc)

    # Expired learned active guard.
    exp_active = {
        "type": "Trading Guard", "guard_id": "auto_expired_active",
        "status": "active", "scope": "paper_entry", "auto_generated": True,
        "tags": ["auto-generated", "shadow-only"], "predicates": [],
        "created_at": (now - timedelta(days=45)).isoformat(),
        "expires_at": (now - timedelta(days=15)).isoformat(),
    }
    (guards_dir / "expired_active.md").write_text(render_frontmatter(exp_active, ""), encoding="utf-8")

    # Expired learned candidate.
    exp_cand = {
        "type": "Trading Guard", "guard_id": "auto_expired_cand",
        "status": "candidate", "scope": "paper_entry", "auto_generated": True,
        "tags": ["auto-generated", "shadow-only"], "predicates": [],
        "created_at": (now - timedelta(days=40)).isoformat(),
        "expires_at": (now - timedelta(days=10)).isoformat(),
    }
    (candidates_dir / "expired_cand.md").write_text(render_frontmatter(exp_cand, ""), encoding="utf-8")

    # Non-expired learned guard (should NOT be touched).
    fresh = {
        "type": "Trading Guard", "guard_id": "auto_fresh",
        "status": "active", "scope": "paper_entry", "auto_generated": True,
        "tags": ["auto-generated", "shadow-only"], "predicates": [],
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=30)).isoformat(),
    }
    (guards_dir / "fresh.md").write_text(render_frontmatter(fresh, ""), encoding="utf-8")

    # Manual active guard (must never be touched by the expiry pass).
    manual = {
        "type": "Trading Guard", "guard_id": "manual-old",
        "status": "active", "scope": "paper_entry", "predicates": [], "tags": [],
    }
    (guards_dir / "manual.md").write_text(render_frontmatter(manual, ""), encoding="utf-8")

    count = _deprecate_expired_learned_guards()
    assert count == 2

    assert parse_frontmatter(guards_dir / "expired_active.md")[0]["status"] == "deprecated"
    assert parse_frontmatter(guards_dir / "expired_active.md")[0]["deprecated_reason"] == "ttl_expired"
    assert parse_frontmatter(candidates_dir / "expired_cand.md")[0]["status"] == "deprecated"
    assert parse_frontmatter(guards_dir / "fresh.md")[0]["status"] == "active"  # not touched
    assert parse_frontmatter(guards_dir / "manual.md")[0]["status"] == "active"  # not touched

    # Idempotent: already-deprecated guards skipped.
    assert _deprecate_expired_learned_guards() == 0


@spec_claim(SpecClaim(
    "GA-B06", "guard_authority", "§promotion refusal of expired candidates", "L2b",
    "promote_guard_candidate_to_active refuses to promote a learned candidate whose "
    "effective expires_at is in the past and returns False (#195)."))
def test_promote_refuses_expired_learned_candidate(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from datetime import datetime, timedelta, timezone
    from aletheia.knowledge.frontmatter import parse_frontmatter, render_frontmatter
    from aletheia.analytics.guard_promotion import promote_guard_candidate_to_active

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    candidates_dir = tmp_path / "knowledge" / "guard_candidates"
    candidates_dir.mkdir(parents=True)

    now = datetime.now(timezone.utc)

    expired_cand = {
        "type": "Trading Guard", "guard_id": "auto_expired_prom",
        "title": "Expired candidate", "status": "candidate", "scope": "paper_entry",
        "auto_generated": True, "tags": ["auto-generated", "shadow-only"], "predicates": [],
        "created_at": (now - timedelta(days=45)).isoformat(),
        "expires_at": (now - timedelta(days=15)).isoformat(),
    }
    (candidates_dir / "expired_prom.md").write_text(render_frontmatter(expired_cand, ""), encoding="utf-8")

    result = promote_guard_candidate_to_active("auto_expired_prom")
    assert result is False, "Promotion of an expired learned candidate must return False"

    # File must remain untouched (candidate, not deprecated by promotion refusal).
    meta, _ = parse_frontmatter(candidates_dir / "expired_prom.md")
    assert meta["status"] == "candidate"


@spec_claim(SpecClaim(
    "GA-B07", "guard_authority", "§expiry deprecation runs even when auto-promote is disabled", "L2b",
    "run_promotion_check still runs _deprecate_expired_learned_guards when auto_promote_enabled=False "
    "and returns expired_deprecated in the skipped result (#195)."))
def test_expiry_deprecation_runs_when_auto_promote_disabled(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from datetime import datetime, timedelta, timezone
    from aletheia.knowledge.frontmatter import parse_frontmatter, render_frontmatter
    from aletheia.analytics import guard_promotion as gp

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    guards_dir = tmp_path / "knowledge" / "guards"
    guards_dir.mkdir(parents=True)

    now = datetime.now(timezone.utc)
    exp = {
        "type": "Trading Guard", "guard_id": "auto_should_deprecate",
        "status": "active", "scope": "paper_entry", "auto_generated": True,
        "tags": ["auto-generated", "shadow-only"], "predicates": [],
        "created_at": (now - timedelta(days=45)).isoformat(),
        "expires_at": (now - timedelta(days=1)).isoformat(),
    }
    (guards_dir / "should_deprecate.md").write_text(render_frontmatter(exp, ""), encoding="utf-8")

    with patch.object(gp, "load_config", return_value=MagicMock(
        guard=MagicMock(auto_promote_enabled=False)
    )):
        result = gp.run_promotion_check()

    assert result.get("skipped") is True
    assert result.get("expired_deprecated") == 1
    assert parse_frontmatter(guards_dir / "should_deprecate.md")[0]["status"] == "deprecated"
