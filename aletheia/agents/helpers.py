"""Shared helpers for agents: LLM calls with graceful degradation + tool logging."""

import logging

from aletheia.model_provider.base import ModelRequest, StructuredModelRequest
from aletheia.model_provider.router import get_provider

logger = logging.getLogger("aletheia.agents")


async def llm_complete(system: str, user: str, *, max_tokens: int = 800) -> tuple[str | None, str | None]:
    """Call the default model provider. Returns (content, error).

    Never raises — on any failure returns (None, error_message) so the agent can
    record a clean error and still complete its run.
    """
    try:
        provider = get_provider()
        resp = await provider.complete(ModelRequest(
            messages=[{"role": "user", "content": user}],
            system=system,
            max_tokens=max_tokens,
        ))
        _record_llm(True)
        return resp.content, None
    except Exception as e:
        logger.warning("LLM call failed: %s", e)
        _record_llm(False)
        return None, f"llm_unavailable: {e}"


async def llm_structured(system: str, user: str, *, schema, max_tokens: int = 400) -> tuple[dict | None, str | None]:
    """Call the provider's structured() and return (parsed_dict, error).

    Never raises — absorbs provider errors AND the JSON-parse failure in the
    provider impl, returning (None, error) so the caller can fall back cleanly.
    `schema` is a Pydantic model class (uses model_json_schema()).
    """
    try:
        provider = get_provider()
        data = await provider.structured(
            StructuredModelRequest(messages=[{"role": "user", "content": user}], system=system,
                                   max_tokens=max_tokens),
            schema,
        )
        if not isinstance(data, dict):
            return None, "structured_non_dict"
        _record_llm(True)
        return data, None
    except Exception as e:
        logger.warning("LLM structured call failed: %s", e)
        _record_llm(False)
        return None, f"llm_unavailable: {e}"


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
