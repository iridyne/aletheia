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
