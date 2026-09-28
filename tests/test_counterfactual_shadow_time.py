"""Shadow cohort readers compare persisted timestamps by UTC instant."""

from __future__ import annotations

from unittest.mock import patch

import pytest


def _candidate(opportunity_id: str) -> dict:
    return {
        "opportunity_id": opportunity_id,
        "symbol": "BTC/USDT",
        "venue": "bitget",
        "market_type": "spot",
        "direction": "long",
        "scanner_family": "momentum",
        "trade_score": 60.0,
    }


def _persist_shadow(opportunity_id: str, *, epoch: str | None = "epoch-test") -> str:
    from extensions.counterfactual.trades import persist_shadow_batch

    candidate = _candidate(opportunity_id)
    with (
        patch("extensions.counterfactual.trades._current_price", return_value=100.0),
        patch("extensions.counterfactual.trades._current_policy_epoch", return_value=epoch),
    ):
        return persist_shadow_batch([candidate], candidate, top_n=1)[0]


def _set_created_at(shadow_id: str, created_at: str) -> None:
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute("UPDATE shadow_trades SET created_at=? WHERE id=?", (created_at, shadow_id))
    db.commit()


def _settle_shadow(shadow_id: str) -> None:
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db
    from tests.helpers.shadow_outcome_fixture import insert_canonical_outcome_for_shadow

    db = get_db()
    key = db.execute(
        "SELECT outcome_candidate_key FROM shadow_trades WHERE id=?", (shadow_id,)
    ).fetchone()["outcome_candidate_key"]
    insert_canonical_outcome_for_shadow(
        shadow_id,
        horizon="24h",
        net_return_pct=1.0,
    )
    assert repo.settle_linked_shadow_outcomes(key) >= 1


def test_rolling_window_uses_utc_instants_for_space_and_timestamps():
    from extensions.counterfactual.trades import list_deduped_shadows
    from aletheia.storage.sqlite import get_db

    before_id = _persist_shadow("rolling-before")
    after_id = _persist_shadow("rolling-after")
    _set_created_at(before_id, "2026-08-21T18:59:59+00:00")
    _set_created_at(after_id, "2026-08-21 19:00:00.000001+00:00")
    _settle_shadow(before_id)
    _settle_shadow(after_id)

    db = get_db()
    db.create_function("datetime", -1, lambda *_args: "2026-08-21 19:00:00")

    rows = list_deduped_shadows(days=0, columns=("id", "created_at"))
    assert rows == [{"id": after_id, "created_at": "2026-08-21 19:00:00.000001+00:00"}]


def test_absolute_window_is_half_open_and_normalizes_offsets():
    from extensions.counterfactual.trades import list_deduped_shadows

    lower_space_id = _persist_shadow("absolute-lower-space")
    lower_offset_id = _persist_shadow("absolute-lower-offset")
    inside_id = _persist_shadow("absolute-inside")
    upper_id = _persist_shadow("absolute-upper")
    _set_created_at(lower_space_id, "2026-08-21 00:00:00+00:00")
    _set_created_at(lower_offset_id, "2026-08-21 08:00:00+08:00")
    _set_created_at(inside_id, "2026-08-21T03:00:00+00:00")
    _set_created_at(upper_id, "2026-08-21T04:00:00+00:00")
    for shadow_id in (lower_space_id, lower_offset_id, inside_id, upper_id):
        _settle_shadow(shadow_id)

    rows = list_deduped_shadows(
        since_iso="2026-08-21T00:00:00+00:00",
        until_iso="2026-08-21T04:00:00+00:00",
        columns=("id",),
    )
    assert {row["id"] for row in rows} == {
        lower_space_id,
        lower_offset_id,
        inside_id,
    }
    assert upper_id not in {row["id"] for row in rows}

    with pytest.raises(ValueError, match="invalid since_iso timestamp"):
        list_deduped_shadows(since_iso="not-a-timestamp")
    with pytest.raises(ValueError, match="invalid until_iso timestamp"):
        list_deduped_shadows(
            since_iso="2026-08-21T00:00:00+00:00",
            until_iso="not-a-timestamp",
        )


def test_epoch_detector_and_reader_share_the_same_temporal_population():
    from extensions.counterfactual.trades import _detect_window_epochs, list_deduped_shadows

    inside_id = _persist_shadow("epoch-inside", epoch="epoch-inside")
    outside_id = _persist_shadow("epoch-outside", epoch="epoch-outside")
    _set_created_at(inside_id, "2026-08-21T02:00:00+00:00")
    _set_created_at(outside_id, "2026-08-21 05:00:00+00:00")
    _settle_shadow(inside_id)
    _settle_shadow(outside_id)

    since_iso = "2026-08-21T01:00:00+00:00"
    until_iso = "2026-08-21T04:00:00+00:00"
    rows = list_deduped_shadows(
        since_iso=since_iso,
        until_iso=until_iso,
        columns=("id", "policy_epoch_id"),
    )
    assert rows == [{"id": inside_id, "policy_epoch_id": "epoch-inside"}]
    assert _detect_window_epochs(since_iso=since_iso, until_iso=until_iso) == (
        "epoch-inside",
        "single",
    )


def test_deduplication_orders_mixed_encodings_by_fractional_utc_instant():
    from extensions.counterfactual.trades import list_deduped_shadows

    older_id = _persist_shadow("dedup-mixed")
    newer_id = _persist_shadow("dedup-mixed")
    _set_created_at(older_id, "2026-08-21T10:00:00.900000+00:00")
    _set_created_at(newer_id, "2026-08-21 10:00:01.100000+00:00")
    _settle_shadow(older_id)

    rows = list_deduped_shadows(
        since_iso="2026-08-21T09:00:00+00:00",
        until_iso="2026-08-21T11:00:00+00:00",
        columns=("id", "created_at"),
    )
    assert rows == [
        {"id": newer_id, "created_at": "2026-08-21 10:00:01.100000+00:00"}
    ]


def test_malformed_timestamps_fail_closed_are_counted_and_do_not_mix_epochs(caplog):
    from extensions.counterfactual.trades import (
        _detect_window_epochs,
        _shadow_time_window_sql,
        list_deduped_shadows,
    )
    from aletheia.storage.sqlite import get_db, iso_instant_us, register_instant_us

    valid_id = _persist_shadow("malformed-valid", epoch="epoch-good")
    malformed_id = _persist_shadow("malformed-row", epoch="epoch-bad")
    _set_created_at(valid_id, "2026-08-21T12:00:00+00:00")
    _set_created_at(malformed_id, "2026-08-21T12:01:00+00:00")
    _settle_shadow(valid_id)
    _settle_shadow(malformed_id)
    _set_created_at(malformed_id, "not-a-timestamp")

    since_iso = "2026-08-21T11:00:00+00:00"
    until_iso = "2026-08-21T13:00:00+00:00"
    with caplog.at_level("WARNING"):
        rows = list_deduped_shadows(
            since_iso=since_iso,
            until_iso=until_iso,
            columns=("id",),
        )
    assert rows == [{"id": valid_id}]
    assert [record.message for record in caplog.records] == [
        "shadow_cohort_invalid_created_at_excluded count=1"
    ]

    caplog.clear()
    with caplog.at_level("WARNING"):
        assert _detect_window_epochs(
            since_iso=since_iso,
            until_iso=until_iso,
        ) == ("epoch-good", "single")
    assert [record.message for record in caplog.records] == [
        "shadow_cohort_invalid_created_at_excluded count=1"
    ]

    assert iso_instant_us(None) is None
    db = get_db()
    register_instant_us(db)
    time_where, time_params = _shadow_time_window_sql(
        days=None,
        since_iso=since_iso,
        until_iso=until_iso,
    )
    rows = db.execute(
        "WITH sample(created_at) AS (VALUES (NULL), (?)) "
        "SELECT created_at FROM sample "
        f"WHERE aletheia_instant_us(created_at) IS NOT NULL AND {time_where}",
        ("2026-08-21T12:00:00+00:00", *time_params),
    ).fetchall()
    assert [row["created_at"] for row in rows] == ["2026-08-21T12:00:00+00:00"]
