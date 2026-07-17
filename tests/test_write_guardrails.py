"""Phase 3 write-side guardrail tests.

The guardrails these tests cover are the reason the retention-shortening pass
above stays safe: without them the shortened windows would still be flooded
with duplicate rows, telemetry writes would still amplify each other, and the
context blob would keep dominating the DB even after `agent_shell_messages`
retention drops to 3 days.

Every guardrail is asserted by exercising the actual write path once, then
checking either the resulting DB rows or file artifacts. Repositories are
stubbed with ``pytest.MonkeyPatch`` only where the write function must be
observed rather than executed for real (e.g. verifying ``log_tool`` no longer
double-writes ``agent_run_steps``).
"""

from __future__ import annotations

import json
import uuid

import pytest

from aletheia.storage.sqlite import get_db


# ---------------------------------------------------------------------------
# content.context: agent_shell_messages does NOT persist the full snapshot
# ---------------------------------------------------------------------------


def test_seed_branch_workspace_drops_context_field(monkeypatch):
    """The persisted assistant message must not carry the raw ``context`` blob.

    Agent 5 modified `_seed_branch_workspace` at
    aletheia/server/routes/operator.py to strip the ``context`` key before
    ``append_message`` so `agent_shell_messages` no longer stores multi-MB
    context snapshots. The full snapshot regenerates on read via
    /internal/context/query, so nothing downstream needs the persisted copy.
    """
    from aletheia.server.routes import operator as operator_routes

    captured: dict[str, object] = {}

    def fake_resolve(*, session_id, profile, sections):
        return {
            "profile": "default",
            "session_id": session_id,
            "session_kind": "command_session",
            "sections": ["risk", "market"],
            "summary": "context summary",
            "context": {"risk": {"blob": "x" * 4096}},
        }

    def fake_append_message(*, session_id, role, content, message_type, related_event_id):
        captured.setdefault("messages", []).append({
            "session_id": session_id,
            "role": role,
            "content": content,
            "message_type": message_type,
        })
        return str(uuid.uuid4())

    monkeypatch.setattr(operator_routes, "_resolve_context_query", fake_resolve)
    monkeypatch.setattr(operator_routes, "append_message", fake_append_message)

    operator_routes._seed_branch_workspace("s-guard", "hello")

    persisted = captured["messages"][0]
    assert persisted["message_type"] == "context"
    content = persisted["content"]
    assert isinstance(content, dict)
    assert "context" not in content, (
        "The raw expanded `context` blob must NOT be written into agent_shell_messages."
    )
    # The summary + profile + section list are all safe to keep.
    assert content.get("text") == "context summary"
    assert content.get("profile") == "default"
    assert content.get("sections") == ["risk", "market"]


# ---------------------------------------------------------------------------
# controller_recommendations: insufficient×insufficient rows dropped
# ---------------------------------------------------------------------------


def test_filter_controller_skips_both_insufficient(monkeypatch):
    """When both verdict and evidence_verdict == 'insufficient_data', the
    controller must NOT persist a per-cell row — it should coalesce into a
    single summary row per run.
    """
    from aletheia.analytics import filter_controller

    class _R:
        def __init__(self, verdict: str, direction: str) -> None:
            self.filter_name = "f"
            self.scanner_family = "sf"
            self.stage = "s"
            self.current_value = None
            self.verdict = verdict
            self.suggested_direction = direction
            self.error_signal = None
            self.n_samples = 0
            self.reasoning = "x"
            self.filter_version = None
            self.direction = None
            self.market_type = None
            self.horizon = None
            self.passed_n = None
            self.rejected_n = None
            self.passed_shrunk_avg_net = None
            self.rejected_shrunk_avg_net = None
            self.outcome_coverage = None

    monkeypatch.setattr(
        filter_controller,
        "compute_filter_feedback",
        lambda min_samples=0: [
            _R("insufficient_data", "n/a"),
            _R("insufficient_data", "n/a"),
            _R("hold", "hold"),
        ],
    )
    # Freshness gate: force _is_fresh_prior_run to return False so the writer
    # runs on this synthetic call. Otherwise the freshness guard could short
    # circuit the persistence path.
    monkeypatch.setattr(
        filter_controller, "_is_fresh_prior_run", lambda interval_hours: False
    )

    inserted: list[dict] = []

    def fake_insert(rec: dict) -> None:
        inserted.append(rec)

    from aletheia.storage import repositories as repo
    monkeypatch.setattr(repo, "insert_controller_recommendation", fake_insert)

    filter_controller.run_controller_observe(min_samples=0)

    # Two insufficient rows are coalesced into ONE (insufficient_summary) row;
    # the single 'hold' row is preserved.
    verdicts = [rec["verdict"] for rec in inserted]
    filter_names = [rec["filter_name"] for rec in inserted]
    assert verdicts.count("insufficient_data") == 1
    assert "(insufficient_summary)" in filter_names
    assert "hold" in verdicts


# ---------------------------------------------------------------------------
# log_tool: no longer double-writes to agent_run_steps
# ---------------------------------------------------------------------------


def test_log_tool_does_not_double_write_run_steps(monkeypatch):
    """Agent 8 dropped the `insert_run_step` call inside `log_tool`.

    Only `insert_tool_call` should still be invoked; `agent_run_steps` is fed
    exclusively by `record_step` from now on.
    """
    from aletheia.agents import helpers

    tool_calls: list[dict] = []
    run_steps: list[dict] = []

    def fake_insert_tool_call(run_id, tool_name, tool_class, permitted, *,
                               output_data=None, input_data=None):
        tool_calls.append({
            "run_id": run_id,
            "tool_name": tool_name,
            "tool_class": tool_class,
        })

    def fake_insert_run_step(**kwargs):
        run_steps.append(kwargs)

    import aletheia.storage.repositories as repo
    monkeypatch.setattr(repo, "insert_tool_call", fake_insert_tool_call)
    monkeypatch.setattr(repo, "insert_run_step", fake_insert_run_step)

    helpers.log_tool("run-1", "get_x", "read", True,
                     output="ok", input_data="{}")

    assert len(tool_calls) == 1
    assert run_steps == [], (
        "log_tool must not double-write agent_run_steps; see spec §Agent 8."
    )


# ---------------------------------------------------------------------------
# write_task_note: telemetry types skipped, failure/cancel always written
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "task_type,status,expected_written",
    [
        ("evaluate_strategy_state", "completed", False),
        ("generate_review", "completed", False),
        ("create_review_report", "completed", False),
        ("diagnose_system_health", "completed", False),
        ("set_symbol_observe_only", "completed", False),
        # Failure / cancellation always writes regardless of type.
        ("evaluate_strategy_state", "failed", True),
        ("evaluate_strategy_state", "cancelled", True),
        # Non-telemetry types always write.
        ("pi_reasoning_session", "completed", True),
    ],
)
def test_write_task_note_skips_telemetry_types(task_type, status, expected_written):
    from aletheia.agent_shell.file_artifacts import write_task_note

    task = {
        "id": f"t-{task_type}-{status}",
        "task_type": task_type,
        "status": status,
        "priority": "normal",
        "created_by": "test",
        "assigned_to": "operator",
        "input_data": {"symbol": "BTCUSDT"},
        "output_data": None,
        "error": None,
        "created_at": "2026-07-17T00:00:00Z",
        "started_at": "2026-07-17T00:00:00Z",
        "completed_at": "2026-07-17T00:00:00Z",
    }
    result = write_task_note(task)
    if expected_written:
        assert result is not None
        assert result.exists()
    else:
        assert result is None


# ---------------------------------------------------------------------------
# create_task: idempotency dedup + backpressure
# ---------------------------------------------------------------------------


def test_create_task_dedups_within_window():
    from aletheia.agent_shell.operator_store import create_task

    # The dedup path in ``create_task`` short-circuits only when an existing
    # row for the same semantic hash is in status IN ('running','completed')
    # (per RETENTION_POLICY.md and the Agent 6 spec) — a still-pending task is
    # deliberately not considered a duplicate. So the first insert here uses
    # ``status="running"`` to represent "task currently in flight" — the exact
    # state the guardrail is designed to protect against re-enqueue on.
    first = create_task(
        task_type="evaluate_strategy_state",
        status="running",
        priority="normal",
        created_by="unit_test",
        input_data={"symbol": "BTCUSDT", "direction": "long"},
    )
    # Same task_type + created_by + input_data hash within 4h window → dedup.
    second = create_task(
        task_type="evaluate_strategy_state",
        status="pending",
        priority="normal",
        created_by="unit_test",
        input_data={"symbol": "BTCUSDT", "direction": "long"},
    )
    assert second == first


def test_create_task_backpressure_evaluate_strategy_state():
    """Refuse to enqueue evaluate_strategy_state once >= 50 pending."""
    from aletheia.agent_shell.operator_store import create_task

    db = get_db()
    # Seed 50 pending rows directly to avoid dedup collapsing them.
    for i in range(50):
        db.execute(
            "INSERT INTO agent_shell_tasks "
            "(id, task_type, status, priority, created_by, assigned_to, "
            " input_data, created_at) "
            "VALUES (?, 'evaluate_strategy_state', 'pending', 'normal', "
            " ?, 'operator', ?, datetime('now'))",
            (str(uuid.uuid4()), f"seed-{i}", json.dumps({"i": i})),
        )
    db.commit()

    with pytest.raises(RuntimeError, match="backpressure"):
        create_task(
            task_type="evaluate_strategy_state",
            status="pending",
            priority="normal",
            created_by="unit_test",
            input_data={"symbol": "SOLUSDT"},
        )


def test_create_task_non_idempotent_types_bypass_dedup():
    """create_repair_proposal / pi_reasoning_session / create_diagnostic_report
    must NOT be deduped even when identical payloads arrive back-to-back."""
    from aletheia.agent_shell.operator_store import create_task

    a = create_task(
        task_type="pi_reasoning_session",
        status="pending",
        priority="normal",
        created_by="unit_test",
        input_data={"session": "s1"},
    )
    b = create_task(
        task_type="pi_reasoning_session",
        status="pending",
        priority="normal",
        created_by="unit_test",
        input_data={"session": "s1"},
    )
    assert a != b, "non-idempotent task types must never dedup"


# ---------------------------------------------------------------------------
# append_message: context messages stay latest-only per session
# ---------------------------------------------------------------------------


def test_append_message_context_keeps_latest_only():
    from aletheia.agent_shell.operator_store import (
        append_message,
        create_session_record,
    )

    session_id = create_session_record(
        session_id="s-ctx", user_input="probe",
    )
    append_message(
        session_id=session_id,
        role="assistant",
        content={"text": "first"},
        message_type="context",
    )
    append_message(
        session_id=session_id,
        role="assistant",
        content={"text": "second"},
        message_type="context",
    )
    append_message(
        session_id=session_id,
        role="assistant",
        content={"text": "third"},
        message_type="context",
    )

    db = get_db()
    ctx_count = db.execute(
        "SELECT COUNT(*) FROM agent_shell_messages "
        "WHERE session_id = ? AND message_type = 'context'",
        (session_id,),
    ).fetchone()[0]
    assert ctx_count == 1, (
        "append_message must keep only the newest context row per session"
    )

    # Non-context messages accumulate as normal.
    append_message(
        session_id=session_id, role="user",
        content={"text": "hello"}, message_type="message",
    )
    append_message(
        session_id=session_id, role="user",
        content={"text": "there"}, message_type="message",
    )
    total = db.execute(
        "SELECT COUNT(*) FROM agent_shell_messages WHERE session_id = ?",
        (session_id,),
    ).fetchone()[0]
    assert total == 3  # 1 context + 2 messages
