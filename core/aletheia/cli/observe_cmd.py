"""Observation Lab CLI (Issue #1072).

Observe-only surface. No command in this module mutates production behavior,
holds Risk / Execution authority, or reaches for exchange credentials. Positive
verdicts are surfaced through ``observe status``; promotion decisions remain
manual PR + owner admission work outside the CLI.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import typer

observe_app = typer.Typer(
    name="observe",
    no_args_is_help=True,
    help="Observation Lab: register, sync, and inspect prospective experiments.",
)


def _experiment_authority_source() -> str:
    """Read the durable cutover row without bootstrapping a missing app DB."""
    import sqlite3

    try:
        from aletheia.storage.sqlite import readonly_db

        with readonly_db(allow_missing=True) as db:
            if db is None:
                return "unavailable"
            try:
                row = db.execute(
                    "SELECT authority_source FROM observation_authority_state "
                    "WHERE state_key='experiment_authority'"
                ).fetchone()
            except sqlite3.OperationalError:
                return "unavailable"
            if row is None:
                return "unavailable"
            source = str(row[0])
            return source if source in {"legacy", "lab"} else "unavailable"
    except FileNotFoundError:
        return "unavailable"
    except Exception:
        return "unavailable"


def _require_experiment_authority() -> str:
    source = _experiment_authority_source()
    if source != "lab":
        typer.echo(
            "experiment authority unavailable; refusing legacy fallback",
            err=True,
        )
        raise typer.Exit(code=1)
    return source


@observe_app.command("list")
def list_observations(
    status: str | None = typer.Option(
        None, "--status", help="Filter by status (collecting / ready_for_review / …)."
    ),
    limit: int = typer.Option(50, "--limit"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """List registered Observations with their frozen-unit counts."""
    _require_experiment_authority()
    payload = _list_lab_observations(status=status, limit=limit)
    if payload is None:
        typer.echo("observation lab unavailable", err=True)
        raise typer.Exit(code=1)
    _render_observation_list(payload, json_output=json_output)
    return


def _render_observation_list(payload: list[dict], *, json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(payload, indent=2, default=str))
        return
    if not payload:
        typer.echo("(no observations)")
        return
    for entry in payload:
        typer.echo(
            f"{entry['id'][:12]}… {entry['filter_name']}/{entry['scanner_family']}/"
            f"{entry['horizon']} status={entry['status']} "
            f"frozen_units={entry['frozen_units']} started={entry['started_at'][:19]}"
        )


def _list_lab_observations(*, status: str | None, limit: int) -> list[dict] | None:
    """Project Lab roots into the historical CLI list shape."""
    from extensions.observation_lab.filter_shadow import list_filter_shadow_observations
    from extensions.observation_lab.db import readonly_lab_db

    try:
        filter_rows = list_filter_shadow_observations(limit=1000)
        if filter_rows is None:
            return None
        filter_by_key = {
            (str(row["observation_id"]), int(row["generation"])): row
            for row in filter_rows
        }
        with readonly_lab_db(allow_missing=True) as db:
            if db is None:
                return None
            rows = db.execute(
                "SELECT observation_id, kind, generation, status, started_at, spec_json "
                "FROM observation_windows ORDER BY started_at DESC, observation_id, generation "
                "LIMIT ?",
                (1000,),
            ).fetchall()
            result: list[dict] = []
            for row in rows:
                key = (str(row["observation_id"]), int(row["generation"]))
                if row["kind"] == "filter_shadow":
                    projected = filter_by_key.get(key)
                    if projected is None or (status and projected["status"] != status):
                        continue
                    result.append(projected)
                    continue
                if status and str(row["status"]) != status:
                    continue
                try:
                    spec = json.loads(row["spec_json"] or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    spec = {}
                result.append({
                    "id": f"{row['observation_id']}:g{row['generation']}",
                    "filter_name": str(spec.get("filter_name") or spec.get("path_id") or row["kind"]),
                    "scanner_family": str(spec.get("scanner_family") or spec.get("overlap_group") or "unknown"),
                    "horizon": str(spec.get("horizon") or spec.get("target_horizon") or "unknown"),
                    "status": row["status"],
                    "started_at": row["started_at"],
                    "frozen_units": int(db.execute(
                        "SELECT COUNT(*) FROM observation_units WHERE observation_id=? AND generation=?",
                        (row["observation_id"], row["generation"]),
                    ).fetchone()[0] or 0),
                })
            result.sort(key=lambda item: (
                str(item.get("started_at") or ""),
                str(item.get("id") or ""),
            ), reverse=True)
            return result[: max(1, min(int(limit), 1000))]
    except Exception:
        return None


@observe_app.command("status")
def observation_status(
    observation_id: str = typer.Argument(..., help="Observation id (or unique prefix)."),
    generation: int | None = typer.Option(None, "--generation", min=1),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Show a durable canonical Observation Window."""
    from aletheia.config.loader import get_data_dir
    from extensions.observation_lab.db import get_lab_db
    from extensions.observation_lab.kernel import status_window
    from extensions.observation_lab.runner import register_builtin_handlers

    _require_experiment_authority()
    if not (get_data_dir() / "observation-lab" / "lab.db").is_file():
        typer.echo("observation lab unavailable", err=True)
        raise typer.Exit(code=1)

    register_builtin_handlers()

    embedded_observation_id, embedded_generation = _split_lab_observation_ref(
        observation_id
    )
    resolved_generation = generation or embedded_generation
    durable = None
    try:
        durable = (
            status_window(embedded_observation_id, resolved_generation)
            if resolved_generation
            else status_window(embedded_observation_id)
        )
    except KeyError:
        rows = get_lab_db().execute(
            "SELECT DISTINCT observation_id FROM observation_windows "
            "WHERE substr(observation_id, 1, ?) = ? ORDER BY observation_id",
            (len(embedded_observation_id), embedded_observation_id),
        ).fetchall()
        if len(rows) > 1:
            typer.echo(
                f"prefix {embedded_observation_id!r} is ambiguous - "
                f"matches {[str(row[0]) for row in rows]}", err=True,
            )
            raise typer.Exit(code=2)
        if rows:
            durable = (
                status_window(str(rows[0][0]), resolved_generation)
                if resolved_generation else status_window(str(rows[0][0]))
            )
    if durable is not None:
        if json_output:
            typer.echo(json.dumps(durable, indent=2, default=str))
        else:
            for key in (
                "observation_id", "generation", "kind", "handler_version", "handler_available",
                "slot_key", "authority_mode", "status", "phase", "spec_hash", "started_at",
                "collect_until", "finalize_not_before", "unit_counts", "latest_summary",
                "last_advanced_at", "last_error", "terminal_reason", "artifact_path",
                "artifact_sha256",
            ):
                typer.echo(f"{key}={durable.get(key)}")
        return

    typer.echo("observation is not available in the canonical Lab ledger", err=True)
    raise typer.Exit(code=1)


@observe_app.command("overview")
def observation_overview(
    equivalence_limit: int = typer.Option(
        5, "--equivalence-limit",
        help="How many recent UFC equivalence records to include.",
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Whole-Lab health snapshot: experiments, UFC equivalence, manifests, checkpoint.

    This is the "is Observation Lab alive and healthy?" single command
    Fedora operators run to answer the four questions ``observation_lab_v1_design.md``
    §9 asks: is it running, is UFC still matching, did anything drift, and
    what manifests are live.
    """
    payload = _collect_overview(equivalence_limit=equivalence_limit)
    if json_output:
        typer.echo(json.dumps(payload, indent=2, default=str))
        return

    typer.echo(f"overall_status: {payload['overall_status']}")
    typer.echo(
        "reason_codes: "
        + (", ".join(payload["reason_codes"]) if payload["reason_codes"] else "(none)")
    )
    typer.echo(f"execution_owner: {payload['execution_owner']}")
    worker = payload["worker"]
    typer.echo(
        f"worker: systemd_available={worker['systemd_available']} "
        f"checkout={worker['checkout']} head_sha={worker['head_sha']}"
    )
    typer.echo(
        f"  timer: load={worker['timer']['load_state']} "
        f"active={worker['timer']['active_state']}"
    )
    service_pass = worker["last_service_pass"]
    typer.echo(
        f"  last_service_pass: load={service_pass['load_state']} "
        f"status={service_pass['status']} at={service_pass['at']} "
        f"exit_code={service_pass['exit_code']}"
    )
    typer.echo("storage:")
    for name in ("app_db", "lab_db"):
        item = payload["storage"][name]
        typer.echo(f"  {name}: status={item['status']} path={item['path']}")
    typer.echo(
        f"active_windows: available={payload['windows']['available']} "
        f"count={payload['windows']['active_count']}"
    )
    for item in payload["windows"]["items"]:
        typer.echo(
            f"  {item['observation_id']} gen={item['generation']} "
            f"kind={item['kind']} phase={item['phase']} "
            f"handler_available={item['handler_available']} "
            f"last_advanced_at={item['last_advanced_at']} "
            f"last_error={item['last_error']}"
        )
    typer.echo(
        f"experiments: {payload['experiments']['total']} total, "
        f"{payload['experiments']['collecting']} collecting, "
        f"{payload['experiments']['ready_for_review']} ready_for_review, "
        f"{payload['experiments']['invalidated']} invalidated"
    )
    typer.echo(
        f"ufc_shadow_equivalence: last {equivalence_limit} — "
        f"matched={payload['ufc_equivalence']['recent_matched']}/"
        f"{payload['ufc_equivalence']['recent_checked']} "
        f"skipped={payload['ufc_equivalence']['recent_skipped']}"
    )
    for record in payload["ufc_equivalence"]["recent_records"]:
        marker = {"matched": "✓", "mismatched": "✗", "skipped": "○"}[
            record["result"]
        ]
        detail = (
            f" mismatch={record['mismatch_field']}"
            if record["result"] == "mismatched" and record.get("mismatch_field")
            else f" skipped={record['notes']}"
            if record["result"] == "skipped"
            else ""
        )
        typer.echo(
            f"  {marker} {record['checked_at']} {record['observation_id']}{detail}"
        )
    typer.echo(f"active_manifests: {payload['manifests']['active_count']}")
    for m in payload["manifests"]["active"]:
        typer.echo(
            f"  {m['observation_id']} gen={m['generation']} "
            f"hash={m['manifest_hash'][:12]}… composability={m['composability_contract']}"
        )
    bridge = payload.get("bridge") or {}
    typer.echo(
        f"delivery_bridge: available={bridge.get('available')} "
        f"authority={bridge.get('authority_source')} "
        f"pending={bridge.get('outbox_pending_count')} "
        f"watermark={bridge.get('lab_imported_watermark')} "
        f"archive_backlog={bridge.get('archive_backlog_count')}"
    )
    cp = payload["checkpoint"]
    if cp["latest"] is None:
        typer.echo("checkpoint: (no fingerprint recorded yet)")
    else:
        marker = "✓" if cp["verified"]["ok"] else "✗"
        typer.echo(
            f"checkpoint: {marker} recorded_at={cp['latest']['recorded_at']} "
            f"app_migrations={cp['latest']['app_migration_count']} "
            f"active_manifests={cp['latest']['active_manifest_count']} "
            f"reason={cp['verified']['reason']}"
        )


@observe_app.command("checkpoint")
def observation_checkpoint(
    verify_only: bool = typer.Option(
        False, "--verify-only",
        help="Only verify without appending; useful when diagnosing drift.",
    ),
    acknowledge: str | None = typer.Option(
        None, "--acknowledge",
        help=(
            "Comma-separated drift components the operator has explained "
            "(e.g. app_migration,main_outbox). Saves a new checkpoint only if "
            "every mismatched component is named; the acknowledgement is audited."
        ),
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Run one joint app.db + lab.db + manifest checkpoint pass."""
    from extensions.observation_lab.checkpoint import (
        compute_joint_checkpoint,
        save_joint_checkpoint,
        verify_joint_checkpoint,
    )
    verdict = verify_joint_checkpoint()
    saved = False
    acknowledged: list[str] = []
    if not verify_only and verdict.ok:
        save_joint_checkpoint(compute_joint_checkpoint())
        saved = True
    elif not verify_only and acknowledge is not None:
        named = {part.strip() for part in acknowledge.split(",") if part.strip()}
        actual = set(verdict.mismatch_components)
        # A verdict without components (lab/outbox unavailable) has nothing an
        # operator can acknowledge; it stays fail-closed.
        if not actual or not actual <= named:
            typer.echo(
                f"refusing to acknowledge: actual mismatch components="
                f"{sorted(actual) or '(none)'} reason={verdict.reason}",
                err=True,
            )
            raise typer.Exit(code=1)
        acknowledged = sorted(actual)
        from aletheia.files.jsonl import write_audit

        # Audit before saving: an acknowledgement that cannot be recorded must
        # not create a new baseline.
        if not write_audit({
            "actor": "human",
            "source": "cli",
            "event_type": "observation_checkpoint_drift_acknowledged",
            "entity_type": "observation_joint_checkpoint",
            "entity_id": verdict.last.checkpoint_id if verdict.last else "none",
            "before": {
                "joint_fingerprint": verdict.last.joint_fingerprint if verdict.last else None,
            },
            "after": {
                "acknowledged_components": acknowledged,
                "named_components": sorted(named),
                "current_fingerprint": verdict.current_fingerprint,
                "reason": verdict.reason,
            },
            "message": f"observation checkpoint drift acknowledged: {','.join(acknowledged)}",
        }):
            typer.echo("refusing to acknowledge: audit write unavailable", err=True)
            raise typer.Exit(code=1)
        save_joint_checkpoint(
            compute_joint_checkpoint(notes=f"acknowledged:{','.join(acknowledged)}")
        )
        saved = True
    payload = {
        "ok": verdict.ok,
        "reason": verdict.reason,
        "current_fingerprint": verdict.current_fingerprint,
        "mismatch_components": list(getattr(verdict, "mismatch_components", ())),
        "last_fingerprint": (
            verdict.last.joint_fingerprint if verdict.last is not None else None
        ),
        "saved": saved,
        "acknowledged_components": acknowledged,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return
    marker = "✓" if verdict.ok else "✗"
    typer.echo(f"{marker} {verdict.reason}")
    typer.echo(f"current_fingerprint={verdict.current_fingerprint[:16]}…")
    if verdict.last is not None:
        typer.echo(f"last_fingerprint   ={verdict.last.joint_fingerprint[:16]}…")
    if acknowledged:
        typer.echo(f"acknowledged drift: {','.join(acknowledged)}")
    if saved:
        typer.echo("appended new checkpoint row (idempotent no-op if unchanged)")


@observe_app.command("drift")
def observation_drift(
    fetch: bool = typer.Option(
        False, "--fetch",
        help=(
            "Run 'git fetch origin master --quiet' first. Off by default so "
            "the command stays offline-safe."
        ),
    ),
    warn_after: int = typer.Option(
        30, "--warn-after",
        help=(
            "Commits-behind threshold at which the exit code becomes non-zero, "
            "so operators can wire this into cron/monitoring."
        ),
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Report Worker checkout drift vs upstream master.

    The Worker checkout is deliberately pinned (see Issue #1072). This
    command lets the operator see, without leaving Fedora, how far the
    Worker has drifted from master:

    - ``HEAD`` — the currently-running Worker commit.
    - ``origin/master`` — as-known-locally (pass ``--fetch`` to refresh).
    - Commits behind + commits ahead.
    - Exit non-zero when behind exceeds ``--warn-after`` (default 30) so
      the command can be wired into a health check without extra logic.

    Not run automatically. Advisory: the Worker is meant to be pinned,
    but the operator wants to know when the pin is stale.
    """
    import subprocess
    from pathlib import Path

    cwd = Path.cwd()
    if not (cwd / ".git").exists():
        typer.echo(
            f"observe drift: {cwd} is not a git checkout — run this from the "
            "aletheia-observer directory",
            err=True,
        )
        raise typer.Exit(code=1)

    def _git(*args: str, timeout: int = 15) -> tuple[str | None, str | None]:
        try:
            result = subprocess.run(
                ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return None, "git_timeout"
        except FileNotFoundError:
            return None, "git_unavailable"
        if result.returncode != 0:
            return None, "git_command_failed"
        return (result.stdout or "").strip(), None

    fetched = False

    def _unavailable(reason: str, *, head_sha: str | None = None, master_sha: str | None = None) -> None:
        payload = {
            "available": False,
            "worker_head": head_sha,
            "origin_master": master_sha,
            "fetched": fetched,
            "behind": None,
            "ahead": None,
            "warn_after": warn_after,
            "drifted": None,
            "unavailable_reason": reason,
        }
        if json_output:
            typer.echo(json.dumps(payload, indent=2))
        else:
            typer.echo(f"✗ unavailable={reason}")
            typer.echo(f"  worker_head={(head_sha or 'unknown')[:12]}… origin/master={(master_sha or 'unknown')[:12]}…")
        raise typer.Exit(code=1)

    if fetch:
        _fetch_output, fetch_error = _git(
            "fetch", "origin", "master", "--quiet", timeout=60,
        )
        if fetch_error:
            _unavailable("git_fetch_failed" if fetch_error == "git_command_failed" else fetch_error)
        fetched = True

    head_sha, head_error = _git("rev-parse", "HEAD")
    if head_error or not head_sha:
        _unavailable(head_error or "git_head_unavailable")
    master_sha, master_error = _git("rev-parse", "origin/master")
    if master_error or not master_sha:
        _unavailable(master_error or "git_origin_master_unavailable", head_sha=head_sha)
    behind_raw, behind_error = _git("rev-list", "--count", "HEAD..origin/master")
    if behind_error or not behind_raw:
        _unavailable(behind_error or "git_behind_count_unavailable", head_sha=head_sha, master_sha=master_sha)
    ahead_raw, ahead_error = _git("rev-list", "--count", "origin/master..HEAD")
    if ahead_error or not ahead_raw:
        _unavailable(ahead_error or "git_ahead_count_unavailable", head_sha=head_sha, master_sha=master_sha)
    try:
        behind = int(behind_raw)
        ahead = int(ahead_raw)
    except (TypeError, ValueError):
        _unavailable("git_count_invalid", head_sha=head_sha, master_sha=master_sha)

    payload = {
        "available": True,
        "worker_head": head_sha,
        "origin_master": master_sha,
        "fetched": fetched,
        "behind": behind,
        "ahead": ahead,
        "warn_after": warn_after,
        "drifted": behind > warn_after,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    else:
        marker = "⚠" if payload["drifted"] else "✓"
        typer.echo(
            f"{marker} worker_head={head_sha[:12]}… "
            f"origin/master={(master_sha or 'unknown')[:12]}…"
        )
        typer.echo(f"  behind={behind}  ahead={ahead}  warn_after={warn_after}")
        if not fetch:
            typer.echo(
                "  (origin/master is local snapshot; pass --fetch for live comparison)"
            )
    if payload["drifted"]:
        raise typer.Exit(code=1)


@observe_app.command("run")
def run_worker_pass(
    min_samples: int = typer.Option(
        20, "--min-samples",
        help="Minimum settled samples per (filter, family) cell before verdicts emit.",
    ),
    json_output: bool = typer.Option(False, "--json"),
    quiet: bool = typer.Option(
        False, "--quiet",
        help=(
            "Suppress routine per-pass output. Ownership errors, lock contention, "
            "runner errors, and UFC equivalence mismatches still print. Reduces "
            "systemd journal chatter for long-running Workers."
        ),
    ),
) -> None:
    """One Worker pass of the Observation cycle (Issue #1072).

    Entry point for the sibling ``aletheia-observer`` checkout's systemd
    timer. Fails closed unless
    ``config.observation.execution_owner == "worker"`` and no other Worker
    pass currently holds the cross-process cycle lock.

    Reuses the existing one-shot path — this command does NOT reimplement
    the Observation cycle. It calls the exact same
    ``run_controller_observe`` → ``run_lab_windows`` chain the Main
    Scheduler would call in ``main`` mode; the split is about **who**
    runs it, not **what** runs.

    Non-zero exit on:
    - Ownership contract violation (``observation.execution_owner != "worker"``).
    - Another Worker pass already holds the cross-process lock.
    - Any exception the underlying runner propagates.

    Explicitly does NOT start Gateway, Frontdoor, full Main scheduler,
    Operator shell, Risk, Execution, exchange adapters, or trading
    authority.
    """
    from extensions.observation_lab.execution_owner import (
        ObservationOwnerError,
        get_execution_owner,
        require_worker_owner,
    )
    from extensions.observation_lab.worker_lock import (
        WorkerCycleLockError,
        worker_pass_lock,
    )

    try:
        require_worker_owner(action="observe run")
    except ObservationOwnerError as exc:
        typer.echo(f"observe run: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    try:
        from aletheia.agent_runtime.build_info import get_boot_commit
        from aletheia.update.git_ops import current_commit

        source_commit = get_boot_commit() or current_commit()
    except Exception:  # pragma: no cover - build info fallback
        source_commit = None

    try:
        with worker_pass_lock(source_commit=source_commit):
            from extensions.analytics.filter_controller import run_controller_observe

            summary = run_controller_observe(min_samples=int(min_samples))
    except WorkerCycleLockError as exc:
        typer.echo(f"observe run: {exc}", err=True)
        raise typer.Exit(code=3) from exc

    pass_status = summary.get("pass_status", "success")
    pass_reason = summary.get("pass_reason")

    payload = {
        "owner": get_execution_owner(),
        "source_commit": source_commit,
        "recommendations_written": summary.get("recommendations_written", 0),
        "lab_windows": summary.get("lab_windows"),
        "ufc_shadow_equivalence": summary.get("ufc_shadow_equivalence"),
        "experiment_registration": summary.get("experiment_registration"),
        "experiment_evaluation": summary.get("experiment_evaluation"),
        "pass_status": pass_status,
        "pass_reason": pass_reason,
    }
    # Issue #1240: exit non-zero for failed/partial passes to surface evidence
    # integrity issues to systemd/cron health monitoring.
    exit_code = 0 if pass_status == "success" else 4

    if json_output:
        typer.echo(json.dumps(payload, indent=2, default=str))
        raise typer.Exit(code=exit_code)

    lab_windows = payload.get("lab_windows") or {}
    ufc = payload.get("ufc_shadow_equivalence") or {}

    # ponytail: quiet suppresses clean passes, but mismatches/errors are signal
    has_signal = (
        (lab_windows.get("windows_errored") or 0) > 0
        or (ufc.get("mismatched") or 0) > 0
    )
    if quiet and pass_status == "success" and not has_signal:
        # Clean pass — stay silent so systemd journal only carries signal.
        raise typer.Exit(code=0)

    typer.echo(f"owner={payload['owner']} source_commit={payload['source_commit']}")
    typer.echo(f"recommendations_written={payload['recommendations_written']}")
    typer.echo(f"pass_status={pass_status} pass_reason={pass_reason or 'N/A'}")
    if lab_windows:
        typer.echo(
            f"lab_windows: run={lab_windows.get('windows_run')} "
            f"errored={lab_windows.get('windows_errored')}"
        )
    if ufc:
        typer.echo(
            f"ufc_shadow_equivalence: checked={ufc.get('checked')} "
            f"matched={ufc.get('matched')} mismatched={ufc.get('mismatched')} "
            f"skipped={ufc.get('skipped')}"
        )

    raise typer.Exit(code=exit_code)


@observe_app.command("sync")
def sync_all(
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Capture new first-exposure units + evaluate all collecting Observations.

    Same body as the controller's periodic cycle for the Lab; convenient when
    an operator wants an immediate refresh without waiting for the next cycle.
    """
    _require_experiment_authority()
    from extensions.observation_lab.runner import settle_observation_continuity

    settled = settle_observation_continuity(advance_windows=True)
    payload = {
        "delivery_import": settled.get("delivery_import"),
        "audit_mirror": settled.get("audit_mirror"),
        "windows": settled.get("windows"),
        "compaction": settled.get("compaction"),
        "ok": settled.get("ok"),
        "reason": settled.get("reason"),
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, default=str))
    else:
        typer.echo(f"delivery import: {payload['delivery_import']}")
        typer.echo(f"audit mirror: {payload['audit_mirror']}")
        typer.echo(f"windows: {payload['windows']}")
        typer.echo(f"compaction: {payload['compaction']}")
        if not settled.get("ok"):
            typer.echo(f"sync failed: {payload['reason']}", err=True)
    if not settled.get("ok"):
        raise typer.Exit(code=1)
    return


@observe_app.command("migrate-learning-ledger")
def migrate_learning_ledger(
    apply_migration: bool = typer.Option(
        False, "--apply", help="Write the deterministic legacy import into Lab.",
    ),
    archive_root: Path | None = typer.Option(
        None, "--archive-root", file_okay=False,
        help="Managed archive root for terminal imported generations.",
    ),
    retire_legacy_schema: bool = typer.Option(
        False,
        "--retire-legacy-schema",
        help="After a successful Lab import, physically drop retired Experiment tables.",
    ),
    runtime_commit: str | None = typer.Option(
        None, "--runtime-commit", help="Runtime commit included in the cutover proof."
    ),
    rollback_backup_identity: str | None = typer.Option(
        None,
        "--rollback-backup-identity",
        help="Authoritative backup identity required for schema retirement.",
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Plan or apply the idempotent legacy Learning→Lab import."""
    from extensions.observation_lab.migration import (
        apply_legacy_learning_migration,
        plan_legacy_learning_migration,
    )

    if retire_legacy_schema and not apply_migration:
        typer.echo("--retire-legacy-schema requires --apply", err=True)
        raise typer.Exit(code=1)
    try:
        payload = (
            apply_legacy_learning_migration(
                archive_root=str(archive_root) if archive_root else None,
                retire_legacy_schema=retire_legacy_schema,
                runtime_commit=runtime_commit,
                rollback_backup_identity=rollback_backup_identity,
            )
            if apply_migration
            else plan_legacy_learning_migration()
        )
    except Exception as exc:
        typer.echo(f"legacy migration refused: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(payload, indent=2, default=str))
        return
    if not payload.get("available", False):
        typer.echo(f"migration unavailable: {payload.get('reason')}", err=True)
        raise typer.Exit(code=1)
    if apply_migration:
        imported = payload.get("imported", {})
        typer.echo(
            "applied legacy import: "
            + ", ".join(f"{key}={value}" for key, value in imported.items())
        )
    else:
        typer.echo(
            "plan only: "
            + ", ".join(
                f"{table}={details['row_count']}"
                for table, details in payload.get("legacy_tables", {}).items()
            )
        )
        typer.echo(f"plan_fingerprint={payload.get('plan_fingerprint')}")


@observe_app.command("set-experiment-authority")
def set_experiment_authority(
    source: str = typer.Argument(..., help="Canonical source: lab (one-way cutover)."),
    reason: str = typer.Option(..., "--reason", help="Audited cutover reason."),
    runtime_commit: str | None = typer.Option(
        None, "--runtime-commit", help="Runtime commit included in a Lab cutover proof."
    ),
    rollback_backup_identity: str | None = typer.Option(
        None,
        "--rollback-backup-identity",
        help="Authoritative backup identity included in a Lab cutover proof.",
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Atomically cut over to the sole Lab Experiment authority."""
    from extensions.observation_lab.canonical import (
        build_authority_cutover_proof,
        set_authority_source,
    )

    try:
        if source != "lab":
            raise ValueError("authority_source_must_be_lab")
        proof = build_authority_cutover_proof(
            runtime_commit=runtime_commit,
            rollback_backup_identity=rollback_backup_identity,
        )
        state = set_authority_source(
            source,
            actor="operator",
            reason=reason,
            cutover_proof=proof,
        )
    except (RuntimeError, ValueError) as exc:
        typer.echo(f"authority change refused: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    payload = {
        "authority_source": state.source,
        "authority_epoch": state.epoch,
        "actor": state.actor,
        "reason": state.reason,
        "updated_at": state.updated_at,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(f"experiment authority={state.source} epoch={state.epoch}")


@observe_app.command("evaluate")
def evaluate_observation(
    observation_id: str = typer.Argument(..., help="Observation id (or unique prefix)."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Run one evaluation pass for a specific Observation."""
    _require_experiment_authority()
    from extensions.observation_lab.evaluator import evaluate_observation as evaluate_lab

    lookup_generation = None
    lookup_id = observation_id
    if ":g" in observation_id and observation_id.rsplit(":g", 1)[1].isdigit():
        lookup_id, raw_generation = observation_id.rsplit(":g", 1)
        lookup_generation = int(raw_generation)
    result = evaluate_lab(lookup_id, lookup_generation)
    if not result.get("available"):
        typer.echo(
            f"canonical Lab evaluation unavailable: {result.get('reason', 'unknown')}",
            err=True,
        )
        raise typer.Exit(code=1)
    if json_output:
        typer.echo(json.dumps(result, indent=2, default=str))
        return
    typer.echo("disposition=evaluated")
    typer.echo(f"verdict={result.get('verdict')}")
    typer.echo(f"source_cutoff={result.get('source_cutoff')}")
    return


@observe_app.command("register")
def register(
    spec_path: Path = typer.Argument(
        ..., exists=True, dir_okay=False, readable=True,
        help="Path to a YAML file describing the Observation spec.",
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Register an Observation from a hand-authored YAML spec.

    Required keys: ``filter_name``, ``stage``, ``scanner_family``, ``direction``,
    ``market_type``, ``horizon``, ``control`` (dict), ``treatment`` (dict).
    Optional: ``filter_version``, ``assignment_policy``, ``min_samples``,
    ``min_coverage``, ``min_edge_delta_pct``. The adapter causal identity hash
    is computed at registration time from the declared adapter identity vector
    (see docs/design/contracts/observation_lab_contract.md).
    """
    import yaml  # local — CLI-only path, keeps import cost off hot startup

    _require_experiment_authority()
    from extensions.observation_lab.canonical import register_observation_spec

    try:
        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        typer.echo(f"cannot parse YAML: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if not isinstance(spec, dict):
        typer.echo("spec file must contain a YAML mapping", err=True)
        raise typer.Exit(code=1)
    try:
        row = register_observation_spec(spec)
    except (KeyError, ValueError, TypeError) as exc:
        typer.echo(f"failed to register: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    payload = {
        "observation_id": row["observation_id"],
        "generation": row["generation"],
        "status": row["status"],
        "authority_mode": row["authority_mode"],
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(
            f"registered canonical Lab observation {row['observation_id']} "
            f"generation {row['generation']}"
        )
    return


@observe_app.command("publish-manifest")
def publish_manifest_cmd(
    spec_path: Path = typer.Argument(
        ..., exists=True, dir_okay=False, readable=True,
        help="Path to a YAML file describing the ExecutionManifest.",
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Publish one ExecutionManifest to lab.db (Packet 2 admission entry).

    Required YAML keys mirror ``extensions.observation_lab.manifest.build_manifest``:

    - ``observation_id``, ``generation``, ``spec_hash``
    - ``treatment_adapter_id``, ``treatment_adapter_version``
    - ``assignment_policy``, ``assignment_salt``, ``assignment_unit_key_template``
    - ``allocation_pct`` (0-1 float)
    - ``control_payload``, ``treatment_payload`` (dicts)
    - ``eligible_population`` (dict; include ``population_key`` when
      ``composability_contract`` is ``disjoint_population``)
    - ``behavior_contract_hash``, ``interference_key``
    - ``composability_contract`` — ``exclusive`` / ``disjoint_population`` / ``factorial``
    - ``effective_from`` (ISO UTC timestamp)
    - ``deterministic_baseline`` (dict), ``unavailable_fallback``
      (``baseline`` / ``exclude``)

    Optional: ``effective_until``, ``owner_admission_ref``, and, for a
    Paper publication, ``inference_plan`` (with ``plan`` and frozen
    ``cutoff`` mappings). ``no_manifest_reason`` records an explicit
    non-executable publication decision instead of issuing a Manifest.

    Exit codes:
    - 0: manifest published or silent no-op (same hash already present).
    - 1: YAML parse error, missing/invalid field, or generation conflict
      (different content already occupies this ``(observation_id,
      generation)``; caller must bump generation).
    """
    import yaml

    from extensions.observation_lab.manifest import (
        ManifestGenerationConflict,
        build_manifest,
        publish_manifest,
    )

    try:
        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        typer.echo(f"cannot parse YAML: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if not isinstance(spec, dict):
        typer.echo("spec file must contain a YAML mapping", err=True)
        raise typer.Exit(code=1)

    # Lifecycle-only publication fields are not Manifest wire fields.  Keep
    # them in the same YAML document so the Lab transaction can atomically
    # freeze the plan, publish the Manifest, and append its lifecycle/audit
    # evidence.
    inference_plan = spec.pop("inference_plan", None)
    no_manifest_reason = spec.pop("no_manifest_reason", None)

    try:
        manifest = build_manifest(**spec)
    except (TypeError, ValueError, KeyError) as exc:
        typer.echo(f"invalid manifest spec: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    try:
        _require_experiment_authority()
        from extensions.observation_lab.canonical import publish_manifest_with_lifecycle
        from extensions.observation_lab.runner import register_builtin_handlers

        register_builtin_handlers()
        publication = publish_manifest_with_lifecycle(
            manifest,
            actor="operator",
            reason="cli_manifest_publish",
            inference_plan=inference_plan,
            no_manifest_reason=no_manifest_reason,
        )
        newly_inserted = bool(publication["published"])
    except ManifestGenerationConflict as exc:
        typer.echo(f"generation conflict: {exc}", err=True)
        typer.echo(
            "bump ``generation`` in the spec for a semantically different manifest.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    payload = {
        "manifest_id": manifest.manifest_id,
        "manifest_hash": manifest.manifest_hash,
        "observation_id": manifest.observation_id,
        "generation": manifest.generation,
        "newly_inserted": newly_inserted,
    }
    payload.update({
        "lifecycle_event_id": publication["lifecycle_event_id"],
        "inference_plan_id": publication["inference_plan_id"],
        "no_manifest_reason": publication["no_manifest_reason"],
    })
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return
    verb = "published" if newly_inserted else "already published (no-op)"
    typer.echo(f"{verb} manifest {manifest.manifest_id[:12]}…")
    typer.echo(f"  observation_id={manifest.observation_id} generation={manifest.generation}")
    typer.echo(f"  manifest_hash={manifest.manifest_hash}")


@observe_app.command("close")
def close_observation(
    observation_id: str = typer.Argument(..., help="Observation id (or unique prefix)."),
    reason: str = typer.Option(
        ..., "--reason",
        help="Human-readable close reason (recorded in invalidation_reason).",
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Stop a collecting Observation and record the operator's reason.

    Owner review §7: stopping preserves all evidence/lifecycle history — this
    only halts further collection. Historical evaluations remain queryable.
    """
    _require_experiment_authority()
    from extensions.observation_lab.canonical import record_owner_action

    target = _find_lab_generation(observation_id)
    if target is None:
        typer.echo("canonical Lab observation unavailable or ambiguous", err=True)
        raise typer.Exit(code=1)
    receipt_id = record_owner_action(
        action="close",
        actor="operator",
        reason=reason,
        observation_id=target["observation_id"],
        generation=int(target["generation"]),
        dedup_key=f"cli-close:{target['observation_id']}:{target['generation']}:{reason[:200]}",
    )
    payload = {
        "observation_id": target["observation_id"],
        "generation": int(target["generation"]),
        "receipt_id": receipt_id,
        "status": "pending_lab_application",
        "reason": reason,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(
            f"close requested for {target['observation_id']} generation "
            f"{target['generation']} (receipt={receipt_id})"
        )
    return


def _find_lab_generation(observation_id: str) -> dict | None:
    from extensions.observation_lab.db import readonly_lab_db

    observation_id, generation = _split_lab_observation_ref(observation_id)
    try:
        with readonly_lab_db(allow_missing=True) as db:
            if db is None:
                return None
            generation_clause = " AND generation=?" if generation else ""
            params: tuple[object, ...] = (
                observation_id, len(observation_id), observation_id,
            )
            if generation:
                params += (generation,)
            rows = db.execute(
                "SELECT observation_id, generation, status FROM observation_windows "
                "WHERE (observation_id=? OR substr(observation_id,1,?)=?) "
                f"{generation_clause} "
                "ORDER BY observation_id, generation DESC",
                params,
            ).fetchall()
            if len(rows) != 1:
                return None
            return dict(rows[0])
    except Exception:
        return None


def _split_lab_observation_ref(value: str) -> tuple[str, int | None]:
    """Accept the report id form ``<observation_id>:g<generation>``."""
    prefix, separator, raw_generation = str(value).rpartition(":g")
    if separator and prefix and raw_generation.isdigit() and int(raw_generation) > 0:
        return prefix, int(raw_generation)
    return str(value), None


def _systemctl_show(unit: str, properties: tuple[str, ...]) -> dict[str, str]:
    """Read one user unit without changing systemd state."""
    import subprocess

    result = subprocess.run(
        [
            "systemctl",
            "--user",
            "show",
            unit,
            f"--property={','.join(properties)}",
            "--no-pager",
        ],
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("systemctl_show_failed")
    return {
        key: value
        for line in result.stdout.splitlines()
        for key, separator, value in [line.partition("=")]
        if separator and key
    }


def _git_head(checkout: str | None) -> str | None:
    """Return the checkout's exact HEAD without fetching or mutating it."""
    import subprocess

    if not checkout or not Path(checkout).is_dir():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", checkout, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    head = result.stdout.strip()
    return head if result.returncode == 0 and len(head) == 40 else None


def _collect_worker_status() -> dict:
    try:
        timer = _systemctl_show(
            "aletheia-observer.timer", ("LoadState", "ActiveState")
        )
        service = _systemctl_show(
            "aletheia-observer.service",
            (
                "LoadState",
                "Result",
                "ExecMainStatus",
                "ExecMainExitTimestamp",
                "WorkingDirectory",
            ),
        )
        systemd_available = True
    except Exception:
        timer = {}
        service = {}
        systemd_available = False

    checkout = service.get("WorkingDirectory") or None
    checkout_available = bool(checkout and Path(checkout).is_dir())
    raw_exit_code = service.get("ExecMainStatus")
    try:
        exit_code = int(raw_exit_code) if raw_exit_code not in (None, "") else None
    except ValueError:
        exit_code = None
    return {
        "systemd_available": systemd_available,
        "checkout": checkout,
        "checkout_available": checkout_available,
        "head_sha": _git_head(checkout) if checkout_available else None,
        "timer": {
            "load_state": timer.get("LoadState"),
            "active_state": timer.get("ActiveState"),
        },
        "last_service_pass": {
            "load_state": service.get("LoadState"),
            "status": service.get("Result") or None,
            "at": service.get("ExecMainExitTimestamp") or None,
            "exit_code": exit_code,
        },
    }


def _collect_overview(*, equivalence_limit: int = 5) -> dict:
    """Whole-Lab snapshot backing ``observe overview``.

    Kept as a pure function so unit tests can seed lab.db, call it once,
    and inspect the shape without spawning a subprocess. The CLI command
    is a thin renderer on top.
    """
    from contextlib import ExitStack

    from aletheia.config.loader import get_data_dir, read_only_config
    from extensions.observation_lab.checkpoint import (
        latest_joint_checkpoint,
        verify_joint_checkpoint,
    )
    from extensions.observation_lab.canonical import observation_bridge_health
    from extensions.observation_lab.db import readonly_lab_db
    from extensions.observation_lab.execution_owner import get_execution_owner
    from extensions.observation_lab.kernel import status_window
    from extensions.observation_lab.manifest import load_active_manifests
    from extensions.observation_lab.runner import register_builtin_handlers
    from extensions.observation_lab.ufc_shadow import decode_equivalence_result
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import db_path, readonly_db

    with read_only_config():
        owner = get_execution_owner()
    authority_source = "unavailable"
    try:
        with readonly_db(allow_missing=True) as authority_db:
            if authority_db is not None:
                row = authority_db.execute(
                    "SELECT authority_source FROM observation_authority_state "
                    "WHERE state_key='experiment_authority'"
                ).fetchone()
                authority_source = str(row[0]) if row else "unavailable"
    except Exception:
        authority_source = "unavailable"
    worker = _collect_worker_status()
    reasons: set[str] = set()
    unavailable_reasons: set[str] = set()
    if owner not in {"main", "worker"}:
        unavailable_reasons.add("execution_owner_unavailable")

    app_path = db_path()
    lab_path = get_data_dir() / "observation-lab" / "lab.db"
    storage = {
        "app_db": {"path": str(app_path), "status": "missing"},
        "lab_db": {"path": str(lab_path), "status": "missing"},
    }
    counts = {
        "available": False,
        "total": None,
        "collecting": None,
        "ready_for_review": None,
        "completed_no_support": None,
        "invalidated": None,
    }
    equivalence_rows: list[dict] = []
    recent_matched = recent_mismatched = recent_skipped = None
    manifest_dicts: list[dict] = []
    manifest_count: int | None = None
    checkpoint_payload = {
        "latest": None,
        "verified": {"ok": None, "reason": "unavailable", "current_fingerprint": None},
    }
    windows = {"available": False, "active_count": None, "items": []}
    bridge = {"available": False, "reason": "unavailable"}

    with ExitStack() as stack:
        try:
            app_db = stack.enter_context(readonly_db(allow_missing=True))
            if app_db is not None:
                storage["app_db"]["status"] = "reachable"
        except Exception:
            app_db = None
            storage["app_db"]["status"] = "unreachable"
        try:
            lab_db = stack.enter_context(readonly_lab_db(allow_missing=True))
            if lab_db is not None:
                storage["lab_db"]["status"] = "reachable"
        except Exception:
            lab_db = None
            storage["lab_db"]["status"] = "unreachable"

        if app_db is not None and authority_source != "lab":
            try:
                experiments = repo.list_filter_shadow_experiments(limit=1000)
                counts = {"available": True, "total": len(experiments)}
                for status in (
                    "collecting",
                    "ready_for_review",
                    "completed_no_support",
                    "invalidated",
                ):
                    counts[status] = sum(
                        1 for row in experiments if row["status"] == status
                    )
            except Exception:
                storage["app_db"]["status"] = "unreachable"
                app_db = None

        if lab_db is not None and authority_source == "lab":
            try:
                rows = lab_db.execute(
                    "SELECT w.status, w.kind, e.result_json "
                    "FROM observation_windows w "
                    "LEFT JOIN observation_evaluations e ON e.evaluation_id = ("
                    "SELECT evaluation_id FROM observation_evaluations "
                    "WHERE observation_id=w.observation_id AND generation=w.generation "
                    "ORDER BY created_at DESC, evaluation_id DESC LIMIT 1)"
                ).fetchall()
                projected: list[str] = []
                for row in rows:
                    status = str(row[0])
                    if status == "active":
                        disposition = "collecting"
                        if row[2]:
                            try:
                                payload = json.loads(str(row[2]))
                            except (TypeError, ValueError, json.JSONDecodeError):
                                payload = {}
                            if isinstance(payload, dict) and payload.get("disposition") in {
                                "ready_for_review", "completed_no_support", "invalidated",
                            }:
                                disposition = str(payload["disposition"])
                    elif status == "invalidated":
                        disposition = "invalidated"
                    else:
                        disposition = "completed_no_support"
                    projected.append(disposition)
                counts = {
                    "available": True,
                    "total": len(rows),
                    "collecting": projected.count("collecting"),
                    "ready_for_review": projected.count("ready_for_review"),
                    "completed_no_support": projected.count("completed_no_support"),
                    "invalidated": projected.count("invalidated"),
                }
            except Exception:
                storage["lab_db"]["status"] = "unreachable"

        if lab_db is not None:
            try:
                limit = max(0, int(equivalence_limit))
                equivalence_rows = [
                    dict(row)
                    for row in lab_db.execute(
                        "SELECT observation_id, checked_at, matched, mismatch_field, "
                        "learning_verdict, recomputed_verdict, notes "
                        "FROM ufc_shadow_equivalence ORDER BY checked_at DESC LIMIT ?",
                        (limit,),
                    )
                    .fetchall()
                ]
                for row in equivalence_rows:
                    row["result"] = decode_equivalence_result(row)
                recent_matched = sum(
                    1 for row in equivalence_rows if row["result"] == "matched"
                )
                recent_mismatched = sum(
                    1 for row in equivalence_rows if row["result"] == "mismatched"
                )
                recent_skipped = sum(
                    1 for row in equivalence_rows if row["result"] == "skipped"
                )
                active_manifests = load_active_manifests(db=lab_db)
                manifest_dicts = [
                    {
                        "observation_id": manifest.observation_id,
                        "generation": manifest.generation,
                        "manifest_hash": manifest.manifest_hash,
                        "composability_contract": manifest.composability_contract,
                        "effective_from": manifest.effective_from,
                        "effective_until": manifest.effective_until,
                    }
                    for manifest in active_manifests
                ]
                manifest_count = len(manifest_dicts)
            except Exception:
                storage["lab_db"]["status"] = "unreachable"
                lab_db = None

        if lab_db is not None:
            try:
                register_builtin_handlers()
                active_rows = (
                    lab_db.execute(
                        "SELECT observation_id, generation FROM observation_windows "
                        "WHERE status='active' ORDER BY observation_id, generation"
                    )
                    .fetchall()
                )
                items = []
                for row in active_rows:
                    status = status_window(str(row[0]), int(row[1]), db=lab_db)
                    items.append(
                        {
                            "observation_id": status["observation_id"],
                            "generation": status["generation"],
                            "kind": status["kind"],
                            "phase": status["phase"],
                            "handler_available": status["handler_available"],
                            "last_advanced_at": status.get("last_advanced_at"),
                            "last_error": status.get("last_error"),
                        }
                    )
                windows = {
                    "available": True,
                    "active_count": len(items),
                    "items": items,
                }
            except Exception:
                reasons.add("window_state_unavailable")
                unavailable_reasons.add("window_state_unavailable")

        if app_db is not None and lab_db is not None:
            try:
                latest_cp = latest_joint_checkpoint()
                verified = verify_joint_checkpoint()
                checkpoint_payload = {
                    "latest": None if latest_cp is None else {
                        "recorded_at": latest_cp.recorded_at,
                        "joint_fingerprint": latest_cp.joint_fingerprint,
                        "app_migration_head": latest_cp.app_migration_head,
                        "app_migration_count": latest_cp.app_migration_count,
                        "active_manifest_count": latest_cp.active_manifest_count,
                        "main_outbox_high_watermark": latest_cp.main_outbox_high_watermark,
                        "main_outbox_pending_count": latest_cp.main_outbox_pending_count,
                        "main_outbox_fingerprint": latest_cp.main_outbox_fingerprint,
                    },
                    "verified": {
                        "ok": verified.ok,
                        "reason": verified.reason,
                        "current_fingerprint": verified.current_fingerprint,
                        "mismatch_components": list(
                            getattr(verified, "mismatch_components", ())
                        ),
                    },
                }
                bridge = observation_bridge_health()
            except Exception:
                storage["lab_db"]["status"] = "unreachable"

    if app_db is not None and authority_source == "unavailable":
        unavailable_reasons.add("experiment_authority_unavailable")
    if lab_db is not None and authority_source == "lab" and not bridge.get("available", False):
        unavailable_reasons.add("observation_bridge_unavailable")

    for name in ("app_db", "lab_db"):
        status = storage[name]["status"]
        if status != "reachable":
            code = f"{name}_{status}"
            reasons.add(code)
            unavailable_reasons.add(code)

    if owner == "worker":
        if not worker["systemd_available"]:
            reasons.add("worker_systemd_unavailable")
            unavailable_reasons.add("worker_systemd_unavailable")
        else:
            if worker["timer"]["load_state"] != "loaded":
                reasons.add("worker_timer_not_loaded")
            if worker["timer"]["active_state"] != "active":
                reasons.add("worker_timer_inactive")
            service_pass = worker["last_service_pass"]
            if service_pass["load_state"] != "loaded":
                reasons.add("worker_service_not_loaded")
            if service_pass["at"] is None:
                reasons.add("worker_pass_not_recorded")
            elif service_pass["status"] != "success" or service_pass["exit_code"] != 0:
                reasons.add("worker_last_pass_failed")
            if not worker.get("checkout_available", worker["checkout"] is not None):
                reasons.add("worker_checkout_unavailable")
            if worker["head_sha"] is None:
                reasons.add("worker_head_unavailable")
    elif worker["systemd_available"] and worker["timer"]["active_state"] == "active":
        reasons.add("worker_timer_active_with_main_owner")

    if windows["available"]:
        for item in windows["items"]:
            if not item["handler_available"]:
                reasons.add("active_window_handler_unavailable")
            if item["last_error"]:
                reasons.add("active_window_last_error")
    if checkpoint_payload["verified"]["ok"] is False:
        reasons.add("observation_checkpoint_mismatch")
    if recent_mismatched:
        reasons.add("ufc_equivalence_mismatch")

    reasons.update(unavailable_reasons)
    overall_status = (
        "unavailable" if unavailable_reasons else "degraded" if reasons else "healthy"
    )
    return {
        "overall_status": overall_status,
        "reason_codes": sorted(reasons),
        "execution_owner": owner,
        "experiment_authority_source": authority_source,
        "storage": storage,
        "worker": worker,
        "windows": windows,
        "experiments": counts,
        "ufc_equivalence": {
            "recent_records": [
                {
                    "observation_id": r["observation_id"],
                    "checked_at": r["checked_at"],
                    "result": r["result"],
                    "matched": (
                        True
                        if r["result"] == "matched"
                        else False
                        if r["result"] == "mismatched"
                        else None
                    ),
                    "mismatch_field": r.get("mismatch_field"),
                    "learning_verdict": r.get("learning_verdict"),
                    "recomputed_verdict": r.get("recomputed_verdict"),
                    "notes": r.get("notes"),
                }
                for r in equivalence_rows
            ],
            "recent_matched": recent_matched,
            "recent_mismatched": recent_mismatched,
            "recent_skipped": recent_skipped,
            "recent_checked": (
                None if recent_matched is None else recent_matched + recent_mismatched
            ),
            "recent_total": None if recent_matched is None else len(equivalence_rows),
        },
        "manifests": {
            "active": manifest_dicts,
            "active_count": manifest_count,
        },
        "checkpoint": checkpoint_payload,
        "bridge": bridge,
    }


