"""Focused regression tests for reconciliation incidents and Learning refreeze."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib
import json
from types import SimpleNamespace

import pytest


def _position(*, pid: str, symbol: str, source: str, opened_at: str,
              closed_at: str | None = None, close_reason: str | None = None,
              close_category: str = "unknown", semantics_version: str | None = None,
              execution_mode: str = "okx_demo", account_fingerprint: str | None = None):
    from aletheia.storage.models import Position

    identity = None
    if account_fingerprint:
        identity = json.dumps({
            "broker_id": execution_mode,
            "account_environment": "demo",
            "account_fingerprint": account_fingerprint,
        })
    return Position(
        id=pid,
        symbol=symbol,
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        execution_mode=execution_mode,
        status="closed" if closed_at else "open",
        source=source,
        opened_at=opened_at,
        closed_at=closed_at,
        close_reason=close_reason,
        close_category=close_category,
        outcome_semantics_version=semantics_version,
        reconciliation_identity=identity,
    )


def _insert_churn_pair(*, symbol: str, source: str, offset_minutes: int,
                       close_reason: str = "reconciled_gone",
                       close_category: str = "unknown",
                       semantics_version: str | None = None,
                       execution_mode: str = "okx_demo",
                       closed_account: str | None = None,
                       reopened_account: str | None = None):
    from aletheia.storage import repositories as repo

    now = datetime.now(timezone.utc)
    closed_at = now - timedelta(minutes=offset_minutes)
    reopened_at = closed_at + timedelta(minutes=1)
    token = symbol.replace("/", "-")
    repo.insert_position(_position(
        pid=f"closed-{token}-{offset_minutes}", symbol=symbol, source="native",
        opened_at=(closed_at - timedelta(hours=1)).isoformat(),
        closed_at=closed_at.isoformat(), close_reason=close_reason,
        close_category=close_category, semantics_version=semantics_version,
        execution_mode=execution_mode, account_fingerprint=closed_account,
    ))
    repo.insert_position(_position(
        pid=f"reopen-{token}-{offset_minutes}", symbol=symbol, source=source,
        opened_at=reopened_at.isoformat(), execution_mode=execution_mode,
        account_fingerprint=reopened_account,
    ))


def test_detector_matches_native_and_adopted_reopens_with_scoped_identity():
    from aletheia.portfolio.position_reconciler import detect_reconcile_thrash

    _insert_churn_pair(symbol="BTC/USDT", source="native", offset_minutes=8)
    _insert_churn_pair(symbol="ETH/USDT", source="adopted", offset_minutes=6)
    # Genuine close is never an incident, even with a later same-symbol open.
    _insert_churn_pair(
        symbol="SOL/USDT", source="native", offset_minutes=4,
        close_reason="take_profit", close_category="strategy",
        semantics_version="oi-v1",
    )
    # Explicitly different accounts do not match despite identical instrument identity.
    _insert_churn_pair(
        symbol="XRP/USDT", source="native", offset_minutes=3,
        closed_account="account-a", reopened_account="account-b",
    )

    detected = detect_reconcile_thrash(window_minutes=30)
    assert detected["is_thrashing"] is True
    assert detected["churn_count"] == 2
    assert set(detected["churned_symbols"]) == {"BTC/USDT:long", "ETH/USDT:long"}
    assert detected["reopened_by_source"] == {"adopted": 1, "native": 1}
    assert {row["market_type"] for row in detected["churned_identities"]} == {
        "usdt_futures"
    }
    assert all(row["execution_mode"] == "okx_demo" for row in detected["churned_identities"])


def test_structured_administrative_category_triggers_without_hardcoded_reason():
    from aletheia.portfolio.position_reconciler import detect_reconcile_thrash

    for idx, symbol in enumerate(("ADA/USDT", "DOGE/USDT"), start=2):
        _insert_churn_pair(
            symbol=symbol, source="native", offset_minutes=idx,
            close_reason="external_disappearance", close_category="administrative",
            semantics_version="oi-v1",
        )
    detected = detect_reconcile_thrash(window_minutes=30)
    assert detected["is_thrashing"] is True
    assert detected["administrative_close_recent"] == 2
    assert detected["reopened_by_source"] == {"native": 2}


def test_incident_is_durable_idempotent_and_requires_explicit_clean_resolution(monkeypatch):
    from aletheia.portfolio import reconciliation_integrity as integrity

    base = datetime(2026, 7, 12, 5, 0, tzinfo=timezone.utc)
    detection = {
        "is_thrashing": True,
        "window_minutes": 30,
        "churn_count": 2,
        "churned_symbols": ["BTC/USDT:long", "ETH/USDT:long"],
        "churned_identities": [
            {"identity_key": "one", "reopen_source": "native"},
            {"identity_key": "two", "reopen_source": "adopted"},
        ],
        "reopened_by_source": {"native": 1, "adopted": 1},
    }
    first, created = integrity.record_reconciliation_integrity_incident(
        detection, now=base
    )
    second, created_again = integrity.record_reconciliation_integrity_incident(
        detection, now=base
    )
    assert created is True
    assert created_again is False
    assert second["id"] == first["id"]
    assert integrity.get_active_reconciliation_integrity_incident()["id"] == first["id"]

    with pytest.raises(ValueError, match="clean observation"):
        integrity.resolve_reconciliation_integrity_incident(
            first["id"], actor="owner", reason="reviewed", now=base
        )

    assert integrity.record_reconciliation_clean_observation(
        first["id"], now=base + timedelta(minutes=5)
    )["clean_observation_count"] == 1
    # Exact/rapid retry cannot manufacture another clean window.
    assert integrity.record_reconciliation_clean_observation(
        first["id"], now=base + timedelta(minutes=5)
    )["clean_observation_count"] == 1
    assert integrity.record_reconciliation_clean_observation(
        first["id"], now=base + timedelta(minutes=10)
    )["clean_observation_count"] == 2

    monkeypatch.setattr(
        "aletheia.portfolio.position_reconciler.detect_reconcile_thrash",
        lambda window_minutes=30: {"is_thrashing": False},
    )
    resolved = integrity.resolve_reconciliation_integrity_incident(
        first["id"], actor="owner", reason="two clean cycles reviewed", now=base + timedelta(minutes=11)
    )
    assert resolved["status"] == "resolved"
    assert resolved["resolved_by"] == "owner"
    assert integrity.get_active_reconciliation_integrity_incident() is None
    from aletheia.storage import repositories as repo
    audit = repo.list_audit_events(limit=5)
    assert audit[0]["event_type"] == "reconciliation_integrity_incident_resolved"
    assert audit[0]["entity_id"] == first["id"]


def test_run_reconcile_keeps_destructive_actions_suspended_until_resolution(monkeypatch):
    from aletheia.portfolio import position_reconciler as reconciler

    item = reconciler.ReconcileItem(
        symbol="BTC/USDT", direction="long", classification="orphaned",
        local={"id": "position-open"},
    )
    classified = reconciler.ReconcileResult(orphaned=[item], sync_status="ok")
    monkeypatch.setattr(reconciler, "classify_positions", lambda mode=None: classified)
    monkeypatch.setattr(reconciler, "detect_reconcile_thrash", lambda window_minutes=30: {
        "is_thrashing": True, "window_minutes": 30, "churn_count": 2,
        "churned_symbols": ["BTC/USDT:long", "ETH/USDT:long"],
        "churned_identities": [
            {"identity_key": "one", "reopen_source": "native"},
            {"identity_key": "two", "reopen_source": "adopted"},
        ],
        "reopened_by_source": {"native": 1, "adopted": 1},
        "reconciled_gone_recent": 2, "adopted_recent": 1,
        "administrative_close_recent": 2, "reopened_recent": 2,
    })
    closed: list[str] = []
    monkeypatch.setattr(reconciler, "reconcile_close_orphan", lambda value: closed.append(value.symbol))
    monkeypatch.setattr("aletheia.files.jsonl.write_audit", lambda *a, **k: None)
    monkeypatch.setattr("aletheia.notifications.notify_operator", lambda *a, **k: None)

    first = reconciler.run_reconcile("demo")
    assert first["suspended"] is True
    assert first["incident"]["status"] == "active"
    assert closed == []

    monkeypatch.setattr(reconciler, "detect_reconcile_thrash", lambda window_minutes=30: {
        "is_thrashing": False, "window_minutes": 30, "churn_count": 0,
        "churned_symbols": [], "churned_identities": [], "reopened_by_source": {},
        "reconciled_gone_recent": 0, "adopted_recent": 0,
        "administrative_close_recent": 0, "reopened_recent": 0,
    })
    second = reconciler.run_reconcile("demo")
    assert second["suspended"] is True
    assert second["incident"]["status"] == "active"
    assert second["recovery_state"] in {"observing_clean", "clean_gate_met"}
    assert closed == []


def test_run_reconcile_fails_closed_when_incident_detector_is_unavailable(monkeypatch):
    from aletheia.portfolio import position_reconciler as reconciler

    item = reconciler.ReconcileItem(
        symbol="BTC/USDT", direction="long", classification="orphaned",
        local={"id": "position-open"},
    )
    monkeypatch.setattr(
        reconciler, "classify_positions",
        lambda mode=None: reconciler.ReconcileResult(orphaned=[item], sync_status="ok"),
    )
    monkeypatch.setattr(reconciler, "detect_reconcile_thrash", lambda: {
        "detector_available": False, "is_thrashing": False,
    })
    monkeypatch.setattr(
        reconciler, "reconcile_close_orphan",
        lambda value: (_ for _ in ()).throw(AssertionError("must stay suspended")),
    )
    result = reconciler.run_reconcile("demo")
    assert result["suspended"] is True
    assert result["recovery_state"] == "detector_unavailable"
    assert result["closed_orphans"] == []


def test_active_incident_refreezes_jobs_treatments_prompts_and_survives_reload(monkeypatch):
    from aletheia.portfolio import reconciliation_integrity as integrity
    from aletheia.learning import control

    incident, _ = integrity.record_reconciliation_integrity_incident({
        "is_thrashing": True, "window_minutes": 30, "churn_count": 2,
        "churned_symbols": ["A/USDT:long", "B/USDT:long"],
        "churned_identities": [{"identity_key": "a"}, {"identity_key": "b"}],
        "reopened_by_source": {"native": 2},
    })
    cfg = SimpleNamespace(
        agent=SimpleNamespace(autonomous_learning=True),
        runtime=SimpleNamespace(paused=False),
    )
    monkeypatch.setattr(control, "load_config", lambda: cfg)
    assert control.global_learning_refreeze_reason() == (
        f"reconciliation_integrity_incident:{incident['id']}"
    )
    assert control.learning_job_gate_reason("lesson_reviewer") == (
        "reconciliation_integrity_incident"
    )
    assert control.learning_influence_enabled() is False

    reloaded = importlib.reload(control)
    monkeypatch.setattr(reloaded, "load_config", lambda: cfg)
    assert reloaded.global_learning_refreeze_reason().endswith(incident["id"])

    from aletheia.learning import experiments
    monkeypatch.setattr(
        experiments, "_ensure_experiment",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must stay baseline")),
    )
    decision = experiments.apply_numeric_treatment(
        path_id="ranker_case_score", scope={}, action={}, source_cohort="test",
        target_horizon="4h", candidate_key="candidate", baseline=10.0,
        treatment=20.0, consumer="ranker", consumer_stage="score",
    )
    assert decision.value == 10.0
    assert decision.applied is False
    assert decision.reason == "reconciliation_integrity_incident"


def test_ranker_guard_and_persona_learning_return_static_baseline_during_incident(monkeypatch):
    from aletheia.portfolio import reconciliation_integrity as integrity

    integrity.record_reconciliation_integrity_incident({
        "is_thrashing": True, "window_minutes": 30, "churn_count": 2,
        "churned_symbols": ["A/USDT:long", "B/USDT:long"],
        "churned_identities": [{"identity_key": "a"}, {"identity_key": "b"}],
        "reopened_by_source": {"native": 2},
    })

    from aletheia.agents import paper_trader_ranker as ranker
    monkeypatch.setattr(
        ranker, "evaluate_guards",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("guard must refreeze")),
    )
    result = ranker._evaluate_guards_safe(
        {"symbol": "BTC/USDT", "market": "usdt_futures"}, None, None,
        "long", "high", False, None, None,
    )
    assert result is None

    from aletheia.trading import sizing_policy
    cfg = SimpleNamespace(trading=SimpleNamespace(
        persona_learning_enabled=True, persona_learning_min_samples=1,
    ))
    monkeypatch.setattr(sizing_policy, "_clean_bucket_rewards", lambda bucket: [10.0])
    assert sizing_policy.learned_appetite_adjustment("bull", cfg=cfg) == 0.0


def test_operator_prompt_progressive_unlock_and_rollout_share_incident_refreeze(monkeypatch):
    from aletheia.portfolio import reconciliation_integrity as integrity

    incident, _ = integrity.record_reconciliation_integrity_incident({
        "is_thrashing": True, "window_minutes": 30, "churn_count": 2,
        "churned_symbols": ["A/USDT:long", "B/USDT:long"],
        "churned_identities": [{"identity_key": "a"}, {"identity_key": "b"}],
        "reopened_by_source": {"native": 2},
    })

    from aletheia.agent_shell import trading_context
    monkeypatch.setattr(trading_context, "_build_layer1_snapshot", lambda: {})
    monkeypatch.setattr(trading_context, "_build_layer2_short_term", lambda: {
        "reflection": "learned", "trading_memo": "learned",
        "reasoning_feedback": "learned", "calibration_feedback": "learned",
    })
    monkeypatch.setattr(trading_context, "_build_layer3_wisdom", lambda: {
        "lessons": ["learned"], "ps_lessons": ["learned"],
        "guard_candidates": ["learned"],
    })
    context = trading_context.build_enriched_trading_context()
    assert all(context["layer2"][key] is None for key in (
        "reflection", "trading_memo", "reasoning_feedback", "calibration_feedback",
    ))
    assert context["layer3"]["lessons"] == []
    assert context["layer3"]["ps_lessons"] == []
    assert context["learning_manifest"]["components"] == {}

    from aletheia.trading import persona
    learned_persona = SimpleNamespace(
        operator_decidable={"base_pct": True},
        unlock_ladder=["demo_leverage_cap"],
    )
    persona_cfg = SimpleNamespace(trading=SimpleNamespace(
        persona_progressive_unlock_enabled=True,
    ))
    assert persona.effective_operator_decidable(learned_persona, persona_cfg) == {
        "base_pct": True
    }

    from aletheia.trading import persona_rollout
    rollout_cfg = SimpleNamespace(trading=SimpleNamespace(
        persona_auto_advance_enabled=True,
        persona_sizing_enabled=True,
    ))
    monkeypatch.setattr("aletheia.config.loader.load_config", lambda: rollout_cfg)
    monkeypatch.setattr(
        "aletheia.config.loader.save_config",
        lambda cfg: (_ for _ in ()).throw(AssertionError("must not advance")),
    )
    rollout = persona_rollout.advance_persona_rollout()
    assert rollout == {
        "action": "hold",
        "reason": f"reconciliation_integrity_incident:{incident['id']}",
    }
