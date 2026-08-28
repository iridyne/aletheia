import json
from types import SimpleNamespace


def test_audit_data_preserves_observed_zero_metrics(monkeypatch, capsys):
    import aletheia.cli.audit_cmd as audit_cmd
    import aletheia.config.loader as loader
    import aletheia.storage.repositories as repositories
    import aletheia.storage.sqlite as sqlite

    class Result:
        def fetchone(self):
            return (4, 0, 0.0)

    class Database:
        def execute(self, *_args):
            return Result()

    monkeypatch.setattr(
        loader,
        "load_config",
        lambda: SimpleNamespace(historical_data=SimpleNamespace(min_bars_for_backtest=10)),
    )
    monkeypatch.setattr(repositories, "get_active_universe_members", lambda: [])
    monkeypatch.setattr(repositories, "complete_outcome_economics_sql", lambda: "1")
    monkeypatch.setattr(sqlite, "get_db", lambda: Database())

    audit_cmd.data(output_json=False)
    human = capsys.readouterr().out
    assert "Win rate: 0.0%" in human
    assert "Avg return: 0.0%" in human
    assert "Status: degraded" in human

    audit_cmd.data(output_json=True)
    report = json.loads(capsys.readouterr().out)
    effectiveness = report["signal_effectiveness_7d"]
    assert effectiveness["settled_outcomes"] == 4
    assert effectiveness["win_rate_pct"] == 0.0
    assert effectiveness["avg_net_return_pct"] == 0.0
