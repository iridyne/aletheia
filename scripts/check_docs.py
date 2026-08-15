"""Lightweight documentation-governance checks.

This intentionally checks only cheap, durable invariants. It is not a
documentation database, status tracker, or generated-site pipeline.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_PROPOSAL_STATUSES = {
    "draft",
    "accepted",
    "rejected",
    "implemented",
    "superseded",
}
FROZEN_MARKERS = {
    Path("docs/archive/legacy/issues/README.md"): "FORENSIC-FROZEN",
    Path("docs/archive/legacy/plans/README.md"): "LEGACY-FROZEN",
    Path("docs/archive/legacy/status/README.md"): "SNAPSHOT-FROZEN",
    Path("docs/archive/legacy/ai-context/README.md"): "HISTORICAL-NO-TRACKING",
}
REQUIRED_CURRENT_FILES = {
    Path("docs/governance/policies/documentation_governance.md"),
    Path("docs/design/contracts/README.md"),
    Path("docs/design/proposals/README.md"),
    Path("docs/product/CAPABILITY_GROUND_TRUTH.md"),
    Path("docs/product/ACTIVE_RUNTIME_SCOPE.md"),
}
RETIRED_CONTRACT_PATHS = {
    Path("docs/design/contracts/learning_evolution_contract.md"),
    Path("docs/design/contracts/learning_observability_contract.md"),
    Path("docs/design/contracts/promotion_gates_contract.md"),
}
LINK_PATTERN = re.compile(r"!?\[[^\]]*]\(([^)]+)\)")
CURRENT_DOC_PREFIXES = (
    "docs/governance/",
    "docs/product/definition/",
    "docs/product/gates/",
    "docs/product/experiments/",
    "docs/product/capability-integrity/",
    "docs/design/contracts/",
    "docs/design/decisions/",
    "docs/design/proposals/",
    "docs/design/data/",
    "docs/design/api/",
    "docs/design/architecture/",
    "docs/design/operator/",
    "docs/design/frontend/",
    "docs/design/market-systems/",
    "docs/operations/",
)
CURRENT_DOC_FILES = {
    "AGENTS.md",
    "docs/README.md",
    "docs/product/README.md",
    "docs/product/SCOPE.md",
    "docs/product/ACTIVE_RUNTIME_SCOPE.md",
    "docs/product/CAPABILITY_GROUND_TRUTH.md",
    "docs/product/capability-integrity/README.md",
    "docs/design/README.md",
    "docs/design/process/SDD_METHODOLOGY.md",
}


def repository_markdown_files() -> list[Path]:
    """Return repository Markdown, including new files, but not ignored files."""
    result = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
            "*.md",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    paths = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        path = Path(raw.decode("utf-8"))
        if path.parts[:2] == ("docs", "monitoring"):
            continue
        paths.append(path)
    return sorted(set(paths))


def current_authority_markdown_files() -> list[Path]:
    return [
        path
        for path in repository_markdown_files()
        if path.as_posix() in CURRENT_DOC_FILES
        or path.as_posix().startswith(CURRENT_DOC_PREFIXES)
    ]


def _link_target(raw: str) -> str:
    target = raw.strip()
    if target.startswith("<") and ">" in target:
        return target[1 : target.index(">")]
    return target.split(maxsplit=1)[0]


def broken_relative_links(markdown_files: list[Path]) -> list[str]:
    failures: list[str] = []
    for relative in markdown_files:
        source = ROOT / relative
        if not source.is_file():
            continue
        text = source.read_text(encoding="utf-8")
        for raw_target in LINK_PATTERN.findall(text):
            target = unquote(_link_target(raw_target))
            if (
                not target
                or target.startswith(("#", "/", "//"))
                or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target)
            ):
                continue
            path_text = target.split("#", 1)[0].split("?", 1)[0]
            if not path_text:
                continue
            resolved = (source.parent / path_text).resolve()
            try:
                resolved.relative_to(ROOT)
            except ValueError:
                failures.append(f"{relative}: link escapes repository: {target}")
                continue
            if not resolved.exists():
                failures.append(f"{relative}: missing link target: {target}")
    return failures


def proposal_metadata_failures() -> list[str]:
    failures: list[str] = []
    proposal_dir = ROOT / "docs/design/proposals"
    for path in sorted(proposal_dir.glob("*.md")):
        if path.name in {"README.md", "template.md"}:
            continue
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            failures.append(f"{path.relative_to(ROOT)}: missing YAML frontmatter")
            continue
        try:
            frontmatter = text.split("---\n", 2)[1]
        except IndexError:
            failures.append(f"{path.relative_to(ROOT)}: unclosed YAML frontmatter")
            continue
        proposal_id = re.search(r"^id:\s*(\S+)\s*$", frontmatter, re.MULTILINE)
        status = re.search(r"^status:\s*(\S+)\s*$", frontmatter, re.MULTILINE)
        if not proposal_id:
            failures.append(f"{path.relative_to(ROOT)}: missing proposal id")
        if not status or status.group(1) not in ALLOWED_PROPOSAL_STATUSES:
            failures.append(
                f"{path.relative_to(ROOT)}: invalid proposal status"
            )
    return failures


def governance_failures() -> list[str]:
    failures: list[str] = []
    for relative in REQUIRED_CURRENT_FILES:
        if not (ROOT / relative).is_file():
            failures.append(f"missing current authority: {relative}")
    for relative, marker in FROZEN_MARKERS.items():
        path = ROOT / relative
        if not path.is_file() or marker not in path.read_text(encoding="utf-8"):
            failures.append(f"{relative}: missing frozen-boundary marker {marker}")
    duplicate_scope = ROOT / "docs/product/definition/ACTIVE_RUNTIME_SCOPE.md"
    if duplicate_scope.exists():
        failures.append(
            "docs/product/definition/ACTIVE_RUNTIME_SCOPE.md: duplicate authority"
        )
    for relative in RETIRED_CONTRACT_PATHS:
        if (ROOT / relative).exists():
            failures.append(f"{relative}: future or retired design in contracts/")
    return failures


def semantic_truth_failures() -> list[str]:
    """Guard a few cross-document truths that structural link checks cannot see."""
    failures: list[str] = []

    registry_path = ROOT / "aletheia/learning/registry.py"
    registry_tree = ast.parse(registry_path.read_text(encoding="utf-8"))
    measured_paths: list[str] = []
    for node in ast.walk(registry_tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "LearningPath":
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        measurement_supported = next(
            (
                keyword.value
                for keyword in node.keywords
                if keyword.arg == "measurement_supported"
            ),
            None,
        )
        if (
            isinstance(measurement_supported, ast.Constant)
            and measurement_supported.value is True
        ):
            measured_paths.append(str(node.args[0].value))
    if measured_paths != ["universe_family_calibration", "ranker_case_score"]:
        failures.append(
            "aletheia/learning/registry.py: expected exactly two "
            "measurement-supported paths (universe_family_calibration and "
            f"ranker_case_score), found {measured_paths}"
        )

    current_truth = {
        Path("docs/design/contracts/learning_contract.md"):
            "UFC remains the only Paper-authority-capable path;",
        Path("docs/product/CAPABILITY_GROUND_TRUTH.md"):
            "LN-08 | Learned guard generation and observation",
        Path("docs/product/capability-integrity/CAPABILITY_INVENTORY.md"):
            "LN-08 | Learned guard generation and observation",
        Path("docs/product/capability-integrity/FACT_CARDS.md"):
            "no learned block/penalty",
        Path("docs/product/experiments/EXPERIMENT_MVP_GATE.md"):
            "retired historical compatibility pointer",
        Path("docs/product/experiments/EXPERIMENT_BACKLOG.md"):
            "retired historical compatibility pointer",
    }
    for relative, required in current_truth.items():
        text = (ROOT / relative).read_text(encoding="utf-8")
        if required not in text:
            failures.append(f"{relative}: missing semantic truth marker: {required}")

    legacy_script = (ROOT / "scripts/mvp_gate_check.py").read_text(encoding="utf-8")
    if "LEGACY-ONLY" not in legacy_script:
        failures.append(
            "scripts/mvp_gate_check.py: historical Round 1 gate is not marked LEGACY-ONLY"
        )

    stale_phrases = {
        "Learned guard generation and enforcement",
        "the two supported paths",
        "GitHub issue #489",
        "[VISION.md](VISION.md)",
    }
    truth_files = [
        ROOT / "docs/product/SCOPE.md",
        ROOT / "docs/product/CAPABILITY_GROUND_TRUTH.md",
        ROOT / "docs/product/gates/METRICS.md",
        ROOT / "docs/product/gates/PHASE_GATES.md",
        ROOT / "docs/product/experiments/EXPERIMENT_PROTOCOL.md",
        ROOT / "docs/design/contracts/learning_contract.md",
        ROOT / "docs/design/process/DOC_ATLAS_BY_DOMAIN.md",
        ROOT / "docs/design/data/data_utilization.md",
    ]
    for path in truth_files:
        text = path.read_text(encoding="utf-8")
        for phrase in stale_phrases:
            if phrase in text:
                failures.append(
                    f"{path.relative_to(ROOT)}: stale semantic authority: {phrase}"
                )

    retired_gate_phrases = {
        "Phase 2A - 2B",
        "direction_affinity=\"short\"",
        "SELECT COUNT(*) FROM strategy_edits",
    }
    phase_gates = (ROOT / "docs/product/gates/PHASE_GATES.md").read_text(
        encoding="utf-8"
    )
    for phrase in retired_gate_phrases:
        if phrase in phase_gates:
            failures.append(
                "docs/product/gates/PHASE_GATES.md: retired strategy gate remains: "
                + phrase
            )

    return failures


def main() -> int:
    failures = []
    failures.extend(governance_failures())
    failures.extend(proposal_metadata_failures())
    failures.extend(broken_relative_links(current_authority_markdown_files()))
    failures.extend(semantic_truth_failures())
    if failures:
        print("Documentation governance check failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("Documentation governance check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
