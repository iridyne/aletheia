"""Command Center API — natural language intent classification."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel

from aletheia.storage.sqlite import get_db
from aletheia.storage.migrations import run_migrations

router = APIRouter(prefix="/api", tags=["command"])
logger = logging.getLogger("aletheia.command")

KNOWN_INTENTS = {
    "pause_gateway": {"keywords": ["pause", "stop", "halt"], "risk": "low"},
    "resume_gateway": {"keywords": ["resume", "start", "continue", "unpause"], "risk": "low"},
    "status": {"keywords": ["status", "running", "state", "doing"], "risk": "low"},
    "kill_switch": {"keywords": ["kill", "emergency", "shutdown"], "risk": "high"},
    "run_agent": {"keywords": ["run", "trigger", "execute", "start agent"], "risk": "low"},
    "latest_proposals": {"keywords": ["proposal", "proposals", "pending", "approve"], "risk": "low"},
    "risk_status": {"keywords": ["risk", "violations", "boundaries"], "risk": "low"},
    "market_info": {"keywords": ["market", "price", "btc", "eth", "sol"], "risk": "low"},
}


def _classify_intent_simple(text: str) -> tuple[str, dict]:
    """Rule-based fallback intent classification."""
    text_lower = text.lower().strip()
    for intent, meta in KNOWN_INTENTS.items():
        for kw in meta["keywords"]:
            if kw in text_lower:
                return intent, meta
    return "unknown", {"risk": "low"}


class CommandRequest(BaseModel):
    input: str
    context: dict | None = None


class CommandResponse(BaseModel):
    intent: str
    risk_level: str
    response: str
    action: dict | None = None
    needs_confirmation: bool = False


@router.post("/command")
async def process_command(req: CommandRequest) -> CommandResponse:
    run_migrations()
    text = req.input.strip()

    intent, meta = _classify_intent_simple(text)

    try:
        from aletheia.agents.llm import llm_complete
        prompt = (
            f"You are the Aletheia Trading OS command interpreter.\n"
            f"User said: \"{text}\"\n"
            f"Classify the intent as one of: {', '.join(KNOWN_INTENTS.keys())}, or 'query' for general questions.\n"
            f"Then provide a brief (1-2 sentence) response.\n"
            f"Format: INTENT: <intent>\\nRESPONSE: <response>"
        )
        llm_result = await llm_complete(prompt)
        if llm_result and "INTENT:" in llm_result:
            lines = llm_result.strip().split("\n")
            for line in lines:
                if line.startswith("INTENT:"):
                    parsed_intent = line.replace("INTENT:", "").strip().lower()
                    if parsed_intent in KNOWN_INTENTS:
                        intent = parsed_intent
                        meta = KNOWN_INTENTS[intent]
                elif line.startswith("RESPONSE:"):
                    response_text = line.replace("RESPONSE:", "").strip()
                    return CommandResponse(
                        intent=intent,
                        risk_level=meta.get("risk", "low"),
                        response=response_text,
                        action=_build_action(intent),
                        needs_confirmation=meta.get("risk") == "high",
                    )
    except Exception as e:
        logger.debug("LLM command classification unavailable: %s", e)

    response = _build_response(intent, text)
    return CommandResponse(
        intent=intent,
        risk_level=meta.get("risk", "low"),
        response=response,
        action=_build_action(intent),
        needs_confirmation=meta.get("risk") == "high",
    )


def _build_action(intent: str) -> dict | None:
    actions = {
        "pause_gateway": {"type": "api_call", "method": "POST", "path": "/api/gateway/pause"},
        "resume_gateway": {"type": "api_call", "method": "POST", "path": "/api/gateway/resume"},
        "kill_switch": {"type": "api_call", "method": "POST", "path": "/api/risk/kill-switch", "body": {"active": True, "reason": "command_center"}},
        "run_agent": {"type": "api_call", "method": "POST", "path": "/api/agent-runs/start", "body": {"agent_name": "market_observer"}},
    }
    return actions.get(intent)


def _build_response(intent: str, original_text: str) -> str:
    responses = {
        "pause_gateway": "I'll pause the gateway. All scheduled agents will stop until resumed.",
        "resume_gateway": "Resuming the gateway. Agents will restart their scheduled runs.",
        "status": "Use the Cockpit page to see full system status, or check the top status bar.",
        "kill_switch": "This will engage the kill switch and halt ALL trading immediately. Are you sure?",
        "run_agent": "Triggering the market observer agent now.",
        "latest_proposals": "Check the Proposals page for pending items that need your review.",
        "risk_status": "Check the Risk & Controls page for current violations and boundaries.",
        "market_info": "Check the Charts page for current market data and price action.",
        "unknown": f"I'm not sure what you mean by \"{original_text}\". Try: pause, resume, status, or run agent.",
    }
    return responses.get(intent, responses["unknown"])
