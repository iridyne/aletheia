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
    session_id: str | None = None


class AgentShellRuntime:
    """Single request-response cycle: input → intent → permission → execute → synthesize → audit."""

    @staticmethod
    def _split_extension_invocation(text: str) -> tuple[str, str] | None:
        if not text.startswith("/"):
            return None
        slash_command, _, raw_tail = text.partition(" ")
        return slash_command.strip(), raw_tail.strip()

    @staticmethod
    def _extension_parameters(extension: dict, raw_tail: str) -> tuple[dict, str | None]:
        manifest = extension.get("manifest")
        manifest = dict(manifest) if isinstance(manifest, dict) else {}
        params = manifest.get("default_parameters")
        params = dict(params) if isinstance(params, dict) else {}
        if not raw_tail:
            params["_extension"] = {
                "extension_id": extension.get("id"),
                "slash_command": extension.get("slash_command"),
            }
            return params, None

        try:
            parsed = json.loads(raw_tail)
        except Exception:
            parsed = None

        if isinstance(parsed, dict):
            params.update(parsed)
            params["_extension"] = {
                "extension_id": extension.get("id"),
                "slash_command": extension.get("slash_command"),
            }
            return params, None

        text_parameter = str(manifest.get("text_parameter") or "").strip()
        if text_parameter:
            params[text_parameter] = raw_tail
            params["_extension"] = {
                "extension_id": extension.get("id"),
                "slash_command": extension.get("slash_command"),
            }
            return params, None

        return {}, (
            f"Extension {extension.get('slash_command')} requires either JSON object arguments "
            "or a manifest text_parameter binding."
        )

    async def handle(
        self,
        user_input: str,
        *,
        permission_mode: str | None = None,
        permission_scope: str = "worker",
        session_id: str | None = None,
    ) -> CommandResponse:
        from aletheia.agent_shell.context import (
            build_app_context,
            context_summary_text,
            extend_summary_with_session_trace,
            sections_for_session_kind,
        )
        from aletheia.agent_shell.intent import classify_intent
        from aletheia.agent_shell.operator_store import (
            create_event,
            get_extension_by_slash_command,
            get_session,
        )
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

        session_id, start_time = create_session(text, session_id=session_id)
        cfg = load_config()
        if permission_mode is not None:
            current_mode = permission_mode
        elif permission_scope == "operator":
            current_mode = cfg.operator.permission_mode
        else:
            current_mode = cfg.agent.permission_mode

        try:
            session = get_session(session_id) if session_id else None
            session_kind = str(session.get("kind") or "command_session") if session else "command_session"
            context_sections = (
                sections_for_session_kind(session_kind)
                if session and session_kind != "command_session"
                else ["system", "market", "agents", "trading"]
            )
            ctx = build_app_context(context_sections)
            summary = extend_summary_with_session_trace(
                context_summary_text(ctx),
                session_id,
                pending_user_input=text,
            )

            extension_invocation = self._split_extension_invocation(text)
            if extension_invocation is not None:
                slash_command, raw_tail = extension_invocation
                extension = get_extension_by_slash_command(slash_command)
                if extension is None:
                    response = f"Unknown slash command: {slash_command}"
                    record_command(session_id, slash_command, {}, False, response)
                    close_session(
                        session_id,
                        intent="extension_unknown",
                        command=slash_command,
                        response=response,
                        permitted=False,
                        start_time=start_time,
                    )
                    return CommandResponse(
                        intent="extension_unknown",
                        risk_level="read",
                        response=response,
                        session_id=session_id,
                    )

                if extension.get("status") != "enabled":
                    response = (
                        f"Extension {slash_command} exists but is not enabled yet "
                        f"(current status: {extension.get('status')})."
                    )
                    record_command(session_id, slash_command, {}, False, response)
                    close_session(
                        session_id,
                        intent="extension_not_enabled",
                        command=slash_command,
                        response=response,
                        permitted=False,
                        start_time=start_time,
                    )
                    return CommandResponse(
                        intent="extension_not_enabled",
                        risk_level="read",
                        response=response,
                        session_id=session_id,
                    )

                intent = type("ResolvedIntent", (), {})()
                intent.command = str(extension.get("target_command") or "").strip()
                intent.parameters, parameter_error = self._extension_parameters(extension, raw_tail)
                if parameter_error:
                    record_command(session_id, slash_command, {}, False, parameter_error)
                    close_session(
                        session_id,
                        intent="extension_invalid_arguments",
                        command=slash_command,
                        response=parameter_error,
                        permitted=False,
                        start_time=start_time,
                    )
                    return CommandResponse(
                        intent="extension_invalid_arguments",
                        risk_level="read",
                        response=parameter_error,
                        session_id=session_id,
                    )
                create_event(
                    event_type="extension_invoked",
                    source="agent_shell_runtime",
                    session_id=session_id,
                    related_entity_type="extension",
                    related_entity_id=str(extension.get("id") or slash_command),
                    payload={
                        "slash_command": slash_command,
                        "target_command": intent.command,
                        "raw_tail": raw_tail or None,
                    },
                )
            else:
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
                    session_id=session_id,
                )

            if intent.command == "query":
                response = await self._handle_general_query(text, ctx, summary)
                record_command(session_id, "query", {}, True, response[:200])
                close_session(session_id, intent="query", command=None,
                              response=response, permitted=True, start_time=start_time)
                return CommandResponse(
                    intent="query", risk_level="read", response=response, session_id=session_id,
                )

            cmd_def = COMMANDS.get(intent.command)
            if not cmd_def:
                response = f"Unknown command: {intent.command}"
                close_session(session_id, intent=intent.command, command=None,
                              response=response, permitted=False, start_time=start_time)
                return CommandResponse(
                    intent=intent.command, risk_level="read", response=response, session_id=session_id,
                )

            allowed, denial = check_permission(
                intent.command,
                current_mode,
                permission_scope=permission_scope,
            )
            if not allowed:
                record_command(session_id, intent.command, intent.parameters, False, denial or "")
                close_session(session_id, intent=intent.command, command=intent.command,
                              response=denial or "", permitted=False, start_time=start_time)
                return CommandResponse(
                    intent=intent.command, risk_level=cmd_def.risk_level,
                    response=denial or "Permission denied.",
                    needs_confirmation=False,
                    session_id=session_id,
                )

            if permission_scope == "operator":
                worker_allowed, worker_denial = check_permission(
                    intent.command,
                    cfg.agent.permission_mode,
                    permission_scope="worker",
                )
                if not worker_allowed:
                    response = (
                        "This command is currently blocked by worker/global safety. "
                        f"{worker_denial or 'Permission denied.'}"
                    )
                    record_command(session_id, intent.command, intent.parameters, False, response)
                    close_session(
                        session_id,
                        intent=intent.command,
                        command=intent.command,
                        response=response,
                        permitted=False,
                        start_time=start_time,
                    )
                    return CommandResponse(
                        intent=intent.command,
                        risk_level=cmd_def.risk_level,
                        response=response,
                        needs_confirmation=False,
                        session_id=session_id,
                    )

            handler = HANDLERS.get(intent.command)
            if not handler:
                response = f"Command '{intent.command}' has no handler implemented."
                close_session(session_id, intent=intent.command, command=intent.command,
                              response=response, permitted=True, start_time=start_time)
                return CommandResponse(
                    intent=intent.command, risk_level=cmd_def.risk_level, response=response, session_id=session_id,
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
                needs_confirmation=cmd_def.requires_confirmation,
                session_id=session_id,
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
                intent="error", risk_level="read", response=response, session_id=session_id,
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
