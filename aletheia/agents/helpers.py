"""Shared helpers for agents: LLM calls with graceful degradation + tool logging."""

import logging

from aletheia.model_provider.base import ModelRequest
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
        return resp.content, None
    except Exception as e:
        logger.warning("LLM call failed: %s", e)
        return None, f"llm_unavailable: {e}"


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
