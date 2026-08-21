"""Post-trade Reviewer agent — reviews eligible closes and writes a review.

Also triggers structured case study generation and guard candidate detection
from the trade_case_builder module (deterministic, no LLM).
"""

import hashlib
import json
import logging
import time

from aletheia.agents import register_agent
from aletheia.agents.helpers import elapsed_ms, llm_complete, log_tool
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.analytics.outcome_integrity import eligible_strategy_outcomes
from aletheia.knowledge.manager import create_post_trade_review

logger = logging.getLogger("aletheia.agents.post_trade_reviewer")

_STRATEGY_ATTEMPT_LIMIT = 20
_REVIEW_INPUT_BUDGET_CHARS = 60_000
_REVIEW_POSITION_LIMIT = 10
_STRATEGY_ROUND_ROBIN_STATE_KEY = (
    "agents.post_trade_reviewer.strategy_round_robin.v1"
)
_CLOSED_STRATEGY_CTE = """
    WITH closed_strategies AS (
        SELECT
            strategy_id,
            CASE
                WHEN strategy_id IS NULL THEN '0:'
                ELSE '1:' || strategy_id
            END AS strategy_key
        FROM positions
        WHERE status = 'closed'
          AND outcome_eligible = 1
        GROUP BY strategy_id
    )
"""
_REOPENED_STRATEGY_CTE = """
    WITH reopened_strategies AS (
        SELECT DISTINCT
            strategy_id,
            CASE
                WHEN strategy_id IS NULL THEN '0:'
                ELSE '1:' || strategy_id
            END AS strategy_key
        FROM knowledge_items
        WHERE item_type = 'post_trade_review'
          AND status IN ('archived', 'deprecated')
    )
"""



def _fills_window_key(strategy_id: str | None, fills: list) -> str:
    """Stable fingerprint of evidence identities seen for a strategy.

    The legacy name is retained for compatibility. The reviewer now passes
    centrally eligible Position identities; callers that still pass Fill-like
    dictionaries keep the same order-independent hash contract.
    """
    sid = strategy_id or "global"
    fill_ids = sorted(f.get("id", "") for f in fills)
    raw = f"{sid}:{','.join(fill_ids)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _review_already_written(strategy_id: str | None, window_key: str) -> bool:
    """Return True if an active post_trade_review for this exact fills-window exists.

    Archived/deprecated reviews are excluded so they do not permanently block
    regeneration after an owner archives an invalid review (#269).
    """
    from aletheia.storage.sqlite import get_db
    tag_fragment = f"window:{window_key}"
    row = get_db().execute(
        "SELECT id FROM knowledge_items "
        "WHERE item_type = 'post_trade_review' "
        "  AND status NOT IN ('archived', 'deprecated') "
        "  AND strategy_id IS ? "
        "  AND tags LIKE ? "
        "LIMIT 1",
        (strategy_id, f"%{tag_fragment}%"),
    ).fetchone()
    return row is not None


def _reviewed_position_ids(strategy_id: str | None) -> set[str]:
    """Return Position identities covered by active (non-archived) reviews only.

    Archived/deprecated reviews are excluded so their covered Positions remain
    schedulable for regeneration (#269).
    """
    from aletheia.storage.sqlite import get_db

    rows = get_db().execute(
        "SELECT tags FROM knowledge_items "
        "WHERE item_type='post_trade_review' "
        "  AND status NOT IN ('archived', 'deprecated') "
        "  AND strategy_id IS ?",
        (strategy_id,),
    ).fetchall()
    reviewed: set[str] = set()
    for row in rows:
        try:
            tags = json.loads(row["tags"] or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(tags, list):
            continue
        reviewed.update(
            str(tag).split(":", 1)[1]
            for tag in tags
            if str(tag).startswith("position:")
        )
    return reviewed


def _closed_strategy_page(
    *,
    next_key: str | None,
    page_size: int,
) -> list[dict]:
    """Read one bounded, wrapping page of potentially eligible strategies.

    ``page_size`` includes a one-row successor sentinel.  The query groups inside
    SQLite and returns only the requested identities; Python never materializes
    the full strategy population. ``outcome_eligible`` is only a cheap necessary
    prefilter; the central Outcome Integrity reader still makes the admission
    decision. A deleted/missing cursor naturally resumes at the next lexical
    identity, then wraps to the beginning when necessary.
    """
    from aletheia.storage.sqlite import get_db

    if page_size < 1:
        return []
    db = get_db()
    cte = _CLOSED_STRATEGY_CTE

    if next_key:
        first = db.execute(
            cte
            + """
                SELECT strategy_id, strategy_key
                FROM closed_strategies
                WHERE strategy_key >= ?
                ORDER BY strategy_key ASC
                LIMIT ?
            """,
            (next_key, page_size),
        ).fetchall()
    else:
        first = db.execute(
            cte
            + """
                SELECT strategy_id, strategy_key
                FROM closed_strategies
                ORDER BY strategy_key ASC
                LIMIT ?
            """,
            (page_size,),
        ).fetchall()

    page = [dict(row) for row in first]
    if next_key and len(page) < page_size:
        wrapped = db.execute(
            cte
            + """
                SELECT strategy_id, strategy_key
                FROM closed_strategies
                WHERE strategy_key < ?
                ORDER BY strategy_key ASC
                LIMIT ?
            """,
            (next_key, page_size - len(page)),
        ).fetchall()
        page.extend(dict(row) for row in wrapped)
    return page


def _closed_strategy_prefix_page(
    *,
    frontier_key: str | None,
    cursor_key: str | None,
    page_size: int,
) -> list[dict]:
    """Read a bounded page of reopened strategies before the forward cursor.

    Archived/deprecated reviews are no longer active evidence, so their
    strategies get an independent retry cursor. Ordinary identities stay on
    the forward rotation and retain its bounded-round semantics.
    """
    if page_size < 1 or not frontier_key:
        return []
    from aletheia.storage.sqlite import get_db

    db = get_db()
    if cursor_key and cursor_key < frontier_key:
        first = db.execute(
            _REOPENED_STRATEGY_CTE
            + """
                SELECT strategy_id, strategy_key
                FROM reopened_strategies
                WHERE strategy_key >= ? AND strategy_key < ?
                ORDER BY strategy_key ASC
                LIMIT ?
            """,
            (cursor_key, frontier_key, page_size),
        ).fetchall()
        page = [dict(row) for row in first]
        if len(page) < page_size:
            wrapped = db.execute(
                _REOPENED_STRATEGY_CTE
                + """
                    SELECT strategy_id, strategy_key
                    FROM reopened_strategies
                    WHERE strategy_key < ?
                    ORDER BY strategy_key ASC
                    LIMIT ?
                """,
                (cursor_key, page_size - len(page)),
            ).fetchall()
            page.extend(dict(row) for row in wrapped)
        return page

    rows = db.execute(
        _REOPENED_STRATEGY_CTE
        + """
            SELECT strategy_id, strategy_key
            FROM reopened_strategies
            WHERE strategy_key < ?
            ORDER BY strategy_key ASC
            LIMIT ?
        """,
        (frontier_key, page_size),
    ).fetchall()
    return [dict(row) for row in rows]



@register_agent("post_trade_reviewer")
async def run_post_trade_reviewer(permission_mode: str = "Observe", run_id: str | None = None, **kwargs) -> dict:
    """Review centrally eligible closed outcomes grouped by strategy."""
    from aletheia.learning.opportunity_information_policy import (
        ensure_opportunity_information_policy_epoch,
    )

    ensure_opportunity_information_policy_epoch()
    outputs: list[str] = []
    errors: list[str] = []
    artifact_refs: list[dict[str, str]] = []

    if not is_tool_permitted("create_post_trade_review", permission_mode):
        errors.append("create_post_trade_review not permitted in current mode")
        return {"outputs": outputs, "errors": errors}

    from aletheia.agents.round_robin import DurableRoundRobin

    round_robin = DurableRoundRobin(_STRATEGY_ROUND_ROBIN_STATE_KEY)
    started_at = time.monotonic()
    frontier_key = round_robin.next_key()
    prefix_cursor = round_robin.prefix_key()
    if frontier_key and prefix_cursor and prefix_cursor >= frontier_key:
        round_robin.reset_prefix_key()
        prefix_cursor = None
    strategy_page = _closed_strategy_page(
        next_key=frontier_key,
        page_size=_STRATEGY_ATTEMPT_LIMIT + 1,
    )
    prefix_page = _closed_strategy_prefix_page(
        frontier_key=frontier_key,
        cursor_key=prefix_cursor,
        page_size=_STRATEGY_ATTEMPT_LIMIT + 1,
    )
    if prefix_cursor is not None and not prefix_page:
        round_robin.reset_prefix_key()
    prefix_rows = prefix_page[:_STRATEGY_ATTEMPT_LIMIT]
    prefix_keys = {row["strategy_key"] for row in prefix_rows}
    forward_rows = [
        row
        for row in strategy_page[:_STRATEGY_ATTEMPT_LIMIT]
        if row["strategy_key"] not in prefix_keys
    ]
    strategy_attempts: list[tuple[dict, bool]] = []
    for index in range(max(len(prefix_rows), len(forward_rows))):
        if len(strategy_attempts) >= _STRATEGY_ATTEMPT_LIMIT:
            break
        if index < len(prefix_rows):
            strategy_attempts.append((prefix_rows[index], True))
        if len(strategy_attempts) >= _STRATEGY_ATTEMPT_LIMIT:
            break
        if index < len(forward_rows):
            strategy_attempts.append((forward_rows[index], False))
    prefix_scan_exhausted = len(prefix_page) <= _STRATEGY_ATTEMPT_LIMIT
    log_tool(run_id, "get_positions", "observe", True, duration_ms=elapsed_ms(started_at))

    eligible_window_seen = False
    eligibility_read_failed = False
    # Each page includes a one-row successor sentinel. Forward and prefix
    # cursors advance independently, so a growing suffix cannot starve retries.
    # The forward sentinel preserves the next bounded cycle without enumerating
    # the complete strategy population.
    for strategy_row, prefix_owned in strategy_attempts:

        strategy_id = strategy_row["strategy_id"]
        strategy_key = strategy_row["strategy_key"]
        completed_attempt = False
        try:
            strat_outcomes = eligible_strategy_outcomes(
                strategy_id=strategy_id,
                limit=200,
            )
            if not strat_outcomes:
                completed_attempt = True
                continue
            eligible_window_seen = True
            reviewed_ids = _reviewed_position_ids(strategy_id)
            strat_outcomes = [
                outcome for outcome in strat_outcomes
                if str(outcome["id"]) not in reviewed_ids
            ]
            if not strat_outcomes:
                outputs.append(
                    f"post_trade_review_skipped:already_reviewed:{strategy_id}"
                )
                completed_attempt = True
                continue

            from aletheia.analytics.trade_attribution import proposal_decision_context

            prompt_suffix = "\n\nWrite a post-trade review."
            header = (
                f"Strategy: {strategy_id or 'global'}. "
                f"Eligible closed outcomes: {len(strat_outcomes)}."
                "\n\nPosition Details:\n"
            )
            coverage_reserve = len(
                f"Included complete position records: {len(strat_outcomes)}. "
                f"Omitted complete position records: {len(strat_outcomes)} "
                "(position limit or input budget; no selected record was truncated)."
            ) + 1
            detail_budget = max(
                0,
                _REVIEW_INPUT_BUDGET_CHARS
                - len(header)
                - len(prompt_suffix)
                - coverage_reserve,
            )
            position_details = []
            selected_positions: list[dict] = []
            used_detail_chars = 0
            for position in strat_outcomes:
                if len(position_details) >= _REVIEW_POSITION_LIMIT:
                    break

                decision_context = proposal_decision_context(
                    position.get("proposal_id")
                )
                detail = (
                    f"id={position.get('id', '?')} "
                    f"{position.get('symbol', '?')} {position.get('direction', '?')} "
                    f"entry={position.get('entry_price')} "
                    f"exit={position.get('current_price')} "
                    f"net_pnl={position.get('realized_pnl')} "
                    f"regime={position.get('regime_tag', '?')}"
                )
                entry_ctx = position.get("entry_market_summary") or ""
                if entry_ctx:
                    detail += f" entry_context={entry_ctx}"
                for label, field in (
                    ("decision_context_status", "decision_context_status"),
                    ("pi_reasoning", "pi_decision_reasoning"),
                    ("bear_case", "pi_decision_bear_case"),
                    ("assumptions", "pi_decision_assumptions"),
                    ("proposal_rationale", "proposal_agent_rationale"),
                    ("evidence_manifest", "decision_evidence_manifest"),
                    ("learning_manifest", "decision_learning_manifest"),
                ):
                    value = decision_context[field]
                    if isinstance(value, (dict, list)):
                        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
                    detail += f"\n  {label}={value}"
                separator_chars = 1 if position_details else 0
                if len(detail) + separator_chars > detail_budget - used_detail_chars:
                    continue
                position_details.append(detail)
                selected_positions.append(position)
                used_detail_chars += len(detail) + separator_chars
            omitted_count = len(strat_outcomes) - len(position_details)
            coverage_line = (
                f"Included complete position records: {len(position_details)}. "
                f"Omitted complete position records: {omitted_count} "
                "(position limit or input budget; no selected record was truncated)."
            )
            positions_block = (
                "\n".join(position_details)
                if position_details
                else "No position details available."
            )
            ctx = f"{header}{coverage_line}\n{positions_block}"

            if not selected_positions:
                outputs.append(
                    f"post_trade_review_skipped:no_complete_records_fit:{strategy_id}"
                )
                completed_attempt = True
                continue

            window_items = [
                {"id": f"position:{position['id']}"}
                for position in selected_positions
            ]
            window_key = _fills_window_key(strategy_id, window_items)
            if _review_already_written(strategy_id, window_key):
                logger.debug(
                    "post_trade_reviewer: skip strategy=%s window=%s (already reviewed)",
                    strategy_id, window_key,
                )
                outputs.append(
                    f"post_trade_review_skipped:already_reviewed:{strategy_id}"
                )
                completed_attempt = True
                continue

            content, err = await llm_complete(
                system=(
                    "You are a post-trade reviewer for an autonomous crypto paper-trading system. "
                    "You are given recent fills and their position outcomes (entry/exit price, realized "
                    "PnL, market regime, entry context). Write a short review focused on SIGNAL QUALITY "
                    "and DECISION-MAKING: was the direction correct, was the entry timing good, did the "
                    "thesis play out, which pattern should be repeated or avoided next time. "
                    "Judge the TRADE DECISION, not execution microstructure — this is a market-order "
                    "system with no VWAP/smart-routing/slippage desk, so do NOT invent execution-quality "
                    "commentary. If PnL is 'open', say the trade is unresolved rather than guessing. "
                    "Under 200 words."
                ),
                user=f"{ctx}{prompt_suffix}",
                max_tokens=600,
                caller="post_trade_reviewer.review",
            )
            log_tool(run_id, "llm_complete", "observe", True)
            if err:
                errors.append(err)
                logger.info(
                    "post_trade_reviewer: LLM unavailable for strategy=%s, skipping write",
                    strategy_id,
                )
                completed_attempt = True
                continue

            # C1 (LEARNING_LOOP_AUDIT §R1): a post_trade_review is a SOFT LLM
            # reflection, not an attributed hard case. Keep it medium_confidence
            # so the retrieval quality gate is led by real trade case studies.
            started_at = time.monotonic()
            item = create_post_trade_review(
                "Post-Trade Review",
                content,
                metadata={"source": "post_trade_reviewer"},
                strategy_id=strategy_id,
                confidence="medium_confidence",
                tags=[
                    f"window:{window_key}",
                    *[
                        f"position:{position['id']}"
                        for position in selected_positions
                    ],
                ],
            )
            if item:
                log_tool(
                    run_id,
                    "create_post_trade_review",
                    "observe",
                    True,
                    output=item.id,
                    duration_ms=elapsed_ms(started_at),
                )
                outputs.append(f"post_trade_review:{item.id}")
                artifact_refs.append({"artifact_type": "post_trade_review", "artifact_id": item.id})
            completed_attempt = True
        except Exception as exc:
            eligibility_read_failed = True
            completed_attempt = True
            errors.append(f"strategy_review_failed:{strategy_key}:{exc}")
            logger.warning(
                "post_trade_reviewer: strategy attempt failed strategy=%s: %s",
                strategy_id,
                exc,
            )
        finally:
            # asyncio cancellation does not enter the Exception handler, so an
            # interrupted in-flight strategy is retried. Completed skips and
            # failures advance the appropriate cursor, preventing one bad
            # identity from blocking later strategies forever.
            if completed_attempt:
                if prefix_owned:
                    is_last_prefix_row = bool(
                        prefix_rows
                        and strategy_key == prefix_rows[-1]["strategy_key"]
                    )
                    if prefix_scan_exhausted and is_last_prefix_row:
                        round_robin.reset_prefix_key()
                    else:
                        round_robin.advance_prefix_after(
                            prefix_page,
                            completed_key=strategy_key,
                            key=lambda row: row["strategy_key"],
                        )
                else:
                    round_robin.advance_after(
                        strategy_page,
                        completed_key=strategy_key,
                        key=lambda row: row["strategy_key"],
                    )

    if not eligible_window_seen and not eligibility_read_failed:
        if len(strategy_page) <= _STRATEGY_ATTEMPT_LIMIT:
            # The bounded page covered the complete identity pool.
            outputs.append("post_trade_review_skipped:no_eligible_outcomes")
        else:
            outputs.append(
                "post_trade_review_skipped:no_eligible_outcomes_in_strategy_page"
            )

    # Phase 4: Structured case study generation + guard candidate detection
    case_studies, guard_candidates, builder_errors = _run_case_builder_safe(run_id)
    if builder_errors:
        errors.extend(builder_errors)
    if case_studies:
        outputs.append(f"case_studies_created:{len(case_studies)}")
    if guard_candidates:
        outputs.append(f"guard_candidates_created:{len(guard_candidates)}")


def _run_case_builder_safe(run_id: str | None) -> tuple[list, list, list[str]]:
    """Run trade_case_builder deterministically. Returns (cases, candidates, errors)."""
    errors = []
    cases = []
    candidates = []

    try:
        from aletheia.analytics.trade_case_builder import build_case_studies

        started_at = time.monotonic()
        result = build_case_studies(since_hours=168, skip_existing=True)
        cases = result["created"]
        per_pos_failed = result["failed"]
        log_tool(
            run_id,
            "build_case_studies",
            "observe",
            not per_pos_failed,
            output=f"{len(cases)} created, {len(per_pos_failed)} failed",
            duration_ms=elapsed_ms(started_at),
        )
        for fail in per_pos_failed:
            errors.append(f"case_builder:position_failed:{fail['position_id']}:{fail['error']}")
    except Exception as e:
        logger.warning("build_case_studies failed: %s", e, exc_info=True)
        errors.append(f"case_builder:build_case_studies:{type(e).__name__}")
        log_tool(
            run_id,
            "build_case_studies",
            "observe",
            False,
            output=f"{type(e).__name__}: {str(e)[:100]}",
            duration_ms=elapsed_ms(started_at) if 'started_at' in locals() else 0,
        )

    try:
        from aletheia.analytics.trade_case_builder import detect_guard_candidates

        started_at = time.monotonic()
        candidates = detect_guard_candidates()
        log_tool(
            run_id,
            "detect_guard_candidates",
            "observe",
            True,
            output=f"{len(candidates)} created",
            duration_ms=elapsed_ms(started_at),
        )
    except Exception as e:
        logger.warning("detect_guard_candidates failed: %s", e, exc_info=True)
        errors.append(f"case_builder:detect_guard_candidates:{type(e).__name__}")
        log_tool(
            run_id,
            "detect_guard_candidates",
            "observe",
            False,
            output=f"{type(e).__name__}: {str(e)[:100]}",
            duration_ms=elapsed_ms(started_at) if 'started_at' in locals() else 0,
        )

    return cases, candidates, errors
