"""Shared helpers for agents: LLM calls with graceful degradation + tool logging."""

import logging
import json
from collections.abc import AsyncIterator

from aletheia.model_provider.base import ModelRequest, StreamChunk
from aletheia.model_provider.router import get_provider
from aletheia.model_provider.fallback import complete_with_fallback

logger = logging.getLogger("aletheia.agents")


async def llm_complete(system: str, user: str, *, max_tokens: int = 800, provider: str | None = None) -> tuple[str | None, str | None]:
    """Call the model provider. Returns (content, error).

    Never raises — on any failure returns (None, error_message) so the agent can
    record a clean error and still complete its run.
    provider: explicit provider name (or None → use default_provider).
    """
    try:
        provider = get_provider(provider)
        resp = await complete_with_fallback(ModelRequest(
            messages=[{"role": "user", "content": user}],
            system=system,
            max_tokens=max_tokens,
        ), provider=provider)
        _record_llm(True)
        return resp.content, None
    except Exception as e:
        logger.warning("LLM call failed: %s", e)
        _record_llm(False)
        return None, f"llm_unavailable: {e}"


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


async def llm_structured(system: str, user: str, *, schema, max_tokens: int = 1024, provider: str | None = None) -> tuple[dict | None, str | None]:
    """Call the provider's structured() and return (parsed_dict, error).

    Never raises — absorbs provider errors AND the JSON-parse failure in the
    provider impl, returning (None, error) so the caller can fall back cleanly.
    `schema` is a Pydantic model class (uses model_json_schema()).
    provider: explicit provider name (or None → use default_provider).
    """
    try:
        provider = get_provider(provider)
        schema_json = json.dumps(schema.model_json_schema() if hasattr(schema, "model_json_schema") else {})
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

        # Bounded retry (gateway-stability fix): under LLM-provider contention a
        # capable model intermittently prepends prose / returns non-JSON. One
        # corrective retry at temperature 0 recovers most of these cheaply. We retry
        # ONLY the parse-failure case (non-empty content, no JSON) — empty content
        # (reasoning-budget exhaustion / outage) is not retried (retry can't help and
        # only adds load). market_observer's wall-clock budget caps total time.
        last_err = "structured_parse_error: no valid JSON object found in response"
        for attempt in range(2):
            messages = list(base_messages)
            if attempt == 1:
                messages.append({"role": "user", "content": (
                    "Your previous reply was NOT valid JSON. Output ONLY the JSON object "
                    "now: start with '{' and end with '}', no prose, no code fences."
                )})
            resp = await complete_with_fallback(
                ModelRequest(
                    messages=messages, system=structured_system,
                    temperature=0.3 if attempt == 0 else 0.0, max_tokens=max_tokens,
                ),
                provider=provider,
            )
            content = (resp.content or "").strip()
            if not content:
                # LLM connected and ran (usage tokens > 0) but returned empty text.
                # This is a parse failure, NOT an LLM outage — do not trip the health
                # monitor's failure counter (which would pause the scheduler). Not
                # retried: empty = reasoning-budget exhaustion, retry won't help.
                usage = (resp.raw or {}).get("usage", {}) if resp is not None else {}
                if usage.get("completion_tokens", 0) > 0:
                    logger.warning("LLM structured call: empty content with %d completion_tokens — "
                                   "reasoning model exhausted budget on reasoning phase",
                                   usage["completion_tokens"])
                    return None, "structured_empty_content"
                logger.warning("LLM structured call: empty content, no completion tokens")
                _record_llm(False)
                return None, "llm_unavailable: empty_response"
            data = _extract_json_object(content)
            if isinstance(data, dict):
                _record_llm(True)
                return data, None
            if data is not None:
                return None, "structured_non_dict"
            # parse failure → loop to retry once (attempt 1)
        logger.warning("LLM structured call: no valid JSON after corrective retry")
        return None, last_err
    except json.JSONDecodeError as e:
        # Bad JSON but the LLM did respond — not an outage, don't trip health counter
        logger.warning("LLM structured call: JSON parse failed: %s", e)
        return None, f"structured_parse_error: {e}"
    except Exception as e:
        logger.warning("LLM structured call failed: %s", e)
        _record_llm(False)
        return None, f"llm_unavailable: {e}"


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
        logger.warning("LLM stream failed: %s", e)
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
    """Best-effort tool-call + reasoning-step logging (never raises).

    Writes BOTH:
      - `agent_tool_calls` (the existing structured tool ledger)
      - `agent_run_steps` (the §6 reasoning trace; closes WEBUI_BACKEND_HANDOFF #2's
        write-side gap so `GET /api/agent-runs/{id}/steps` actually returns content).
    """
    if not run_id:
        return
    try:
        from aletheia.storage.repositories import insert_tool_call, insert_run_step
        insert_tool_call(run_id, tool_name, tool_class, permitted, output_data=output,
                         input_data=input_data)
        insert_run_step(
            run_id=run_id,
            step_number=_next_step(run_id),
            action=f"tool:{tool_name}" + ("" if permitted else " (denied)"),
            input_data=input_data,
            output_data=output,
        )
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
