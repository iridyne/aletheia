"""Claude Code CLI model provider — delegates to the locally-authenticated `claude` binary.

Uses `claude -p` (print mode) for non-interactive completions. Authentication is
owned by the Claude Code CLI itself (OAuth / API key in ~/.claude); we store NO
credentials. Two execution modes:

  1. Text mode (complete): --max-turns 1, no tools → fast, cheap (~$0.002/call)
  2. Schema mode (structured): --json-schema + --allowedTools StructuredOutput
     + --max-turns 2 → native validation (~$0.027/call, used as fallback)

Default path is text mode + _extract_json for structured calls (16x cheaper).
"""

import asyncio
import json
import logging
import shutil

from aletheia.gateway.logs import redact_log_text
from aletheia.model_provider.cli_harness import run_cli
from aletheia.model_provider.base import (
    ModelHealth,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    StructuredModelRequest,
    model_json_schema_payload,
)

logger = logging.getLogger("aletheia.model_provider.claude_cli")

_EXEC_TIMEOUT = 120
_PROBE_TIMEOUT = 15


class ClaudeCLIProvider(ModelProvider):
    name = "claude_cli"

    def __init__(self, command: str = "claude", mode: str = "print",
                 enabled: bool = False, model: str | None = None):
        self.command = command
        self.mode = mode
        self.enabled = enabled
        self.model = model if model else None

    async def complete(self, request: ModelRequest) -> ModelResponse:
        if not self.enabled:
            raise RuntimeError("Claude CLI provider is not enabled")
        if not shutil.which(self.command):
            raise RuntimeError(f"Claude CLI command '{self.command}' not found in PATH")

        prompt = ""
        if request.system:
            prompt += request.system + "\n\n"
        for msg in request.messages:
            prompt += f"{msg['role']}: {msg['content']}\n"

        argv = [
            self.command,
            "-p",
            "--output-format",
            "json",
            "--bare",
            "--disallowed-tools",
            "*",
            "--max-turns",
            "1",
        ]
        if self.model:
            argv += ["--model", self.model]

        try:
            result = await run_cli(
                argv,
                stdin=prompt.strip().encode("utf-8"),
                timeout=_EXEC_TIMEOUT,
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError("Claude CLI timed out") from exc

        if result.returncode != 0:
            detail = redact_log_text(result.stderr.decode("utf-8", "replace").strip())[:300]
            stdout_text = result.stdout.decode("utf-8", "replace").strip()
            # Claude CLI returns exit 1 but still has valid JSON with error info
            if stdout_text:
                try:
                    data = json.loads(stdout_text)
                    if data.get("result"):
                        return self._parse_response(data)
                    err_msg = data.get("result", detail)
                    raise RuntimeError(f"Claude CLI error: {err_msg}")
                except json.JSONDecodeError:
                    data = None
            raise RuntimeError(f"Claude CLI failed (rc={result.returncode}): {detail}")

        raw_out = result.stdout.decode("utf-8", "replace").strip()
        if not raw_out:
            raise RuntimeError("Claude CLI returned empty output")

        try:
            data = json.loads(raw_out)
        except json.JSONDecodeError:
            raise RuntimeError("Claude CLI returned non-JSON output")

        return self._parse_response(data)

    def _parse_response(self, data: dict) -> ModelResponse:
        text = data.get("result", "")
        if not text and data.get("is_error"):
            raise RuntimeError(f"Claude CLI error: {data.get('result', 'unknown')}")
        usage = data.get("usage") or {}
        model_usage = data.get("modelUsage") or {}
        model_name = next(iter(model_usage.keys()), self.model or "claude")
        return ModelResponse(content=text, model=model_name, usage=usage)

    async def structured(self, request: StructuredModelRequest, schema: type) -> dict:
        schema_json = model_json_schema_payload(schema)

        # Primary path: text mode + extract JSON (fast, cheap)
        prompt = ""
        for msg in request.messages:
            prompt += f"{msg['role']}: {msg['content']}\n"
        prompt += f"\nRespond with ONLY valid JSON matching this schema: {json.dumps(schema_json)}"

        mr = ModelRequest(
            messages=[{"role": "user", "content": prompt.strip()}],
            system=request.system,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
        resp = await self.complete(mr)
        try:
            return _extract_json(resp.content)
        except RuntimeError:
            pass

        # Fallback: native --json-schema mode (slower, more reliable)
        logger.info("Text-mode JSON extraction failed, falling back to schema mode")
        return await self._structured_schema_mode(request, schema_json)

    async def _structured_schema_mode(self, request: StructuredModelRequest, schema_json: dict) -> dict:
        if not shutil.which(self.command):
            raise RuntimeError(f"Claude CLI command '{self.command}' not found in PATH")

        prompt = ""
        if request.system:
            prompt += request.system + "\n\n"
        for msg in request.messages:
            prompt += f"{msg['role']}: {msg['content']}\n"

        argv = [
            self.command,
            "-p",
            "--output-format",
            "json",
            "--bare",
            "--json-schema",
            json.dumps(schema_json),
            "--allowedTools",
            "StructuredOutput",
            "--max-turns",
            "2",
        ]
        if self.model:
            argv += ["--model", self.model]

        try:
            result = await run_cli(
                argv,
                stdin=prompt.strip().encode("utf-8"),
                timeout=_EXEC_TIMEOUT,
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError("Claude CLI structured mode timed out") from exc

        raw_out = result.stdout.decode("utf-8", "replace").strip()
        if not raw_out:
            raise RuntimeError("Claude CLI structured mode returned empty output")

        try:
            data = json.loads(raw_out)
        except json.JSONDecodeError:
            raise RuntimeError("Claude CLI structured mode returned non-JSON")

        if data.get("structured_output") and isinstance(data["structured_output"], dict):
            return data["structured_output"]

        # Last resort: parse from result text
        result_text = data.get("result", "")
        if result_text:
            return _extract_json(result_text)

        raise RuntimeError("Claude CLI structured mode produced no parseable output")

    async def list_models(self) -> list[dict]:
        return [
            {"slug": "opus", "display_name": "Claude Opus"},
            {"slug": "sonnet", "display_name": "Claude Sonnet"},
            {"slug": "haiku", "display_name": "Claude Haiku"},
        ]

    async def healthcheck(self) -> ModelHealth:
        if not self.enabled:
            return ModelHealth(provider=self.name, available=False, error="disabled")
        if not shutil.which(self.command):
            return ModelHealth(provider=self.name, available=False, error="not_installed")
        try:
            result = await run_cli(
                [self.command, "--version"],
                timeout=_PROBE_TIMEOUT,
            )
            if result.returncode != 0:
                return ModelHealth(provider=self.name, available=False, error="version_check_failed")
            version = result.stdout.decode("utf-8", "replace").strip()
        except Exception as e:
            return ModelHealth(provider=self.name, available=False, error=f"probe_failed:{e}")

        models_list = await self.list_models()
        slugs = [m["slug"] for m in models_list]
        return ModelHealth(
            provider=self.name,
            available=True,
            model=self.model or "opus",
            models=slugs,
            model_metadata=models_list,
        )


def _extract_json(text: str) -> dict:
    """Parse JSON from text, tolerating surrounding prose. Raises if none found."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == open_ch:
                depth += 1
            elif text[i] == close_ch:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
    raise RuntimeError("Claude CLI structured output contained no parseable JSON")
