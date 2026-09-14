"""Admission evidence stays truthful across Candidate persistence and planning."""
from aletheia.trading.serenity_publication import publish_serenity_candidates
from datetime import datetime

import pytest

from aletheia.storage import repositories as repo
from aletheia.storage.models import UniverseMember, UniverseSnapshot
from aletheia.universe.candidate_opportunity import (
    CandidateOpportunityContextV1,
    target_candidate_opportunity_context,
)


AT = "2026-09-05T00:00:00+00:00"


@pytest.mark.parametrize("nested", [False, True])
def test_candidate_snapshot_publication_rolls_back_partial_members(nested):
    import sqlite3
    from contextlib import nullcontext

    snapshot = UniverseSnapshot(id="atomic-candidates", created_at=AT)
    member = _member(_context()).model_copy(update={"snapshot_id": snapshot.id})
    db = repo.get_db()
    with repo.transaction(immediate=True) if nested else nullcontext():
        with pytest.raises(sqlite3.IntegrityError):
            repo.publish_candidate_snapshot(snapshot, [member, member])
        assert db.execute("SELECT COUNT(*) FROM universe_snapshots WHERE id=?", (snapshot.id,)).fetchone()[0] == 0
        assert repo.get_candidate(member.id) is None
        # A failed nested publication must not poison the caller transaction.
        assert repo.publish_candidate_snapshot(snapshot, [member]) == 1
    assert repo.get_candidate(member.id)["snapshot_id"] == snapshot.id


def test_candidate_snapshot_publication_rejects_cross_snapshot_member():
    snapshot = UniverseSnapshot(id="other-snapshot", created_at=AT)
    member = _member(_context())
    with pytest.raises(ValueError, match="candidate_snapshot_mismatch"):
        repo.publish_candidate_snapshot(snapshot, [member])
    assert repo.get_candidate(member.id) is None
    assert repo.get_db().execute("SELECT COUNT(*) FROM universe_snapshots WHERE id=?", (snapshot.id,)).fetchone()[0] == 0


def test_preview_fixture_seed_keeps_snapshot_members_without_planning_authority():
    from aletheia.cli.dev_seed import SeedSymbol, _seed_universe

    snapshot_id, inserted = _seed_universe([SeedSymbol("BTC/USDT", "spot", "momentum", 100, "Bitcoin")])
    assert inserted == 1
    rows = repo.get_db().execute("SELECT snapshot_id FROM universe_members").fetchall()
    assert [row["snapshot_id"] for row in rows] == [snapshot_id]
    assert repo.get_db().execute("SELECT COUNT(*) FROM candidate_planning_generations").fetchone()[0] == 0


def _context():
    return target_candidate_opportunity_context(
        candidate_id="startup-candidate", policy_epoch="startup-epoch",
        source_record_hash="a" * 64, detected_at=AT,
        decision={
            "version": "candidate-selection-utility/v2",
            "primary_mechanism": "continuation", "primary_direction": "long",
            "economic_utility_pct": None, "admission_basis": "startup",
            "startup_reason": "no_history",
            "opportunity_evidence": {
                "source_cutoff_at": AT, "feature_snapshot_id": "snapshot-facts",
                "mechanisms": [{
                    "mechanism_id": "continuation", "mechanism_version": "continuation/v1",
                    "source_component_id": "momentum", "pessimistic_strength": 72,
                    "capability_gaps": [], "evidence_refs": [],
                }],
            },
        },
    )


def _member(context):
    return UniverseMember(
        id=context.candidate_id, snapshot_id="startup-snapshot", asset_id="btc",
        venue="bitget", symbol="BTC/USDT", market_type="perp", rank=1,
        total_score=72, scanner_family="momentum", scanner_direction="long",
        expires_at="2099-01-01T00:00:00+00:00", entry_price_usd=100,
        candidate_selection_policy="candidate_selection_utility_v1",
        candidate_selection_policy_epoch="startup-epoch",
        candidate_selection_utility_pct=context.opportunity["economic_utility_pct"],
        opportunity_mechanism="continuation", opportunity_mechanism_version="continuation/v1",
        opportunity_source_component="momentum", opportunity_source_cutoff_at=AT,
        opportunity_evidence_hash="b" * 64,
        candidate_opportunity_context_version=context.version,
        candidate_opportunity_context_json=context.model_dump_json(),
        candidate_opportunity_context_hash=context.context_hash,
    )


def test_startup_persists_null_utility_and_can_own_planning_generation():
    context = _context()
    member = _member(context)
    repo.publish_candidate_snapshot(UniverseSnapshot(id=member.snapshot_id, created_at=AT), [member])
    persisted = repo.get_candidate(member.id)
    assert persisted["candidate_selection_utility_pct"] is None
    restored = CandidateOpportunityContextV1.model_validate_json(
        persisted["candidate_opportunity_context_json"]
    )
    assert restored.context_hash == context.context_hash
    assert restored.opportunity["admission_basis"] == "startup"
    assert restored.opportunity["startup_reason"] == "no_history"
    generation, _ = repo.ensure_candidate_planning_generation(
        member.id, now=datetime.fromisoformat(AT)
    )
    assert generation["last_candidate_id"] == member.id
    assert generation["opportunity_mechanism"] == "continuation"
    assert generation["direction"] == "long"


@pytest.mark.parametrize("change", [
    {"admission_basis": None}, {"admission_basis": "measured"},
    {"startup_reason": "tail_harm"}, {"startup_reason": "overdue_settlement"},
    {"economic_utility_pct": 0}, {"capability_gaps": ["missing_facts"]},
    {"capability_status": "observe_only"}, {"pessimistic_strength": float("nan")},
])
def test_null_utility_requires_valid_startup_evidence(change):
    payload = _context().model_dump()
    payload["context_hash"] = ""
    payload["opportunity"].update(change)
    with pytest.raises(ValueError):
        CandidateOpportunityContextV1.model_validate(payload)


def test_startup_preserves_hash_epoch_and_member_identity_checks():
    context = _context()
    payload = context.model_dump()
    payload["opportunity"]["startup_reason"] = "child_sample_insufficient"
    with pytest.raises(ValueError, match="hash_mismatch"):
        CandidateOpportunityContextV1.model_validate(payload)
    payload["context_hash"] = ""
    payload["candidate_selection"]["policy_epoch"] = None
    with pytest.raises(ValueError, match="epoch_missing"):
        CandidateOpportunityContextV1.model_validate(payload)
    member = _member(context).model_dump()
    for change in ({"candidate_selection_utility_pct": 1}, {"scanner_direction": "short"},
                   {"opportunity_source_component": "mean_reversion"}):
        with pytest.raises(ValueError, match="identity mismatch"):
            UniverseMember.model_validate(member | change)


def test_historical_measured_context_hash_is_not_rewritten():
    payload = _context().model_dump()
    payload["context_hash"] = ""
    payload["candidate_selection"].pop("decision_version")
    for field in ("admission_basis", "startup_reason", "pessimistic_strength"):
        payload["opportunity"].pop(field)
    payload["opportunity"]["economic_utility_pct"] = 1.5
    historical = CandidateOpportunityContextV1.model_validate(payload)
    serialized = historical.model_dump_json()
    restored = CandidateOpportunityContextV1.model_validate_json(serialized)
    assert restored.model_dump_json() == serialized
    assert "admission_basis" not in restored.opportunity
    assert _member(restored).candidate_selection_utility_pct == 1.5


def _serenity_publication_source():
    import math
    from datetime import timezone
    from aletheia.config.loader import load_config, save_config
    from aletheia.data.providers import Candle
    from aletheia.storage.models import SerenityTheme, SerenityOpportunity, SerenityEvidence, UniverseAsset, UniverseFeatureSnapshot

    now = datetime.now(timezone.utc)
    cfg = load_config()
    # Publication fixtures need an explicit monitorable route; fresh config is blank.
    cfg.exchange.name = "bitget"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    venue = "bitget"
    cfg.market_data.ccxt_exchange = venue
    save_config(cfg)
    theme = SerenityTheme(name="Research publication", status="active")
    repo.insert_serenity_theme(theme)
    opportunity = SerenityOpportunity(theme_id=theme.id, symbol="BTC/USDT", direction="long", market_type="spot")
    repo.insert_serenity_opportunity(opportunity)
    repo.insert_serenity_evidence(SerenityEvidence(theme_id=theme.id, title="Filed research", source="sec",
        source_ref="https://www.sec.gov/Archives/research", tier="disclosed", assessed_tier="disclosed", supports=True))
    asset = UniverseAsset(venue=venue, symbol=opportunity.symbol, market_type="spot")
    repo.upsert_universe_asset(asset)
    repo.insert_universe_feature_snapshot(UniverseFeatureSnapshot(asset_id=asset.id, ts=now.isoformat(),
        quote_volume_24h=10_000_000, spread_bps=5, depth_10bps_usd=30_000, data_freshness_sec=0,
        return_1h=1, return_4h=3, return_24h=8, relative_strength_24h=5, rsi_14=60, atr_pct=2))
    for timeframe, interval, count in (("15m", 900_000, 96), ("1h", 3_600_000, 1)):
        end = int(now.timestamp()*1000) // interval * interval
        candles = [Candle(timestamp=end-(count-index)*interval, open=100+math.sin(index/3),
            high=102+math.sin(index/3), low=98+math.sin(index/3), close=100+math.sin(index/3), volume=1000)
            for index in range(count)]
        repo.upsert_ohlcv_candles(opportunity.symbol, timeframe, candles, exchange=venue, market_type="spot")
    repo.execute_write("UPDATE market_ohlcv SET first_persisted_at=?, last_mutated_at=?", (now.isoformat(), now.isoformat()))
    return theme, opportunity


def test_serenity_publication_is_idempotent_scoped_and_never_writes_pi_decisions(bitget_fast_stream):
    import json
    from datetime import timezone
    theme, opportunity = _serenity_publication_source()
    ids = publish_serenity_candidates(theme.id, [opportunity.id, opportunity.id])
    assert publish_serenity_candidates(theme.id, [opportunity.id]) == ids
    assert publish_serenity_candidates(theme.id, [opportunity.id], new_candidates_only=True) == []
    candidate = repo.get_candidate(ids[0])
    source = json.loads(candidate["candidate_opportunity_context_json"])["source"]
    assert (source["kind"], source["id"], source["trade_school"]) == ("serenity", opportunity.id, "serenity")
    generation = repo.get_candidate_planning_for_candidate(ids[0])
    repo.reconcile_candidate_attention(set(), producer_scope="universe", eligible_identity_keys=set(),
        refresh_source={"scanner_run_id": "universe-refresh"}, soft_lease_sec=900, now=datetime.now(timezone.utc))
    assert repo.get_candidate_planning_generation(generation["id"])["attention_status"] == "active"
    repo.expire_universe_snapshots()
    assert repo.get_db().execute("SELECT status FROM universe_snapshots WHERE id=?", (candidate["snapshot_id"],)).fetchone()[0] == "active"
    assert repo.get_db().execute("SELECT COUNT(*) FROM pi_decisions").fetchone()[0] == 0
    assert repo.get_db().execute("SELECT COUNT(*) FROM candidate_planning_generations").fetchone()[0] == 1
    assert repo.get_serenity_theme(theme.id)["status"] == "promoted"


def test_serenity_missing_stream_never_publishes_candidate_or_generation(monkeypatch, bitget_fast_stream):
    theme, opportunity = _serenity_publication_source()
    monkeypatch.setattr("aletheia.universe.fast_stream._FAST_STREAM_ADAPTERS", {})
    db = repo.get_db()
    for _ in range(2):
        with pytest.raises(ValueError, match="serenity_instrument_ineligible"):
            publish_serenity_candidates(theme.id, [opportunity.id])
        for table in ("universe_snapshots", "universe_members", "candidate_planning_generations", "pi_decisions"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert repo.get_serenity_theme(theme.id)["status"] == "active"


def test_serenity_publication_rolls_back_all_rows_on_failure(bitget_fast_stream):
    theme, opportunity = _serenity_publication_source()
    db = repo.get_db()
    db.execute("CREATE TEMP TRIGGER reject_research_generation BEFORE INSERT ON candidate_planning_generations BEGIN SELECT RAISE(ABORT, 'publication failure'); END")
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError, match="publication failure"):
        publish_serenity_candidates(theme.id, [opportunity.id])
    assert db.execute("SELECT COUNT(*) FROM universe_snapshots").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM universe_members").fetchone()[0] == 0
    assert repo.get_serenity_theme(theme.id)["status"] == "active"


def test_serenity_publication_rechecks_source_after_pa_preparation(monkeypatch, bitget_fast_stream):
    theme, opportunity = _serenity_publication_source()
    prepare = repo.prepare_candidate_requalification
    def change_source(candidate):
        proof = prepare(candidate)
        repo.update_serenity_opportunity(opportunity.id, thesis="changed research")
        return proof
    monkeypatch.setattr(repo, "prepare_candidate_requalification", change_source)
    with pytest.raises(ValueError, match="serenity_source_changed"):
        publish_serenity_candidates(theme.id, [opportunity.id])
    assert repo.get_db().execute("SELECT COUNT(*) FROM universe_members").fetchone()[0] == 0
    assert repo.get_serenity_theme(theme.id)["status"] == "active"


def test_cross_source_cannot_replace_active_instrument_owner(bitget_fast_stream):
    from datetime import timezone
    theme, opportunity = _serenity_publication_source()
    ids = publish_serenity_candidates(theme.id, [opportunity.id])
    owner = repo.get_candidate_planning_for_candidate(ids[0])
    source = repo.get_candidate(ids[0])
    context = _context().model_dump()
    context["candidate_id"] = "universe-collision"
    context["context_hash"] = ""
    context["source"].update(kind="universe", id="universe-collision", detected_at=datetime.now(timezone.utc).isoformat(), source_cutoff_at=datetime.now(timezone.utc).isoformat())
    context["source"]["detected_at"] = context["source"]["source_cutoff_at"]
    frozen = CandidateOpportunityContextV1.model_validate(context)
    candidate = UniverseMember.model_validate(_member(_context()).model_dump() | {
        "id": frozen.candidate_id, "venue": source["venue"], "market_type": source["market_type"], "asset_id": source["asset_id"],
        "opportunity_source_cutoff_at": frozen.source["source_cutoff_at"],
        "candidate_opportunity_context_json": frozen.model_dump_json(), "candidate_opportunity_context_hash": frozen.context_hash})
    repo._insert("universe_snapshots", UniverseSnapshot(id=candidate.snapshot_id).model_dump())
    repo._insert("universe_members", candidate.model_dump())
    with pytest.raises(ValueError, match="instrument_owned"):
        repo.ensure_candidate_planning_generation(candidate.id)
    assert repo.get_candidate_planning_generation(owner["id"])["is_current"] == 1
    assert repo.get_db().execute("SELECT COUNT(*) FROM candidate_planning_generations WHERE is_current=1").fetchone()[0] == 1


def test_serenity_pa_absent_or_opposed_does_not_create_entry_authority(bitget_fast_stream):
    theme, opportunity = _serenity_publication_source()
    repo.update_serenity_opportunity(opportunity.id, direction="short")
    with pytest.raises(ValueError, match="serenity_pa_direction_conflict"):
        publish_serenity_candidates(theme.id, [opportunity.id])
    repo.update_serenity_opportunity(opportunity.id, direction="long")
    repo.execute_write("DELETE FROM market_ohlcv WHERE timeframe='15m'")
    with pytest.raises(ValueError, match="serenity_pa_requalification_unavailable"):
        publish_serenity_candidates(theme.id, [opportunity.id])
    assert repo.get_db().execute("SELECT COUNT(*) FROM candidate_planning_generations").fetchone()[0] == 0
    assert repo.get_db().execute("SELECT COUNT(*) FROM pi_decisions").fetchone()[0] == 0
    assert repo.get_serenity_theme(theme.id)["status"] == "active"


def test_research_batch_collision_rolls_back_and_released_owner_can_cut_over(bitget_fast_stream):
    import json
    from datetime import timezone
    from aletheia.storage.models import SerenityOpportunity
    theme, first = _serenity_publication_source()
    second = SerenityOpportunity(theme_id=theme.id, symbol=first.symbol, direction="long", market_type="spot")
    repo.insert_serenity_opportunity(second)
    with pytest.raises(ValueError, match="instrument_owned"):
        publish_serenity_candidates(theme.id, [first.id, second.id])
    assert repo.get_db().execute("SELECT COUNT(*) FROM universe_members").fetchone()[0] == 0
    assert repo.get_serenity_theme(theme.id)["status"] == "active"
    ids = publish_serenity_candidates(theme.id, [first.id])
    old = repo.get_candidate_planning_for_candidate(ids[0])
    with pytest.raises(ValueError, match="instrument_owned"):
        publish_serenity_candidates(theme.id, [second.id])
    repo.expire_candidate_attention(now=datetime(2099, 1, 1, tzinfo=timezone.utc))
    new_ids = publish_serenity_candidates(theme.id, [second.id])
    successor = repo.get_candidate_planning_for_candidate(new_ids[0])
    assert successor["id"] != old["id"]
    assert repo.get_candidate_planning_generation(old["id"])["is_current"] == 0
    assert json.loads(successor["requalification_source_json"])["candidate_opportunity_context"]["source"]["id"] == second.id


def test_generation_source_is_frozen_and_refresh_assets_allow_all_venues(bitget_fast_stream):
    import json
    from datetime import timezone
    theme, opportunity = _serenity_publication_source()
    candidate_id = publish_serenity_candidates(theme.id, [opportunity.id])[0]
    candidate = repo.get_candidate(candidate_id)
    generation = repo.get_candidate_planning_for_candidate(candidate_id)
    frozen = json.loads(generation["requalification_source_json"])["candidate_opportunity_context"]
    repo.reconcile_candidate_attention({generation["id"]}, producer_scope="serenity", eligible_identity_keys=set(),
        refresh_source={"publication": "serenity"}, soft_lease_sec=900, now=datetime.now(timezone.utc))
    refreshed = repo.get_candidate_planning_generation(generation["id"])
    assert json.loads(refreshed["requalification_source_json"])["candidate_opportunity_context"] == frozen
    assert [row["id"] for row in repo.list_candidate_planning_refresh_assets()] == [candidate["asset_id"]]
    assert repo.list_candidate_planning_refresh_assets(venue="unconfigured-venue") == []
    assert repo.list_universe_snapshots() == []
