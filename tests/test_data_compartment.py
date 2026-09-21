"""Concrete public Data is absent-safe at the existing composition roots."""

import os
import builtins
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("collector", ["coingecko_top", "defillama", "hyperliquid", "macro", "coinalyze", "options_structure"])
def test_missing_context_adapter_does_not_stop_primary_lane(collector, monkeypatch):
    from aletheia.agent_runtime import scheduler

    missing = f"extensions.data.collectors.{collector}"
    original_import = builtins.__import__
    failures, successes = [], []

    def import_without_adapter(name, *args, **kwargs):
        if name == missing:
            raise ModuleNotFoundError(missing, name=missing)
        return original_import(name, *args, **kwargs)

    async def primary(_cfg, _tick):
        return {"status": "success", "error_codes": []}

    monkeypatch.delenv("ALETHEIA_COLLECTOR_PROCESS", raising=False)
    monkeypatch.setattr(scheduler, "_DC_TICK", 0)
    monkeypatch.setattr(scheduler, "_DC_CONTEXT_TICK", 0)
    monkeypatch.setattr(builtins, "__import__", import_without_adapter)
    monkeypatch.setattr(scheduler, "load_config", lambda: object())
    monkeypatch.setattr(scheduler, "_async_data_collectors", primary)
    monkeypatch.setattr(scheduler, "_record_job_failure", lambda job, reason: failures.append((job, reason)))
    monkeypatch.setattr(scheduler, "_record_job_success", successes.append)
    scheduler._run_data_collector_context()
    assert failures == [("data_collector_context", missing)]
    assert not successes
    assert scheduler._run_data_collectors()["status"] == "success"
    assert successes == ["data_collectors"]


def test_public_data_has_one_physical_implementation():
    root = Path(__file__).resolve().parents[1]
    for relative in (
        "ccxt_provider.py", "sources/__init__.py", "sources/chain.py",
        "sources/coingecko.py", "sources/binance_public.py",
        "collectors/microstructure.py", "collectors/derivatives.py",
        "collectors/volume_scanner.py",
        "collectors/coingecko_top.py", "collectors/defillama.py",
        "collectors/hyperliquid.py",
        "collectors/macro.py", "collectors/coinalyze.py",
        "collectors/options_structure.py",
    ):
        assert not (root / "aletheia/data" / relative).exists()
        assert (root / "extensions/data" / relative).is_file()


def test_attribution_and_stored_context_survive_absent_position_x(tmp_path):
    script = '''
from datetime import datetime, timezone
import socket
import sys

attempted = []
class MissingPositionX:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "aletheia.data.position_x":
            attempted.append(fullname)
            raise ModuleNotFoundError(fullname, name=fullname)
sys.meta_path.insert(0, MissingPositionX())
side_effects = []
def forbidden(*args, **kwargs):
    side_effects.append("network_or_credentials")
    raise AssertionError("stored context must not access network or credentials")
socket.socket.connect = forbidden
from aletheia.config import secrets
secrets.get_secret = forbidden
from aletheia.storage import repositories as repo
from aletheia.storage.migrations import run_migrations
from aletheia.trading.entry_snapshot import capture_attribution_snapshot
run_migrations()
now = datetime.now(timezone.utc).isoformat()
repo.insert_orderbook_snapshot(dict(
    symbol="BTC/USDT", exchange="bitget", market_type="perp",
    bid_price=100, ask_price=101, spread_bps=10,
    bid_depth_10bps_usd=10000, ask_depth_10bps_usd=10000,
    total_depth_10bps_usd=20000, observed_at=now,
))
repo.insert_funding_rate_snapshot(dict(
    symbol="BTC/USDT", exchange="bitget", funding_rate=0.0001,
    funding_rate_annualized=0.1095, observed_at=now,
))
context = capture_attribution_snapshot(
    "BTC/USDT", exchange="bitget", market_type="perp",
)["market_context"]
assert context.get("data_completeness") == 0.67, context
assert context["tradability"]["depth_10bps_usd"] == 20000
assert context["leverage"]["funding_rate"] == 0.0001

from aletheia.data.context_builder import render_enriched_context_md
row = dict(as_of_time=now, source="fixture", venue="bitget", data_quality="degraded",
    structure_bias="bearish", composite_z=-0.9, structure_rank=25, universe_size=30,
    dimension_scores_json='{"cgo":{"direction":"bearish","percentile":13.3}}')
rendered = render_enriched_context_md(["BTC/USDT"], {}, {}, None,
    perp_structure={"BTC/USDT": row})
assert "Position X (advisory only): bias bearish; composite_z -0.90; rank 25/30" in rendered
assert "cgo bearish p13.3; caveat degraded_data" in rendered
assert not attempted and not side_effects, (attempted, side_effects)
'''
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "ALETHEIA_DATA_DIR": str(tmp_path)},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_stored_options_contract_survives_absent_data_extension(tmp_path):
    script = '''
import socket
import sys

class MissingData:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "extensions" or fullname.startswith("extensions."):
            raise AssertionError("stored options contract must not import Extensions")
sys.meta_path.insert(0, MissingData())
def forbidden(*args, **kwargs):
    raise AssertionError("stored options must not access network or credentials")
socket.socket.connect = forbidden
from aletheia.config import secrets
secrets.get_secret = forbidden
from aletheia.data.options_volatility import OptionsVolatilitySnapshot
from aletheia.storage import repositories as repo
from aletheia.storage.migrations import run_migrations
run_migrations()
cutoff = "2026-09-11T08:00:00+00:00"
snapshot = OptionsVolatilitySnapshot(symbol="BTC", acquired_at=cutoff, atm_iv_30d=65)
repo.insert_options_volatility_snapshot(snapshot.model_dump())
exported = repo.options_volatility_at("BTC", cutoff)
assert exported["available"] and exported["atm_iv_30d"] == 65
assert exported["authority"] == "observation_only"
assert exported["derivation_version"] == snapshot.derivation_version
assert repo.options_volatility_at("BTC", "2026-09-11T08:31:00+00:00")["reason"] == "stale_snapshot"
assert repo.latest_options_structure("BTC") is None
'''
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "ALETHEIA_DATA_DIR": str(tmp_path)},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("entrypoint", ["cli", "web", "collector"])
@pytest.mark.parametrize("missing", [
    "extensions", "extensions.data", "extensions.data.ccxt_provider", "broken_dependency",
])
def test_data_absence_clears_bindings_without_starting_runtime(entrypoint, missing, tmp_path):
    script = f"entrypoint = {entrypoint!r}\nmissing = {missing!r}\n" + '''
import asyncio
import importlib
import socket
import sys
from aletheia.config import secrets
from aletheia.data import providers

def forbidden(*args, **kwargs):
    raise AssertionError("composition must not read secrets, open clients or use stale bindings")
secrets.get_secret = forbidden
socket.socket.connect = forbidden
providers.configure_listing_reader(forbidden)
providers.configure_market_data_factory(forbidden)
attempted = []
class MissingData:
    def find_spec(self, fullname, path=None, target=None):
        target_name = "extensions.data.ccxt_provider" if missing == "broken_dependency" else missing
        if fullname == target_name:
            attempted.append(fullname)
            raise ModuleNotFoundError("isolated missing dependency", name=missing)
sys.meta_path.insert(0, MissingData())

class StopBeforeRuntime(Exception):
    pass
def stop_before_runtime():
    raise StopBeforeRuntime
try:
    if entrypoint == "cli":
        from aletheia import main
        main._configure_runtime()
    elif entrypoint == "web":
        importlib.import_module("aletheia.server.app")  # no lifespan / Gateway startup
    else:
        import aletheia.logging_config
        from aletheia.config import loader
        from aletheia.data import collector_runner
        aletheia.logging_config.configure_logging = lambda: None
        loader.bootstrap = stop_before_runtime
        try:
            collector_runner.main()
        except StopBeforeRuntime:
            pass
except ModuleNotFoundError as exc:
    assert missing == "broken_dependency" and exc.name == missing, exc
else:
    assert missing != "broken_dependency", "internal dependency defects must propagate"
    assert providers._listing_reader is None
    assert providers._provider_factory is None
    try:
        providers.get_market_data_provider("okx", exact_venue=True)
    except RuntimeError as exc:
        assert str(exc) == "market data provider unavailable"
    else:
        raise AssertionError("absent Data must remain unavailable")
assert attempted, "test must reach the missing adapter or parent"
'''
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "ALETHEIA_DATA_DIR": str(tmp_path)},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
