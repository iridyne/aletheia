"""Spec: Symbol-level material de-duplication fingerprint.

Source: docs plan sorted-kindling-thacker (#321), decision_lifecycle_contract
DL-INV-23 (new facts reopen) / DL-INV-24 (judge-word bypass forbidden).

universe refresh mints a fresh ``opportunity_id`` for the same symbol every
~10 min, defeating the per-opportunity fast-path and re-burning LLM re-evals on
a symbol already judged ``direct_skip``. ``operator_decision_material_fingerprint``
is the shared primitive that lets emit / the LLM fast-path / reassess recognise
"same setup, market has not materially moved" across that churning id.

The safety contract is entirely carried by this fingerprint:
  * it MUST be stable across opportunity_id / timestamp / feature_snapshot_id
    churn (otherwise suppression never fires — no dedup), AND
  * it MUST flip on any real material move — price/momentum/funding/regime/
    direction/market_type (otherwise a changed setup is wrongly suppressed,
    violating DL-INV-23), AND
  * it MUST NOT depend on free-text reasoning / trade_score (otherwise a
    judge-word or rank jitter leaks into the suppression key — DL-INV-24).

These claims are pure/offline (no LLM): the fingerprint is a deterministic hash.
"""

import json

from aletheia.agent_shell import commands
from aletheia.agent_shell.trading_context import (
    material_fingerprint_from_candidate_ctx,
    operator_decision_material_fingerprint,
    scope_operator_context_to_candidate,
)
from tests.specs.spec_meta import SpecClaim, spec_claim


def _fp(**overrides):
    base = dict(
        symbol="CHZ/USDT",
        direction="short",
        market_type="usdt_futures",
        scanner_family="momentum",
        features={
            "rsi_15m": 55.0,
            "return_1h": 0.42,
            "funding_rate": 0.0001,
            "oi_delta_1h": 2.0,
            "vol_ratio": 1.3,
        },
        regime="sideways",
    )
    base.update(overrides)
    return operator_decision_material_fingerprint(**base)


# ---------------------------------------------------------------------------
# MD-S01 — L2b: fingerprint is stable across carrier / snapshot / rank churn
# ---------------------------------------------------------------------------
@spec_claim(
    SpecClaim("MD-S01", "decision_lifecycle_contract", "§2.2b material fp", "L2b",
              "operator_decision_material_fingerprint is identical for two calls "
              "with the same symbol/direction/market_type/scanner_family/features/"
              "regime — it does not depend on opportunity_id, timestamps, or the "
              "feature_snapshot_id, so a fresh universe-refresh carrier for an "
              "unchanged setup hashes identically."),
)
def test_fingerprint_stable_across_carrier_churn():
    assert _fp() == _fp()
    # direction normalization: 'sell'/'bear' canonicalize to 'short'
    assert _fp(direction="short") == _fp(direction="sell") == _fp(direction="bear")
    # market_type normalization: futures aliases canonicalize together
    assert _fp(market_type="usdt_futures") == _fp(market_type="perp")


# ---------------------------------------------------------------------------
# MD-S02 — L2b: any real material move flips the fingerprint (DL-INV-23)
# ---------------------------------------------------------------------------
@spec_claim(
    SpecClaim("MD-S02", "decision_lifecycle_contract", "DL-INV-23 reopen", "L2b",
              "a genuine change to any material input — a technical feature, the "
              "regime, the direction, or the market_type — flips the fingerprint, "
              "so a materially-changed setup is never suppressed (reopens eval)."),
)
def test_fingerprint_flips_on_material_change():
    baseline = _fp()
    assert _fp(features={
        "rsi_15m": 70.0, "return_1h": 0.42, "funding_rate": 0.0001,
        "oi_delta_1h": 2.0, "vol_ratio": 1.3,
    }) != baseline, "rsi move must flip"
    assert _fp(regime="volatile") != baseline, "regime change must flip"
    assert _fp(direction="long") != baseline, "direction flip must flip"
    assert _fp(market_type="spot") != baseline, "market_type change must flip"
    assert _fp(scanner_family="mean_reversion") != baseline, "family change must flip"


# ---------------------------------------------------------------------------
# MD-S03 — L2b: funding_rate precision guards against over-suppression
# ---------------------------------------------------------------------------
@spec_claim(
    SpecClaim("MD-S03", "decision_lifecycle_contract", "DL-INV-23 precision", "L2b",
              "funding_rate is hashed at enough precision (~1e-4 scale) that a "
              "genuine funding shift flips the fingerprint rather than being "
              "flattened to 'unchanged' and over-suppressed."),
)
def test_funding_rate_precision_not_flattened():
    base = _fp(features={"funding_rate": 0.0001, "rsi_15m": 55.0})
    shifted = _fp(features={"funding_rate": 0.0009, "rsi_15m": 55.0})
    assert base != shifted, "a 4-digit funding move must flip the fingerprint"


# ---------------------------------------------------------------------------
# MD-S04 — L2b: free-text reasoning / rank never enter the fingerprint (DL-INV-24)
# ---------------------------------------------------------------------------
@spec_claim(
    SpecClaim("MD-S04", "decision_lifecycle_contract", "DL-INV-24 no bypass", "L2b",
              "the material fingerprint is computed only from symbol identity + "
              "material features + regime; reasoning prose, trade_score and "
              "feature_snapshot_id are not inputs, so a judge-word or rank jitter "
              "cannot leak into the suppression key."),
)
def test_ctx_adapter_ignores_reasoning_and_rank():
    def _ctx(reasoning: str, trade_score: float, snapshot_id: str) -> dict:
        return {
            "layer1": {
                "regime": "sideways",
                "feature_index": {
                    "k": {
                        "symbol": "CHZ/USDT",
                        "market_type": "usdt_futures",
                        "feature_snapshot_id": snapshot_id,
                        "rsi_15m": 55.0,
                        "return_1h": 0.42,
                        "funding_rate": 0.0001,
                        "oi_delta_1h": 2.0,
                        "vol_ratio": 1.3,
                    }
                },
            },
            "layer2": {
                "pending_signals": [
                    {
                        "opportunity_id": "opp-abc",
                        "symbol": "CHZ/USDT",
                        "direction": "short",
                        "market_type": "usdt_futures",
                        "scanner_family": "momentum",
                        "trade_score": trade_score,
                        "reasoning": reasoning,
                    }
                ]
            },
        }

    a = material_fingerprint_from_candidate_ctx(
        _ctx("bearish momentum", 71.0, "snap-1"), "opp-abc"
    )
    b = material_fingerprint_from_candidate_ctx(
        _ctx("TOTALLY different prose", 12.0, "snap-2"), "opp-abc"
    )
    assert a is not None and a == b, (
        "reasoning text, trade_score and feature_snapshot_id must not change the fp"
    )
    # A real feature move DOES flip it, proving the adapter reads features.
    moved = dict(a=None)
    ctx_moved = _ctx("bearish momentum", 71.0, "snap-1")
    ctx_moved["layer1"]["feature_index"]["k"]["rsi_15m"] = 80.0
    assert material_fingerprint_from_candidate_ctx(ctx_moved, "opp-abc") != a


# ---------------------------------------------------------------------------
# MD-S05 — L2b: adapter fails open (None) when the candidate is absent
# ---------------------------------------------------------------------------
@spec_claim(
    SpecClaim("MD-S05", "decision_lifecycle_contract", "§2.2b fail-open", "L2b",
              "material_fingerprint_from_candidate_ctx returns None when the "
              "opportunity is not present in the ctx, so callers evaluate rather "
              "than suppress on missing data (fail open)."),
)
def test_ctx_adapter_fails_open_when_candidate_missing():
    ctx = {"layer1": {"feature_index": {}}, "layer2": {"pending_signals": []}}
    assert material_fingerprint_from_candidate_ctx(ctx, "nope") is None


def test_stock_linked_context_excludes_crypto_priors_and_rotates_material_identity():
    from aletheia.agent_shell.trading_decision import _build_assessment_prompt
    from aletheia.data.symbols import (
        build_instrument_background,
        instrument_analysis_context,
    )

    background = build_instrument_background(
        symbol="SOXL/USDT:USDT",
        venue="binance",
        market_type="perp",
        market_info={
            "symbol": "SOXL/USDT:USDT",
            "base": "SOXL",
            "quote": "USDT",
            "info": {"underlyingType": "EQUITY"},
        },
    )
    analysis_context = instrument_analysis_context(background)
    candidate = {
        "opportunity_id": "opp-soxl",
        "signal_source": "market_observer",
        "symbol": "SOXL/USDT",
        "direction": "long",
        "market_type": "usdt_futures",
        "scanner_family": "momentum",
        "asset_id": "asset-soxl",
        "venue": "binance",
        "asset_class": "equity",
        "asset_subclass": "stock_linked_perp",
        "instrument_background": background,
        "analysis_context": analysis_context,
        "b2_evidence": {"status": "preferred", "directional_verdict": "long"},
    }
    feature = {
        "asset_id": "asset-soxl",
        "symbol": "SOXL/USDT",
        "market_type": "perp",
        "rsi_14": 58.0,
        "return_24h": 3.0,
        "funding_rate": 0.0002,
        "open_interest": 12_000_000.0,
        "relative_strength_24h": 91.0,
        "btc_beta": 4.0,
    }
    context = {
        "layer1": {
            "positions": [],
            "positions_meta": {"total": 0, "prompt_visible_count": 0},
            "regime": {
                "state": "bull",
                "confidence": 0.99,
                "long_bias": 0.9,
                "short_bias": 0.1,
            },
            "features": [feature.copy()],
            "feature_index": {"binance:perp:SOXL/USDT": feature.copy()},
            "opportunities": [{
                "id": "opp-soxl",
                "symbol": "SOXL/USDT",
                "market": "usdt_futures",
                "direction_bias": "long",
                "scanner_family": "momentum",
                "evidence_manifest": {},
            }],
        },
        "layer2": {
            "pending_signals": [candidate],
            "executable_candidates": [candidate.copy()],
            "outcome_stats": {"total_trades": 99},
            "per_symbol": [{"symbol": "SOXL/USDT", "win_rate": 100}],
            "factor_feedback": [{"status": "preferred"}],
        },
        "layer3": {
            "lessons": [{"content": "old crypto lesson"}],
            "ps_lessons": [{"content": "old crypto post-trade lesson"}],
            "guard_candidates": [{"name": "old crypto guard"}],
            "serenity_opportunities": [],
        },
    }

    global_regime_fp = material_fingerprint_from_candidate_ctx(context, "opp-soxl")
    context["layer1"]["regime"]["state"] = "bear"
    assert material_fingerprint_from_candidate_ctx(
        context, "opp-soxl"
    ) == global_regime_fp

    scoped = scope_operator_context_to_candidate(context, "opp-soxl")
    scoped_feature = scoped["layer1"]["features"][0]
    scoped_candidate = scoped["layer2"]["pending_signals"][0]
    assert scoped["layer1"]["regime"] is None
    assert scoped["layer1"]["regime_applicability"]["status"] == "not_applicable"
    assert scoped_feature["relative_strength_24h"] is None
    assert scoped_feature["btc_beta"] is None
    assert scoped_candidate["b2_evidence"]["status"] == "not_applicable"
    assert scoped["layer2"]["outcome_stats"] is None
    assert scoped["layer3"]["lessons"] == []

    prompt = _build_assessment_prompt(scoped)
    assert "SOXL/USDT is a Binance USDT perpetual" in prompt
    assert "+3x DAILY exposure" in prompt
    assert "ICESEMIT" in prompt
    assert "path-dependent" in prompt
    assert background["embedded_product"]["source_refs"][0] in prompt
    assert "RS24h=" not in prompt
    assert "BULL (conf=" not in prompt
    assert "BEAR (conf=" not in prompt

    legacy = operator_decision_material_fingerprint(
        symbol="SOXL/USDT",
        direction="long",
        market_type="perp",
        scanner_family="momentum",
        features=feature,
        regime=None,
    )
    background_aware = operator_decision_material_fingerprint(
        symbol="SOXL/USDT",
        direction="long",
        market_type="perp",
        scanner_family="momentum",
        features={
            **feature,
            "instrument_background": background,
            "analysis_context": analysis_context,
        },
        regime=None,
    )
    assert background_aware != legacy


@spec_claim(
    SpecClaim("MD-S06", "decision_lifecycle_contract", "DL-INV-23 material dedup consumer", "L2b",
              "the pre-inference reader suppresses a fresh carrier only when its symbol, "
              "direction, market type, material fingerprint, B2 policy epoch, and cost-model "
              "identity match an active defer; any changed or missing identity reopens evaluation."),
)
def test_symbol_level_defer_reader_uses_material_fingerprint(tmp_path, monkeypatch):
    """The existing B1.6 consumer, not a generic prompt cache, owns re-eval suppression."""
    from aletheia.storage.models import Opportunity
    from aletheia.storage.repositories import insert_opportunity
    from aletheia.storage.sqlite import get_db
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    db = get_db()
    now = "2999-01-01T00:00:00+00:00"
    insert_opportunity(Opportunity(
        id="fresh-carrier",
        symbol="CHZ/USDT",
        market="usdt_futures",
        direction_bias="short",
        valid_until=now,
    ))
    snapshot = {
        "opportunity_id": "fresh-carrier",
        "symbol": "CHZ/USDT",
        "direction": "short",
        "market_type": "perp",
        "material_fingerprint": "same-material",
        "b2_policy_epoch_id": "b2-current",
        "cost_model_version": "v1",
        "defer_until": now,
        "defer_reason": "operator_low_conviction",
    }
    db.execute(
        "INSERT INTO operator_evaluation_events "
        "(id,dedup_key,event_kind,trigger,pending_unresolved_total,"
        "pending_visible_count,queue_snapshot_json,data_health_json,policy_epoch_id,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "dedup-fixture", "dedup-fixture-key",
            "operator_decision_deferred_low_conviction", "test", 1, 1,
            json.dumps(snapshot), "{}", "b1.6", now,
        ),
    )
    db.commit()

    hit = commands._existing_deferred_context_observation(
        "different-context-fingerprint",
        opportunity_id="fresh-carrier",
        policy_epoch_id="b1.6",
        window_minutes=120,
        symbol="CHZ/USDT",
        direction="short",
        market_type="usdt_futures",
        material_fingerprint="same-material",
        b2_policy_epoch_id="b2-current",
        cost_model_version="v1",
        symbol_level=True,
    )
    assert hit and hit["id"] == "dedup-fixture"
    assert commands._existing_deferred_context_observation(
        "different-context-fingerprint",
        opportunity_id="fresh-carrier",
        policy_epoch_id="b1.6",
        window_minutes=120,
        symbol="CHZ/USDT",
        direction="short",
        market_type="usdt_futures",
        material_fingerprint="changed-material",
        b2_policy_epoch_id="b2-current",
        cost_model_version="v1",
        symbol_level=True,
    ) is None
    for b2_epoch, cost_model in (("b2-rotated", "v1"), ("b2-current", "v2")):
        assert commands._existing_deferred_context_observation(
            "different-context-fingerprint",
            opportunity_id="fresh-carrier",
            policy_epoch_id="b1.6",
            window_minutes=120,
            symbol="CHZ/USDT",
            direction="short",
            market_type="usdt_futures",
            material_fingerprint="same-material",
            b2_policy_epoch_id=b2_epoch,
            cost_model_version=cost_model,
            symbol_level=True,
        ) is None

    assert commands._existing_deferred_context_observation(
        "different-context-fingerprint",
        opportunity_id="fresh-carrier",
        policy_epoch_id="b1.6",
        window_minutes=120,
        b2_policy_epoch_id="b2-current",
        cost_model_version="v1",
        symbol_level=False,
    )
    assert commands._existing_deferred_context_observation(
        "different-context-fingerprint",
        opportunity_id="fresh-carrier",
        policy_epoch_id="b1.6",
        window_minutes=120,
        b2_policy_epoch_id="b2-rotated",
        cost_model_version="v1",
        symbol_level=False,
    ) is None

    legacy = {**snapshot, "material_fingerprint": "legacy-material"}
    legacy.pop("b2_policy_epoch_id")
    legacy.pop("cost_model_version")
    db.execute(
        "INSERT INTO operator_evaluation_events "
        "(id,dedup_key,event_kind,trigger,pending_unresolved_total,"
        "pending_visible_count,queue_snapshot_json,data_health_json,policy_epoch_id,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "legacy-fixture", "legacy-fixture-key",
            "operator_decision_deferred_low_conviction", "test", 1, 1,
            json.dumps(legacy), "{}", "b1.6", now,
        ),
    )
    db.commit()
    assert commands._existing_deferred_context_observation(
        "different-context-fingerprint",
        opportunity_id="fresh-carrier",
        policy_epoch_id="b1.6",
        window_minutes=120,
        symbol="CHZ/USDT",
        direction="short",
        market_type="usdt_futures",
        material_fingerprint="legacy-material",
        b2_policy_epoch_id="b2-current",
        cost_model_version="v1",
        symbol_level=True,
    ) is None
