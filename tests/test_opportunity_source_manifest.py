import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest


def _prepare_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sqlite
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    sqlite.reset_db()
    run_migrations()


def test_universe_opportunity_metadata_has_bounded_exact_t0_trigger():
    from aletheia.universe.signal_emitter import emit_deterministic_opportunities

    inserted = []
    member = {
        "id": "member-1",
        "snapshot_id": "snapshot-1",
        "symbol": "BTC/USDT",
        "market_type": "perp",
        "scanner_family": "momentum",
        "total_score": 70,
        "expires_at": "2026-07-19T01:00:00Z",
    }
    feature = {
        "symbol": "BTC/USDT",
        "market_type": "perp",
        "feature_snapshot_id": "feature-1",
        "scanner_direction": "long",
        "return_1h": 2.0,
        "return_4h": 4.0,
        "rsi_14": 62.0,
        "reasons": [f"reason-{index}-" + ("x" * 300) for index in range(20)],
    }

    with patch(
        "aletheia.universe.signal_emitter.find_opportunity_by_symbol_snapshot",
        return_value=None,
    ), patch(
        "aletheia.universe.signal_emitter.insert_opportunity_once_by_snapshot",
        side_effect=lambda opp: (inserted.append(opp) or opp.model_dump(), True),
    ), patch(
        "aletheia.universe.signal_emitter._record_emission_decision"
    ), patch(
        "aletheia.universe.signal_emitter.has_fresh_market_snapshot",
        return_value=True,
    ), patch(
        "aletheia.universe.signal_emitter.lookup_factor_signal",
        return_value={"status": "watch"},
    ):
        assert emit_deterministic_opportunities([member], "snapshot-1", [feature]) == 1

    manifest = json.loads(inserted[0].metadata)["evidence_manifest"]
    assert manifest["version"] == "opportunity-information-integrity-v1"
    assert manifest["t0"]["trigger_type"] == "universe_scanner"
    assert manifest["t0"]["universe_snapshot_id"] == "snapshot-1"
    assert manifest["t0"]["universe_member_id"] == "member-1"
    assert manifest["t0"]["feature_snapshot_id"] == "feature-1"
    assert len(manifest["t0"]["reasons"]) == 5
    assert all(len(reason) <= 160 for reason in manifest["t0"]["reasons"])


@pytest.mark.asyncio
async def test_observer_t1_names_only_sources_rendered_for_the_exact_instrument(
    tmp_path, monkeypatch,
):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.agents import market_observer as observer
    from aletheia.storage.sqlite import get_db

    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    db.execute(
        "INSERT INTO macro_snapshots (id, risk_regime, observed_at) "
        "VALUES ('macro-rendered', 'neutral', ?)",
        (now,),
    )
    db.commit()
    cache = observer._DbCache(
        perp_structures={
            "BTC/USDT": {
                "id": "position-x-rendered",
                "symbol": "BTC/USDT",
                "source": "owner_aggregate",
                "as_of_time": now,
                "observed_at": now,
                "data_quality": "complete",
            }
        },
        funding_hl={
            "BTC/USDT": {
                "id": "funding-rendered",
                "exchange": "hyperliquid",
                "funding_rate_annualized": 0.2,
                "observed_at": now,
            }
        },
        funding_hl_pred={},
        open_interest_hl={
            "BTC/USDT": {
                "id": "oi-rendered",
                "exchange": "hyperliquid",
                "open_interest_usd": 1_000_000,
                "observed_at": now,
            }
        },
        news={
            "BTC/USDT": [{
                "id": "news-rendered",
                "title": "Bitcoin demand rises",
                "source": "fixture",
                "published_at": now,
            }]
        },
        onchain_rows=[{
            "id": "onchain-rendered",
            "source": "defillama",
            "metric_type": "chain_tvl",
            "total_usd": 1_000_000,
            "observed_at": now,
        }],
    )

    async def technical_context(*_args, **_kwargs):
        return "technical", {
            "symbols": {"BTC/USDT": {"1h": {"rsi_14": 70.0, "atr_pct": 1.0}}}
        }

    snapshot = SimpleNamespace(
        tradability={}, leverage={}, macro={"risk_regime": "neutral", "observed_at": now}
    )
    with patch(
        "aletheia.trading.market_context.build_market_context",
        side_effect=technical_context,
    ), patch(
        "aletheia.data.context_builder.build_market_context_from_db",
        return_value=snapshot,
    ), patch.object(
        observer,
        "_prediction_market_signal_context",
        return_value=(["- prediction"], [{"id": "prediction-rendered", "updated_at": now}]),
    ):
        prefetched = await observer._prefetch_symbol(
            SimpleNamespace(symbol="BTC/USDT", market_type="perp"),
            venue="okx",
            provider=None,
            prediction_context_enabled=True,
            prediction_context_cutoff="2026-07-18T00:00:00Z",
            db_cache=cache,
        )

    source_ids = {
        kind: [source["id"] for source in sources]
        for kind, sources in prefetched.rendered_sources.items()
    }
    assert source_ids == {
        "news": ["news-rendered"],
        "macro": ["macro-rendered"],
        "onchain": ["onchain-rendered"],
        "prediction": ["prediction-rendered"],
        "hyperliquid": ["funding-rendered", "oi-rendered"],
        "position_x": ["position-x-rendered"],
    }

    manifest = observer._opportunity_evidence_manifest(
        metadata={
            "evidence_manifest": {
                "version": "opportunity-information-integrity-v1",
                "t0": {"status": "available", "feature_snapshot_id": "feature-t0"},
            }
        },
        rendered_sources=prefetched.rendered_sources,
        run_id="observer-run",
        universe_snapshot_id="snapshot-1",
        universe_member_id="member-1",
        scanner_family="momentum",
        pre_filter=prefetched.pre_filter,
        pre_filter_evidence_id="filter-t0",
    )
    assert manifest["t0"]["feature_snapshot_id"] == "feature-t0"
    assert manifest["t1"]["sources"] == prefetched.rendered_sources


def test_pi_manifest_copies_opportunity_t0_t1_and_ignores_newer_decoys(
    tmp_path, monkeypatch,
):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.agent_shell.trading_context import build_decision_evidence_manifest
    from aletheia.storage.models import Opportunity
    from aletheia.storage.repositories import insert_opportunity
    from aletheia.storage.sqlite import get_db

    opportunity_evidence = {
        "version": "opportunity-information-integrity-v1",
        "t0": {
            "status": "available",
            "trigger_type": "universe_scanner",
            "feature_snapshot_id": "feature-t0",
        },
        "t1": {
            "status": "available",
            "sources": {
                "news": [{"id": "news-t1", "published_at": "2026-07-19T00:00:00Z"}],
                "macro": [{"id": "macro-t1", "observed_at": "2026-07-19T00:00:00Z"}],
                "onchain": [{"id": "chain-t1", "metric_type": "chain_tvl"}],
                "prediction": [{"id": "prediction-t1", "updated_at": "2026-07-19T00:00:00Z"}],
                "hyperliquid": [
                    {"id": "funding-t1", "kind": "funding"},
                    {"id": "oi-t1", "kind": "open_interest"},
                ],
                "position_x": [{"id": "position-x-t1", "source": "owner_aggregate"}],
            },
        },
    }
    insert_opportunity(Opportunity(
        id="opp-source-manifest",
        symbol="BTC/USDT",
        market="usdt_futures",
        direction_bias="long",
        scanner_family="momentum",
        metadata=json.dumps({"evidence_manifest": opportunity_evidence}),
    ))
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    db.execute(
        "INSERT INTO funding_rate_snapshots "
        "(id, symbol, exchange, funding_rate, funding_rate_annualized, observed_at) "
        "VALUES ('funding-decoy', 'BTC/USDT', 'hyperliquid', 0.1, 1, ?)",
        (now,),
    )
    db.execute(
        "INSERT INTO open_interest_snapshots "
        "(id, symbol, exchange, open_interest_usd, observed_at) "
        "VALUES ('oi-decoy', 'BTC/USDT', 'hyperliquid', 1, ?)",
        (now,),
    )
    db.execute(
        "INSERT INTO perp_structure_snapshots "
        "(id, symbol, source, as_of_time, observed_at, raw_json) "
        "VALUES ('position-x-decoy', 'BTC/USDT', 'owner_aggregate', ?, ?, '{}')",
        (now, now),
    )
    db.commit()

    context = {
        "layer1": {
            "feature_index": {
                "okx|perp|BTC/USDT": {
                    "feature_snapshot_id": "operator-feature-t1",
                    "feature_ts": "2026-07-19T00:05:00Z",
                    "venue": "okx",
                    "symbol": "BTC/USDT",
                    "market_type": "perp",
                }
            },
            "opportunities": [{
                "id": "opp-source-manifest",
                "evidence_manifest": opportunity_evidence,
            }],
        },
        "layer2": {
            "pending_signals": [{
                "opportunity_id": "opp-source-manifest",
                "symbol": "BTC/USDT",
                "direction": "long",
                "market_type": "perp",
                "scanner_family": "momentum",
            }],
        },
    }
    decision = SimpleNamespace(
        opportunity_id="opp-source-manifest",
        symbol="BTC/USDT",
        market_type="perp",
    )

    manifest = build_decision_evidence_manifest(context, decision)

    assert manifest["opportunity_t0"] == opportunity_evidence["t0"]
    assert manifest["opportunity_t1"] == opportunity_evidence["t1"]
    assert manifest["system_operator_t1"]["technical_snapshot_id"] == "operator-feature-t1"
    assert "source_identities" not in manifest["system_operator_t1"]
    assert manifest["system_operator_t1"]["opportunity_context"] == {
        "status": "via_observer_summary",
        "evidence_version": "opportunity-information-integrity-v1",
    }
    assert manifest["hyperliquid"] == {
        "status": "not_directly_rendered",
        "via": "opportunity_t1",
    }
    assert manifest["position_x"] == {"status": "unavailable"}
    assert "decoy" not in json.dumps(manifest)
    assert manifest["version"] == "vergex-utilization-v1"
    assert manifest["source_chain"]["opportunity_id"] == "opp-source-manifest"


def test_pi_manifest_marks_missing_historical_opportunity_sources_unavailable(
    tmp_path, monkeypatch,
):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.agent_shell.trading_context import build_decision_evidence_manifest
    from aletheia.storage.models import Opportunity
    from aletheia.storage.repositories import insert_opportunity

    insert_opportunity(Opportunity(
        id="opp-malformed-manifest",
        symbol="ETH/USDT",
        market="spot",
        direction_bias="short",
        metadata="{not-json",
    ))
    manifest = build_decision_evidence_manifest(
        {"layer1": {"feature_index": {}, "opportunities": []}, "layer2": {}},
        SimpleNamespace(
            opportunity_id="opp-malformed-manifest",
            symbol="ETH/USDT",
            market_type="spot",
        ),
    )

    assert manifest["opportunity_t0"] == {"status": "unavailable"}
    assert manifest["opportunity_t1"] == {"status": "unavailable"}
    assert manifest["system_operator_t1"]["status"] == "unavailable"
    assert manifest["position_x"]["status"] == "unavailable"


def test_serenity_pi_manifest_uses_exact_layer3_evidence_despite_id_collision(
    tmp_path, monkeypatch,
):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.agent_shell.trading_context import build_decision_evidence_manifest
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import (
        Opportunity,
        SerenityEvidence,
        SerenityOpportunity,
        SerenityTheme,
    )

    collision_id = "shared-bare-opportunity-id"
    market_manifest = {
        "version": "opportunity-information-integrity-v1",
        "t0": {"status": "available", "trigger_type": "market-decoy"},
        "t1": {"status": "available", "sources": {"news": [{"id": "decoy-news"}]}},
    }
    repo.insert_opportunity(Opportunity(
        id=collision_id,
        symbol="BTC/USDT",
        market="spot",
        direction_bias="short",
        metadata=json.dumps({"evidence_manifest": market_manifest}),
    ))
    repo.insert_serenity_theme(SerenityTheme(id="serenity-theme", name="Exact theme"))
    repo.insert_serenity_opportunity(SerenityOpportunity(
        id=collision_id,
        theme_id="serenity-theme",
        symbol="ETH/USDT",
        direction="long",
        market_type="spot",
        thesis="Exact Serenity thesis",
        status="active",
    ))
    repo.insert_serenity_evidence(SerenityEvidence(
        id="serenity-evidence-exact",
        theme_id="serenity-theme",
        title="Exact filing",
        source="official",
        source_ref="https://example.com/exact",
        url="https://example.com/exact",
        tier="high_confidence",
        assessed_tier="high_confidence",
        assessed_by="deterministic_v1",
    ))
    repo.insert_serenity_evidence(SerenityEvidence(
        id="serenity-evidence-not-rendered",
        theme_id="serenity-theme",
        title="Hidden filing",
        source="official",
        source_ref="https://example.com/hidden",
        url="https://example.com/hidden",
        tier="high_confidence",
        assessed_tier="high_confidence",
        assessed_by="deterministic_v1",
    ))
    context = {
        "layer1": {"feature_index": {}, "opportunities": []},
        "layer2": {"pending_signals": [{
            "opportunity_id": collision_id,
            "signal_source": "serenity",
            "symbol": "ETH/USDT",
            "direction": "long",
            "market_type": "spot",
        }]},
        "layer3": {"serenity_opportunities": [{
            "id": collision_id,
            "symbol": "ETH/USDT",
            "direction": "long",
            "market_type": "spot",
            "thesis": "Exact Serenity thesis",
            "evidence_ids": [
                "serenity-evidence-exact",
                "serenity-evidence-not-rendered",
            ],
            "rendered_evidence_ids": ["serenity-evidence-exact"],
            "evidence": [{
                "id": "serenity-evidence-exact",
                "source": "official",
                "source_ref": "https://example.com/exact",
                "title": "Exact filing",
            }],
        }]},
    }

    manifest = build_decision_evidence_manifest(
        context,
        SimpleNamespace(
            opportunity_id=collision_id,
            signal_source="serenity",
            symbol="ETH/USDT",
            market_type="spot",
        ),
    )

    assert manifest["source_chain"]["signal_source"] == "serenity"
    assert manifest["source_chain"]["serenity_theme_id"] == "serenity-theme"
    assert manifest["opportunity_t0"]["trigger_type"] == "serenity_research"
    assert manifest["opportunity_t1"]["sources"]["serenity_evidence"] == [{
        "id": "serenity-evidence-exact",
        "source": "official",
        "source_ref": "https://example.com/exact",
        "title": "Exact filing",
    }]
    assert "market-decoy" not in json.dumps(manifest)
    assert "decoy-news" not in json.dumps(manifest)


def test_operator_position_x_is_perp_only_and_manifest_names_every_rendered_identity(
    tmp_path, monkeypatch,
):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.agent_shell.trading_context import (
        _build_layer1_snapshot,
        build_decision_evidence_manifest,
    )
    from aletheia.agent_shell.trading_decision import _build_assessment_prompt
    from aletheia.storage import repositories as repo

    now = datetime.now(timezone.utc).isoformat()

    def source(source_id):
        return {
            "id": source_id,
            "symbol": "BTC/USDT",
            "source": "owner_aggregate",
            "as_of_time": now,
            "observed_at": now,
            "data_quality": "complete",
        }

    positions = [
        {
            "id": "spot-position",
            "symbol": "BTC/USDT",
            "market_type": "spot",
            "direction": "long",
        },
        {
            "id": "perp-position",
            "symbol": "ETH/USDT",
            "market_type": "usdt_futures",
            "direction": "short",
        },
    ]
    monkeypatch.setattr(repo, "list_all_open_positions", lambda: positions)
    monkeypatch.setattr(
        repo,
        "latest_perp_structure",
        lambda symbol: source(f"px-{symbol.split('/')[0].lower()}"),
    )

    layer1 = _build_layer1_snapshot()

    by_id = {row["id"]: row for row in layer1["positions"]}
    assert not by_id["spot-position"].get("structure_context")
    assert by_id["perp-position"]["structure_context"]["status"] == "ok"

    layer1["opportunities"] = [
        {
            "id": "selected-perp-opportunity",
            "symbol": "SOL/USDT",
            "market": "perp",
            "direction_bias": "long",
            "structure_context": {
                "status": "ok",
                "rendered": "Position X (advisory only): selected",
                "source_identity": source("px-sol"),
            },
        }
    ]
    context = {
        "layer1": layer1,
        "layer2": {"pending_signals": [{
            "opportunity_id": "selected-perp-opportunity",
            "symbol": "SOL/USDT",
            "direction": "long",
            "market_type": "perp",
        }]},
        "layer3": {},
    }
    prompt = _build_assessment_prompt(context)
    manifest = build_decision_evidence_manifest(
        context,
        SimpleNamespace(
            opportunity_id="selected-perp-opportunity",
            symbol="SOL/USDT",
            market_type="perp",
        ),
    )

    assert "px-btc" not in prompt
    assert "px-eth" not in prompt  # source ids are audit facts, not prompt prose
    assert "Position X (advisory only): selected" in prompt
    rendered = manifest["position_x"]["rendered_source_identities"]
    assert [(row["entity_type"], row["entity_id"], row["id"]) for row in rendered] == [
        ("position", "perp-position", "px-eth"),
        ("opportunity", "selected-perp-opportunity", "px-sol"),
    ]


@pytest.mark.asyncio
async def test_observer_enrichment_failure_does_not_claim_unrendered_source_ids(
    tmp_path, monkeypatch,
):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.agents import market_observer as observer
    from aletheia.storage.sqlite import get_db

    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    db.execute(
        "INSERT INTO macro_snapshots (id, risk_regime, observed_at) "
        "VALUES ('macro-never-rendered', 'neutral', ?)",
        (now,),
    )
    db.commit()
    cache = observer._DbCache(
        perp_structures={},
        funding_hl={
            "BTC/USDT": {
                "id": "funding-never-rendered",
                "exchange": "hyperliquid",
                "funding_rate_annualized": 0.2,
                "observed_at": now,
            }
        },
        funding_hl_pred={},
        news={},
        onchain_rows=[{
            "id": "onchain-never-rendered",
            "source": "defillama",
            "metric_type": "chain_tvl",
            "total_usd": 1_000_000,
            "observed_at": now,
        }],
    )

    async def technical_context(*_args, **_kwargs):
        return "technical", {
            "symbols": {"BTC/USDT": {"1h": {"rsi_14": 70.0, "atr_pct": 1.0}}}
        }

    snapshot = SimpleNamespace(
        tradability={}, leverage={}, macro={"risk_regime": "neutral", "observed_at": now}
    )
    with patch(
        "aletheia.trading.market_context.build_market_context",
        side_effect=technical_context,
    ), patch(
        "aletheia.data.context_builder.build_market_context_from_db",
        return_value=snapshot,
    ), patch(
        "aletheia.data.context_builder.render_enriched_context_md",
        side_effect=ValueError("render failed"),
    ):
        prefetched = await observer._prefetch_symbol(
            SimpleNamespace(symbol="BTC/USDT", market_type="perp"),
            venue="okx",
            provider=None,
            prediction_context_enabled=False,
            prediction_context_cutoff=None,
            db_cache=cache,
        )

    assert prefetched.context_str == "technical"
    assert prefetched.rendered_sources == observer._empty_rendered_sources()
