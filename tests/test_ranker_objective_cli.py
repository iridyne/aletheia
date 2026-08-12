from __future__ import annotations

import json

import pytest
import typer


def test_build_audit_view_dispatches_ranker_objective(monkeypatch):
    import aletheia.analytics.ranker_objective_audit as audit
    from aletheia.cli.opportunity import _build_audit_view

    seen = {}

    def fake_builder(**kwargs):
        seen.update(kwargs)
        return {"read_only": True, "behavior_changed": False, "window": kwargs}

    monkeypatch.setattr(audit, "build_ranker_objective_audit", fake_builder)
    monkeypatch.setattr(audit, "render_ranker_objective_audit", lambda report: "READ-ONLY observational")

    report, rendered = _build_audit_view(
        hours=48,
        limit=321,
        stage=None,
        verdict=None,
        symbol=None,
        view="ranker-objective",
    )

    assert seen == {"hours": 48, "limit": 321}
    assert report["read_only"] is True
    assert rendered == "READ-ONLY observational"


def test_ranker_objective_rejects_incompatible_shared_filters():
    from aletheia.cli.opportunity import _build_audit_view

    for filters in (
        {"stage": "ranked", "verdict": None, "symbol": None},
        {"stage": None, "verdict": "would_trade", "symbol": None},
        {"stage": None, "verdict": None, "symbol": "BTC/USDT"},
    ):
        with pytest.raises(typer.BadParameter, match="not supported"):
            _build_audit_view(hours=24, limit=10, view="ranker-objective", **filters)


def test_json_contract_and_text_labels_are_explicit():
    from aletheia.analytics.ranker_objective_audit import (
        build_ranker_objective_audit,
        render_ranker_objective_audit,
    )

    report = build_ranker_objective_audit(hours=12, limit=10)
    encoded = json.dumps(report)
    rendered = render_ranker_objective_audit(report)

    assert encoded
    assert report["read_only"] is True
    assert report["behavior_changed"] is False
    for key in ("window", "variants", "strata", "exclusions", "availability"):
        assert key in report
    assert "READ-ONLY" in rendered
    assert "Observational" in rendered
    assert "production ranking unchanged" in rendered
    assert "authoritative shadow-batch linkage unavailable" in rendered
    assert "Intended batch completeness: unavailable_on_current_schema" in rendered
    assert "Writer atomicity: false" in rendered
    assert "descriptive over persisted members only" in rendered
    assert "promotion/action authority: none" in rendered
