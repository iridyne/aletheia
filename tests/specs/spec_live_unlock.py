"""Spec: Live Unlock Contract (live_unlock_contract.md)

Live trading must never be enabled without explicit human confirmation.
"""

import json

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


# ---------------------------------------------------------------------------
# L2a — Structural claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "LU-S01", "live_unlock_contract", "§1 default state", "L2a",
        "LiveConfig default is live_disabled — never live-enabled by default",
    ),
)
def test_live_default_disabled():
    import inspect
    from aletheia.config.schema import LiveConfig

    source = inspect.getsource(LiveConfig)
    assert "live_disabled" in source


@spec_claim(
    SpecClaim(
        "LU-S02", "live_unlock_contract", "§6 forbidden intents", "L2a",
        "FORBIDDEN_INTENTS is a frozenset with >=9 items: the 7 core intents "
        "plus the 2 OL-C03 double-lock additions (deactivate_kill_switch, "
        "modify_permissions)",
    ),
)
def test_forbidden_intents_structure():
    from aletheia.agent_shell.registry import FORBIDDEN_INTENTS

    assert isinstance(FORBIDDEN_INTENTS, frozenset)
    # 7 core + 2 added 2026-06-23 for the Level-3 operator double-lock (OL-C03).
    # Assert the required members rather than an exact count so future additions
    # of new forbidden intents don't spuriously fail this structural claim.
    _required = {
        "enable_live", "modify_credentials", "bypass_risk", "place_live_order",
        "modify_execution_gateway", "delete_knowledge", "change_risk_limits",
        "deactivate_kill_switch", "modify_permissions",
    }
    missing = _required - FORBIDDEN_INTENTS
    assert not missing, f"FORBIDDEN_INTENTS missing required intents: {missing}"
    assert len(FORBIDDEN_INTENTS) >= 9


# ---------------------------------------------------------------------------
# L2b — Behavioral claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "LU-B01", "live_unlock_contract", "§6 enforcement", "L2b",
        "FORBIDDEN_INTENTS are blocked by permissions.check_permission()",
    ),
)
def test_forbidden_intents_blocked_by_permissions():
    from aletheia.agent_shell.permissions import check_permission
    from aletheia.agent_shell.registry import FORBIDDEN_INTENTS

    for intent in FORBIDDEN_INTENTS:
        allowed, reason = check_permission(intent, "Full")
        assert allowed is False, f"{intent} should be blocked even in Full mode"
        assert "forbidden" in reason.lower()


@spec_claim(
    SpecClaim(
        "LU-B02", "live_unlock_contract", "§6 restricted tools", "L2b",
        "RESTRICTED_TOOLS are registered with 'restricted' permission class",
    ),
)
def test_restricted_tools_permission_class():
    from aletheia.agent_runtime.tool_registry import RESTRICTED_TOOLS, init_tool_registry
    from aletheia.agent_runtime.permissions import TOOL_REGISTRY

    init_tool_registry()
    for tool in RESTRICTED_TOOLS:
        assert tool in TOOL_REGISTRY, f"{tool} not registered"
        assert TOOL_REGISTRY[tool] == "restricted", \
            f"{tool} has class '{TOOL_REGISTRY[tool]}' instead of 'restricted'"


# ---------------------------------------------------------------------------
# L2c — Invariant claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "LU-C01", "live_unlock_contract", "§6 no escalation", "L2c",
        "No permission_mode grants access to FORBIDDEN_INTENTS",
    ),
)
def test_no_mode_permits_forbidden_intents():
    from aletheia.agent_shell.permissions import check_permission
    from aletheia.agent_shell.registry import FORBIDDEN_INTENTS

    for mode in ("Observe", "Paper", "Full"):
        for intent in FORBIDDEN_INTENTS:
            allowed, _ = check_permission(intent, mode)
            assert allowed is False, (
                f"INVARIANT VIOLATION: {intent} permitted in {mode} mode"
            )


# ---------------------------------------------------------------------------
# L2b — Behavioral: HTTP endpoint safety (live.py)
# ---------------------------------------------------------------------------


def _make_live_app():
    """Minimal FastAPI app with only the live-unlock router mounted."""
    from fastapi import FastAPI
    from aletheia.server.routes.live import router as live_router

    app = FastAPI()
    app.include_router(live_router)
    return app


@spec_claim(
    SpecClaim(
        "LU-B03", "live_unlock_contract", "§2 unlock prerequisites", "L2b",
        "POST /api/settings/live-unlock without confirm=true returns 4xx",
    ),
)
def test_live_unlock_requires_confirm(tmp_path, monkeypatch):
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    from fastapi.testclient import TestClient

    client = TestClient(_make_live_app(), raise_server_exceptions=False)
    resp = client.post("/api/settings/live-unlock", json={"confirm": False})
    assert resp.status_code >= 400, (
        f"Expected 4xx when confirm=false, got {resp.status_code}"
    )


@spec_claim(
    SpecClaim(
        "LU-B04", "live_unlock_contract", "§2 first-unlock acknowledge", "L2b",
        "Without acknowledge_first_unlock=true on first unlock, returns 4xx",
    ),
)
def test_live_unlock_first_time_requires_acknowledge(tmp_path, monkeypatch):
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    from fastapi.testclient import TestClient

    client = TestClient(_make_live_app(), raise_server_exceptions=False)
    # confirm=true but acknowledge_first_unlock omitted (defaults False)
    # first_live_confirmation_done defaults False in LiveConfig
    resp = client.post(
        "/api/settings/live-unlock",
        json={"confirm": True, "acknowledge_first_unlock": False},
    )
    assert resp.status_code >= 400, (
        f"Expected 4xx for first unlock without acknowledge, got {resp.status_code}"
    )


@spec_claim(
    SpecClaim(
        "LU-B05", "live_unlock_contract", "§2 one-way", "L2b",
        "Once state is live_limited, unlock endpoint does NOT escalate further",
    ),
)
def test_live_unlock_no_escalation_from_limited(tmp_path, monkeypatch):
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    # Pre-seed config with live_limited state
    import yaml
    from aletheia.config.schema import AletheiaConfig

    cfg = AletheiaConfig()
    cfg.live.state = "live_limited"
    cfg.live.first_live_confirmation_done = True
    (tmp_path / "config.yaml").write_text(
        yaml.dump(cfg.model_dump(mode="json")), encoding="utf-8"
    )

    from fastapi.testclient import TestClient

    client = TestClient(_make_live_app(), raise_server_exceptions=False)
    resp = client.post(
        "/api/settings/live-unlock",
        json={"confirm": True, "acknowledge_first_unlock": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    # Must not transition beyond live_limited — returned state stays live_limited
    assert data.get("status") == "live_limited", (
        f"Expected status=live_limited (no escalation), got {data}"
    )


@spec_claim(
    SpecClaim(
        "LU-B06", "live_unlock_contract", "§3 audit format", "L2b",
        "Successful unlock writes audit event with actor/source/event_type fields",
    ),
)
def test_live_unlock_writes_audit_event(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.files.jsonl as jsonl_mod

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    # jsonl._log_dir() calls get_data_dir() from loader — monkeypatch covers it
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    # Reset cached hash so the new log dir is clean
    jsonl_mod._last_audit_hash = None

    import yaml
    from aletheia.config.schema import AletheiaConfig

    cfg = AletheiaConfig()
    cfg.live.state = "live_disabled"
    cfg.live.first_live_confirmation_done = False
    (tmp_path / "config.yaml").write_text(
        yaml.dump(cfg.model_dump(mode="json")), encoding="utf-8"
    )

    from fastapi.testclient import TestClient

    client = TestClient(_make_live_app(), raise_server_exceptions=False)
    resp = client.post(
        "/api/settings/live-unlock",
        json={"confirm": True, "acknowledge_first_unlock": True},
    )
    assert resp.status_code == 200, f"Unexpected status: {resp.status_code}"

    audit_path = tmp_path / "logs" / "audit.jsonl"
    assert audit_path.exists(), "audit.jsonl not created"
    lines = [l for l in audit_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert lines, "audit.jsonl is empty"
    last = json.loads(lines[-1])
    assert last.get("actor") == "human", f"actor field wrong: {last}"
    assert last.get("source") == "dashboard", f"source field wrong: {last}"
    assert last.get("event_type") == "live_unlocked", f"event_type field wrong: {last}"


# ---------------------------------------------------------------------------
# L2c — Invariants: naming conventions and permission constants
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "LU-C02", "live_unlock_contract", "§6 naming convention", "L2c",
        "Every FORBIDDEN_INTENTS member contains an underscore and is lowercase",
    ),
)
def test_forbidden_intents_naming_convention():
    from aletheia.agent_shell.registry import FORBIDDEN_INTENTS

    for intent in FORBIDDEN_INTENTS:
        assert intent == intent.lower(), (
            f"FORBIDDEN_INTENTS member '{intent}' is not lowercase"
        )
        assert "_" in intent, (
            f"FORBIDDEN_INTENTS member '{intent}' contains no underscore"
        )


@spec_claim(
    SpecClaim(
        "LU-C03", "live_unlock_contract", "§6 restricted tools", "L2c",
        "TOOL_CLASSES['restricted'] is the empty set — no mode grants restricted access",
    ),
)
def test_restricted_tool_class_is_empty_set():
    from aletheia.agent_runtime.permissions import TOOL_CLASSES

    restricted_modes = TOOL_CLASSES.get("restricted")
    assert restricted_modes == set(), (
        f"INVARIANT VIOLATION: TOOL_CLASSES['restricted'] = {restricted_modes!r}, "
        "expected empty set — no permission_mode should satisfy restricted access"
    )


# ---------------------------------------------------------------------------
# GAP-LU-02: §4b — unlock must elevate operator.permission_mode to Auto
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "LU-B07", "live_unlock_contract", "§4b pi full authority on unlock", "L2b",
        "POST /api/settings/live-unlock transitions operator.permission_mode to Auto",
    ),
)
def test_live_unlock_sets_operator_auto(tmp_path, monkeypatch):
    import yaml
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    from aletheia.config.schema import AletheiaConfig
    from fastapi.testclient import TestClient

    cfg = AletheiaConfig()
    cfg.live.state = "live_disabled"
    cfg.live.first_live_confirmation_done = False
    cfg.operator.permission_mode = "ReadOnly"
    (tmp_path / "config.yaml").write_text(
        yaml.dump(cfg.model_dump(mode="json")), encoding="utf-8"
    )

    client = TestClient(_make_live_app(), raise_server_exceptions=False)
    resp = client.post(
        "/api/settings/live-unlock",
        json={"confirm": True, "acknowledge_first_unlock": True},
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

    # Re-read persisted config to verify operator.permission_mode was set
    from aletheia.config.loader import load_config
    saved = load_config()
    assert saved.operator.permission_mode == "Auto", (
        f"§4b INVARIANT VIOLATION: operator.permission_mode after unlock = "
        f"'{saved.operator.permission_mode}', expected 'Auto'"
    )


@spec_claim(
    SpecClaim(
        "LU-C04", "live_unlock_contract", "§4b pi authority invariant", "L2c",
        "After live_unlock: live.state=live_limited AND operator.permission_mode=Auto (both must hold)",
    ),
)
def test_unlock_invariant_state_and_permission(tmp_path, monkeypatch):
    import yaml
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    from aletheia.config.schema import AletheiaConfig
    from fastapi.testclient import TestClient

    cfg = AletheiaConfig()
    cfg.live.state = "live_disabled"
    cfg.live.first_live_confirmation_done = True  # already confirmed once
    (tmp_path / "config.yaml").write_text(
        yaml.dump(cfg.model_dump(mode="json")), encoding="utf-8"
    )

    client = TestClient(_make_live_app(), raise_server_exceptions=False)
    resp = client.post(
        "/api/settings/live-unlock",
        json={"confirm": True, "acknowledge_first_unlock": True},
    )
    assert resp.status_code == 200

    from aletheia.config.loader import load_config
    saved = load_config()
    assert saved.live.state == "live_limited", (
        f"Expected live.state=live_limited, got '{saved.live.state}'"
    )
    assert saved.operator.permission_mode == "Auto", (
        f"Expected operator.permission_mode=Auto, got '{saved.operator.permission_mode}'"
    )


# ---------------------------------------------------------------------------
# GAP-LU-05: §4b — FORBIDDEN_INTENTS are blocked even under operator Auto mode
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "LU-C01b", "live_unlock_contract", "§4b+§6 constitution survives Auto", "L2c",
        "FORBIDDEN_INTENTS are blocked by check_permission() across ALL operator modes including Auto",
    ),
)
def test_forbidden_intents_blocked_in_all_operator_modes():
    from aletheia.agent_shell.permissions import check_permission
    from aletheia.agent_shell.registry import FORBIDDEN_INTENTS

    # Cover worker modes (existing LU-C01) + operator modes including Auto (GAP-LU-05)
    worker_modes = ("Observe", "Paper", "Full")
    operator_modes = ("ReadOnly", "Research", "PaperAuto", "AllowEditing", "Auto")

    for mode in worker_modes:
        for intent in FORBIDDEN_INTENTS:
            allowed, _ = check_permission(intent, mode)
            assert allowed is False, (
                f"INVARIANT VIOLATION: {intent} permitted in worker mode {mode}"
            )

    for mode in operator_modes:
        for intent in FORBIDDEN_INTENTS:
            allowed, _ = check_permission(intent, mode, permission_scope="operator")
            assert allowed is False, (
                f"INVARIANT VIOLATION: {intent} permitted in operator mode {mode} — "
                "constitution (§4b) must hold even under Auto(4)"
            )


# ---------------------------------------------------------------------------
# GAP-LU-04: §4b — PI full authority scoped to main track (spec claim only,
# code note: no runtime filter yet — this claim is status="missing" until the
# market_type scope check is added to permissions.py)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "LU-C05", "live_unlock_contract", "§4b main-track scope", "L2c",
        "Auto mode is scoped to main track (perp/spot); serenity track is governed "
        "by serenity_executor independently — no shared permission elevation",
        status="missing",  # Tripwire: serenity_executor market_type isolation not yet enforced in code
    ),
)
def test_auto_mode_scope_main_track_only():
    # This is a structural RED tripwire — passes xfail until the market_type
    # scope check is added to permissions.py / serenity_executor boundary.
    # When implemented, this test should verify that Auto permission is only
    # propagated to the main-track executor, not to serenity_executor paths.
    pytest.xfail(
        "LU-C05 tripwire: serenity_executor market_type isolation not enforced in "
        "permissions.py — deferred to serenity Phase 5 hardening"
    )


# ---------------------------------------------------------------------------
# GAP-LU-06: §4b — lock must reset operator.permission_mode symmetrically
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "LU-C06", "live_unlock_contract", "§4b lock symmetry", "L2c",
        "POST /api/settings/live-lock resets operator.permission_mode to ReadOnly "
        "(symmetric with unlock which elevates to Auto — no one-way ratchet)",
    ),
)
def test_live_lock_resets_operator_permission(tmp_path, monkeypatch):
    import yaml
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    from aletheia.config.schema import AletheiaConfig
    from fastapi.testclient import TestClient

    # Pre-seed with live_limited + Auto (as if unlock already ran)
    cfg = AletheiaConfig()
    cfg.live.state = "live_limited"
    cfg.live.first_live_confirmation_done = True
    cfg.operator.permission_mode = "Auto"
    (tmp_path / "config.yaml").write_text(
        yaml.dump(cfg.model_dump(mode="json")), encoding="utf-8"
    )

    client = TestClient(_make_live_app(), raise_server_exceptions=False)
    resp = client.post("/api/settings/live-lock")
    assert resp.status_code == 200

    from aletheia.config.loader import load_config
    saved = load_config()
    assert saved.live.state == "live_disabled", (
        f"Expected live.state=live_disabled after lock, got '{saved.live.state}'"
    )
    assert saved.operator.permission_mode == "ReadOnly", (
        f"INVARIANT VIOLATION: operator.permission_mode after lock = "
        f"'{saved.operator.permission_mode}', expected 'ReadOnly' — "
        "lock must not leave PI with Auto authority"
    )


# ---------------------------------------------------------------------------
# GAP-LU-07: §4b — UI warning wall text elements (static source check)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "LU-S03", "live_unlock_contract", "§4b ui warning wall", "L2a",
        "Settings.tsx LiveUnlockWarningWall contains all three required elements: "
        "(1) PI Agent 将完全自主执行交易，操作不可预测 "
        "(2) 真实资金损失可能发生 "
        "(3) 开启后所有操作由人类承担后果",
    ),
)
def test_ui_warning_wall_three_elements():
    import pathlib

    settings_tsx = pathlib.Path(__file__).parents[2] / "frontend" / "src" / "pages" / "Settings.tsx"
    assert settings_tsx.exists(), f"Settings.tsx not found at {settings_tsx}"
    source = settings_tsx.read_text(encoding="utf-8")

    required_phrases = [
        "PI Agent 将完全自主执行交易，操作不可预测",
        "真实资金损失可能发生",
        "开启后所有操作由人类承担后果",
    ]
    for phrase in required_phrases:
        assert phrase in source, (
            f"§4b UI warning wall MISSING required element: '{phrase}' "
            f"not found in Settings.tsx — all three consent elements must be present"
        )
