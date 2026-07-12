"""Deterministic regression replay over the sanitized runtime SQLite pack."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sqlite3


FIXTURES = Path(__file__).resolve().parent / "fixtures"
PACK = FIXTURES / "runtime_replay_v1.sqlite"
EXPECTED = FIXTURES / "runtime_replay_v1.expected.json"


def _metrics(conn: sqlite3.Connection) -> dict:
    decisions = dict(conn.execute(
        "SELECT action, COUNT(*) FROM pi_decisions GROUP BY action"
    ).fetchall())
    risk = dict(conn.execute(
        "SELECT approved, COUNT(*) FROM risk_decisions GROUP BY approved"
    ).fetchall())
    positions = conn.execute("SELECT * FROM positions").fetchall()
    position_results = Counter()
    genuine = 0
    from aletheia.learning.outcomes import assess_realized_position

    for raw in positions:
        row = dict(raw)
        if row["status"] == "open":
            position_results["open"] += 1
            continue
        key = "closed_win" if float(row["realized_pnl"]) > 0 else "closed_loss"
        position_results[key] += 1
        assessed = assess_realized_position(row)
        if assessed.eligible:
            genuine += 1

    authority = Counter()
    for row in conn.execute("SELECT manifest_json FROM learning_exposures"):
        manifest = json.loads(row[0])
        authority["applied" if manifest.get("authority_applied") else "refrozen"] += 1

    version = conn.execute(
        "SELECT value FROM runtime_replay_manifest WHERE key='fixture_version'"
    ).fetchone()[0]
    return {
        "candidate_count": conn.execute(
            "SELECT COUNT(*) FROM universe_members WHERE execution_eligible=1"
        ).fetchone()[0],
        "decision_distribution": decisions,
        "fixture_version": version,
        "genuine_outcome_count": genuine,
        "learning_authority": {
            "applied": authority.get("applied", 0),
            "refrozen": authority.get("refrozen", 0),
        },
        "position_results": dict(position_results),
        "proposal_count": conn.execute("SELECT COUNT(*) FROM trade_proposals").fetchone()[0],
        "risk_decisions": {
            "approved": risk.get(1, 0),
            "rejected": risk.get(0, 0),
        },
        "settled_net_outcome_count": conn.execute(
            "SELECT COUNT(*) FROM universe_forward_outcomes "
            "WHERE status='settled' AND net_return_pct IS NOT NULL"
        ).fetchone()[0],
    }


def test_runtime_replay_v1_matches_explicit_baseline():
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    source = sqlite3.connect(f"file:{PACK}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    replay = sqlite3.connect(":memory:")
    replay.row_factory = sqlite3.Row
    try:
        source.backup(replay)
        assert replay.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert replay.execute("PRAGMA foreign_key_check").fetchall() == []
        assert _metrics(replay) == expected
    finally:
        replay.close()
        source.close()


def test_runtime_replay_v1_preserves_every_causal_and_learning_link():
    conn = sqlite3.connect(f"file:{PACK}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        native_positions = conn.execute(
            "SELECT COUNT(*) FROM positions WHERE source='native'"
        ).fetchone()[0]
        complete_positions = conn.execute(
            "SELECT COUNT(*) FROM positions p "
            "JOIN orders o ON o.id=p.order_id "
            "JOIN risk_decisions r ON r.id=o.risk_decision_id AND r.approved=1 "
            "JOIN trade_proposals t ON t.id=r.proposal_id AND t.id=p.proposal_id "
            "JOIN pi_decisions d ON d.proposal_id=t.id "
            "JOIN opportunities x ON x.id=d.opportunity_id AND x.id=p.opportunity_id "
            "WHERE p.source='native'"
        ).fetchone()[0]
        assert complete_positions == native_positions == 3

        linked_exposures = conn.execute(
            "SELECT COUNT(*) FROM learning_exposures e "
            "JOIN universe_forward_outcomes o "
            "ON o.outcome_candidate_key=e.outcome_candidate_key "
            "WHERE o.status='settled' AND o.net_return_pct IS NOT NULL "
            "AND o.cost_model_version IS NOT NULL"
        ).fetchone()[0]
        exposure_count = conn.execute(
            "SELECT COUNT(*) FROM learning_exposures"
        ).fetchone()[0]
        assert linked_exposures == exposure_count == 4

        manifest = dict(conn.execute(
            "SELECT key,value FROM runtime_replay_manifest"
        ).fetchall())
        assert manifest["source_values_copied"] == "false"
        assert manifest["sanitization"] == "category-preserving-total-replacement-v1"
    finally:
        conn.close()
