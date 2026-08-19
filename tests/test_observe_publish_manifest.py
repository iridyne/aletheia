"""G2 tests: ``aletheia observe publish-manifest`` CLI (#1072 v1 design §5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aletheia.cli.observe_cmd import observe_app


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _write_manifest_yaml(tmp_path: Path, **overrides) -> Path:
    """Compose a valid ExecutionManifest YAML with optional field overrides."""
    import yaml

    base = {
        "observation_id": "ufc:g2-test:epoch-1:0",
        "generation": 1,
        "spec_hash": "g2-spec",
        "treatment_adapter_id": "universe_family_calibration",
        "treatment_adapter_version": "v11",
        "assignment_policy": "stable_assignment_by_key",
        "assignment_salt": "g2salt",
        "assignment_unit_key_template": (
            "universe:cluster:{scoring_cohort}:{family}:{direction}:{market_type}"
        ),
        "allocation_pct": 0.5,
        "control_payload": {"multiplier": 1.0},
        "treatment_payload": {"multiplier": 1.1},
        "eligible_population": {"path_id": "universe_family_calibration"},
        "behavior_contract_hash": "bh-g2",
        "interference_key": "universe:momentum:long:perp",
        "composability_contract": "exclusive",
        "effective_from": "2026-08-19T00:00:00+00:00",
        "deterministic_baseline": {"multiplier": 1.0},
        "unavailable_fallback": "baseline",
    }
    base.update(overrides)
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(base), encoding="utf-8")
    return path


def test_publish_manifest_writes_row_and_reports_hash(runner: CliRunner, tmp_path: Path):
    from aletheia.observation_lab.manifest import load_active_manifests

    spec_path = _write_manifest_yaml(tmp_path)
    result = runner.invoke(observe_app, ["publish-manifest", str(spec_path), "--json"])

    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["newly_inserted"] is True
    assert payload["observation_id"] == "ufc:g2-test:epoch-1:0"
    assert payload["generation"] == 1
    assert len(payload["manifest_hash"]) == 64

    active = load_active_manifests(now="2026-08-19T12:00:00+00:00")
    assert any(m.manifest_hash == payload["manifest_hash"] for m in active)


def test_publish_manifest_is_idempotent_on_same_content(runner: CliRunner, tmp_path: Path):
    spec_path = _write_manifest_yaml(tmp_path)
    first = runner.invoke(observe_app, ["publish-manifest", str(spec_path), "--json"])
    second = runner.invoke(observe_app, ["publish-manifest", str(spec_path), "--json"])

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert json.loads(first.stdout)["newly_inserted"] is True
    assert json.loads(second.stdout)["newly_inserted"] is False


def test_publish_manifest_rejects_generation_conflict(runner: CliRunner, tmp_path: Path):
    """Same (observation_id, generation) with different content = exit 1."""
    first_path = _write_manifest_yaml(tmp_path)
    first = runner.invoke(observe_app, ["publish-manifest", str(first_path), "--json"])
    assert first.exit_code == 0

    # Different treatment payload → different manifest_hash, same
    # (observation_id, generation) → ManifestGenerationConflict.
    conflicting = _write_manifest_yaml(
        tmp_path, treatment_payload={"multiplier": 1.25},
    )
    conflicting_path = tmp_path / "conflict.yaml"
    conflicting_path.write_text(
        conflicting.read_text(encoding="utf-8"), encoding="utf-8",
    )
    result = runner.invoke(observe_app, ["publish-manifest", str(conflicting_path)])

    assert result.exit_code == 1
    assert "generation conflict" in (result.stdout + (result.stderr or ""))


def test_publish_manifest_rejects_malformed_yaml(runner: CliRunner, tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- just: a list, not a mapping\n", encoding="utf-8")

    result = runner.invoke(observe_app, ["publish-manifest", str(bad)])
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "mapping" in combined.lower() or "invalid" in combined.lower()


def test_publish_manifest_rejects_invalid_field_values(runner: CliRunner, tmp_path: Path):
    """out-of-range allocation_pct exercises ``build_manifest`` validators."""
    spec_path = _write_manifest_yaml(tmp_path, allocation_pct=1.5)
    result = runner.invoke(observe_app, ["publish-manifest", str(spec_path)])

    assert result.exit_code == 1
    combined = result.stdout + (result.stderr or "")
    assert "allocation_pct" in combined or "invalid manifest" in combined
