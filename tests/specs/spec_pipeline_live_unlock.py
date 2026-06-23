"""Spec: Pipeline — Live Unlock (end-to-end)

E2E pipeline claims for POST /api/settings/live-unlock → permission/confirm
guard → LiveConfig state transition → audit emission.

Contract: pipeline_live_unlock
Claim prefix: PL-LU
"""

import json

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_live_app():
    """Minimal FastAPI app with only the live-unlock router mounted."""
    from fastapi import FastAPI
    from aletheia.server.routes.live import router as live_router

    app = FastAPI()
    app.include_router(live_router)
    return app


def _setup_config(tmp_path, monkeypatch, state="live_disabled", first_done=False):
    """Seed config.yaml and wire monkeypatches for HTTP-level tests."""
    import yaml
    import aletheia.config.loader as loader
    import aletheia.files.jsonl as jsonl_mod
    from aletheia.config.schema import AletheiaConfig

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    jsonl_mod._last_audit_hash = None

    cfg = AletheiaConfig()
    cfg.live.state = state
    cfg.live.first_live_confirmation_done = first_done
    (tmp_path / "config.yaml").write_text(
        yaml.dump(cfg.model_dump(mode="json")), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# PL-LU-01  L2b  confirm_required: POST without confirm=true → 4xx
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-LU-01", "pipeline_live_unlock", "§2 confirm_required", "L2b",
        "POST /api/settings/live-unlock without confirm=true returns 4xx; "
        "LiveConfig.state remains unchanged",
    ),
)
def test_live_unlock_confirm_required(tmp_path, monkeypatch):
    _setup_config(tmp_path, monkeypatch, state="live_disabled")

    from fastapi.testclient import TestClient
    from aletheia.config.loader import load_config

    client = TestClient(_make_live_app(), raise_server_exceptions=False)

    resp = client.post("/api/settings/live-unlock", json={"confirm": False})
    assert resp.status_code >= 400, (
        f"Expected 4xx when confirm=false, got {resp.status_code}: {resp.text}"
    )

    # State must be unchanged
    cfg_after = load_config()
    assert cfg_after.live.state == "live_disabled", (
        f"LiveConfig.state changed despite rejected request: {cfg_after.live.state}"
    )


# ---------------------------------------------------------------------------
# PL-LU-02  L2b  first_unlock_acknowledge: first-time unlock requires acknowledge flag
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-LU-02", "pipeline_live_unlock", "§2 first_unlock_acknowledge", "L2b",
        "POST with confirm=true but acknowledge_first_unlock missing on "
        "first-time unlock → 4xx",
    ),
)
def test_live_unlock_first_unlock_acknowledge_required(tmp_path, monkeypatch):
    _setup_config(tmp_path, monkeypatch, state="live_disabled", first_done=False)

    from fastapi.testclient import TestClient

    client = TestClient(_make_live_app(), raise_server_exceptions=False)

    resp = client.post(
        "/api/settings/live-unlock",
        json={"confirm": True, "acknowledge_first_unlock": False},
    )
    assert resp.status_code >= 400, (
        f"Expected 4xx for first unlock without acknowledge, "
        f"got {resp.status_code}: {resp.text}"
    )


# ---------------------------------------------------------------------------
# PL-LU-03  L2b  one_way_transition: live_limited never escalates further
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-LU-03", "pipeline_live_unlock", "§2 one_way_transition", "L2b",
        "Successful unlock transitions live_disabled → live_limited; "
        "second call from live_limited does NOT escalate to higher state",
    ),
)
def test_live_unlock_one_way_transition(tmp_path, monkeypatch):
    # Pre-seed at live_limited (post-first-unlock state)
    _setup_config(tmp_path, monkeypatch, state="live_limited", first_done=True)

    from fastapi.testclient import TestClient

    client = TestClient(_make_live_app(), raise_server_exceptions=False)

    resp = client.post(
        "/api/settings/live-unlock",
        json={"confirm": True, "acknowledge_first_unlock": True},
    )
    assert resp.status_code == 200, (
        f"Expected 200 from live_limited state, got {resp.status_code}: {resp.text}"
    )
    data = resp.json()
    # live_limited must stay live_limited — must not escalate
    assert data.get("status") == "live_limited", (
        f"Expected status=live_limited (no escalation beyond live_limited), "
        f"got: {data}"
    )


# ---------------------------------------------------------------------------
# PL-LU-04  L2b  audit_format: successful unlock writes audit with required fields
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-LU-04", "pipeline_live_unlock", "§3 audit_format", "L2b",
        "Successful first unlock writes audit row with actor='human', "
        "source='dashboard', event_type='live_unlocked' — in both JSONL "
        "AND audit_events SQLite table",
    ),
)
def test_live_unlock_audit_format(tmp_path, monkeypatch):
    import sqlite3

    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    _setup_config(tmp_path, monkeypatch, state="live_disabled", first_done=False)

    # Wire up SQLite DB so audit_events table exists
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    sq._db_conn = conn
    run_migrations()

    from fastapi.testclient import TestClient

    client = TestClient(_make_live_app(), raise_server_exceptions=False)
    resp = client.post(
        "/api/settings/live-unlock",
        json={"confirm": True, "acknowledge_first_unlock": True},
    )
    assert resp.status_code == 200, (
        f"Unexpected status: {resp.status_code}: {resp.text}"
    )

    # --- JSONL audit check ---
    audit_path = tmp_path / "logs" / "audit.jsonl"
    assert audit_path.exists(), "audit.jsonl was not created"
    lines = [
        l for l in audit_path.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    assert lines, "audit.jsonl is empty"
    last = json.loads(lines[-1])
    assert last.get("actor") == "human", f"JSONL actor field wrong: {last}"
    assert last.get("source") == "dashboard", f"JSONL source field wrong: {last}"
    assert last.get("event_type") == "live_unlocked", (
        f"JSONL event_type field wrong: {last}"
    )

    # --- SQLite audit_events check ---
    audit_rows = conn.execute(
        "SELECT * FROM audit_events WHERE event_type=? AND actor=? AND source=?",
        ("live_unlocked", "human", "dashboard"),
    ).fetchall()
    assert len(audit_rows) >= 1, (
        "Expected audit_events row with event_type='live_unlocked', actor='human', "
        f"source='dashboard'. All audit events: "
        f"{[dict(r) for r in conn.execute('SELECT event_type, actor, source FROM audit_events').fetchall()]}"
    )


# ---------------------------------------------------------------------------
# PL-LU-05  L2c  forbidden_intents_double_lock — captures OL-C03
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-LU-05", "pipeline_live_unlock", "§6 forbidden_intents_double_lock", "L2c",
        "deactivate_kill_switch and modify_permissions are in FORBIDDEN_INTENTS "
        "(double-lock defense-in-depth for Level-3 operator actions)",
        status="implemented",
    ),
)
def test_forbidden_intents_cover_level3_actions():
    """Assert that the two Level-3 operator actions appear in FORBIDDEN_INTENTS.

    Fixed 2026-06-23 (OL-C03 / §15.1): both deactivate_kill_switch and
    modify_permissions are now in FORBIDDEN_INTENTS (registry.py:415), giving
    the two highest-risk Level-3 operator actions a permission-layer hard wall
    on top of their Level-3 classification.
    """
    from aletheia.agent_shell.registry import FORBIDDEN_INTENTS

    assert "deactivate_kill_switch" in FORBIDDEN_INTENTS, (
        "INVARIANT VIOLATION: deactivate_kill_switch not in FORBIDDEN_INTENTS "
        "(OL-C03 double-lock gap — §15.1)"
    )
    assert "modify_permissions" in FORBIDDEN_INTENTS, (
        "INVARIANT VIOLATION: modify_permissions not in FORBIDDEN_INTENTS "
        "(OL-C03 double-lock gap — §15.1)"
    )
