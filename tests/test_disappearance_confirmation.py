"""Focused regressions for durable, account-scoped disappearance evidence."""

from __future__ import annotations

import importlib
import json

from aletheia.storage.models import Position
from aletheia.storage import repositories as repo
from tests.test_position_reconciler import _ex, setup_env


def _position(*, execution_mode: str = "bitget_demo") -> Position:
    return Position(
        id="position-under-test",
        symbol="ETH/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=3_000.0,
        current_price=3_050.0,
        execution_mode=execution_mode,
    )


def _legacy_snapshot(monkeypatch, *, positions: list[dict], fetched_at: str,
                     age_seconds: float = 1.0):
    from aletheia.portfolio import position_reconciler as reconciler

    snapshot = {
        "positions": positions,
        "open_positions_count": len(positions),
        "age_seconds": age_seconds,
        "fetched_at": fetched_at,
    }
    monkeypatch.setattr(reconciler.exchange_sync, "get_unified_cached_state", lambda: None)
    monkeypatch.setattr(
        reconciler.exchange_sync, "get_cached_state", lambda mode=None: snapshot,
    )
    return snapshot


def test_one_complete_absence_creates_candidate_but_never_closes(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    position = _position()
    repo.insert_position(position)
    _legacy_snapshot(
        monkeypatch, positions=[], fetched_at="2026-07-12T05:00:00+00:00",
    )

    from aletheia.portfolio import position_reconciler as reconciler
    monkeypatch.setattr(
        reconciler, "reconcile_close_orphan",
        lambda item: (_ for _ in ()).throw(AssertionError("one view must not close")),
    )
    result = reconciler.run_reconcile("demo")

    assert result["closed_orphans"] == []
    assert len(repo.list_positions(status="open")) == 1
    evidence = result["disappearance"]
    assert evidence["created"] == 1
    assert evidence["confirmed"] == 0
    assert evidence["observations"][0]["confirmation_count"] == 1
    candidate = repo.list_position_disappearance_candidates(status="pending")[0]
    assert candidate["position_id"] == position.id
    assert candidate["broker_id"] == "bitget_demo"
    assert candidate["account_environment"] == "demo"
    assert candidate["account_fingerprint"].startswith("sha256:")
    assert candidate["market_type"] == "usdt_futures"
    assert candidate["direction"] == "long"
    assert json.loads(candidate["identity_key"].removeprefix("json:"))[
        "position_id"
    ] == position.id


def test_same_snapshot_retry_is_idempotent_and_second_view_confirms_without_close(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    repo.insert_position(_position())
    snapshot = _legacy_snapshot(
        monkeypatch, positions=[], fetched_at="2026-07-12T05:00:00+00:00",
    )

    from aletheia.portfolio import position_reconciler as reconciler
    monkeypatch.setattr(
        reconciler, "reconcile_close_orphan",
        lambda item: (_ for _ in ()).throw(AssertionError("T05 never closes")),
    )
    first = reconciler.run_reconcile("demo")
    retry = reconciler.run_reconcile("demo")
    assert retry["disappearance"]["observations"][0]["inserted"] is False
    candidate = repo.list_position_disappearance_candidates(status="pending")[0]
    assert candidate["confirmation_count"] == 1
    assert len(repo.list_position_disappearance_observations(candidate["id"])) == 1

    snapshot["fetched_at"] = "2026-07-12T04:59:00+00:00"
    regressed = reconciler.run_reconcile("demo")
    assert regressed["disappearance"]["observations"][0]["inserted"] is False
    assert repo.list_position_disappearance_candidates(status="pending")[0][
        "confirmation_count"
    ] == 1

    monkeypatch.setattr(reconciler.exchange_sync, "get_unified_cached_state", lambda: {
        "accounts": [{"broker_id": "okx_demo"}],
        "positions": [{
            **_ex("ETHUSDT", "long", 1.0, 3_000.0),
            "broker_id": "okx_demo", "market_type": "usdt_futures",
        }],
        "age_seconds": 1.0,
        "fetched_at": "2026-07-12T05:06:00+00:00",
    })
    different_account_present = reconciler.run_reconcile("demo")
    assert different_account_present["disappearance"]["resolved"] == []
    assert repo.get_active_position_disappearance_candidate(candidate["identity_key"])

    snapshot["fetched_at"] = "2026-07-12T05:02:00+00:00"
    second = reconciler.run_reconcile("demo")
    candidate = repo.list_position_disappearance_candidates(status="pending")[0]
    assert candidate["confirmation_count"] == 2
    assert second["disappearance"]["confirmed"] == 1
    assert second["closed_orphans"] == []
    assert len(repo.list_positions(status="open")) == 1


def test_stale_or_wrong_account_view_never_advances_candidate(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    repo.insert_position(_position())
    snapshot = _legacy_snapshot(
        monkeypatch, positions=[], fetched_at="2026-07-12T05:00:00+00:00",
    )
    from aletheia.portfolio import position_reconciler as reconciler
    reconciler.run_reconcile("demo")
    candidate = repo.list_position_disappearance_candidates(status="pending")[0]
    assert candidate["confirmation_count"] == 1

    snapshot["fetched_at"] = "2026-07-12T05:02:00+00:00"
    snapshot["age_seconds"] = 601.0
    stale = reconciler.run_reconcile("demo")
    candidate = repo.list_position_disappearance_candidates(status="pending")[0]
    assert candidate["confirmation_count"] == 1
    assert stale["disappearance"]["observations"] == []

    # An OKX local compared with a fresh Bitget-only unified view is incomplete,
    # not a second absence observation.
    repo.update_position("position-under-test", execution_mode="okx_demo")
    import aletheia.config.loader as loader
    cfg = loader.load_config()
    cfg.exchange.paper_execution_mode = "okx_demo"
    loader.save_config(cfg)
    monkeypatch.setattr(reconciler.exchange_sync, "get_unified_cached_state", lambda: {
        "accounts": [{"broker_id": "bitget_demo"}],
        "positions": [],
        "age_seconds": 1.0,
        "fetched_at": "2026-07-12T05:04:00+00:00",
    })
    wrong = reconciler.run_reconcile("demo")
    assert wrong["disappearance"]["observations"] == []
    assert repo.list_position_disappearance_candidates(status="pending")[0][
        "confirmation_count"
    ] == 1


def test_exchange_reappearance_resolves_but_degraded_fallback_does_not(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    repo.insert_position(_position())
    snapshot = _legacy_snapshot(
        monkeypatch, positions=[], fetched_at="2026-07-12T05:00:00+00:00",
    )
    from aletheia.portfolio import position_reconciler as reconciler
    reconciler.run_reconcile("demo")
    candidate = repo.list_position_disappearance_candidates(status="pending")[0]

    monkeypatch.setattr(reconciler.exchange_sync, "get_cached_state", lambda mode=None: None)
    degraded = reconciler.run_reconcile("demo")
    assert degraded["disappearance"]["resolved"] == []
    assert repo.get_active_position_disappearance_candidate(candidate["identity_key"])

    snapshot["positions"] = [_ex("ETHUSDT", "long", 1.0, 3_000.0)]
    snapshot["fetched_at"] = "2026-07-12T05:04:00+00:00"
    monkeypatch.setattr(
        reconciler.exchange_sync, "get_cached_state", lambda mode=None: snapshot,
    )
    reappeared = reconciler.run_reconcile("demo")
    assert reappeared["disappearance"]["resolved"] == [candidate["id"]]
    stored = repo.list_position_disappearance_candidates(limit=5)[0]
    assert stored["status"] == "reappeared"
    assert stored["resolved_at"]
    audit = repo.list_audit_events(limit=10)
    assert any(
        row["event_type"] == "position_disappearance_resolved"
        and row["entity_id"] == candidate["id"]
        for row in audit
    )


def test_candidate_and_observations_survive_database_reconnection(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    repo.insert_position(_position())
    _legacy_snapshot(
        monkeypatch, positions=[], fetched_at="2026-07-12T05:00:00+00:00",
    )
    from aletheia.portfolio import position_reconciler as reconciler
    reconciler.run_reconcile("demo")
    candidate = repo.list_position_disappearance_candidates(status="pending")[0]

    from aletheia.storage.sqlite import get_db, reset_db
    # setup_env injects a legacy default-isolation connection; production uses
    # autocommit. Commit explicitly before simulating process reconnection.
    get_db().commit()
    reset_db()
    import aletheia.portfolio.disappearance_confirmation as confirmation
    confirmation = importlib.reload(confirmation)
    restored = repo.get_active_position_disappearance_candidate(candidate["identity_key"])
    assert restored["id"] == candidate["id"]
    assert confirmation.disappearance_ready(restored) is False
    assert len(repo.list_position_disappearance_observations(candidate["id"])) == 1


def test_future_direct_absent_evidence_can_mark_candidate_ready(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    position = _position()
    repo.insert_position(position)
    from aletheia.portfolio.disappearance_confirmation import (
        disappearance_ready,
        record_disappearance_observation,
    )
    candidate, inserted = record_disappearance_observation(
        position.model_dump(),
        {
            "view_complete": True,
            "view_identity": "bitget_demo:demo:direct-query-1",
            "broker_id": "bitget_demo",
            "account_environment": "demo",
            "account_fingerprint": "sha256:logical-account",
            "fetched_at": "2026-07-12T05:00:00+00:00",
        },
        direct_query_state="confirmed_absent",
    )
    assert inserted is True
    assert candidate["confirmation_count"] == 1
    assert candidate["direct_query_state"] == "confirmed_absent"
    assert disappearance_ready(candidate) is True


def test_live_disappearance_records_evidence_but_remains_alert_only(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    import aletheia.config.loader as loader
    cfg = loader.load_config()
    cfg.exchange.mode = "live"
    cfg.exchange.name = "bitget"
    loader.save_config(cfg)
    repo.insert_position(_position(execution_mode="bitget_live_limited"))
    _legacy_snapshot(
        monkeypatch, positions=[], fetched_at="2026-07-12T05:00:00+00:00",
    )
    from aletheia.portfolio import position_reconciler as reconciler
    result = reconciler.run_reconcile("live")
    assert result["closed_orphans"] == []
    assert result["adopted"] == []
    assert result["disappearance"]["created"] == 1
    assert repo.list_positions(status="open")[0]["status"] == "open"
