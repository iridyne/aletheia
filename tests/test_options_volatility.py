"""Frozen public-chain fixtures: research semantics, acquisition and GEX isolation."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import math
from statistics import NormalDist

import pytest

from aletheia.data.options_volatility import derive_options_volatility

NOW = datetime(2026, 9, 11, 8, tzinfo=timezone.utc)


def chain(currency="BTC", days=(7, 30, 60)):
    forward = 100_000 if currency == "BTC" else 4_000
    result = []
    for day in days:
        expiry = (NOW + timedelta(days=day)).strftime("%d%b%y").upper()
        for side in ("C", "P"):
            iv = 60 if side == "C" else 70
            sigma, t = iv/100, day/365.25
            strikes = [float(forward)]
            for delta in (0.20, 0.30):
                d1 = NormalDist().inv_cdf(delta if side == "C" else 1-delta)
                strikes.append(round(forward*math.exp(sigma*sigma*t/2-d1*sigma*math.sqrt(t)), 5))
            for strike in strikes:
                result.append({"instrument_name": f"{currency}-{expiry}-{strike}-{side}",
                               "mark_iv": iv, "underlying_price": forward,
                               "underlying_index": f"{currency}-{expiry}",
                               "creation_timestamp": NOW.timestamp()*1000,
                               "open_interest": 10})
    return result


@pytest.mark.parametrize("currency", ["BTC", "ETH"])
def test_normal_chain(currency):
    result = derive_options_volatility(currency, chain(currency), NOW.isoformat())
    assert [result.atm_iv_7d, result.atm_iv_30d, result.atm_iv_60d] == [65]*3
    assert result.call_25d_iv_30d == pytest.approx(60)
    assert result.put_25d_iv_30d == pytest.approx(70)
    assert result.risk_reversal_25d_30d == pytest.approx(-10)
    assert result.skew_25d_30d == pytest.approx(10)
    assert result.term_slope_7d_30d == result.term_slope_30d_60d == 0
    assert result.volatility_regime == "flat"
    assert result.tenors["30"]["state"] == "exact"
    assert result.tenors["30"]["call_25d"]["state"] == "interpolated"
    assert result.authority == "observation_only"
    assert all(row["id"] and row["source_time"] and row["instrument"] for row in result.inputs)


@pytest.mark.parametrize("days,available", [((9,), True), ((10,), False), ((23,), True), ((22,), False)])
def test_tenor_boundary(days, available):
    result = derive_options_volatility("BTC", chain(days=days), NOW.isoformat())
    target = "7" if days[0] < 15 else "30"
    assert (result.tenors[target]["atm"]["value"] is not None) is available
    assert result.tenors[target]["state"] == ("nearest" if available else "unavailable")


@pytest.mark.parametrize("side", ["C", "P"])
def test_missing_side(side):
    items = [r for r in chain() if not r["instrument_name"].endswith(side)]
    result = derive_options_volatility("BTC", items, NOW.isoformat())
    assert result.atm_iv_30d is None
    assert result.risk_reversal_25d_30d is None
    assert result.skew_25d_30d is None
    assert result.volatility_regime == "unavailable"


@pytest.mark.parametrize("field,value", [
    ("mark_iv", None), ("mark_iv", 0), ("mark_iv", float("nan")),
    ("mark_iv", float("inf")), ("underlying_price", float("inf")),
    ("underlying_index", "index_price"), ("creation_timestamp", None),
    ("creation_timestamp", (NOW.timestamp()-121)*1000),
    ("creation_timestamp", (NOW.timestamp()+1)*1000),
])
def test_invalid_source_is_unavailable(field, value):
    items = [{**r, field: value} for r in chain()]
    result = derive_options_volatility("BTC", items, NOW.isoformat())
    assert result.atm_iv_30d is None
    assert result.call_25d_iv_30d is None
    assert result.coverage["valid"] == 0
    assert result.coverage["rejected"]


def test_expired_and_nonfinite_strike():
    items = chain(days=(7,))
    for row in items:
        parts = row["instrument_name"].split("-")
        parts[2] = "inf"
        row["instrument_name"] = "-".join(parts)
    result = derive_options_volatility("BTC", items, NOW.isoformat())
    assert result.atm_iv_7d is None
    expired = derive_options_volatility("BTC", chain(), (NOW+timedelta(days=61)).isoformat())
    assert expired.coverage["rejected"]["expired_instrument"] == len(chain())


def test_sparse_delta_and_duplicate_input():
    items = [r for r in chain() if "-100000.0-" in r["instrument_name"]]
    result = derive_options_volatility("BTC", items, NOW.isoformat())
    assert result.atm_iv_30d == 65
    assert result.call_25d_iv_30d is None
    duplicate = derive_options_volatility("BTC", items+items, NOW.isoformat())
    assert duplicate.atm_iv_30d is None


def test_delta_interpolation_boundary():
    from aletheia.data.options_volatility import _delta_point

    def rows(a, b):
        return [{"delta": d, "side": "C", "iv": 60, "id": str(d), "instrument": str(d)} for d in (a, b)]
    assert _delta_point(rows(.175, .325), "C")["value"] == 60
    assert _delta_point(rows(.174, .326), "C")["value"] is None
    assert _delta_point(rows(.3, .35), "C")["value"] is None
    assert _delta_point(rows(.25, .35), "C")["state"] == "exact"


def test_frozen_selected_inputs_reproduce_values():
    original = derive_options_volatility("BTC", chain(), NOW.isoformat())
    inputs = [{"instrument_name": r["instrument"], "mark_iv": r["iv"],
               "underlying_price": r["forward"], "underlying_index": r["underlying_index"],
               "creation_timestamp": datetime.fromisoformat(r["source_time"]).timestamp()*1000}
              for r in original.inputs]
    replay = derive_options_volatility("BTC", inputs, original.acquired_at)
    for field in ("atm_iv_7d", "atm_iv_30d", "atm_iv_60d", "call_25d_iv_30d",
                  "put_25d_iv_30d", "risk_reversal_25d_30d", "skew_25d_30d",
                  "term_slope_7d_30d", "term_slope_30d_60d", "volatility_regime"):
        assert getattr(replay, field) == getattr(original, field)
    assert replay.inputs == original.inputs


def test_source_skew_and_forward_disagreement():
    items = chain()
    for row in items:
        if "-18SEP26-" in row["instrument_name"]:
            row["creation_timestamp"] -= 31_000
    result = derive_options_volatility("BTC", items, NOW.isoformat())
    assert result.atm_iv_7d == 65
    assert result.term_slope_7d_30d is None
    assert result.volatility_regime == "unavailable"
    items = chain(days=(30,))
    items[0]["underlying_price"] *= 1.01
    result = derive_options_volatility("BTC", items, NOW.isoformat())
    assert result.atm_iv_30d is None
    assert result.tenors["30"]["reason"] == "source_skew_or_forward_disagreement"


def test_scheduler_persists_and_cli_reads(monkeypatch):
    from typer.testing import CliRunner
    from aletheia.agent_runtime import scheduler
    from aletheia.cli.data_cmd import data_app
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage import repositories as repo

    run_migrations()
    snapshot = derive_options_volatility("BTC", chain(), NOW.isoformat())

    async def bounded(coro, label, timeout, deadline):
        coro.close()
        return {"volatility_snapshots": [snapshot]} if label == "options_structure" else {}

    monkeypatch.setattr(scheduler, "_bounded", bounded)
    monkeypatch.setattr(scheduler, "_get_observed_instruments", lambda cfg: [])
    asyncio.run(scheduler._async_data_collector_context(AletheiaConfig(), tick=1))
    result = CliRunner().invoke(data_app, ["options-volatility", "--symbol", "BTC", "--cutoff", NOW.isoformat()])
    assert result.exit_code == 0, result.output
    assert '"atm_iv_30d": 65.0' in result.output
    assert repo.latest_options_structure("BTC") is None


def test_additive_migration_preserves_existing_table():
    import sqlite3
    from aletheia.storage.migrations import _apply_options_volatility_v85

    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE options_market_structure_snapshots (value TEXT)")
    db.execute("INSERT INTO options_market_structure_snapshots VALUES ('frozen')")
    _apply_options_volatility_v85(db)
    assert db.execute("SELECT * FROM options_market_structure_snapshots").fetchall() == [("frozen",)]
    assert db.execute("SELECT count(*) FROM options_volatility_snapshots").fetchone()[0] == 0
    db.close()


def test_cutoff_storage_and_legacy_isolation():
    from aletheia.storage import repositories as repo
    from aletheia.storage.migrations import run_migrations

    run_migrations()
    snap = derive_options_volatility("BTC", chain(), NOW.isoformat())
    repo.insert_options_volatility_snapshot(snap.model_dump())
    assert repo.latest_options_structure("BTC") is None
    assert not repo.options_volatility_at("BTC", (NOW-timedelta(seconds=1)).isoformat())["available"]
    exported = repo.options_volatility_at("BTC", NOW.isoformat())
    assert exported["atm_iv_30d"] == 65
    assert exported["inputs"] == snap.inputs
    assert repo.options_volatility_at("BTC", (NOW+timedelta(minutes=31)).isoformat())["reason"] == "stale_snapshot"
    empty = derive_options_volatility("BTC", [], (NOW+timedelta(seconds=1)).isoformat())
    repo.insert_options_volatility_snapshot(empty.model_dump())
    assert repo.options_volatility_at("BTC", (NOW+timedelta(seconds=2)).isoformat())["atm_iv_30d"] is None


def test_same_response_gex_unchanged(monkeypatch):
    import aletheia.data.collectors.options_structure as collector

    calls = []

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    class Response:
        def __init__(self, currency):
            self.currency = currency

        def raise_for_status(self):
            pass

        def json(self):
            return {"result": chain(self.currency)}

    class Client:
        async def get(self, url, params):
            calls.append((url, params))
            return Response(params["currency"])

    @asynccontextmanager
    async def client(**kwargs):
        yield Client()

    monkeypatch.setattr(collector, "datetime", FrozenDatetime)
    monkeypatch.setattr(collector, "httpx_client", client)
    legacy = [asyncio.run(collector._collect_currency(c)).model_dump() for c in ("BTC", "ETH")]
    calls.clear()
    enriched = asyncio.run(collector.OptionsStructureCollector().collect())
    assert [s.model_dump() for s in enriched["snapshots"]] == legacy
    assert len(calls) == 2
    assert len(enriched["volatility_snapshots"]) == 2
    assert all(s.atm_iv_30d == 65 for s in enriched["volatility_snapshots"])
