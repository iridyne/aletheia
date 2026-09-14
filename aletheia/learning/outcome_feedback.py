"""Optional research feedback; no Position, settlement or job lifecycle writes."""

from datetime import datetime, timezone

from aletheia.portfolio.outcome_hooks import SHADOW_TRADES_HOOK, SERENITY_POST_MORTEM_HOOK


def execute_outcome_feedback(hook_type: str, position: dict) -> None:
    if hook_type == SHADOW_TRADES_HOOK:
        from aletheia.counterfactual.trades import (
            resolve_shadow_trades_for_position,
        )

        resolve_shadow_trades_for_position(position)
        return
    if hook_type == SERENITY_POST_MORTEM_HOOK:
        realized = position.get("realized_pnl")
        if realized is None:
            raise ValueError("Serenity feedback requires realized economics")
        _close_serenity_feedback(
            position,
            float(realized),
            str(position.get("close_reason") or "verified_exit"),
        )
        return
    raise ValueError(f"unsupported Outcome hook type: {hook_type}")


def _close_serenity_feedback(position: dict, realized_pnl: float, close_reason: str) -> None:
    """Write SerenityPostMortem and update opportunity status when a serenity position closes.

    This closes the attribution loop: position close → PostMortem → opportunity status.
    The dreaming_agent reads PostMortems for pattern synthesis; trade_attribution joins
    opportunity_id back to serenity_opportunities. Without this hook both are severed.
    """
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import SerenityPostMortem

    opp_id = position.get("opportunity_id")
    if not opp_id:
        return

    outcome = "win" if realized_pnl > 0 else "loss"
    entry = position.get("entry_price") or 0
    exit_price = position.get("current_price") or entry
    notional = entry * (position.get("quantity") or 0)
    pnl_pct = (realized_pnl / notional * 100) if notional else 0.0

    failure_bucket = None
    if outcome == "loss":
        if close_reason == "stop_loss":
            failure_bucket = "stop_hit"
        elif close_reason == "thesis_invalidated":
            failure_bucket = "thesis_invalidated"
        elif close_reason == "max_hold":
            failure_bucket = "time_decay"
        else:
            failure_bucket = close_reason or "unknown"

    pm = SerenityPostMortem(
        position_id=str(position.get("id") or "") or None,
        opportunity_id=opp_id,
        outcome=outcome,
        failure_bucket=failure_bucket,
        score_delta=None,
        attribution=(
            f"trade_school=serenity close_reason={close_reason} "
            f"pnl={realized_pnl:.2f} pnl_pct={pnl_pct:.1f}% "
            f"hold_h={_compute_holding_hours(position.get('opened_at'), position.get('closed_at')) or 0:.0f}"
        ),
        summary=(
            f"{position.get('symbol')} {position.get('direction')} "
            f"{'profit' if realized_pnl > 0 else 'loss'} {pnl_pct:+.1f}% "
            f"closed by {close_reason}"
        ),
        created_by="system_auto_close",
    )
    repo.insert_serenity_post_mortem(pm)

    repo.update_serenity_opportunity(
        opp_id,
        status="traded_closed",
        updated_at=datetime.now(timezone.utc).isoformat(),
    )



def _compute_holding_hours(opened_at: str | None, closed_at: str | None) -> float | None:
    """Compute holding duration in hours between two ISO timestamps."""
    if not opened_at or not closed_at:
        return None
    try:
        from datetime import datetime as _dt

        fmt_candidates = [
            "%Y-%m-%dT%H:%M:%S.%f+00:00",
            "%Y-%m-%dT%H:%M:%S+00:00",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
        ]
        t_open = t_close = None
        for fmt in fmt_candidates:
            try:
                t_open = _dt.strptime(opened_at.replace("Z", "+00:00").split("+")[0], fmt.split("+")[0])
                break
            except ValueError:
                continue
        for fmt in fmt_candidates:
            try:
                t_close = _dt.strptime(closed_at.replace("Z", "+00:00").split("+")[0], fmt.split("+")[0])
                break
            except ValueError:
                continue
        if t_open and t_close:
            return (t_close - t_open).total_seconds() / 3600.0
    except Exception:
        pass
    return None

