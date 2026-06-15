import sqlite3

from aletheia.storage.migrations import run_migrations
from aletheia.storage.sqlite import reset_db


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


def test_notify_operator_writes_local_records_and_windows_toast(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia import notifications

    toasts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        notifications,
        "send_windows_toast",
        lambda title, message: toasts.append((title, message)),
    )

    notifications.notify_operator(
        "Gateway failed",
        "spawn verifier timed out",
        source="watchdog",
        entity_type="gateway",
        entity_id="gateway",
    )

    assert toasts == [("Gateway failed", "spawn verifier timed out")]

    from aletheia.files.jsonl import read_jsonl
    audit = read_jsonl("audit.jsonl")
    errors = read_jsonl("errors.jsonl")
    assert any(e.get("event_type") == "operator_notification" for e in audit)
    assert any(e.get("source") == "watchdog" and e.get("title") == "Gateway failed" for e in errors)


def test_notify_operator_respects_windows_toast_config(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.health.alert_via_windows_toast = False
    save_config(cfg)

    from aletheia import notifications
    toasts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        notifications,
        "send_windows_toast",
        lambda title, message: toasts.append((title, message)),
    )

    notifications.notify_operator("Health degraded", "market data stale", source="health")

    assert toasts == []
