from __future__ import annotations

from typer.testing import CliRunner

from aletheia.cli.inspect_cmd import inspect_app, microstructure


def test_microstructure_forwards_requested_limit(monkeypatch, capsys):
    from aletheia.storage import repositories as repo

    calls: list[int] = []
    monkeypatch.setattr(repo, "get_active_universe_members", lambda limit=20: calls.append(limit) or [])

    microstructure(symbol=None, limit=30, json_output=True)

    assert calls == [30]
    assert capsys.readouterr().out.strip() == "[]"


def test_microstructure_rejects_nonpositive_limit():
    result = CliRunner().invoke(inspect_app, ["microstructure", "--limit", "0", "--json"])

    assert result.exit_code != 0
