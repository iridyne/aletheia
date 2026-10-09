"""Go-Live Scorecard — quantitative readiness assessment for demo→live transition.

Aggregates 10 criteria into a composite score (0-100) with traffic light status.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Callable

logger = logging.getLogger("aletheia.analytics.go_live_scorecard")

from aletheia.analytics.outcome_integrity import (
    CompleteOutcomeCohort,
    read_complete_outcome_cohort,
)
from aletheia.analytics.learning_window import outcome_window_metadata


_compute_calibration_score: Callable[[], float | None] | None = None
_get_calibration_report: Callable[[], dict | None] | None = None


def configure_calibration_readers(*, score_reader=None, report_reader=None) -> None:
    """Bind optional Analytics readers; absence remains a zero-score blocker."""
    global _compute_calibration_score, _get_calibration_report
    _compute_calibration_score = score_reader
    _get_calibration_report = report_reader


def _scorecard_cohort() -> CompleteOutcomeCohort | None:
    """Read the selected external cohort completely; unresolved selection has none."""
    from aletheia.analytics.execution_mode import resolve_selected_execution_mode

    mode = resolve_selected_execution_mode()
    return read_complete_outcome_cohort(execution_mode=mode) if mode else None


def _scorecard_outcomes() -> list[dict]:
    """Eligible rows of the selected cohort, oldest close first."""
    cohort = _scorecard_cohort()
    if cohort is None:
        return []
    if cohort.cohort_state != "available":
        raise RuntimeError("go-live outcome cohort unavailable")
    return cohort.eligible_rows


def compute_scorecard(*, live_evidence: dict | None = None) -> dict:
    """Compute the go-live readiness scorecard. Pure read-only, no side effects.

    Every PnL criterion and the net-PnL gate read one complete cohort snapshot
    (#1262, #1968); a cohort that cannot be read is a blocker, never a partial
    sample scored as if it were the whole history.
    """
    cohort = _scorecard_cohort()
    pnls = _genuine_closed_pnls(cohort)
    criteria = [
        _check_trade_volume(pnls),
        _check_profit_factor(pnls),
        _check_win_rate(pnls),
        _check_max_drawdown(pnls),
        _check_exchange_connectivity(live_evidence),
        _check_risk_violations(),
        _check_data_coverage(),
        _check_model_health(),
        _check_reconciliation(),
        _check_calibration(),
    ]

    composite = sum(c["score"] * c["weight"] for c in criteria)
    if composite < 50:
        traffic_light = "red"
    elif composite <= 75:
        traffic_light = "yellow"
    else:
        traffic_light = "green"

    performance_gate = _check_positive_net_pnl(pnls)
    blockers = [c["detail"] for c in criteria if c["status"] == "fail"]
    if pnls is None:
        traffic_light = "red"
        blockers.append("Eligible outcome cohort is not available")
    if performance_gate["status"] == "fail":
        traffic_light = "red"
        blockers.append(performance_gate["detail"])

    return {
        "composite_score": round(composite, 1),
        "traffic_light": traffic_light,
        "performance_gate": performance_gate,
        "criteria": criteria,
        "blockers": blockers,
        "cohort_state": cohort.cohort_state if cohort is not None else "unavailable",
        "cohort_complete": cohort.cohort_complete if cohort is not None else False,
        "source_raw_event_count": cohort.source_raw_event_count if cohort is not None else None,
        "ordered_source_digest": cohort.ordered_source_digest if cohort is not None else None,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        **outcome_window_metadata(),
    }


def _genuine_closed_pnls(cohort: CompleteOutcomeCohort | None) -> list[float] | None:
    """realized_pnl of GENUINE closed positions, oldest close first.

    Reconciler orphan sweeps, maintenance closes, legacy rows and incomplete economics
    are not verified outcomes; counting them pollutes PF / win-rate / drawdown / volume.
    ``None`` means the cohort could not be read; an unresolved account has no outcomes.
    """
    if cohort is None:
        return []
    if cohort.cohort_state != "available":
        return None
    return [float(row["realized_pnl"]) for row in cohort.eligible_rows]


def _check_positive_net_pnl(pnls: list[float] | None) -> dict:
    """Return the non-weighted profitability gate for live readiness."""
    if not pnls:
        return {
            "status": "not_available",
            "eligible_trade_count": 0,
            "net_realized_pnl": None,
            "detail": "Eligible net realized PnL is not available",
        }

    net_pnl = round(sum(pnls), 8)
    passed = net_pnl > 0
    return {
        "status": "pass" if passed else "fail",
        "eligible_trade_count": len(pnls),
        "net_realized_pnl": net_pnl,
        "detail": (
            f"Eligible net realized PnL {net_pnl:.2f} USDT is positive"
            if passed
            else f"Eligible net realized PnL {net_pnl:.2f} USDT must be > 0"
        ),
    }


def _criterion(key: str, label: str, score: float, weight: float, detail: str) -> dict:
    if score >= 100:
        status = "pass"
    elif score <= 0:
        status = "fail"
    else:
        status = "warning"
    return {
        "key": key,
        "label": label,
        "score": min(100.0, max(0.0, round(score, 1))),
        "weight": weight,
        "weighted_contribution": round(min(100.0, max(0.0, score)) * weight, 1),
        "detail": detail,
        "status": status,
    }


def _check_trade_volume(pnls: list[float] | None) -> dict:
    try:
        if pnls is None:
            raise RuntimeError("outcome cohort unavailable")
        count = len(pnls)
        target = 50
        score = min(count / target, 1.0) * 100
        return _criterion("trade_volume", "Trade Volume (≥50 closed)", score, 0.135,
                          f"{count} trades (target ≥{target})")
    except Exception:
        return _criterion("trade_volume", "Trade Volume (≥50 closed)", 0, 0.135, "unavailable")


def count_consecutive_zero_fill_days() -> int:
    """Count consecutive UTC calendar days with zero new position opens (D8-S04 / product §8 K2).

    Walks backwards from today counting days with no new opens. Returns the integer
    day count. Three or more → trading loop is stalled (K2 kill condition).
    """
    try:
        from aletheia.storage.sqlite import get_db
        from aletheia.analytics.execution_mode import resolve_selected_execution_mode

        mode = resolve_selected_execution_mode()
        if not mode:
            return 0
        db = get_db()
        rows = db.execute(
            "SELECT DATE(opened_at) as open_date FROM positions "
            "WHERE execution_mode=? AND status IN ('open','closed') ORDER BY opened_at DESC LIMIT 500",
            (mode,),
        ).fetchall()
        open_dates = {r[0] for r in rows if r[0]}
        day = datetime.now(timezone.utc).date()
        consecutive = 0
        for _ in range(30):  # check at most 30 days back
            if day.isoformat() in open_dates:
                break
            consecutive += 1
            day -= timedelta(days=1)
        return consecutive
    except Exception as exc:
        logger.warning("go_live_scorecard: zero-fill-day probe unavailable (%s)", type(exc).__name__)
        return 0


def check_kill_condition_consecutive_rounds(window_days: int = 14) -> dict:
    """Detect consecutive experiment rounds where PF < 0.5 (D8-S02 / product §8 K1).

    Splits closed positions into 14-day windows (one per experiment round) and
    counts how many consecutive trailing windows have PF below the kill floor.
    Returns {"consecutive_kill_rounds": int, "kill_threshold": 0.5, "window_days": int}.
    Two consecutive kill rounds → recommend terminating the experiment.
    """
    try:
        rows = _scorecard_outcomes()
        if not rows:
            return {"consecutive_kill_rounds": 0, "kill_threshold": 0.5, "window_days": window_days}

        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        kill_floor = 0.5
        consecutive = 0

        for i in range(8):  # check up to 8 trailing windows
            start = now - timedelta(days=window_days * (i + 1))
            end = now - timedelta(days=window_days * i)
            window_rows = [
                row for row in rows
                if start.isoformat()[:10] <= str(row.get("closed_at"))[:10] < end.isoformat()[:10]
            ]
            if not window_rows:
                break  # no data → stop counting
            gp = sum(float(row["realized_pnl"]) for row in window_rows if float(row["realized_pnl"]) > 0)
            gl = sum(abs(float(row["realized_pnl"])) for row in window_rows if float(row["realized_pnl"]) < 0)
            pf = gp / gl if gl > 0 else 99.0
            if pf < kill_floor:
                consecutive += 1
            else:
                break  # streak broken

        return {"consecutive_kill_rounds": consecutive, "kill_threshold": kill_floor, "window_days": window_days}
    except Exception:
        # Failure here silently returns `consecutive_kill_rounds=0`,
        # which erases any kill-condition signal downstream. That is
        # also a scorecard-policy question filed as followup; log so
        # the underlying result-read / field / numeric-conversion
        # defect is visible.
        logger.warning(
            "go_live_scorecard: kill-round tally failed "
            "(defaulting consecutive_kill_rounds=0)",
            exc_info=True,
        )
        return {"consecutive_kill_rounds": 0, "kill_threshold": 0.5, "window_days": window_days}


def compute_pnl_by_trade_school() -> dict:
    """Return profit_factor and win_rate grouped by trade_school (D2-S03).

    Enables serenity vs main-track PnL isolation without mixing the two pools.
    Returns {trade_school: {"profit_factor": float, "win_rate": float, "count": int}}.
    """
    try:
        rows = _scorecard_outcomes()
        result: dict = {}
        from collections import defaultdict
        buckets: dict = defaultdict(list)
        for row in rows:
            buckets[row.get("trade_school") or "short_term"].append(
                float(row["realized_pnl"])
            )
        for school, pnls in buckets.items():
            gross_profit = sum(p for p in pnls if p > 0)
            gross_loss = sum(abs(p) for p in pnls if p < 0)
            wins = sum(1 for p in pnls if p > 0)
            pf = gross_profit / gross_loss if gross_loss > 0 else 99.0
            wr = wins / len(pnls) if pnls else 0.0
            result[school] = {"profit_factor": round(pf, 3), "win_rate": round(wr, 3), "count": len(pnls)}
        return result
    except Exception as exc:
        logger.warning("go_live_scorecard: PnL-by-school aggregation unavailable (%s)", type(exc).__name__)
        return {}


def _check_profit_factor(pnls: list[float] | None) -> dict:
    try:
        if pnls is None:
            raise RuntimeError("outcome cohort unavailable")
        if not pnls:
            return _criterion("profit_factor", "Profit Factor (≥1.2)", 0, 0.18, "no trades")
        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss = sum(abs(p) for p in pnls if p < 0)
        pf = gross_profit / gross_loss if gross_loss > 0 else 99.0
        # Kill-floor: PF < 0.5 is a hard stop regardless of go-live target (product §8 K1)
        if pf < 0.5:
            return _criterion("profit_factor", "Profit Factor (≥1.2)", 0, 0.18,
                              f"PF {pf:.2f} — below kill threshold 0.5")
        target = 1.2
        score = min(pf / target, 1.0) * 100 if pf < target else 100.0
        return _criterion("profit_factor", "Profit Factor (≥1.2)", score, 0.18,
                          f"PF {pf:.2f} (target ≥{target})")
    except Exception:
        return _criterion("profit_factor", "Profit Factor (≥1.2)", 0, 0.18, "unavailable")


def _check_win_rate(pnls: list[float] | None) -> dict:
    try:
        if pnls is None:
            raise RuntimeError("outcome cohort unavailable")
        if not pnls:
            return _criterion("win_rate", "Win Rate (≥45%)", 0, 0.135, "no trades")
        wins = sum(1 for p in pnls if p > 0)
        total = len(pnls)
        wr = wins / total * 100
        target = 45.0
        score = min(wr / target, 1.0) * 100 if wr < target else 100.0
        return _criterion("win_rate", "Win Rate (≥45%)", score, 0.135,
                          f"{wr:.1f}% (target ≥{target}%)")
    except Exception:
        return _criterion("win_rate", "Win Rate (≥45%)", 0, 0.135, "unavailable")


def _check_max_drawdown(pnls: list[float] | None) -> dict:
    try:
        if pnls is None:
            raise RuntimeError("outcome cohort unavailable")
        if not pnls:
            return _criterion("max_drawdown", "Max Drawdown (<10%)", 100, 0.135, "no trades")
        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in pnls:
            cum += p
            if cum > peak:
                peak = cum
            dd = peak - cum
            if dd > max_dd:
                max_dd = dd
        dd_pct = (
            max_dd / peak * 100
            if peak > 0
            else (100.0 if max_dd > 0 else 0.0)
        )
        target = 10.0
        score = 100.0 if dd_pct <= target else max(0, (1 - (dd_pct - target) / target) * 100)
        return _criterion("max_drawdown", "Max Drawdown (<10%)", score, 0.135,
                          f"{dd_pct:.1f}% drawdown (target <{target}%)")
    except Exception:
        return _criterion("max_drawdown", "Max Drawdown (<10%)", 0, 0.135, "unavailable")


def _check_exchange_connectivity(live_evidence: dict | None = None) -> dict:
    """Check the exact configured broker, never the legacy Bitget-only cache."""
    try:
        from aletheia.config.loader import load_config
        cfg = load_config()
        broker = str(getattr(cfg.exchange, "name", "") or "").lower()
        if str(getattr(cfg.exchange, "mode", "") or "").lower() == "live":
            from aletheia.trust.live_unlock import evidence_matches_config

            evidence = live_evidence or getattr(cfg.live, "unlock_evidence", None)
            valid, reason = evidence_matches_config(cfg, evidence)
            if not valid:
                return _criterion(
                    "exchange_ok", "Selected Live Account Connected", 0, 0.09,
                    f"selected {broker or 'not available'} live account is not verified ({reason})",
                )
            return _criterion(
                "exchange_ok", "Selected Live Account Connected", 100, 0.09,
                f"{broker} live private account authenticated; evidence is fresh",
            )

        from aletheia.analytics.execution_mode import resolve_selected_execution_mode

        selected_mode = resolve_selected_execution_mode()
        if not selected_mode:
            return _criterion("exchange_ok", "Exchange Connected", 0, 0.09, "execution account unavailable")

        from aletheia.portfolio.exchange_sync import get_unified_cached_state, status_for

        state = get_unified_cached_state()
        connected = set(state.get("connected_brokers") or []) if state else set()
        if state and not connected:
            connected = {
                str(account.get("broker_id") or "")
                for account in (state.get("accounts") or [])
            }
        if status_for(state, max_age_sec=300) == "ok" and selected_mode in connected:
            return _criterion(
                "exchange_ok", "Exchange Connected", 100, 0.09,
                f"{selected_mode} connected",
            )
        return _criterion(
            "exchange_ok", "Exchange Connected", 0, 0.09,
            f"selected {selected_mode or 'not available'} account is stale, missing, or not connected",
        )
    except Exception:
        return _criterion("exchange_ok", "Exchange Connected", 0, 0.09, "unavailable")


def _check_risk_violations() -> dict:
    try:
        from aletheia.storage.sqlite import get_db
        import json
        db = get_db()
        from aletheia.analytics.execution_mode import resolve_selected_execution_mode

        mode = resolve_selected_execution_mode()
        if not mode:
            return _criterion("risk_clean", "Zero Risk Violations (24h)", 0, 0.09, "execution account unavailable")
        cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        # The target is the whole 24h window, not its newest rows (#1696).
        rows = db.execute(
            "SELECT violations FROM risk_decisions WHERE created_at >= ? "
            "AND proposal_execution_mode=?",
            (cutoff, mode),
        ).fetchall()
        violation_count = 0
        for r in rows:
            v = r[0]
            if v and v != "[]":
                parsed = json.loads(v) if isinstance(v, str) else v
                if parsed:
                    violation_count += 1
        score = 100.0 if violation_count == 0 else 0.0
        return _criterion("risk_clean", "Zero Risk Violations (24h)", score, 0.09,
                          f"{violation_count} violation events" if violation_count else "clean")
    except Exception:
        # The 50-point fallback here is a fail-open scorecard behaviour
        # that inflates the go-live composite when this criterion cannot
        # be verified. Whether "cannot verify" should score 50 (or 0, or
        # abstain) is a scorecard-policy question the owner has not yet
        # settled — filed as followup. This log makes the underlying DB
        # or schema failure visible so the 50 is not silent.
        logger.warning(
            "go_live_scorecard: risk_clean criterion evaluation failed "
            "(defaulting score to 50/100 'cannot verify')",
            exc_info=True,
        )
        return _criterion("risk_clean", "Zero Risk Violations (24h)", 50, 0.09, "cannot verify")


def _check_data_coverage() -> dict:
    try:
        from aletheia.agent_shell.trading_context import _get_data_health_summary
        health = _get_data_health_summary()
        if health.get("ohlcv_ready"):
            return _criterion("data_coverage", "Data Coverage Complete", 100, 0.045, "all symbols covered")
        ready = health.get("members_with_full_coverage", 0)
        total = max(health.get("total_members", 1), 1)
        score = ready / total * 100
        return _criterion("data_coverage", "Data Coverage Complete", score, 0.045,
                          f"{ready}/{total} symbols have full OHLCV")
    except Exception:
        return _criterion("data_coverage", "Data Coverage Complete", 0, 0.045, "unavailable")


def _check_model_health() -> dict:
    try:
        from aletheia.model_provider.fallback import get_model_health_snapshot
        # One record per provider/model; available = not cooling and its latest
        # call succeeded. No record means no provider has been proven up (#1698).
        available = [
            record for record in get_model_health_snapshot()
            if not record["cooling_down"]
            and record["last_success_at"] is not None
            and (
                record["last_failure_at"] is None
                or record["last_success_at"] > record["last_failure_at"]
            )
        ]
        if available:
            return _criterion("model_health", "LLM Model Available", 100, 0.045, "at least one provider up")
        return _criterion("model_health", "LLM Model Available", 0, 0.045, "no providers available")
    except Exception:
        return _criterion("model_health", "LLM Model Available", 50, 0.045, "cannot verify")


# Audit facts the reconciliation paths write when they cannot prove exchange state.
_RECONCILIATION_ERROR_EVENTS = (
    "order_reconciliation_inconclusive",
    "proposal_execution_reconciliation_required",
    "pending_close_reconciliation_required",
)


def _check_reconciliation() -> dict:
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        placeholders = ", ".join("?" for _ in _RECONCILIATION_ERROR_EVENTS)
        errors = db.execute(
            "SELECT COUNT(*) FROM audit_events "
            f"WHERE event_type IN ({placeholders}) AND timestamp >= ?",
            (*_RECONCILIATION_ERROR_EVENTS, cutoff),
        ).fetchone()[0]
        # An active integrity incident is unresolved whenever it was detected.
        incidents = db.execute(
            "SELECT COUNT(*) FROM reconciliation_integrity_incidents WHERE status = 'active'"
        ).fetchone()[0]
        score = 100.0 if errors == 0 and incidents == 0 else 0.0
        return _criterion(
            "reconciliation", "No Reconciliation Errors (7d)", score, 0.045,
            f"{errors} errors in 7 days, {incidents} active integrity incidents"
            if errors or incidents else "clean",
        )
    except Exception:
        # Same fail-open scorecard 50/100 fallback as `risk_clean`
        # above; log so the underlying `audit_events` query failure or
        # schema drift is visible.
        logger.warning(
            "go_live_scorecard: reconciliation criterion evaluation failed "
            "(defaulting score to 50/100 'cannot verify')",
            exc_info=True,
        )
        return _criterion("reconciliation", "No Reconciliation Errors (7d)", 50, 0.045, "cannot verify")


def _check_calibration() -> dict:
    try:
        if _compute_calibration_score is None or _get_calibration_report is None:
            return _criterion("calibration", "Conviction Calibration (ECE<0.10)", 0, 0.10, "unavailable")
        score = _compute_calibration_score()
        report = _get_calibration_report()
        if not report:
            return _criterion(
                "calibration", "Conviction Calibration (ECE<0.10)",
                0, 0.10, "insufficient current-semantics data",
            )
        else:
            total = report.get("total_executed", 0)
            ece = report.get("ece")
            grade = report.get("ece_grade", "?")
            detail = (
                f"ECE={ece:.3f} ({grade}), {total} trades"
                if ece is not None and score is not None
                else f"calibration unavailable ({total} trades)"
            )
        return _criterion(
            "calibration", "Conviction Calibration (ECE<0.10)",
            float(score) if score is not None else 0.0, 0.10, detail,
        )
    except Exception:
        return _criterion("calibration", "Conviction Calibration (ECE<0.10)", 0, 0.10, "unavailable")
