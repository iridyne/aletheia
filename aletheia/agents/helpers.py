"""Shared helpers for agents: LLM calls with graceful degradation + tool logging."""

import asyncio
import logging
import json
from collections.abc import AsyncIterator

from aletheia.model_provider.base import ModelRequest, StreamChunk
from aletheia.model_provider.router import get_provider
from aletheia.model_provider.fallback import complete_with_fallback

logger = logging.getLogger("aletheia.agents")

# Must stay below the observer loop budget (200s) and observer job timeout (240s).
# The provider may allow 300s for long research generations, but structured observer
# calls need their own total budget across all repair attempts so scheduler cancellation
# never abandons the event-loop thread mid-request.
_LLM_STRUCTURED_WALLCLOCK_BUDGET_SEC = 180.0


def _exception_detail(exc: BaseException) -> str:
    """Keep the exception class when libraries raise with an empty message."""
    name = type(exc).__name__
    message = str(exc).strip()
    return f"{name}: {message}" if message else name


async def llm_complete(system: str, user: str, *, max_tokens: int = 800, provider: str | None = None) -> tuple[str | None, str | None]:
    """Call the model provider. Returns (content, error).

    Never raises — on any failure returns (None, error_message) so the agent can
    record a clean error and still complete its run.
    provider: explicit provider name (or None → use default_provider).
    """
    # #161 fix: preserve the provider NAME so complete_with_fallback keys health
    # tracking + cooldown + candidate-model resolution against the tier the
    # caller actually asked for (e.g. get_provider_for_tier("deep") →
    # "anthropic"), not cfg.models.default_provider. Without this, a tier
    # provider's cooldown state was being tracked under default_provider's
    # name, so a deep-tier failure would (a) never trip its own model's
    # cooldown, and (b) fall back through default_provider's candidate list —
    # not the tier's.
    provider_name = provider
    try:
        provider = get_provider(provider_name)
        resp = await complete_with_fallback(ModelRequest(
            messages=[{"role": "user", "content": user}],
            system=system,
            max_tokens=max_tokens,
        ), provider=provider, provider_name=provider_name)
        _record_llm(True)
        return resp.content, None
    except Exception as e:
        detail = _exception_detail(e)
        logger.warning("LLM call failed: %s", detail)
        _record_llm(False)
        return None, f"llm_unavailable: {detail}"


def _extract_json_object(text: str):
    """Extract the first valid JSON object from LLM response text.

    Handles: raw JSON, markdown code fences (```json ... ```), prose before/after
    the JSON block, and nested braces. Returns the parsed object or None.
    """
    import re
    # Strategy 1: try direct parse (fastest path for well-behaved models)
    text = text.strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    # Strategy 2: strip markdown code fences
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Strategy 3: brace-matching extraction — find the first '{' and match
    # its closing '}' accounting for nesting and string literals.
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            if in_string:
                escape = True
            continue
        if c == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


async def llm_structured(
    system: str,
    user: str,
    *,
    schema,
    max_tokens: int = 2048,
    provider: str | None = None,
    fallback_to_defaults: bool = False,
) -> tuple[dict | None, str | None]:
    """Call the provider's structured() and return (parsed_dict, error).

    Never raises — absorbs provider errors AND the JSON-parse failure in the
    provider impl, returning (None, error) so the caller can fall back cleanly.
    `schema` is a Pydantic model class (uses model_json_schema()).
    provider: explicit provider name (or None → use default_provider).
    fallback_to_defaults: if True and all retries fail, return a dict filled
        with schema defaults / None values instead of None. Use this only for
        non-critical paths where "best effort" is better than nothing.
    """
    # #161 fix (mirrors llm_complete): thread the tier's provider name into
    # complete_with_fallback so its fallback/health/cooldown keying matches the
    # provider actually in use, not cfg.models.default_provider.
    provider_name = provider
    schema_dict: dict = {}

    def _failure(error: str) -> tuple[dict | None, str]:
        if fallback_to_defaults:
            return _schema_defaults_dict(schema_dict), f"{error}(fallback)"
        return None, error

    try:
        provider = get_provider(provider_name)
        schema_dict = schema.model_json_schema() if hasattr(schema, "model_json_schema") else {}
        schema_json = json.dumps(schema_dict)
        base_messages = [
            {"role": "user", "content": user},
            {"role": "user", "content": (
                f"Respond with ONLY a raw JSON object matching this schema (no markdown fences, "
                f"no explanation, no text before or after — just the JSON): {schema_json}"
            )},
        ]
        # Prepend /no_think for Qwen3-series reasoning models: without it the model
        # exhausts the token budget on internal reasoning and returns empty text output.
        # Other models ignore this prefix safely.
        structured_system = f"/no_think\n{system}" if system else "/no_think"
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _LLM_STRUCTURED_WALLCLOCK_BUDGET_SEC

        # 3 attempts with escalating token budget + corrective strategy:
        #   attempt 0: base max_tokens at temperature 0.3, standard prompt
        #   attempt 1: +50% tokens, temperature 0, stronger JSON-only prompt
        #   attempt 2: 2x tokens, temperature 0, repair-mode prompt (send back previous bad output)
        last_bad_output = ""
        last_err = "structured_parse_error: no valid JSON object found in response"
        max_attempts = 3
        for attempt in range(max_attempts):
            # Escalate token budget on each retry
            attempt_max_tokens = max_tokens
            if attempt == 1:
                attempt_max_tokens = int(max_tokens * 1.5)
            elif attempt >= 2:
                attempt_max_tokens = max_tokens * 2

            messages = list(base_messages)
            if attempt == 1:
                # Stronger corrective prompt + emphasize immediate JSON output
                messages.append({"role": "user", "content": (
                    "IMPORTANT: Your previous reply was invalid. "
                    "Output ONLY the JSON object now. "
                    "Do NOT include any thinking, explanation, or markdown. "
                    "Start your reply with '{' and end with '}'. Nothing else."
                )})
            elif attempt >= 2:
                # Final attempt: repair mode — show the model its previous bad output
                # and ask it to fix it. Simpler task than generating from scratch.
                repair_hint = last_bad_output[:500] if last_bad_output else "(empty)"
                messages.append({"role": "user", "content": (
                    f"FIX THIS JSON. Your previous output was:\n"
                    f"```\n{repair_hint}\n```\n"
                    f"Reply with ONLY the corrected JSON. Start with '{{' and end with '}}'. "
                    f"No extra text, no explanation."
                )})
            remaining = deadline - loop.time()
            if remaining <= 0:
                logger.warning("LLM structured call exhausted %.0fs total wall-clock budget",
                               _LLM_STRUCTURED_WALLCLOCK_BUDGET_SEC)
                _record_llm(False)
                return _failure("llm_unavailable: structured_timeout")
            try:
                resp = await asyncio.wait_for(
                    complete_with_fallback(
                        ModelRequest(
                            messages=messages, system=structured_system,
                            temperature=0.3 if attempt == 0 else 0.0,
                            max_tokens=attempt_max_tokens,
                        ),
                        provider=provider,
                        provider_name=provider_name,
                    ),
                    timeout=remaining,
                )
            except TimeoutError:
                logger.warning("LLM structured call exceeded %.0fs total wall-clock budget",
                               _LLM_STRUCTURED_WALLCLOCK_BUDGET_SEC)
                _record_llm(False)
                return _failure("llm_unavailable: structured_timeout")
            content = (resp.content or "").strip()
            usage = (resp.raw or {}).get("usage", {}) if resp is not None else {}
            output_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0))
            last_bad_output = content

            if not content:
                if output_tokens > 0:
                    # Reasoning model exhausted budget on thinking phase → retry with more
                    if attempt < max_attempts - 1:
                        next_budget = int(max_tokens * (1.5 if attempt == 0 else 2))
                        logger.warning(
                            "LLM structured call: empty content with %d output_tokens "
                            "(reasoning model exhausted budget) — retrying with %d tokens "
                            "(attempt %d/%d)",
                            output_tokens, next_budget, attempt + 1, max_attempts,
                        )
                        continue
                    logger.warning("LLM structured call: empty content with %d output_tokens — "
                                   "reasoning model exhausted budget on reasoning phase",
                                   output_tokens)
                    return _failure("structured_empty_content")
                if attempt < max_attempts - 1:
                    logger.warning(
                        "LLM structured call: empty content, no output_tokens — retrying "
                        "(attempt %d/%d)", attempt + 1, max_attempts,
                    )
                    continue
                logger.warning("LLM structured call: empty content, no completion tokens")
                _record_llm(False)
                return _failure("llm_unavailable: empty_response")

            data = _extract_json_object(content)
            if isinstance(data, dict):
                _record_llm(True)
                return data, None
            if data is not None:
                last_err = "structured_non_dict"
                if attempt < max_attempts - 1:
                    logger.warning(
                        "LLM structured call: JSON value is not an object (attempt %d/%d) — retrying",
                        attempt + 1, max_attempts,
                    )
                    continue
                break

            # Parse failed — check if output was truncated (near token limit)
            truncated = output_tokens > 0 and output_tokens >= attempt_max_tokens * 0.9
            if truncated and attempt < max_attempts - 1:
                logger.warning(
                    "LLM structured call: parse failure, output likely truncated "
                    "(%d/%d tokens used) — retrying with more tokens (attempt %d/%d)",
                    output_tokens, attempt_max_tokens, attempt + 1, max_attempts,
                )
                continue

            # Parse failure, not obviously truncated → corrective retry
            if attempt < max_attempts - 1:
                logger.warning(
                    "LLM structured call: parse failure (attempt %d/%d), content_len=%d — retrying",
                    attempt + 1, max_attempts, len(content),
                )
                continue
            last_err = "structured_parse_error: no valid JSON object found in response"

        logger.warning("LLM structured call: no valid JSON after %d attempts", max_attempts)
        return _failure(last_err)
    except json.JSONDecodeError as e:
        # Bad JSON but the LLM did respond — not an outage, don't trip health counter
        logger.warning("LLM structured call: JSON parse failed: %s", e)
        return _failure(f"structured_parse_error: {e}")
    except Exception as e:
        detail = _exception_detail(e)
        logger.warning("LLM structured call failed: %s", detail)
        _record_llm(False)
        return _failure(f"llm_unavailable: {detail}")


def _schema_defaults_dict(schema_dict: dict) -> dict:
    """Build a safe default dict from a JSON schema's properties.

    Fills each field with None / empty list / empty dict depending on type.
    Used for fallback_to_defaults mode so callers get a structurally-valid
    (if content-free) result even when the LLM is unusable.
    """
    result: dict = {}
    props = schema_dict.get("properties", {})
    for key, prop in props.items():
        if "default" in prop:
            result[key] = prop["default"]
            continue
        typ = prop.get("type", "string")
        if isinstance(typ, list):
            typ = next((item for item in typ if item != "null"), "string")
        if typ == "array":
            result[key] = []
        elif typ == "object":
            result[key] = {}
        elif typ == "integer" or typ == "number":
            result[key] = 0
        elif typ == "boolean":
            result[key] = False
        else:
            result[key] = None
    return result


async def llm_stream(system: str, user: str, *, max_tokens: int = 800, provider: str | None = None) -> AsyncIterator[StreamChunk]:
    """Stream response chunks from the provider.

    Yields StreamChunk objects. On failure yields a single chunk with done=True.
    Callers should iterate with `async for chunk in llm_stream(...)`.
    provider: explicit provider name (or None → use default_provider).
    """
    try:
        provider = get_provider(provider)
        async for chunk in provider.stream(ModelRequest(
            messages=[{"role": "user", "content": user}],
            system=system,
            max_tokens=max_tokens,
        )):
            yield chunk
        _record_llm(True)
    except Exception as e:
        logger.warning("LLM stream failed: %s", _exception_detail(e))
        _record_llm(False)
        yield StreamChunk(content="", done=True)


def _record_llm(success: bool) -> None:
    """Feed the health monitor's consecutive-failure counter. Never raises.

    Local import avoids any import-time cycle with agent_runtime.health.
    """
    try:
        from aletheia.agent_runtime.health import record_llm_success, record_llm_failure
        (record_llm_success if success else record_llm_failure)()
    except Exception:
        pass


# Per-run step counters. agent_run_steps.step_number must be monotonic within a run;
# keeping the counter in-process avoids a SELECT-MAX round-trip on every tool call.
# Lost on restart, which is fine: a step trace only matters for currently-running agents.
_step_counters: dict[str, int] = {}


def _next_step(run_id: str) -> int:
    n = _step_counters.get(run_id, 0) + 1
    _step_counters[run_id] = n
    return n


def _release_run_counter(run_id: str) -> None:
    """Drop the per-run step counter after the run finishes (orchestrator calls this)."""
    _step_counters.pop(run_id, None)


def log_tool(run_id: str | None, tool_name: str, tool_class: str, permitted: bool,
             output: str | None = None, input_data: str | None = None) -> None:
    """Best-effort tool-call logging (never raises).

    Writes only to `agent_tool_calls` — the structured tool ledger. The old
    duplicate write to `agent_run_steps` doubled every tool-call payload on
    disk; `/api/agent-runs/{id}/steps` now UNIONs both tables at read time so
    the reasoning trace continues to include tool events without persisting
    each one twice. Non-tool reasoning steps still go through `record_step`.
    """
    if not run_id:
        return
    try:
        from aletheia.storage.repositories import insert_tool_call
        insert_tool_call(run_id, tool_name, tool_class, permitted, output_data=output,
                         input_data=input_data)
    except Exception:
        pass


def record_step(run_id: str | None, action: str, input_data: str | None = None,
                output_data: str | None = None) -> None:
    """Record a non-tool reasoning step (e.g. 'considered N lessons', 'skipped: neutral bias').

    Use this for decision points that aren't tool calls — context the reasoning trace
    needs to make sense. Best-effort, never raises.
    """
    if not run_id:
        return
    try:
        from aletheia.storage.repositories import insert_run_step
        insert_run_step(
            run_id=run_id,
            step_number=_next_step(run_id),
            action=action,
            input_data=input_data,
            output_data=output_data,
        )
    except Exception:
        pass
