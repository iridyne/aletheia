"""Strategy Designer agent — turns opportunities/hypotheses into strategy drafts."""

from pydantic import BaseModel

from aletheia.agents import register_agent
from aletheia.agents.helpers import llm_structured, log_tool
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.config.loader import load_config
from aletheia.trading.instruments import tradable_symbols_from_config, observed_symbols_from_config
from aletheia.knowledge.manager import create_strategy_memory
from aletheia.trading.strategy_lifecycle import create_strategy, promote_strategy

# States the designer walks a fresh strategy through so it becomes tradable.
# All of these are ungated (only paper_passed / live_* trigger promotion gates),
# so this is a legitimate auto-advance — it never touches a live gate.
_ACTIVATION_PATH = ["research_note", "strategy_hypothesis", "paper_candidate", "paper_running"]

_VALID_DIRECTIONS = {"long", "short", "neutral"}


class StrategySpec(BaseModel):
    """Structured strategy the LLM is asked to produce (degrades gracefully).

    NOTE: entry_rule/exit_rule are ADVISORY text — they document the LLM's intent
    but are NOT parsed or executed by paper_trader. Actual trade direction comes
    from opportunity.direction_bias. These fields exist for auditability only.
    """
    name: str = "Designed Strategy"
    timeframe: str = "1h"
    direction_bias: str = "neutral"
    entry_rule: str = ""  # advisory, NOT executed — see docstring
    exit_rule: str = ""   # advisory, NOT executed — see docstring
    rationale: str = ""


@register_agent("strategy_designer")
async def run_strategy_designer(permission_mode: str = "Observe", run_id: str | None = None, **kwargs) -> dict:
    """Draft a candidate strategy, persist its parameters, and advance it to paper_running.

    The strategy is created with a structured `parameters` snapshot (symbol, timeframe,
    direction, entry/exit) so downstream promotion gates and post-trade reviews can
    audit what the strategy was actually running. It is then auto-advanced through the
    ungated lifecycle states to `paper_running` so the paper trader can bind trades to
    it — closing the producer side of the strategy↔trade causal chain. Live gates are
    untouched: promotion stops at paper_running.
    """
    outputs: list[str] = []
    errors: list[str] = []

    # Designing strategies is a research-class activity (creating hypotheses/drafts).
    if not is_tool_permitted("create_strategy_hypothesis", permission_mode):
        errors.append("create_strategy_hypothesis not permitted in current mode")
        return {"outputs": outputs, "errors": errors}

    cfg = load_config()
    # In whitelist mode, use the symbol_list. In blacklist mode, use observed symbols.
    if cfg.risk.symbol_filter_mode == "whitelist":
        symbols = tradable_symbols_from_config(cfg)
    else:
        symbols = observed_symbols_from_config(cfg)
    default_symbol = symbols[0] if symbols else "BTC/USDT"

    # Experience loop (§6): consult prior high-confidence lessons + playbooks as
    # ADVISORY design inputs. Both are read-only and quality-gated; degrade to nothing.
    memory_block = ""
    try:
        from aletheia.knowledge.retrieval import retrieve_lessons, format_memory_block
        lessons = retrieve_lessons(
            query="strategy design entry exit risk drawdown expectancy",
            k=3,
            item_types=["research_note", "post_trade_review", "strategy_memory"],
        )
        memory_block = format_memory_block(lessons)
        log_tool(run_id, "retrieve_lessons", "observe", True, output=f"lessons:{len(lessons)}")
    except Exception:
        pass

    playbook_block = ""
    try:
        from aletheia.trading.playbooks import load_playbooks, format_playbook_block
        playbook_block = format_playbook_block(load_playbooks())
        log_tool(run_id, "load_playbooks", "observe", True)
    except Exception:
        pass

    spec, err = await llm_structured(
        system="You are a crypto trading strategy designer. Propose ONE concrete paper-tradable "
               "strategy for BTC/ETH/SOL. Return JSON with: name, timeframe (e.g. 15m/1h/4h), "
               "direction_bias (one of long/short/neutral), entry_rule, exit_rule, and a brief "
               "rationale. The risk engine will gate execution; keep rules concrete and conservative."
               + playbook_block,
        user=f"Tradable symbols: {', '.join(symbols) or default_symbol}. Design one candidate "
             f"strategy.{memory_block}",
        schema=StrategySpec,
        max_tokens=600,
    )
    log_tool(run_id, "llm_structured", "observe", True)

    spec = spec or {}
    if err:
        errors.append(err)

    name = str(spec.get("name") or "Designed Strategy")[:120]
    timeframe = str(spec.get("timeframe") or "1h")[:16]
    direction = str(spec.get("direction_bias") or "neutral").lower()
    if direction not in _VALID_DIRECTIONS:
        direction = "neutral"
    entry_rule = str(spec.get("entry_rule") or "")[:500]
    exit_rule = str(spec.get("exit_rule") or "")[:500]
    rationale = str(spec.get("rationale") or "")[:500]

    # Structured parameter snapshot — the auditable record of "what this strategy is".
    parameters = {
        "symbol": default_symbol,
        "timeframe": timeframe,
        "direction_bias": direction,
        "entry_rule": entry_rule,
        "exit_rule": exit_rule,
    }
    description = (
        rationale
        or f"{name} ({timeframe}, {direction}). Entry: {entry_rule or 'n/a'}. Exit: {exit_rule or 'n/a'}."
    )[:1000]

    strategy = create_strategy(name=name, description=description, parameters=parameters)
    log_tool(run_id, "create_strategy_hypothesis", "observe", True, output=strategy.id)
    outputs.append(f"strategy:{strategy.id}")

    # Persist strategy card into KB (§4.2 strategy_memory: parameters + intent snapshot).
    try:
        import json as _json
        memory_content = (
            f"# {name}\n\n"
            f"**Direction**: {direction} | **Timeframe**: {timeframe} | **Symbol**: {default_symbol}\n\n"
            f"## Entry Rule (advisory)\n{entry_rule or 'n/a'}\n\n"
            f"## Exit Rule (advisory)\n{exit_rule or 'n/a'}\n\n"
            f"## Rationale\n{rationale or 'n/a'}\n\n"
            f"## Parameters\n```json\n{_json.dumps(parameters, indent=2)}\n```\n"
        )
        create_strategy_memory(strategy.id, f"Strategy Card: {name}", memory_content)
        log_tool(run_id, "create_strategy_memory", "observe", True, output=strategy.id)
    except Exception as e:
        errors.append(f"strategy_memory_failed:{e}")

    # Advance through the ungated lifecycle states so the paper trader can bind to it.
    # Stop and record on the first refused transition rather than forcing it.
    state = "draft"
    for target in _ACTIVATION_PATH:
        result = promote_strategy(strategy.id, target)
        if not result.get("success"):
            errors.append(f"promotion_stopped_at:{state}->{target}:{result.get('error')}")
            break
        state = target
    outputs.append(f"strategy_state:{state}")

    return {"outputs": outputs, "errors": errors}
