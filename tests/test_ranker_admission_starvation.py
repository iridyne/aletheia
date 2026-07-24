"""Tests for _check_ranker_admission_starvation (issue #329).

Four cases:
  1. Starvation: Ranker ran ≥ MIN_RUNS, 0 admissions, ≥ MIN_OPPS eligible → emit.
  2. No starvation: at least one run has signals_pending → no emit.
  3. Machine mostly offline (coverage < 60%) → no emit (nightly-shutdown guard).
  4. Too few runs / too few opps → no emit (not enough signal to be meaningful).
"""

import json
import pytest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_run(started_at: str, outputs: list[str], status: str = "completed") -> dict:
    return {
        "agent_name": "paper_trader",
        "status": status,
        "started_at": started_at,
        "outputs_created": json.dumps(outputs),
    }


def _session(started_at: str, ended_at: str | None = None) -> dict:
    return {"started_at": started_at, "ended_at": ended_at}


# ---------------------------------------------------------------------------
# test 1 — starvation fires
# ---------------------------------------------------------------------------

def test_starvation_emits_infra_event(monkeypatch):
    from aletheia.agent_runtime import health as _h

    emitted = []

    def fake_emit(**kwargs):
        emitted.append(kwargs)

    # Machine was online 100 % of the window
    monkeypatch.setattr(_h, "_ranker_session_coverage", lambda _w: 1.0)

    # Patch get_db so the function sees synthetic agent_runs + opportunity count
    import types

    class FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

        def fetchone(self):
            return self._rows[0] if self._rows else None

    class FakeDB:
        def execute(self, sql, params=()):
            if "agent_runs" in sql:
                return FakeCursor([
                    (json.dumps(["no_ranked_candidate"]),),
                ] * _h._RANKER_STARVATION_MIN_RUNS)
            if "opportunities" in sql:
                return FakeCursor([(_h._RANKER_STARVATION_MIN_OPPS,)])
            return FakeCursor([])

    import aletheia.storage.sqlite as _sqlite
    monkeypatch.setattr(_sqlite, "get_db", lambda: FakeDB())

    import aletheia.infra.events as _events
    monkeypatch.setattr(_events, "emit_infra_event", fake_emit)

    _h._check_ranker_admission_starvation()

    assert len(emitted) == 1
    evt = emitted[0]
    assert evt["code"] == "ranker_admission_starvation"
    assert evt["severity"] == "warning"
    assert evt["source"] == "ranker"


# ---------------------------------------------------------------------------
# test 2 — at least one admitted candidate suppresses the alert
# ---------------------------------------------------------------------------

def test_no_starvation_when_signals_pending(monkeypatch):
    from aletheia.agent_runtime import health as _h

    emitted = []
    monkeypatch.setattr(_h, "_ranker_session_coverage", lambda _w: 1.0)

    class FakeDB:
        def execute(self, sql, params=()):
            if "agent_runs" in sql:
                rows = [
                    (json.dumps(["no_ranked_candidate"]),),
                ] * (_h._RANKER_STARVATION_MIN_RUNS - 1)
                rows.append((json.dumps(["signals_pending:3"]),))
                return type("C", (), {"fetchall": lambda s: rows, "fetchone": lambda s: rows[0]})()
            return type("C", (), {"fetchall": lambda s: [], "fetchone": lambda s: None})()

    import aletheia.storage.sqlite as _sqlite
    monkeypatch.setattr(_sqlite, "get_db", lambda: FakeDB())

    import aletheia.infra.events as _events
    monkeypatch.setattr(_events, "emit_infra_event", lambda **kw: emitted.append(kw))

    _h._check_ranker_admission_starvation()

    assert emitted == []


# ---------------------------------------------------------------------------
# test 3 — machine mostly offline → no emit
# ---------------------------------------------------------------------------

def test_no_emit_when_machine_offline(monkeypatch):
    from aletheia.agent_runtime import health as _h

    emitted = []
    # Coverage below the 60 % threshold
    monkeypatch.setattr(_h, "_ranker_session_coverage", lambda _w: 0.3)

    import aletheia.infra.events as _events
    monkeypatch.setattr(_events, "emit_infra_event", lambda **kw: emitted.append(kw))

    _h._check_ranker_admission_starvation()

    assert emitted == []


# ---------------------------------------------------------------------------
# test 4a — too few Ranker runs → no emit
# ---------------------------------------------------------------------------

def test_no_emit_when_too_few_runs(monkeypatch):
    from aletheia.agent_runtime import health as _h

    emitted = []
    monkeypatch.setattr(_h, "_ranker_session_coverage", lambda _w: 1.0)

    class FakeDB:
        def execute(self, sql, params=()):
            if "agent_runs" in sql:
                # One fewer than minimum
                rows = [(json.dumps(["no_ranked_candidate"]),)] * (_h._RANKER_STARVATION_MIN_RUNS - 1)
                return type("C", (), {"fetchall": lambda s: rows})()
            return type("C", (), {"fetchall": lambda s: [], "fetchone": lambda s: (0,)})()

    import aletheia.storage.sqlite as _sqlite
    monkeypatch.setattr(_sqlite, "get_db", lambda: FakeDB())

    import aletheia.infra.events as _events
    monkeypatch.setattr(_events, "emit_infra_event", lambda **kw: emitted.append(kw))

    _h._check_ranker_admission_starvation()

    assert emitted == []


# ---------------------------------------------------------------------------
# test 4b — too few eligible opportunities → no emit
# ---------------------------------------------------------------------------

def test_no_emit_when_too_few_opps(monkeypatch):
    from aletheia.agent_runtime import health as _h

    emitted = []
    monkeypatch.setattr(_h, "_ranker_session_coverage", lambda _w: 1.0)

    class FakeDB:
        def execute(self, sql, params=()):
            if "agent_runs" in sql:
                rows = [(json.dumps(["no_ranked_candidate"]),)] * _h._RANKER_STARVATION_MIN_RUNS
                return type("C", (), {"fetchall": lambda s: rows})()
            if "opportunities" in sql:
                # One fewer than minimum
                return type("C", (), {"fetchone": lambda s: (_h._RANKER_STARVATION_MIN_OPPS - 1,)})()
            return type("C", (), {"fetchall": lambda s: [], "fetchone": lambda s: (0,)})()

    import aletheia.storage.sqlite as _sqlite
    monkeypatch.setattr(_sqlite, "get_db", lambda: FakeDB())

    import aletheia.infra.events as _events
    monkeypatch.setattr(_events, "emit_infra_event", lambda **kw: emitted.append(kw))

    _h._check_ranker_admission_starvation()

    assert emitted == []
