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
    """Block real LLM provider resolution in e2e tests.

    get_provider() (aletheia/model_provider/router.py) is the shared seam that
    llm_complete() and llm_structured() call before dispatching to any provider.
    Blocking it here ensures no E2E test can silently fall through to a configured
    local/remote model. llm_complete/llm_structured both catch the exception and
    return (None, "llm_unavailable: ...") so tests can assert data-flow behavior
    without needing a real provider.

    E2E tests that inject deterministic responses (e.g. patching llm_complete
    directly, or test_guard_blocks_real_llm below) override above this seam and
    are unaffected.
    """
    import aletheia.agents.helpers as _helpers

    def _blocked_get_provider(name=None):
        raise RuntimeError(
            "E2E guard: LLM provider resolution blocked "
            f"(requested={name!r}). Inject a deterministic response instead."
        )

    monkeypatch.setattr(_helpers, "get_provider", _blocked_get_provider)


@pytest.fixture(autouse=True)
def _mock_exchange(monkeypatch):
    """Block real CCXT client construction in e2e tests.

    make_ccxt() (aletheia/net.py) is the ONLY sanctioned seam for building a
    CCXT exchange client. Blocking it here ensures no test can accidentally open
    aiohttp connections to a real exchange. Tests that intentionally exercise
    market-data flow inject their own provider/class at a higher level and are
    not affected because their patches take precedence over this fixture.
    """
    import aletheia.net as _net

    def _blocked_make_ccxt(name: str, **options):
        raise RuntimeError(
            f"E2E guard: real CCXT client construction blocked (exchange={name!r}). "
            "Inject a fake MarketDataProvider instead of reaching the network."
        )

    monkeypatch.setattr(_net, "make_ccxt", _blocked_make_ccxt)
