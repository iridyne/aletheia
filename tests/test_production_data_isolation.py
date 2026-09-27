"""A test process must never own the production data directory.

``ALETHEIA_DATA_DIR`` is what CI sets per regression tier. When it is unset, every
path resolved *before* a test's ``tmp_path`` monkeypatch lands — import-time config,
scheduler and auto-resume state — falls back to the per-user state root, i.e. the
real ``~/.aletheia``. Two test processes then own one production directory, which is
the standing "do not run two suites concurrently" rule and a trust-boundary problem
rather than a convenience one.

``tests/conftest.py::_configure_test_data_dir`` roots the session to a temporary
directory when the variable is unset. These tests pin that, because a future edit to
the conftest is exactly the change that would silently remove it.

The assertions read ``runtime_home()``, not ``get_data_dir()``: an autouse fixture
repoints ``loader.DATA_DIR`` at ``tmp_path`` for every test, so ``get_data_dir()``
would pass regardless. ``runtime_home()`` is the process-level root and is what the
pre-monkeypatch paths actually use.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest


def _production_root() -> Path:
    from aletheia.runtime_paths import default_runtime_home

    return default_runtime_home().resolve()


def test_process_runtime_root_is_not_the_production_directory():
    from aletheia.runtime_paths import runtime_home

    resolved = runtime_home().resolve()

    assert resolved != _production_root(), (
        f"this test process resolved the production data directory {resolved}; "
        "tests must never own real runtime state"
    )


def test_process_runtime_root_is_outside_runtime_and_checkout():
    from aletheia.runtime_paths import runtime_home

    resolved = runtime_home().resolve()
    checkout = Path(__file__).resolve().parents[1]
    assert not resolved.is_relative_to(_production_root())
    assert not resolved.is_relative_to(checkout)


def test_data_dir_never_resolves_into_the_repository():
    """Runtime state must not be written into the checkout either."""
    from aletheia.config.loader import get_data_dir

    repo_root = Path(__file__).resolve().parents[1]

    assert not Path(get_data_dir()).resolve().is_relative_to(repo_root)


def test_the_guard_leaves_the_override_set():
    """The conftest guard must always leave ``ALETHEIA_DATA_DIR`` set.

    Safe explicit overrides are respected; production and checkout roots are
    rejected before collection. An unset override receives an isolated root.
    """
    assert os.environ.get("ALETHEIA_DATA_DIR", "").strip()


@pytest.mark.parametrize("mode", ["production", "symlink", "production-child", "checkout", "safe-root", "unset"])
def test_data_root_guard_runs_before_test_collection(tmp_path, mode):
    """Use a fake HOME and no inherited credentials, never the real runtime."""
    checkout = Path(__file__).resolve().parents[1]
    fake_home = tmp_path / "home"
    production = fake_home / ".aletheia"
    production.mkdir(parents=True)
    work = tmp_path / "pytest-work"
    work.mkdir()
    (work / "pytest.ini").write_text("[pytest]\n")
    marker = tmp_path / "collected-root"
    fallback_temp = tmp_path / "default-temp"
    fallback_temp.mkdir()
    (work / "conftest.py").write_text(
        "import tempfile\n"
        "from tests.conftest import _configure_test_data_dir, pytest_unconfigure\n"
        "def pytest_configure(config):\n"
        f"    tempfile.tempdir = {str(fallback_temp)!r}\n"
        "    _configure_test_data_dir(config)\n"
    )
    (work / "test_probe.py").write_text(
        "from pathlib import Path\n"
        "from aletheia.runtime_paths import runtime_home\n"
        f"Path({str(marker)!r}).write_text(str(runtime_home()))\n"
        "from tests.test_production_data_isolation import "
        "test_process_runtime_root_is_outside_runtime_and_checkout\n"
    )
    env = {
        "HOME": str(fake_home), "USERPROFILE": str(fake_home),
        "PATH": os.defpath,
        "PYTHONPATH": os.pathsep.join([str(checkout), str(checkout / "core")]),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    explicit = production
    if mode == "symlink":
        explicit = tmp_path / "runtime-alias"
        explicit.symlink_to(production, target_is_directory=True)
    elif mode == "production-child":
        explicit = production / "nested-state"
    elif mode == "checkout":
        explicit = checkout / ".test-root-that-must-never-be-created"
    elif mode == "safe-root":
        explicit = tmp_path / "var" / "tmp" / "aletheia-ci-explicit"
        explicit.mkdir(parents=True)
    if mode != "unset":
        env["ALETHEIA_DATA_DIR"] = str(explicit)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-c", str(work / "pytest.ini"), "test_probe.py"],
        cwd=work, env=env, capture_output=True, text=True, timeout=30,
    )
    if mode in {"safe-root", "unset"}:
        assert result.returncode == 0, result.stdout + result.stderr
        resolved = Path(marker.read_text()).resolve()
        assert not resolved.is_relative_to(production)
        assert not resolved.is_relative_to(checkout)
        if mode == "safe-root":
            assert resolved == explicit.resolve()
    else:
        assert result.returncode == 4, result.stdout + result.stderr
        assert "ALETHEIA_DATA_DIR" in result.stderr
        assert not marker.exists(), "unsafe root reached test module import"
    assert list(production.iterdir()) == []
