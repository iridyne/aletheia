"""Strategy Designer agent — turns opportunities/hypotheses into strategy drafts.

As of the Money Loop Reset, the designer attempts to produce an executable
RuleSet (stored in parameters["ruleset"]) that the paper_trader can validate.
Falls back to advisory text when the LLM output doesn't compile.
"""
from __future__ import annotations

import json as _json
import logging

from pydantic import BaseModel

from aletheia.agents import register_agent
from aletheia.agents.helpers import llm_structured, log_tool
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.config.loader import load_config
from aletheia.trading.instruments import tradable_symbols_from_config, observed_symbols_from_config
from aletheia.knowledge.manager import create_strategy_memory
from aletheia.trading.strategy_lifecycle import create_strategy, promote_strategy

logger = logging.getLogger("aletheia.agents.strategy_designer")

_ACTIVATION_PATH = ["research_note", "strategy_hypothesis", "paper_candidate", "paper_running"]

_VALID_DIRECTIONS = {"long", "short", "neutral"}

# Valid indicator names for LLM output validation.
_VALID_INDICATORS = {
    "close", "open", "high", "low", "volume",
    "sma", "ema", "rsi", "atr",
    "bollinger_upper", "bollinger_lower", "bollinger_mid",
    "momentum_pct", "zscore",
}
_VALID_COMPARATORS = {"lt", "gt", "lte", "gte", "cross_above", "cross_below"}


class ConditionSpec(BaseModel):
    """One condition the LLM outputs."""
    indicator: str = "close"              # indicator name
    indicator_params: dict = {}           # e.g. {"period": 14}
    comparator: str = "gt"               # lt/gt/lte/gte/cross_above/cross_below
    threshold: float | None = None        # literal value (mutually exclusive with versus)
    versus: str | None = None             # another indicator name (e.g. "sma")
    versus_params: dict = {}              # params for versus indicator


class ExecutableStrategySpec(BaseModel):
    """LLM-facing schema that can be compiled to a RuleSet."""
    name: str = "Designed Strategy"
    timeframe: str = "1h"
    direction_bias: str = "neutral"
    entry_conditions: list[ConditionSpec] = []   # AND-combined
    exit_conditions: list[ConditionSpec] = []    # OR-combined
    stop_loss_pct: float | None = None           # e.g. 2.0 = 2%
    stop_loss_atr_multiple: float | None = None  # e.g. 1.5
    rationale: str = ""


class StrategySpec(BaseModel):
    """Fallback schema (advisory only, backward compat)."""
    name: str = "Designed Strategy"
    timeframe: str = "1h"
    direction_bias: str = "neutral"
    entry_rule: str = ""
    exit_rule: str = ""
    rationale: str = ""


def _compile_ruleset(spec: dict) -> str | None:
    """Try to compile an ExecutableStrategySpec dict into a RuleSet JSON string.

    Returns the JSON string on success, or None if the spec is invalid.
    """
    from aletheia.strategy.dsl import (
        Comparator, Condition, IndicatorRef, RuleExpr, RuleSet, StopLoss,
    )
    try:
        entry_conds = spec.get("entry_conditions") or []
        exit_conds = spec.get("exit_conditions") or []
        if not entry_conds or not exit_conds:
            return None

        def _build_cond(c: dict) -> Condition | None:
            ind = c.get("indicator", "close")
            if ind not in _VALID_INDICATORS:
                return None
            comp_str = c.get("comparator", "gt")
            if comp_str not in _VALID_COMPARATORS:
                return None
            left = IndicatorRef(name=ind, params=c.get("indicator_params") or {})
            versus = c.get("versus")
            if versus and versus in _VALID_INDICATORS:
                right: float | IndicatorRef = IndicatorRef(name=versus, params=c.get("versus_params") or {})
            elif c.get("threshold") is not None:
                right = float(c["threshold"])
            else:
                return None
            return Condition(left=left, comparator=Comparator(comp_str), right=right)

        entry_compiled = [_build_cond(c) for c in entry_conds]
        exit_compiled = [_build_cond(c) for c in exit_conds]
        if None in entry_compiled or None in exit_compiled:
            return None

        entry_expr = RuleExpr(op="and", conditions=entry_compiled)  # type: ignore[arg-type]
        exit_expr = RuleExpr(op="or", conditions=exit_compiled)  # type: ignore[arg-type]

        stop: StopLoss | None = None
        if spec.get("stop_loss_atr_multiple"):
            stop = StopLoss(mode="atr_multiple", value=float(spec["stop_loss_atr_multiple"]))
        elif spec.get("stop_loss_pct"):
            stop = StopLoss(mode="fixed_pct", value=float(spec["stop_loss_pct"]))

        ruleset = RuleSet(entry=entry_expr, exit=exit_expr, stop_loss=stop)
        return ruleset.to_json()
    except Exception as e:
        logger.debug("RuleSet compilation failed: %s", e)
        return None


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

    # Experience loop (§6): consult prior high-confidence lessons + case studies +
    # guard advisories as ADVISORY design inputs. Quality-gated; degrade to nothing.
    memory_block = ""
    try:
        from aletheia.knowledge.router import build_advisory_context
        memory_block = build_advisory_context(
            role="strategy_designer",
            query="strategy design entry exit risk drawdown expectancy",
            k=3,
        )
        log_tool(run_id, "retrieve_lessons", "observe", True,
                 output=f"lessons:{1 if memory_block else 0}")
    except Exception:
        pass

    playbook_block = ""
    try:
        from aletheia.trading.playbooks import load_playbooks, format_playbook_block
        playbook_block = format_playbook_block(load_playbooks())
        log_tool(run_id, "load_playbooks", "observe", True)
    except Exception:
        pass

    kill_feedback_block = ""
    try:
        from aletheia.strategy.feedback import (
            get_recent_kill_reasons, get_compile_failures, format_kill_feedback_block,
        )
        kills = get_recent_kill_reasons(days=7, limit=5)
        compile_stats = get_compile_failures(days=7)
        kill_feedback_block = format_kill_feedback_block(kills, compile_stats)
        log_tool(run_id, "kill_feedback", "observe", True, output=f"kills:{len(kills)}")
    except Exception:
        pass

    # ── Attempt executable spec (RuleSet compilation) ──
    _EXEC_SYSTEM = (
        "You are a crypto trading strategy designer. Propose ONE concrete paper-tradable "
        "strategy for BTC/ETH/SOL.\n\n"
        "Return JSON matching this schema exactly:\n"
        "- name: short strategy name\n"
        "- timeframe: \"15m\" | \"1h\" | \"4h\"\n"
        "- direction_bias: \"long\" | \"short\" | \"neutral\"\n"
        "- entry_conditions: list of conditions (AND-combined). Each has:\n"
        "    indicator: one of close/sma/ema/rsi/atr/bollinger_upper/bollinger_lower/momentum_pct/zscore\n"
        "    indicator_params: {\"period\": N} or {}\n"
        "    comparator: lt/gt/lte/gte/cross_above/cross_below\n"
        "    threshold: number (use this OR versus, not both)\n"
        "    versus: another indicator name (e.g. \"sma\")\n"
        "    versus_params: {\"period\": N}\n"
        "- exit_conditions: list of conditions (OR-combined, same structure)\n"
        "- stop_loss_pct: number or null (e.g. 2.0 for 2%)\n"
        "- stop_loss_atr_multiple: number or null (e.g. 1.5)\n"
        "- rationale: brief explanation\n\n"
        "Keep rules concrete and conservative. Use at most 3 entry and 2 exit conditions."
        + playbook_block
    )
    spec, err = await llm_structured(
        system=_EXEC_SYSTEM,
        user=f"Tradable symbols: {', '.join(symbols) or default_symbol}. Design one candidate "
             f"strategy.{memory_block}{kill_feedback_block}",
        schema=ExecutableStrategySpec,
        max_tokens=800,
    )
    log_tool(run_id, "llm_structured", "observe", True)

    spec = spec or {}
    if err:
        errors.append(err)

    # Try to compile a RuleSet from the spec
    ruleset_json = _compile_ruleset(spec)

    # If compilation failed, fall back to advisory-only (backward compat)
    if not ruleset_json and not err:
        spec_fallback, err2 = await llm_structured(
            system="You are a crypto trading strategy designer. Propose ONE concrete paper-tradable "
                   "strategy for BTC/ETH/SOL. Return JSON with: name, timeframe, direction_bias "
                   "(long/short/neutral), entry_rule (text), exit_rule (text), rationale."
                   + playbook_block,
            user=f"Tradable symbols: {', '.join(symbols) or default_symbol}. Design one candidate "
                 f"strategy.{memory_block}{kill_feedback_block}",
            schema=StrategySpec,
            max_tokens=600,
        )
        if spec_fallback:
            spec = spec_fallback
        if err2:
            errors.append(err2)

    name = str(spec.get("name") or "Designed Strategy")[:120]
    timeframe = str(spec.get("timeframe") or "1h")[:16]
    direction = str(spec.get("direction_bias") or "neutral").lower()
    if direction not in _VALID_DIRECTIONS:
        direction = "neutral"
    entry_rule = str(spec.get("entry_rule") or spec.get("rationale") or "")[:500]
    exit_rule = str(spec.get("exit_rule") or "")[:500]
    rationale = str(spec.get("rationale") or "")[:500]

    parameters: dict = {
        "symbol": default_symbol,
        "timeframe": timeframe,
        "direction_bias": direction,
        "entry_rule": entry_rule,
        "exit_rule": exit_rule,
    }
    if ruleset_json:
        parameters["ruleset"] = ruleset_json
    description = (
        rationale
        or f"{name} ({timeframe}, {direction}). Entry: {entry_rule or 'n/a'}. Exit: {exit_rule or 'n/a'}."
    )[:1000]

    strategy = create_strategy(name=name, description=description, parameters=parameters)
    log_tool(run_id, "create_strategy_hypothesis", "observe", True, output=strategy.id)
    outputs.append(f"strategy:{strategy.id}")

    # Persist strategy card into KB (§4.2 strategy_memory: parameters + intent snapshot).
    try:
        executable_tag = "EXECUTABLE (RuleSet)" if ruleset_json else "advisory only"
        memory_content = (
            f"# {name}\n\n"
            f"**Direction**: {direction} | **Timeframe**: {timeframe} | **Symbol**: {default_symbol}\n"
            f"**Rules**: {executable_tag}\n\n"
            f"## Entry Rule\n{entry_rule or 'n/a'}\n\n"
            f"## Exit Rule\n{exit_rule or 'n/a'}\n\n"
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
