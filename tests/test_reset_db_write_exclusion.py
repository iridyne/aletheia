"""``reset_db`` must not free an injected connection under an inline writer.

An injected connection is shared across threads and ``run_write`` executes on
the calling thread for it, serialised only by the write lock. If ``reset_db``
closes that connection while another thread is inside ``db.execute``, sqlite3
frees the handle under that thread and the interpreter dies with SIGSEGV — which
pytest reports as ``0 failed`` because the process never gets to write a report.

These tests pin the exclusion deterministically instead of relying on the timing
that produced the crash in the wild.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from aletheia.storage import sqlite as sq


@pytest.fixture()
def injected_conn(tmp_path, monkeypatch):
    """Install a shared in-memory connection the way the suite's fixture does."""
    from aletheia.config import loader as _loader

    monkeypatch.setattr(_loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(_loader, "CONFIG_PATH", tmp_path / "config.yaml")

    sq.reset_db()
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    conn.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY, value TEXT)")
    conn.commit()

    yield conn

    sq.reset_db()


def test_reset_db_waits_for_an_inline_write_in_flight(injected_conn):
    """reset_db must block until a concurrent inline write releases the lock."""
    inside_write = threading.Event()
    may_finish_write = threading.Event()
    close_returned = threading.Event()
    errors: list[BaseException] = []

    def slow_write(db: sqlite3.Connection) -> None:
        # Runs while run_write holds the write lock, mirroring the crash frame:
        # a background thread mid-statement on the shared connection.
        db.execute("INSERT INTO probe (value) VALUES ('held')")
        inside_write.set()
        # Do not return until the main thread has tried to close underneath us.
        may_finish_write.wait(timeout=10)

    def writer() -> None:
        try:
            sq.run_write(slow_write)
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    thread = threading.Thread(target=writer, name="inline-writer")
    thread.start()
    assert inside_write.wait(timeout=10), "writer never reached the guarded region"

    def closer() -> None:
        try:
            sq.reset_db()
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            close_returned.set()

    close_thread = threading.Thread(target=closer, name="resetter")
    close_thread.start()

    # The exclusion is the whole point: while the writer holds the lock, the
    # close must not have completed. Without it, close_db() frees the handle
    # here and the writer's next connection touch is a use-after-free.
    assert not close_returned.wait(timeout=1.0), (
        "reset_db closed the shared connection while an inline write held the "
        "write lock — the connection can be freed under the writer"
    )

    may_finish_write.set()
    thread.join(timeout=10)
    assert close_returned.wait(timeout=10), "reset_db never completed"
    close_thread.join(timeout=10)
    assert errors == []


def test_reset_db_still_closes_when_no_writer_holds_the_lock(injected_conn):
    """The exclusion must not turn an ordinary teardown into a hang."""
    sq.run_write(lambda db: db.execute("INSERT INTO probe (value) VALUES ('done')"))

    done = threading.Event()

    def closer() -> None:
        sq.reset_db()
        done.set()

    thread = threading.Thread(target=closer, name="resetter")
    thread.start()
    assert done.wait(timeout=10), "reset_db blocked with no writer holding the lock"
    thread.join(timeout=10)
    assert sq._injected_db_active is False
