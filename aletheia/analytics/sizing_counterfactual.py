"""Read-only paired sizing counterfactuals over verified realized closes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import mean, median
from typing import Iterable


POLICY_VERSION = "sizing-counterfactual/v1"


def build_sizing_counterfactual(
    *,
    days: int = 90,
    min_sample_size: int = 10,
    rows: Iterable[dict] | None = None,
) -> dict:
    """Compare sizing rules on the same cost-complete realized trades.

    The report changes notional only. It assumes returns and costs scale linearly
    and therefore does not claim price-impact, liquidation, or portfolio-capacity
    effects. It never changes a decision or writes runtime state.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).isoformat()
    if rows is None:
        from aletheia.analytics.outcome_integrity import eligible_strategy_outcomes

        source_rows = eligible_strategy_outcomes(closed_after=cutoff, limit=1000)
        proposal_input_status = _attach_immutable_proposal_inputs(source_rows)
    else:
        source_rows = [dict(row) for row in rows]
        proposal_input_status = {"status": "provided_rows"}

    trades = [_trade(row) for row in source_rows]
    trades = [trade for trade in trades if trade is not None]
    stop_trades = [trade for trade in trades if trade["stop_distance"] is not None]
    report = {
        "policy_version": POLICY_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window": {"days": max(1, days), "cutoff": cutoff},
        "status": "available" if len(trades) >= min_sample_size else "insufficient_data",
        "sample_size": len(trades),
        "minimum_sample_size": min_sample_size,
        "stop_eligible_sample_size": len(stop_trades),
        "proposal_input_status": proposal_input_status,
        "policies": {},
        "conviction_buckets": _conviction_buckets(trades),
        "assumptions": [
            "same selected trades and realized return path",
            "fees and funding scale linearly with notional",
            "no price impact, liquidation, capacity, or cross-trade interference",
            "descriptive paired evidence only; no sizing authority",
        ],
    }
    if not trades:
        report["reason"] = "no_cost_complete_realized_closes"
        return report

    actual_notionals = [trade["actual_notional"] for trade in trades]
    equal_notional = median(actual_notionals)
    report["policies"]["actual"] = _metrics(trades, actual_notionals)
    report["policies"]["equal_notional"] = _metrics(trades, [equal_notional] * len(trades))

    if stop_trades:
        stop_actual = [trade["actual_notional"] for trade in stop_trades]
        target_median = median(stop_actual)
        raw = [1.0 / trade["stop_distance"] for trade in stop_trades]
        scale = target_median / median(raw)
        observed_cap = max(stop_actual)
        stop_notionals = [min(observed_cap, value * scale) for value in raw]
        report["stop_paired_policies"] = {
            "normalization": {
                "target_median_notional_usdt": round(target_median, 8),
                "maximum_observed_notional_usdt": round(observed_cap, 8),
                "rule": "notional proportional to 1/stop_distance, median-matched, capped at observed max",
            },
            "actual": _metrics(stop_trades, stop_actual),
            "risk_at_stop_equalized": _metrics(stop_trades, stop_notionals),
        }
    else:
        report["stop_paired_policies"] = {
            "status": "insufficient_data",
            "reason": proposal_input_status.get("reason", "no_valid_persisted_stop_distance"),
        }
    return report


def render_sizing_counterfactual(report: dict) -> str:
    lines = [
        "Sizing Counterfactual",
        "",
        f"Status: {report.get('status', 'unavailable')}",
        f"Eligible closes: {report.get('sample_size', 0)}",
        f"Stop-eligible closes: {report.get('stop_eligible_sample_size', 0)}",
        f"Proposal inputs: {(report.get('proposal_input_status') or {}).get('status', 'unavailable')}",
    ]
    for name, metrics in (report.get("policies") or {}).items():
        lines.append(
            f"{name}: pnl={_fmt(metrics.get('total_pnl_usdt'))}U "
            f"max_dd={_fmt(metrics.get('max_drawdown_usdt'))}U "
            f"median_notional={_fmt(metrics.get('median_notional_usdt'))}U"
        )
    paired = report.get("stop_paired_policies") or {}
    for name in ("actual", "risk_at_stop_equalized"):
        metrics = paired.get(name)
        if metrics:
            lines.append(
                f"stop_paired.{name}: pnl={_fmt(metrics.get('total_pnl_usdt'))}U "
                f"max_dd={_fmt(metrics.get('max_drawdown_usdt'))}U"
            )
    lines.extend(["", "Descriptive only: no sizing authority or runtime mutation."])
    return "\n".join(lines)


def _trade(row: dict) -> dict | None:
    try:
        entry = float(row.get("entry_price") or 0)
        quantity = float(row.get("quantity") or 0)
        pnl = float(row["realized_pnl"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    notional = entry * quantity
    if entry <= 0 or quantity <= 0 or notional <= 0:
        return None
    stop_distance = None
    try:
        stop = float(row.get("entry_stop_price", row.get("stop_price")) or 0)
        if stop > 0 and stop != entry:
            stop_distance = abs(entry - stop) / entry
    except (TypeError, ValueError, OverflowError):
        pass
    conviction = row.get("conviction")
    try:
        conviction = int(conviction) if conviction is not None else None
    except (TypeError, ValueError, OverflowError):
        conviction = None
    return {
        "id": row.get("position_id") or row.get("id"),
        "actual_notional": notional,
        "return_fraction": pnl / notional,
        "stop_distance": stop_distance,
        "conviction": conviction,
    }


def _metrics(trades: list[dict], notionals: list[float]) -> dict:
    pnls = [trade["return_fraction"] * notional for trade, notional in zip(trades, notionals)]
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for pnl in pnls:
        cumulative += pnl
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    return {
        "sample_size": len(trades),
        "total_pnl_usdt": round(sum(pnls), 8),
        "mean_pnl_usdt": round(mean(pnls), 8),
        "worst_trade_usdt": round(min(pnls), 8),
        "max_drawdown_usdt": round(max_drawdown, 8),
        "total_notional_usdt": round(sum(notionals), 8),
        "median_notional_usdt": round(median(notionals), 8),
    }


def _conviction_buckets(trades: list[dict]) -> list[dict]:
    buckets = ((55, 64), (65, 74), (75, 84), (85, 100))
    result = []
    for low, high in buckets:
        subset = [trade for trade in trades if trade["conviction"] is not None and low <= trade["conviction"] <= high]
        returns = [trade["return_fraction"] * 100.0 for trade in subset]
        result.append(
            {
                "range": f"{low}-{high}",
                "sample_size": len(subset),
                "mean_net_return_pct": round(mean(returns), 8) if returns else None,
                "win_rate": round(sum(value > 0 for value in returns) / len(returns), 8) if returns else None,
                "mean_actual_notional_usdt": round(mean(trade["actual_notional"] for trade in subset), 8)
                if subset
                else None,
            }
        )
    return result


def _attach_immutable_proposal_inputs(rows: list[dict]) -> dict:
    """Attach entry stop and conviction without trusting mutable Position fields."""
    proposal_ids = sorted({str(row.get("proposal_id")) for row in rows if row.get("proposal_id")})
    if not proposal_ids:
        return {"status": "not_applicable", "proposal_count": 0}
    try:
        from aletheia.storage.sqlite import get_db

        db = get_db()
        inputs = {}
        for start in range(0, len(proposal_ids), 400):
            batch = proposal_ids[start : start + 400]
            placeholders = ",".join("?" for _ in batch)
            found = db.execute(
                "SELECT tp.id AS proposal_id, tp.stop_price, "
                "(SELECT pd.conviction FROM pi_decisions pd "
                " WHERE pd.proposal_id = tp.id "
                " ORDER BY datetime(pd.created_at) DESC LIMIT 1) AS conviction "
                f"FROM trade_proposals tp WHERE tp.id IN ({placeholders})",
                batch,
            ).fetchall()
            inputs.update({str(item["proposal_id"]): dict(item) for item in found})
        for row in rows:
            item = inputs.get(str(row.get("proposal_id")))
            if not item:
                continue
            row["entry_stop_price"] = item.get("stop_price")
            row["conviction"] = item.get("conviction")
        return {
            "status": "available",
            "proposal_count": len(proposal_ids),
            "matched_proposal_count": len(inputs),
        }
    except Exception:
        # Outcome reporting remains available; missing immutable inputs only remove
        # stop/conviction-specific diagnostics and never become inferred values.
        return {
            "status": "unavailable",
            "reason": "proposal_input_read_failed",
            "proposal_count": len(proposal_ids),
        }


def _fmt(value) -> str:
    return "N/A" if value is None else f"{float(value):.2f}"
