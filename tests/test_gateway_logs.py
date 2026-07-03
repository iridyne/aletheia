"""Tests for gateway log rotation + tail-only reads (§3 hardening).

A gateway crash loop streams stderr to errors.log; without a size cap it can fill
the disk, and a whole-file read in the CLI can OOM. rotate_if_needed() caps it to
50MB chunks with many retained generations and tail_logs() reads only the file's
tail.
"""

from aletheia.gateway import logs as gl


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)


def test_rotate_no_op_when_under_cap(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = gl.errors_log_path()
    p.write_text("small\n", encoding="utf-8")
    assert gl.rotate_if_needed(p, max_bytes=1024) is False
    assert p.exists()
    assert not p.with_suffix(p.suffix + ".1").exists()


def test_rotate_rolls_when_over_cap(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = gl.errors_log_path()
    p.write_text("x" * 5000, encoding="utf-8")
    assert gl.rotate_if_needed(p, max_bytes=1024) is True
    # original rolled to .1, fresh slot is free for the next open
    assert p.with_suffix(p.suffix + ".1").exists()
    assert not p.exists()


def test_rotate_keeps_multiple_backups(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = gl.errors_log_path()
    for label in ("a", "b", "c"):
        p.write_text(label * 5000, encoding="utf-8")
        gl.rotate_if_needed(p, max_bytes=1024, keep_generations=100)

    assert (p.with_name("errors.log.1")).read_text(encoding="utf-8").startswith("a")
    assert (p.with_name("errors.log.2")).read_text(encoding="utf-8").startswith("b")
    assert (p.with_name("errors.log.3")).read_text(encoding="utf-8").startswith("c")


def test_rotate_prunes_only_beyond_generation_limit(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = gl.errors_log_path()
    for label in ("a", "b", "c"):
        p.write_text(label * 5000, encoding="utf-8")
        gl.rotate_if_needed(p, max_bytes=1024, keep_generations=2)

    assert not (p.with_name("errors.log.1")).exists()
    assert (p.with_name("errors.log.2")).exists()
    assert (p.with_name("errors.log.3")).exists()


def test_tail_logs_reads_only_tail(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = gl.log_path()
    p.write_text("\n".join(f"line{i}" for i in range(1000)) + "\n", encoding="utf-8")
    out = gl.tail_logs(lines=10)
    assert out[-1] == "line999"
    assert len(out) == 10


def test_tail_logs_missing_file(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    assert gl.tail_logs() == []


def test_redacted_tail_masks_secret_like_values(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = gl.errors_log_path()
    p.write_text(
        "Authorization: Bearer sk-SECRETSECRETSECRET\napi_key=abc123SECRET\n",
        encoding="utf-8",
    )

    text = "\n".join(gl.tail_file_redacted(p, lines=5))
    assert "sk-SECRET" not in text
    assert "abc123SECRET" not in text
    assert "[REDACTED]" in text


def test_log_inventory_reports_active_and_rotated_files(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    gl.errors_log_path().write_text("x", encoding="utf-8")
    gl.errors_log_path().with_name("errors.log.1").write_text("old", encoding="utf-8")

    rows = gl.log_inventory()
    names = {row["name"] for row in rows if row["exists"]}
    assert {"errors.log", "errors.log.1"} <= names
