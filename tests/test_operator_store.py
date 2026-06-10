import sqlite3


def _init_test_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.sqlite import reset_db
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    return conn


def test_operator_tables_exist(tmp_path, monkeypatch):
    conn = _init_test_db(tmp_path, monkeypatch)

    names = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    assert "agent_shell_messages" in names
    assert "agent_shell_tasks" in names
    assert "agent_shell_artifacts" in names
    assert "agent_shell_heartbeats" in names


def test_record_and_fetch_latest_operator_heartbeat(tmp_path, monkeypatch):
    _init_test_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.operator_store import (
        get_latest_heartbeat,
        record_heartbeat,
    )

    record_heartbeat(
        agent_id="operator",
        status="online",
        mode="Observing",
        permission_mode="ReadOnly",
        is_busy=False,
        current_task_id=None,
        current_intent="monitoring system state",
        queue_size=0,
        last_error=None,
        model_provider="openai-compatible",
        session_id="operator-main",
    )

    hb = get_latest_heartbeat()

    assert hb is not None
    assert hb["agent_id"] == "operator"
    assert hb["mode"] == "Observing"
    assert hb["permission_mode"] == "ReadOnly"
    assert hb["last_seen_at"] is not None
    assert hb["last_success_at"] is not None


def test_create_update_and_list_operator_tasks(tmp_path, monkeypatch):
    _init_test_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.operator_store import (
        create_task,
        list_tasks,
        update_task,
    )

    task_id = create_task(
        task_type="diagnose_failure",
        status="running",
        priority="normal",
        created_by="operator_loop",
        assigned_to="operator",
        input_data={"entity_id": "run-1"},
    )

    update_task(task_id, status="completed", output_data={"summary": "ok"})

    tasks = list_tasks(limit=10)

    assert tasks[0]["id"] == task_id
    assert tasks[0]["status"] == "completed"
    assert tasks[0]["output_data"] == {"summary": "ok"}


def test_append_and_list_session_messages(tmp_path, monkeypatch):
    _init_test_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.operator_store import (
        append_message,
        list_messages,
    )

    append_message(
        session_id="session-1",
        role="assistant",
        content={"text": "hello"},
        message_type="message",
        related_event_id=None,
    )

    messages = list_messages("session-1")

    assert len(messages) == 1
    assert messages[0]["role"] == "assistant"
    assert messages[0]["content"] == {"text": "hello"}


def test_create_and_list_task_artifacts(tmp_path, monkeypatch):
    _init_test_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.operator_store import (
        create_artifact,
        create_task,
        list_artifacts,
    )

    task_id = create_task(
        task_type="diagnose_failure",
        status="running",
        priority="normal",
        created_by="operator_loop",
        assigned_to="operator",
        input_data={"entity_id": "run-1"},
    )

    create_artifact(
        task_id=task_id,
        session_id="operator-main",
        artifact_type="repair_proposal",
        title="Repair proposal",
        content={"summary": "restart gateway"},
    )

    artifacts = list_artifacts(task_id=task_id)

    assert len(artifacts) == 1
    assert artifacts[0]["artifact_type"] == "repair_proposal"
    assert artifacts[0]["content"] == {"summary": "restart gateway"}
