"""#1599: a venue clock ahead of ours must never start a Position's holding clock."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import aletheia.portfolio.position_manager as pm
from aletheia.storage import repositories as repo
from tests.test_position_reconciler import _ex, current_account, setup_env


def _opened_at_after_adopting(tmp_path, monkeypatch, ctime: datetime) -> datetime:
    setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio.position_reconciler import adopt_position

    row = {**_ex("DOGEUSDT", "long", 204, entry=0.08689), "cTime": str(int(ctime.timestamp() * 1000))}
    adopted = adopt_position(row, "demo")
    position = repo._select_one("positions", adopted["position_id"])
    assert position is not None
    return datetime.fromisoformat(position["opened_at"])


def test_adoption_refuses_a_future_exchange_open_time(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    opened = _opened_at_after_adopting(tmp_path, monkeypatch, now + timedelta(days=1))
    assert opened <= datetime.now(timezone.utc)
    assert abs((opened - now).total_seconds()) < 60


def test_adoption_keeps_a_past_exchange_open_time(tmp_path, monkeypatch):
    past = datetime.now(timezone.utc) - timedelta(hours=30)
    opened = _opened_at_after_adopting(tmp_path, monkeypatch, past)
    assert abs((opened - past).total_seconds()) < 1


def _fill_report(executed_at: datetime):
    from aletheia.uta.compat import instrument_from_local
    from aletheia.uta.models import FillReport

    return FillReport(
        identity=current_account(), instrument=instrument_from_local("DOGE/USDT", "usdt_futures"),
        client_order_id="cli-1", venue_order_id="ex-1", venue_fill_id="t-1", side="buy",
        quantity=Decimal("204"), price=Decimal("0.08689"), fee=Decimal("0.01"), fee_currency="USDT",
        executed_at=executed_at, received_at=datetime.now(timezone.utc),
    )


def test_future_private_fill_is_invalid_evidence():
    from aletheia.execution.exchange_materialization import (
        PrivateFillEvidenceInvalid, _private_fill_from_report,
    )

    now = datetime.now(timezone.utc)
    with pytest.raises(PrivateFillEvidenceInvalid, match="future"):
        _private_fill_from_report(_fill_report(now + timedelta(days=1)))
    # Inside the declared clock-skew allowance the fill is still admitted.
    assert _private_fill_from_report(_fill_report(now + timedelta(seconds=30))).fill_id == "t-1"


def test_corrupt_future_opened_at_never_reads_as_negative_age(caplog):
    now = datetime.now(timezone.utc)
    future = {"id": "p-future", "opened_at": (now + timedelta(days=1)).isoformat()}
    with caplog.at_level("WARNING", logger="aletheia.position_manager"):
        assert pm._position_age_hours(future, now) == 0.0
    assert "p-future" in caplog.text
    past = {"id": "p-past", "opened_at": (now - timedelta(hours=73)).isoformat()}
    assert pm._position_age_hours(past, now) == pytest.approx(73.0)
    assert pm._position_age_hours({"id": "p-bad", "opened_at": None}, now) is None
