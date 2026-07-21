"""Shared fixtures for end-to-end pipeline tests.

Key difference from unit tests:
- Session-scoped DB: one SQLite file for the entire e2e session (fast)
- Per-test isolation via SAVEPOINT/ROLLBACK (not DROP+CREATE)
- NO mocking of module-to-module interfaces (that's the whole point)
- Exchange/LLM calls ARE mocked (we test data flow, not network)
"""

import sqlite3
import pytest

import aletheia.config.loader as loader
import aletheia.storage.sqlite as sq
from aletheia.storage.migrations import run_migrations


def pytest_collection_modifyitems(items):
    """Auto-mark all tests in this directory as 'e2e'."""
    for item in items:
        path = str(getattr(item, "path", "") or getattr(item, "fspath", "")).replace("\\", "/")
        if "/tests/e2e/" in path or path.startswith("tests/e2e/"):
            item.add_marker(pytest.mark.e2e)


@pytest.fixture(scope="session")
def _e2e_db_path(tmp_path_factory):
    """Create a single DB file for the entire e2e session."""
    db_dir = tmp_path_factory.mktemp("e2e_data")
    (db_dir / "logs").mkdir()
    return db_dir


@pytest.fixture(autouse=True)
def e2e_db(_e2e_db_path, monkeypatch):
    """Per-test DB isolation via SAVEPOINT/ROLLBACK.

    Each test sees a clean DB state (post-migrations) without paying
    the cost of DROP+CREATE. ~1ms per test instead of ~40ms.
    """
    db_path = _e2e_db_path / "e2e.db"
    monkeypatch.setattr(loader, "DATA_DIR", _e2e_db_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", _e2e_db_path / "config.yaml")
    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: None)
    monkeypatch.setattr("aletheia.config.secrets.get_data_dir", lambda: _e2e_db_path)
    monkeypatch.setattr(sq, "db_path", lambda: db_path)

    # Ensure DB + migrations exist (idempotent after first test)
    sq.reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    sq._db_conn = conn
    run_migrations()

    # SAVEPOINT for per-test isolation
    conn.execute("SAVEPOINT e2e_test")
    yield conn
    conn.execute("ROLLBACK TO SAVEPOINT e2e_test")
    conn.execute("RELEASE SAVEPOINT e2e_test")


@pytest.fixture(autouse=True)
def _mock_llm(monkeypatch):
    """Block real LLM calls — e2e tests verify data flow, not LLM quality."""

    def blocked_get_provider(*_args, **_kwargs):
        raise AssertionError("real LLM provider resolution is forbidden in E2E tests")

    monkeypatch.setattr("aletheia.agents.helpers.get_provider", blocked_get_provider)


@pytest.fixture(autouse=True)
def _mock_exchange(monkeypatch):
    """Block real exchange calls in e2e tests."""

    def blocked_make_ccxt(*_args, **_kwargs):
        raise AssertionError(
            "real CCXT construction is forbidden in E2E tests"
        )

    monkeypatch.setattr("aletheia.net.make_ccxt", blocked_make_ccxt)
