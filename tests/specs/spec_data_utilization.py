"""Spec: Data Utilization Contract — every acquired data domain must be routed
to a consuming module (物尽其用 / no orphan data).

First principle: data exists to inform a decision. A persisted datum with no
consumer is pure cost (I/O + storage + audit noise) AND a hazard — it fabricates
a sense of richness ("we have OI data") that can mislead. So every data domain
must EITHER have a declared decision-chain consumer OR be explicitly marked
observation-only. These claims lock the consumers that close known gaps and
guard against regression back to collected-but-unused.

Claims attach to data_utilization_contract.

WARNING — CONFORMANCE COLLECTION TRAP:
Add this file to _import_all_specs() in tests/test_spec_conformance.py.
Without that registration the conformance matrix collects zero DU-* cases.
"""

from __future__ import annotations

from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim

_ROOT = Path(__file__).parent.parent.parent
_MARKET_OBS = _ROOT / "aletheia" / "agents" / "market_observer.py"
_CONTEXT_BUILDER = _ROOT / "aletheia" / "data" / "context_builder.py"

_CONTRACT = "data_utilization_contract"


# ---------------------------------------------------------------------------
# DU-S01 — render hook for the cross-venue (Hyperliquid) funding signal exists
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "DU-S01", _CONTRACT, "§cross-venue funding", "L2a",
        "render_enriched_context_md accepts an xvenue_funding parameter — the "
        "render hook that routes the Hyperliquid feed into the LLM context.",
    ),
)
def test_render_has_xvenue_funding_param():
    import inspect
    from aletheia.data.context_builder import render_enriched_context_md
    sig = inspect.signature(render_enriched_context_md)
    assert "xvenue_funding" in sig.parameters, (
        "render_enriched_context_md must expose xvenue_funding to consume Hyperliquid"
    )


# ---------------------------------------------------------------------------
# DU-S02 — Hyperliquid feed is ROUTED to the decision context (no longer orphan)
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "DU-S02", _CONTRACT, "§no-orphan: hyperliquid", "L2c",
        "market_observer reads the hyperliquid-tagged funding rows AND passes "
        "xvenue_funding into the rendered LLM context — i.e. the acquired "
        "Hyperliquid data reaches a decision consumer, not just collector/doctor.",
    ),
)
def test_hyperliquid_routed_to_decision_context():
    src = _MARKET_OBS.read_text(encoding="utf-8")
    # the cross-venue read (explicit exchange filter) ...
    assert 'exchange="hyperliquid"' in src or "exchange='hyperliquid'" in src, (
        "market_observer must read hyperliquid funding via explicit exchange filter"
    )
    # ... and it must actually flow into the rendered context
    assert "xvenue_funding" in src, (
        "market_observer must pass xvenue_funding into render_enriched_context_md"
    )


# ---------------------------------------------------------------------------
# DU-S03 — DefiLlama on-chain feed is ROUTED to the decision context
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "DU-S03", _CONTRACT, "§no-orphan: onchain", "L2c",
        "market_observer reads latest_onchain_snapshots AND passes onchain= into "
        "the rendered LLM context — DefiLlama on-chain is consumed, not orphan.",
    ),
)
def test_onchain_routed_to_decision_context():
    src = _MARKET_OBS.read_text(encoding="utf-8")
    assert "latest_onchain_snapshots" in src, "market_observer must read onchain snapshots"
    assert "onchain=" in src, "market_observer must pass onchain= into render"


# ---------------------------------------------------------------------------
# DU-B01 — cross-venue funding renders an annualized divergence (behavioral)
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "DU-B01", _CONTRACT, "§cross-venue funding", "L2b",
        "Given Hyperliquid + primary annualized funding, render emits a "
        "'Cross-venue funding' section comparing ANNUALIZED rates (HL 1h vs "
        "primary 8h interval are not comparable raw) with the divergence in pp.",
    ),
)
def test_cross_venue_funding_renders_annualized_divergence():
    from aletheia.data.context_builder import render_enriched_context_md
    xv = {"BTC/USDT": {"hl_ann": 0.109, "hl_predicted_ann": 0.120, "primary_ann": 0.055}}
    out = render_enriched_context_md(["BTC/USDT"], {}, {}, None, xvenue_funding=xv)
    assert "Cross-venue funding" in out, "cross-venue section missing"
    assert "Hyperliquid" in out and "primary" in out, "must compare HL vs primary"
    # 0.109 - 0.055 = 0.054 → +5.4pp divergence
    assert "+5.4pp" in out, f"annualized divergence (pp) wrong in: {out!r}"
    assert "predicted" in out, "predicted funding should be surfaced when present"


# ---------------------------------------------------------------------------
# DU-B02 — no Hyperliquid data for a symbol ⇒ section omitted (no noise)
# ---------------------------------------------------------------------------

@spec_claim(
    SpecClaim(
        "DU-B02", _CONTRACT, "§cross-venue funding", "L2b",
        "When a symbol has no Hyperliquid funding, the cross-venue section is "
        "omitted entirely (no empty/garbage rows leak into the prompt).",
    ),
)
def test_cross_venue_funding_omitted_when_no_hl_data():
    from aletheia.data.context_builder import render_enriched_context_md
    out = render_enriched_context_md(
        ["SOL/USDT"], {}, {}, None, xvenue_funding={"SOL/USDT": {"hl_ann": None}}
    )
    assert "Cross-venue funding" not in out, "must not render an empty cross-venue section"
