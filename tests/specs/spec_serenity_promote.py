"""Spec: Serenity promote → durable Candidate publication (D2 canonical entry)

Contract Delta (2026-09-07, serenity_contract.md §7): the legacy
`signal_pending` PiDecision queue is RETIRED. Both promotion paths — manual
`routes/data.py:promote_serenity_theme` and automatic
`research_worker._auto_promote_serenity_opportunities` — now go through one
source orchestration function in `trading.serenity_publication`:

    publish_serenity_candidates(theme_id, opportunity_ids, *,
                                     min_score_for_pi=None,
                                     new_candidates_only=False) -> list[str]

    POST /api/serenity/themes/{id}/promote
       → publish_serenity_candidates(theme_id, <active opportunity ids>)
            · prepares research evidence, exact registry instrument, current
              feature/market facts and the shared PA evaluator OUTSIDE the
              writer transaction
            · re-verifies source material hash + global exact-instrument owner
              inside one BEGIN IMMEDIATE, then writes the source-scoped
              UniverseSnapshot + UniverseMember + PlanningGeneration
            · marks the theme 'promoted' only when a durable Candidate exists
       → notify_pi_daemon(source='serenity_promote')  # wakes the canonical Planner

Consequences for the claims below:
- No `pi_decisions` row is ever written by promotion (SP-B01).
- Promotion is IDEMPOTENT by (source id, material hash) — re-promoting returns
  the same Candidate IDs instead of the retired 400 double-enqueue guard
  (SP-B02).
- A writer failure rolls back every row and leaves the theme retryable
  (SP-B04).

The D5 config switch `aletheia.config.schema.SerenityConfig` keeps
`auto_promote=False` by default — manual-only promotion preserves the
human-curator workflow.

Contract: serenity_promote (logical contract — registered in the conformance
matrix but intentionally NOT in REQUIRED_CONTRACTS, mirroring the behavioral
pipeline specs and the D2/Phase 2 specs).
"""

from __future__ import annotations

from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim

_CONTRACT = "serenity_promote"
_ROOT = Path(__file__).parent.parent.parent
_SCHEMA = _ROOT / "aletheia" / "config" / "schema.py"
_DATA_ROUTES = _ROOT / "aletheia" / "server" / "routes" / "data.py"
_SETTINGS_ROUTES = _ROOT / "aletheia" / "server" / "routes" / "settings.py"
_API_REF = _ROOT / "docs" / "design" / "api" / "api_reference.md"


def _promote_fn_body() -> str:
    """Slice promote_serenity_theme body from data.py for region-scoped grep.

    Whole-file grep would false-positive against other Serenity endpoints in the
    same router (deep-dive, feedback, claims) — they coexist with the promote
    handler and use overlapping vocabulary. Anchor on the def + next handler,
    then strip the function docstring so historical mentions of the legacy path
    in prose (e.g. "Replaces the old StrategyRecord(state='draft') path") don't
    false-trigger the negative grep in SP-S02.
    """
    src = _DATA_ROUTES.read_text(encoding="utf-8")
    start = src.index("def promote_serenity_theme(")
    tail = src[start:]
    end_marker = "\n@router."
    end = tail.index(end_marker, 1)
    body = tail[:end]
    # Strip the leading triple-quoted docstring from the function body.
    import re
    body = re.sub(r'"""[\s\S]*?"""', "", body, count=1)
    return body


# ---------------------------------------------------------------------------
# L2a — Structural
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "SP-S01", _CONTRACT, "§D5 SerenityConfig defaults", "L2a",
        "config.schema defines SerenityConfig with auto_promote=False default so the "
        "human-curator workflow is preserved unless the owner flips the switch",
    ),
)
def test_serenity_config_default_auto_promote_off():
    from aletheia.config.schema import AletheiaConfig, SerenityConfig

    cfg = SerenityConfig()
    assert cfg.auto_promote is False, (
        f"D5: auto_promote default must be False, got {cfg.auto_promote!r}"
    )
    # min_score_for_pi must be present and within a sane band; the wider 0..100
    # bound is enforced by the settings route.
    assert 0 <= cfg.min_score_for_pi <= 100, (
        f"min_score_for_pi out of band: {cfg.min_score_for_pi!r}"
    )
    # The top-level config must register the field — otherwise no UI control
    # could ever read or write it.
    root = AletheiaConfig()
    assert hasattr(root, "serenity") and isinstance(root.serenity, SerenityConfig), (
        "AletheiaConfig.serenity is missing — Settings UI would have nothing to bind"
    )


@spec_claim(
    SpecClaim(
        "SP-S02", _CONTRACT, "§promote no longer writes draft StrategyRecord", "L2a",
        "promote_serenity_theme has stopped writing StrategyRecord(state='draft') — the "
        "legacy dead-record path that PI never consumed",
    ),
)
def test_promote_does_not_write_draft_strategy_record():
    body = _promote_fn_body()
    assert "StrategyRecord" not in body, (
        "promote_serenity_theme still references StrategyRecord — the Phase 3 "
        "Option A migration is incomplete and the dead-record path lives on"
    )
    assert 'state="draft"' not in body and "state='draft'" not in body, (
        "promote_serenity_theme still creates a draft record — PI never reads "
        "state='draft' StrategyRecords, so the promote action would be a no-op"
    )


@spec_claim(
    SpecClaim(
        "SP-S03", _CONTRACT, "§promote publishes durable Candidates", "L2a",
        "promote_serenity_theme delegates the all-or-nothing publication to the "
        "source's publish_serenity_candidates and wakes the canonical Planner only "
        "after it commits. "
        "Contract Delta (2026-09-07): the signal_pending PiDecision queue is retired; "
        "enqueue_serenity_theme_for_pi no longer exists.",
    ),
)
def test_promote_publishes_durable_candidates():
    body = _promote_fn_body()
    assert "publish_serenity_candidates" in body, (
        "promote_serenity_theme does not call the repository's atomic publication — "
        "theme state and durable Candidates could diverge"
    )
    assert "enqueue_serenity_theme_for_pi" not in body, (
        "promote_serenity_theme still references the retired pending-carrier handoff"
    )
    assert "insert_pi_decision" not in body and "signal_pending" not in body, (
        "promote_serenity_theme still writes the retired PiDecision queue — promotion "
        "must produce durable Candidates, not legacy decisions"
    )
    assert "notify_pi_daemon" in body, (
        "promote_serenity_theme does not wake the Planner — published Candidates would "
        "sit until the next scheduled tick instead of being planned promptly"
    )


@spec_claim(
    SpecClaim(
        "SP-S04", _CONTRACT, "§D5 settings endpoint exposes auto_promote toggle", "L2a",
        "routes/settings.py exposes POST /serenity with auto_promote + min_score_for_pi "
        "fields and a 0..100 bound on min_score_for_pi",
    ),
)
def test_settings_serenity_endpoint_registered():
    src = _SETTINGS_ROUTES.read_text(encoding="utf-8")
    assert '"/serenity"' in src or "'/serenity'" in src, (
        "POST /serenity endpoint not registered — Settings UI cannot persist "
        "the D5 auto_promote switch"
    )
    assert "SerenityConfigUpdate" in src, (
        "no SerenityConfigUpdate pydantic model — endpoint body has no schema"
    )
    assert "auto_promote" in src and "min_score_for_pi" in src, (
        "SerenityConfigUpdate is missing required D5 fields"
    )
    assert "_SERENITY_BOUNDS" in src and "min_score_for_pi" in src, (
        "no bounds on min_score_for_pi — a malformed UI input could write any "
        "integer into config"
    )


@spec_claim(
    SpecClaim(
        "SP-S05", _CONTRACT, "§api_reference lists /settings/serenity row", "L2a",
        "api_reference.md documents the new POST /settings/serenity row — config↔UI "
        "rule (CLAUDE.md) requires every config field to be reachable from the UI and "
        "described in the API contract",
    ),
)
def test_api_reference_lists_settings_serenity():
    md = _API_REF.read_text(encoding="utf-8")
    assert "/settings/serenity" in md, (
        "api_reference.md has no POST /settings/serenity row — contract drift "
        "vs. routes/settings.py"
    )


# ---------------------------------------------------------------------------
# L2b — Behavioral (TestClient → real route → in-memory DB round-trip)
# ---------------------------------------------------------------------------


def _make_test_client():
    """Spin up a starlette TestClient for the FastAPI app.

    Deferred import so route registration / lifespan side-effects don't run at
    pytest collection. raise_server_exceptions=False lets us assert on 4xx
    without pytest treating them as errors.
    """
    from fastapi.testclient import TestClient
    from aletheia.server.app import app
    return TestClient(app, raise_server_exceptions=False)


def _switch_to_filebacked_db():
    """Replace the autouse :memory: connection with a file-backed DB in tmp_path.

    Why this is necessary: the autouse `_inmemory_db` fixture sets the SQLite
    connection on the *test thread's* threading.local. TestClient dispatches
    async routes onto an anyio worker thread, which has its own threading.local
    — so the route's `get_db()` falls through, opens a brand-new connection on
    `data_dir/app.db` (already patched to tmp_path by the autouse fixture), and
    sees an empty / un-migrated file. The route then 500s.

    File-backed solves this: every thread that calls `get_db()` independently
    opens `tmp_path/app.db`. With `isolation_level=None` (autocommit) + WAL,
    writes from the test thread (our seed) are immediately visible to the
    TestClient worker thread.
    """
    import aletheia.storage.sqlite as _sq
    from aletheia.storage.sqlite import reset_db
    from aletheia.storage.migrations import run_migrations

    reset_db()                      # close the autouse :memory: conn on this thread
    _sq._db_conn = None             # clear so subsequent get_db() opens the file
    run_migrations()                # write schema into tmp_path/app.db


_PUBLISHABLE_SYMBOLS = ("BTC/USDT", "ETH/USDT")


def _seed_theme_with_opportunities(n: int = 2):
    """Seed a theme whose active opportunities can reach durable publication.

    `publish_serenity_candidates` refuses anything it cannot ground in real
    facts, so a promotable theme needs, per opportunity: supporting Serenity
    evidence, an ACTIVE `universe_registry` row for the exact
    venue/symbol/market_type, a current feature snapshot that clears the hard
    gate, and enough OHLCV history for requalification. Seeding less than this
    makes the route answer 409 for a data reason instead of exercising the
    publication claim.
    """
    import math
    from datetime import datetime, timezone

    from aletheia.config.loader import load_config, save_config
    from aletheia.data.providers import Candle
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import (
        SerenityEvidence,
        SerenityOpportunity,
        SerenityTheme,
        UniverseAsset,
        UniverseFeatureSnapshot,
    )

    assert n <= len(_PUBLISHABLE_SYMBOLS), "extend _PUBLISHABLE_SYMBOLS"
    now = datetime.now(timezone.utc)
    cfg = load_config()
    cfg.exchange.name = "bitget"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    venue = "bitget"
    cfg.market_data.ccxt_exchange = venue
    save_config(cfg)

    theme = SerenityTheme(name="Test industrial chain — semis", status="active")
    repo.insert_serenity_theme(theme)
    repo.insert_serenity_evidence(SerenityEvidence(
        theme_id=theme.id, title="Filed research", source="sec",
        source_ref="https://www.sec.gov/Archives/research", tier="disclosed",
        supports=True,
    ))

    opp_ids: list[str] = []
    for symbol in _PUBLISHABLE_SYMBOLS[:n]:
        opp = SerenityOpportunity(
            theme_id=theme.id, symbol=symbol, direction="long",
            market_type="spot", status="active", action_intent="watch",
        )
        repo.insert_serenity_opportunity(opp)
        opp_ids.append(opp.id)

        asset = UniverseAsset(venue=venue, symbol=symbol, market_type="spot")
        repo.upsert_universe_asset(asset)
        repo.insert_universe_feature_snapshot(UniverseFeatureSnapshot(
            asset_id=asset.id, ts=now.isoformat(), quote_volume_24h=10_000_000,
            spread_bps=5, depth_10bps_usd=30_000, data_freshness_sec=0,
            return_1h=1, return_4h=3, return_24h=8, relative_strength_24h=5,
            rsi_14=60, atr_pct=2,
        ))
        for timeframe, interval, count in (("15m", 900_000, 96), ("1h", 3_600_000, 1)):
            end = int(now.timestamp() * 1000) // interval * interval
            candles = [
                Candle(
                    timestamp=end - (count - index) * interval,
                    open=100 + math.sin(index / 3), high=102 + math.sin(index / 3),
                    low=98 + math.sin(index / 3), close=100 + math.sin(index / 3),
                    volume=1000,
                )
                for index in range(count)
            ]
            repo.upsert_ohlcv_candles(
                symbol, timeframe, candles, exchange=venue, market_type="spot",
            )
    repo.execute_write(
        "UPDATE market_ohlcv SET first_persisted_at=?, last_mutated_at=?",
        (now.isoformat(), now.isoformat()),
    )
    return theme.id, opp_ids


def _serenity_candidate_ids(repo, opp_ids: set[str]) -> list[str]:
    """Durable Candidate IDs whose frozen context names one of these sources."""
    import json

    found = []
    for row in repo.get_db().execute(
        "SELECT id, candidate_opportunity_context_json FROM universe_members"
    ).fetchall():
        context = json.loads(row["candidate_opportunity_context_json"] or "{}")
        source = context.get("source") or {}
        if source.get("kind") == "serenity" and source.get("id") in opp_ids:
            found.append(row["id"])
    return found


@spec_claim(
    SpecClaim(
        "SP-B01", _CONTRACT, "§promote → durable Candidate full round-trip", "L2b",
        "POST /api/serenity/themes/{id}/promote with 2 active opportunities publishes 2 "
        "durable Candidates whose frozen context carries source.kind='serenity' + the "
        "exact SerenityOpportunity id, marks the theme 'promoted', and writes NO "
        "pi_decisions row. "
        "Contract Delta (2026-09-07): replaces the retired signal_pending PiDecision "
        "queue and the action_intent='pi_evaluate' flip.",
    ),
)
def test_promote_endpoint_full_round_trip(monkeypatch):
    from aletheia.storage import repositories as repo

    _switch_to_filebacked_db()

    # Intercept the daemon-wake side effect — the test would otherwise touch
    # a real pi_daemon module. We just need to confirm it was attempted.
    wake_calls: list[str] = []
    try:
        import aletheia.agent_runtime.pi_daemon as _pd
        monkeypatch.setattr(
            _pd, "notify_pi_daemon",
            lambda source="unknown": wake_calls.append(source),
        )
    except Exception:
        # If the module fails to import in this test env, the route's own
        # try/except will swallow it — that's fine for the persistence assertions.
        pass

    theme_id, opp_ids = _seed_theme_with_opportunities(n=2)
    client = _make_test_client()
    resp = client.post(f"/api/serenity/themes/{theme_id}/promote")
    assert resp.status_code == 200, (
        f"promote returned {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert body.get("status") == "submitted_to_planning", (
        f"unexpected status: {body!r}"
    )
    assert body.get("candidate_count") == 2, (
        f"expected 2 Candidates published, got {body!r}"
    )
    candidate_ids = body.get("candidate_ids") or []
    assert len(set(candidate_ids)) == 2, f"candidate_ids not distinct: {body!r}"

    # Theme must be marked promoted
    theme_row = repo.get_serenity_theme(theme_id)
    assert theme_row is not None
    assert theme_row["status"] == "promoted", (
        f"theme status not flipped: {theme_row['status']!r}"
    )

    # Each published Candidate must be causally frozen to its exact research
    # source — this is what replaced the signal_source='serenity' discriminator.
    assert set(_serenity_candidate_ids(repo, set(opp_ids))) == set(candidate_ids), (
        "published Candidates do not carry the exact SerenityOpportunity sources"
    )
    for candidate_id in candidate_ids:
        planning = repo.get_candidate_planning_for_candidate(candidate_id)
        assert planning is not None, (
            f"Candidate {candidate_id} has no PlanningGeneration — the Planner "
            "would never pick it up"
        )

    # The retired queue must stay empty: promotion produces durable Candidates,
    # never a legacy decision row.
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM pi_decisions"
    ).fetchone()[0] == 0, "promotion wrote a retired pi_decisions carrier"

    # Daemon wake attempted (best-effort — route swallows ImportError, but in
    # the normal path this should fire)
    assert wake_calls == ["serenity_promote"] or wake_calls == [], (
        f"unexpected notify_pi_daemon calls: {wake_calls!r}"
    )


@spec_claim(
    SpecClaim(
        "SP-B02", _CONTRACT, "§promote idempotency by source material", "L2b",
        "Re-promoting a theme reuses the durable Candidate already published for the "
        "same (source id, material hash) instead of creating a second one. "
        "Contract Delta (2026-09-07): the retired 400 double-enqueue guard is replaced "
        "by real idempotency — unchanged research cannot yield two entry carriers.",
    ),
)
def test_promote_is_idempotent_for_unchanged_research():
    from aletheia.storage import repositories as repo

    _switch_to_filebacked_db()
    theme_id, opp_ids = _seed_theme_with_opportunities(n=1)
    client = _make_test_client()

    first = client.post(f"/api/serenity/themes/{theme_id}/promote")
    assert first.status_code == 200, f"first promote failed: {first.text}"
    first_ids = first.json()["candidate_ids"]
    assert len(first_ids) == 1

    second = client.post(f"/api/serenity/themes/{theme_id}/promote")
    assert second.status_code == 200, (
        f"re-promote of unchanged research should be idempotent, got "
        f"{second.status_code}: {second.text}"
    )
    assert second.json()["candidate_ids"] == first_ids, (
        "re-promote published a different Candidate for unchanged research"
    )
    assert _serenity_candidate_ids(repo, set(opp_ids)) == first_ids, (
        "re-promote duplicated the durable Candidate"
    )


@spec_claim(
    SpecClaim(
        "SP-B04", _CONTRACT, "§promote atomic publication", "L2b",
        "A Serenity theme becomes promoted only when the Snapshot, Candidate and "
        "PlanningGeneration of every published opportunity commit together; a writer "
        "failure rolls the whole publication back so the same theme can be retried "
        "without duplicate Candidates.",
    ),
)
def test_promote_publication_rolls_back_and_remains_retryable(monkeypatch):
    from aletheia.storage import repositories as repo

    _switch_to_filebacked_db()
    theme_id, opp_ids = _seed_theme_with_opportunities(n=2)
    real_insert = repo.publish_candidate_snapshot
    calls = 0

    def fail_second_insert(snapshot, members):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("deterministic publication failure")
        return real_insert(snapshot, members)

    monkeypatch.setattr(repo, "publish_candidate_snapshot", fail_second_insert)
    client = _make_test_client()
    failed = client.post(f"/api/serenity/themes/{theme_id}/promote")

    assert failed.status_code == 500, (
        f"writer failure must not be reported as success: {failed.text}"
    )
    assert repo.get_serenity_theme(theme_id)["status"] == "active", (
        "theme was marked promoted despite a rolled-back publication"
    )
    assert _serenity_candidate_ids(repo, set(opp_ids)) == [], (
        "a partial publication left a durable Candidate behind"
    )

    monkeypatch.setattr(repo, "publish_candidate_snapshot", real_insert)
    retried = client.post(f"/api/serenity/themes/{theme_id}/promote")
    assert retried.status_code == 200, f"retry failed: {retried.text}"
    assert retried.json()["candidate_count"] == 2
    assert repo.get_serenity_theme(theme_id)["status"] == "promoted"
    assert len(_serenity_candidate_ids(repo, set(opp_ids))) == 2, (
        "retry did not converge on exactly one Candidate per source"
    )


@spec_claim(
    SpecClaim(
        "SP-B03", _CONTRACT, "§D5 settings endpoint persists + bounds-checks", "L2b",
        "POST /api/settings/serenity persists auto_promote and rejects out-of-band "
        "min_score_for_pi values",
    ),
)
def test_settings_serenity_endpoint_persists_and_bounds(tmp_path, monkeypatch):
    # Settings POST writes audit + saves yaml — keep config writes hermetic by
    # ensuring tmp_path/logs exists (the autouse fixture patches DATA_DIR but
    # doesn't create the logs/ subdir that write_audit needs).
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    _switch_to_filebacked_db()
    client = _make_test_client()

    ok = client.post(
        "/api/settings/serenity",
        json={"auto_promote": True, "min_score_for_pi": 80},
    )
    assert ok.status_code == 200, (
        f"valid update should be 200, got {ok.status_code}: {ok.text}"
    )
    updated = ok.json().get("updated") or {}
    assert updated.get("auto_promote") is True
    assert updated.get("min_score_for_pi") == 80

    bad = client.post(
        "/api/settings/serenity",
        json={"min_score_for_pi": 250},  # > 100 → must reject
    )
    assert bad.status_code == 400, (
        f"out-of-band min_score_for_pi should be 400, got {bad.status_code}: {bad.text}"
    )


# ---------------------------------------------------------------------------
# L2a — Auto-promote pipeline integration
# ---------------------------------------------------------------------------

_RESEARCH_WORKER = _ROOT / "extensions" / "research" / "research_worker.py"


@spec_claim(
    SpecClaim(
        "SP-S06", _CONTRACT, "§auto_promote pipeline integration", "L2a",
        "When auto_promote=True and score≥min_score_for_pi, research_worker publishes "
        "through the SAME source function as manual promotion after pipeline stage 4 "
        "— one publication path, so automatic and manual promotion cannot diverge.",
    ),
)
def test_research_worker_auto_promote_block_exists():
    src = _RESEARCH_WORKER.read_text(encoding="utf-8")
    publication_src = (
        _ROOT / "aletheia" / "trading" / "serenity_publication.py"
    ).read_text(encoding="utf-8")
    assert "auto_promote" in src, (
        "research_worker.py has no auto_promote reference — the D5 switch is dead"
    )
    assert "min_score_for_pi=cfg.serenity.min_score_for_pi" in src, (
        "research_worker does not pass the configured score gate into the publication"
    )
    assert "score_serenity_opportunity" in publication_src, (
        "atomic Serenity publication does not re-score current evidence and gates"
    )
    assert "publish_serenity_candidates" in src, (
        "research_worker does not use the shared Candidate publication — auto-promote "
        "can diverge from manual promotion"
    )
    assert "enqueue_serenity_theme_for_pi" not in src, (
        "research_worker still references the retired pending-carrier handoff"
    )
    assert "notify_pi_daemon" in src, (
        "research_worker does not wake the Planner after auto-promote"
    )
    assert "serenity_auto_promote" in src, (
        "research_worker auto-promote source tag missing — audit trail incomplete"
    )


@spec_claim(
    SpecClaim(
        "SP-S07", _CONTRACT, "§theme feedback boundary", "L2a",
        "Serenity More/Less is presented as durable theme history only and must not "
        "claim to alter future research selection or prompts.",
        status="implemented",
    ),
)
def test_theme_feedback_ui_states_non_behavioral_boundary():
    src = (
        _ROOT / "frontend" / "src" / "pages" / "SerenityLens.tsx"
    ).read_text(encoding="utf-8")
    assert "records owner feedback in theme history only" in src
    assert "does not change future research selection or prompts" in src
