"""Trading probes for doctor reports."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

__all__ = [
    "_probe_candidate_planning",
    "_probe_concentration",
    "_probe_execution_liveness",
    "_probe_filter_evidence",
    "_probe_health",
    "_probe_knowledge_flood",
    "_probe_learning_retrieval",
    "_probe_operator_shell",
    "_probe_outcome_economics",
    "_probe_outcome_history_integrity",
    "_probe_persona_rollout",
    "_probe_pi_daemon",
    "_probe_reconcile_thrash",
    "_probe_reconciled_ratio",
    "_probe_round1",
    "_probe_signal_effectiveness",
    "_probe_sizing_policy",
    "_probe_skip_halt",
    "_probe_startup_behavior_gate",
    "_probe_trading_loop",
    "_render_daemon_report",
    "_safe_candidate_planning_diagnostic",
]


def _safe_candidate_planning_diagnostic() -> dict:
    try:
        from aletheia.storage import repositories as repo

        return {
            "status": "available",
            "summary": repo.get_entry_workbench_summary(),
            "candidates": repo.list_entry_workbench_candidates(),
            "refresh_assets": repo.list_candidate_planning_refresh_assets(),
        }
    except Exception as exc:
        return {"status": "unavailable", "reason": str(exc)}


def _probe_candidate_planning(report: Report) -> None:
    """Report canonical planning state without inferring market-data health."""
    state = _safe_candidate_planning_diagnostic()
    if state["status"] == "unavailable":
        report.add("data.candidate_planning", "info",
                   f"planning unavailable: {state["reason"]}")
        return
    summary = state["summary"]
    blocked = [row for row in state["candidates"] if row.get("status") == "unavailable"]
    detail = (f"needs_planning={summary["needs_planning"]}; "
              f"active_entry_plans={summary["active_entry_plans"]}; "
              f"refresh_assets={len(state["refresh_assets"])}")
    if blocked:
        detail += "; " + ", ".join(
            f"{row["venue"]}:{row["symbol"]}:{row["market_type"]}"
            f"[{row.get("outcome_reason") or "unavailable"}]"
            for row in blocked[:3]
        )
    report.add("data.candidate_planning", "warn" if blocked else "info", detail,
               "inspect canonical planning evidence; do not relax exact identity or freshness")


def _probe_trading_loop(report: Report) -> None:
    """The 'is the closed loop actually turning' probe — counts, not narrative."""
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()

        def _count(table: str) -> int:
            try:
                return int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            except Exception:
                return -1

        orders = _count("orders")
        fills = _count("fills")
        opps = _count("opportunities")
        ki = _count("knowledge_items")
        try:
            open_pos = int(db.execute("SELECT COUNT(*) FROM positions WHERE status='open'").fetchone()[0])
            closed_pos = int(db.execute("SELECT COUNT(*) FROM positions WHERE status='closed'").fetchone()[0])
        except Exception:
            open_pos = closed_pos = -1

        # Age of newest opportunity — the liveness signal for the observe stage.
        newest_opp = None
        try:
            row = db.execute("SELECT created_at FROM opportunities ORDER BY created_at DESC LIMIT 1").fetchone()
            newest_opp = row[0] if row else None
        except Exception:
            logger.warning("doctor: newest-opportunity probe unavailable", exc_info=True)

        # Session uptime (display only). The market_observer warmup signal this used
        # to carry is gone: see the retired-observe-path note below.
        session_uptime = None
        try:
            session_row = db.execute(
                "SELECT started_at FROM runtime_sessions WHERE ended_at IS NULL"
                " ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            if session_row:
                session_uptime = _age_minutes(session_row[0])
        except Exception:
            logger.warning("doctor: session-uptime probe unavailable", exc_info=True)
    except Exception as e:
        report.add_probe_error("loop.activity", e, "could not read trading tables")
        return

    opp_age = _age_minutes(newest_opp)
    report.add("loop.activity", "info",
               f"orders={orders} fills={fills} positions(open={open_pos},closed={closed_pos}) "
               f"opportunities={opps} knowledge={ki}")
    # The Universe Opportunity observe path was retired with its deterministic
    # emitter (#2945); market_observer is now enrichment-only and its admitted
    # workset is structurally empty (#2990). This table therefore stops advancing
    # by design, so its age is historical evidence, not a live-loop failure, and
    # there is no "observer should fire" remedy to offer. Current entry liveness
    # belongs to data.candidate_planning, exactly as _probe_skip_halt already
    # documents for the retired signal_pending evidence.
    if opp_age is None:
        report.add("loop.newest_opportunity", "info", "no opportunities recorded")
    else:
        uptime_str = (
            f", session {_fmt_age(session_uptime)} old" if session_uptime is not None else ""
        )
        report.add(
            "loop.newest_opportunity",
            "info",
            f"newest opportunity {_fmt_age(opp_age)}{uptime_str} — retired observe path, "
            "historical evidence only",
            "current entry liveness is data.candidate_planning",
        )


def _probe_skip_halt(report: Report) -> None:
    """Read retired entry evidence, never current Candidate/Plan health.

    Preserve historical trace, outcome and heartbeat diagnostics. Current entry
    liveness belongs to the canonical workbench and _probe_candidate_planning.
    """
    try:
        from aletheia.analytics.skip_diagnosis import diagnose_skip_halt
        from aletheia.storage.sqlite import get_db

        db = get_db()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        row = db.execute(
            """SELECT
                 COUNT(*) AS candidate_skips,
                 SUM(CASE WHEN NOT EXISTS (
                     SELECT 1 FROM filter_decisions fd
                     WHERE fd.candidate_key = 'pi_decision:' || p.id
                       AND fd.filter_name = 'operator_action_assessment'
                 ) THEN 1 ELSE 0 END) AS missing_trace,
                 SUM(CASE WHEN (
                     SELECT COUNT(DISTINCT fco.horizon)
                     FROM filter_candidate_outcomes fco
                     WHERE fco.outcome_candidate_key = 'pi_decision:' || p.id
                 ) < 4 THEN 1 ELSE 0 END) AS missing_outcome_seed
               FROM pi_decisions p
               WHERE p.action = 'skip'
                 AND p.opportunity_id IS NOT NULL
                 AND COALESCE(p.signal_source, 'market_observer') != 'system_cascade'
                 AND julianday(p.created_at) >= julianday(?)""",
            (cutoff,),
        ).fetchone()
        administrative_skips = db.execute(
            """SELECT COUNT(*) FROM pi_decisions
               WHERE action='skip' AND opportunity_id IS NOT NULL
                 AND signal_source='system_cascade'
                 AND julianday(created_at) >= julianday(?)""",
            (cutoff,),
        ).fetchone()[0]
        unscoped = db.execute(
            """SELECT COUNT(*) FROM pi_decisions
               WHERE action = 'skip' AND opportunity_id IS NULL
                 AND julianday(created_at) >= julianday(?)""",
            (cutoff,),
        ).fetchone()[0]
        no_candidate_events = db.execute(
            """SELECT COUNT(*) FROM operator_evaluation_events
               WHERE event_kind = 'no_evaluable_candidate'
                 AND julianday(created_at) >= julianday(?)""",
            (cutoff,),
        ).fetchone()[0]
        deferred_context_events = db.execute(
            """SELECT COUNT(*) FROM operator_evaluation_events
               WHERE event_kind LIKE 'operator_decision_deferred_%'
                 AND julianday(created_at) >= julianday(?)""",
            (cutoff,),
        ).fetchone()[0]
        path_counts = db.execute(
            """SELECT
                 SUM(CASE WHEN fd.reason_code='operator_model_skip' THEN 1 ELSE 0 END)
                   AS direct_model_skips,
                 SUM(CASE WHEN fd.reason_code='operator_model_trade' THEN 1 ELSE 0 END)
                   AS trade_intent_gated_skips,
                 SUM(CASE WHEN fd.reason_code='operator_model_skip'
                           AND COALESCE(json_extract(fd.input_snapshot,
                               '$.confirmation_attempts'), 0) > 0
                          THEN 1 ELSE 0 END) AS confirmed_adverse_skips
               FROM filter_decisions fd
               JOIN pi_decisions p
                 ON fd.candidate_key='pi_decision:' || p.id
               WHERE fd.filter_name='operator_action_assessment'
                 AND p.action='skip'
                 AND p.opportunity_id IS NOT NULL
                 AND COALESCE(p.signal_source, 'market_observer') != 'system_cascade'
                 AND julianday(p.created_at) >= julianday(?)""",
            (cutoff,),
        ).fetchone()
        pending = db.execute(
            """SELECT COUNT(*) FROM pi_decisions
               WHERE action = 'signal_pending'
                 AND julianday(created_at) >= julianday(?)""",
            (cutoff,),
        ).fetchone()[0]
        # A reload can leave pre-remediation decisions inside the rolling 24h
        # window.  Keep those raw counts visible, but judge the current B1
        # behavior only from the active, versioned remediation epoch.  This is
        # a read-only reporting boundary; it never creates/rotates an epoch.
        epoch_row = db.execute(
            """SELECT epoch_id FROM learning_policy_epochs
               WHERE epoch_kind='system_operator_skip_remediation'
                 AND ended_at IS NULL
               ORDER BY started_at DESC LIMIT 1"""
        ).fetchone()
        policy_epoch_id = str(epoch_row[0]) if epoch_row is not None else None
        proposals = db.execute(
            "SELECT COUNT(*) FROM trade_proposals "
            "WHERE julianday(created_at) >= julianday(?)",
            (cutoff,),
        ).fetchone()[0]
    except Exception as exc:
        report.add_probe_error("loop.skip_halt", exc)
        return

    candidate_skips = int((row or [0])[0] or 0)
    missing_trace = int((row or [0, 0])[1] or 0)
    missing_outcome_seed = int((row or [0, 0, 0])[2] or 0)
    direct_model_skips = int((path_counts or [0])[0] or 0)
    trade_intent_gated_skips = int((path_counts or [0, 0])[1] or 0)
    confirmed_adverse_skips = int((path_counts or [0, 0, 0])[2] or 0)
    diagnosis = diagnose_skip_halt(policy_epoch_id=policy_epoch_id)
    scoped_counts = diagnosis.counts
    epoch_label = policy_epoch_id or "none"
    detail = (
        f"raw_24h_candidate_skips={candidate_skips}, raw_24h_missing_trace={missing_trace}, "
        f"raw_24h_missing_outcome_seed={missing_outcome_seed}, "
        f"raw_24h_direct_model_skips={direct_model_skips}, "
        f"raw_24h_trade_intent_gated_skips={trade_intent_gated_skips}, "
        f"raw_24h_confirmed_adverse_skips={confirmed_adverse_skips}, "
        f"administrative_cascade_skips_excluded={int(administrative_skips or 0)}, "
        f"unscoped_heartbeat_skips={int(unscoped or 0)}, "
        f"no_evaluable_candidate_events={int(no_candidate_events or 0)}, "
        f"deferred_unchanged_context_events={int(deferred_context_events or 0)}, "
        f"raw_24h_pending_signals={int(pending or 0)}, proposals={int(proposals or 0)}, "
        f"diagnosis_epoch={epoch_label}, "
        f"current_epoch_candidate_skips={int(scoped_counts.get('candidate_skips', 0))}, "
        f"mature_epoch_candidate_skips={int(scoped_counts.get('mature_candidate_skips', 0))}, "
        f"immature_epoch_candidate_skips={int(scoped_counts.get('immature_candidate_skips', 0))}, "
        f"current_epoch_pending_signals={int(scoped_counts.get('pending_signals', 0))}, "
        f"current_epoch_unreconstructable_adverse_evidence="
        f"{int(scoped_counts.get('unreconstructable_adverse_evidence', 0))}, "
        f"finding={diagnosis.finding}, threshold={diagnosis.threshold_version}"
    )
    report.add(
        "loop.skip_halt", "info",
        "historical entry evidence; not current planning health — " + detail,
        "inspect historical skip trace/outcome coverage; use Candidate/Plan diagnostics "
        "for current entry; does not change PI, config, Risk, or execution",
    )


def _probe_round1(report: Report) -> None:
    """Round 1 experiment progress vs Go/Kill thresholds.

    Read-only; reports gap toward the 60-trade G6 line.
    """
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        row = db.execute(
            "SELECT COUNT(*) closed, "
            "  COALESCE(ROUND(SUM(realized_pnl),3),0) realized, "
            "  COALESCE(SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END),0) wins, "
            "  COALESCE(SUM(CASE WHEN realized_pnl>0 THEN realized_pnl ELSE 0 END),0) gp, "
            "  COALESCE(-SUM(CASE WHEN realized_pnl<0 THEN realized_pnl ELSE 0 END),0) gl "
            "FROM positions WHERE status='closed' AND closed_at >= ?",
            ("2026-06-22",),
        ).fetchone()
    except Exception as e:
        report.add_probe_error("loop.round1", e, "could not read positions")
        return
    closed = row["closed"] or 0
    wins = row["wins"] or 0
    gp = float(row["gp"] or 0)
    gl = float(row["gl"] or 0)
    wr = round(wins / closed * 100, 1) if closed else None
    pf = round(gp / gl, 3) if gl > 0 else (None if gp == 0 else float("inf"))
    realized = float(row["realized"] or 0)
    target = 60
    gap = max(0, target - closed)
    detail = (f"closed={closed}/{target} (gap={gap}), realized={realized:+.2f}, "
              f"WR={wr if wr is not None else '?'}%, PF={pf if pf is not None else '?'}")
    # Round 1 terminated 2026-06-27 by owner decision. Report historical result as
    # info-only so it doesn't pollute the WARN list with a closed experiment's scores.
    if closed == 0:
        report.add("loop.round1", "info", "no closed trades since 2026-06-22 (Round 1 terminated)")
    else:
        report.add("loop.round1", "info", f"Round 1 final (terminated 2026-06-27): {detail}")


def _probe_knowledge_flood(report: Report) -> None:
    """Guard-candidate pipeline health.

    L-Bug-A' (trading_context.py) was fixed: Layer3 now queries guard_candidates
    directly by item_type, so table-level flooding no longer affects PI prompts.
    This probe now checks whether the pipeline is *producing* guard_candidates at all.
    """
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        total_guards = db.execute(
            "SELECT COUNT(*) FROM knowledge_items WHERE item_type='guard_candidate'"
        ).fetchone()[0]
        recent_guards = db.execute(
            "SELECT COUNT(*) FROM knowledge_items "
            "WHERE item_type='guard_candidate' AND created_at >= datetime('now','-7 days')"
        ).fetchone()[0]
    except Exception as e:
        report.add_probe_error("loop.knowledge_flood", e)
        return

    if total_guards == 0:
        report.add("loop.knowledge_flood", "warn",
                   "no guard_candidates produced — self-evolution pipeline not generating guards",
                   "check outcome_recorder and guard candidate generation")
    else:
        report.add("loop.knowledge_flood", "ok",
                   f"guard_candidates: {total_guards} total, {recent_guards} in last 7d")


def _probe_learning_retrieval(report: Report) -> None:
    """Stored Learning inventory — retrieval composition and guard counts.

    Two read-only checks surfaced by the 2026-06-26 full-lifecycle audit
    (docs/archive/legacy/issues/audits/LEARNING_LOOP_AUDIT_2026-06-26.md):

    1. retrieval_pool: the eligible pool (high_confidence + active) that the
       FROZEN ranker scores via _lesson_penalty is dominated by
       post_trade_review rows. Those reviews auto-upgrade to high_confidence on
       any fill, while trade_case_study needs has_full_chain — which is starved
       by the opportunity_id=None root cause (R0). A lopsided review:case ratio
       means the only live feedback loop is scoring against hallucination
       templates, not real attributed cases.
    2. guard_promotion: active and pending shadow inventory, not a promotion
       stall. The retired automatic-promotion flag needs no corrective action.

    Both are monitoring only; pending learned candidates do not grant authority.
    """
    try:
        from collections import Counter

        from aletheia.storage.sqlite import get_db
        db = get_db()

        # --- 1. eligible retrieval pool composition --------------------------
        pool: Counter = Counter()
        for r in db.execute(
            "SELECT item_type, COUNT(*) n FROM knowledge_items "
            "WHERE confidence='high_confidence' AND (status='active' OR status IS NULL) "
            "GROUP BY item_type"
        ):
            pool[r["item_type"]] = r["n"]
        total = sum(pool.values())
        if total == 0:
            report.add("loop.retrieval_pool", "info", "eligible pool empty yet")
        else:
            reviews = pool.get("post_trade_review", 0)
            cases = pool.get("trade_case_study", 0)
            review_pct = round(reviews / total * 100, 1)
            ratio = round(reviews / cases, 1) if cases else float("inf")
            detail = (f"pool={total}: post_trade_review={reviews}({review_pct}%), "
                      f"trade_case_study={cases}, review:case={ratio}")
            # warn when reviews flood the pool AND real attributed cases are starved
            if reviews > 0 and (ratio == float("inf") or ratio >= 20):
                report.add(
                    "loop.retrieval_pool", "warn",
                    f"retrieval pool skewed to historical reviews — {detail}",
                    "Stored-data debt only — this skew NO LONGER reaches AI decisions: "
                    "ranker _lesson_penalty (B1) and the advisory path "
                    "router._ITEM_TYPES_BY_ROLE (dbce73e) both now exclude "
                    "post_trade_review; new reviews are medium_confidence (C1) so they "
                    "never enter this high_confidence pool. Root cause R0 (opportunity_id "
                    "starving trade_case_study has_full_chain) is fixed — the residual "
                    "1.8k high-confidence reviews are Round-1 legacy that dilutes as "
                    "Round 2 emits full-chain cases, or clear via one-time reclassification "
                    "(LEARNING_LOOP_AUDIT_2026-06-26.md §R0/R1; L-Bug-C closed)")
            else:
                report.add("loop.retrieval_pool", "ok", detail)

        # --- 2. guard promotion / shadow-candidate pool ---------------------
        # Both counts come from FILES (knowledge/guards + guard_candidates by
        # frontmatter status), never knowledge_items: the DB can retain legacy /
        # cross-platform file paths, and only the frontmatter status is authoritative
        # for the guard lifecycle.
        active_guards = pending_candidates = 0
        try:
            from extensions.knowledge.guards import load_trading_guards
            active_guards = len(load_trading_guards(status="active"))
            pending_candidates = len(load_trading_guards(status="candidate"))
        except Exception:
            logger.warning("doctor: guard-promotion counts unavailable — reported zeros are unreliable", exc_info=True)
        # A shadow backlog is observation feed, not a broken automatic promotion chain.
        detail = f"{active_guards} active guard(s), {pending_candidates} pending shadow candidate(s)"
        report.add("loop.guard_promotion", "ok", detail)
    except Exception as e:
        report.add_probe_error("loop.retrieval_pool", e)


def _probe_reconciled_ratio(report: Report) -> None:
    """Fraction of recent closes that came from reconciler (positions vanished
    exchange-side) rather than AI-driven stop_loss / take_profit / pi_close.

    A high reconciled ratio means the Round 1 sample is dominated by orphan
    sweeping rather than genuine AI exits — judgement numbers (PF / WR) are
    being driven by data-integrity events instead of strategy performance.
    Read-only. See ROUND1_JUDGMENT_PREVIEW.md §1D for context.
    """
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        cutoff_72h = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
        rows = [dict(row) for row in db.execute(
            "SELECT * FROM positions "
            "WHERE status='closed' AND closed_at >= ? "
            "ORDER BY closed_at DESC LIMIT 50",
            (cutoff_72h,),
        )]
        if not rows:
            report.add("loop.reconciled_ratio", "info",
                       "no closed positions since 2026-06-22")
            return
        from collections import Counter

        from aletheia.analytics.outcome_integrity import classify_closure
        categories: Counter = Counter(classify_closure(row).category for row in rows)
        total = len(rows)
        reconciled = categories["administrative"]
        maintenance = categories["maintenance"]
        ai_driven = categories["strategy"]
        unknown = categories["unknown"]
        newest_reconciled = next((
            row["closed_at"] for row in rows
            if classify_closure(row).category == "administrative"
        ), None)
        newest_reconciled_age = _age_minutes(newest_reconciled) if newest_reconciled else None
        recon_pct = round(reconciled / total * 100, 1) if total else 0
        ai_pct = round(ai_driven / total * 100, 1) if total else 0
        detail = (f"last {total}: administrative={reconciled}({recon_pct}%), "
                  f"maintenance={maintenance}, strategy={ai_driven}({ai_pct}%), "
                  f"unknown={unknown}")
        if recon_pct > 50:
            active_thrash = False
            try:
                from aletheia.portfolio.position_reconciler import detect_reconcile_thrash
                active_thrash = bool(detect_reconcile_thrash(window_minutes=30).get("churned_symbols"))
            except Exception as exc:
                logger.warning("doctor: reconcile-thrash probe unavailable (%s)", type(exc).__name__)
                active_thrash = False
            if active_thrash:
                report.add("loop.reconciled_ratio", "warn",
                           f"reconciler dominates closes: {detail}",
                           "current close→reopen churn is present; judgement PF/WR is driven by "
                           "orphan sweep rather than AI exits")
            else:
                age_note = (
                    f"; newest reconciled {_fmt_age(newest_reconciled_age)}"
                    if newest_reconciled_age is not None else ""
                )
                report.add("loop.reconciled_ratio", "info",
                           f"historical reconciler-dominated closes: {detail}{age_note}; "
                           "no active churn fingerprint",
                           "recent judgement PF/WR remains polluted by historical orphan sweeps, but "
                           "loop.reconcile_thrash is the current-fault detector")
        elif recon_pct > 25:
            report.add("loop.reconciled_ratio", "info",
                       f"reconciled noticeable: {detail}")
        else:
            report.add("loop.reconciled_ratio", "ok", detail)
    except Exception as e:
        report.add_probe_error("loop.reconciled_ratio", e)


def _probe_outcome_history_integrity(report: Report) -> None:
    """Read-only cleanup readiness for classified history and derived artifacts."""
    try:
        from ops.maintenance.outcome_integrity import remediate_outcome_history

        plan = remediate_outcome_history(dry_run=True)
        classifications = int(plan["classification"]["planned"])
        archives = int(plan["archive"]["planned"])
        compensation = len(plan["compensation_candidates"])
        detail = (
            f"anchor={plan['clean_window_anchor_id']}; "
            f"classification_pending={classifications}; archive_pending={archives}; "
            f"cash_compensation_candidates={compensation} (owner approval required)"
        )
        if classifications or archives:
            report.add(
                "loop.outcome_history", "info", detail,
                "preview with `aletheia knowledge remediate-outcomes`; apply is opt-in",
            )
        else:
            report.add("loop.outcome_history", "ok", detail)
    except Exception as e:
        report.add_probe_error("loop.outcome_history", e)


def _probe_sizing_policy(report: Report) -> None:
    """Persona-learning (P3) health: is the sizing learner active, and is any active
    regime bucket bleeding (negative clean reward while nudging appetite)? Dormant by
    default → info. A bucket that is active AND has negative mean reward is flagged
    warn (the learner is defensive there, but the operator should see the bleed).
    Read-only."""
    try:
        from aletheia.trading.sizing_policy import sizing_policy_health
        h = sizing_policy_health()
        if not h["enabled"]:
            report.add("loop.sizing_policy", "info", "persona learning dormant (persona_learning_enabled=False)")
            return
        active = {b: v for b, v in h["buckets"].items() if v["active"]}
        if not active:
            report.add("loop.sizing_policy", "info",
                       f"persona learning ON, no bucket past sample gate ({h['min_samples']})")
            return
        bleeding = {b: v for b, v in active.items() if (v["mean_reward_pct"] or 0) < 0}
        summary = ", ".join(f"{b}:n={v['clean_samples']} r={v['mean_reward_pct']}% adj={v['appetite_adj']}"
                            for b, v in active.items())
        if bleeding:
            report.add("loop.sizing_policy", "warn",
                       f"active learning buckets bleeding: {summary}",
                       "the learner is sizing DOWN there (defensive), but a negative-reward "
                       "regime bucket may warrant refreezing the param or reviewing the strategy")
        else:
            report.add("loop.sizing_policy", "ok", f"active learning buckets: {summary}")
    except Exception as e:
        report.add_probe_error("loop.sizing_policy", e)


def _probe_persona_rollout(report: Report) -> None:
    """Show desired Persona flags separately from durable dynamic authority."""
    try:
        from aletheia.config.loader import load_config
        from aletheia.trading.persona_rollout import persona_rollout_status

        cfg = load_config()
        status = persona_rollout_status(cfg)
        if not status.get("desired_progressive_unlock"):
            report.add(
                "loop.persona_rollout",
                "info",
                "progressive unlock disabled; actual authority is static-only",
            )
            return
        evidence = status.get("evidence") or {}
        actual = ",".join(status.get("actual_authorized_params") or []) or "none"
        detail = (
            f"desired_progressive=on; status={status.get('status')}; actual={actual}; "
            f"samples={evidence.get('sample_count', 'not available')}/"
            f"{evidence.get('required_samples', 'not available')}; "
            f"market_days={evidence.get('market_day_count', 'not available')}/"
            f"{evidence.get('required_market_days', 'not available')}; "
            f"verdict={evidence.get('reason', 'not available')}"
        )
        if status.get("status") == "refrozen" or evidence.get("reason") in {
            "invalid_provenance",
            "negative_contribution",
            "degrading_contribution",
            "unstable_contribution",
            "reconcile_thrash_state_unavailable",
        }:
            report.add("loop.persona_rollout", "warn", detail)
        elif status.get("status") in {"unlocked", "completed"}:
            report.add("loop.persona_rollout", "ok", detail)
        else:
            report.add("loop.persona_rollout", "info", detail)
    except Exception as e:
        report.add_probe_error("loop.persona_rollout", e)


def _probe_execution_liveness(report: Report) -> None:
    """Can an entry still get through; is any Order stuck; does the funnel produce fills."""
    try:
        from aletheia.execution.liveness import (
            OVERDUE_HOURS, entry_funnel, funnel_is_dead, identity_blocked_modes,
            overdue_orders, retry_storm_proposals,
        )

        overdue = overdue_orders()
        blocked = identity_blocked_modes()
        storm = retry_storm_proposals()
        funnel = entry_funnel()
        problems = []
        if overdue:
            problems.append(
                f"{len(overdue)} Order(s) non-terminal beyond {OVERDUE_HOURS}h: "
                + ", ".join(f"{o['id']}({o['status']},{o['age_hours']}h)" for o in overdue[:5])
            )
        if blocked:
            problems.append(f"identity gate refuses new entries on: {', '.join(blocked)}")
        if storm:
            problems.append(f"{len(storm)} proposal(s) with an error-order retry storm")
        if funnel_is_dead(funnel):
            problems.append(
                f"{funnel['plans']} Entry Plans in {funnel['window_days']}d, "
                f"{funnel['ordered']} reached an Order, 0 filled"
            )
        detail = (f"plans_{funnel['window_days']}d={funnel['plans']} ordered={funnel['ordered']} "
                  f"filled={funnel['filled']}")
        if problems:
            report.add("loop.execution_liveness", "warn", "; ".join(problems) + f"; {detail}",
                       "see execution_contract §3.0b/§6.1: re-anchor or end stuck Orders; "
                       "never loosen the identity comparison")
        else:
            report.add("loop.execution_liveness", "ok", detail)
    except Exception as e:
        report.add_probe_error("loop.execution_liveness", e)


def _probe_reconcile_thrash(report: Report) -> None:
    """Report current churn plus the durable circuit-breaker/recovery state."""
    try:
        from extensions.learning.control import global_learning_refreeze_reason
        from aletheia.portfolio.position_reconciler import detect_reconcile_thrash
        from aletheia.portfolio.reconciliation_integrity import (
            clean_observation_gate,
            get_active_reconciliation_integrity_incident,
        )
        t = detect_reconcile_thrash(window_minutes=30)
        incident = get_active_reconciliation_integrity_incident()
        if not t.get("detector_available", True):
            report.add(
                "loop.reconcile_thrash", "warn",
                "reconciliation integrity detector unavailable",
                "demo reconcile actions must stay suspended until scoped position "
                "history can be read",
            )
            return
        churned = t["churned_symbols"]
        detail = (f"{t['churn_count']} churned in {t['window_minutes']}min "
                  f"(administrative={t['administrative_close_recent']}, "
                  f"reopened={t['reopened_recent']}, sources={t['reopened_by_source']}); "
                  f"{', '.join(churned[:5]) or 'none'}")
        if incident is not None:
            gate = clean_observation_gate(incident)
            freeze = global_learning_refreeze_reason()
            report.add("loop.reconcile_thrash", "warn",
                       f"reconcile integrity incident {incident['id']} active; {detail}; "
                       f"clean gate={gate['observed']}/{gate['required']}; "
                       f"Learning freeze={freeze}",
                       "demo adopt/orphan-close and behavior-changing Learning remain "
                       "frozen; verify account-scoped broker evidence, collect spaced "
                       "clean observations, then resolve explicitly")
        elif t["is_thrashing"]:
            report.add("loop.reconcile_thrash", "warn",
                       f"current churn has no durable incident yet: {detail}",
                       "the next reconcile cycle must persist the incident before any "
                       "demo adopt/orphan-close action")
        elif t["churn_count"] == 1:
            report.add("loop.reconcile_thrash", "info",
                       f"single flip-flop (early signal, not tripping): {detail}")
        else:
            report.add("loop.reconcile_thrash", "ok", f"no thrash fingerprint: {detail}")
    except Exception as e:
        report.add_probe_error("loop.reconcile_thrash", e)


def _probe_concentration(report: Report) -> None:
    """24h fills concentration — flag if a single symbol×direction dominates.

    A >60% single-bucket concentration means PI is making essentially one bet
    repeatedly (e.g. \"ETH short\"), which is a 5x-correlated risk exposure
    instead of a diversified strategy basket. Read-only; observation only.
    """
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        rows = list(db.execute(
            "SELECT o.symbol || ':' || o.direction AS bucket, COUNT(*) n, MAX(f.filled_at) newest "
            "FROM fills f JOIN orders o ON f.order_id = o.id "
            "WHERE f.filled_at >= datetime('now', '-1 day') "
            "GROUP BY bucket ORDER BY n DESC"
        ))
        if not rows:
            report.add("loop.concentration", "info", "no fills in last 24h")
            return
        total = sum(r[1] for r in rows)
        top_bucket, top_n, top_newest = rows[0][0], rows[0][1], rows[0][2]
        top_age = _age_minutes(top_newest)
        top_pct = round(top_n / total * 100, 1) if total else 0
        detail = f"24h fills={total}, top: {top_bucket}={top_n} ({top_pct}%)"
        if top_pct > 60 and total >= 10:
            if top_age is not None and top_age > 360:
                report.add("loop.concentration", "info",
                           f"historical concentration residue: {detail}; newest top-bucket fill {_fmt_age(top_age)}")
            else:
                report.add("loop.concentration", "warn",
                           f"Trading Decision bias: {detail}",
                           "highly correlated exposure — System Operator may be making one bet "
                           "repeatedly; check trade_proposals for direction diversity")
        elif top_pct > 40:
            report.add("loop.concentration", "info",
                       f"moderate concentration: {detail}")
        else:
            report.add("loop.concentration", "ok", detail)
    except Exception as e:
        report.add_probe_error("loop.concentration", e)


def _probe_health(report: Report) -> None:
    try:
        from aletheia.agent_runtime.health import compute_health
        h = compute_health()
    except Exception as e:
        report.add_probe_error("health.rollup", e, "compute_health failed")
        return
    status = str(h.get("status") or ("healthy" if h.get("healthy") else "degraded"))
    if status == "healthy":
        report.add("health.rollup", "ok", "healthy (no issues)")
    else:
        issues = ", ".join(h.get("issues", [])) or "health evidence is not established"
        report.add("health.rollup", "warn", f"{status}: {issues}")


def _probe_startup_behavior_gate(report: Report) -> None:
    """Surface the Startup Behavior Gate's durable blocker as ground truth.

    When the gate is holding the trading loop, this is the single place an
    operator (or agent) can read WHY new AI decisions and trades are suspended
    while data collection keeps running."""
    try:
        from aletheia.agent_runtime.startup_behavior_gate import read_gate_state
        from aletheia.config.loader import load_config

        cfg = load_config()
        if not cfg.health.startup_behavior_gate_enabled:
            report.add("startup_gate.status", "info", "startup behavior gate disabled")
            return
        state = read_gate_state()
        if not state:
            report.add("startup_gate.status", "ok", "checks clear — AI/trading permitted")
            return
        blockers = ", ".join(state.get("blockers") or []) or "unknown"
        detail_map = {c.get("name"): c.get("detail") for c in state.get("checks") or []}
        detail = "; ".join(
            f"{name}: {detail_map.get(name, '')}" for name in (state.get("blockers") or [])
        ) or blockers
        if state.get("paused_by_gate"):
            report.add(
                "startup_gate.status", "blocker",
                f"AI/trading paused; data collection continues. Blockers: {detail}",
                fix_hint="Resolve the blockers (or fix config); the gate re-checks "
                         "periodically and resumes automatically when clear.",
            )
        else:
            reason = state.get("user_block_reason") or "held"
            report.add(
                "startup_gate.status", "warn",
                f"gate held by operator/system ({reason}); not auto-resumed",
                fix_hint="This is a human-owned pause/kill switch — resume from the "
                         "dashboard or clear the kill switch to lift it.",
            )
    except Exception as e:
        report.add_probe_error("startup_gate.status", e, "startup gate probe error")


def _probe_operator_shell(report: Report) -> None:
    """Report the System Operator shell liveness."""
    try:
        from aletheia.agent_shell.operator_store import get_latest_heartbeat
        from aletheia.config.loader import load_config

        cfg = load_config()
        if not cfg.operator.enabled:
            report.add("operator.shell", "info", "operator command shell disabled in config")
            return

        heartbeat = get_latest_heartbeat("operator")
        if heartbeat is None:
            gateway_ready = any(
                check.name == "process.gateway" and check.severity == "ok"
                for check in report.checks
            )
            report.add(
                "operator.shell",
                "warn" if gateway_ready else "info",
                "no operator command-shell heartbeat yet",
                "check operator-shell dependencies and restart the gateway" if gateway_ready else "",
            )
            return

        status = str(heartbeat.get("status") or "offline")
        age = _age_minutes(heartbeat.get("last_seen_at"))
        stale_after_minutes = float(cfg.operator.heartbeat_stale_after_sec) / 60.0
        stale = age is None or age > stale_after_minutes
        mode = str(heartbeat.get("mode") or "unknown")
        error = " ".join(str(heartbeat.get("last_error") or "").split())[:240]

        if status == "online" and not stale:
            report.add("operator.shell", "ok", f"online ({mode}), heartbeat {_fmt_age(age)}")
            return

        detail = f"{status} ({mode}), heartbeat {_fmt_age(age)}"
        if stale:
            detail += "; heartbeat stale"
        if error:
            detail += f"; last error: {error}"
        lower_error = error.lower()
        if "node" in lower_error and (
            "no such file" in lower_error or "not found" in lower_error
        ):
            hint = "install Node in a stable fnm/Homebrew location, then restart the gateway"
        else:
            from aletheia.config.loader import get_data_dir

            hint = f"inspect {get_data_dir() / 'logs' / 'operator-shell-errors.log'} and restart the gateway"
        report.add("operator.shell", "warn", detail, hint)
    except Exception as e:
        report.add_probe_error("operator.shell", e)


def _probe_pi_daemon(report: Report) -> None:
    """System Operator (PI Daemon) liveness — the event-driven trade decision loop.

    The daemon is an in-memory ``asyncio.Task`` inside the gateway process, so a
    separate ``aletheia doctor`` process cannot see its module-level state
    (``is_daemon_running()`` would always be False when imported here).  We
    therefore **query the gateway's ``/api/operator/status`` endpoint**, which
    evaluates ``daemon_status()`` *inside* the real gateway process and returns
    the ``trading_daemon`` payload.

    Direct import of ``pi_daemon`` is only a fallback for the unusual case where
    the API is unreachable.
    """
    import json
    import urllib.request

    from aletheia.config.loader import load_config
    from aletheia.gateway.network import local_frontdoor_host

    cfg = load_config()
    if not cfg.pi_daemon.enabled:
        report.add("pi_daemon", "info", "PI daemon disabled in config")
        return

    # --- Attempt 1: query the gateway HTTP API (cross-process safe) ---
    # The front door may gzip-compress responses; a bare ProxyHandler opener
    # does not install urllib's default Content-Encoding handler, so we send
    # Accept-Encoding: identity and also try the backend port as fallback.
    daemon: dict | None = None
    try:
        host = local_frontdoor_host()
        runtime_port = os.environ.get("ALETHEIA_RUNTIME_PORT")
        base_port = int(runtime_port) if runtime_port else cfg.app.dashboard_port
        for port in (base_port, base_port + 1):
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                req = urllib.request.Request(
                    f"http://{host}:{port}/api/operator/status",
                    headers={"Accept": "application/json", "Accept-Encoding": "identity"},
                )
                with opener.open(req, timeout=2) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                payload_daemon = payload.get("trading_daemon")
                if payload_daemon is not None:
                    daemon = payload_daemon
                    break
            except Exception:
                continue
    except Exception:
        daemon = None

    if daemon is not None:
        _render_daemon_report(report, daemon)
        return

    # --- Attempt 2: API unreachable. The gateway may be down, restarting, or
    # the probe timed out.  Do NOT assert "daemon not running": the in-process
    # import below only works when doctor runs inside the gateway process
    # itself. In a separate CLI it would always report False, giving a false
    # negative.  Report indeterminate unless the import authoritatively says
    # the daemon IS running.
    try:
        from aletheia.agent_runtime.pi_daemon import daemon_status, is_daemon_running

        if is_daemon_running():
            status = daemon_status()
            hb_age = status.get("last_heartbeat_age_sec")
            failures = status.get("consecutive_failures", 0)
            queue_depth = status.get("signal_pending_queue_depth")

            parts = [f"running (gen={status.get('task_generation', 0)})"]
            if hb_age is not None:
                parts.append(f"heartbeat {_fmt_age(hb_age)}")
            if failures >= 3:
                parts.append(f"{failures} consecutive eval failures")
            if queue_depth is not None and queue_depth > 5:
                parts.append(f"queue depth {queue_depth}")

            if hb_age is not None and hb_age > 120:
                report.add("pi_daemon", "warn", ", ".join(parts),
                           "daemon heartbeat stale — watchdog will restart if >3min")
            elif failures >= 3:
                report.add("pi_daemon", "warn", ", ".join(parts))
            else:
                report.add("pi_daemon", "ok", ", ".join(parts))
        else:
            report.add(
                "pi_daemon", "info",
                "gateway API unreachable — cannot determine daemon state",
                "gateway may be restarting; re-run `aletheia doctor` in a moment",
            )
    except Exception as e:
        report.add_probe_error("pi_daemon", e)


def _render_daemon_report(report: Report, daemon: dict) -> None:
    """Render a doctor ``pi_daemon`` line from the ``trading_daemon`` payload
    returned by ``/api/operator/status``.
    """
    configured = daemon.get("configured", True)
    running = daemon.get("running", False)
    state = daemon.get("state", "unknown")
    evaluating = daemon.get("evaluating", False)
    last_error = daemon.get("last_error_summary")
    fallback = daemon.get("fallback_available", False)

    if not configured:
        report.add("pi_daemon", "info", "PI daemon disabled in config")
        return

    if not running:
        if fallback:
            report.add(
                "pi_daemon", "warn",
                "PI daemon not running (scheduler fallback active) — "
                "event-driven decisions paused",
                "watchdog will auto-restart; if persistent, check gateway logs",
            )
        else:
            report.add(
                "pi_daemon", "warn",
                "PI daemon not running — trade decisions stopped",
                "watchdog will auto-restart; if persistent, check gateway logs",
            )
        return

    parts = [f"running (state={state})"]
    if evaluating:
        parts.append("evaluating")
    if last_error:
        parts.append(f"last error: {last_error}")
    if fallback:
        parts.append("scheduler fallback available")

    report.add("pi_daemon", "ok", ", ".join(parts))


def _probe_signal_effectiveness(report: Report) -> None:
    """Check universe signal win-rate over the last 7 days."""
    try:
        from datetime import timedelta

        from aletheia.storage.repositories import complete_outcome_economics_sql, net_clears_tradable_edge_sql
        from aletheia.storage.sqlite import get_db
        from aletheia.universe.outcome_economics import COST_MODEL_VERSION

        db = get_db()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

        # 24h horizon — most meaningful for strategy effectiveness
        row = db.execute(
            f"""SELECT COUNT(*),
                      SUM(CASE WHEN {net_clears_tradable_edge_sql()}
                               THEN 1 ELSE 0 END),
                      AVG(net_return_pct)
               FROM filter_candidate_outcomes
               WHERE status = 'settled'
                 AND horizon = '24h'
                 AND settled_at >= ?
                 AND cost_model_version = ?
                 AND {complete_outcome_economics_sql()}""",
            (cutoff, COST_MODEL_VERSION),
        ).fetchone()

        total, wins, avg_ret = row if row else (0, None, None)

        if not total:
            report.add("data.signal", "info",
                       "insufficient cost-aware net outcomes in last 7d — signal effectiveness unknown")
            return

        win_rate = (wins or 0) / total * 100
        avg_str = f"{avg_ret:.3f}%" if avg_ret is not None else "n/a"
        identity = f"cost_model={COST_MODEL_VERSION}"

        if win_rate < 40:
            report.add(
                "data.signal", "warn",
                f"24h net signal win_rate {win_rate:.1f}% "
                f"({total} settled, avg_net={avg_str}, {identity}) — "
                "aggregate observation requires cohort diagnosis",
                "inspect outcome coverage, candidate-skip evidence, and cohort splits; do not auto-tune PI",
            )
        elif win_rate >= 55:
            report.add("data.signal", "ok",
                       f"24h net signal win_rate {win_rate:.1f}% "
                       f"({total} settled, avg_net={avg_str}, {identity})")
        else:
            report.add("data.signal", "info",
                       f"24h net signal win_rate {win_rate:.1f}% "
                       f"({total} settled, avg_net={avg_str}, {identity}) — near-random")
    except Exception:
        logger.warning("doctor: signal-effectiveness probe unavailable", exc_info=True)


def _probe_outcome_economics(report: Report) -> None:
    """Expose whether post-migration outcomes have complete net economics."""
    try:
        from aletheia.storage.repositories import complete_outcome_economics_sql
        from aletheia.storage.sqlite import get_db
        from aletheia.universe.outcome_economics import COST_MODEL_VERSION

        db = get_db()
        row = db.execute(
            "SELECT COUNT(*) AS total, "
            f"SUM(CASE WHEN {complete_outcome_economics_sql()} "
            "  THEN 1 ELSE 0 END) AS complete, "
            "0 AS missing_funding, "
            "SUM(CASE WHEN net_return_pct IS NULL "
            "  AND settlement_price_source = 'late_ticker' THEN 1 ELSE 0 END) AS late_price "
            "FROM filter_candidate_outcomes "
            "WHERE status='settled' AND cost_model_version=?",
            (COST_MODEL_VERSION,),
        ).fetchone()
        total = int(row["total"] or 0) if row else 0
        complete = int(row["complete"] or 0) if row else 0
        missing_funding = int(row["missing_funding"] or 0) if row else 0
        late_price = int(row["late_price"] or 0) if row else 0
        if total == 0:
            report.add(
                "data.outcome_economics",
                "info",
                "cost-aware outcome model is warming up; no settled "
                f"{COST_MODEL_VERSION} rows yet",
            )
            return
        ratio = complete / total
        detail = (
            f"cost-aware outcomes complete={complete}/{total} ({ratio:.0%}); "
            f"missing_funding={missing_funding}, late_price={late_price}; "
            f"cost_model={COST_MODEL_VERSION}"
        )
        if total >= 10 and ratio < 0.5:
            report.add(
                "data.outcome_economics",
                "warn",
                detail,
                "inspect derivative funding coverage and outcome settlement lag",
            )
        elif complete == total:
            report.add("data.outcome_economics", "ok", detail)
        else:
            report.add("data.outcome_economics", "info", detail)
    except Exception:
        logger.warning("doctor: outcome-economics probe unavailable", exc_info=True)


def _probe_filter_evidence(report: Report) -> None:
    """Report whether filter pass/reject facts and their net outcomes are connected."""
    try:
        from aletheia.storage.repositories import (
            complete_outcome_economics_sql,
            count_pending_filter_candidate_outcomes_unreachable_benchmark,
            filter_outcome_unreachable_predicate,
        )
        from aletheia.storage.sqlite import get_db

        unreachable_sql, unreachable_params = filter_outcome_unreachable_predicate()

        db = get_db()
        decisions = db.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN decision='passed' THEN 1 ELSE 0 END) AS passed, "
            "SUM(CASE WHEN decision='rejected' THEN 1 ELSE 0 END) AS rejected, "
            "COUNT(DISTINCT CASE WHEN outcome_candidate_key IS NOT NULL "
            "  THEN outcome_candidate_key END) AS outcome_candidates "
            "FROM filter_decisions"
        ).fetchone()
        batches = db.execute(
            "SELECT COUNT(*) AS total, "
            "COALESCE(SUM(candidate_count), 0) AS candidates, "
            "COUNT(DISTINCT filter_name) AS filters, "
            "SUM(CASE WHEN candidate_count != "
            "  passed_count + rejected_count + not_applicable_count + error_count "
            "  THEN 1 ELSE 0 END) AS malformed "
            "FROM filter_decision_batches"
        ).fetchone()
        total = int(decisions["total"] or 0) if decisions else 0
        batch_total = int(batches["total"] or 0) if batches else 0
        batch_candidates = int(batches["candidates"] or 0) if batches else 0
        batch_filters = int(batches["filters"] or 0) if batches else 0
        malformed_batches = int(batches["malformed"] or 0) if batches else 0
        if total == 0:
            if batch_total:
                detail = (
                    f"decisions=0; batches={batch_total}, "
                    f"batch_candidates={batch_candidates}, "
                    f"batch_filters={batch_filters}, malformed={malformed_batches}; "
                    "directional outcome evidence is warming up"
                )
                if malformed_batches:
                    report.add(
                        "data.filter_evidence",
                        "warn",
                        detail,
                        "inspect incomplete directionless filter batch writes",
                    )
                else:
                    report.add("data.filter_evidence", "info", detail)
                return
            report.add(
                "data.filter_evidence",
                "info",
                "filter evidence is warming up; no pass/reject facts recorded yet",
            )
            return

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        stale_before = (
            now - timedelta(minutes=_OUTCOME_SETTLER_GRACE_MINUTES)
        ).isoformat()
        outcomes = db.execute(
            "SELECT COUNT(DISTINCT outcome_candidate_key) AS registered, "
            "SUM(CASE WHEN status='settled' THEN 1 ELSE 0 END) AS settled, "
            "SUM(CASE WHEN status='pending' AND datetime(due_at) < datetime(?) "
            f"  AND NOT {unreachable_sql} "
            "  THEN 1 ELSE 0 END) AS overdue, "
            "SUM(CASE WHEN status='pending' AND datetime(due_at) < datetime(?) "
            f"  AND NOT {unreachable_sql} "
            "  THEN 1 ELSE 0 END) AS stale_overdue "
            "FROM filter_candidate_outcomes",
            (now_iso, *unreachable_params, stale_before, *unreachable_params),
        ).fetchone()
        from aletheia.analytics.outcome_semantics import candidate_outcome_temporal_reason

        net_complete_rows = db.execute(
            "SELECT * FROM filter_candidate_outcomes "
            "WHERE status='settled' AND "
            f"{complete_outcome_economics_sql()}",
        ).fetchall()
        net_complete = 0
        temporal_invalid_settled = 0
        for raw in net_complete_rows:
            if candidate_outcome_temporal_reason(dict(raw), as_of_at=now_iso) is None:
                net_complete += 1
            else:
                temporal_invalid_settled += 1
        outcome_candidates = int(decisions["outcome_candidates"] or 0)
        registered = int(outcomes["registered"] or 0) if outcomes else 0
        settled = int(outcomes["settled"] or 0) if outcomes else 0
        overdue = int(outcomes["overdue"] or 0) if outcomes else 0
        stale_overdue = int(outcomes["stale_overdue"] or 0) if outcomes else 0
        awaiting_cadence = max(0, overdue - stale_overdue)
        registration = registered / outcome_candidates if outcome_candidates else 1.0
        # Rows already written off without any observation behind them, across all
        # three settlement queues. Every other number in this probe describes work
        # that is still pending and will drain on its own; this one describes
        # evidence that is gone and stays gone until someone reclaims it, so it is
        # the one that has to be visible.
        from ops.maintenance.settlement_requeue import (
            count_unobserved_failed_outcomes,
        )

        unobserved_failed = sum(count_unobserved_failed_outcomes().values())
        # A second class of rows the settler will never reach: their discovery
        # benchmark is no longer active, so both the selector and the terminal
        # write guard refuse them.  Like the write-offs above, they do not drain
        # on their own, so they must not be reported as settler backlog.
        unreachable_pending = count_pending_filter_candidate_outcomes_unreachable_benchmark(
            now_iso
        )
        detail = (
            f"decisions={total} (pass={int(decisions['passed'] or 0)}, "
            f"reject={int(decisions['rejected'] or 0)}); "
            f"batches={batch_total}, batch_candidates={batch_candidates}, "
            f"batch_filters={batch_filters}, malformed={malformed_batches}; "
            f"outcome_candidates={registered}/{outcome_candidates}; "
            f"settled={settled}, net_complete={net_complete}, "
            f"temporal_invalid_settled={temporal_invalid_settled}, overdue={overdue}, "
            f"awaiting_cadence={awaiting_cadence}, stale_overdue={stale_overdue}, "
            f"unreachable_pending={unreachable_pending}, "
            f"unobserved_failed={unobserved_failed}"
        )
        if unobserved_failed:
            report.add(
                "data.filter_evidence",
                "warn",
                detail,
                f"{unobserved_failed} settlement rows are 'failed' with no price and no "
                "price source — they were written off before any observation was taken, "
                "and nothing revisits them. Reclaim with "
                "`aletheia data requeue-unobserved-settlements --apply` "
                "(dry-run without --apply)",
            )
        elif unreachable_pending:
            report.add(
                "data.filter_evidence",
                "warn",
                detail,
                f"{unreachable_pending} due outcomes belong to a discovery benchmark that "
                "is no longer active; the settler can neither select nor write them, so "
                "they are not settler backlog. The settler closes them on its next run — "
                "a non-zero count means no settler has run since they became unreachable",
            )
        elif malformed_batches:
            report.add(
                "data.filter_evidence",
                "warn",
                detail,
                "inspect incomplete directionless filter batch writes",
            )
        elif registration < 0.95:
            report.add(
                "data.filter_evidence",
                "warn",
                detail,
                "inspect filter evidence write failures; eligible decisions are missing outcomes",
            )
        elif stale_overdue > 50:
            report.add(
                "data.filter_evidence",
                "warn",
                detail,
                "inspect outcome settler logs and exact-instrument market coverage; "
                "recently due rows inside the cadence are excluded from this warning",
            )
        else:
            report.add("data.filter_evidence", "ok", detail)
    except Exception:
        logger.warning("doctor: filter-evidence probe unavailable", exc_info=True)


# Define exports before importing doctor so either module can be imported first.
from aletheia.diagnostics.doctor import (
    Check as Check,
    Report,
    _OUTCOME_SETTLER_GRACE_MINUTES,
    _age_minutes,
    _fmt_age,
    logger,
)
