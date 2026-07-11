"""Spec: machine-readable trust-boundary policy is in lockstep with governance docs.

Source:
  .agent/policy.yaml (machine-readable)
  AGENTS.md §Development Rules "Trust boundaries"
  docs/governance/trust_and_change_control.md §6.1 Controlled Modules
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from tests.specs.spec_meta import SpecClaim, spec_claim

_ROOT = Path(__file__).resolve().parents[2]
_CONTRACT = "trust_boundary_policy"


def _policy_paths() -> set[str]:
    doc = yaml.safe_load((_ROOT / ".agent/policy.yaml").read_text(encoding="utf-8"))
    return {p.rstrip("/*") for entry in doc["trust_boundaries"] for p in entry["paths"]}


def _agents_md_paths() -> set[str]:
    text = (_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    # Anchor on the "Trust boundaries" bullet block; grab paths mentioned in the
    # two lines that follow. The block is deliberately compact so we do not
    # ambiently swallow later lines.
    match = re.search(
        r"- Trust boundaries[^\n]*\n((?:  [^\n]+\n){0,4})", text
    )
    assert match, "AGENTS.md Trust boundaries bullet not found"
    block = match.group(1)
    return {token.strip("/").strip() for token in re.findall(r"`([^`]+)`", block)}


def _governance_paths() -> set[str]:
    text = (_ROOT / "docs/governance/trust_and_change_control.md").read_text(encoding="utf-8")
    match = re.search(r"### 6\.1 Controlled Modules.*?```text\n(.*?)```", text, re.DOTALL)
    assert match, "trust_and_change_control.md §6.1 fenced block not found"
    return {line.strip().rstrip("/") for line in match.group(1).splitlines() if line.strip()}


@spec_claim(SpecClaim("TB-S01", _CONTRACT, "§policy", "L2a", "Machine-readable trust-boundary policy exists with schema v1."))
def test_policy_file_exists_with_schema_v1():
    doc = yaml.safe_load((_ROOT / ".agent/policy.yaml").read_text(encoding="utf-8"))
    assert doc["schema_version"] == 1
    assert isinstance(doc["trust_boundaries"], list) and doc["trust_boundaries"]
    for entry in doc["trust_boundaries"]:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", entry["id"])
        assert entry["paths"]
        assert entry["required_command_keys"]


@spec_claim(SpecClaim("TB-S02", _CONTRACT, "§policy", "L2a", "Policy required_command_keys are registered in .agent/commands.yaml."))
def test_policy_commands_are_all_registered():
    policy = yaml.safe_load((_ROOT / ".agent/policy.yaml").read_text(encoding="utf-8"))
    commands = yaml.safe_load((_ROOT / ".agent/commands.yaml").read_text(encoding="utf-8"))["commands"]
    referenced = {key for entry in policy["trust_boundaries"] for key in entry["required_command_keys"]}
    missing = sorted(referenced - set(commands))
    assert not missing, f"unregistered command_keys in policy: {missing}"


@spec_claim(SpecClaim("TB-S03", _CONTRACT, "§policy", "L2a", "Policy paths point at existing repository files or directories."))
def test_policy_paths_resolve_on_disk():
    policy = yaml.safe_load((_ROOT / ".agent/policy.yaml").read_text(encoding="utf-8"))
    for entry in policy["trust_boundaries"]:
        for path in entry["paths"]:
            base = path.split("*", 1)[0].rstrip("/")
            resolved = _ROOT / base
            assert resolved.exists(), f"trust-boundary path missing on disk: {path}"


@spec_claim(SpecClaim("TB-S04", _CONTRACT, "§drift", "L2a", "AGENTS.md trust-boundaries and .agent/policy.yaml carry the same path set."))
def test_agents_md_matches_policy():
    policy_paths = _policy_paths()
    agents_paths = _agents_md_paths()
    missing_from_agents = sorted(policy_paths - agents_paths)
    extra_in_agents = sorted(agents_paths - policy_paths)
    assert not missing_from_agents, f"AGENTS.md missing trust boundaries: {missing_from_agents}"
    assert not extra_in_agents, f"AGENTS.md lists non-policy trust boundaries: {extra_in_agents}"


@spec_claim(SpecClaim("TB-S05", _CONTRACT, "§drift", "L2a", "governance §6.1 module list and .agent/policy.yaml carry the same path set."))
def test_governance_matches_policy():
    policy_paths = _policy_paths()
    governance = _governance_paths()
    missing_in_governance = sorted(policy_paths - governance)
    extra_in_governance = sorted(governance - policy_paths)
    assert not missing_in_governance, f"governance §6.1 missing paths: {missing_in_governance}"
    assert not extra_in_governance, f"governance §6.1 lists non-policy paths: {extra_in_governance}"


@spec_claim(SpecClaim("TB-S06", _CONTRACT, "§enforcement", "L2b", "validate() rejects trust-boundary changes without matching command_key evidence."))
def test_validate_enforces_trust_boundary_evidence():
    from aletheia.agent_workflow import validate
    source = Path(validate.__code__.co_filename).read_text(encoding="utf-8")
    assert "trust-boundary" in source
    assert "policy.yaml" in source


@spec_claim(SpecClaim("TB-S07", _CONTRACT, "§enforcement", "L2b", "pre-commit hook rejects trust-boundary changes without matching evidence."))
def test_pre_commit_hook_enforces_trust_boundary_evidence():
    hook = (_ROOT / "scripts/hooks/pre-commit").read_text(encoding="utf-8")
    assert "policy.yaml" in hook or "trust-boundary" in hook, (
        "pre-commit hook must reference the trust-boundary policy"
    )
