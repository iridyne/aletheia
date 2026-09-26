"""Structured-model failure behavior; shared test-environment setup helper.

`llm_structured` must degrade gracefully (return an error, never raise) when no
model provider is configured. `setup_env` is imported by many other test
modules to bootstrap an isolated config/DB environment.
"""

import sqlite3

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.agent_runtime.tool_registry import init_tool_registry


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    for sub in ("logs", "knowledge/research_notes"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    init_tool_registry()


# --- llm_structured graceful degradation ---

async def test_llm_structured_degrades_gracefully(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agents.helpers import llm_structured
    from pydantic import BaseModel

    class Schema(BaseModel):
        direction_bias: str = "neutral"

    # No model provider configured → returns (None, error), never raises.
    data, err = await llm_structured(system="x", user="y", schema=Schema)
    assert data is None
    assert err is not None
