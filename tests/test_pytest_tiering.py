import importlib.util
import sys
from pathlib import Path

import tests.conftest as pytest_conftest


def _load_slow_classifier():
    path = Path("scripts/pytest_slow_classifier.py")
    spec = importlib.util.spec_from_file_location("pytest_slow_classifier", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_pytest_tier_classifier_sample_accuracy():
    cases = {
        "tests/test_hypothesis_properties.py": "tier_unit",
        "tests/test_operator_routes.py": "tier_integration",
        "tests/test_universe_service.py": "tier_integration",
        "tests/e2e/test_pipeline_feedback_loop.py": "tier_e2e",
        "tests/test_anti_hallucination_e2e.py": "tier_e2e",
        "tests/specs/spec_risk_engine.py": "tier_spec",
    }

    correct = 0
    for path, expected in cases.items():
        if pytest_conftest._classify_test_tier(path) == expected:
            correct += 1

    assert correct == len(cases)


def test_pytest_domain_classifier_sample_coverage():
    cases = {
        "tests/test_operator_routes.py": "domain_operator",
        "tests/test_universe_signal_emitter.py": "domain_universe",
        "tests/test_serenity_workbench.py": "domain_serenity",
        "tests/specs/spec_risk_engine.py": "domain_risk",
        "tests/test_frontend_navigation_ia.py": "domain_frontend",
        "tests/test_knowledge.py": "domain_learning",
        "tests/test_security.py": "domain_security",
        "tests/test_config_routes.py": "domain_config",
        "tests/test_storage.py": "domain_storage",
        "tests/test_miscellaneous_shape.py": "domain_misc",
    }

    for path, expected in cases.items():
        assert expected in pytest_conftest._classify_test_domains(path)


def test_pytest_markers_register_layers_domains_and_slow_binary_labels():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    conftest = Path("tests/conftest.py").read_text(encoding="utf-8")
    for marker in [
        "fast",
        "integration",
        "e2e",
        "tier_unit",
        "tier_integration",
        "tier_e2e",
        "tier_spec",
        "domain_operator",
        "domain_universe",
        "domain_serenity",
        "domain_risk",
        "slow_optimizable",
        "slow_structural",
    ]:
        assert marker in pyproject
        assert marker in conftest


def test_fast_integration_slow_classifier_sample_accuracy():
    assert pytest_conftest._classify_test_tier("tests/test_hypothesis_properties.py") == "tier_unit"
    assert pytest_conftest._is_slow_test("tests/test_hypothesis_properties.py") is False
    assert pytest_conftest._is_slow_test("tests/test_serenity_workbench.py") is True
    assert pytest_conftest._is_slow_test("tests/e2e/test_pipeline_feedback_loop.py") is True
    assert pytest_conftest._is_slow_test("tests/specs/spec_hyperliquid.py::test_hyperliquid_live_e2e") is True


def test_pytest_cacheprovider_enabled_for_last_failed_workflow():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    gitignore = Path(".gitignore").read_text(encoding="utf-8")

    assert "-p no:cacheprovider" not in pyproject
    assert ".pytest_cache/" in gitignore


def test_ai_default_test_script_runs_last_failed_then_fast():
    script = Path("scripts/test_ai.ps1").read_text(encoding="utf-8")

    assert "--lf" in script
    assert "--last-failed-no-failures=none" in script
    assert "cache_dir=.pytest_cache_ai" in script
    assert '-m", "fast' in script
    assert "pytest" in script


def test_xdist_is_available_but_not_default_parallel():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert "pytest-xdist" in pyproject
    assert "-n auto" not in pyproject


def test_core_project_rules_are_mapped_to_executable_tests():
    guide = Path("docs/operations/development/PYTEST_OPTIMIZATION_GUIDE.md").read_text(encoding="utf-8")
    for expected in [
        "tests/specs/spec_execution.py",
        "tests/specs/spec_live_unlock.py",
        "tests/specs/spec_api_reference.py",
        "tests/specs/spec_data_layer.py",
        "tests/specs/spec_data_model.py",
        "tests/test_pytest_tiering.py",
        "scripts/test_ai.ps1",
    ]:
        assert expected in guide


def test_slow_duration_classifier_parses_and_binary_labels_pytest_output():
    classifier = _load_slow_classifier()
    output = [
        "6.73s call tests/test_serenity_workbench.py::test_research_worker_success",
        "2.67s call tests/test_operator_routes.py::test_operator_public_branch_create_generates_child_session_and_selects_it",
        "1.40s teardown tests/test_acceptance.py::test_ac07",
        "0.20s call tests/test_fast.py::test_fast_path",
    ]

    records = classifier.parse_duration_lines(output, threshold=1.0)
    assert [r.tag for r in records] == [
        "slow_optimizable",
        "slow_optimizable",
        "slow_structural",
    ]
    summary = classifier.summarize(records)
    assert summary["slow_optimizable"]["count"] == 2
    assert summary["slow_structural"]["count"] == 1
