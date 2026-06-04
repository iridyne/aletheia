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


def log_tool(run_id: str | None, tool_name: str, tool_class: str, permitted: bool,
             output: str | None = None) -> None:
    """Best-effort tool-call logging (never raises)."""
    if not run_id:
        return
    try:
        from aletheia.storage.repositories import insert_tool_call
        insert_tool_call(run_id, tool_name, tool_class, permitted, output_data=output)
    except Exception:
        pass
