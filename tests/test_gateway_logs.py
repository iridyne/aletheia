"""Tests for gateway log rotation + tail-only reads (§3 hardening).

A gateway crash loop streams stderr to errors.log; without a size cap it can fill
the disk, and a whole-file read in the CLI can OOM. rotate_if_needed() caps it to a
single backup and tail_logs() reads only the file's tail.
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


def test_rotate_keeps_single_backup(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = gl.errors_log_path()
    # First rotation
    p.write_text("a" * 5000, encoding="utf-8")
    gl.rotate_if_needed(p, max_bytes=1024)
    # Second rotation overwrites the previous backup (no unbounded .1/.2/.3 growth)
    p.write_text("b" * 5000, encoding="utf-8")
    gl.rotate_if_needed(p, max_bytes=1024)
    backup = p.with_suffix(p.suffix + ".1")
    assert backup.read_text(encoding="utf-8").startswith("b")


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
