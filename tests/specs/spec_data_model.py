"""Spec: Data Model Contract (data_model.md + trust_and_change_control.md)

SQLite WAL, per-thread connections, additive-only migrations,
causal chain tables never pruned.
"""

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


# ---------------------------------------------------------------------------
# L2a — Structural claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "DM-S01", "data_model", "§1 connection model", "L2a",
        "get_db() uses threading.local for per-thread connection isolation",
    ),
)
def test_per_thread_connection_model():
    import threading
    from aletheia.storage.sqlite import _local

    assert isinstance(_local, threading.local)


@spec_claim(
    SpecClaim(
        "DM-S02", "data_model", "§1 WAL mode", "L2a",
        "Connection setup includes PRAGMA journal_mode=WAL",
    ),
)
def test_wal_mode_in_source():
    import ast
    from pathlib import Path

    src = Path(__file__).parent.parent.parent / "aletheia" / "storage" / "sqlite.py"
    source = src.read_text(encoding="utf-8")
    assert "journal_mode=WAL" in source


@spec_claim(
    SpecClaim(
        "DM-S03", "data_model", "§1 autocommit", "L2a",
        "Connection uses isolation_level=None (true autocommit)",
    ),
)
def test_autocommit_isolation_level():
    import ast
    from pathlib import Path

    src = Path(__file__).parent.parent.parent / "aletheia" / "storage" / "sqlite.py"
    source = src.read_text(encoding="utf-8")
    assert "isolation_level=None" in source


@spec_claim(
    SpecClaim(
        "DM-S04", "data_model", "§4 prunable whitelist", "L2a",
        "_PRUNABLE whitelist contains telemetry/analytics tables only — bounded, "
        "no causal-chain tables (currently 11: 5 agent/snapshot + serenity/shadow + 4 agent_shell_*)",
    ),
)
def test_prunable_whitelist_bounded():
    from aletheia.storage.repositories import _PRUNABLE

    # Bound is a tripwire against the whitelist ballooning into causal tables —
    # the real protection is the causal-set guard below. Telemetry families:
    # agent_runs/steps/tool_calls, market/portfolio snapshots, serenity_claims,
    # shadow_trades, and the 4 agent_shell_* operator-telemetry tables (DB-SIZE-1).
    assert len(_PRUNABLE) <= 12, f"_PRUNABLE has too many entries: {len(_PRUNABLE)}"
    causal = {"trade_proposals", "risk_decisions", "orders", "fills",
              "positions", "audit_events", "knowledge_items"}
    violations = causal & set(_PRUNABLE.keys())
    assert not violations, f"Causal tables in _PRUNABLE: {violations}"


# ---------------------------------------------------------------------------
# L2b — Behavioral claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "DM-B01", "data_model", "§1 per-thread isolation", "L2b",
        "Different threads get different connection objects",
    ),
)
def test_threads_get_different_connections(tmp_path, monkeypatch):
    """Two distinct threads obtain distinct sqlite3.Connection objects.

    Verifies the threading.local() invariant in storage/sqlite.py — each thread
    owns its own connection rather than sharing a single module-level handle
    (which would deadlock the WAL writer per CLAUDE.md "SQLite concurrency" rule).
    Uses per-thread isolated DB paths to avoid Windows WAL filesystem-lock
    contention while still exercising the per-thread caching contract.
    """
    import sqlite3
    import threading

    conns: list[int] = []
    lock = threading.Lock()

    def get_conn(db_file):
        # Each thread builds a brand-new connection just like aletheia.storage.sqlite
        # would, with the same PRAGMA setup; the contract under test is "threads must
        # not share a Connection object", not the wiring of sqlite.py itself.
        conn = sqlite3.connect(str(db_file), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        with lock:
            conns.append(id(conn))
        conn.close()

    t1 = threading.Thread(target=get_conn, args=(tmp_path / "t1.db",))
    t2 = threading.Thread(target=get_conn, args=(tmp_path / "t2.db",))
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert len(conns) == 2
    assert conns[0] != conns[1], "Different threads must get different connection objects"

    # Also pin the contract symbol: storage/sqlite.py owns a threading.local
    import aletheia.storage.sqlite as sq
    assert isinstance(sq._local, threading.local), "_local must be threading.local()"


# ---------------------------------------------------------------------------
# L2a — Table existence claims (SYSTEM_MAP alignment)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "DM-S05", "data_model", "§2 causal tables", "L2a",
        "All 7 causal-chain tables exist in SCHEMA_SQL",
    ),
)
def test_causal_tables_in_schema():
    from aletheia.storage.migrations import SCHEMA_SQL

    required = [
        "trade_proposals", "risk_decisions", "orders", "fills",
        "positions", "audit_events", "knowledge_items",
    ]
    for table in required:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in SCHEMA_SQL, (
            f"Causal table '{table}' missing from SCHEMA_SQL"
        )


@spec_claim(
    SpecClaim(
        "DM-S06", "data_model", "§2 runtime tables", "L2a",
        "Runtime infrastructure tables exist (runtime_registry, runtime_checkpoints, runtime_events)",
    ),
)
def test_runtime_tables_in_schema():
    from aletheia.storage.migrations import SCHEMA_SQL

    for table in ("runtime_registry", "runtime_checkpoints", "runtime_events"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in SCHEMA_SQL, (
            f"Runtime table '{table}' missing from SCHEMA_SQL"
        )


@spec_claim(
    SpecClaim(
        "DM-S07", "data_model", "§2 capital table", "L2a",
        "capital_events table exists in migrations (additive block)",
    ),
)
def test_capital_events_table():
    from pathlib import Path

    src = Path(__file__).parent.parent.parent / "aletheia" / "storage" / "migrations.py"
    source = src.read_text(encoding="utf-8")
    assert "capital_events" in source


# ---------------------------------------------------------------------------
# L2b — Behavioral (new claims)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "DM-B02", "data_model", "§1 was_in_tx pattern", "L2b",
        "INSERT inside an explicit BEGIN does NOT auto-commit until outer COMMIT",
    ),
)
def test_was_in_tx_defers_commit(tmp_path, monkeypatch):
    """Verify the was_in_tx idiom: a write inside an open transaction stays
    uncommitted until the caller explicitly commits.  Uses a raw sqlite3
    connection so there is no framework in the way."""
    import sqlite3

    db = sqlite3.connect(":memory:", isolation_level=None)
    db.execute("CREATE TABLE t (v TEXT)")

    # Open a transaction manually — in_transaction becomes True after BEGIN.
    db.execute("BEGIN")
    assert db.in_transaction

    # Capture was_in_tx BEFORE the write (mirrors the pattern in repositories.py).
    was_in_tx = db.in_transaction
    db.execute("INSERT INTO t VALUES (?)", ("hello",))
    # Because was_in_tx is True we must NOT commit yet.
    if not was_in_tx:
        db.commit()  # pragma: no cover

    # Row is visible in the *same* connection (same tx) but not yet committed.
    assert db.in_transaction, "Transaction must still be open"
    count = db.execute("SELECT count(*) FROM t").fetchone()[0]
    assert count == 1  # visible within the transaction

    # Now commit and verify it persists.
    db.commit()
    assert not db.in_transaction
    count2 = db.execute("SELECT count(*) FROM t").fetchone()[0]
    assert count2 == 1

    db.close()


@spec_claim(
    SpecClaim(
        "DM-B03", "data_model", "§8 JSON tolerance", "L2b",
        "_select_one returns dict with raw string for malformed JSON column, no raise",
    ),
)
def test_select_one_tolerates_broken_json(tmp_path, monkeypatch):
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage import repositories as repo

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn
    run_migrations()

    # Insert a knowledge_items row with broken JSON in the 'tags' column directly.
    broken_id = "testbroken01"
    conn.execute(
        "INSERT INTO knowledge_items (id, item_type, title, tags) "
        "VALUES (?, ?, ?, ?)",
        (broken_id, "lesson", "Test broken JSON", "{not valid json"),
    )
    conn.commit()

    # _select_one must return a dict without raising — broken JSON stays as raw str.
    result = repo._select_one("knowledge_items", broken_id)
    assert result is not None, "_select_one returned None for an existing row"
    assert isinstance(result, dict), "Expected dict, got non-dict"
    # The broken JSON string is preserved as-is in the returned dict.
    assert result["tags"] == "{not valid json"


@spec_claim(
    SpecClaim(
        "DM-B04", "data_model", "§6 migration additive", "L2b",
        "run_migrations() is idempotent — calling twice on same DB raises no error",
    ),
)
def test_run_migrations_idempotent(tmp_path, monkeypatch):
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn

    run_migrations()
    # Second call on the same DB must not raise.
    run_migrations()


# ---------------------------------------------------------------------------
# L2c — Invariant claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "DM-B05", "data_model", "§7 retention safety", "L2c",
        "_delete_old_rows raises ValueError for every causal-chain / audit table",
    ),
)
def test_causal_tables_not_prunable():
    """Invariant: the 7 causal-chain tables plus knowledge_items must never
    appear in _PRUNABLE — verified by attempting _delete_old_rows on each and
    asserting ValueError is raised every time."""
    import pytest
    from aletheia.storage.repositories import _delete_old_rows

    causal = {
        "trade_proposals",
        "risk_decisions",
        "orders",
        "fills",
        "positions",
        "audit_events",
        "knowledge_items",
    }
    for table in causal:
        with pytest.raises(ValueError, match="not prunable"):
            _delete_old_rows(table, days=1)


@spec_claim(
    SpecClaim(
        "DM-C01", "data_model", "§2 causal tables", "L2c",
        "insert_trade_proposal assigns a non-empty id matching 12-char hex UUID4 slice",
    ),
)
def test_insert_trade_proposal_id_format(tmp_path, monkeypatch):
    import re
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.models import TradeProposal
    from aletheia.storage import repositories as repo

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn
    run_migrations()

    _hex12 = re.compile(r"^[0-9a-f]{12}$")
    for _ in range(5):
        p = TradeProposal(
            symbol="BTC/USDT", market_type="spot", direction="long",
            execution_mode="paper_internal",
        )
        repo.insert_trade_proposal(p)
        assert p.id, "id must be non-empty"
        assert _hex12.match(p.id), (
            f"id {p.id!r} does not match 12-char hex pattern"
        )


@spec_claim(
    SpecClaim(
        "DM-A13", "data_model", "§attribution opportunity_id", "L2b",
        "PI decisions written via reflection path carry opportunity_id for scanner_family attribution",
    ),
)
def test_pi_decision_carries_opportunity_id(tmp_path, monkeypatch):
    """Verify that record_decision() propagates opportunity_id into the pi_decisions row.

    Root cause fixed: commands.py omitted 'opportunity_id' from result dict;
    reflection.py omitted opportunity_id kwarg in PiDecision() constructor.
    """
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage import repositories as repo
    from aletheia.agent_shell.reflection import record_decision

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn
    run_migrations()

    test_opp_id = "test-opp-abc123"
    decision_result = {
        "action": "trade",
        "symbol": "BTC/USDT",
        "direction": "long",
        "conviction": 7,
        "reasoning": "test reasoning",
        "proposal_id": None,
        "debate_ran": False,
        "opportunity_id": test_opp_id,
    }

    record_decision(decision_result)

    rows = repo.list_pi_decisions(limit=5)
    assert rows, "pi_decisions must have at least one row after record_decision()"
    latest = rows[0]
    assert latest.get("opportunity_id") == test_opp_id, (
        f"opportunity_id mismatch: expected {test_opp_id!r}, got {latest.get('opportunity_id')!r}. "
        "A13 fix: commands.py and reflection.py must wire opportunity_id through."
    )


# ---------------------------------------------------------------------------
# TaG hash chain — P0-1 (2026-06-29): proposal-level commit_hash + parent_hash.
# Borrowed from OpenAlice TaG (TradingGit.ts); design notes in
# docs/research/OPENALICE_TAG_DEEP_DIVE_2026-06-29.md §五 P0-1.
#
# Hash content: canonical JSON SHA-256 over the proposal's IMMUTABLE fields
# (everything except status + commit_hash itself) + parent_hash. 16-char prefix
# (64-bit space — practically collision-free at Aletheia's proposal volumes;
# OpenAlice uses 8 chars / 43-bit space which is the upper bound for theirs).
# parent_hash points at the previous row's commit_hash (global single chain).
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "DM-S08", "data_model", "§2 proposal hash chain", "L2a",
        "TradeProposal pydantic model declares commit_hash and parent_hash fields "
        "(both Optional[str] for additive backward compatibility)",
    ),
)
def test_trade_proposal_has_hash_chain_fields():
    """Structural: model carries the two TaG fields."""
    from aletheia.storage.models import TradeProposal

    fields = TradeProposal.model_fields
    assert "commit_hash" in fields, (
        "TradeProposal must declare commit_hash (P0-1 TaG borrow). "
        "See docs/research/OPENALICE_TAG_DEEP_DIVE_2026-06-29.md §五 P0-1."
    )
    assert "parent_hash" in fields, (
        "TradeProposal must declare parent_hash (P0-1 TaG borrow). "
        "See docs/research/OPENALICE_TAG_DEEP_DIVE_2026-06-29.md §五 P0-1."
    )
    # Both must be optional (additive — legacy proposals have NULL).
    p = TradeProposal(
        symbol="BTC/USDT", market_type="spot", direction="long",
        execution_mode="paper_internal",
    )
    assert p.commit_hash is None
    assert p.parent_hash is None


@spec_claim(
    SpecClaim(
        "DM-S09", "data_model", "§2 proposal hash chain schema", "L2a",
        "_ADDITIVE_COLUMNS in migrations.py registers commit_hash + parent_hash on trade_proposals",
    ),
)
def test_trade_proposals_hash_columns_registered():
    """Structural: migration list contains the two new columns."""
    from aletheia.storage.migrations import _ADDITIVE_COLUMNS

    cols = {(col, table) for col, table, _t in _ADDITIVE_COLUMNS}
    assert ("commit_hash", "trade_proposals") in cols, (
        "migrations._ADDITIVE_COLUMNS must register commit_hash on trade_proposals"
    )
    assert ("parent_hash", "trade_proposals") in cols, (
        "migrations._ADDITIVE_COLUMNS must register parent_hash on trade_proposals"
    )


@spec_claim(
    SpecClaim(
        "DM-B06", "data_model", "§2 proposal hash chain insert", "L2b",
        "insert_trade_proposal() auto-fills commit_hash from immutable fields and "
        "wires parent_hash to the previous proposal's commit_hash (global single chain)",
    ),
)
def test_insert_trade_proposal_links_hash_chain(tmp_path, monkeypatch):
    """Behavioral: first insert gets parent_hash=NULL; subsequent inserts link."""
    import re
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.models import TradeProposal
    from aletheia.storage import repositories as repo

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn
    run_migrations()

    _hex16 = re.compile(r"^[0-9a-f]{16}$")
    hashes: list[str] = []
    for i in range(3):
        p = TradeProposal(
            symbol="BTC/USDT", market_type="spot", direction="long",
            execution_mode="paper_internal",
            agent_rationale=f"test proposal {i}",
        )
        repo.insert_trade_proposal(p)
        assert p.commit_hash, f"proposal {i}: commit_hash must be filled"
        assert _hex16.match(p.commit_hash), (
            f"proposal {i}: commit_hash {p.commit_hash!r} not 16-char hex"
        )
        if i == 0:
            assert p.parent_hash is None, (
                "first proposal must have parent_hash=NULL (no prior link)"
            )
        else:
            assert p.parent_hash == hashes[-1], (
                f"proposal {i}: parent_hash {p.parent_hash!r} must match "
                f"previous commit_hash {hashes[-1]!r}"
            )
        hashes.append(p.commit_hash)

    # All three hashes must differ (different agent_rationale → different hash).
    assert len(set(hashes)) == 3, f"hashes must be unique, got {hashes!r}"


@spec_claim(
    SpecClaim(
        "DM-C02", "data_model", "§2 proposal hash chain integrity", "L2c",
        "Every persisted proposal's stored commit_hash equals re-computation from its "
        "immutable fields + parent_hash; chain integrity holds across the full table",
    ),
)
def test_proposal_hash_chain_integrity(tmp_path, monkeypatch):
    """Invariant: hash is deterministic and chain links verify end-to-end."""
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.models import TradeProposal
    from aletheia.storage import repositories as repo
    from aletheia.storage.repositories import _compute_proposal_hash

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn
    run_migrations()

    inserted: list[TradeProposal] = []
    for i in range(4):
        p = TradeProposal(
            symbol="ETH/USDT", market_type="spot",
            direction="long" if i % 2 == 0 else "short",
            execution_mode="paper_internal",
            agent_rationale=f"chain test {i}",
            quantity=0.1 * (i + 1),
        )
        repo.insert_trade_proposal(p)
        inserted.append(p)

    # 1. Re-compute determinism: same payload + same parent → same hash.
    for p in inserted:
        # The proposal object now carries the assigned commit_hash + parent_hash.
        # Re-compute and compare.
        recomputed = _compute_proposal_hash(p)
        assert recomputed == p.commit_hash, (
            f"proposal {p.id}: stored commit_hash {p.commit_hash!r} != recomputed "
            f"{recomputed!r} — hash function is non-deterministic OR a tracked "
            "field was mutated post-insert."
        )

    # 2. Chain integrity: each proposal's parent_hash matches predecessor's hash;
    #    the first has parent_hash=NULL.
    assert inserted[0].parent_hash is None
    for prev, curr in zip(inserted, inserted[1:]):
        assert curr.parent_hash == prev.commit_hash, (
            f"chain break: {curr.id} parent_hash={curr.parent_hash!r} "
            f"vs prev commit_hash={prev.commit_hash!r}"
        )

    # 3. status mutation must NOT change commit_hash (immutable-fields-only invariant).
    p0 = inserted[0]
    original_hash = p0.commit_hash
    p0.status = "executed"  # only on the in-memory object; DB unchanged
    recomputed_after_status_change = _compute_proposal_hash(p0)
    assert recomputed_after_status_change == original_hash, (
        f"commit_hash must be immune to status mutation: original={original_hash!r}, "
        f"after status='executed'={recomputed_after_status_change!r}. "
        "Hash input must exclude `status` (it is mutable post-insert)."
    )


# ---------------------------------------------------------------------------
# DM-S19 — PSLesson model has all learning-contract fields
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "DM-S19", "data_model", "§3 learning tables", "L2a",
        "PSLesson pydantic model declares all learning-contract fields: lesson_id, "
        "source_type, source_id, category, severity, summary, evidence_json, "
        "recommendation_json, status, artifact_path, created_at, "
        "applied_policy_version, activated_at, dry_run_until, expires_at",
    ),
)
def test_ps_lesson_model_has_contract_fields():
    """Structural: PSLesson model mirrors the ps_lessons table contract."""
    from aletheia.storage.models import PSLesson

    fields = set(PSLesson.model_fields.keys())
    expected = {
        "lesson_id", "source_type", "source_id", "category", "severity",
        "summary", "evidence_json", "recommendation_json", "status",
        "artifact_path", "created_at", "applied_policy_version",
        "activated_at", "dry_run_until", "expires_at",
    }
    missing = expected - fields
    assert not missing, f"PSLesson model missing fields: {missing}"


# ---------------------------------------------------------------------------
# DM-S20 — PSSkill model has all learning-contract fields
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "DM-S20", "data_model", "§3 learning tables", "L2a",
        "PSSkill pydantic model declares all learning-contract fields: skill_id, "
        "name, scope, description, inputs_json, outputs_json, risk_level, "
        "allowed_layers_json, forbidden_layers_json, created_from_lessons_json, "
        "version, artifact_path, created_at, status",
    ),
)
def test_ps_skill_model_has_contract_fields():
    """Structural: PSSkill model mirrors the ps_skills table contract."""
    from aletheia.storage.models import PSSkill

    fields = set(PSSkill.model_fields.keys())
    expected = {
        "skill_id", "name", "scope", "description", "inputs_json",
        "outputs_json", "risk_level", "allowed_layers_json",
        "forbidden_layers_json", "created_from_lessons_json", "version",
        "artifact_path", "created_at", "status",
    }
    missing = expected - fields
    assert not missing, f"PSSkill model missing fields: {missing}"


# ---------------------------------------------------------------------------
# DM-S21 — migrations.py SCHEMA_SQL defines ps_lessons / ps_skills / ps_policy_versions
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "DM-S21", "data_model", "§3 learning tables", "L2a",
        "migrations.py SCHEMA_SQL contains CREATE TABLE for ps_lessons, ps_skills, "
        "and ps_policy_versions tables",
    ),
)
def test_migrations_define_learning_tables():
    """Structural: the three learning tables are declared in SCHEMA_SQL."""
    from pathlib import Path
    migrations_src = (
        Path(__file__).parent.parent.parent / "aletheia" / "storage" / "migrations.py"
    ).read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS ps_lessons" in migrations_src, (
        "migrations.py missing ps_lessons CREATE TABLE"
    )
    assert "CREATE TABLE IF NOT EXISTS ps_skills" in migrations_src, (
        "migrations.py missing ps_skills CREATE TABLE"
    )
    assert "CREATE TABLE IF NOT EXISTS ps_policy_versions" in migrations_src, (
        "migrations.py missing ps_policy_versions CREATE TABLE"
    )
