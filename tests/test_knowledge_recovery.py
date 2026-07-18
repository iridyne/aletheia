"""Knowledge body recovery — legacy Windows/POSIX file_path migration + scan report.

Covers two orthogonal recovery paths:

* ``retrieval._read_body`` must migrate a Windows-style or POSIX legacy path
  (``C:\\Users\\ollama\\Projects\\aletheia-trading-os\\data\\knowledge\\...``,
  ``/opt/prev-user/aletheia/data/knowledge/...``) to the current runtime root
  when the file is still on disk under the new layout. This is the code path
  that decides whether an orphan row is *actually* unindexable or just points
  at the pre-``~/.aletheia`` location.
* ``recovery.scan_orphan_bodies`` must report those recoverable rows so
  operators can copy files into the new root instead of blindly downgrading
  them. Rows that resolve nowhere are counted separately so downgrade work is
  a deliberate choice, not a coverage silent-failure.
"""

from pathlib import Path

from tests.test_experience_loop import setup_env


def _seed_row(item_type: str, title: str, file_path: str) -> None:
    from aletheia.storage.sqlite import get_db

    db = get_db()
    was_in_tx = db.in_transaction
    db.execute(
        "INSERT INTO knowledge_items (id, item_type, title, file_path, confidence, status) "
        "VALUES (?, ?, ?, ?, 'high_confidence', 'active')",
        (f"row_{title.replace(' ', '_')}", item_type, title, file_path),
    )
    if not was_in_tx:
        db.commit()


def test_read_body_migrates_windows_legacy_path(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.knowledge.retrieval import _read_body

    # New layout: body lives at ~/.aletheia/knowledge/research_notes/<file>.md.
    subdir = tmp_path / "knowledge" / "research_notes"
    subdir.mkdir(parents=True, exist_ok=True)
    body = "Recovered Windows legacy body."
    (subdir / "20260601-000000-1234-legacy.md").write_text(body, encoding="utf-8")

    win_legacy = (
        r"C:\Users\ollama\Projects\aletheia-trading-os\data\knowledge\research_notes\20260601-000000-1234-legacy.md"
    )
    assert _read_body(win_legacy).strip() == body, (
        "_read_body must resolve the legacy Windows path against the new data root"
    )


def test_read_body_migrates_posix_legacy_path(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.knowledge.retrieval import _read_body

    subdir = tmp_path / "knowledge" / "dreaming_logs"
    subdir.mkdir(parents=True, exist_ok=True)
    body = "Recovered POSIX legacy body."
    (subdir / "20260602-000000-1234-legacy.md").write_text(body, encoding="utf-8")

    posix_legacy = (
        "/opt/prev-user/aletheia/data/knowledge/dreaming_logs/20260602-000000-1234-legacy.md"
    )
    assert _read_body(posix_legacy).strip() == body


def test_scan_orphan_bodies_reports_recoverable_and_lost(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.knowledge.recovery import scan_orphan_bodies

    # Recoverable: file exists on disk at the current data root.
    recoverable_dir = tmp_path / "knowledge" / "research_notes"
    recoverable_dir.mkdir(parents=True, exist_ok=True)
    file_recoverable = recoverable_dir / "20260603-000000-ffee-recoverable.md"
    file_recoverable.write_text("Body still on disk.", encoding="utf-8")
    _seed_row(
        "research_note",
        "recoverable",
        str(file_recoverable),
    )

    # Lost: legacy Windows path, no matching disk body anywhere.
    _seed_row(
        "research_note",
        "lost",
        r"C:\Users\ollama\Projects\aletheia-trading-os\data\knowledge\research_notes\missing.md",
    )

    report = scan_orphan_bodies()
    assert report["total_orphans"] == 2
    assert report["recoverable"] == 1
    assert report["unrecoverable"] == 1
    assert "research_note" in report["by_type"]
    rn_stats = report["by_type"]["research_note"]
    assert rn_stats == {"total": 2, "recoverable": 1}

    resolved_paths = {c["title"]: c["resolved_path"] for c in report["candidates"]}
    assert resolved_paths["recoverable"] == str(file_recoverable)
    assert resolved_paths["lost"] is None
