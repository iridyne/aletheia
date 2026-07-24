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

from tests.specs.spec_meta import SpecClaim, spec_claim

from aletheia.agent_shell.trading_context import (
    operator_decision_material_fingerprint,
    material_fingerprint_from_candidate_ctx,
)


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
