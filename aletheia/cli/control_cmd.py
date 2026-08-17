"""Minimal machine control plane: `aletheia control status`.

Surfaces master/deployed SHA, active hypotheses, policy epochs, authority,
and window continuity in one command — so issues like #470/#657 never need
manually maintained dynamic values in governance documents.

All reads are offline-safe: the command works without a running Gateway.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import typer

control_app = typer.Typer(no_args_is_help=True, help="Machine governance control plane.")


def _disk_commit() -> str:
    """Return on-disk source/release SHA; empty on error."""
    try:
        from aletheia.update.git_ops import current_commit as _git_sha, is_repo
        if is_repo():
            return _git_sha() or ""
    except Exception:
        pass
    try:
        from aletheia.update.packaged import current_selection, _paths, _read_json
        sel = current_selection()
        current = sel.get("current")
        if isinstance(current, str) and current:
            mf = _read_json(_paths()["releases"] / current / "release-manifest.json", required=False)
            sha = mf.get("git_sha")
            return sha if isinstance(sha, str) and sha else ""
    except Exception:
        pass
    return ""


def _deployed_commit() -> str:
    """Return the running Gateway boot SHA; empty when unavailable."""
    try:
        from aletheia.agent_runtime.build_info import get_boot_commit

        sha = get_boot_commit()
        if sha:
            return sha
    except Exception:
        pass
    try:
        from aletheia.storage.sqlite import get_db

        row = get_db().execute(
            "SELECT boot_commit FROM runtime_sessions "
            "WHERE ended_at IS NULL AND boot_commit IS NOT NULL "
            "ORDER BY started_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        return str(row["boot_commit"]) if row and row["boot_commit"] else ""
    except Exception:
        return ""


def _cached_master_commit() -> str:
    """Return the cached origin/master tracking SHA; empty if unavailable."""
    try:
        from aletheia.update.git_ops import remote_commit
        return remote_commit("master") or ""
    except Exception:
        return ""


def _cfg_commit() -> str:
    """Return the last recorded commit SHA from config."""
    try:
        from aletheia.config.loader import load_config
        return load_config().update.current_commit or ""
    except Exception:
        return ""


def _auto_deploy_commit() -> str:
    """Return the last successfully activated stable Tag SHA."""
    try:
        from aletheia.update.auto import status

        last = status().get("last_successful") or {}
        sha = last.get("sha") if isinstance(last, dict) else None
        return sha if isinstance(sha, str) else ""
    except Exception:
        return ""


def _open_experiments() -> dict[str, Any]:
    try:
        from aletheia.learning.ledger import list_open_experiments

        return {"available": True, "items": list_open_experiments()}
    except Exception as exc:
        return {"available": False, "items": [], "error": type(exc).__name__}


def _open_policy_epochs(limit: int = 10) -> list[dict[str, Any]]:
    try:
        from aletheia.storage.sqlite import get_db
        rows = get_db().execute(
            "SELECT epoch_id, epoch_kind, effective_values_json, started_at "
            "FROM learning_policy_epochs WHERE ended_at IS NULL "
            "ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    except Exception:
        return []


def _evidence_epochs() -> dict[str, Any]:
    try:
        from aletheia.learning.evidence_epoch import evidence_epoch_summary
        return evidence_epoch_summary()
    except Exception:
        return {"error": "unavailable"}


def _learning_authority() -> dict[str, Any]:
    try:
        from aletheia.learning.control import (
            learning_influence_gate_reason,
            global_learning_refreeze_reason,
        )
        gate_reason = learning_influence_gate_reason()
        refreeze_reason = global_learning_refreeze_reason()
        return {
            "influence_gate": gate_reason or "open",
            "global_refreeze": refreeze_reason or "none",
            "influence_enabled": gate_reason is None,
        }
    except Exception:
        return {"error": "unavailable"}


def _runtime_continuity() -> dict[str, Any]:
    try:
        from aletheia.config.loader import load_config
        from aletheia.agent_runtime.pause_ownership import (
            active_pause_blocker_names,
            active_pause_owners,
            pause_blocked_reason,
        )
        cfg = load_config()
        owners = active_pause_owners(cfg)
        blockers = active_pause_blocker_names(cfg)
        return {
            "enabled": cfg.runtime.runtime_continuity_enabled,
            "paused": cfg.runtime.paused,
            "pause_owners": owners,
            "pause_blockers": list(blockers),
            "resume_blocked_reason": pause_blocked_reason(cfg),
            "scheduler_resume_authority": {
                "allowed": not blockers,
                "blocked_by": list(blockers),
                "dependency_recovery_is_resume": False,
            },
            "kill_switch_active": cfg.runtime.kill_switch_active,
            "demo_brake_active": cfg.runtime.demo_brake_active,
            "live_brake_active": cfg.runtime.live_brake_active,
        }
    except Exception:
        return {"error": "unavailable"}


def _gateway_running() -> bool | None:
    try:
        from aletheia.gateway.pidfile import is_running
        return is_running()
    except Exception:
        return None


def _position_path_observer_status() -> dict[str, Any]:
    """Return durable Position-path recovery facts without inventing runtime state."""

    try:
        from aletheia.portfolio.position_path_observer import (
            build_position_path_observer_status,
        )

        return {"available": True, **build_position_path_observer_status()}
    except Exception as exc:
        return {
            "available": False,
            "error": type(exc).__name__,
            "oldest_missing_bucket": None,
            "deferred_bucket_count": None,
            "observer_lag": None,
            "last_24h": None,
        }


def build_control_status() -> dict[str, Any]:
    """Collect all governance-relevant state into one serializable dict."""
    deployed = _deployed_commit()
    disk = _disk_commit()
    master = _cached_master_commit()
    cfg_sha = _cfg_commit()
    auto_sha = _auto_deploy_commit()
    if deployed and auto_sha == deployed:
        recorded_sha, recorded_source = auto_sha, "auto_release_tag"
    elif deployed and cfg_sha == deployed:
        recorded_sha, recorded_source = cfg_sha, "config"
    elif auto_sha:
        recorded_sha, recorded_source = auto_sha, "auto_release_tag"
    else:
        recorded_sha, recorded_source = cfg_sha, "config" if cfg_sha else "not_available"

    sha_section = {
        "deployed_head": deployed,
        "deployed_head_short": deployed[:12] if deployed else "not_available",
        "disk_head": disk,
        "disk_head_short": disk[:12] if disk else "not_available",
        "cached_master_head": master,
        "cached_master_head_short": master[:12] if master else "not_available",
        "config_recorded": cfg_sha,
        "config_recorded_short": cfg_sha[:12] if cfg_sha else "not_available",
        "auto_recorded": auto_sha,
        "auto_recorded_short": auto_sha[:12] if auto_sha else "not_available",
        "runtime_recorded": recorded_sha,
        "runtime_recorded_short": recorded_sha[:12] if recorded_sha else "not_available",
        "runtime_recorded_source": recorded_source,
        "deployed_matches_recorded": (
            bool(deployed and recorded_sha and deployed[:12] == recorded_sha[:12])
            if deployed and recorded_sha else None
        ),
        "deployed_matches_config": (
            bool(deployed and cfg_sha and deployed[:12] == cfg_sha[:12])
            if deployed and cfg_sha else None
        ),
        "deployed_matches_master": (
            bool(deployed and master and deployed[:12] == master[:12])
            if deployed and master else None
        ),
        "disk_matches_deployed": (
            bool(disk and deployed and disk[:12] == deployed[:12])
            if disk and deployed else None
        ),
    }

    # Active hypotheses / open experiments
    experiment_result = _open_experiments()
    active_hypotheses: list[dict[str, Any]] = []
    for exp in experiment_result["items"]:
        active_hypotheses.append({
            "experiment_id": exp.get("experiment_id"),
            "hypothesis_id": exp.get("hypothesis_id"),
            "stage": exp.get("stage"),
            "status": exp.get("status"),
            "started_at": exp.get("started_at"),
            "policy_epoch": exp.get("concurrent_policy_epoch"),
        })

    # Policy epochs (most recent open)
    raw_epochs = _open_policy_epochs(limit=8)
    open_epochs: list[dict[str, Any]] = []
    for row in raw_epochs:
        open_epochs.append({
            "epoch_id": row.get("epoch_id"),
            "epoch_kind": row.get("epoch_kind"),
            "started_at": row.get("started_at"),
        })

    # Evidence epochs (trailing + entry)
    evidence = _evidence_epochs()
    authority = _learning_authority()
    continuity = _runtime_continuity()
    gateway = _gateway_running()
    position_path = _position_path_observer_status()

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "sha": sha_section,
        "gateway_running": gateway,
        "runtime": continuity,
        "position_path_observer": position_path,
        "authority": authority,
        "active_experiments": active_hypotheses,
        "active_experiments_available": experiment_result["available"],
        "active_experiments_error": experiment_result.get("error"),
        "open_policy_epochs": open_epochs,
        "evidence_epochs": evidence,
        "window_continuity": {
            "evidence_anchored": evidence.get("all_anchored", False),
            "evidence_sha_consistent": evidence.get("all_sha_consistent", False),
            "deployed_head_consistent": sha_section["deployed_matches_recorded"],
        },
    }


@control_app.command("status")
def status(
    json_output: bool = typer.Option(False, "--json", help="Print raw JSON."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Include open policy epochs."),
):
    """Show the machine governance control-plane snapshot.

    Reports master/deployed SHA, active hypotheses, policy epochs, authority
    state, and observation window continuity — eliminating the need for
    manually maintained dynamic values in issue trackers (e.g. #470, #657).
    """
    data = build_control_status()

    if json_output:
        typer.echo(json.dumps(data, indent=2, ensure_ascii=False))
        return

    sha = data["sha"]
    continuity = data["window_continuity"]
    authority = data.get("authority", {})
    evidence = data.get("evidence_epochs", {})

    # ─── Header ────────────────────────────────────────────────────────────
    typer.echo("╔═ Aletheia Control Status ════════════════════════════╗")
    typer.echo(f"  As of     : {data['as_of'][:19]}Z")
    typer.echo(f"  Gateway   : {'running' if data.get('gateway_running') else 'stopped' if data.get('gateway_running') is False else 'unknown'}")
    paused = data.get("runtime", {}).get("paused")
    typer.echo(f"  Paused    : {paused}")
    runtime = data.get("runtime", {})
    blockers = runtime.get("pause_blockers") or []
    typer.echo(f"  Resume    : {'blocked by ' + ', '.join(blockers) if blockers else 'permitted'}")
    owners = runtime.get("pause_owners") or []
    if owners:
        for owner in owners:
            typer.echo(
                f"  Owner     : {owner.get('owner')} ({owner.get('reason')}) "
                f"generation={owner.get('generation') or 'unknown'} "
                f"age={owner.get('age_seconds') if owner.get('age_seconds') is not None else 'unknown'}s"
            )
    typer.echo("╠═ SHA ════════════════════════════════════════════════╣")
    typer.echo(f"  Running   : {sha['deployed_head_short']}{'  ✓ matches recorded deployment' if sha.get('deployed_matches_recorded') else '  ⚠ recorded deployment disagrees' if sha.get('deployed_matches_recorded') is False else ''}")
    typer.echo(f"  Disk      : {sha['disk_head_short']}{'  (same as running)' if sha.get('disk_matches_deployed') else '  (different from running)' if sha.get('disk_matches_deployed') is False else ''}")
    typer.echo(f"  Cached master: {sha['cached_master_head_short']}{'  (same as running)' if sha.get('deployed_matches_master') else '  (different)' if sha.get('deployed_matches_master') is False else ''}")
    typer.echo(f"  Config    : {sha['config_recorded_short']}")
    typer.echo(
        f"  Recorded  : {sha['runtime_recorded_short']} "
        f"({sha['runtime_recorded_source']})"
    )

    # ─── Position path observer ────────────────────────────────────────────
    typer.echo("╠═ Position Path Observer ══════════════════════════════╣")
    position_path = data.get("position_path_observer", {})
    if not position_path.get("available"):
        typer.echo("  (not available — durable observer state query failed)")
    else:
        oldest = position_path.get("oldest_missing_bucket") or {}
        typer.echo(
            "  Oldest missing: "
            + (
                f"{oldest.get('position_id')} @ {oldest.get('bucket_open_at')}"
                if oldest
                else "none"
            )
        )
        typer.echo(f"  Deferred buckets: {position_path.get('deferred_bucket_count')}")
        lag = position_path.get("observer_lag") or {}
        typer.echo(
            f"  Observer lag: {lag.get('seconds', 'not_available')}s "
            f"({lag.get('buckets', 'not_available')} buckets)"
        )
        counts = position_path.get("last_24h") or {}
        typer.echo(
            "  Last 24h: "
            f"available={counts.get('available', 'not_available')} "
            f"unavailable={counts.get('unavailable', 'not_available')} "
            f"skipped={counts.get('skipped', 'not_available')}"
        )

    # ─── Authority ─────────────────────────────────────────────────────────
    typer.echo("╠═ Authority ══════════════════════════════════════════╣")
    if "error" in authority:
        typer.echo("  (unavailable — gateway may be stopped)")
    else:
        gate = authority.get("influence_gate", "unknown")
        refreeze = authority.get("global_refreeze", "none")
        typer.echo(f"  Influence gate : {gate}")
        typer.echo(f"  Global refreeze: {refreeze}")

    # ─── Active experiments / hypotheses ───────────────────────────────────
    typer.echo("╠═ Active Hypotheses ══════════════════════════════════╣")
    exps = data.get("active_experiments", [])
    if not data.get("active_experiments_available"):
        typer.echo("  (not available — experiment ledger query failed)")
    elif not exps:
        typer.echo("  (none open)")
    else:
        for exp in exps:
            typer.echo(
                f"  [{exp.get('stage', '?')}] {exp.get('experiment_id', '?')[:16]}  "
                f"status={exp.get('status', '?')}  "
                f"started={str(exp.get('started_at') or '')[:19]}"
            )
            if exp.get("policy_epoch"):
                typer.echo(f"      epoch: {exp['policy_epoch'][:12]}...")

    # ─── Evidence epoch window ─────────────────────────────────────────────
    typer.echo("╠═ Evidence Epochs ════════════════════════════════════╣")
    typer.echo(f"  Anchored : {evidence.get('all_anchored', False)}   SHA consistent: {evidence.get('all_sha_consistent', False)}")
    for path in evidence.get("paths", []):
        flag = "✓" if path.get("sha_consistent") else ("!" if path.get("sha_consistent") is False else "?")
        typer.echo(
            f"  {flag} {path['path_id']}: {path.get('status', '?')}  "
            f"started={str(path.get('started_at') or 'none')[:19]}"
        )

    # ─── Window continuity ─────────────────────────────────────────────────
    typer.echo("╠═ Window Continuity ══════════════════════════════════╣")
    for key, val in continuity.items():
        flag = "✓" if val is True else ("⚠" if val is False else "?")
        typer.echo(f"  {flag} {key}: {val}")

    # ─── Open policy epochs (verbose) ──────────────────────────────────────
    if verbose:
        typer.echo("╠═ Open Policy Epochs ═════════════════════════════════╣")
        epochs = data.get("open_policy_epochs", [])
        if not epochs:
            typer.echo("  (none)")
        else:
            for ep in epochs:
                typer.echo(
                    f"  [{ep.get('epoch_kind', '?')[:40]}]  "
                    f"id={str(ep.get('epoch_id') or '')[:12]}  "
                    f"started={str(ep.get('started_at') or '')[:19]}"
                )

    typer.echo("╚══════════════════════════════════════════════════════╝")
