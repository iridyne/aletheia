"""Optional supplements cannot become Core boot or settlement requirements."""

import asyncio
import ast
import builtins
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from aletheia.data import providers
from aletheia.universe import outcome_recorder


@pytest.fixture(autouse=True)
def bindings(monkeypatch):
    monkeypatch.setattr(providers, "_macro_reader", None)
    monkeypatch.setattr(providers, "_prediction_history_reader", None)
    monkeypatch.setattr(outcome_recorder, "_structure_shadow_settler", None)


@pytest.mark.parametrize("entry", ["core/aletheia/main.py", "core/aletheia/server/app.py"])
@pytest.mark.parametrize("module,callback", [
    ("yfinance_provider", "_macro_reader"),
    ("polymarket_provider", "_prediction_history_reader"),
    ("perp_structure_shadow", "_structure_shadow_settler"),
])
@pytest.mark.parametrize("missing", [None, "extensions.data", "internal_dependency"])
def test_exact_bootstrap_binding_is_independent(entry, module, callback, missing, monkeypatch):
    owner = outcome_recorder if callback == "_structure_shadow_settler" else providers
    monkeypatch.setattr(owner, callback, object())
    real_import = builtins.__import__

    def imported(name, *args, **kwargs):
        if name == "extensions.data." + module and missing:
            raise ModuleNotFoundError(name=missing)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", imported)
    tree = ast.parse(Path(entry).read_text())
    body = (next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name == "_configure_runtime").body if entry.endswith("main.py") else tree.body)
    block = next(n for n in body if isinstance(n, ast.Try) and isinstance(n.body[0], ast.ImportFrom)
                 and n.body[0].module == "extensions.data." + module)
    namespace = {
        "configure_macro_reader": providers.configure_macro_reader,
        "configure_prediction_history_reader": providers.configure_prediction_history_reader,
        "configure_structure_shadow_settler": outcome_recorder.configure_structure_shadow_settler,
    }
    if missing == "internal_dependency":
        with pytest.raises(ModuleNotFoundError) as exc:
            exec(compile(ast.Module(body=[block], type_ignores=[]), entry, "exec"), namespace)
        assert exc.value.name == missing
    else:
        exec(compile(ast.Module(body=[block], type_ignores=[]), entry, "exec"), namespace)
        assert getattr(owner, callback) is None if missing else callable(getattr(owner, callback))


def test_macro_absent_and_installed(monkeypatch):
    assert asyncio.run(providers.read_macro_context()) == ""
    reader = AsyncMock(return_value="macro facts")
    monkeypatch.setattr(providers, "_macro_reader", reader)
    assert asyncio.run(providers.read_macro_context()) == "macro facts"
    reader.assert_awaited_once_with()


@pytest.mark.parametrize("enabled,fails", [(False, False), (True, False), (True, True)])
def test_macro_gate_preserves_base_context(monkeypatch, enabled, fails):
    from aletheia.config import loader
    from aletheia.config.schema import AletheiaConfig
    from aletheia.trading.market_context import build_market_context
    from tests.test_market_context import _FakeProvider
    cfg = AletheiaConfig()
    assert cfg.market_data.enable_yfinance_macro is False
    cfg.market_data.enable_yfinance_macro = enabled
    monkeypatch.setattr(loader, "load_config", lambda: cfg)
    reader = AsyncMock(side_effect=RuntimeError("offline")) if fails else AsyncMock(return_value="supplemental macro facts")
    monkeypatch.setattr(providers, "_macro_reader", reader)
    context, debug = asyncio.run(build_market_context(["BTCUSDT"], provider=_FakeProvider()))
    assert "Verified market data snapshot" in context and debug["fallback"] is False
    assert ("supplemental macro facts" in context) is (enabled and not fails)
    assert reader.await_count == int(enabled)


@pytest.mark.parametrize("local,market,reader,result", [
    ([{"ts": "t", "yes_probability": 0.4}], None, None, "local"),
    ([], {"external_market_id": "external"}, None, "local"),
    ([], {"external_market_id": "external"}, [], "polymarket_api"),
    ([], {"external_market_id": "external"}, RuntimeError("offline"), "local"),
    ([], None, None, "404"),
])
def test_history_preserves_local_and_external_source(monkeypatch, local, market, reader, result):
    from fastapi import HTTPException
    from aletheia.server.routes import prediction
    monkeypatch.setattr(prediction.repo, "list_probability_snapshots", lambda *a, **k: local)
    monkeypatch.setattr(prediction.repo, "get_prediction_market_by_id", lambda *a: market)
    callback = AsyncMock(side_effect=reader) if isinstance(reader, Exception) else AsyncMock(return_value=reader)
    if reader is not None:
        monkeypatch.setattr(providers, "_prediction_history_reader", callback)
    if result == "404":
        with pytest.raises(HTTPException) as exc:
            asyncio.run(prediction.get_prediction_market_history("market"))
        assert exc.value.status_code == 404
    else:
        value = asyncio.run(prediction.get_prediction_market_history("market"))
        assert value["source"] == result
    if local or reader is None:
        callback.assert_not_awaited()


@pytest.mark.parametrize("command,module,args", [
    ("positioning-evidence", "extensions.data.positioning_evidence", ["--symbol", "BTC/USDT", "--cutoff", "2026-01-01T00:00:00+00:00"]),
    ("evaluate-perp-structure", "extensions.data.perp_structure_shadow", []),
    ("ingest-perp-structure", "extensions.data.collectors.vergex_structure", []),
])
def test_cli_absence_precedes_producer_and_payload_read(tmp_path, monkeypatch, command, module, args):
    from typer.testing import CliRunner
    from aletheia.cli.data_cmd import data_app
    real_import = builtins.__import__
    def imported(name, *a, **k):
        if name == module:
            raise ModuleNotFoundError(name=module)
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", imported)
    if command == "ingest-perp-structure":
        payload = tmp_path / "invalid.json"
        payload.touch()
        args = [str(payload)]
    result = CliRunner().invoke(data_app, [command, *args])
    assert result.exit_code == 1
    assert "unavailable" in result.output


def test_canonical_settlement_survives_absent_structure_settler(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import UniverseForwardOutcome
    now = datetime.now(timezone.utc)
    row = UniverseForwardOutcome(
        member_id="core-member", snapshot_id="core-snapshot", symbol="BTC/USDT",
        venue="okx", market_type="spot", direction="long", scanner_family="momentum",
        horizon="4h", entry_price_usd=100.0,
        entry_ts=(now-timedelta(hours=5)).isoformat(), due_at=(now-timedelta(hours=1)).isoformat(),
    )
    repo.insert_forward_outcome(row)
    monkeypatch.setattr(outcome_recorder, "_batch_fetch_market_observations", AsyncMock(return_value={row.id: {
        "exit_price": 101.0, "range_high": 101.0, "range_low": 100.0,
        "price_source": "horizon_ohlcv", "price_quality_ok": True, "settlement_lag_sec": 0,
    }}))
    monkeypatch.setattr(outcome_recorder, "_maybe_generate_guard_candidates", lambda *_: None)
    assert asyncio.run(outcome_recorder.settle_due(max_rows=1)) == 1
    assert repo._select_one("universe_forward_outcomes", row.id)["status"] == "settled"


def test_doctor_absent_structure_producer_still_reads_stored_rows(monkeypatch):
    from aletheia.diagnostics import doctor
    from aletheia.storage import repositories as repo
    real_import = builtins.__import__
    def imported(name, *a, **k):
        if name == "extensions.data.collectors.vergex_structure":
            raise ModuleNotFoundError(name=name)
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", imported)
    report = doctor.Report()
    doctor._probe_perp_structure(report)
    assert any("optional structure producer" in item.detail for item in report.checks)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    repo.insert_perp_structure_snapshot(dict(
        symbol="BTC/USDT", venue="hyperliquid", source="owner_aggregate",
        as_of_time=now, observed_at=now, data_quality="complete",
    ))
    report = doctor.Report()
    doctor._probe_perp_structure(report)
    assert any(item.severity == "ok" and "owner_aggregate" in item.detail for item in report.checks)
