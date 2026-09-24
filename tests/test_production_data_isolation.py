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
import tempfile
from pathlib import Path


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


def test_process_runtime_root_is_a_temporary_directory():
    from aletheia.runtime_paths import runtime_home

    resolved = runtime_home().resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()

    assert resolved.is_relative_to(temp_root), (
        f"expected the session root under {temp_root}, got {resolved}"
    )


def test_data_dir_never_resolves_into_the_repository():
    """Runtime state must not be written into the checkout either."""
    from aletheia.config.loader import get_data_dir

    repo_root = Path(__file__).resolve().parents[1]

    assert not Path(get_data_dir()).resolve().is_relative_to(repo_root)


def test_the_guard_leaves_the_override_set():
    """The conftest guard must always leave ``ALETHEIA_DATA_DIR`` set.

    An explicitly configured value is a deliberate operator choice and is respected
    — including a warning when it points at production — but it must never be left
    empty, which is the state that made the production fallback reachable.
    """
    assert os.environ.get("ALETHEIA_DATA_DIR", "").strip()
