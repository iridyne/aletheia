"""Runtime Session — identity of one Gateway PROCESS lifecycle.

    runtime-abc123                 (runtime_id — the running version)
      ├── session-001  first boot
      ├── session-002  gateway restart
      └── session-003  restart after a config change

Every boot of the trading gateway creates one `runtime_sessions` row and holds its
identity in-process. The seven core trading-fact entities are stamped with that
identity at insert time (see repositories._stamp_provenance) so any row can be
traced to the exact process, commit, and behaviour-config that produced it.

This module owns ONLY the session lifecycle + fingerprints + the in-process
"current session" pointer. DB writes go through storage.repositories. It never
raises into the caller — a provenance failure must not break trading.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("aletheia.runtime_session")

_SCOPE_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs" / "product" / "ACTIVE_RUNTIME_SCOPE.md"
)

# In-process pointer to the current session (a RuntimeSession.model_dump() dict),
# or None when no session is active (tests, out-of-band inserts → NULL provenance).
_current_session: dict | None = None
# True only when the current session row has been verified as durably persisted in the DB.
# Non-durable sessions must NOT stamp child rows with runtime_session_id.
_session_durable: bool = False


def _sha(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def config_hash(cfg) -> str:
    """Stable fingerprint of the FULL config snapshot this session booted with."""
    try:
        return _sha(cfg.model_dump(mode="json"))
    except Exception:
        return _sha(str(cfg))


def _behavior_relevant(cfg) -> dict:
    """The behaviour-affecting subset of config. Narrow on purpose — expand later.

    Uses getattr fallbacks so a partial/older config never crashes fingerprinting.
    """
    def g(obj, attr, default=None):
        return getattr(obj, attr, default)

    agent = g(cfg, "agent")
    exchange = g(cfg, "exchange")
    models = g(cfg, "models")
    learning = g(cfg, "learning")
    risk = g(cfg, "risk")
    return {
        "permission_mode": g(agent, "permission_mode"),
        "autonomous_learning": g(agent, "autonomous_learning"),
        "paper_execution_mode": g(exchange, "paper_execution_mode"),
        "default_provider": g(models, "default_provider"),
        "risk_policies": {
            "demo": (
                g(g(risk, "demo"), "model_dump", lambda **_: {})(
                    mode="json"
                )
                if g(risk, "demo") is not None else None
            ),
            "live": (
                g(g(risk, "live"), "model_dump", lambda **_: {})(
                    mode="json"
                )
                if g(risk, "live") is not None else None
            ),
        },
        "learning": {
            "paper_influence_enabled": g(learning, "paper_influence_enabled"),
            "lifecycle_automation_enabled": g(learning, "lifecycle_automation_enabled"),
            "paper_influence_paths": sorted(g(learning, "paper_influence_paths", []) or []),
        },
    }


def behavior_fingerprint(cfg) -> str:
    """Fingerprint of the behaviour-affecting config (decisions/influence/mode)."""
    return _sha(_behavior_relevant(cfg))


def _boot_commit() -> str | None:
    try:
        from aletheia.agent_runtime.build_info import get_boot_commit

        commit = get_boot_commit()
        if commit:
            return commit
    except Exception:
        pass
    try:
        from aletheia.update.git_ops import current_commit

        commit = current_commit()
        return commit or None
    except Exception:
        return None


def _active_scope_id() -> str | None:
    try:
        from aletheia.knowledge.frontmatter import parse_frontmatter

        meta, _ = parse_frontmatter(_SCOPE_PATH)
        return (meta or {}).get("scope_id")
    except Exception:
        return None


def _runtime_id(runtime_context: dict | None) -> str:
    if runtime_context and runtime_context.get("runtime_id"):
        return str(runtime_context["runtime_id"])
    return os.environ.get("ALETHEIA_RUNTIME_ID", "runtime-local-current")


def start_runtime_session(cfg=None, runtime_context: dict | None = None) -> dict:
    """Create and persist a Runtime Session for this process; set it as current.

    Returns the session dict (always includes a ``durable`` key).  Best-effort:
    on any persistence or verification error the in-memory session is still set so
    the process can continue, but ``durable`` will be False and child rows will NOT
    carry a ``runtime_session_id`` — they stay NULL rather than advertise an orphan
    identity.
    """
    global _current_session, _session_durable
    if cfg is None:
        from aletheia.config.loader import load_config

        cfg = load_config()

    from aletheia.storage.migrations import SCHEMA_VERSION
    from aletheia.storage.models import RuntimeSession

    session = RuntimeSession(
        runtime_id=_runtime_id(runtime_context),
        boot_commit=_boot_commit(),
        config_hash=config_hash(cfg),
        behavior_fingerprint=behavior_fingerprint(cfg),
        schema_version=SCHEMA_VERSION,
        active_scope_id=_active_scope_id(),
        execution_mode=getattr(getattr(cfg, "exchange", None), "paper_execution_mode", None),
    )
    durable = False
    try:
        from aletheia.storage import repositories as repo

        repo.insert_runtime_session(session)
        # Verify the row actually landed with the expected immutable fingerprints.
        row = repo.get_runtime_session(session.session_id)
        if (
            row is not None
            and row["session_id"] == session.session_id
            and row["behavior_fingerprint"] == session.behavior_fingerprint
        ):
            durable = True
        else:
            logger.warning(
                "runtime session verification failed after insert (row absent or fingerprint mismatch): %s",
                session.session_id,
            )
    except Exception as e:
        logger.warning("runtime session persist failed (continuing in-memory): %s", e)

    _current_session = {**session.model_dump(), "durable": durable}
    _session_durable = durable
    logger.info(
        "runtime session started: %s (runtime_id=%s, commit=%s, mode=%s, durable=%s)",
        session.session_id, session.runtime_id,
        (session.boot_commit or "-")[:12], session.execution_mode, durable,
    )
    return dict(_current_session)


def end_runtime_session(clean: bool = True) -> None:
    """Mark the current session ended (best-effort). Clears the current pointer."""
    global _current_session
    session = _current_session
    _current_session = None
    if not session:
        return
    ended_at = datetime.now(timezone.utc).isoformat()
    try:
        from aletheia.storage import repositories as repo

        repo.update_runtime_session(
            session["session_id"], ended_at=ended_at, shutdown_clean=1 if clean else 0,
        )
    except Exception as e:
        logger.warning("runtime session end update failed: %s", e)


def current_session() -> dict | None:
    return dict(_current_session) if _current_session else None


def current_session_id() -> str | None:
    return _current_session["session_id"] if _current_session else None


def current_provenance() -> dict:
    """Provenance to stamp on a row PRODUCED by the current session.

    ``runtime_session_id`` is only populated when the session is durably persisted —
    a non-durable (in-memory-only) session must not be stamped as a durable reference.
    ``producer_commit`` and ``behavior_fingerprint`` are stamped whenever a session is
    active (durable or not) so the row is not completely opaque even in degraded mode.
    All-None when no session is active.
    """
    s = _current_session
    if not s:
        return {
            "runtime_session_id": None,
            "producer_commit": None,
            "behavior_fingerprint": None,
        }
    return {
        "runtime_session_id": s["session_id"] if _session_durable else None,
        "producer_commit": s["boot_commit"],
        "behavior_fingerprint": s["behavior_fingerprint"],
    }


def current_close_provenance() -> dict:
    """Provenance to stamp on a position when it is CLOSED by the current session.

    Distinct column names so open (created) and close (outcome) producers coexist.
    Same durability rule as ``current_provenance``: ``closed_runtime_session_id`` is
    only populated when the closing session is durably persisted.
    """
    s = _current_session
    if not s:
        return {
            "closed_runtime_session_id": None,
            "closed_producer_commit": None,
            "closed_behavior_fingerprint": None,
        }
    return {
        "closed_runtime_session_id": s["session_id"] if _session_durable else None,
        "closed_producer_commit": s["boot_commit"],
        "closed_behavior_fingerprint": s["behavior_fingerprint"],
    }


def reset_current_session() -> None:
    """Drop the in-process current-session pointer without touching the DB (tests)."""
    global _current_session, _session_durable
    _current_session = None
    _session_durable = False


def current_session_durable() -> bool:
    """Return True when the active session has been verified as durably persisted."""
    return _session_durable


def retry_session_durability() -> bool:
    """Attempt to verify/repair durability for the current in-memory session.

    Safe to call repeatedly — idempotent. Returns True if the session is now durable.
    Raises nothing; logs on failure.
    """
    global _session_durable
    s = _current_session
    if not s:
        return False
    if _session_durable:
        return True
    try:
        from aletheia.storage import repositories as repo

        row = repo.get_runtime_session(s["session_id"])
        if (
            row is not None
            and row["session_id"] == s["session_id"]
            and row["behavior_fingerprint"] == s["behavior_fingerprint"]
        ):
            _session_durable = True
            logger.info("runtime session durability recovered: %s", s["session_id"])
            return True
        # Row absent — attempt re-insert only if fingerprints are still consistent.
        if row is None:
            from aletheia.storage.models import RuntimeSession

            session_obj = RuntimeSession(**{
                k: v for k, v in s.items()
                if k in RuntimeSession.model_fields and k != "durable"
            })
            repo.insert_runtime_session(session_obj)
            row = repo.get_runtime_session(s["session_id"])
            if (
                row is not None
                and row["session_id"] == s["session_id"]
                and row["behavior_fingerprint"] == s["behavior_fingerprint"]
            ):
                _session_durable = True
                logger.info("runtime session durability restored via re-insert: %s", s["session_id"])
                return True
        logger.warning(
            "runtime session durability retry failed (fingerprint conflict or db error): %s", s["session_id"]
        )
    except Exception as e:
        logger.warning("runtime session durability retry exception: %s", e)
    return False
