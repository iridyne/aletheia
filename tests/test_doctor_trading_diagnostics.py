from __future__ import annotations

import json


def test_default_doctor_json_does_not_include_trading_diagnostics(monkeypatch):
    from typer.testing import CliRunner

    from aletheia.main import app

    def fail_if_called(*args, **kwargs):
        raise AssertionError("trading audit should not run in default doctor")

    monkeypatch.setattr(
        "aletheia.analytics.trade_funnel.build_trade_funnel_audit",
        fail_if_called,
    )
    monkeypatch.setattr(
        "aletheia.analytics.sizing_trace.build_sizing_trace_audit",
        fail_if_called,
    )
    monkeypatch.setattr(
        "aletheia.analytics.thesis_lineage.build_thesis_lineage_audit",
        fail_if_called,
    )

    result = CliRunner().invoke(app, ["doctor", "--json"])

    assert result.exit_code in (0, 1)
    payload = json.loads(result.output)
    assert "diagnostics" not in payload


def test_doctor_json_deep_includes_compact_trading_diagnostics():
    from typer.testing import CliRunner

    from aletheia.main import app

    result = CliRunner().invoke(app, ["doctor", "--json", "--deep"])

    assert result.exit_code in (0, 1)
    payload = json.loads(result.output)
    trading = payload["diagnostics"]["trading"]
    assert set(trading) == {"alpha", "funnel", "sizing", "thesis"}
    assert "main_bottleneck" in trading["alpha"]
    assert "main_choke_point" in trading["funnel"]
    assert "main_sizing_bottleneck" in trading["sizing"]
    assert "missing_counts" in trading["thesis"]
    assert "raw_missing" in trading["thesis"]


def test_doctor_embeds_audit_failures_as_unavailable(monkeypatch):
    from aletheia.diagnostics import doctor as dx

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr(
        "aletheia.analytics.trade_funnel.build_trade_funnel_audit",
        fail,
    )

    bundle = dx.build_deep_diagnostics(dx.Report(generated_at="t"))

    assert bundle["trading"]["funnel"]["available"] is False
    assert "synthetic audit failure" in bundle["trading"]["funnel"]["reason"]


def test_trading_diagnostics_do_not_create_doctor_blockers(monkeypatch):
    from aletheia.diagnostics import doctor as dx

    monkeypatch.setattr(
        "aletheia.analytics.trade_funnel.build_trade_funnel_audit",
        lambda **kwargs: {
            "schema_errors": [],
            "counts": {},
            "position_stats": {},
            "main_choke_point": {
                "stage": "risk_engine",
                "severity": "warn",
                "summary": "risk rejected every proposal",
                "next_action": "inspect risk reasons",
            },
        },
    )

    report = dx.Report(generated_at="t")
    bundle = dx.build_deep_diagnostics(report)

    assert report.blockers == []
    assert bundle["trading"]["funnel"]["main_choke_point"]["stage"] == "risk_engine"
    assert "thesis" in bundle["trading"]


def test_doctor_fix_does_not_mutate_trading_config(monkeypatch):
    from typer.testing import CliRunner

    from aletheia.main import app

    def fail_if_config_saved(*args, **kwargs):
        raise AssertionError("doctor --fix must not mutate trading config from audits")

    monkeypatch.setattr("aletheia.config.loader.save_config", fail_if_config_saved, raising=False)
    monkeypatch.setattr("aletheia.diagnostics.doctor.apply_fixes", lambda: ["no trading fix"])

    result = CliRunner().invoke(app, ["doctor", "--json", "--deep", "--fix"])

    assert result.exit_code in (0, 1)
    payload = json.loads(result.output)
    assert payload["fix_actions"] == ["no trading fix"]
    assert "trading" in payload["diagnostics"]
