"""Spec: Serenity promote → PI evaluation queue (Phase 3 — Option A, D5 switch)

Owner decision 2026-06-27 (Option A, System Operator-centered): the apex System Operator is the SOLE
trade-decision maker. Phase 2 wired the `signal_source` discriminator on
`pi_decisions`; this phase replaces the legacy `StrategyRecord(state='draft')`
dead-record path with a real producer → consumer chain:

    POST /api/serenity/themes/{id}/promote
       → for each active serenity_opportunity under the theme:
            update_serenity_opportunity(action_intent='pi_evaluate')
            insert_pi_decision(action='signal_pending', signal_source='serenity',
                               opportunity_id=<serenity_opportunities.id>)
       → update_serenity_theme(status='promoted')
       → notify_pi_daemon(source='serenity_promote')

PI then evaluates these signals like any other input (Layer 3 already surfaces
the Serenity context — see Phase 2 / SA-S04). The D5 config switch
`aletheia.config.schema.SerenityConfig` keeps `auto_promote=False` by default —
manual-only promotion preserves the human-curator workflow.

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
        "SP-S01",
        _CONTRACT,
        "§D5 SerenityConfig defaults",
        "L2a",
        "config.schema defines SerenityConfig with auto_promote=False default so the "
        "human-curator workflow is preserved unless the owner flips the switch",
    ),
)
def test_serenity_config_default_auto_promote_off():
    from aletheia.config.schema import AletheiaConfig, SerenityConfig

    cfg = SerenityConfig()
    assert cfg.auto_promote is False, f"D5: auto_promote default must be False, got {cfg.auto_promote!r}"
    # min_score_for_pi must be present and within a sane band; the wider 0..100
    # bound is enforced by the settings route.
    assert 0 <= cfg.min_score_for_pi <= 100, f"min_score_for_pi out of band: {cfg.min_score_for_pi!r}"
    # The top-level config must register the field — otherwise no UI control
    # could ever read or write it.
    root = AletheiaConfig()
    assert hasattr(root, "serenity") and isinstance(root.serenity, SerenityConfig), (
        "AletheiaConfig.serenity is missing — Settings UI would have nothing to bind"
    )


@spec_claim(
    SpecClaim(
        "SP-S02",
        _CONTRACT,
        "§promote no longer writes draft StrategyRecord",
        "L2a",
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


def test_settings_serenity_endpoint_registered():
    src = _SETTINGS_ROUTES.read_text(encoding="utf-8")
    assert '"/serenity"' in src or "'/serenity'" in src, (
        "POST /serenity endpoint not registered — Settings UI cannot persist the D5 auto_promote switch"
    )
    assert "SerenityConfigUpdate" in src, "no SerenityConfigUpdate pydantic model — endpoint body has no schema"
    assert "auto_promote" in src and "min_score_for_pi" in src, "SerenityConfigUpdate is missing required D5 fields"
    assert "_SERENITY_BOUNDS" in src and "min_score_for_pi" in src, (
        "no bounds on min_score_for_pi — a malformed UI input could write any integer into config"
    )


@spec_claim(
    SpecClaim(
        "SP-S05",
        _CONTRACT,
        "§api_reference lists /settings/serenity row",
        "L2a",
        "api_reference.md documents the new POST /settings/serenity row — config↔UI "
        "rule (CLAUDE.md) requires every config field to be reachable from the UI and "
        "described in the API contract",
    ),
)
def test_api_reference_lists_settings_serenity():
    md = _API_REF.read_text(encoding="utf-8")
    assert "/settings/serenity" in md, (
        "api_reference.md has no POST /settings/serenity row — contract drift vs. routes/settings.py"
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

    reset_db()  # close the autouse :memory: conn on this thread
    _sq._db_conn = None  # clear so subsequent get_db() opens the file
    run_migrations()  # write schema into tmp_path/app.db


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
    assert ok.status_code == 200, f"valid update should be 200, got {ok.status_code}: {ok.text}"
    updated = ok.json().get("updated") or {}
    assert updated.get("auto_promote") is True
    assert updated.get("min_score_for_pi") == 80

    bad = client.post(
        "/api/settings/serenity",
        json={"min_score_for_pi": 250},  # > 100 → must reject
    )
    assert bad.status_code == 400, f"out-of-band min_score_for_pi should be 400, got {bad.status_code}: {bad.text}"


# ---------------------------------------------------------------------------
# L2a — Auto-promote pipeline integration
# ---------------------------------------------------------------------------

_RESEARCH_WORKER = _ROOT / "aletheia" / "agents" / "research_worker.py"


def test_theme_feedback_ui_states_non_behavioral_boundary():
    src = (_ROOT / "frontend" / "src" / "pages" / "SerenityLens.tsx").read_text(encoding="utf-8")
    assert "records owner feedback in theme history only" in src
    assert "does not change future research selection or prompts" in src
