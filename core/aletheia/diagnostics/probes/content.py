"""Read-only content-quality probes for doctor reports."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

__all__ = [
    "_CONTENT_PROBES",
    "_probe_content_attribution",
    "_probe_content_lesson_chain",
    "_probe_content_lesson_guards",
    "_probe_content_memo_quality",
    "_probe_content_proposal_bias",
    "_probe_content_shadow_trades",
    "_probe_content_signal_settlement",
]


# --- content-quality probes (Phase 2: AI-native bug discovery) ---------------
# These differ from the operational probes above: instead of "is the loop turning",
# they ask "is the content the AI decision chain produced coherent / non-hallucinated
# / non-garbage" — exactly what a human used to find by eye in the Web UI's Evolve
# module. Each is read-only and follows the probe-honesty rule (owner-acknowledged /
# config-gated states report info with the numbers, not a false warn). See
# docs/archive/legacy/plans/engineering/ai_native_bugfix_path.md §3. They are deliberately NON-overlapping with
# loop.knowledge_flood / loop.retrieval_pool / loop.reconciled_ratio / loop.concentration.

def _probe_content_memo_quality(report: Report) -> None:
    """Trading memo (the regime playbook injected into PI context): absent vs
    expired vs (when memo_effectiveness has scored it) empirically overturned."""
    try:
        from aletheia.storage.repositories import list_knowledge_items
    except Exception as e:
        report.add_probe_error("content.memo_quality", e)
        return
    memo = None
    try:
        # #1939: the memo reader is mode-scoped. For the doctor probe we
        # accept the newest memo regardless of the currently-selected mode so
        # the diagnostic still fires when no config is loaded and so a memo
        # for a different mode is still reported ("exists but for another
        # cohort") rather than silently disappearing.
        import json as _json
        from aletheia.analytics.outcome_integrity import is_current_outcome_artifact
        from datetime import timedelta as _td
        items = list_knowledge_items(item_type="trading_memo", limit=5)
        for item in items:
            content = item.get("content") or ""
            if not content:
                continue
            try:
                candidate = _json.loads(content)
            except (TypeError, ValueError):
                continue
            if is_current_outcome_artifact(candidate, max_age=_td(hours=72)):
                memo = candidate
                break
    except Exception:
        logger.warning("doctor: trading-memo read failed", exc_info=True)
    if memo:
        # accuracy_score is written by memo_effectiveness (not yet on master); light
        # up only when the tag exists, so this probe sharpens automatically once it lands.
        try:
            rows = list_knowledge_items(item_type="trading_memo", limit=1)
            import json as _json
            tags = _json.loads(rows[0].get("tags") or "{}") if rows else {}
            acc = tags.get("accuracy_score")
        except Exception:
            acc = None
        if isinstance(acc, (int, float)) and acc < 0.4:
            report.add("content.memo_quality", "warn",
                       f"latest trading_memo accuracy_score={acc:.2f} (<0.40) — its rules were "
                       f"contradicted by outcomes (likely hallucinated playbook)",
                       "invalidate + re-synthesize the memo (insight_synthesizer.synthesize_trading_memo)")
        else:
            n = len(memo.get("regime_playbook") or [])
            report.add("content.memo_quality", "ok",
                       f"valid memo present ({n} regime rules, valid_until={memo.get('valid_until','?')[:16]})")
        return
    # No valid memo — distinguish expired from never-created.
    try:
        any_memo = list_knowledge_items(item_type="trading_memo", limit=1)
    except Exception as exc:
        logger.warning("doctor: trading-memo probe unavailable (%s)", type(exc).__name__)
        any_memo = []
    if any_memo:
        report.add("content.memo_quality", "warn",
                   "trading_memo exists but is expired — ranker _memo_scoring contributes 0 to every decision",
                   "re-synthesize: insight_synthesizer.synthesize_trading_memo()")
    else:
        report.add("content.memo_quality", "info", "no trading_memo yet")


def _probe_content_lesson_chain(report: Report) -> None:
    """ps_lessons learning→policy chain: lessons that never get accepted or applied
    are a dead loop (post-trade conclusions reach nothing)."""
    try:
        from collections import Counter

        from aletheia.storage.sqlite import get_db
        db = get_db()
        cnt: Counter = Counter()
        for r in db.execute("SELECT status, COUNT(*) n FROM ps_lessons GROUP BY status"):
            cnt[r["status"]] = r["n"]
        applied = db.execute(
            "SELECT COUNT(*) FROM ps_lessons WHERE applied_policy_version IS NOT NULL"
        ).fetchone()[0]
        from aletheia.agents import get_agent_fn
        try:
            get_agent_fn("lesson_reviewer")
        except ValueError:
            reviewer_wired = False
        else:
            reviewer_wired = True
        # Backlog that is old enough to have been reviewed (cron runs daily).
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        stale_proposed = db.execute(
            "SELECT COUNT(*) FROM ps_lessons WHERE status='proposed' AND created_at < ?",
            (cutoff,),
        ).fetchone()[0]
    except Exception as e:
        report.add_probe_error("content.lesson_chain", e)
        return
    total = sum(cnt.values())
    if total == 0:
        report.add("content.lesson_chain", "info", "no lessons yet")
        return
    # The reviewer "acted" if any lesson left 'proposed' via accept/reject/apply.
    progressed = cnt.get("accepted", 0) + cnt.get("applied", 0) + cnt.get("rejected", 0)
    detail = f"lessons={total} ({dict(cnt)}), applied_to_policy={applied}"
    if progressed > 0 or applied > 0:
        report.add("content.lesson_chain", "ok", detail)
    elif not reviewer_wired:
        report.add("content.lesson_chain", "warn",
                   f"lesson→policy chain not built: {detail}",
                   "no lesson_reviewer agent — lessons stuck 'proposed', none promoted "
                   "(Round 2 fix L-Bug-B)")
    elif stale_proposed > 0:
        # Machinery exists but the backlog is not draining — a real failure.
        report.add("content.lesson_chain", "warn",
                   f"lesson reviewer wired but not draining backlog: {stale_proposed} proposed "
                   f">48h still unreviewed: {detail}",
                   "lesson_reviewer is registered but has promoted nothing — check the "
                   "lesson_reviewer cron job / LLM availability")
    else:
        # Wired, and the only proposed lessons are recent — honestly awaiting review.
        report.add("content.lesson_chain", "info",
                   f"lesson reviewer wired; {cnt.get('proposed', 0)} proposed awaiting "
                   f"evidence-gated review (cron 03:00 CST): {detail}")


def _probe_content_lesson_guards(report: Report) -> None:
    """Accepted ps_lessons: dry-run vs active-blocking vs expired guard status.

    Lessons with block_* actions directly affect what trades the risk engine allows.
    Showing guard activation state makes risk behavior changes observable in doctor.
    """
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        lessons = db.execute(
            "SELECT lesson_id, dry_run_until, expires_at FROM ps_lessons WHERE status = 'accepted'"
        ).fetchall()
    except Exception as e:
        report.add_probe_error("content.lesson_guards", e)
        return
    if not lessons:
        report.add("content.lesson_guards", "ok", "no accepted lessons (no active risk guards)")
        return
    now_iso = datetime.now(timezone.utc).isoformat()
    dry_run, blocking, expired = 0, 0, 0
    for row in lessons:
        if row["expires_at"] and now_iso > row["expires_at"]:
            expired += 1
        elif row["dry_run_until"] and now_iso < row["dry_run_until"]:
            dry_run += 1
        else:
            blocking += 1
    total = len(lessons)
    detail = f"{total} accepted: {blocking} blocking, {dry_run} dry-run, {expired} expired"
    if blocking > 0:
        report.add("content.lesson_guards", "info",
                   detail,
                   f"{blocking} lesson(s) are actively blocking trades via the risk engine")
    else:
        report.add("content.lesson_guards", "ok", detail)


def _probe_content_attribution(report: Report) -> None:
    """opportunity_id NULL rate on the RECENT ranker output (last ~200 signal_pending
    decisions) — the live attribution-health signal (LP-S07).

    Deliberately NOT a 7d / all-time aggregate: those are dominated by pre-fix NULL
    backlog that auto-prunes (observed 89% NULL over 7d while the most-recent 200 are
    0% NULL), so a window-based aggregate would false-alarm forever. signal_pending is
    the row where the ranker links the opportunity; a healthy ranker leaves it set.
    """
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        rows = list(db.execute(
            "SELECT opportunity_id FROM pi_decisions WHERE action='signal_pending' "
            "AND (reasoning IS NULL OR reasoning NOT LIKE 'RuleSet entry:%') "
            "ORDER BY created_at DESC LIMIT 200"
        ))
    except Exception as e:
        report.add_probe_error("content.attribution", e)
        return
    total = len(rows)
    if total < 20:
        report.add("content.attribution", "info",
                   f"only {total} recent signal_pending decisions — too few to judge attribution")
        return
    nulls = sum(1 for r in rows if r["opportunity_id"] is None)
    rate = nulls / total
    detail = f"recent {total} signal_pending: {nulls} missing opportunity_id ({rate:.0%})"
    if rate > 0.10:
        report.add("content.attribution", "warn",
                   f"broken attribution chain: {detail}",
                   "recent ranker output is dropping opportunity_id — learning can't attribute "
                   "outcomes (regression of the b9ad957 fix; check trading_decision opportunity_id wiring)")
    else:
        report.add("content.attribution", "ok", detail)


def _probe_content_shadow_trades(report: Report) -> None:
    """Shadow trades whose counterfactual pnl never lands — edge-lab analysis
    silently loses that sample.

    The unresolved rows split by `outcome_candidate_key`, and the two halves need
    DIFFERENT remedies, so a bare count would send the operator to the wrong tool:

    * key IS NULL — pre-b9ad957 orphans whose parent position carries no
      opportunity_id. `resolve_stale_shadows` (and `scripts/maintenance/backfill_shadow_trades.py`
      on top of it) resolve these by their own historical horizon.
    * key IS NOT NULL — was resolved by `settle_linked_shadow_outcomes()`, which
      fired when the linked 24h `filter_candidate_outcomes` row settled. That
      settler was retired with the rest of the shadow storage surface (#3708) and
      the table is a tombstone awaiting its drop migration, so these rows have no
      owner left. Both backfill paths SELECT `outcome_candidate_key IS NULL`, so
      neither can move a single one of them either.
    """
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        total = db.execute(
            "SELECT COUNT(*) FROM shadow_trades WHERE invalidated_at IS NULL"
        ).fetchone()[0]
        if total == 0:
            report.add("content.shadow_trades", "info", "no shadow trades yet")
            return
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        row = db.execute(
            "SELECT COUNT(*) AS stale, "
            "SUM(CASE WHEN outcome_candidate_key IS NULL THEN 1 ELSE 0 END) AS orphaned "
            "FROM shadow_trades "
            "WHERE invalidated_at IS NULL AND resolved_at IS NULL AND created_at < ?",
            (cutoff,),
        ).fetchone()
        stale = int(row["stale"] or 0)
        orphaned = int(row["orphaned"] or 0)
    except Exception as e:
        report.add_probe_error("content.shadow_trades", e)
        return
    linked = stale - orphaned
    stale_pct = stale / total if total else 0.0
    if stale == 0:
        report.add("content.shadow_trades", "ok", f"{total} shadow trades, none stale")
        return
    # Point at the remedy that can actually move the dominant half.
    if linked > orphaned:
        hint = ("these carry an outcome_candidate_key, so they resolve only when their "
                "linked 24h filter_candidate_outcomes row settles — check "
                "data.filter_evidence stale_overdue; the backfill script excludes them")
    else:
        hint = ("run scripts/maintenance/backfill_shadow_trades.py for the orphaned backlog; the "
                "shadow_resolver job catches up new ones (resolve_stale_shadows)")
    split = f"{orphaned} orphaned, {linked} awaiting 24h filter-outcome settlement"
    if stale_pct > 0.25:
        report.add("content.shadow_trades", "warn",
                   f"{stale} shadow trades unresolved >24h ({stale_pct:.0%} of {total}; "
                   f"{split}) — counterfactual pnl never computed",
                   hint)
    else:
        # Bounded residue: symbols that left the universe have no OHLCV to resolve
        # against, so a small unresolved tail is honest, not a systemic failure.
        report.add("content.shadow_trades", "info",
                   f"{stale} shadow trades unresolved >24h ({stale_pct:.0%} of {total}; "
                   f"{split}) — bounded residue (delisted/out-of-universe symbols lack "
                   "OHLCV to resolve)",
                   hint)


def _probe_content_signal_settlement(report: Report) -> None:
    """filter_candidate_outcomes that are past due_at but still pending — the outcome
    settler job is stalled, so signal effectiveness silently stops updating."""
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        total = db.execute("SELECT COUNT(*) FROM filter_candidate_outcomes").fetchone()[0]
        if total == 0:
            report.add("content.signal_settlement", "info", "no forward outcomes yet")
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        overdue = db.execute(
            "SELECT COUNT(*) FROM filter_candidate_outcomes "
            "WHERE status='pending' AND due_at IS NOT NULL AND due_at < ?",
            (now_iso,),
        ).fetchone()[0]
        oldest_due = None
        if overdue > 0:
            oldest_due = db.execute(
                "SELECT MIN(due_at) FROM filter_candidate_outcomes "
                "WHERE status='pending' AND due_at IS NOT NULL AND due_at < ?",
                (now_iso,),
            ).fetchone()[0]
    except Exception as e:
        report.add_probe_error("content.signal_settlement", e)
        return
    if overdue > 0:
        oldest_age = _age_minutes(oldest_due)
        # outcome_settler runs every 15 min; allow one cadence plus scheduling slack.
        if (
            oldest_age is not None
            and oldest_age > _OUTCOME_SETTLER_GRACE_MINUTES
        ):
            report.add("content.signal_settlement", "warn",
                       f"{overdue} forward outcomes past due_at, oldest {_fmt_age(oldest_age)} — settler stalled",
                       "outcome settler job is not closing due outcomes; check its scheduler cadence")
        else:
            report.add("content.signal_settlement", "info",
                       f"{overdue} outcomes past due_at, oldest {_fmt_age(oldest_age)} — within settler cadence")
    else:
        report.add("content.signal_settlement", "ok", f"{total} forward outcomes, settlement current")


def _probe_content_proposal_bias(report: Report) -> None:
    """Directional bias at the PROPOSAL layer (before risk). loop.concentration only
    sees fills (after risk rejection); a model that proposes only 'short' is invisible
    there if risk happens to reject a mixed subset."""
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        rows = list(db.execute(
            "SELECT direction, COUNT(*) n FROM trade_proposals "
            "WHERE created_at >= ? AND (source IS NULL OR source != 'adopted') "
            "GROUP BY direction ORDER BY n DESC",
            (cutoff,),
        ))
    except Exception as e:
        report.add_probe_error("content.proposal_bias", e)
        return
    total = sum(r["n"] for r in rows)
    if total < 20:
        report.add("content.proposal_bias", "info", f"only {total} proposals in 24h — too few to judge bias")
        return
    top_dir, top_n = rows[0]["direction"], rows[0]["n"]
    top_pct = top_n / total
    detail = f"24h proposals={total}, top: {top_dir}={top_n} ({top_pct:.0%})"
    if top_pct > 0.80:
        report.add("content.proposal_bias", "warn",
                   f"proposal-layer directional bias: {detail}",
                   "Inspect Candidate source directions and compiled Entry Plans; "
                   "historical proposal counts do not prove an active direction brake.")
    else:
        report.add("content.proposal_bias", "ok", detail)


# The content-quality probe set (Phase 2). Named once so build_report() and the
# Operator's System Health Monitor (build_content_report) cannot drift apart.
_CONTENT_PROBES = (
    _probe_content_memo_quality,
    _probe_content_lesson_chain,
    _probe_content_lesson_guards,
    _probe_content_attribution,
    _probe_content_shadow_trades,
    _probe_content_signal_settlement,
    _probe_content_proposal_bias,
)


# Define exports before importing doctor so either module can be imported first.
from aletheia.diagnostics.doctor import (
    Check,
    Report,
    _OUTCOME_SETTLER_GRACE_MINUTES,
    _age_minutes,
    _fmt_age,
    _parse_iso_datetime,
    logger,
)
