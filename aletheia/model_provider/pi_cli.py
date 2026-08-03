"""Pi CLI provider backed by Pi's locally authenticated print-mode harness.

Aletheia never imports or copies Pi credentials. The installed ``pi`` process
owns provider login and model selection. Calls are one-shot, receive prompts on
stdin, and disable Pi tools, extensions, skills, prompt templates, context files,
and project-local trust so this adapter remains an inference boundary rather
than a second autonomous coding runtime.
"""

from __future__ import annotations

import asyncio
import json
import shutil

from aletheia.gateway.logs import redact_log_text
from aletheia.model_provider.base import (
    ModelHealth,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    StructuredModelRequest,
    model_json_schema_payload,
)
from aletheia.model_provider.cli_harness import run_cli  # pyright: ignore[reportMissingImports]

_EXEC_TIMEOUT = 300
_PROBE_TIMEOUT = 20


class PiCLIProvider(ModelProvider):
    name = "pi_cli"

    def __init__(
        self,
        command: str = "pi",
        enabled: bool = False,
        model: str | None = None,
    ):
        self.command = command or "pi"
        self.enabled = enabled
        self.model = model if model and model != "default" else None

    def _base_argv(self) -> list[str]:
        return [
            self.command,
            "--offline",
            "--no-session",
            "--no-tools",
            "--no-extensions",
            "--no-skills",
            "--no-prompt-templates",
            "--no-context-files",
            "--no-approve",
        ]

    async def complete(self, request: ModelRequest) -> ModelResponse:
        if not self.enabled:
            raise RuntimeError("Pi CLI provider is not enabled")
        if not shutil.which(self.command):
            raise RuntimeError(f"Pi CLI command '{self.command}' not found in PATH")

        prompt_parts: list[str] = []
        if request.system:
            prompt_parts.append(f"system: {request.system}")
        prompt_parts.extend(f"{message['role']}: {message['content']}" for message in request.messages)
        argv = [*self._base_argv(), "-p"]
        if self.model:
            argv.extend(("--model", self.model))
        try:
            result = await run_cli(
                argv,
                stdin="\n\n".join(prompt_parts).encode("utf-8"),
                timeout=_EXEC_TIMEOUT,
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError("Pi CLI timed out") from exc
        if result.returncode != 0:
            detail = redact_log_text(result.stderr.decode("utf-8", "replace").strip())[:300]
            raise RuntimeError(f"Pi CLI failed: {detail or 'unknown error'}")
        text = result.stdout.decode("utf-8", "replace").strip()
        if not text:
            raise RuntimeError("Pi CLI returned empty output")
        return ModelResponse(content=text, model=self.model or "pi", usage={})

    async def structured(self, request: StructuredModelRequest, schema: type) -> dict:
        schema_payload = model_json_schema_payload(schema)
        messages = list(request.messages)
        messages.append(
            {
                "role": "user",
                "content": (f"Respond with only JSON matching this schema: {json.dumps(schema_payload)}"),
            }
        )
        response = await self.complete(
            ModelRequest(
                messages=messages,
                system=request.system,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
            )
        )
        return _extract_json(response.content)

    async def list_models(self) -> list[dict]:
        if not shutil.which(self.command):
            return []
        try:
            result = await run_cli(
                [self.command, "--offline", "--list-models"],
                timeout=_PROBE_TIMEOUT,
            )
        except (asyncio.TimeoutError, OSError):
            return []
        if result.returncode != 0:
            return []
        models: list[dict] = []
        for line in result.stdout.decode("utf-8", "replace").splitlines()[1:]:
            parts = line.split()
            if len(parts) < 2:
                continue
            provider, model = parts[0], parts[1]
            slug = f"{provider}/{model}"
            models.append({"slug": slug, "display_name": slug})
        return models

    async def healthcheck(self) -> ModelHealth:
        if not self.enabled:
            return ModelHealth(provider=self.name, available=False, error="disabled")
        if not shutil.which(self.command):
            return ModelHealth(provider=self.name, available=False, error="not_installed")
        try:
            version = await run_cli(
                [self.command, "--version"],
                timeout=_PROBE_TIMEOUT,
            )
        except (asyncio.TimeoutError, OSError) as exc:
            return ModelHealth(
                provider=self.name,
                available=False,
                error=f"probe_failed:{type(exc).__name__}",
            )
        if version.returncode != 0:
            return ModelHealth(
                provider=self.name,
                available=False,
                error="version_check_failed",
            )
        models = await self.list_models()
        if not models:
            return ModelHealth(
                provider=self.name,
                available=False,
                error="no_configured_models",
            )
        slugs = [model["slug"] for model in models]
        selected = self.model or slugs[0]
        return ModelHealth(
            provider=self.name,
            available=True,
            model=selected,
            models=slugs,
            model_metadata=models,
        )


def _extract_json(text: str) -> dict:
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        value = None
    start = text.find("{")
    if start < 0:
        raise RuntimeError("Pi structured output contained no JSON object")
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(text[start : index + 1])
                except json.JSONDecodeError:
                    break
                if isinstance(value, dict):
                    return value
                break
    raise RuntimeError("Pi structured output contained no parseable JSON object")
