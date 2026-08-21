"""Versioned runtime continuity state machine.

This module deliberately stays in the operator/gateway control plane. It records
candidate, shadow, promotion, drain, and rollback state; it does not place orders,
unlock live trading, or change risk limits.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import json
from uuid import uuid4

logger = logging.getLogger(__name__)

from aletheia.gateway.frontdoor_route import read_frontdoor_route, write_frontdoor_route
from aletheia.storage.migrations import assert_migrations_current
from aletheia.storage.sqlite import get_db


class RuntimeState(StrEnum):
    INSTALLED = "installed"
    CANDIDATE = "candidate"
    SHADOWING = "shadowing"
    READY_TO_PROMOTE = "ready_to_promote"
    CURRENT = "current"
    DRAINING = "draining"
    LEGACY_POSITION_MANAGER = "legacy_position_manager"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class RuntimeTransitionError(RuntimeError):
    """Raised when a runtime continuity transition is rejected."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _json_dump(value: object) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _json_load(value: str | None) -> object:
    if value in (None, ""):
        return {}
    return json.loads(value)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def _kill_shadow_process(runtime: dict) -> None:
    """Terminate shadow gateway process if its PID is recorded."""
    import os
    import signal
    metadata = runtime.get("metadata")
    if isinstance(metadata, str):
        metadata = _json_load(metadata)
    gw = (metadata or {}).get("gateway_process") or {}
    pid = gw.get("pid")
    if not pid:
        return
    try:
        os.kill(int(pid), signal.SIGTERM)
        logger.info("Killed shadow gateway process pid=%d for runtime %s", pid, runtime.get("runtime_id"))
    except (ProcessLookupError, OSError):
        pass


def _decode_row(row) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    for key in ("metadata", "payload"):
        if key in item:
            item[key] = _json_load(item[key])
    return item


def _route_target(runtime: dict | None) -> dict | None:
    metadata = dict((runtime or {}).get("metadata") or {})
    gateway_process = metadata.get("gateway_process")
    if not isinstance(gateway_process, dict):
        return None
    if gateway_process.get("ready") is False or gateway_process.get("started") is False:
        return None
    base_url = gateway_process.get("base_url")
    if not base_url:
        return None
    return {
        "backend_base_url": str(base_url),
        "backend_pid": gateway_process.get("pid"),
    }


def _activation_plan(*, runtime_id: str, action: str, runtime: dict | None = None) -> dict:
    route = _route_target(runtime)
    if route is not None:
        return {
            "runtime_id": runtime_id,
            "action": action,
            "strategy": "frontdoor_route_switch",
            "restart_required": False,
            "reason": "ready_shadow_backend_can_be_promoted_without_rebinding_frontdoor",
            "backend_base_url": route["backend_base_url"],
            "backend_pid": route.get("backend_pid"),
            "created_at": _utc_now(),
        }
    return {
        "runtime_id": runtime_id,
        "action": action,
        "strategy": "supervisor_respawn",
        "restart_required": True,
        "reason": "no_ready_shadow_backend_route_available",
        "created_at": _utc_now(),
    }


class RuntimeContinuityService:
    def __init__(
        self,
        *,
        lock_ttl_sec: int = 300,
        enabled: bool = False,
        shadow_gateway_launcher=None,
        shadow_live_prober=None,
    ):
        self.lock_ttl_sec = lock_ttl_sec
        self.enabled = enabled
        self.shadow_gateway_launcher = shadow_gateway_launcher
        # Optional override for tests; production uses live_probe_shadow from processes.
        self.shadow_live_prober = shadow_live_prober
        assert_migrations_current()
        self.reconcile_pending_intents()

    def reconcile_pending_intents(self) -> None:
        """#1199: re-apply any pending transition intents so a crash between intent
        creation and route file switch (or between switch and intent completion mark)
        self-heals on the next boot or transition.
        """
        db = self._db()
        pending = db.execute(
            "SELECT * FROM runtime_transition_intent WHERE state = 'pending' ORDER BY created_at ASC"
        ).fetchall()
        for row in pending:
            intent_id = row["id"]
            new_route = row["new_route"]
            try:
                current_route = read_frontdoor_route()
                if current_route and current_route.get("backend_base_url") == new_route:
                    # Route file already switched; mark complete.
                    db.execute(
                        "UPDATE runtime_transition_intent SET state = 'complete', completed_at = ? WHERE id = ?",
                        (_utc_now(), intent_id),
                    )
                    db.commit()
                    logger.info("Reconciled pending intent %s: route file already matches new_route.", intent_id)
                else:
                    # Route file not switched; re-attempt idempotently.
                    logger.warning("Reconciling pending intent %s: re-attempting route switch to %s", intent_id, new_route)
                    # The write_frontdoor_route is idempotent; calling it again is safe.
                    write_frontdoor_route(
                        backend_base_url=new_route,
                        runtime_id=str(row.get("action") or "unknown"),
                        runtime_mode="current",
                        runtime_state="current",
                        reason="reconcile_pending_intent",
                    )
                    db.execute(
                        "UPDATE runtime_transition_intent SET state = 'complete', completed_at = ? WHERE id = ?",
                        (_utc_now(), intent_id),
                    )
                    db.commit()
                    logger.info("Reconciled pending intent %s: route switch completed.", intent_id)
            except Exception as exc:
                logger.error("Failed to reconcile pending intent %s: %s", intent_id, exc, exc_info=True)

    def _db(self):
        return get_db()

    def _require_enabled(self, op_name: str) -> None:
        """Guard mutating operations behind the runtime_continuity_enabled kill switch.

        #82 fix: settings UI + config schema present runtime continuity as opt-in
        (default False, "Experimental"), but the write methods previously ran
        regardless — an owner who kept the switch off could still have candidate /
        shadow / promote / drain / rollback mutations happen via the API. Now they
        raise RuntimeTransitionError with detail 'runtime_continuity_disabled:<op>'
        which the routes surface as HTTP 409. Read paths (status, list_events,
        current_runtime_id) stay unguarded so the dashboard can render recorded
        state even when the feature is off.
        """
        if not self.enabled:
            raise RuntimeTransitionError(f"runtime_continuity_disabled:{op_name}")

    def _record_event(
        self,
        *,
        runtime_id: str | None,
        event_type: str,
        phase: str,
        actor: str,
        cause: str | None = None,
        severity: str = "info",
        payload: dict | None = None,
        operation_id: str | None = None,
        kind: str = "runtime_transition",
        status: str = "recorded",
    ) -> str:
        db = self._db()
        event_id = _new_id("runtime-event")
        db.execute(
            """
            INSERT OR IGNORE INTO runtime_events (
                id, runtime_id, event_type, phase, severity, actor, cause,
                kind, status, operation_id, payload, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                runtime_id,
                event_type,
                phase,
                severity,
                actor,
                cause,
                kind,
                status,
                operation_id,
                _json_dump(payload or {}),
                _utc_now(),
            ),
        )
        return event_id

    def _apply_activation(self, activation: dict, *, actor: str, cause: str, fencing_token: str | None = None) -> None:
        """Attempt Frontdoor route switch with two-phase intent for #1199.

        Phase 1: persist transition intent row (old_route, new_route, state=pending) — commit.
        Phase 2: atomically replace the route file.
        Phase 3: mark intent complete — commit.

        On boot / before each transition, reconcile_pending_intents() re-applies any
        state=pending intent idempotently so a crash between phase 1 and 3 self-heals.

        Raises RuntimeTransitionError on failure; the two-phase persist means DB and
        route file are always reconcilable regardless of crash timing.
        """
        if activation.get("strategy") != "frontdoor_route_switch":
            return
        db = self._db()
        old_route = read_frontdoor_route()
        intent_id = _new_id("rt-intent")
        ft = fencing_token or uuid4().hex
        new_route_url = str(activation["backend_base_url"])
        # Phase 1: record pending intent — commit before touching the route file.
        if db.in_transaction:
            db.execute("SAVEPOINT intent_write")
        else:
            db.execute("BEGIN")
        try:
            db.execute(
                """
                INSERT INTO runtime_transition_intent
                    (id, fencing_token, action, old_route, new_route, state, created_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    intent_id,
                    ft,
                    str(activation.get("action") or "activate"),
                    _json_dump(old_route),
                    new_route_url,
                    _utc_now(),
                ),
            )
            if db.in_transaction:
                db.execute("RELEASE intent_write")
            db.execute("COMMIT")
        except BaseException:
            try:
                if db.in_transaction:
                    db.execute("ROLLBACK TO intent_write")
                    db.execute("RELEASE intent_write")
            except Exception:
                pass
            raise
        # Phase 2: atomically replace route file.
        try:
            write_frontdoor_route(
                backend_base_url=new_route_url,
                runtime_id=str(activation["runtime_id"]),
                runtime_mode="current",
                runtime_state=RuntimeState.CURRENT.value,
                backend_pid=activation.get("backend_pid"),
                reason=f"runtime_{activation.get('action') or 'activate'}",
            )
        except Exception as exc:
            raise RuntimeTransitionError(f"frontdoor_route_switch_failed:{exc}") from exc
        # Phase 3: mark intent complete.
        try:
            db.execute("BEGIN")
            db.execute(
                "UPDATE runtime_transition_intent SET state = 'complete', completed_at = ? WHERE id = ?",
                (_utc_now(), intent_id),
            )
            self._record_event(
                runtime_id=str(activation["runtime_id"]),
                event_type="frontdoor_route_switched",
                phase="activation",
                actor=actor,
                cause=cause,
                payload={**activation, "intent_id": intent_id},
            )
            db.execute("COMMIT")
        except Exception:
            # Best-effort: route file is already switched. Log and continue.
            logger.warning("Failed to mark transition intent complete (intent_id=%s); will reconcile on next boot.", intent_id)
            try:
                db.execute("ROLLBACK")
            except Exception:
                pass

    def _current_runtime(self) -> dict | None:
        row = self._db().execute(
            "SELECT * FROM runtime_registry WHERE state = ? "
            "ORDER BY COALESCE(promoted_at, created_at) DESC, rowid DESC LIMIT 1",
            (RuntimeState.CURRENT.value,),
        ).fetchone()
        return _decode_row(row)

    def _runtime(self, runtime_id: str) -> dict | None:
        row = self._db().execute(
            "SELECT * FROM runtime_registry WHERE runtime_id = ?",
            (runtime_id,),
        ).fetchone()
        return _decode_row(row)

    def _candidate_runtime(self) -> dict | None:
        row = self._db().execute(
            """
            SELECT * FROM runtime_registry
            WHERE state IN ('candidate', 'shadowing', 'ready_to_promote', 'failed')
            ORDER BY updated_at DESC, rowid DESC LIMIT 1
            """
        ).fetchone()
        return _decode_row(row)

    def _draining_runtime(self) -> dict | None:
        row = self._db().execute(
            "SELECT * FROM runtime_registry WHERE state = ? ORDER BY updated_at DESC, rowid DESC LIMIT 1",
            (RuntimeState.DRAINING.value,),
        ).fetchone()
        return _decode_row(row)

    def _latest_checkpoint(self, checkpoint_type: str) -> dict | None:
        row = self._db().execute(
            "SELECT * FROM runtime_checkpoints WHERE checkpoint_type = ? AND status = 'available' "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (checkpoint_type,),
        ).fetchone()
        return _decode_row(row)

    def _active_locks(self) -> list[dict]:
        now = _utc_now()
        rows = self._db().execute(
            """
            SELECT * FROM runtime_exclusive_lock
            WHERE released_at IS NULL AND expires_at > ?
            ORDER BY acquired_at DESC
            """,
            (now,),
        ).fetchall()
        return [_decode_row(row) for row in rows]

    def _release_expired_locks(self, *, actor: str = "system") -> None:
        now = _utc_now()
        rows = self._db().execute(
            "SELECT * FROM runtime_exclusive_lock WHERE released_at IS NULL AND expires_at <= ?",
            (now,),
        ).fetchall()
        released_any = False
        for row in rows:
            self._db().execute(
                "UPDATE runtime_exclusive_lock SET released_at = ? WHERE lock_name = ?",
                (now, row["lock_name"]),
            )
            released_any = True
            self._record_event(
                runtime_id=row["runtime_id"],
                event_type="runtime_lock_expired",
                phase="lock",
                severity="warning",
                actor=actor,
                cause="lock_ttl_expired",
                payload={"lock_name": row["lock_name"], "owner": row["owner"]},
            )
        if released_any:
            self._db().commit()

    def current_runtime_id(self) -> str:
        current = self._current_runtime()
        return str(current["runtime_id"]) if current else "runtime-local-current"

    def list_events(self, *, limit: int = 100) -> list[dict]:
        rows = self._db().execute(
            "SELECT * FROM runtime_events ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_decode_row(row) for row in rows]

    def status(self) -> dict:
        self._release_expired_locks()
        db = self._db()
        candidate = self._candidate_runtime()
        current = self._current_runtime()
        draining = self._draining_runtime()
        shadow = None
        if candidate and candidate.get("state") in {
            RuntimeState.SHADOWING.value,
            RuntimeState.READY_TO_PROMOTE.value,
        }:
            shadow = candidate
        locks = [{**lock, "token": "redacted"} for lock in self._active_locks()]
        checkpoint = self._latest_checkpoint("pre_promote")
        can_promote = bool(candidate and candidate.get("state") == RuntimeState.READY_TO_PROMOTE.value)
        can_rollback = bool(checkpoint and current and current.get("runtime_id") != checkpoint.get("runtime_id"))
        can_drain = draining is not None
        lock_owner = locks[0]["owner"] if locks else None
        lock_expires_at = locks[0]["expires_at"] if locks else None
        activation = None
        if current:
            activation = dict(current.get("metadata") or {}).get("activation")
        metrics = self._metrics(db, active_lock_count=len(locks))
        guidance = self._guidance(
            candidate=candidate,
            current=current,
            draining=draining,
            can_promote=can_promote,
            can_rollback=can_rollback,
            activation=activation,
            locks=locks,
        )
        return {
            "runtime_id": current.get("runtime_id") if current else None,
            "phase": candidate.get("state") if candidate else (current.get("state") if current else "not_configured"),
            "actor": "system",
            "cause": None,
            "started_at": current.get("started_at") if current else None,
            "expires_at": lock_expires_at,
            "lock_owner": lock_owner,
            "can_promote": can_promote,
            "can_rollback": can_rollback,
            "can_drain": can_drain,
            "activation": activation,
            "activation_required": bool(activation and activation.get("restart_required")),
            "enabled": self.enabled,
            "current": current,
            "candidate": candidate,
            "shadow": shadow,
            "draining": draining,
            "locks": locks,
            "events": self.list_events(limit=20),
            "ownership_summary": self._ownership_summary(db),
            "metrics": metrics,
            "guidance": guidance,
        }

    def _ownership_summary(self, db) -> dict:
        rows = db.execute(
            "SELECT runtime_id, status, COUNT(*) AS count FROM runtime_ownership "
            "GROUP BY runtime_id, status"
        ).fetchall()
        return {
            f"{row['runtime_id']}:{row['status']}": row["count"]
            for row in rows
        }

    def _metrics(self, db, *, active_lock_count: int) -> dict:
        state_rows = db.execute(
            "SELECT state, COUNT(*) AS count FROM runtime_registry GROUP BY state"
        ).fetchall()
        recent_event_count = db.execute(
            "SELECT COUNT(*) AS count FROM runtime_events WHERE created_at >= datetime('now', '-1 day')"
        ).fetchone()
        ownership_active_count = db.execute(
            "SELECT COUNT(*) AS count FROM runtime_ownership WHERE status = 'active'"
        ).fetchone()
        failed_count = db.execute(
            "SELECT COUNT(*) AS count FROM runtime_registry WHERE state = 'failed'"
        ).fetchone()
        return {
            "state_counts": {row["state"]: row["count"] for row in state_rows},
            "active_lock_count": active_lock_count,
            "recent_event_count": int(recent_event_count["count"] if recent_event_count else 0),
            "ownership_active_count": int(ownership_active_count["count"] if ownership_active_count else 0),
            "failed_runtime_count": int(failed_count["count"] if failed_count else 0),
        }

    def _guidance(
        self,
        *,
        candidate: dict | None,
        current: dict | None,
        draining: dict | None,
        can_promote: bool,
        can_rollback: bool,
        activation: dict | None,
        locks: list[dict],
    ) -> dict:
        if locks:
            return {
                "action": "observe",
                "reason": "runtime_lock_active",
                "runtime_id": locks[0].get("runtime_id"),
            }
        if activation and activation.get("restart_required"):
            return {
                "action": "activate_restart",
                "reason": "current_runtime_change_requires_supervisor_respawn",
                "runtime_id": current.get("runtime_id") if current else None,
            }
        if can_promote:
            return {
                "action": "promote",
                "reason": "shadow_runtime_ready_to_promote",
                "runtime_id": candidate.get("runtime_id") if candidate else None,
            }
        if candidate and candidate.get("state") == RuntimeState.CANDIDATE.value:
            return {
                "action": "start_shadow",
                "reason": "candidate_waiting_for_shadow_validation",
                "runtime_id": candidate.get("runtime_id"),
            }
        if draining:
            return {
                "action": "drain",
                "reason": "previous_runtime_waiting_for_retirement",
                "runtime_id": draining.get("runtime_id"),
            }
        if can_rollback:
            return {
                "action": "rollback",
                "reason": "pre_promote_checkpoint_available",
                "runtime_id": current.get("runtime_id") if current else None,
            }
        return {
            "action": "prepare_candidate",
            "reason": "no_candidate_runtime_available",
            "runtime_id": current.get("runtime_id") if current else None,
        }

    def acquire_lock(
        self,
        lock_name: str,
        *,
        runtime_id: str | None,
        owner: str,
        force: bool = False,
    ) -> str:
        # #1197: single unified key regardless of caller-supplied lock_name so
        # promote and rollback share one atomic CAS row — no split-lock race.
        self._require_enabled("acquire_lock")
        db = self._db()
        now = _utc_now()
        expires = (datetime.now(timezone.utc) + timedelta(seconds=self.lock_ttl_sec)).isoformat().replace("+00:00", "Z")
        self._release_expired_locks(actor=owner)
        token = uuid4().hex
        # BEGIN IMMEDIATE: acquire the SQLite writer lock before the read so the
        # check-and-set is truly atomic — no window between SELECT and INSERT.
        db.execute("BEGIN IMMEDIATE")
        try:
            existing = db.execute(
                "SELECT * FROM runtime_exclusive_lock WHERE lock_name = 'runtime-transition' AND released_at IS NULL",
            ).fetchone()
            if existing and _parse_ts(existing["expires_at"]) and _parse_ts(existing["expires_at"]) > datetime.now(timezone.utc):
                if not force:
                    db.execute("ROLLBACK")
                    raise RuntimeTransitionError(
                        f"runtime_lock_conflict:runtime-transition:owner={existing['owner']}"
                    )
            db.execute(
                """
                INSERT INTO runtime_exclusive_lock (
                    lock_name, runtime_id, owner, token, acquired_at, expires_at, released_at, metadata
                ) VALUES ('runtime-transition', ?, ?, ?, ?, ?, NULL, '{}')
                ON CONFLICT(lock_name) DO UPDATE SET
                    runtime_id = excluded.runtime_id,
                    owner = excluded.owner,
                    token = excluded.token,
                    acquired_at = excluded.acquired_at,
                    expires_at = excluded.expires_at,
                    released_at = NULL,
                    metadata = '{}'
                """,
                (runtime_id, owner, token, now, expires),
            )
            self._record_event(
                runtime_id=runtime_id,
                event_type="runtime_lock_acquired",
                phase="lock",
                actor=owner,
                cause="runtime-transition",
                payload={"lock_name": "runtime-transition", "caller_key": lock_name, "expires_at": expires, "force": force},
            )
            db.execute("COMMIT")
        except RuntimeTransitionError:
            raise
        except BaseException:
            try:
                db.execute("ROLLBACK")
            except Exception:
                pass
            raise
        return token

    def release_lock(self, lock_name: str, token: str | None = None, *, actor: str = "system") -> None:
        # #1198: release_lock runs in its OWN short transaction and never commits
        # the caller's pending work — this was the bug where an exception mid-
        # transition would cause release_lock (called from the except handler) to
        # commit the partial transition state alongside the lock release.
        self._require_enabled("release_lock")
        db = self._db()
        # If caller left an open transaction (e.g. BEGIN IMMEDIATE in acquire_lock
        # was never closed due to exception path), ensure we start clean.
        if db.in_transaction:
            db.execute("ROLLBACK")
        row = db.execute(
            "SELECT * FROM runtime_exclusive_lock WHERE lock_name = 'runtime-transition' AND released_at IS NULL",
        ).fetchone()
        if row is None:
            return
        if token is not None and row["token"] != token:
            raise RuntimeTransitionError(f"runtime_lock_token_mismatch:runtime-transition")
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute(
                "UPDATE runtime_exclusive_lock SET released_at = ? WHERE lock_name = 'runtime-transition'",
                (_utc_now(),),
            )
            self._record_event(
                runtime_id=row["runtime_id"],
                event_type="runtime_lock_released",
                phase="lock",
                actor=actor,
                cause="runtime-transition",
                payload={"lock_name": "runtime-transition", "caller_key": lock_name},
            )
            db.execute("COMMIT")
        except BaseException:
            try:
                db.execute("ROLLBACK")
            except Exception:
                pass
            raise

    def _clear_locks(self, *, actor: str, cause: str) -> None:
        rows = self._active_locks()
        now = _utc_now()
        for row in rows:
            self._db().execute(
                "UPDATE runtime_exclusive_lock SET released_at = ? WHERE lock_name = ?",
                (now, row["lock_name"]),
            )
            self._record_event(
                runtime_id=row["runtime_id"],
                event_type="runtime_lock_force_cleared",
                phase="rollback",
                severity="warning",
                actor=actor,
                cause=cause,
                payload={"lock_name": row["lock_name"], "owner": row["owner"]},
            )

    def prepare_candidate(
        self,
        *,
        actor: str,
        cause: str,
        code_version: str | None = None,
        config_version: str | None = None,
        schema_version: str | None = None,
        build_git_sha: str | None = None,
        release_bundle_hash: str | None = None,
        artifact_path: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        self._require_enabled("prepare_candidate")
        bundle_hash = (release_bundle_hash or build_git_sha or uuid4().hex[:12]).strip()
        runtime_id = f"runtime-{bundle_hash[:12].replace('/', '-')}"
        db = self._db()
        existing = db.execute(
            "SELECT * FROM runtime_registry WHERE runtime_id = ?",
            (runtime_id,),
        ).fetchone()
        if existing:
            return _decode_row(existing)

        now = _utc_now()
        db.execute(
            """
            INSERT INTO runtime_registry (
                runtime_id, state, code_version, config_version, schema_version,
                build_git_sha, release_bundle_hash, artifact_path, mode, metadata,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'candidate', ?, ?, ?)
            """,
            (
                runtime_id,
                RuntimeState.CANDIDATE.value,
                code_version,
                config_version,
                schema_version,
                build_git_sha,
                bundle_hash,
                artifact_path,
                _json_dump(metadata or {}),
                now,
                now,
            ),
        )
        self._record_event(
            runtime_id=runtime_id,
            event_type="candidate_prepared",
            phase=RuntimeState.CANDIDATE.value,
            actor=actor,
            cause=cause,
            payload={
                "code_version": code_version,
                "config_version": config_version,
                "schema_version": schema_version,
                "build_git_sha": build_git_sha,
                "release_bundle_hash": bundle_hash,
                "artifact_path": artifact_path,
            },
        )
        db.commit()
        record = self._runtime(runtime_id)
        if record is None:
            raise RuntimeTransitionError("candidate_write_failed")
        return record

    def start_shadow(self, runtime_id: str, *, actor: str, cause: str) -> dict:
        self._require_enabled("start_shadow")
        db = self._db()
        runtime = self._runtime(runtime_id)
        if runtime is None:
            raise RuntimeTransitionError(f"runtime_not_found:{runtime_id}")
        if runtime["state"] == RuntimeState.READY_TO_PROMOTE.value:
            return runtime
        if runtime["state"] != RuntimeState.CANDIDATE.value:
            raise RuntimeTransitionError(
                f"invalid_transition:{runtime['state']}->{RuntimeState.SHADOWING.value}"
            )
        now = _utc_now()
        db.execute(
            "UPDATE runtime_registry SET state = ?, mode = 'shadow', started_at = ?, updated_at = ? "
            "WHERE runtime_id = ?",
            (RuntimeState.SHADOWING.value, now, now, runtime_id),
        )
        self._record_event(
            runtime_id=runtime_id,
            event_type="shadow_started",
            phase=RuntimeState.SHADOWING.value,
            actor=actor,
            cause=cause,
            payload={"runtime_mode": "shadow", "readonly": True},
        )
        db.commit()
        record = self._runtime(runtime_id)
        if record is None:
            raise RuntimeTransitionError("shadow_write_failed")
        if self.shadow_gateway_launcher is not None:
            try:
                process_report = self.shadow_gateway_launcher(record)
            except Exception as exc:
                process_report = {
                    "attempted": True,
                    "started": False,
                    "error": str(exc),
                }
            metadata = dict(record.get("metadata") or {})
            metadata["gateway_process"] = process_report
            db.execute(
                "UPDATE runtime_registry SET metadata = ?, updated_at = ? WHERE runtime_id = ?",
                (_json_dump(metadata), _utc_now(), runtime_id),
            )
            failed = process_report.get("started") is False or process_report.get("ready") is False
            self._record_event(
                runtime_id=runtime_id,
                event_type="shadow_gateway_failed" if failed else "shadow_gateway_started",
                phase=RuntimeState.SHADOWING.value,
                severity="error" if failed else "info",
                actor=actor,
                cause=cause,
                payload=process_report,
            )
            if failed:
                _kill_shadow_process({"metadata": metadata, "runtime_id": runtime_id})
                db.execute(
                    "UPDATE runtime_registry SET state = ?, failed_at = ?, last_error = ?, updated_at = ? "
                    "WHERE runtime_id = ?",
                    (
                        RuntimeState.FAILED.value,
                        _utc_now(),
                        "shadow_gateway_start_failed",
                        _utc_now(),
                        runtime_id,
                    ),
                )
                db.commit()
                raise RuntimeTransitionError("shadow_gateway_start_failed")
            db.commit()
            record = self._runtime(runtime_id)
            if record is None:
                raise RuntimeTransitionError("shadow_gateway_metadata_write_failed")
        return record

    def validate_shadow(self, runtime_id: str, *, actor: str, cause: str) -> dict:
        self._require_enabled("validate_shadow")
        db = self._db()
        runtime = self._runtime(runtime_id)
        if runtime is None:
            raise RuntimeTransitionError(f"runtime_not_found:{runtime_id}")
        if runtime["state"] == RuntimeState.READY_TO_PROMOTE.value:
            return {**runtime, "can_promote": True, "validation": self._validation_report(runtime)}
        if runtime["state"] != RuntimeState.SHADOWING.value:
            raise RuntimeTransitionError(
                f"invalid_transition:{runtime['state']}->{RuntimeState.READY_TO_PROMOTE.value}"
            )
        report = self._validation_report(runtime)
        if not report["passed"]:
            _kill_shadow_process(runtime)
            now = _utc_now()
            db.execute(
                "UPDATE runtime_registry SET state = ?, failed_at = ?, last_error = ?, updated_at = ? "
                "WHERE runtime_id = ?",
                (RuntimeState.FAILED.value, now, "shadow_validation_failed", now, runtime_id),
            )
            self._record_event(
                runtime_id=runtime_id,
                event_type="shadow_validation_failed",
                phase=RuntimeState.FAILED.value,
                severity="error",
                actor=actor,
                cause=cause,
                payload=report,
            )
            db.commit()
            raise RuntimeTransitionError("shadow_validation_failed")
        now = _utc_now()
        db.execute(
            "UPDATE runtime_registry SET state = ?, updated_at = ? WHERE runtime_id = ?",
            (RuntimeState.READY_TO_PROMOTE.value, now, runtime_id),
        )
        self._record_event(
            runtime_id=runtime_id,
            event_type="shadow_validated",
            phase=RuntimeState.READY_TO_PROMOTE.value,
            actor=actor,
            cause=cause,
            payload=report,
        )
        db.commit()
        record = self._runtime(runtime_id)
        if record is None:
            raise RuntimeTransitionError("validation_write_failed")
        return {**record, "can_promote": True, "validation": report}

    def _validation_report(self, runtime: dict) -> dict:
        current = self._current_runtime()
        metadata = dict(runtime.get("metadata") or {})
        gateway_process = metadata.get("gateway_process")
        gateway_process_attempted = isinstance(gateway_process, dict) and bool(gateway_process.get("attempted"))
        gateway_process_ready = not gateway_process_attempted or (
            isinstance(gateway_process, dict)
            and bool(gateway_process.get("started"))
            and gateway_process.get("ready", True) is not False
            and bool(gateway_process.get("base_url") or gateway_process.get("pid"))
        )
        checks = [
            {"name": "candidate_record", "passed": bool(runtime.get("runtime_id"))},
            {"name": "current_runtime_present", "passed": current is not None},
            {
                "name": "schema_version_present",
                "passed": bool(runtime.get("schema_version")),
            },
            {
                "name": "release_identity_present",
                "passed": bool(runtime.get("release_bundle_hash") or runtime.get("build_git_sha")),
            },
            {"name": "side_effect_mode", "passed": runtime.get("mode") in {"shadow", None}},
            {
                "name": "shadow_gateway_process_ready",
                "passed": gateway_process_ready,
                "detail": gateway_process if gateway_process_attempted else "not_attempted",
            },
        ]
        return {
            "passed": all(item["passed"] for item in checks),
            "checks": checks,
            "validated_at": _utc_now(),
        }

    def promote(self, runtime_id: str, *, actor: str, cause: str) -> dict:
        self._require_enabled("promote")
        runtime = self._runtime(runtime_id)
        if runtime is None:
            raise RuntimeTransitionError(f"runtime_not_found:{runtime_id}")
        if runtime["state"] == RuntimeState.CURRENT.value:
            return {**self.status(), "current": runtime, "previous_current": None}
        if runtime["state"] != RuntimeState.READY_TO_PROMOTE.value:
            raise RuntimeTransitionError(f"runtime_not_promotable:{runtime['state']}")

        token = self.acquire_lock("promote", runtime_id=runtime_id, owner=actor)
        db = self._db()
        try:
            # Compute activation plan first to determine if shadow process needs liveness check
            current = self._current_runtime()
            if current is None:
                raise RuntimeTransitionError("current_runtime_missing")
            activation = _activation_plan(runtime_id=runtime_id, action="promote", runtime=runtime)

            # Fresh liveness probe: verify shadow PID and HTTP readiness at promotion boundary
            # only for frontdoor_route_switch (when a live shadow process exists).
            # supervisor_respawn has no shadow process to check.
            # Persisted readiness is audit-only; promotion must not trust stale evidence.
            if activation.get("strategy") == "frontdoor_route_switch":
                prober = self.shadow_live_prober
                if prober is None:
                    from aletheia.runtime_continuity.processes import live_probe_shadow
                    prober = live_probe_shadow
                probe = prober(runtime)
                if not (probe.get("alive") and probe.get("ready") and probe.get("pid_match")):
                    reason = probe.get("reason", "liveness_probe_failed")
                    logger.warning(
                        "Promote rejected: shadow process not alive/ready at promotion boundary. "
                        "runtime_id=%s, probe=%s",
                        runtime_id, probe,
                    )
                    raise RuntimeTransitionError(f"shadow_process_not_ready:{reason}")
            # _apply_activation manages its own transactions (intent row + route file).
            # Run it BEFORE the registry BEGIN IMMEDIATE to avoid nested transactions.
            self._apply_activation(activation, actor=actor, cause=cause, fencing_token=token)

            # #1198: wrap registry state writes in explicit BEGIN/COMMIT so an exception
            # does ROLLBACK (discarding partial state) before release_lock.
            db.execute("BEGIN IMMEDIATE")
            try:
                now = _utc_now()
                checkpoint_id = _new_id("runtime-checkpoint")
                promoted_metadata = dict(runtime.get("metadata") or {})
                promoted_metadata["activation"] = activation

                db.execute(
                    """
                    INSERT INTO runtime_checkpoints (
                        id, runtime_id, checkpoint_type, status, payload, created_at
                    ) VALUES (?, ?, 'pre_promote', 'available', ?, ?)
                    """,
                    (
                        checkpoint_id,
                        current["runtime_id"],
                        _json_dump({"previous_current": current, "promoting_runtime_id": runtime_id}),
                        now,
                    ),
                )
                db.execute(
                    "UPDATE runtime_registry SET state = ?, mode = 'current', drained_at = ?, updated_at = ? "
                    "WHERE runtime_id = ?",
                    (RuntimeState.DRAINING.value, now, now, current["runtime_id"]),
                )
                # #1194: for supervisor_respawn, record restart_required intent — don't flip to CURRENT.
                if activation.get("strategy") == "supervisor_respawn":
                    promoted_metadata["transition_result"] = "restart_required"
                    db.execute(
                        "UPDATE runtime_registry SET state = ?, mode = 'shadow', metadata = ?, updated_at = ? "
                        "WHERE runtime_id = ?",
                        (RuntimeState.READY_TO_PROMOTE.value, _json_dump(promoted_metadata), now, runtime_id),
                    )
                else:
                    db.execute(
                        "UPDATE runtime_registry SET state = ?, mode = 'current', promoted_at = ?, "
                        "metadata = ?, updated_at = ? "
                        "WHERE runtime_id = ?",
                        (RuntimeState.CURRENT.value, now, _json_dump(promoted_metadata), now, runtime_id),
                    )
                self._record_event(
                    runtime_id=runtime_id,
                    event_type="runtime_promoted",
                    phase=RuntimeState.CURRENT.value if activation.get("strategy") == "frontdoor_route_switch" else "pending_restart",
                    actor=actor,
                    cause=cause,
                    payload={
                        "previous_runtime_id": current["runtime_id"],
                        "checkpoint_id": checkpoint_id,
                        "activation": activation,
                    },
                )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

            # Lock release happens AFTER the transition commits or rolls back.
            self.release_lock("promote", token, actor=actor)
            return {
                **self.status(),
                "current": self._runtime(runtime_id),
                "previous_current": self._runtime(current["runtime_id"]),
                "draining": self._runtime(current["runtime_id"]),
                "activation": activation,
            }
        except Exception:
            # Best-effort lock release on any exception. release_lock now does its own
            # clean transaction so it won't commit the caller's rolled-back work.
            try:
                self.release_lock("promote", token, actor=actor)
            except Exception:
                pass
            raise

    def drain(self, runtime_id: str, *, actor: str, cause: str, force: bool = False) -> dict:
        self._require_enabled("drain")
        runtime = self._runtime(runtime_id)
        if runtime is None:
            raise RuntimeTransitionError(f"runtime_not_found:{runtime_id}")
        if runtime["state"] not in {RuntimeState.DRAINING.value, RuntimeState.CURRENT.value}:
            raise RuntimeTransitionError(f"runtime_not_draining:{runtime['state']}")
        _kill_shadow_process(runtime)
        state = RuntimeState.LEGACY_POSITION_MANAGER.value if force else RuntimeState.DRAINING.value
        now = _utc_now()
        self._db().execute(
            "UPDATE runtime_registry SET state = ?, drained_at = ?, updated_at = ? WHERE runtime_id = ?",
            (state, now, now, runtime_id),
        )
        self._record_event(
            runtime_id=runtime_id,
            event_type="runtime_drained" if force else "runtime_drain_observed",
            phase=state,
            actor=actor,
            cause=cause,
            payload={"force": force},
        )
        self._db().commit()
        record = self._runtime(runtime_id)
        if record is None:
            raise RuntimeTransitionError("drain_write_failed")
        return record

    def rollback(self, *, actor: str, cause: str) -> dict:
        self._require_enabled("rollback")
        checkpoint = self._latest_checkpoint("pre_promote")
        if checkpoint is None:
            raise RuntimeTransitionError("rollback_checkpoint_missing")
        target_runtime_id = str(checkpoint["runtime_id"])
        current = self._current_runtime()
        if current and current["runtime_id"] == target_runtime_id:
            return {**self.status(), "current": current, "rolled_back": None}

        token = self.acquire_lock("rollback", runtime_id=target_runtime_id, owner=actor, force=True)
        db = self._db()
        try:
            now = _utc_now()
            target = self._runtime(target_runtime_id)
            target_metadata = dict((target or {}).get("metadata") or {})
            activation = _activation_plan(runtime_id=target_runtime_id, action="rollback", runtime=target)
            target_metadata["activation"] = activation

            # #1198: explicit transaction — ROLLBACK discards partial state before lock release
            db.execute("BEGIN IMMEDIATE")
            try:
                self._apply_activation(activation, actor=actor, cause=cause, fencing_token=token)
                if current is not None:
                    db.execute(
                        "UPDATE runtime_registry SET state = ?, rolled_back_at = ?, updated_at = ? "
                        "WHERE runtime_id = ?",
                        (RuntimeState.ROLLED_BACK.value, now, now, current["runtime_id"]),
                    )
                db.execute(
                    "UPDATE runtime_registry SET state = ?, mode = 'current', promoted_at = ?, "
                    "metadata = ?, updated_at = ? "
                    "WHERE runtime_id = ?",
                    (RuntimeState.CURRENT.value, now, _json_dump(target_metadata), now, target_runtime_id),
                )
                db.execute(
                    "UPDATE runtime_checkpoints SET restored_at = ?, status = 'restored' WHERE id = ?",
                    (now, checkpoint["id"]),
                )
                self._clear_locks(actor=actor, cause=cause)
                self._record_event(
                    runtime_id=target_runtime_id,
                    event_type="runtime_rolled_back",
                    phase=RuntimeState.CURRENT.value,
                    actor=actor,
                    cause=cause,
                    severity="warning",
                    payload={
                        "checkpoint_id": checkpoint["id"],
                        "rolled_back_runtime_id": current["runtime_id"] if current else None,
                        "activation": activation,
                    },
                )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

            self.release_lock("rollback", token, actor=actor)
            return {
                **self.status(),
                "current": self._runtime(target_runtime_id),
                "rolled_back": self._runtime(current["runtime_id"]) if current else None,
                "activation": activation,
            }
        except Exception:
            try:
                self.release_lock("rollback", token, actor=actor)
            except Exception:
                pass
            raise

    def attach_ownership(
        self,
        *,
        entity_type: str,
        entity_id: str,
        runtime_id: str | None = None,
        ownership_kind: str = "operator_task",
        metadata: dict | None = None,
    ) -> dict:
        effective_runtime_id = runtime_id or self.current_runtime_id()
        ownership_id = _new_id("runtime-own")
        self._db().execute(
            """
            INSERT INTO runtime_ownership (
                id, entity_type, entity_id, runtime_id, ownership_kind, status, metadata
            ) VALUES (?, ?, ?, ?, ?, 'active', ?)
            ON CONFLICT(entity_type, entity_id, status) WHERE status = 'active'
            DO UPDATE SET runtime_id = excluded.runtime_id, ownership_kind = excluded.ownership_kind,
                metadata = excluded.metadata
            """,
            (
                ownership_id,
                entity_type,
                entity_id,
                effective_runtime_id,
                ownership_kind,
                _json_dump(metadata or {}),
            ),
        )
        self._db().commit()
        row = self._db().execute(
            "SELECT * FROM runtime_ownership WHERE entity_type = ? AND entity_id = ? AND status = 'active'",
            (entity_type, entity_id),
        ).fetchone()
        decoded = _decode_row(row)
        if decoded is None:
            raise RuntimeTransitionError("runtime_ownership_write_failed")
        return decoded
