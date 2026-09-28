"""Focused regression tests for cross-document Product/Learning truth."""

from pathlib import Path

import scripts.check_docs as check_docs
from scripts.check_docs import (
    contract_code_path_failures,
    semantic_truth_failures,
    silent_failure_fallback_failures,
    timestamp_unit_suffix_failures,
    vacuous_test_failures,
)


def test_current_document_authorities_match_learning_registry() -> None:
    assert semantic_truth_failures() == []


def test_agents_routes_pr_lifecycle_authority() -> None:
    agents = Path("AGENTS.md").read_text(encoding="utf-8")
    assert (
        "[@PR_REVIEW_LIFECYCLE.md](docs/operations/development/agent-guides/"
        "PR_REVIEW_LIFECYCLE.md)"
    ) in agents


def test_no_runbook_fabricates_position_outcome_evidence() -> None:
    """No runbook may hand an operator SQL that grants outcome eligibility.

    ``exit_fill_verified`` and ``outcome_eligible`` default to false and
    ``exit_price_source`` to ``unavailable`` precisely because unproven means
    ineligible.  A runbook that UPDATEs them to true manufactures, from the
    operations side, a fact only the fill path may write -- and the fabricated
    outcome then flows into Risk and Learning attribution.  Diagnostic SELECTs
    over the same columns stay allowed.
    """
    granting = ("outcome_eligible", "exit_fill_verified")
    offenders: list[str] = []
    for runbook in Path("docs/operations/runbooks").rglob("*.md"):
        for block in runbook.read_text(encoding="utf-8").split("```")[1::2]:
            lowered = block.lower()
            if "update positions" not in lowered:
                continue
            named = [column for column in granting if column in lowered]
            if named:
                offenders.append(f"{runbook}: UPDATE positions sets {named}")
    assert offenders == [], (
        "runbook grants Position outcome eligibility by raw SQL: "
        + "; ".join(offenders)
    )


def test_contract_code_path_failures_reports_missing_and_ignores_valid() -> None:
    contracts = Path("docs/design/contracts")
    probe = contracts / "_contract_path_probe.md"
    try:
        probe.write_text(
            "`core/aletheia/agent_runtime/scheduler.py` exists\n"
            "`core/aletheia/nonexistent_module.py` is broken\n"
            "`learning/registry.py` is package-relative shorthand\n",
            encoding="utf-8",
        )
        failures = contract_code_path_failures()
    finally:
        probe.unlink(missing_ok=True)
    assert (
        "docs/design/contracts/_contract_path_probe.md:2: "
        "missing contract code path: core/aletheia/nonexistent_module.py"
    ) in failures
    assert not any(
        "core/aletheia/agent_runtime/scheduler.py" in failure
        or "learning/registry.py" in failure
        for failure in failures
    )


def test_timestamp_unit_suffix_failures_flags_contradiction_and_ignores_correct() -> None:
    source_root = Path("core/aletheia")
    probe = source_root / "_timestamp_unit_probe.py"
    try:
        probe.write_text(
            "bogus_timeout_ms = raw_value / 1000\n"
            "bogus_delay_sec = raw_value * 1000\n"
            "bogus_ok_sec = raw_value / 1000\n"
            "bogus_ok_ms = raw_value * 1000\n",
            encoding="utf-8",
        )
        failures = timestamp_unit_suffix_failures()
    finally:
        probe.unlink(missing_ok=True)
    assert (
        "core/aletheia/_timestamp_unit_probe.py:1: "
        "timestamp unit contradiction: bogus_timeout_ms = ... / 1000"
    ) in failures
    assert (
        "core/aletheia/_timestamp_unit_probe.py:2: "
        "timestamp unit contradiction: bogus_delay_sec = ... * 1000"
    ) in failures
    assert not any("bogus_ok_" in failure for failure in failures)


def test_vacuous_test_failures_flags_no_assertion_and_ignores_real_assertion() -> None:
    probe = Path("tests") / "_vacuous_probe.py"
    try:
        probe.write_text(
            "def test_bogus_vacuous():\n"
            "    x = 1 + 1\n"
            "def test_bogus_real_assert():\n"
            "    assert 1 + 1 == 2\n",
            encoding="utf-8",
        )
        failures = vacuous_test_failures()
    finally:
        probe.unlink(missing_ok=True)
    assert (
        "tests/_vacuous_probe.py:1: vacuous test (no assertion): test_bogus_vacuous"
    ) in failures
    assert not any("test_bogus_real_assert" in failure for failure in failures)


def test_silent_failure_fallback_flags_broad_zero_and_ignores_none() -> None:
    source_root = Path("core/aletheia")
    probe = source_root / "_silent_failure_probe.py"
    try:
        probe.write_text(
            "def _bogus_broad_zero():\n"
            "    try:\n"
            "        return 'x'\n"
            "    except Exception:\n"
            "        return 0\n"
            "def _bogus_ok_none():\n"
            "    try:\n"
            "        return 'x'\n"
            "    except Exception:\n"
            "        return None\n",
            encoding="utf-8",
        )
        failures = silent_failure_fallback_failures()
    finally:
        probe.unlink(missing_ok=True)
    assert (
        "core/aletheia/_silent_failure_probe.py:4: "
        "silent failure fallback: broad except returns falsy '0'"
    ) in failures
    assert not any("_bogus_ok_none" in failure for failure in failures)


def test_skip_without_reason_is_reported_and_strict_xfail_is_not() -> None:
    """The fifth gate: reason-less skips fail; strict xfail with reason passes."""
    from scripts.check_docs import skip_xfail_and_data_guard_failures

    probe = Path("tests") / "_skip_xfail_probe.py"
    try:
        probe.write_text(
            "import pytest\n"
            "\n"
            "@pytest.mark.skip\ndef test_bad_unconditional_skip():\n"
            "    assert True\n"
            "\n"
            "@pytest.mark.xfail(reason='known gap', strict=True)\n"
            "def test_good_strict_xfail():\n"
            "    assert False\n",
            encoding="utf-8",
        )
        failures = skip_xfail_and_data_guard_failures()
    finally:
        probe.unlink(missing_ok=True)
    assert any(
        "tests/_skip_xfail_probe.py:3: unconditional @pytest.mark.skip "
        "on test_bad_unconditional_skip" in failure
        for failure in failures
    )
    assert not any("test_good_strict_xfail" in failure for failure in failures)


def test_claim_section_matcher_accepts_both_heading_numberings() -> None:
    """``## 2 Foo`` and ``## §1 file location`` are both live in the contracts."""
    from scripts.check_docs import _claim_section_present

    document = "# Title\n## 2. Provider lifecycle\n## §1 文件位置\n### Cache\n"
    assert _claim_section_present("§2 provider lifecycle", document)
    assert _claim_section_present("§1 anything", document)
    assert _claim_section_present("Cache", document)
    assert not _claim_section_present("§9 invented", document)
    # A word that appears only in the body, never as a heading, is not a section.
    assert not _claim_section_present("Title-adjacent prose", document)


def test_claim_anchor_gate_rejects_unregistered_and_proposal_only_contracts() -> None:
    """The sixth gate: an executable claim may not invent its own authority."""
    from scripts.check_docs import spec_claim_anchor_failures

    probe = Path("tests/specs") / "spec__anchor_probe.py"
    try:
        probe.write_text(
            "from tests.specs.spec_meta import SpecClaim, spec_claim\n"
            "\n"
            "@spec_claim(SpecClaim('PROBE-1', 'no_such_contract', '§1 x', 'L2a', 'i'))\n"
            "def test_probe_unregistered():\n"
            "    assert True\n"
            "\n"
            "@spec_claim(SpecClaim('PROBE-2', 'agent_contracts', '§1 scheduler', 'L2a', 'i'))\n"
            "def test_probe_good():\n"
            "    assert True\n",
            encoding="utf-8",
        )
        failures = spec_claim_anchor_failures()
    finally:
        probe.unlink(missing_ok=True)
    assert any(
        "tests/specs/spec__anchor_probe.py:3: claim PROBE-1 names unregistered "
        "contract 'no_such_contract'" in failure
        for failure in failures
    )
    assert not any("PROBE-2" in failure for failure in failures)


def test_claim_anchor_gate_is_green_on_the_current_tree() -> None:
    from scripts.check_docs import spec_claim_anchor_failures

    assert spec_claim_anchor_failures() == []


def test_capability_inventory_citations_are_checked(monkeypatch, tmp_path) -> None:
    """The product-promise authority is inside the code-path gate's scope."""
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "present.py").write_text("", encoding="utf-8")
    inventory = tmp_path / "CAPABILITY_INVENTORY.md"
    inventory.write_text(
        "`core/present.py` is the live evidence\n"
        "`core/gone.py` was deleted with its mechanism\n"
        "`learning/registry.py` is package-relative shorthand\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    monkeypatch.setattr(check_docs, "TOP_LEVEL_DIRS", {"core"})
    monkeypatch.setattr(check_docs, "CODE_PATH_DOCS", ("CAPABILITY_INVENTORY.md",))

    failures = check_docs.contract_code_path_failures()

    assert failures == ["CAPABILITY_INVENTORY.md:2: missing contract code path: core/gone.py"]


def test_authority_doc_code_paths_are_green_on_the_current_tree() -> None:
    """Every cited path in contracts/ and the capability inventory resolves."""
    assert contract_code_path_failures() == []


def test_navigation_route_gate_flags_renamed_symbol_and_unlinked_row(
    monkeypatch, tmp_path
) -> None:
    """The task-routing gate fails on the row that is wrong, not on the file.

    A route row that keeps its link but names a symbol the source no longer has
    is the expensive failure: the link resolves, so nothing else notices, and the
    reader lands in the right file looking for the wrong thing.
    """
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "owner.py").write_text(
        "def live_symbol():\n    return 1\n", encoding="utf-8"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_owner.py").write_text(
        "def test_live_symbol():\n    assert live_symbol()\n", encoding="utf-8"
    )
    (tmp_path / "MAP.md").write_text(
        "<!-- navigation-check:start -->\n"
        "| task | [owner.py](core/owner.py): `live_symbol` / `renamed_symbol` |"
        " [test_owner.py](tests/test_owner.py) |\n"
        "| task | `only_symbol` |\n"
        "<!-- navigation-check:end -->\n"
        "`outside_the_region` is prose, not a route\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)

    assert check_docs.navigation_route_failures(Path("MAP.md")) == [
        "MAP.md:2: navigation symbol `renamed_symbol` is not in the linked "
        "source (core/owner.py)",
        "MAP.md:3: route row names only_symbol but links no non-test source file",
    ]


def test_navigation_route_gate_requires_a_focused_test_for_each_row(
    monkeypatch, tmp_path
) -> None:
    """A route pointing at a test that never touches its symbol is not a route.

    Both cases were live on 2026-09-28: the ACK/Fill/Close row cited a spec that
    exercises neighbouring materializers only, and the Story row omitted the test
    that carries the read-only assertion.
    """
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "owner.py").write_text(
        "def live_symbol():\n    return 1\n", encoding="utf-8"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_other.py").write_text(
        "def test_something_else():\n    assert True\n", encoding="utf-8"
    )
    (tmp_path / "MAP.md").write_text(
        "<!-- navigation-check:start -->\n"
        "| task | [owner.py](core/owner.py): `live_symbol` |"
        " [test_other.py](tests/test_other.py) |\n"
        "| task | [owner.py](core/owner.py): `live_symbol` |\n"
        "<!-- navigation-check:end -->\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)

    assert check_docs.navigation_route_failures(Path("MAP.md")) == [
        "MAP.md:2: no linked focused test names a row symbol (tests/test_other.py)",
        "MAP.md:3: route row names live_symbol but links no focused test file",
    ]


def test_navigation_route_gate_requires_its_region_markers(monkeypatch, tmp_path) -> None:
    """A map that dropped the markers would silently stop being checked."""
    (tmp_path / "MAP.md").write_text("| task | `symbol` |\n", encoding="utf-8")
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)

    failures = check_docs.navigation_route_failures(Path("MAP.md"))

    assert failures == [
        "MAP.md: missing <!-- navigation-check:start --> / "
        "<!-- navigation-check:end --> region markers"
    ]


def test_navigation_route_gate_is_green_on_the_current_tree() -> None:
    """Every symbol in SYSTEM_MAP's route table resolves in its linked source."""
    assert check_docs.navigation_route_failures() == []
