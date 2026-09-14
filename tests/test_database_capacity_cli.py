import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from aletheia.main import app


@pytest.mark.parametrize("apply", [False, True])
def test_compaction_cli_registers_ops_with_explicit_apply(monkeypatch, apply):
    from aletheia.maintenance import database_capacity
    from ops.maintenance import database_compaction

    assert not hasattr(database_capacity, "compact_database")
    calls = []

    def compact(**kwargs):
        calls.append(kwargs)
        return {"applied": kwargs["apply"]}

    monkeypatch.setattr(database_compaction, "compact_database", compact)
    result = CliRunner().invoke(app, ["data", "compact"] + (["--apply"] if apply else []))
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0]["apply"] is apply
    assert callable(calls[0]["progress"]) is apply
    assert ("Dry-run only" in result.output) is (not apply)


@pytest.mark.parametrize("args", [["data", "capacity"], ["data", "capacity", "--json"]])
def test_capacity_unavailable_is_failure_in_both_output_modes(monkeypatch, args):
    monkeypatch.setattr(
        "aletheia.config.loader.load_config",
        lambda **_: SimpleNamespace(retention=SimpleNamespace(app_db_capacity_mb=2048)),
    )
    monkeypatch.setattr(
        "aletheia.maintenance.database_capacity.database_capacity_snapshot",
        lambda **_: {"available": False, "path": "/missing/app.db"},
    )
    monkeypatch.setattr(
        "aletheia.maintenance.database_capacity.database_capacity_history",
        lambda **_: [],
    )
    monkeypatch.setattr(
        "aletheia.maintenance.database_capacity.database_capacity_trend",
        lambda *_args, **_kwargs: {"status": "insufficient_history"},
    )

    result = CliRunner().invoke(app, args)

    assert result.exit_code == 1
    if "--json" in args:
        payload = json.loads(result.stdout)
        assert payload["current"]["available"] is False
    else:
        assert "Database not available" in result.stdout
