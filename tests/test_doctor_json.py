"""Phase 1 — doctor machine-readable output (the AI agent's eyes).

Verifies the JSON contract that an agent reads instead of scraping the printed
table: stable keys, valid severity enum, and the GET /_infra/doctor endpoint
returning the same shape.
"""

import json

from aletheia.diagnostics import doctor as dx

VALID_SEVERITIES = {"ok", "info", "warn", "blocker"}


def test_report_to_dict_contract():
    report = dx.Report(generated_at="2026-06-28T00:00:00+00:00")
    report.add("a.ok", "ok", "fine")
    report.add("b.warn", "warn", "careful", "do the thing")
    report.add("c.block", "blocker", "broken", "fix it")

    d = report.to_dict()
    assert d["generated_at"] == "2026-06-28T00:00:00+00:00"
    assert d["healthy"] is False  # has warn + blocker
    assert d["summary"]["total"] == 3
    assert d["summary"]["blockers"] == ["c.block"]
    assert d["summary"]["warns"] == ["b.warn"]
    # each check is a flat dict with exactly the four contract fields
    for c in d["checks"]:
        assert set(c) == {"name", "severity", "detail", "fix_hint"}
        assert c["severity"] in VALID_SEVERITIES
    # round-trips through json cleanly (no non-serializable objects)
    assert json.loads(json.dumps(d)) == d


def test_report_to_dict_healthy_when_only_ok_info():
    report = dx.Report(generated_at="t")
    report.add("a", "ok", "x")
    report.add("b", "info", "y")
    d = report.to_dict()
    assert d["healthy"] is True
    assert d["summary"]["blockers"] == [] and d["summary"]["warns"] == []


def test_build_report_to_dict_is_json_serializable():
    # build_report() runs the real probes; its dict form must always serialize.
    d = dx.build_report().to_dict()
    s = json.dumps(d)  # must not raise
    assert json.loads(s)["checks"], "expected at least one probe to run"
    for c in d["checks"]:
        assert c["severity"] in VALID_SEVERITIES


def test_infra_doctor_endpoint_matches_contract(api_only_runtime):
    from fastapi.testclient import TestClient
    from aletheia.server.app import app

    with TestClient(app) as client:
        resp = client.get("/_infra/doctor")
    assert resp.status_code == 200
    d = resp.json()
    assert {"generated_at", "healthy", "checks", "summary"} <= set(d)
    assert isinstance(d["checks"], list) and d["checks"]
    for c in d["checks"]:
        assert set(c) == {"name", "severity", "detail", "fix_hint"}
        assert c["severity"] in VALID_SEVERITIES


def test_infra_doctor_attributes_process_to_serving_runtime(api_only_runtime, monkeypatch):
    from fastapi.testclient import TestClient
    from aletheia.server.app import app

    monkeypatch.setenv("ALETHEIA_RUNTIME_ID", "runtime-isolated-8791")
    monkeypatch.setenv("ALETHEIA_RUNTIME_PORT", "8791")
    with TestClient(app) as client:
        response = client.get("/_infra/doctor")

    assert response.status_code == 200
    process = next(
        check for check in response.json()["checks"]
        if check["name"] == "process.gateway"
    )
    assert "runtime-isolated-8791" in process["detail"]
    assert ":8791" in process["detail"]
    assert ":8787" not in process["detail"]


def test_infra_status_reports_effective_runtime_port(api_only_runtime, monkeypatch):
    from fastapi.testclient import TestClient
    from aletheia.server.app import app

    monkeypatch.setenv("ALETHEIA_RUNTIME_PORT", "8791")
    with TestClient(app) as client:
        response = client.get("/_infra/status")

    assert response.json()["port"] == 8791
