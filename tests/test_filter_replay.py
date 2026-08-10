from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _record_ttl_decision(
    *,
    key: str,
    decision: str,
    now: datetime,
    lateness_sec: int = 0,
    net_1h: float,
    filter_version_params: dict | None = None,
) -> None:
    from aletheia.analytics.filter_evidence import record_filter_decision
    from aletheia.storage.sqlite import get_db

    ttl_sec = int((filter_version_params or {}).get("ttl_sec", 1800))
    expires_at = now - timedelta(seconds=lateness_sec)
    if decision == "passed":
        expires_at = now + timedelta(minutes=10)
    record_filter_decision(
        candidate_key=key,
        candidate_kind="opportunity",
        filter_name="family_ttl",
        stage="paper_ranker",
        decision=decision,
        reason_code="universe_member_fresh" if decision == "passed" else "universe_member_expired",
        symbol="BTC/USDT",
        venue="okx",
        market_type="perp",
        direction="long",
        scanner_family="momentum",
        entry_price_usd=100.0,
        inputs={"expires_at": expires_at.isoformat(), "now": now.isoformat()},
        parameters={"ttl_sec": ttl_sec},
        evaluated_at=now.isoformat(),
    )
    db = get_db()
    db.execute(
        "UPDATE filter_candidate_outcomes SET status='settled', net_return_pct=? "
        "WHERE outcome_candidate_key=? AND horizon='1h'",
        (net_1h, key),
    )


def _record_confidence_decision(
    *, key: str, decision: str, confidence: str, net_4h: float, now: datetime
) -> None:
    from aletheia.analytics.filter_evidence import record_filter_decision
    from aletheia.storage.sqlite import get_db

    record_filter_decision(
        candidate_key=key,
        candidate_kind="opportunity",
        filter_name="ranker_confidence_gate",
        stage="paper_ranker",
        decision=decision,
        reason_code="confidence_at_or_above" if decision == "passed" else "confidence_below",
        symbol="BTC/USDT",
        venue="okx",
        market_type="perp",
        direction="long",
        scanner_family="mean_reversion",
        entry_price_usd=100.0,
        inputs={"confidence": confidence},
        parameters={"minimum": "medium"},
        evaluated_at=now.isoformat(),
    )
    get_db().execute(
        "UPDATE filter_candidate_outcomes SET status='settled', net_return_pct=? "
        "WHERE outcome_candidate_key=? AND horizon='4h'",
        (net_4h, key),
    )


def test_family_ttl_replay_is_fixed_version_incremental_and_bounded():
    from aletheia.counterfactual.filter_replay import build_family_ttl_replay
    from aletheia.storage.sqlite import get_db

    now = datetime.now(timezone.utc)
    _record_ttl_decision(
        key="opportunity:pass", decision="passed", now=now,
        net_1h=-0.5,
    )
    _record_ttl_decision(
        key="opportunity:near", decision="rejected", now=now,
        lateness_sec=600, net_1h=1.0,
    )
    _record_ttl_decision(
        key="opportunity:far", decision="rejected", now=now,
        lateness_sec=1500, net_1h=-0.2,
    )

    version = get_db().execute(
        "SELECT filter_version FROM filter_decisions WHERE candidate_key='opportunity:pass'"
    ).fetchone()[0]
    report = build_family_ttl_replay(
        filter_version=version,
        stage="paper_ranker",
        scanner_family="momentum",
        direction="long",
        market_type="perp",
        horizon="1h",
        current_ttl_sec=1800,
        alternative_ttls=[2700, 3600, 7200, 14400, 28800],
        evaluated_since=(now - timedelta(days=1)).isoformat(),
        min_samples=2,
        max_candidates=2,
    )

    assert report["read_only"] is True
    assert report["gate_only"] is True
    assert report["filter_version"] == version
    assert report["candidate_cap"] == 2
    assert len(report["alternatives"]) == 4
    assert report["alternatives"][0]["ttl_sec"] == 2700
    assert report["alternatives"][0]["newly_admitted_candidates"] == 1
    assert report["alternatives"][1]["ttl_sec"] == 3600
    assert report["alternatives"][1]["newly_admitted_candidates"] == 2
    assert report["alternatives"][0]["readiness"] == "insufficient_data"
    assert report["alternatives"][1]["settled_n"] == 2
    assert report["alternatives"][1]["readiness"] == "no_support"


def test_family_ttl_replay_uses_first_exposure_and_rejects_invalid_scope():
    import pytest

    from aletheia.analytics.filter_evidence import record_filter_decision
    from aletheia.counterfactual.filter_replay import build_family_ttl_replay
    from aletheia.storage.sqlite import get_db

    now = datetime.now(timezone.utc)
    _record_ttl_decision(
        key="opportunity:repeat", decision="passed", now=now - timedelta(minutes=20),
        net_1h=0.4,
    )
    record_filter_decision(
        candidate_key="opportunity:repeat",
        candidate_kind="opportunity",
        filter_name="family_ttl",
        stage="paper_ranker",
        decision="rejected",
        reason_code="universe_member_expired",
        symbol="BTC/USDT",
        venue="okx",
        market_type="perp",
        direction="long",
        scanner_family="momentum",
        entry_price_usd=100.0,
        inputs={
            "expires_at": (now - timedelta(minutes=1)).isoformat(),
            "now": now.isoformat(),
        },
        parameters={"ttl_sec": 1800},
        evaluated_at=now.isoformat(),
    )
    version = get_db().execute(
        "SELECT filter_version FROM filter_decisions WHERE candidate_key='opportunity:repeat' "
        "ORDER BY evaluated_at LIMIT 1"
    ).fetchone()[0]

    report = build_family_ttl_replay(
        filter_version=version,
        stage="paper_ranker",
        scanner_family="momentum",
        direction="long",
        market_type="perp",
        horizon="1h",
        current_ttl_sec=1800,
        alternative_ttls=[3600],
    )

    assert report["current_pass"]["candidate_count"] == 1
    assert report["rejected_candidates"] == 0
    assert report["alternatives"][0]["newly_admitted_candidates"] == 0
    with pytest.raises(ValueError, match="filter_version"):
        build_family_ttl_replay(
            filter_version="",
            stage="paper_ranker",
            scanner_family="momentum",
            direction="long",
            market_type="perp",
            horizon="1h",
            current_ttl_sec=1800,
        )


def test_ranker_confidence_replay_uses_existing_controller_path():
    from aletheia.counterfactual.filter_replay import (
        build_bounded_filter_replays,
        build_ranker_confidence_replay,
    )
    from aletheia.storage.sqlite import get_db

    now = datetime.now(timezone.utc)
    _record_confidence_decision(
        key="opportunity:pass", decision="passed", confidence="medium",
        net_4h=0.0, now=now,
    )
    for suffix in ("a", "b"):
        _record_confidence_decision(
            key=f"opportunity:reject-{suffix}", decision="rejected",
            confidence="speculative", net_4h=2.0, now=now,
        )
    version = get_db().execute(
        "SELECT filter_version FROM filter_decisions "
        "WHERE filter_name='ranker_confidence_gate' LIMIT 1"
    ).fetchone()[0]
    scope = {
        "filter_version": version,
        "stage": "paper_ranker",
        "scanner_family": "mean_reversion",
        "direction": "long",
        "market_type": "perp",
        "horizon": "4h",
    }

    report = build_ranker_confidence_replay(**scope, min_samples=2)
    bounded = build_bounded_filter_replays([
        {"filter_name": "ranker_confidence_gate", "verdict": "overrestrictive_suspect", **scope}
    ], min_samples=2)

    assert report["current_minimum_confidence"] == "medium"
    assert report["alternatives"] == [
        {
            "minimum_confidence": "speculative",
            "newly_admitted_candidates": 2,
            "candidate_count": 2,
            "settled_n": 2,
            "outcome_coverage": 1.0,
            "raw_avg_net_pct": 2.0,
            "median_net_pct": 2.0,
            "shrunk_avg_net_pct": 0.181818,
            "delta_vs_current_pass_pct": 0.181818,
            "readiness": "ready_for_controlled_experiment",
        }
    ]
    assert bounded[0]["filter_name"] == "ranker_confidence_gate"
