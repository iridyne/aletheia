"""AgentShellRuntime — orchestrates the full command cycle."""

import json
import logging

from pydantic import BaseModel

logger = logging.getLogger("aletheia.agent_shell.runtime")


class CommandResponse(BaseModel):
    intent: str
    risk_level: str
    response: str
    action: dict | None = None
    needs_confirmation: bool = False


class AgentShellRuntime:
    """Single request-response cycle: input → intent → permission → execute → synthesize → audit."""

    async def handle(self, user_input: str) -> CommandResponse:
        from aletheia.agent_shell.context import build_app_context, context_summary_text
        from aletheia.agent_shell.intent import classify_intent
        from aletheia.agent_shell.permissions import check_permission
        from aletheia.agent_shell.commands import HANDLERS
        from aletheia.agent_shell.registry import COMMANDS, FORBIDDEN_INTENTS
        from aletheia.agent_shell.synthesis import synthesize_answer
        from aletheia.agent_shell.sessions import create_session, close_session
        from aletheia.agent_shell.audit import record_command
        from aletheia.config.loader import load_config

        text = user_input.strip()
        if not text:
            return CommandResponse(
                intent="empty", risk_level="read",
                response="Please type a question or command.",
            )

        session_id, start_time = create_session(text)
        cfg = load_config()
        current_mode = cfg.agent.permission_mode

        try:
            ctx = build_app_context(["system", "market", "agents", "trading"])
            summary = context_summary_text(ctx)

            intent = await classify_intent(text, summary)

            if intent.command == "forbidden":
                response = (
                    "That action is permanently forbidden. The Agent Shell cannot "
                    "access live trading, credentials, or hard risk limits."
                )
                record_command(session_id, "forbidden", intent.parameters, False, response)
                close_session(session_id, intent="forbidden", command=None,
                              response=response, permitted=False, start_time=start_time)
                return CommandResponse(
                    intent="forbidden", risk_level="high",
                    response=response, needs_confirmation=False,
                )

            if intent.command == "query":
                response = await self._handle_general_query(text, ctx, summary)
                record_command(session_id, "query", {}, True, response[:200])
                close_session(session_id, intent="query", command=None,
                              response=response, permitted=True, start_time=start_time)
                return CommandResponse(
                    intent="query", risk_level="read", response=response,
                )

            cmd_def = COMMANDS.get(intent.command)
            if not cmd_def:
                response = f"Unknown command: {intent.command}"
                close_session(session_id, intent=intent.command, command=None,
                              response=response, permitted=False, start_time=start_time)
                return CommandResponse(
                    intent=intent.command, risk_level="read", response=response,
                )

            allowed, denial = check_permission(intent.command, current_mode)
            if not allowed:
                record_command(session_id, intent.command, intent.parameters, False, denial or "")
                close_session(session_id, intent=intent.command, command=intent.command,
                              response=denial or "", permitted=False, start_time=start_time)
                return CommandResponse(
                    intent=intent.command, risk_level=cmd_def.risk_level,
                    response=denial or "Permission denied.",
                    needs_confirmation=False,
                )

            handler = HANDLERS.get(intent.command)
            if not handler:
                response = f"Command '{intent.command}' has no handler implemented."
                close_session(session_id, intent=intent.command, command=intent.command,
                              response=response, permitted=True, start_time=start_time)
                return CommandResponse(
                    intent=intent.command, risk_level=cmd_def.risk_level, response=response,
                )

            result = await handler(intent.parameters)

            response = await synthesize_answer(result, text, ctx)

            action = self._build_action(intent.command, result)
            record_command(session_id, intent.command, intent.parameters, True, response[:200])
            close_session(session_id, intent=intent.command, command=intent.command,
                          response=response, permitted=True, start_time=start_time)

            return CommandResponse(
                intent=intent.command,
                risk_level=cmd_def.risk_level,
                response=response,
                action=action,
                needs_confirmation=cmd_def.risk_level == "operational",
            )

        except Exception as e:
            logger.exception("Agent Shell error: %s", e)
            response = f"An error occurred while processing your request: {e}"
            try:
                close_session(session_id, intent="error", command=None,
                              response=response, permitted=True, start_time=start_time)
            except Exception:
                pass
            return CommandResponse(
                intent="error", risk_level="read", response=response,
            )

    async def _handle_general_query(self, text: str, ctx: dict, summary: str) -> str:
        """Handle free-form questions that don't map to a specific command."""
        try:
            from aletheia.agents.helpers import llm_complete
            system = (
                "You are the Aletheia Trading OS assistant. Answer the user's question "
                "based on the system context provided. Be concise (1-3 sentences). "
                "If you don't have enough data to answer, say so."
            )
            user_prompt = f"System context: {summary}\n\nUser question: \"{text}\""
            content, error = await llm_complete(system, user_prompt, max_tokens=400)
            if content and not error:
                return content
        except Exception:
            pass

        return (
            f"Based on current data: {summary}\n\n"
            f"For more specific information, try asking about system status, "
            f"agent activity, market prices, or positions."
        )

    def _build_action(self, command: str, result: dict) -> dict | None:
        """Build frontend action hint for commands that have side effects."""
        if command == "pause_agents":
            return {"type": "state_change", "key": "paused", "value": True}
        if command == "resume_agents":
            return {"type": "state_change", "key": "paused", "value": False}
        return None
