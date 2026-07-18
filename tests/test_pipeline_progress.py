"""Focused regressions for current-code and Operator-policy diagnostics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3


def _setup_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    loader._config_cache = None
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq.reset_db()
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    return conn


def _insert_epoch(
    conn,
    epoch_id: str,
    epoch_kind: str,
    started_at: str,
    ended_at: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO learning_policy_epochs "
        "(epoch_id,epoch_kind,effective_values_hash,effective_values_json,"
        "affected_path_ids_json,started_at,ended_at,created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            epoch_id,
            epoch_kind,
            f"hash-{epoch_id}",
            "{}",
            "[]",
            started_at,
            ended_at,
            started_at,
        ),
    )


def _insert_filter_fact(
    conn,
    *,
    fact_id: str,
    opportunity_id: str,
    reason_code: str,
    code_commit: str,
    evaluated_at: str,
    policy_epoch_id: str | None = None,
) -> None:
    inputs = {"policy_epoch_id": policy_epoch_id} if policy_epoch_id else {}
    conn.execute(
        "INSERT INTO filter_decisions "
        "(id,dedup_key,candidate_key,candidate_kind,opportunity_id,symbol,"
        "market_type,direction,filter_name,stage,sequence_no,decision,reason_code,"
        "input_snapshot,parameter_snapshot,filter_version,code_commit,evaluated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            fact_id,
            f"dedup-{fact_id}",
            f"opportunity:{opportunity_id}",
            "opportunity",
            opportunity_id,
            "BTC/USDT",
            "usdt_futures",
            "long",
            "operator_action_assessment",
            "system_operator",
            0,
            "rejected",
            reason_code,
            json.dumps(inputs),
            "{}",
            "operator-v1",
            code_commit,
            evaluated_at,
        ),
    )


def test_current_epoch_selects_skip_remediation_not_newer_unrelated_epoch(
    tmp_path, monkeypatch
):
    conn = _setup_db(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    _insert_epoch(
        conn,
        "skip-current",
        "system_operator_skip_remediation",
        (now - timedelta(minutes=2)).isoformat(),
    )
    _insert_epoch(
        conn,
        "learning-newer",
        "decision_policy:operator_learning_bundle",
        (now - timedelta(minutes=1)).isoformat(),
    )
    conn.commit()
    monkeypatch.setattr(
        "aletheia.agent_runtime.build_info.get_boot_commit", lambda: "commit-current"
    )

    from aletheia.diagnostics.pipeline_progress import _current_epoch

    epoch = _current_epoch(conn)
    assert epoch["producer_commit"] == "commit-current"
    assert epoch["policy_epoch_id"] == "skip-current"
    assert epoch["policy_epoch_kind"] == "system_operator_skip_remediation"


def test_operator_buckets_scope_facts_to_selected_skip_policy(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    evaluated_at = datetime.now(timezone.utc).isoformat()
    _insert_filter_fact(
        conn,
        fact_id="current-skip",
        opportunity_id="current",
        reason_code="operator_model_skip",
        code_commit="commit-current",
        evaluated_at=evaluated_at,
        policy_epoch_id="skip-current",
    )
    _insert_filter_fact(
        conn,
        fact_id="old-policy-skip",
        opportunity_id="old-policy",
        reason_code="operator_model_skip",
        code_commit="commit-current",
        evaluated_at=evaluated_at,
        policy_epoch_id="skip-old",
    )
    conn.commit()

    from aletheia.diagnostics.pipeline_progress import _operator_buckets

    buckets = _operator_buckets(
        conn,
        "commit-current",
        "skip-current",
        (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
    )
    assert buckets["direct_model_skip"] == 1


def test_proposal_stats_use_producer_commit_not_content_hash(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    created = now.isoformat()
    for opportunity_id, producer_commit in (
        ("own-current", "commit-current"),
        ("own-old", "commit-old"),
        ("legacy-current", "commit-current"),
        ("explicit-old", "commit-current"),
    ):
        conn.execute(
            "INSERT INTO opportunities "
            "(id,symbol,market,status,asset_class,created_at,producer_commit) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                opportunity_id,
                "BTC/USDT",
                "spot",
                "reviewed",
                "crypto",
                created,
                producer_commit,
            ),
        )
    proposals = (
        ("proposal-current", "own-current", "content-a", "commit-current"),
        # A content hash that happens to equal the Git commit must not count.
        ("proposal-old", "own-old", "commit-current", "commit-old"),
        # Legacy Proposal provenance may fall back to its Opportunity.
        ("proposal-legacy", "legacy-current", "content-c", None),
        # Explicit Proposal provenance always wins over its linked Opportunity.
        ("proposal-explicit-old", "explicit-old", "content-d", "commit-old"),
    )
    for proposal_id, opportunity_id, content_hash, producer_commit in proposals:
        conn.execute(
            "INSERT INTO trade_proposals "
            "(id,opportunity_id,symbol,market_type,direction,order_type,"
            "execution_mode,status,source,trade_school,created_at,commit_hash,producer_commit) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                proposal_id,
                opportunity_id,
                "BTC/USDT",
                "spot",
                "long",
                "market",
                "paper_internal",
                "executed",
                "native",
                "short_term",
                created,
                content_hash,
                producer_commit,
            ),
        )
    conn.commit()

    from aletheia.diagnostics.pipeline_progress import _proposal_stats

    result = _proposal_stats(
        conn,
        "commit-current",
        (now - timedelta(hours=1)).isoformat(),
    )
    assert result["count"] == 2
    assert result["newest_created_at"] == created
