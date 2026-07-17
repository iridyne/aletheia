"""Spec: trust-boundary policy stays aligned with governance and CI."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from tests.specs.spec_meta import SpecClaim, spec_claim

_ROOT = Path(__file__).resolve().parents[2]
_POLICY = _ROOT / "docs/governance/policies/trust_boundaries.yaml"
_CONTRACT = "trust_boundary_policy"


def _policy() -> dict:
    return yaml.safe_load(_POLICY.read_text(encoding="utf-8"))


def _policy_paths() -> set[str]:
    return {
        path.rstrip("/*")
        for entry in _policy()["trust_boundaries"]
        for path in entry["paths"]
    }


def _agents_md_paths() -> set[str]:
    text = (_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    match = re.search(
        r"- Trust boundaries[^\n]*\n((?:  [^\n]+\n){0,4})",
        text,
    )
    assert match, "AGENTS.md Trust boundaries bullet not found"
    return {
        token.strip("/").strip()
        for token in re.findall(r"`([^`]+)`", match.group(1))
    }


def _governance_paths() -> set[str]:
    text = (
        _ROOT / "docs/governance/policies/trust_and_change_control.md"
    ).read_text(encoding="utf-8")
    match = re.search(
        r"### 6\.1 Controlled Modules.*?```text\n(.*?)```",
        text,
        re.DOTALL,
    )
    assert match, "trust_and_change_control.md §6.1 fenced block not found"
    return {
        line.strip().rstrip("/")
        for line in match.group(1).splitlines()
        if line.strip()
    }


@spec_claim(
    SpecClaim(
        "TB-S01",
        _CONTRACT,
        "§policy",
        "L2a",
        "Machine-readable trust-boundary policy exists with schema v1.",
    )
)
def test_policy_file_exists_with_schema_v1():
    doc = _policy()
    assert doc["schema_version"] == 1
    assert isinstance(doc["trust_boundaries"], list)
    assert doc["trust_boundaries"]


@spec_claim(
    SpecClaim(
        "TB-S02",
        _CONTRACT,
        "§policy",
        "L2a",
        "Each controlled boundary names direct focused verification commands.",
    )
)
def test_policy_has_direct_focused_commands():
    for entry in _policy()["trust_boundaries"]:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", entry["id"])
        assert entry["paths"]
        commands = entry["focused_commands"]
        assert commands
        assert all(command.startswith("uv run pytest ") for command in commands)
        assert all("task" not in command.lower() for command in commands)


@spec_claim(
    SpecClaim(
        "TB-S03",
        _CONTRACT,
        "§policy",
        "L2a",
        "Policy paths and focused test files resolve in the repository.",
    )
)
def test_policy_paths_and_tests_resolve_on_disk():
    for entry in _policy()["trust_boundaries"]:
        for path in entry["paths"]:
            base = path.split("*", 1)[0].rstrip("/")
            assert (_ROOT / base).exists(), path
        for command in entry["focused_commands"]:
            test_paths = [
                token for token in command.split() if token.startswith("tests/")
            ]
            assert test_paths
            for path in test_paths:
                assert (_ROOT / path).exists(), path


@spec_claim(
    SpecClaim(
        "TB-S04",
        _CONTRACT,
        "§drift",
        "L2a",
        "AGENTS.md and machine-readable policy carry the same controlled paths.",
    )
)
def test_agents_md_matches_policy():
    assert _agents_md_paths() == _policy_paths()


@spec_claim(
    SpecClaim(
        "TB-S05",
        _CONTRACT,
        "§drift",
        "L2a",
        "Governance §6.1 and machine-readable policy carry the same paths.",
    )
)
def test_governance_matches_policy():
    assert _governance_paths() == _policy_paths()


@spec_claim(
    SpecClaim(
        "TB-S06",
        _CONTRACT,
        "§ci",
        "L2b",
        "PR CI runs executable specs without a repository development-work gate.",
    )
)
def test_ci_runs_specs_without_repository_work_gate():
    workflow = (_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    obsolete_test_name = "test_agent_" + "workflow"
    assert "uv run pytest tests/specs/" in workflow
    assert "agent workflow" not in workflow.lower()
    assert obsolete_test_name not in workflow
    assert ".agent/" not in workflow
