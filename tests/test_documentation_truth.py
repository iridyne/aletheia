"""Focused regression tests for cross-document Product/Learning truth."""

from pathlib import Path

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
