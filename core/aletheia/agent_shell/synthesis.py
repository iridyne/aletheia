"""Answer synthesis — LLM natural language with formatted fallback."""

import json
import logging

logger = logging.getLogger("aletheia.agent_shell.synthesis")

SYNTHESIS_SYSTEM_PROMPT = """\
You are the Aletheia Trading OS assistant. Given command execution results and the user's
original question, provide a clear, concise answer in 1-3 sentences. Be specific — cite
numbers, symbols, and statuses from the data. Do not speculate beyond the data provided.
If the data shows an error, explain it plainly.
"""


async def synthesize_answer(command_result: dict, user_input: str, context: dict) -> str:
    """Generate a natural-language answer from command results.

    LLM path produces conversational text; fallback formats the data readably.
    """
    try:
        from aletheia.agents.helpers import llm_complete
        user_prompt = (
            f"User asked: \"{user_input}\"\n\n"
            f"Command result:\n{json.dumps(command_result, indent=2, default=str)[:1500]}\n\n"
            f"Provide a concise answer."
        )
        content, error = await llm_complete(
            SYNTHESIS_SYSTEM_PROMPT,
            user_prompt,
            max_tokens=600,
            caller="agent_shell.synthesis",
        )
        if content and not error:
            return content
    except Exception as e:
        logger.debug("LLM synthesis failed: %s", e)

    return _format_fallback(command_result)


def _format_fallback(result: dict) -> str:
    """Human-readable formatting when LLM is unavailable."""
    if "error" in result:
        return f"Error: {result['error']}"

    if "message" in result:
        return result["message"]

    parts = []
    for key, value in result.items():
        if isinstance(value, list):
            if not value:
                parts.append(f"{key}: (none)")
            elif len(value) <= 3:
                for item in value:
                    if isinstance(item, dict):
                        line = ", ".join(f"{k}={v}" for k, v in item.items() if v is not None)
                        parts.append(f"  • {line}")
                    else:
                        parts.append(f"  • {item}")
            else:
                parts.append(f"{key}: {len(value)} items")
                for item in value[:3]:
                    if isinstance(item, dict):
                        line = ", ".join(f"{k}={v}" for k, v in item.items() if v is not None)
                        parts.append(f"  • {line}")
        elif isinstance(value, dict):
            line = ", ".join(f"{k}={v}" for k, v in value.items() if v is not None)
            parts.append(f"{key}: {line}")
        else:
            parts.append(f"{key}: {value}")

    return "\n".join(parts) if parts else json.dumps(result, indent=2, default=str)
