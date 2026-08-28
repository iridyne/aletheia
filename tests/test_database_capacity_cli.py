import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from aletheia.main import app


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
