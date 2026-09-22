"""Core scope, durable documents and FTS survive optional Knowledge absence."""

import os
from pathlib import Path
import subprocess
import sys


def test_core_knowledge_facts_without_optional_knowledge(tmp_path):
    script = r'''
import importlib
import socket
import sys

blocked = ("aletheia.knowledge", "aletheia.analytics.temporal", "aletheia.analytics.attribution")
class MissingOptional:
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
            raise ModuleNotFoundError("optional owner absent", name=fullname)
sys.meta_path.insert(0, MissingOptional())
def forbidden(*args, **kwargs):
    raise AssertionError("Core format proof must not use network or credentials")
socket.socket.connect = forbidden
from aletheia.config import secrets
secrets.get_secret = forbidden

from aletheia.config.schema import AletheiaConfig
from aletheia.agent_runtime import startup_behavior_gate as gate, runtime_session
from aletheia.files.frontmatter import parse_frontmatter
scope, _ = parse_frontmatter(gate._SCOPE_PATH)
assert scope["status"] == "active"
cfg = AletheiaConfig()
cfg.exchange.paper_execution_mode = "bitget_demo"
assert gate._check_active_runtime_scope(cfg)[0] is True
assert runtime_session._active_scope_id() == scope["scope_id"]

from aletheia.storage.migrations import run_migrations, _apply_startup_dataset_boundary_v3
from aletheia.storage.sqlite import get_db, reset_db
run_migrations()
db = get_db()
from aletheia.storage import repositories as repo
from aletheia.storage.models import KnowledgeItem
from aletheia.files.knowledge_document import read_knowledge_document
from aletheia.storage.knowledge_index import reindex_all
item = KnowledgeItem(item_type="operator_assessment", title="Core evidence", confidence="high_confidence",
                     content="---\nsymbol: BTC/USDT\n---\nDurable inline evidence.")
repo.insert_knowledge_item(item)
row = repo.get_knowledge_item(item.id)
assert row["content_format"] == "knowledge-markdown/v1"
assert read_knowledge_document(row).state == "available_inline"
assert repo.insert_knowledge_item_if_absent(item, title_window_hours=1)[0] is False
assert read_knowledge_document({"content": "Historical body"}).state == "legacy_body_only"
assert read_knowledge_document(dict(row, content="tampered")).state == "integrity_conflict"

from aletheia.agent_shell.operator_loop import _persist_assessment
_persist_assessment({"timestamp": "2026-09-22T00:00:00Z", "actions_decided": 0,
                     "assessment": {"state": "bounded test"}})
assert db.execute("SELECT COUNT(*) FROM knowledge_items WHERE item_type='operator_assessment'").fetchone()[0] == 2
assert db.execute("SELECT COUNT(*) FROM knowledge_fts WHERE item_type='operator_assessment'").fetchone()[0] == 1
_apply_startup_dataset_boundary_v3(db)
assert db.execute("SELECT COUNT(*) FROM knowledge_fts WHERE item_type='operator_assessment'").fetchone()[0] == 2

# A strict rebuild must propagate failure and roll back its own transaction.
db.commit()
before = [tuple(row) for row in db.execute("SELECT * FROM knowledge_fts ORDER BY item_id")]
def deny_insert(action, arg1, arg2, database, trigger):
    import sqlite3
    return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT and arg1 == "knowledge_fts" else sqlite3.SQLITE_OK
db.set_authorizer(deny_insert)
try:
    reindex_all(db=db, strict=True)
except Exception:
    pass
else:
    raise AssertionError("strict rebuild failure was swallowed")
finally:
    db.set_authorizer(None)
assert not db.in_transaction
assert before == [tuple(row) for row in db.execute("SELECT * FROM knowledge_fts ORDER BY item_id")]

from aletheia.utils.dt import parse_utc_instant, parse_utc
assert parse_utc_instant("2026-09-22T08:00:00+08:00").isoformat() == "2026-09-22T00:00:00+00:00"
assert parse_utc_instant("2026-09-22T00:00:00Z") is not None
assert parse_utc_instant("2026-09-22T00:00:00") is None
assert parse_utc("2026-09-22T00:00:00") is not None
for module in ("candidate_funding", "candidate_eligibility", "candidate_selection_shadow", "economic_utility"):
    consumer = importlib.import_module("aletheia.universe." + module)
    assert consumer.parse_utc_instant is parse_utc_instant
assert not any(any(name == prefix or name.startswith(prefix + ".") for prefix in blocked) for name in sys.modules)
reset_db()
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "ALETHEIA_DATA_DIR": str(tmp_path), "ALETHEIA_RUNTIME_MODE": "test"},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_optional_owners_have_no_core_codec_or_index_implementation():
    import ast

    root = Path(__file__).resolve().parents[1]
    assert not (root / "aletheia/knowledge/document.py").exists()
    assert not (root / "aletheia/knowledge/frontmatter.py").exists()
    for path, names in (
        ("aletheia/knowledge/retrieval.py", {"index_knowledge_item", "reindex_all", "remove_from_index"}),
        ("aletheia/knowledge/manager.py", {"_index"}),
        ("aletheia/analytics/temporal.py", {"parse_utc_instant"}),
    ):
        tree = ast.parse((root / path).read_text())
        assert not {node.name for node in tree.body if isinstance(node, ast.FunctionDef)} & names
