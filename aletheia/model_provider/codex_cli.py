"""Codex CLI model provider — uses locally authenticated codex command."""

import asyncio
import shutil

from aletheia.model_provider.base import (
    ModelProvider, ModelRequest, StructuredModelRequest,
    ModelResponse, ModelHealth,
)


class CodexCLIProvider(ModelProvider):
    name = "codex_cli"

    def __init__(self, command: str = "codex", mode: str = "exec", enabled: bool = False):
        self.command = command
        self.mode = mode
        self.enabled = enabled

    async def complete(self, request: ModelRequest) -> ModelResponse:
        if not self.enabled:
            raise RuntimeError("Codex CLI provider is not enabled")
        if not shutil.which(self.command):
            raise RuntimeError(f"Codex CLI command '{self.command}' not found in PATH")

        prompt = request.system or ""
        for msg in request.messages:
            prompt += f"\n{msg['role']}: {msg['content']}"

        proc = await asyncio.create_subprocess_exec(
            self.command, self.mode, prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Codex CLI failed: {stderr.decode()}")
        return ModelResponse(content=stdout.decode().strip(), model="codex_cli")

    async def structured(self, request: StructuredModelRequest, schema: type) -> dict:
        import json
        mr = ModelRequest(messages=request.messages, system=request.system, temperature=request.temperature)
        resp = await self.complete(mr)
        return json.loads(resp.content)

    async def healthcheck(self) -> ModelHealth:
        if not self.enabled:
            return ModelHealth(provider=self.name, available=False, error="disabled")
        if not shutil.which(self.command):
            return ModelHealth(provider=self.name, available=False, error="command not found")
        return ModelHealth(provider=self.name, available=True, model="codex_cli")
