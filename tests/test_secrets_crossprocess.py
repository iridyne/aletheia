"""Cross-process secrets concurrency regression tests — #1101."""

import multiprocessing
from pathlib import Path

import pytest


# ─── Multiprocessing workers (module-level so they're picklable under 'spawn') ─


def _mp_file_set(data_dir_str: str, key: str, value: str, ready, go) -> None:
    import aletheia.config.secrets as s
    s.get_data_dir = lambda: Path(data_dir_str)
    ready.set()
    go.wait(timeout=5)
    s._file_set(key, value)


def _mp_file_delete(data_dir_str: str, key: str, ready, go) -> None:
    import aletheia.config.secrets as s
    s.get_data_dir = lambda: Path(data_dir_str)
    ready.set()
    go.wait(timeout=5)
    s._file_delete(key)


def _mp_first_use_set(data_dir_str: str, key: str, value: str, ready, go) -> None:
    """Worker that starts with no .secret.key, triggers first-use key creation."""
    import aletheia.config.secrets as s
    s.get_data_dir = lambda: Path(data_dir_str)
    ready.set()
    go.wait(timeout=5)
    s._file_set(key, value)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _read_store(tmp_path: Path) -> dict:
    """Decrypt and return the current secrets store as a plain dict."""
    import aletheia.config.secrets as s
    s.get_data_dir = lambda: tmp_path
    path = tmp_path / "secrets.enc"
    if not path.exists():
        return {}
    import json
    fernet = s._get_fernet()
    return json.loads(fernet.decrypt(path.read_bytes()).decode())


def _seed(tmp_path: Path, data: dict) -> None:
    """Write initial secrets into the store under tmp_path."""
    import aletheia.config.secrets as s
    s.get_data_dir = lambda: tmp_path
    for k, v in data.items():
        s._file_set(k, v)


# ─── Tests ───────────────────────────────────────────────────────────────────


def test_concurrent_writes_to_different_keys_both_survive(tmp_path):
    """Two processes writing different keys concurrently: both must survive."""
    _seed(tmp_path, {"existing": "seed"})

    ctx = multiprocessing.get_context("spawn")
    ready_a = ctx.Event()
    ready_b = ctx.Event()
    go = ctx.Event()

    p1 = ctx.Process(
        target=_mp_file_set,
        args=(str(tmp_path), "operator_service_token", "token-A", ready_a, go),
    )
    p2 = ctx.Process(
        target=_mp_file_set,
        args=(str(tmp_path), "remote_control_api_token", "token-B", ready_b, go),
    )
    p1.start()
    p2.start()
    ready_a.wait(timeout=10)
    ready_b.wait(timeout=10)
    go.set()
    p1.join(timeout=15)
    p2.join(timeout=15)

    assert p1.exitcode == 0, f"process A failed: {p1.exitcode}"
    assert p2.exitcode == 0, f"process B failed: {p2.exitcode}"

    import aletheia.config.secrets as s
    s.get_data_dir = lambda: tmp_path
    final = _read_store(tmp_path)
    assert final.get("operator_service_token") == "token-A", "process A's key was lost"
    assert final.get("remote_control_api_token") == "token-B", "process B's key was lost"
    assert final.get("existing") == "seed", "pre-existing key was destroyed"


def test_concurrent_set_and_delete_preserve_unrelated_key(tmp_path):
    """Concurrent set + delete must not destroy an unrelated third key."""
    _seed(tmp_path, {"keep": "safe", "delete_me": "gone"})

    ctx = multiprocessing.get_context("spawn")
    ready_a = ctx.Event()
    ready_b = ctx.Event()
    go = ctx.Event()

    p1 = ctx.Process(
        target=_mp_file_set,
        args=(str(tmp_path), "new_key", "new_value", ready_a, go),
    )
    p2 = ctx.Process(
        target=_mp_file_delete,
        args=(str(tmp_path), "delete_me", ready_b, go),
    )
    p1.start()
    p2.start()
    ready_a.wait(timeout=10)
    ready_b.wait(timeout=10)
    go.set()
    p1.join(timeout=15)
    p2.join(timeout=15)

    assert p1.exitcode == 0, f"set process failed: {p1.exitcode}"
    assert p2.exitcode == 0, f"delete process failed: {p2.exitcode}"

    final = _read_store(tmp_path)
    assert final.get("keep") == "safe", "unrelated key was destroyed"
    assert "delete_me" not in final, "target key was not deleted"
    assert final.get("new_key") == "new_value", "set key was lost"


def test_simultaneous_first_use_key_creation_yields_readable_store(tmp_path):
    """Two processes starting with no .secret.key must both write a readable store."""
    assert not (tmp_path / ".secret.key").exists()

    ctx = multiprocessing.get_context("spawn")
    ready_a = ctx.Event()
    ready_b = ctx.Event()
    go = ctx.Event()

    p1 = ctx.Process(
        target=_mp_first_use_set,
        args=(str(tmp_path), "key_a", "value_a", ready_a, go),
    )
    p2 = ctx.Process(
        target=_mp_first_use_set,
        args=(str(tmp_path), "key_b", "value_b", ready_b, go),
    )
    p1.start()
    p2.start()
    ready_a.wait(timeout=10)
    ready_b.wait(timeout=10)
    go.set()
    p1.join(timeout=15)
    p2.join(timeout=15)

    assert p1.exitcode == 0, f"process A failed: {p1.exitcode}"
    assert p2.exitcode == 0, f"process B failed: {p2.exitcode}"

    # Exactly one .secret.key must exist and must decrypt the store.
    assert (tmp_path / ".secret.key").exists(), "no .secret.key was created"
    final = _read_store(tmp_path)
    # Both keys must be readable with the surviving Fernet key.
    assert "key_a" in final, "key_a missing from store"
    assert "key_b" in final, "key_b missing from store"


def test_corrupted_store_raises_on_write_not_replaced(tmp_path):
    """A corrupt/undecryptable secrets.enc must cause a write failure, not silent replacement."""
    import aletheia.config.secrets as s
    s.get_data_dir = lambda: tmp_path

    # Create a valid key, then corrupt the store.
    s._file_set("real_key", "real_value")
    (tmp_path / "secrets.enc").write_bytes(b"this-is-not-valid-fernet-data")

    with pytest.raises(RuntimeError, match="failed to read encrypted secret store"):
        s._file_set("new_key", "new_value")

    # The corrupt file must still be corrupt (not replaced with a one-key map).
    content = (tmp_path / "secrets.enc").read_bytes()
    assert content == b"this-is-not-valid-fernet-data", "corrupt store was silently replaced"


def test_lock_acquire_timeout_raises(tmp_path, monkeypatch):
    """If the file lock cannot be acquired, TimeoutError propagates cleanly."""
    import aletheia.config.secrets as s
    s.get_data_dir = lambda: tmp_path

    (tmp_path).mkdir(parents=True, exist_ok=True)
    held_fd = s._acquire_file_lock()
    try:
        monkeypatch.setattr(s, "_WRITE_LOCK_TIMEOUT_S", 0.05)
        with pytest.raises(TimeoutError, match="secrets_write_lock_timeout"):
            s._acquire_file_lock()
    finally:
        s._release_file_lock(held_fd)
