"""#3101 offline source, cutoff, cadence, persistence and isolation checks."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aletheia.data import macro_liquidity as ml
from extensions.data.collectors import macro
from aletheia.storage import repositories as repo

CUTOFF = "2026-09-11T12:00:00+00:00"


@pytest.fixture(autouse=True)
def fresh_process_throttle(monkeypatch):
    monkeypatch.setattr(macro, "_FRED_RESEARCH_ATTEMPTS", {})


def batch(sid, points, acquired=CUTOFF):
    return ml.source_batch(sid, dict(units="lin", observations=[
        dict(date=day, value=str(value), realtime_start="2026-09-11", realtime_end="2026-09-11")
        for day, value in points]), acquired)


def sample():
    values = {"SOFR": (4, 4.1), "DGS2": (4, 4.2), "DGS10": (4.5, 4.6),
              "WALCL": (6700000, 6710000), "RRPONTSYD": (100, 90), "WDTGAL": (800000, 805000)}
    return [batch(sid, [("2026-09-02", old), ("2026-09-09", new)]) for sid, (old, new) in values.items()]


def test_official_metadata_excerpts_and_missing_shape():
    fixture = json.loads((Path(__file__).parent/"fixtures/fred_series_contract.json").read_text())
    assert {r["series_id"] for r in fixture["series"]} == set(ml.SERIES)
    for row in fixture["series"]:
        result = batch(row["series_id"], [(row["date"], row["value"])])
        assert result["unit"] == row["unit"] and result["frequency"] == row["frequency"]
        assert result["seasonal_adjustment"] == fixture["seasonal_adjustment"]
        assert result["observations"][0]["observation_date"] == row["date"]
        assert result["observations"][0]["value"] == float(row["value"])
    missing = fixture["missing_observation"]
    assert batch("SOFR", [(missing["date"], missing["value"])])["availability"] == "unavailable"


def test_rates_curve_and_transparent_liquidity_components():
    result = ml.derive_macro_liquidity(sample(), CUTOFF)
    assert result["coverage"] == 1
    assert result["rates_regime"]["state"] == "tightening"
    assert result["rates_regime"]["change_7d"]["value"] == pytest.approx(10)
    assert result["curve_regime"]["state"] == "normal"
    assert result["curve_regime"]["value"] == pytest.approx(40)
    assert result["curve_regime"]["change_7d"]["value"] == pytest.approx(-10)
    impulse = result["dollar_liquidity_impulse"]
    assert impulse["value"] == 15  # +10 assets -(-10 RRP) -(+5 TGA), billion USD
    assert impulse["components"]["RRPONTSYD"]["value"] == -10
    assert len(impulse["input_ids"]) == 6
    assert result["authority"] == "observation_only" and result["revision_policy"] == ml.REVISION


def test_cutoff_storage_replay_and_old_macro_isolation():
    repo.insert_macro_snapshot(dict(vix_close=22, observed_at=CUTOFF))
    before = repo._select_all("macro_snapshots")
    for b in sample():
        repo.insert_fred_source_batch(b)
    frozen = ml.read_source_batches(CUTOFF)
    result = ml.derive_macro_liquidity(frozen, CUTOFF)
    newer = batch("SOFR", [("2026-09-09", 99)], "2026-09-12T00:00:00+00:00")
    repo.insert_fred_source_batch(newer)
    assert ml.read_source_batches(CUTOFF) == frozen
    assert ml.derive_macro_liquidity(frozen+[newer], CUTOFF) == result
    assert ml.derive_macro_liquidity(result["frozen_batches"], CUTOFF) == result
    assert repo._select_all("macro_snapshots") == before
    assert ml.derive_macro_liquidity(ml.read_source_batches("2026-09-10T00:00:00+00:00"),
        "2026-09-10T00:00:00+00:00")["coverage"] == 0
    return result


def test_refetch_does_not_refresh_old_source_and_revision_has_no_backfill():
    old = batch("SOFR", [("2026-08-01", 5)])
    result = ml.derive_macro_liquidity([old], CUTOFF)
    assert result["source_facts"]["SOFR"]["reason"] == "stale_source"
    revised = batch("SOFR", [("2026-09-02", 9), ("2026-09-09", 3)])
    assert revised["observations"][0]["realtime_start"] == "2026-09-11"
    assert ml.derive_macro_liquidity([revised], "2026-09-10T00:00:00+00:00")["coverage"] == 0


def test_holiday_and_mixed_frequency_asof_do_not_use_latest_daily_rrp():
    data = sample()
    data[0] = batch("SOFR", [("2026-09-04", 4), ("2026-09-07", ".")])
    data[4] = batch("RRPONTSYD", [("2026-09-02", 100), ("2026-09-08", 90),
                               ("2026-09-09", "."), ("2026-09-10", 999)])
    result = ml.derive_macro_liquidity(data, CUTOFF)
    assert result["source_facts"]["SOFR"]["value"] is None  # age 7, not made new by acquisition
    assert result["source_facts"]["RRPONTSYD"]["value"] == 999
    assert result["dollar_liquidity_impulse"]["value"] == 15  # anchor is Wednesday, not Thursday
    holiday = batch("SOFR", [("2026-09-04", 4), ("2026-09-07", ".")], "2026-09-08T12:00:00+00:00")
    assert ml.derive_macro_liquidity([holiday], holiday["acquired_at"])["source_facts"]["SOFR"]["value"] == 4


@pytest.mark.parametrize("sid", ["WALCL", "RRPONTSYD", "WDTGAL"])
def test_missing_component_is_not_zero(sid):
    result = ml.derive_macro_liquidity([b for b in sample() if b["series_id"] != sid], CUTOFF)
    assert result["coverage"] == 5/6
    assert result["dollar_liquidity_impulse"]["value"] is None
    assert result["dollar_liquidity_impulse"]["components"][sid]["value"] is None


def test_delayed_weekly_release_and_yield_date_mismatch_fail_closed():
    data = sample()
    data[5] = batch("WDTGAL", [("2026-09-02", 800000)])
    data[2] = batch("DGS10", [("2026-09-08", 4.6)])
    result = ml.derive_macro_liquidity(data, CUTOFF)
    assert result["dollar_liquidity_impulse"]["value"] is None
    assert result["curve_regime"]["state"] == "unavailable"
    assert ml.derive_macro_liquidity(sample(), "2026-09-21T12:00:00+00:00")["coverage"] == 0


@pytest.mark.parametrize("value,day", [("nan", "2026-09-09"), ("inf", "2026-09-09"),
    ("1e309", "2026-09-09"), ("1e16", "2026-09-09"), ("1", "2026-09-12"), ("1", "bad")])
def test_invalid_and_future_observation_rejected(value, day):
    assert batch("SOFR", [(day, value)])["availability"] == "unavailable"


def test_weekly_dates_duplicates_units_and_missing_history():
    assert batch("WALCL", [("2026-09-10", 10)])["availability"] == "unavailable"
    assert batch("SOFR", [("2026-09-09", 4), ("2026-09-09", 5)])["availability"] == "unavailable"
    assert ml.source_batch("SOFR", dict(units="pc1", observations=[]), CUTOFF)["unavailable_reason"] == "native_units_unproven"
    data = sample()
    data[0]["unit"] = "ratio"
    assert ml.derive_macro_liquidity(data, CUTOFF)["rates_regime"]["state"] == "unavailable"
    single = batch("SOFR", [("2026-09-09", 4)])
    assert ml.derive_macro_liquidity([single], CUTOFF)["rates_regime"]["state"] == "unavailable"


def test_daily_weekly_request_and_storage_budget(monkeypatch):
    now = datetime(2026, 9, 11, 12, tzinfo=timezone.utc)
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    monkeypatch.setattr(macro, "datetime", Frozen)
    calls = []
    @asynccontextmanager
    async def client(**kwargs):
        class Fake:
            async def get(self, url, params):
                calls.append(params)
                days = [now.date()-timedelta(days=i) for i in range(40)]
                if ml.SERIES[params["series_id"]][1] != "Daily":
                    days = [d for d in days if d.weekday() == 2]
                return SimpleNamespace(raise_for_status=lambda: None, json=lambda: dict(units="lin",
                    observations=[dict(date=d.isoformat(), value="4") for d in days[:params["limit"]]]))
        yield Fake()
    monkeypatch.setattr(macro, "httpx_client", client)
    sizes = []
    for day in range(7):
        now = datetime(2026, 9, 11+day, 12, tzinfo=timezone.utc)
        fetched = asyncio.run(macro._fetch_fred_research("fixture"))
        assert len(fetched) == (6 if day == 0 else 4)
        for row in fetched:
            sizes.append(len(json.dumps(row).encode()))
            repo.insert_fred_source_batch(row)
        macro._FRED_RESEARCH_ATTEMPTS.clear()  # restart: durable cadence still holds
        assert asyncio.run(macro._fetch_fred_research("fixture")) == []
    assert len(calls) == 30  # 4 daily * 7 + 2 weekly
    assert all(p["limit"] == (32 if ml.SERIES[p["series_id"]][1] == "Daily" else 4) for p in calls)
    assert max(sizes) < 8192
    assert len(repo._select_all("fred_source_batches", limit=100)) == 30
    print(f"FRED 7d: requests={len(calls)}, rows={len(sizes)}, payload_bytes={sum(sizes)}, max_row_bytes={max(sizes)}")


def test_failed_and_unconfigured_attempts_are_cadence_bounded_and_redacted(monkeypatch):
    calls = []
    @asynccontextmanager
    async def client(**kwargs):
        class Fake:
            async def get(self, *args, **kwargs):
                calls.append(1)
                raise RuntimeError("https://example.invalid/?api_key=DO_NOT_LOG")
        yield Fake()
    monkeypatch.setattr(macro, "httpx_client", client)
    failed = asyncio.run(macro._fetch_fred_research("fixture"))
    assert len(calls) == 6 and all(b["unavailable_reason"] == "request_failed" for b in failed)
    assert "DO_NOT_LOG" not in json.dumps(failed)
    # No writes yet: persistence failure cannot turn into an intraday retry.
    assert asyncio.run(macro._fetch_fred_research("fixture")) == []
    for row in failed:
        repo.insert_fred_source_batch(row)
    macro._FRED_RESEARCH_ATTEMPTS.clear()
    assert asyncio.run(macro._fetch_fred_research("fixture")) == []
    assert len(calls) == 6


def test_no_key_no_network_and_cli_is_read_only(monkeypatch):
    result = asyncio.run(macro._fetch_fred_research(None))
    assert len(result) == 6 and all(b["unavailable_reason"] == "not_configured" for b in result)
    from typer.testing import CliRunner
    from aletheia.cli.data_cmd import data_app
    for row in sample():
        repo.insert_fred_source_batch(row)
    before = repo._select_all("fred_source_batches")
    exported = CliRunner().invoke(data_app, ["macro-liquidity", "--cutoff", CUTOFF])
    assert exported.exit_code == 0, exported.output
    assert json.loads(exported.output)["dollar_liquidity_impulse"]["value"] == 15
    assert repo._select_all("fred_source_batches") == before
    assert CliRunner().invoke(data_app, ["macro-liquidity", "--cutoff", "2026-09-11"]).exit_code != 0


def test_collector_research_failure_does_not_change_existing_snapshot(monkeypatch):
    async def cg(): return 50, 1e12, 1
    async def tradfi(_): return tuple((20, dict(source="fred", precision="date",
        source_observed_at=datetime.now(timezone.utc).date().isoformat(), acquired_at=CUTOFF)) for _ in range(4))
    async def research(_): return []
    monkeypatch.setattr(macro, "_fetch_coingecko", cg)
    monkeypatch.setattr(macro, "_fetch_fred", tradfi)
    monkeypatch.setattr(macro, "_fetch_fred_research", research)
    monkeypatch.setattr("aletheia.config.secrets.get_secret", lambda _: "fixture")
    monkeypatch.setattr("aletheia.config.loader.load_config", lambda: SimpleNamespace(market_data=SimpleNamespace(enable_fear_greed=False)))
    before = asyncio.run(macro.MacroCollector().collect([]))["snapshot"].model_dump()
    async def failed(_): raise RuntimeError("DO_NOT_LOG")
    monkeypatch.setattr(macro, "_fetch_fred_research", failed)
    after = asyncio.run(macro.MacroCollector().collect([]))
    after_snapshot = after["snapshot"].model_dump()
    before.pop("observed_at"); after_snapshot.pop("observed_at")
    assert before == after_snapshot
    assert after["errors"] == ["fred_research:failed"]


def test_storage_retention_and_shape_bound():
    repo.insert_fred_source_batch(batch("SOFR", [("2020-01-01", 4)], "2020-01-02T00:00:00+00:00"))
    assert repo.prune_fred_source_batches() == 1
    bad = deepcopy(sample()[0])
    bad["observations"] *= 17
    with pytest.raises(ValueError, match="invalid_fred_source_batch"):
        repo.insert_fred_source_batch(bad)


def test_scheduler_persists_research_without_legacy_snapshot(monkeypatch):
    from aletheia.agent_runtime import scheduler
    from aletheia.config.schema import AletheiaConfig
    async def bounded(coro, label, timeout, deadline):
        coro.close()
        return dict(snapshot=None, fred_source_batches=sample(), errors=[]) if label == "macro" else {}
    monkeypatch.setattr(scheduler, "_bounded", bounded)
    monkeypatch.setattr(scheduler, "_get_observed_instruments", lambda _: [])
    asyncio.run(scheduler._async_data_collector_context(AletheiaConfig(), tick=1))
    assert len(ml.read_source_batches(CUTOFF)) == 6
    assert repo._select_all("macro_snapshots") == []


def test_missing_storage_does_not_trigger_requests(monkeypatch):
    from contextlib import contextmanager
    @contextmanager
    def absent(**kwargs):
        yield None
    monkeypatch.setattr("aletheia.storage.sqlite.readonly_db", absent)
    assert ml.read_source_batches(CUTOFF) == []
    with pytest.raises(ValueError, match="fred_storage_unavailable"):
        asyncio.run(macro._fetch_fred_research("fixture"))


def test_writer_rejects_forged_proof_and_nonfinite():
    for key, value in (("source", "other"), ("refresh_bucket", "2099-01-01"),
                       ("seasonal_adjustment", "Seasonally Adjusted")):
        forged = {**sample()[0], key: value}
        with pytest.raises(ValueError):
            repo.insert_fred_source_batch(forged)
    forged = sample()[0]
    forged["observations"][0]["value"] = float("inf")
    with pytest.raises(ValueError):
        repo.insert_fred_source_batch(forged)
