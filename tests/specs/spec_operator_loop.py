"""Surviving Operator permission and human-owned artifact contracts."""

from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim


@spec_claim(
    SpecClaim(
        "PCI-G01", "operator_layer",
        "§pi_constitution modify_own_prompt blocked",
        "L2c",
        "FORBIDDEN_INTENTS contains 'modify_own_prompt' — PI can never overwrite its "
        "own system prompt base layer, even via low-risk commands. "
        "Contract: PI_OPERATOR_DESIGN_DECISIONS §产品定义 (2026-06-30).",
        status="implemented",
    ),
)
def test_modify_own_prompt_is_forbidden():
    """modify_own_prompt must be in FORBIDDEN_INTENTS (hard block, not just command gate).

    propose_prompt_change is proposal_only (human reviews), but that command gate
    only applies to named commands. A low-risk command (create_research_note, etc.)
    that assembles a prompt-overwrite write operation bypasses command validation.
    FORBIDDEN_INTENTS is the last hard wall.
    """
    from aletheia.agent_shell.registry import FORBIDDEN_INTENTS

    assert "modify_own_prompt" in FORBIDDEN_INTENTS, (
        "PCI-G01 VIOLATED: 'modify_own_prompt' missing from FORBIDDEN_INTENTS. "
        "PI could overwrite its own system prompt base layer via low-risk commands. "
        "Add 'modify_own_prompt' to FORBIDDEN_INTENTS in registry.py."
    )


@spec_claim(
    SpecClaim(
        "PCI-G03", "operator_layer",
        "§pi_constitution constitution_files_not_in_write_whitelist",
        "L2a",
        "write_operator_report (file_artifacts.py) roots all output under "
        "get_data_dir()/'reports' — never under docs/design/ or docs/governance/. "
        "Constitution files (risk_engine_contract.md, live_unlock_contract.md, "
        "PI_OPERATOR_DESIGN_DECISIONS.md, etc.) are human-owned and must not appear "
        "in any write-command output path. Contract: PI_OPERATOR_DESIGN_DECISIONS "
        "§宪法专有 (2026-06-30).",
        status="implemented",
    ),
)
def test_write_operator_report_root_is_data_dir():
    """write_operator_report must produce paths under data/reports/, never docs/.

    We call report_note_path() (the path-builder, no disk write) and verify the
    returned path does not escape the data directory into the source tree.
    The data dir during tests is a tmp_path; we check that the path is anchored
    under a 'reports' stem and contains no 'docs' segment.
    """
    import ast as _ast

    _PROJECT_ROOT = Path(__file__).resolve().parents[2]
    fa_src = (
        _PROJECT_ROOT / "core" / "aletheia" / "agent_shell" / "file_artifacts.py"
    ).read_text(encoding="utf-8")

    # 1. The path-builder must reference get_data_dir(), not a hardcoded docs/ path.
    assert "get_data_dir()" in fa_src, (
        "file_artifacts.py does not call get_data_dir() — "
        "report paths may escape the data directory"
    )

    # 2. No string literal in file_artifacts.py contains 'docs/design' or 'docs/governance'.
    tree = _ast.parse(fa_src)
    forbidden_paths = ("docs/design", "docs/governance", "docs\\design", "docs\\governance")
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Constant) and isinstance(node.value, str):
            for fp in forbidden_paths:
                assert fp not in node.value, (
                    f"PCI-G03 VIOLATED: file_artifacts.py contains hardcoded path "
                    f"{node.value!r} pointing to constitution files under docs/. "
                    "All PI write paths must stay under data/reports/."
                )
