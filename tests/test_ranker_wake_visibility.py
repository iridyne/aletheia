"""Current CLI planning facts exclude retired pending carriers."""

import json

from typer.testing import CliRunner


def test_entry_status_ignores_legacy_pending(_inmemory_db):
    from aletheia.cli.inspect_cmd import inspect_app
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute("INSERT INTO pi_decisions(id,action,symbol) VALUES ('old','signal_pending','BTC/USDT')")
    db.commit()
    result = CliRunner().invoke(inspect_app, ["entry-status", "--json"])
    assert result.exit_code == 0, result.output
    current = json.loads(result.output)
    assert current["summary"]["needs_planning"] == 0
    assert current["candidate_attention"] == []
    assert db.execute("SELECT action FROM pi_decisions WHERE id='old'").fetchone()[0] == "signal_pending"
    text = CliRunner().invoke(inspect_app, ["entry-status"])
    assert text.exit_code == 0, text.output
