"""Post-trade Reviewer agent — reviews eligible closes and writes a review.

Also triggers structured case study generation and guard candidate detection
from the trade_case_builder module (deterministic, no LLM).
"""

import hashlib
import logging

from aletheia.agents import register_agent
from aletheia.agents.helpers import llm_complete, log_tool
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.analytics.outcome_integrity import eligible_strategy_outcomes
from aletheia.knowledge.manager import create_post_trade_review

logger = logging.getLogger("aletheia.agents.post_trade_reviewer")

_STRATEGY_ATTEMPT_LIMIT = 20
_STRATEGY_ROUND_ROBIN_STATE_KEY = (
    "agents.post_trade_reviewer.strategy_round_robin.v1"
)


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
    """Return True if a post_trade_review for this exact fills-window exists."""
    from aletheia.storage.sqlite import get_db
    tag_fragment = f"window:{window_key}"
    row = get_db().execute(
        "SELECT id FROM knowledge_items "
        "WHERE item_type = 'post_trade_review' "
        "  AND strategy_id IS ? "
        "  AND tags LIKE ? "
        "LIMIT 1",
        (strategy_id, f"%{tag_fragment}%"),
    ).fetchone()
    return row is not None


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
    cte = """
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


@register_agent("post_trade_reviewer")
async def run_post_trade_reviewer(permission_mode: str = "Observe", run_id: str | None = None, **kwargs) -> dict:
    """Review centrally eligible closed outcomes grouped by strategy."""
    outputs: list[str] = []
    errors: list[str] = []

    if not is_tool_permitted("create_post_trade_review", permission_mode):
        errors.append("create_post_trade_review not permitted in current mode")
        return {"outputs": outputs, "errors": errors}

    from aletheia.agents.round_robin import DurableRoundRobin

    round_robin = DurableRoundRobin(_STRATEGY_ROUND_ROBIN_STATE_KEY)
    strategy_page = _closed_strategy_page(
        next_key=round_robin.next_key(),
        page_size=_STRATEGY_ATTEMPT_LIMIT + 1,
    )
    log_tool(run_id, "get_positions", "observe", True)
    eligible_window_seen = False
    eligibility_read_failed = False

    # The final row is only a successor sentinel. It lets advance_after persist
    # exactly where the next bounded cycle must resume without enumerating the
    # complete strategy population.
    for strategy_row in strategy_page[:_STRATEGY_ATTEMPT_LIMIT]:
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

            # Keep the historical helper/tag contract, but fingerprint eligible
            # Position identities rather than arbitrary fills.
            # Prevents redundant LLM calls when the agent runs every 4h with the
            # same static outcome pool (the historical review flood bug).
            window_items = [
                {"id": f"position:{outcome['id']}"}
                for outcome in strat_outcomes
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

            position_details = []
            for position in strat_outcomes[:10]:
                detail = (
                    f"{position.get('symbol', '?')} {position.get('direction', '?')} "
                    f"entry={position.get('entry_price')} "
                    f"exit={position.get('current_price')} "
                    f"net_pnl={position.get('realized_pnl')} "
                    f"regime={position.get('regime_tag', '?')}"
                )
                entry_ctx = position.get("entry_market_summary") or ""
                if entry_ctx:
                    detail += f" entry_context={entry_ctx[:200]}"
                position_details.append(detail)
            positions_block = (
                "\n".join(position_details)
                if position_details
                else "No position details available."
            )
            ctx = (
                f"Strategy: {strategy_id or 'global'}. "
                f"Eligible closed outcomes: {len(strat_outcomes)}."
                f"\n\nPosition Details:\n{positions_block}"
            )

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
                user=f"{ctx}\n\nWrite a post-trade review.",
                max_tokens=600,
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
            item = create_post_trade_review(
                "Post-Trade Review",
                content,
                metadata={"source": "post_trade_reviewer"},
                strategy_id=strategy_id,
                confidence="medium_confidence",
                tags=[
                    f"window:{window_key}",
                    *[
                        f"position:{outcome['id']}"
                        for outcome in strat_outcomes
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
                )
                outputs.append(f"post_trade_review:{item.id}")
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
            # failures advance, preventing one bad identity from blocking later
            # strategies forever.
            if completed_attempt:
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
    case_studies, guard_candidates = _run_case_builder_safe(run_id)
    if case_studies:
        outputs.append(f"case_studies_created:{len(case_studies)}")
    if guard_candidates:
        outputs.append(f"guard_candidates_created:{len(guard_candidates)}")

    return {"outputs": outputs, "errors": errors}


def _run_case_builder_safe(run_id: str | None) -> tuple[list, list]:
    """Run trade_case_builder deterministically. Never crashes the agent."""
    try:
        from aletheia.analytics.trade_case_builder import build_case_studies, detect_guard_candidates

        cases = build_case_studies(since_hours=168, skip_existing=True)
        log_tool(run_id, "build_case_studies", "observe", True, output=f"{len(cases)} created")

        candidates = detect_guard_candidates()
        log_tool(run_id, "detect_guard_candidates", "observe", True, output=f"{len(candidates)} created")

        return cases, candidates
    except Exception as e:
        logger.warning("Case builder failed (non-fatal): %s", e)
        return [], []
