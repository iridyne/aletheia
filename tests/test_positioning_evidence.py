"""Frozen canonical facts and existing SQLite writes, with no live providers."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from aletheia.data.derivatives_facts import project_snapshot, project_price_snapshot, price_snapshot
from extensions.data.positioning_evidence import derive_positioning_evidence, read_positioning_evidence
from tests.test_derivatives_readiness import store, NOW
from aletheia.storage import repositories as repo

VENUES = ("binance", "okx", "bitget")


def frozen():
    facts = []
    for venue, oi, interval in (("binance", 100, 3600), ("okx", 200, 28800)):
        for age in (60, 0):
            instrument = store(venue, age=age, oi=oi/2 if age else oi, interval=interval)
            acquired = NOW-timedelta(minutes=age)
            for family, table in (("funding", "funding_rate_snapshots"), ("open_interest", "open_interest_snapshots")):
                row = repo._select_all(table, where="exchange=?", params=(venue,), order="rowid DESC", limit=1)[0]
                facts.extend(project_snapshot(row, family, now=acquired))
            price = price_snapshot("BTC/USDT", venue, {
                "markPrice": 100 if age else 110, "indexPrice": 100,
                "timestamp": int(acquired.timestamp()*1000),
            }, instrument=instrument, acquired_at=acquired)
            repo.insert_perp_price_snapshot(price)
            facts.extend(project_price_snapshot(price, now=acquired))
    return facts


def derive(facts):
    return derive_positioning_evidence("BTC/USDT", facts, NOW.isoformat(), venues=VENUES)


def test_normal_funding_basis_oi_and_missing_venue():
    result = derive(frozen())
    group = result["comparisons"][0]
    assert group["oi_usd"]["subtotal"]["value"] == 300
    assert group["oi_usd"]["shares"] == pytest.approx({"binance": 1/3, "okx": 2/3})
    assert group["oi_usd"]["coverage"] == pytest.approx(2/3)
    assert group["oi_usd"]["disagreement_ratio"]["value"] == pytest.approx(100/150)
    assert group["funding_annualized"]["values_by_venue"] == pytest.approx({"binance": .0008*8760, "okx": .0008*1095})
    assert group["basis_bps"]["median"]["value"] == pytest.approx(1000)
    assert group["basis_bps"]["dispersion"]["value"] == 0
    assert result["exact_venues"]["binance"]["price_oi_state"]["state"] == "price_up_oi_up"
    assert result["exact_venues"]["bitget"]["oi_usd"]["value"] is None
    assert result["authority"] == "observation_only"


@pytest.mark.parametrize("mutation", ["stale", "skew", "unit", "aggregate", "source_future", "source_missing", "interval"])
def test_invalid_or_unaligned_venue(mutation):
    facts = frozen()
    for f in facts:
        if f["venue"] != "okx":
            continue
        if mutation == "stale":
            f["source_observed_at"] = f["acquired_at"] = (NOW-timedelta(minutes=31)).isoformat()
        elif mutation == "skew":
            f["source_observed_at"] = (NOW-timedelta(minutes=6)).isoformat()
        elif mutation == "unit":
            f["unit"] = "unknown"
        elif mutation == "aggregate":
            f["source_scope"] = "market_aggregate"
            f["provider"] = "coinalyze"
        elif mutation == "source_future":
            f["source_observed_at"] = (NOW+timedelta(seconds=1)).isoformat()
        elif mutation == "source_missing":
            f["source_observed_at"] = None
        else:
            f["interval_seconds"] = None
    result = derive(facts)
    group = result["comparisons"][0]
    assert group["funding_annualized"]["dispersion"]["value"] is None
    if mutation == "skew":
        assert group["oi_usd"]["subtotal"]["reason"] == "source_skew"
    elif mutation != "interval":
        assert group["oi_usd"]["subtotal"]["value"] == 100


def test_incompatible_settlement_is_separate_and_aggregate_never_added():
    facts = frozen()
    for f in facts:
        if f["venue"] == "okx":
            f["instrument"] = {**f["instrument"], "quote_asset": "USDC", "settle_asset": "USDC"}
    aggregate = deepcopy(facts[-2])
    aggregate.update(source_scope="market_aggregate", venue="coinalyze", provider="coinalyze", value=1e8)
    result = derive(facts+[aggregate])
    assert sorted(g["oi_usd"]["subtotal"]["value"] for g in result["comparisons"]) == [100, 200]
    assert result["aggregate_oi_comparison"]["value"] is None


def test_future_acquisition_cannot_change_cutoff_and_inputs_replay():
    facts = frozen()
    original = derive(facts)
    future = deepcopy(facts)
    for f in future:
        f["acquired_at"] = (NOW+timedelta(seconds=1)).isoformat()
        f["source_ref"] += "future"
        f["value"] = 1e9
    assert derive(facts+future) == original
    assert derive(original["inputs"]) == original
    assert derive(list(reversed(facts))) == original
    return original


@pytest.mark.parametrize("history", [False, True])
def test_no_or_incompatible_history_remains_unavailable(history):
    facts = frozen()
    if not history:
        facts = [f for f in facts if f["acquired_at"] == NOW.isoformat()]
    else:
        for f in facts:
            if f["acquired_at"] != NOW.isoformat():
                f["instrument"] = {**f["instrument"], "source_instrument_id": "incompatible"}
    result = derive(facts)
    assert result["exact_venues"]["binance"]["oi_change_1h"]["value"] is None
    assert result["exact_venues"]["binance"]["price_oi_state"]["state"] == "unavailable"
    assert all(result[name]["value"] is None for name in ("funding_extreme", "oi_acceleration", "liquidation_impulse"))


def test_read_path_cli_and_no_existing_consumer_change():
    from typer.testing import CliRunner
    from aletheia.cli.data_cmd import data_app

    expected = derive(frozen())
    before = repo._select_all("open_interest_snapshots", order="rowid DESC", limit=1)
    assert read_positioning_evidence("BTC/USDT", NOW.isoformat(), venues=VENUES) == expected
    store("binance", age=-1, oi=1e9)
    assert read_positioning_evidence("BTC/USDT", NOW.isoformat(), venues=VENUES) == expected
    after = repo._select_all("open_interest_snapshots", order="rowid DESC", limit=1)
    result = CliRunner().invoke(data_app, ["positioning-evidence", "--symbol", "BTC/USDT", "--cutoff", NOW.isoformat()])
    assert result.exit_code == 0, result.output
    assert '"authority": "observation_only"' in result.output
    assert before != after  # only the explicit fixture write changed the reader
    assert repo._select_all("open_interest_snapshots", order="rowid DESC", limit=1) == after


def test_zero_oi_and_nonindex_reference_do_not_fabricate_shares_or_basis():
    facts = frozen()
    for f in facts:
        if f["fact_type"] == "open_interest":
            f["value"] = 0
        if f["fact_type"] == "perpetual_premium":
            f["reference_kind"] = "oracle"
    group = derive(facts)["comparisons"][0]
    assert group["oi_usd"]["subtotal"]["value"] == 0
    assert group["oi_usd"]["shares"] is None
    assert group["oi_usd"]["disagreement_ratio"]["value"] is None
    assert group["basis_bps"]["median"]["value"] is None


def test_source_after_acquisition_and_negative_oi_are_rejected():
    facts = frozen()
    for f in facts:
        if f["venue"] == "binance":
            f["acquired_at"] = (NOW-timedelta(minutes=1)).isoformat()
        elif f["fact_type"] == "open_interest":
            f["value"] = -1
    result = derive(facts)
    assert result["exact_venues"]["binance"]["oi_usd"]["value"] is None
    assert result["exact_venues"]["okx"]["oi_usd"]["value"] is None


def test_bounded_read_does_not_present_truncated_inputs_as_complete():
    from aletheia.storage.sqlite import get_db

    frozen()
    template = repo._select_all("funding_rate_snapshots", limit=1)[0]
    names = list(template)
    rows = [tuple((f"budget-{i}" if key == "id" else template[key]) for key in names) for i in range(2049)]
    get_db().executemany(f"INSERT INTO funding_rate_snapshots ({','.join(names)}) VALUES ({','.join('?' for _ in names)})", rows)
    result = read_positioning_evidence("BTC/USDT", NOW.isoformat())
    assert result["available"] is False
    assert result["reason"] == "input_limit_exceeded"


def test_strict_clock_and_unknown_interval():
    with pytest.raises(ValueError):
        derive_positioning_evidence("BTC/USDT", [], datetime(2026, 1, 1).isoformat())
    assert derive([])["exact_venues"]["binance"]["oi_usd"]["value"] is None
