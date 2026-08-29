from __future__ import annotations

import json

from aletheia.cli.inspect_cmd import serenity


def test_serenity_run_status_filter_reaches_repository(monkeypatch, capsys):
    from aletheia.storage import repositories as repo

    calls: list[dict] = []
    monkeypatch.setattr(
        repo,
        "list_serenity_research_runs",
        lambda **kwargs: calls.append(kwargs) or [],
    )

    serenity(what="runs", status_filter="failed", limit=2, json_output=True)

    assert calls == [{"status": "failed", "limit": 2}]
    assert json.loads(capsys.readouterr().out) == []
