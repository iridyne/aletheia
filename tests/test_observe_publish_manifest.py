"""G2 tests: ``aletheia observe publish-manifest`` CLI (#1072 v1 design §5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aletheia.cli.observe_cmd import observe_app


# The manifest fixtures assert against a fixed timeline; the Observation root must
# begin no later than the manifest's effective_from or publication is refused with
# manifest_effective_from_before_generation.
_OBSERVATION_ID = "ufc:g2-test:epoch-1:0"
_TIMELINE_START = "2026-08-18T00:00:00+00:00"
_EFFECTIVE_FROM = "2026-08-19T00:00:00+00:00"
_EFFECTIVE_UNTIL = "2026-08-20T00:00:00+00:00"
_COLLECT_UNTIL = "2026-08-21T00:00:00+00:00"


@pytest.fixture(autouse=True)
def lab_authority() -> dict[str, str]:
    """The observe CLI refuses to run without lab experiment authority.

    These tests predate that requirement (#1072 §5) and never set it, so every
    command exited 1 with "experiment authority unavailable; refusing legacy
    fallback" — the CLI was correct and the fixture was incomplete.
    """
    from extensions.observation_lab.canonical import set_authority_source

    set_authority_source("lab", actor="test", reason="observe CLI manifest tests")

    # A manifest is published against an existing Observation generation root;
    # without one the CLI fails with observation_generation_root_unavailable. These
    # tests only ever wrote the YAML, so they asserted a CLI path whose precondition
    # they never established.
    from extensions.observation_lab.canonical import register_observation_spec

    root = register_observation_spec({
        "observation_id": _OBSERVATION_ID,
        "path_id": "universe_family_calibration",
        "filter_name": "g2-manifest-test",
        "stage": "shadow",
        "scanner_family": "momentum",
        "direction": "long",
        "market_type": "perp",
        "horizon": "4h",
        "control": {"value": 1.0},
        "treatment": {"value": 2.0},
        "behavior_policy_epoch_ref": "epoch-1",
        "gates": {"min_samples_per_arm": 1, "min_market_days": 0},
    }, started_at=_TIMELINE_START, collect_until=_COLLECT_UNTIL)

    # Publication cross-checks the manifest against the generation contract on
    # several fields (spec hash, behavior contract hash, effective window). Deriving
    # them from the persisted contract keeps the fixture correct by construction —
    # chasing each mismatch one literal at a time is how this test drifted four
    # contract revisions behind.
    from extensions.observation_lab.db import get_lab_db

    # Every field publication cross-checks, read straight off the contract.
    mirrored = (
        "assignment_policy", "assignment_salt", "assignment_unit_key_template",
        "assignment_namespace_key", "assignment_hash_version",
        "behavior_policy_epoch_ref", "behavior_contract_hash", "allocation_pct",
        "target_horizon", "outcome_contract_version", "metric_version",
    )
    lab = get_lab_db()
    row = lab.execute(
        f"SELECT {', '.join(mirrored)}, effective_until "
        "FROM observation_generation_contracts WHERE observation_id=? AND generation=?",
        (_OBSERVATION_ID, 1),
    ).fetchone()
    assert row is not None, "generation contract row was not persisted"
    # Only fields the manifest schema actually accepts; the contract carries more
    # (metric_version, outcome_contract_version) that belong to the generation, not
    # the manifest. Intersecting with the real signature avoids maintaining a second
    # copy of the field list here.
    import inspect

    from extensions.observation_lab.manifest import build_manifest

    accepted = set(inspect.signature(build_manifest).parameters)
    contract = {
        name: row[index] for index, name in enumerate(mirrored) if name in accepted
    }
    contract["spec_hash"] = str(root["spec_hash"])
    contract["effective_from"] = _EFFECTIVE_FROM
    contract["effective_until"] = str(row[len(mirrored)])
    return contract


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _write_manifest_yaml(tmp_path: Path, contract: dict[str, str] | None = None, **overrides) -> Path:
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
        "effective_from": _EFFECTIVE_FROM,
        # A generation carrying an effective_until requires the manifest to declare
        # one no later than it; an open-ended manifest is refused outright.
        "effective_until": _EFFECTIVE_UNTIL,
        "deterministic_baseline": {"multiplier": 1.0},
        "unavailable_fallback": "baseline",
    }
    if contract:
        base.update(contract)
    base.update(overrides)
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(base), encoding="utf-8")
    return path


def test_publish_manifest_writes_row_and_reports_hash(runner: CliRunner, tmp_path: Path, lab_authority: dict[str, str]):
    from extensions.observation_lab.manifest import load_active_manifests

    spec_path = _write_manifest_yaml(tmp_path, contract=lab_authority)
    result = runner.invoke(observe_app, ["publish-manifest", str(spec_path), "--json"])

    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["newly_inserted"] is True
    assert payload["observation_id"] == "ufc:g2-test:epoch-1:0"
    assert payload["generation"] == 1
    assert len(payload["manifest_hash"]) == 64

    active = load_active_manifests(now="2026-08-19T12:00:00+00:00")
    assert any(m.manifest_hash == payload["manifest_hash"] for m in active)


def test_publish_manifest_is_idempotent_on_same_content(runner: CliRunner, tmp_path: Path, lab_authority: dict[str, str]):
    spec_path = _write_manifest_yaml(tmp_path, contract=lab_authority)
    first = runner.invoke(observe_app, ["publish-manifest", str(spec_path), "--json"])
    second = runner.invoke(observe_app, ["publish-manifest", str(spec_path), "--json"])

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert json.loads(first.stdout)["newly_inserted"] is True
    assert json.loads(second.stdout)["newly_inserted"] is False


def test_publish_manifest_rejects_generation_conflict(runner: CliRunner, tmp_path: Path, lab_authority: dict[str, str]):
    """Same (observation_id, generation) with different content = exit 1."""
    first_path = _write_manifest_yaml(tmp_path, contract=lab_authority)
    first = runner.invoke(observe_app, ["publish-manifest", str(first_path), "--json"])
    assert first.exit_code == 0, first.stdout + first.stderr

    # Different treatment payload → different manifest_hash, same
    # (observation_id, generation) → ManifestGenerationConflict.
    conflicting = _write_manifest_yaml(
        tmp_path, contract=lab_authority, treatment_payload={"multiplier": 1.25},
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


@pytest.mark.parametrize(
    ("effective_from", "effective_until"),
    [
        ("not-a-timestamp", None),
        ("2026-08-19T00:00:00", None),
        ("2026-08-19T00:00:00+00:00", "2026-08-19T00:00:00+00:00"),
    ],
)
def test_publish_manifest_rejects_invalid_effective_interval_without_write(
    runner: CliRunner, tmp_path: Path, effective_from: str, effective_until: str | None,
):
    from extensions.observation_lab.db import get_lab_db

    spec_path = _write_manifest_yaml(
        tmp_path,
        effective_from=effective_from,
        effective_until=effective_until,
    )
    result = runner.invoke(observe_app, ["publish-manifest", str(spec_path)])

    assert result.exit_code == 1
    assert get_lab_db().execute(
        "SELECT COUNT(*) FROM execution_manifests"
    ).fetchone()[0] == 0
