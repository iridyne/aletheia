"""Strategy Designer agent — edge miner that designs strategies for specific universe candidates.

Consults the dynamic universe pipeline (scanner scores, features, regime) to
produce targeted executable RuleSets for high-opportunity assets rather than
generic BTC/ETH/SOL indicator combos. Falls back to advisory text when the LLM
output doesn't compile.
"""
from __future__ import annotations

import json as _json
import logging

from pydantic import BaseModel

from aletheia.agents import register_agent
from aletheia.agents.helpers import llm_structured, log_tool
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.model_provider.router import get_provider_for_tier
from aletheia.config.loader import load_config
from aletheia.trading.instruments import tradable_symbols_from_config, observed_symbols_from_config
from aletheia.knowledge.manager import create_strategy_memory
from aletheia.trading.strategy_lifecycle import create_strategy, promote_strategy

logger = logging.getLogger("aletheia.agents.strategy_designer")

_ACTIVATION_PATH = ["research_note", "strategy_hypothesis", "paper_candidate", "paper_running"]

_VALID_DIRECTIONS = {"long", "short", "neutral"}

_VALID_INDICATORS = {
    "close", "open", "high", "low", "volume",
    "sma", "ema", "rsi", "atr",
    "bollinger_upper", "bollinger_lower", "bollinger_mid",
    "momentum_pct", "zscore",
    "donchian_high", "donchian_low", "obv", "volume_sma_ratio", "vwap",
}
_VALID_COMPARATORS = {"lt", "gt", "lte", "gte", "cross_above", "cross_below"}


class ConditionSpec(BaseModel):
    """One condition the LLM outputs."""
    indicator: str = "close"
    indicator_params: dict = {}
    comparator: str = "gt"
    threshold: float | None = None
    versus: str | None = None
    versus_params: dict = {}


class ExecutableStrategySpec(BaseModel):
    """LLM-facing schema that can be compiled to a RuleSet."""
    symbol: str = ""
    name: str = "Designed Strategy"
    timeframe: str = "1h"
    direction_bias: str = "neutral"
    entry_conditions: list[ConditionSpec] = []
    exit_conditions: list[ConditionSpec] = []
    stop_loss_pct: float | None = None
    stop_loss_atr_multiple: float | None = None
    rationale: str = ""


class StrategySpec(BaseModel):
    """Fallback schema (advisory only, backward compat)."""
    symbol: str = ""
    name: str = "Designed Strategy"
    timeframe: str = "1h"
    direction_bias: str = "neutral"
    entry_rule: str = ""
    exit_rule: str = ""
    rationale: str = ""


def _build_universe_context(cfg) -> tuple[str, list[str]]:
    """Pull universe candidates + regime into a structured context block for the LLM.

    Returns (context_block, candidate_symbols). context_block is the formatted
    string to inject into the user prompt; candidate_symbols is the list of
    symbols in priority order for validation.
    """
    from aletheia.universe.service import UniverseService
    from aletheia.storage import repositories as repo

    candidate_symbols: list[str] = []
    lines: list[str] = []
    members: list[dict] = []

    # ── Universe candidates (top 8 by total_score) ──
    try:
        svc = UniverseService()
        members = svc.get_current_universe(execution_eligible_only=True, limit=8)

        # Sort uncovered symbols first: universe members without an existing strategy
        # get priority so the LLM gradually fills gaps rather than redesigning covered assets.
        try:
            from aletheia.data.symbols import to_bitget as _to_bitget
            _active_states = {"paper_running", "paper_candidate", "paper_passed", "active", "live_candidate", "live_limited"}
            _strats = repo.list_strategies(limit=200)

            def _safe_params(raw: str) -> dict:
                try:
                    return _json.loads(raw) if isinstance(raw, str) else (raw or {})
                except Exception:
                    return {}

            _covered = {
                _to_bitget(_safe_params(s.get("parameters") or "{}").get("symbol", ""))
                for s in _strats
                if s.get("state") in _active_states
                and _safe_params(s.get("parameters") or "{}").get("symbol")
            }
            members = sorted(
                members,
                key=lambda m: (
                    _to_bitget(m.get("symbol") or "") in _covered,  # False=0 sorts first
                    -(m.get("total_score") or 0),
                ),
            )
        except Exception:
            pass  # coverage sort is best-effort

        if members and members[0].get("scanner_family") != "core":
            lines.append("\n\n## UNIVERSE CANDIDATES (ranked by opportunity score)")
            lines.append("Pick ONE of these for your strategy — they have active scanner signals:\n")
            for m in members:
                sym = m.get("symbol", "?")
                candidate_symbols.append(sym)
                family = m.get("scanner_family", "?")
                score = m.get("total_score", 0)
                opp_score = m.get("opportunity_score", 0)
                reasons_raw = m.get("inclusion_reason", "[]")
                if isinstance(reasons_raw, str):
                    try:
                        reasons = _json.loads(reasons_raw)
                    except Exception:
                        reasons = []
                else:
                    reasons = reasons_raw or []
                flags_raw = m.get("risk_flags", "[]")
                if isinstance(flags_raw, str):
                    try:
                        flags = _json.loads(flags_raw)
                    except Exception:
                        flags = []
                else:
                    flags = flags_raw or []
                line = (
                    f"- **{sym}** | family={family} | total={score:.1f} | "
                    f"opportunity={opp_score:.1f}"
                )
                if reasons:
                    line += f" | reasons: {', '.join(reasons[:3])}"
                if flags:
                    line += f" | ⚠️ risks: {', '.join(flags)}"
                lines.append(line)
    except Exception as e:
        logger.debug("Universe context unavailable: %s", e)

    # ── Feature snapshot for top candidates (micro-structure context) ──
    if members and members[0].get("scanner_family") != "core":
        try:
            features_lines: list[str] = []
            for m in members[:5]:
                asset_id = m.get("asset_id")
                sym = m.get("symbol", "?")
                if not asset_id:
                    continue
                feat = repo.get_latest_feature_snapshot(asset_id)
                if feat:
                    features_lines.append(
                        f"  {sym}: RSI={feat.get('rsi_14', '?'):.1f}, "
                        f"ATR%={feat.get('atr_pct', 0)*100:.2f}%, "
                        f"ret_1h={feat.get('return_1h', 0)*100:+.2f}%, "
                        f"ret_4h={feat.get('return_4h', 0)*100:+.2f}%, "
                        f"vol_24h=${feat.get('quote_volume_24h', 0)/1e6:.1f}M, "
                        f"spread={feat.get('spread_bps', 0):.1f}bps, "
                        f"funding={feat.get('funding_rate', 0)*100:.4f}%"
                    )
            if features_lines:
                lines.append("\n## LIVE FEATURES")
                lines.extend(features_lines)
        except Exception as e:
            logger.debug("Feature snapshot unavailable: %s", e)

    # ── Market regime ──
    try:
        regime = repo.get_latest_market_regime()
        if regime:
            state = regime.get("state", "unknown")
            conf = regime.get("confidence", 0)
            long_b = regime.get("long_bias", 0.5)
            short_b = regime.get("short_bias", 0.5)
            lines.append(
                f"\n## MARKET REGIME: {state.upper()} "
                f"(confidence={conf:.0%}, long_bias={long_b:.0%}, short_bias={short_b:.0%})"
            )
            if state == "bear":
                lines.append("Prefer short or neutral strategies; long only with strong contrarian signal.")
            elif state == "bull":
                lines.append("Prefer long strategies; short only with strong contrarian signal.")
            elif state == "volatile":
                lines.append("Prefer tight stops and shorter timeframes; avoid wide ATR-based exits.")
    except Exception as e:
        logger.debug("Regime context unavailable: %s", e)

    return "\n".join(lines), candidate_symbols


def _resolve_symbol(spec: dict, candidate_symbols: list[str], fallback: str) -> str:
    """Extract the symbol the LLM chose, validating against candidates."""
    chosen = str(spec.get("symbol") or "").strip().upper()
    if not chosen:
        return candidate_symbols[0] if candidate_symbols else fallback

    # Normalize: accept both "BTCUSDT" and "BTC/USDT" forms
    canonical = chosen if "/" in chosen else None
    if not canonical:
        for cs in candidate_symbols:
            if cs.replace("/", "") == chosen:
                canonical = cs
                break
    if not canonical:
        canonical = chosen

    # Validate against candidates if we have them
    if candidate_symbols:
        if canonical in candidate_symbols:
            return canonical
        # Fuzzy match: maybe LLM returned slightly different form
        for cs in candidate_symbols:
            if cs.replace("/", "") == canonical.replace("/", ""):
                return cs
        # LLM picked something outside candidates — still respect it
        logger.info("LLM chose %s outside universe candidates; accepting", canonical)

    return canonical


def _compile_ruleset(spec: dict) -> tuple[str | None, str]:
    """Try to compile an ExecutableStrategySpec dict into a RuleSet JSON string.

    Returns (json_string, diagnosis). json_string is None on failure;
    diagnosis explains what went wrong (empty string on success).
    """
    from aletheia.strategy.dsl import (
        Comparator, Condition, IndicatorRef, RuleExpr, RuleSet, StopLoss,
    )
    try:
        entry_conds = spec.get("entry_conditions") or []
        exit_conds = spec.get("exit_conditions") or []
        if not entry_conds:
            return None, "no entry_conditions provided"
        if not exit_conds:
            return None, "no exit_conditions provided"

        def _build_cond(c: dict, label: str) -> tuple[Condition | None, str]:
            ind = c.get("indicator", "close")
            if ind not in _VALID_INDICATORS:
                return None, f"{label}: invalid indicator '{ind}'. Valid: {sorted(_VALID_INDICATORS)}"
            comp_str = c.get("comparator", "gt")
            if comp_str not in _VALID_COMPARATORS:
                return None, f"{label}: invalid comparator '{comp_str}'. Valid: {sorted(_VALID_COMPARATORS)}"
            left = IndicatorRef(name=ind, params=c.get("indicator_params") or {})
            versus = c.get("versus")
            if versus and versus in _VALID_INDICATORS:
                right: float | IndicatorRef = IndicatorRef(name=versus, params=c.get("versus_params") or {})
            elif c.get("threshold") is not None:
                right = float(c["threshold"])
            else:
                reason = f"{label}: needs 'threshold' (number) or 'versus' (valid indicator)"
                if versus:
                    reason += f". Got versus='{versus}' which is not in valid indicators"
                return None, reason
            tf = c.get("timeframe") or None
            return Condition(left=left, comparator=Comparator(comp_str), right=right, timeframe=tf), ""

        entry_compiled = []
        for i, c in enumerate(entry_conds):
            cond, reason = _build_cond(c, f"entry[{i}]")
            if cond is None:
                return None, reason
            entry_compiled.append(cond)

        exit_compiled = []
        for i, c in enumerate(exit_conds):
            cond, reason = _build_cond(c, f"exit[{i}]")
            if cond is None:
                return None, reason
            exit_compiled.append(cond)

        entry_expr = RuleExpr(op="and", conditions=entry_compiled)  # type: ignore[arg-type]
        exit_expr = RuleExpr(op="or", conditions=exit_compiled)  # type: ignore[arg-type]

        stop: StopLoss | None = None
        if spec.get("stop_loss_atr_multiple"):
            stop = StopLoss(mode="atr_multiple", value=float(spec["stop_loss_atr_multiple"]))
        elif spec.get("stop_loss_pct"):
            stop = StopLoss(mode="fixed_pct", value=float(spec["stop_loss_pct"]))

        ruleset = RuleSet(entry=entry_expr, exit=exit_expr, stop_loss=stop)
        return ruleset.to_json(), ""
    except Exception as e:
        logger.debug("RuleSet compilation failed: %s", e)
        return None, f"exception: {e}"


@register_agent("strategy_designer")
async def run_strategy_designer(permission_mode: str = "Observe", run_id: str | None = None, **kwargs) -> dict:
    """Mine edges from universe candidates and produce targeted executable strategies.

    Pulls the current dynamic universe (scanner scores, features, regime) and
    asks the LLM to design a strategy for a specific high-opportunity asset
    rather than generating generic indicator combos.
    """
    outputs: list[str] = []
    errors: list[str] = []

    if not is_tool_permitted("create_strategy_hypothesis", permission_mode):
        errors.append("create_strategy_hypothesis not permitted in current mode")
        return {"outputs": outputs, "errors": errors}

    cfg = load_config()

    # ── Gather universe context (primary symbol source) ──
    universe_context, candidate_symbols = _build_universe_context(cfg)
    log_tool(run_id, "universe_context", "observe", True,
             output=f"candidates:{len(candidate_symbols)}")

    # Fallback to config symbols only if universe is empty/disabled
    if not candidate_symbols:
        if cfg.risk.symbol_filter_mode == "whitelist":
            candidate_symbols = tradable_symbols_from_config(cfg)
        else:
            candidate_symbols = observed_symbols_from_config(cfg)
    fallback_symbol = candidate_symbols[0] if candidate_symbols else "BTC/USDT"

    # ── Experience loop ──
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
    winning_block = ""
    try:
        from aletheia.strategy.feedback import (
            get_recent_kill_reasons, get_compile_failures, format_kill_feedback_block,
            get_winning_strategy_templates, format_winning_templates_block,
        )
        kills = get_recent_kill_reasons(days=7, limit=5)
        compile_stats = get_compile_failures(days=7)
        kill_feedback_block = format_kill_feedback_block(kills, compile_stats)
        winners = get_winning_strategy_templates(days=30, limit=3)
        winning_block = format_winning_templates_block(winners)
        log_tool(run_id, "kill_feedback", "observe", True,
                 output=f"kills:{len(kills)},winners:{len(winners)}")
    except Exception:
        pass

    # ── Attempt executable spec (RuleSet compilation) ──
    _EXEC_SYSTEM = (
        "You are a crypto edge miner. Your job is NOT to design generic indicator strategies. "
        "Instead, you exploit specific microstructure opportunities identified by scanners.\n\n"
        "RULES:\n"
        "1. You MUST pick exactly ONE symbol from the candidates provided\n"
        "2. Your strategy must exploit the specific edge the scanner identified for that asset "
        "(momentum continuation, mean-reversion, volatility breakout, derivatives imbalance)\n"
        "3. Entry/exit conditions must be calibrated to the asset's CURRENT features "
        "(use the RSI, ATR%, return values provided)\n"
        "4. Respect the market regime constraint\n\n"
        "Return JSON matching this schema exactly:\n"
        "- symbol: the canonical symbol you chose (e.g. \"ETH/USDT\")\n"
        "- name: short strategy name referencing the asset and edge type\n"
        "- timeframe: \"15m\" | \"1h\" | \"4h\"\n"
        "- direction_bias: \"long\" | \"short\" | \"neutral\"\n"
        "- entry_conditions: list of conditions (AND-combined). Each has:\n"
        "    indicator: one of close/open/high/low/volume/sma/ema/rsi/atr/bollinger_upper/bollinger_lower/momentum_pct/zscore/donchian_high/donchian_low/obv/volume_sma_ratio/vwap\n"
        "    indicator_params: {\"period\": N} or {}\n"
        "    comparator: lt/gt/lte/gte/cross_above/cross_below\n"
        "    threshold: number (use this OR versus, not both)\n"
        "    versus: another indicator name (e.g. \"sma\")\n"
        "    versus_params: {\"period\": N}\n"
        "    timeframe: optional, e.g. \"4h\" or \"1d\" — evaluate this condition on a different timeframe "
        "(use for multi-TF confluence: e.g. confirm 4h trend before entering on 1h)\n"
        "- exit_conditions: list of conditions (OR-combined, same structure)\n"
        "- stop_loss_pct: number or null (e.g. 2.0 for 2%)\n"
        "- stop_loss_atr_multiple: number or null (e.g. 1.5)\n"
        "- rationale: WHY this edge exists for THIS asset right now (not generic TA theory)\n\n"
        "Keep rules concrete. Use at most 3 entry and 2 exit conditions. "
        "Calibrate thresholds to the live features shown."
        + playbook_block
    )
    spec, err = await llm_structured(
        system=_EXEC_SYSTEM,
        user=(
            f"Design one targeted strategy for the highest-opportunity candidate below."
            f"{universe_context}{memory_block}{kill_feedback_block}{winning_block}"
        ),
        schema=ExecutableStrategySpec,
        max_tokens=900,
        provider=get_provider_for_tier("deep"),
    )
    log_tool(run_id, "llm_structured", "observe", True)

    spec = spec or {}
    if err:
        errors.append(err)

    ruleset_json, compile_diagnosis = _compile_ruleset(spec)

    # Retry once with error feedback if compilation failed but LLM call succeeded
    if not ruleset_json and not err and compile_diagnosis:
        logger.info("RuleSet compile failed (%s), retrying with feedback", compile_diagnosis)
        retry_spec, retry_err = await llm_structured(
            system=_EXEC_SYSTEM,
            user=(
                f"Design one targeted strategy for the highest-opportunity candidate below."
                f"{universe_context}{memory_block}{kill_feedback_block}\n\n"
                f"IMPORTANT: Your previous attempt failed to compile. Error: {compile_diagnosis}\n"
                f"Fix the issue. Valid indicators: {sorted(_VALID_INDICATORS)}. "
                f"Valid comparators: {sorted(_VALID_COMPARATORS)}. "
                f"Each condition MUST have either 'threshold' (a number) or 'versus' (a valid indicator name)."
            ),
            schema=ExecutableStrategySpec,
            max_tokens=900,
            provider=get_provider_for_tier("deep"),
        )
        if retry_spec and not retry_err:
            ruleset_json, compile_diagnosis = _compile_ruleset(retry_spec)
            if ruleset_json:
                spec = retry_spec
                log_tool(run_id, "compile_retry_success", "observe", True)
            else:
                errors.append(f"compile_retry_failed:{compile_diagnosis}")
        elif retry_err:
            errors.append(f"compile_retry_llm_error:{retry_err}")

    # If still no RuleSet, fall back to advisory-only
    if not ruleset_json and not err:
        spec_fallback, err2 = await llm_structured(
            system=(
                "You are a crypto edge miner. Pick ONE symbol from the candidates and design "
                "a targeted strategy exploiting its specific scanner signal. "
                "Return JSON with: symbol, name, timeframe, direction_bias "
                "(long/short/neutral), entry_rule (text), exit_rule (text), rationale."
                + playbook_block
            ),
            user=(
                f"Design one targeted strategy."
                f"{universe_context}{memory_block}{kill_feedback_block}"
            ),
            schema=StrategySpec,
            max_tokens=600,
            provider=get_provider_for_tier("deep"),
        )
        if spec_fallback:
            spec = spec_fallback
        if err2:
            errors.append(err2)

    # ── Extract fields, resolving symbol from LLM output ──
    chosen_symbol = _resolve_symbol(spec, candidate_symbols, fallback_symbol)
    name = str(spec.get("name") or "Designed Strategy")[:120]
    timeframe = str(spec.get("timeframe") or "1h")[:16]
    direction = str(spec.get("direction_bias") or "neutral").lower()
    if direction not in _VALID_DIRECTIONS:
        direction = "neutral"
    entry_rule = str(spec.get("entry_rule") or spec.get("rationale") or "")[:500]
    exit_rule = str(spec.get("exit_rule") or "")[:500]
    rationale = str(spec.get("rationale") or "")[:500]

    parameters: dict = {
        "symbol": chosen_symbol,
        "timeframe": timeframe,
        "direction_bias": direction,
        "entry_rule": entry_rule,
        "exit_rule": exit_rule,
    }
    if ruleset_json:
        parameters["ruleset"] = ruleset_json

    # Attach universe provenance for traceability
    if candidate_symbols and chosen_symbol in candidate_symbols:
        parameters["source"] = "universe_scanner"
    else:
        parameters["source"] = "config_fallback"

    description = (
        rationale
        or f"{name} ({timeframe}, {direction}). Entry: {entry_rule or 'n/a'}. Exit: {exit_rule or 'n/a'}."
    )[:1000]

    # Dedup: skip if same symbol+timeframe+direction already has an active strategy
    from aletheia.storage.repositories import list_strategies as _list_strats
    _existing = _list_strats(limit=200)
    _active_keys = set()
    for _s in _existing:
        if _s.get("state") in ("paper_running", "paper_candidate", "paper_passed"):
            try:
                _p = _json.loads(_s.get("parameters") or "{}")
                _key = (_p.get("symbol"), _p.get("timeframe"), _p.get("direction_bias"))
                _active_keys.add(_key)
            except (ValueError, TypeError):
                pass
    _new_key = (chosen_symbol, timeframe, direction)
    if _new_key in _active_keys and ruleset_json:
        outputs.append(f"dedup_skipped:{chosen_symbol}:{timeframe}:{direction}")
        return {"outputs": outputs, "errors": errors}

    # Map direction_bias → direction_affinity for StrategyRecord model field.
    # "neutral" has no directional preference, maps to "both".
    _affinity_map = {"long": "long", "short": "short", "neutral": "both"}
    strategy = create_strategy(
        name=name,
        description=description,
        parameters=parameters,
        direction_affinity=_affinity_map.get(direction, "both"),
    )
    log_tool(run_id, "create_strategy_hypothesis", "observe", True, output=strategy.id)
    outputs.append(f"strategy:{strategy.id}")

    # Persist strategy card into KB
    try:
        executable_tag = "EXECUTABLE (RuleSet)" if ruleset_json else "advisory only"
        source_tag = parameters.get("source", "unknown")
        memory_content = (
            f"# {name}\n\n"
            f"**Direction**: {direction} | **Timeframe**: {timeframe} | **Symbol**: {chosen_symbol}\n"
            f"**Rules**: {executable_tag} | **Source**: {source_tag}\n\n"
            f"## Entry Rule\n{entry_rule or 'n/a'}\n\n"
            f"## Exit Rule\n{exit_rule or 'n/a'}\n\n"
            f"## Rationale\n{rationale or 'n/a'}\n\n"
            f"## Parameters\n```json\n{_json.dumps(parameters, indent=2)}\n```\n"
        )
        create_strategy_memory(strategy.id, f"Strategy Card: {name}", memory_content)
        log_tool(run_id, "create_strategy_memory", "observe", True, output=strategy.id)
    except Exception as e:
        errors.append(f"strategy_memory_failed:{e}")

    # ── Backtest gate: instant kill if strategy fails historical test ──
    backtest_killed = False
    _cached_bars = None  # shared between gate and sweep to avoid double I/O
    if ruleset_json and cfg.strategy_factory.backtest_gate_enabled:
        from aletheia.strategy.backtest import BacktestConfig as _BtCfg, load_bars_from_db, run_backtest
        from aletheia.strategy.dsl import RuleSet as _RS
        from aletheia.strategy.cost_model import CostModel as _CM
        from aletheia.strategy.review import evaluate_strategy as _eval_strat

        try:
            _bars = load_bars_from_db(chosen_symbol, timeframe, limit=2000)
            _cached_bars = _bars
            if len(_bars) >= 30:
                _bt_dir = direction if direction != "neutral" else "long"
                _bt_result = run_backtest(
                    _RS.from_json(ruleset_json), _bars,
                    _BtCfg(symbol=chosen_symbol, timeframe=timeframe,
                            direction=_bt_dir, cost_model=_CM()),
                )
                _m = _bt_result.metrics
                _pf = min(_m.profit_factor, 999.0)
                _verdict = _eval_strat(
                    trade_count=_m.trade_count, win_rate=_m.win_rate,
                    expectancy=_m.expectancy, profit_factor=_pf,
                    max_drawdown_pct=_m.max_drawdown_pct,
                    total_return_pct=_m.total_return_pct,
                )
                # Persist backtest run for performance_ledger statistics
                try:
                    from aletheia.strategy.models import BacktestRun as _BtRun
                    from aletheia.strategy.repositories import insert_backtest_run as _ins_bt
                    from dataclasses import asdict as _asdict
                    _ins_bt(_BtRun(
                        strategy_id=strategy.id,
                        symbol=chosen_symbol, timeframe=timeframe, direction=_bt_dir,
                        bar_count=len(_bars), initial_capital=1000.0,
                        cost_model_json=_json.dumps(_asdict(_CM())),
                        ruleset_json=ruleset_json,
                        trade_count=_m.trade_count, win_rate=_m.win_rate,
                        total_return_pct=_m.total_return_pct, expectancy=_m.expectancy,
                        profit_factor=_pf, max_drawdown_pct=_m.max_drawdown_pct,
                        sharpe_approx=_m.sharpe_approx, avg_trade_bars=_m.avg_trade_bars,
                    ))
                except Exception:
                    pass

                if _verdict.verdict.value == "kill" and _m.trade_count >= 2:
                    backtest_killed = True
                    outputs.append(f"backtest_killed:{_verdict.reasons}")
                    log_tool(run_id, "backtest_gate", "observe", True,
                             output=f"KILL: {_verdict.reasons}")
        except Exception as e:
            errors.append(f"backtest_gate_error:{e}")

    # Phase 4e: All auto-promotion stops at paper_candidate. Promotion to
    # paper_running requires PI Agent confirmation (sole decision maker principle).
    # Backtest-killed strategies are retired immediately.
    if backtest_killed:
        promote_strategy(strategy.id, "retired")
        outputs.append("strategy_state:retired")
    else:
        state = "draft"
        for target in _ACTIVATION_PATH:
            if target == "paper_running":
                # Write recommendation for PI Agent to promote
                try:
                    from aletheia.storage.models import PiDecision
                    from aletheia.storage.repositories import insert_pi_decision
                    pi_dec = PiDecision(
                        symbol=None,
                        direction=None,
                        action="recommend_promote",
                        conviction=0,
                        reasoning=f"Strategy Designer: strategy {strategy.id[:8]} passed gates, recommend promote to paper_running",
                        proposal_id=strategy.id,
                        debate_ran=False,
                    )
                    insert_pi_decision(pi_dec)
                    try:
                        from aletheia.agent_runtime.pi_daemon import notify_pi_daemon
                        notify_pi_daemon(source="strategy_designer_promote")
                    except Exception:
                        pass
                except Exception:
                    pass
                outputs.append("awaiting_pi_promotion:paper_candidate→paper_running")
                break
            result = promote_strategy(strategy.id, target)
            if not result.get("success"):
                errors.append(f"promotion_stopped_at:{state}->{target}:{result.get('error')}")
                break
            state = target
        outputs.append(f"strategy_state:{state}")

    # ── Parameter sweep: generate variants from surviving base strategies ──
    if ruleset_json and not backtest_killed and cfg.strategy_factory.sweep_enabled:
        try:
            from aletheia.strategy.param_sweep import SweepConfig as _SwCfg, run_parameter_sweep
            from aletheia.strategy.backtest import load_bars_from_db as _load_bars
            from aletheia.strategy.dsl import RuleSet as _RS

            sweep_bars = _cached_bars if _cached_bars else _load_bars(chosen_symbol, timeframe, limit=2000)
            if len(sweep_bars) >= 30:
                _bt_dir = direction if direction != "neutral" else "long"
                _max_v = cfg.strategy_factory.sweep_max_variants
                sweep_cfg = _SwCfg(
                    base_ruleset=_RS.from_json(ruleset_json),
                    symbol=chosen_symbol,
                    timeframe=timeframe,
                    direction=_bt_dir,
                    max_variants=_max_v,
                )
                sweep_result = run_parameter_sweep(sweep_cfg, sweep_bars)

                sweep_created = 0
                _max_deploy = min(10, len(sweep_result.all_survivors))
                for variant_rs, variant_metrics in sweep_result.all_survivors[:_max_deploy]:
                    variant_params = dict(parameters)
                    variant_params["ruleset"] = variant_rs.to_json()
                    variant_params["source"] = "param_sweep"
                    variant_params["_parent_strategy_id"] = strategy.id
                    variant_name = f"{name} (sweep-{sweep_created + 1})"[:120]
                    v_strat = create_strategy(
                        name=variant_name, description=f"Param variant of {name}",
                        parameters=variant_params,
                    )
                    for t in _ACTIVATION_PATH:
                        if t == "paper_running":
                            # PI Agent sole decision maker — stop at paper_candidate
                            try:
                                from aletheia.storage.models import PiDecision
                                from aletheia.storage.repositories import insert_pi_decision
                                pi_dec = PiDecision(
                                    symbol=None,
                                    direction=None,
                                    action="recommend_promote",
                                    conviction=0,
                                    reasoning=f"Strategy Designer: sweep variant {v_strat.id[:8]} passed gates, recommend promote to paper_running",
                                    proposal_id=v_strat.id,
                                    debate_ran=False,
                                )
                                insert_pi_decision(pi_dec)
                                try:
                                    from aletheia.agent_runtime.pi_daemon import notify_pi_daemon
                                    notify_pi_daemon(source="strategy_designer_sweep")
                                except Exception:
                                    pass
                            except Exception:
                                pass
                            break
                        r = promote_strategy(v_strat.id, t)
                        if not r.get("success"):
                            break
                    sweep_created += 1

                if sweep_created:
                    outputs.append(f"sweep_variants_created:{sweep_created}")
                    log_tool(run_id, "param_sweep", "observe", True,
                             output=f"{sweep_result.variants_tested} tested, {sweep_created} deployed")
        except Exception as e:
            errors.append(f"param_sweep_error:{e}")

    return {"outputs": outputs, "errors": errors}
