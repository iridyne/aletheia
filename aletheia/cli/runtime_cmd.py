"""Runtime continuity CLI: `aletheia runtime ...`."""

import json

import typer

from aletheia.runtime_continuity.service import RuntimeContinuityService, RuntimeTransitionError

runtime_app = typer.Typer(no_args_is_help=True)


def _service() -> RuntimeContinuityService:
    from aletheia.config.loader import load_config

    cfg = load_config()
    shadow_gateway_launcher = None
    if cfg.runtime.runtime_continuity_enabled:
        from aletheia.runtime_continuity.processes import spawn_shadow_gateway

        shadow_gateway_launcher = lambda runtime: spawn_shadow_gateway(cfg, runtime)
    return RuntimeContinuityService(
        lock_ttl_sec=cfg.runtime.runtime_continuity_lock_ttl_sec,
        enabled=cfg.runtime.runtime_continuity_enabled,
        shadow_gateway_launcher=shadow_gateway_launcher,
    )


def _fail(exc: RuntimeTransitionError) -> None:
    typer.echo(f"Runtime transition rejected: {exc}")
    raise typer.Exit(1)


@runtime_app.command()
def status(json_output: bool = typer.Option(False, "--json", help="Print raw JSON.")):
    """Show current runtime continuity state."""
    payload = _service().status()
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    current = payload.get("current") or {}
    candidate = payload.get("candidate") or {}
    typer.echo(f"Current: {current.get('runtime_id', 'not configured')} ({current.get('state', 'unknown')})")
    typer.echo(f"Candidate: {candidate.get('runtime_id', 'none')} ({candidate.get('state', 'none')})")
    typer.echo(f"Can promote: {payload.get('can_promote')}")
    typer.echo(f"Can rollback: {payload.get('can_rollback')}")
    typer.echo(f"Can drain: {payload.get('can_drain')}")
    guidance = payload.get("guidance") or {}
    metrics = payload.get("metrics") or {}
    typer.echo(f"Guidance: {guidance.get('action', 'observe')} ({guidance.get('reason', 'no_reason')})")
    typer.echo(
        "Metrics: "
        f"locks={metrics.get('active_lock_count', 0)} "
        f"events_24h={metrics.get('recent_event_count', 0)} "
        f"failed={metrics.get('failed_runtime_count', 0)}"
    )
    if payload.get("activation_required"):
        typer.echo("Activation: restart required (supervisor respawn)")
    if payload.get("lock_owner"):
        typer.echo(f"Lock: {payload['lock_owner']} until {payload.get('expires_at')}")


@runtime_app.command()
def candidate(
    code_version: str = typer.Option("", "--code-version"),
    config_version: str = typer.Option("local-config", "--config-version"),
    schema_version: str = typer.Option("sqlite-runtime-continuity-v1", "--schema-version"),
    build_git_sha: str = typer.Option("", "--git-sha"),
    release_bundle_hash: str = typer.Option("", "--bundle-hash"),
    artifact_path: str = typer.Option("", "--artifact-path"),
):
    """Create a candidate runtime record without promoting it."""
    try:
        record = _service().prepare_candidate(
            actor="human-cli",
            cause="runtime_cli_candidate",
            code_version=code_version or None,
            config_version=config_version or None,
            schema_version=schema_version or None,
            build_git_sha=build_git_sha or None,
            release_bundle_hash=release_bundle_hash or build_git_sha or None,
            artifact_path=artifact_path or None,
        )
    except RuntimeTransitionError as exc:
        _fail(exc)
    typer.echo(f"Candidate: {record['runtime_id']} ({record['state']})")


@runtime_app.command()
def shadow(runtime_id: str = typer.Argument("", help="Candidate runtime id. Defaults to latest candidate.")):
    """Start and validate a shadow runtime."""
    svc = _service()
    target = runtime_id or ((svc.status().get("candidate") or {}).get("runtime_id") or "")
    if not target:
        typer.echo("No candidate runtime available.")
        raise typer.Exit(1)
    try:
        started = svc.start_shadow(target, actor="human-cli", cause="runtime_cli_shadow")
        validated = svc.validate_shadow(started["runtime_id"], actor="human-cli", cause="runtime_cli_shadow")
    except RuntimeTransitionError as exc:
        _fail(exc)
    typer.echo(f"Shadow: {validated['runtime_id']} ({validated['state']})")


@runtime_app.command()
def promote(runtime_id: str = typer.Argument("", help="Ready runtime id. Defaults to latest candidate.")):
    """Promote a ready shadow runtime to current."""
    svc = _service()
    target = runtime_id or ((svc.status().get("candidate") or {}).get("runtime_id") or "")
    if not target:
        typer.echo("No promotable runtime available.")
        raise typer.Exit(1)
    try:
        payload = svc.promote(target, actor="human-cli", cause="runtime_cli_promote")
    except RuntimeTransitionError as exc:
        _fail(exc)
    current = payload.get("current") or {}
    typer.echo(f"Current: {current.get('runtime_id')} ({current.get('state')})")
    if (payload.get("activation") or {}).get("restart_required"):
        typer.echo("Activation: restart required (supervisor respawn)")


@runtime_app.command()
def drain(
    runtime_id: str = typer.Argument("", help="Draining runtime id. Defaults to latest draining runtime."),
    force: bool = typer.Option(False, "--force", help="Mark as legacy_position_manager instead of observing drain."),
):
    """Record drain progress for a previous runtime."""
    svc = _service()
    target = runtime_id or ((svc.status().get("draining") or {}).get("runtime_id") or "")
    if not target:
        typer.echo("No draining runtime available.")
        raise typer.Exit(1)
    try:
        record = svc.drain(target, actor="human-cli", cause="runtime_cli_drain", force=force)
    except RuntimeTransitionError as exc:
        _fail(exc)
    typer.echo(f"Draining: {record.get('runtime_id')} ({record.get('state')})")


@runtime_app.command()
def rollback():
    """Rollback to the latest pre-promote checkpoint."""
    try:
        payload = _service().rollback(actor="human-cli", cause="runtime_cli_rollback")
    except RuntimeTransitionError as exc:
        _fail(exc)
    current = payload.get("current") or {}
    typer.echo(f"Current: {current.get('runtime_id')} ({current.get('state')})")
    if (payload.get("activation") or {}).get("restart_required"):
        typer.echo("Activation: restart required (supervisor respawn)")


@runtime_app.command()
def events(limit: int = typer.Option(20, "--limit", "-n")):
    """Show recent runtime continuity events."""
    for event in _service().list_events(limit=limit):
        typer.echo(
            f"{event['created_at']} {event['event_type']} "
            f"{event.get('runtime_id') or '-'} {event.get('phase')}"
        )
