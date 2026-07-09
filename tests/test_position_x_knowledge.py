from __future__ import annotations

import json

from aletheia.data.position_x import (
    extract_position_x_knowledge_cards,
    extract_position_x_material_bundle,
    load_position_x_materials,
)


def test_extract_position_x_knowledge_cards_redacts_sensitive_fields():
    materials = {
        "signals": {
            "symbol": "SNDK/USDT",
            "data": {
                "structureRead": (
                    "Near-term supply-led: $25.48M overhead longs vs $4.41M trapped "
                    "shorts below. Rank #25 (z -0.90, higher = more bullish)."
                ),
                "dimensions": [
                    {
                        "key": "cgo",
                        "label": "Trapped-cost overhang (CGO)",
                        "what": "Book-wide balance of underwater longs above price vs underwater shorts below price.",
                        "direction": "bearish",
                        "strength": "medium",
                        "percentile": 13.333333333333334,
                        "detail": "support below $5.69M vs overhead above $30.44M",
                    }
                ],
                "compositeZ": -0.9,
                "rank": 25,
                "universeSize": 30,
            },
            "api_key": "must-not-survive",
            "token": "must-not-survive",
        },
        "prompt_templates": {
            "templates": [
                {
                    "id": "tpl-1",
                    "name": "Crypto Strategy Template",
                    "prompt_type": "system",
                    "content": (
                        "Trade BTC. Use 4H to determine the trend and 15m to execute trades. "
                        "Only trade pullbacks in trending conditions."
                    ),
                    "credential": "must-not-survive",
                }
            ]
        },
        "strategy_details": [
            {
                "id": "strat-1",
                "name": "Observation-only structure playbook",
                "risk_control": {"max_leverage": 2, "kill_switch": "manual_only"},
                "prompts": {"system": "Never auto-trade from a single external signal."},
            }
        ],
    }

    cards = extract_position_x_knowledge_cards(materials)
    assert cards, "expected knowledge cards from safe Position X materials"

    card_types = {card.card_type for card in cards}
    assert {
        "dimension_definition",
        "agent_prompt_pattern",
        "risk_boundary",
    } <= card_types

    rendered = "\n".join(
        "\n".join(
            [
                card.summary,
                card.agent_template,
                card.risk_caveat,
                card.action_boundary,
            ]
        )
        for card in cards
    )
    assert "must-not-survive" not in rendered
    assert "api_key" not in rendered
    assert "credential" not in rendered
    assert all(card.source_hash for card in cards)
    assert any(card.key == "dimension:cgo" for card in cards)


def test_position_x_knowledge_card_source_hash_is_stable():
    materials_a = {
        "signals": {
            "symbol": "BTC/USDT",
            "data": {
                "dimensions": [
                    {
                        "key": "lfa",
                        "label": "Liquidation-fuel imbalance (LFA)",
                        "what": "Short liquidations above vs long liquidations below.",
                        "detail": "short above $335M vs long below $220M",
                    }
                ]
            },
        }
    }
    materials_b = {
        "signals": {
            "data": {
                "dimensions": [
                    {
                        "detail": "short above $335M vs long below $220M",
                        "what": "Short liquidations above vs long liquidations below.",
                        "label": "Liquidation-fuel imbalance (LFA)",
                        "key": "lfa",
                    }
                ]
            },
            "symbol": "BTC/USDT",
        }
    }

    card_a = next(card for card in extract_position_x_knowledge_cards(materials_a) if card.key == "dimension:lfa")
    card_b = next(card for card in extract_position_x_knowledge_cards(materials_b) if card.key == "dimension:lfa")

    assert card_a.source_hash == card_b.source_hash


def test_load_position_x_materials_supports_evidence_layout(tmp_path):
    evidence_dir = tmp_path / "04_evidence"
    evidence_dir.mkdir()
    (evidence_dir / "sndk_intelligence_signals.json").write_text(
        json.dumps({"symbol": "SNDK/USDT", "data": {"dimensions": []}}),
        encoding="utf-8",
    )
    (evidence_dir / "prompt_templates.json").write_text(
        json.dumps({"templates": []}),
        encoding="utf-8",
    )
    (evidence_dir / "analysis_BTC.json").write_text(
        json.dumps({"symbol": "BTC", "action": "OPEN_LONG"}),
        encoding="utf-8",
    )
    (evidence_dir / "wallets.json").write_text(
        json.dumps({"addresses": ["0xabc"]}),
        encoding="utf-8",
    )

    materials = load_position_x_materials(tmp_path)

    assert "signals" in materials
    assert "prompt_templates" in materials
    assert len(materials["analysis_samples"]) == 1
    assert "wallets" not in materials


def test_extract_position_x_knowledge_cards_include_fit_and_analysis_patterns():
    materials = {
        "signals": {
            "data": {
                "structureRead": "Positioning facts only.",
                "dimensions": [
                    {
                        "key": "lfa",
                        "label": "Liquidation-fuel imbalance (LFA)",
                        "what": "Short liquidations above vs long liquidations below.",
                        "detail": "short above $335M vs long below $220M",
                    }
                ],
            }
        },
        "composite_fit": [
            {
                "sym": "BTC",
                "compositeZ": 0.9,
                "rank": 5,
                "N": 30,
                "dims": {"cgo": 66.7, "vap": 53.3, "skw": 80.0, "lfa": 93.3, "wp": 70.0},
            },
            {
                "sym": "xyz:SNDK",
                "compositeZ": -0.87,
                "rank": 25,
                "N": 30,
                "dims": {"cgo": 13.3, "vap": 16.7, "skw": 20.0, "lfa": 60.0, "wp": 10.0},
            },
        ],
        "dimension_fit": [
            {
                "sym": "BTC",
                "metrics": {
                    "shortLiqAbove": 335_141_283.44,
                    "longLiqBelow": 220_161_274.01,
                    "gLong": -0.0398,
                    "gShort": 0.0167,
                    "convexity": 0.1252,
                    "cascadeVulnPct": 0.0955,
                },
                "pct": {"lfa": 93.3, "cgo": 63.3, "convexity": None, "cascade": None},
            },
            {
                "sym": "xyz:SNDK",
                "metrics": {
                    "shortLiqAbove": 5_995_356.77,
                    "longLiqBelow": 12_496_287.82,
                    "gLong": 0.0003,
                    "gShort": 0.0299,
                    "convexity": 0.2263,
                    "cascadeVulnPct": 0.0006,
                },
                "pct": {"lfa": 60.0, "cgo": 13.3, "convexity": None, "cascade": None},
            },
        ],
        "analysis_samples": [
            {
                "symbol": "BTC",
                "action": "OPEN_LONG",
                "reasoning": "Cost-basis POC acts as support and a short-liq cluster sits above.",
                "structure": {
                    "bias": "bullish",
                    "rank": 7,
                    "universe_size": 30,
                    "levels": {"poc": 62267.64, "support": 62267.64, "value_area_low": 58614.05},
                    "positioning": {"top10": "0.21", "top50": "0.44"},
                    "whale": {
                        "net": "mixed",
                        "rows": [{"address": "0xabc", "account_value": "1000"}],
                    },
                },
            }
        ],
    }

    cards = extract_position_x_knowledge_cards(materials)
    by_key = {card.key: card for card in cards}

    assert "formula:composite_research_prior_v1" in by_key
    assert "formula:lfa_cross_sectional_rank" in by_key
    assert "formula:cgo_cross_sectional_rank" in by_key
    assert "formula:neutral_risk_context" in by_key
    assert "analysis_pattern:btc" in by_key
    assert "correlation" in by_key["formula:lfa_cross_sectional_rank"].summary.lower()
    assert "whale net" in by_key["analysis_pattern:btc"].summary.lower()
    rendered = "\n".join(card.summary + "\n" + card.agent_template for card in cards)
    assert "0xabc" not in rendered
    assert "account_value" not in rendered


def test_extract_position_x_material_bundle_returns_cards_and_snapshot(tmp_path):
    evidence_dir = tmp_path / "04_evidence"
    evidence_dir.mkdir()
    (evidence_dir / "sndk_intelligence_signals.json").write_text(
        json.dumps(
            {
                "symbol": "SNDK/USDT",
                "data": {
                    "bias": "bearish",
                    "structureRead": "Supply-led with overhead resistance.",
                    "dimensions": [
                        {
                            "key": "cgo",
                            "direction": "bearish",
                            "strength": "medium",
                            "percentile": 13.333333333333334,
                            "detail": "support below $5.69M vs overhead above $30.44M",
                        }
                    ],
                    "levels": {"poc": 1960.2759, "valueAreaLow": 1811.4711, "valueAreaHigh": 2002.7916},
                    "metrics": {
                        "shortLiqAbove": 5_909_524.00,
                        "longLiqBelow": 12_584_565.05,
                        "cascadeVulnPct": 0.0042413245,
                        "top10Pct": 0.4843475373,
                        "convexity": 0.2279036709,
                        "includedPositions": 2249,
                        "state": "complete",
                    },
                    "compositeZ": -0.9,
                    "rank": 25,
                    "universeSize": 30,
                },
                "wallets": ["must-not-survive"],
                "credential": "must-not-survive",
            }
        ),
        encoding="utf-8",
    )
    (evidence_dir / "prompt_templates.json").write_text(
        json.dumps({"templates": [{"name": "Crypto Strategy Template", "content": "Use 4H and 15m for pullbacks."}]}),
        encoding="utf-8",
    )
    (evidence_dir / "composite_z_fit_data.json").write_text(
        json.dumps([{"sym": "SNDK", "compositeZ": -0.9, "dims": {"cgo": 13.3, "vap": 16.7, "skw": 20.0, "lfa": 60.0, "wp": 10.0}}]),
        encoding="utf-8",
    )
    (evidence_dir / "dim_internal_fit.json").write_text(
        json.dumps(
            [
                {
                    "sym": "SNDK",
                    "metrics": {
                        "shortLiqAbove": 5_995_356.77,
                        "longLiqBelow": 12_496_287.82,
                        "gLong": 0.0003,
                        "gShort": 0.0299,
                        "convexity": 0.2263,
                        "cascadeVulnPct": 0.0006,
                    },
                    "pct": {"lfa": 60.0, "cgo": 13.3, "convexity": None, "cascade": None},
                }
            ]
        ),
        encoding="utf-8",
    )
    (evidence_dir / "analysis_SNDK.json").write_text(
        json.dumps(
            {
                "symbol": "SNDK",
                "action": "OPEN_SHORT",
                "reasoning": "Overhead supply dominates the near-term structure map.",
                "structure": {
                    "bias": "bearish",
                    "rank": 25,
                    "universe_size": 30,
                    "positioning": {"top10": "0.48", "top50": "0.71"},
                    "whale": {"net": "mixed", "rows": [{"address": "0xabc"}]},
                },
            }
        ),
        encoding="utf-8",
    )

    bundle = extract_position_x_material_bundle(tmp_path)

    assert bundle.knowledge_cards
    assert len(bundle.snapshots) == 1
    assert bundle.snapshots[0].symbol == "SNDK/USDT"
    assert bundle.snapshots[0].structure_bias == "bearish"
    assert any(card.key == "analysis_pattern:sndk" for card in bundle.knowledge_cards)
