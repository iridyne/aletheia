"""Command Center API — routes user input through the Agent Shell."""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["command"])


class CommandRequest(BaseModel):
    input: str
    context: dict | None = None


@router.post("/command")
async def process_command(req: CommandRequest):
    from aletheia.agent_shell.runtime import AgentShellRuntime
    runtime = AgentShellRuntime()
    return await runtime.handle(req.input)
