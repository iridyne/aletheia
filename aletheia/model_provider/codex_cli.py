"""Codex CLI model provider — delegates to the locally-authenticated `codex` binary.

This is the "ChatGPT Codex subscription / local authenticated CLI mode" of guide
§12.1.3. Real OAuth is owned by the codex CLI (token lives under $CODEX_HOME /
~/.codex); we store NO token. We only: run `codex exec` for completions, detect
login state via `codex login status`, and list models via `codex debug models`.
Everything degrades gracefully (raises only inside complete/structured, which the
agent helpers catch; healthcheck/list_models never raise).
"""

import asyncio
import json
import logging
import shutil

from aletheia.gateway.pidfile import _no_window_kwargs
from aletheia.model_provider.base import (
    ModelProvider, ModelRequest, StructuredModelRequest,
    ModelResponse, ModelHealth,
)

logger = logging.getLogger("aletheia.model_provider.codex")

_EXEC_TIMEOUT = 120
_PROBE_TIMEOUT = 15


class CodexCLIProvider(ModelProvider):
    name = "codex_cli"

    def __init__(self, command: str = "codex", mode: str = "exec",
                 enabled: bool = False, model: str | None = None):
        self.command = command
        self.mode = mode
        self.enabled = enabled
        self.model = model

    # --- completion ---

    async def complete(self, request: ModelRequest) -> ModelResponse:
        if not self.enabled:
            raise RuntimeError("Codex CLI provider is not enabled")
        if not shutil.which(self.command):
            raise RuntimeError(f"Codex CLI command '{self.command}' not found in PATH")

        prompt = request.system or ""
        for msg in request.messages:
            prompt += f"\n{msg['role']}: {msg['content']}"

        # Text completion only — read-only sandbox keeps codex from shell/write
        # surface. `exec` is already non-interactive (no approval prompts), and
        # codex >=0.133 removed the `--ask-for-approval` flag from `exec`, so we
        # must not pass it or the whole command is rejected.
        argv = [
            self.command, "exec", "--json", "--skip-git-repo-check",
            "--sandbox", "read-only",
        ]
        if self.model:
            argv += ["-m", self.model]
        argv.append("-")  # read prompt from stdin

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **_no_window_kwargs(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(prompt.encode("utf-8")), timeout=_EXEC_TIMEOUT
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("Codex CLI timed out")

        if proc.returncode != 0:
            detail = (stderr or b"").decode("utf-8", "replace").strip()[:300]
            raise RuntimeError(f"Codex CLI failed: {detail}")

        text_parts: list[str] = []
        usage: dict = {}
        for line in (stdout or b"").decode("utf-8", "replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue  # codex interleaves non-JSON log lines; skip them
            if not isinstance(evt, dict):
                continue
            if evt.get("type") == "item.completed":
                item = evt.get("item") or {}
                if item.get("type") == "agent_message" and item.get("text"):
                    text_parts.append(str(item["text"]))
            elif evt.get("type") == "turn.completed":
                usage = evt.get("usage") or {}

        text = "".join(text_parts).strip()
        if not text:
            raise RuntimeError("Codex CLI returned no agent message")
        return ModelResponse(content=text, model=self.model or "codex", usage=usage)

    async def structured(self, request: StructuredModelRequest, schema: type) -> dict:
        mr = ModelRequest(messages=request.messages, system=request.system,
                          temperature=request.temperature, max_tokens=request.max_tokens)
        resp = await self.complete(mr)
        return _extract_json(resp.content)

    # --- introspection ---

    async def list_models(self) -> list[dict]:
        """Return [{slug, display_name}] from `codex debug models`. Never raises."""
        if not shutil.which(self.command):
            return []
        try:
            out = await self._run([self.command, "debug", "models", "--bundled"], _PROBE_TIMEOUT)
        except Exception:
            return []
        if out is None:
            return []
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return []
        models = []
        for m in (data.get("models") or []):
            if m.get("visibility") == "list" and m.get("slug"):
                models.append({"slug": m["slug"], "display_name": m.get("display_name", m["slug"])})
        return models

    async def healthcheck(self) -> ModelHealth:
        if not self.enabled:
            return ModelHealth(provider=self.name, available=False, error="disabled")
        if not shutil.which(self.command):
            return ModelHealth(provider=self.name, available=False, error="not_installed")
        try:
            rc = await self._run_rc([self.command, "login", "status"], _PROBE_TIMEOUT)
        except Exception as e:
            return ModelHealth(provider=self.name, available=False, error=f"not_available:{e}")
        if rc != 0:
            return ModelHealth(provider=self.name, available=False, error="not_logged_in")
        models = await self.list_models()
        slugs = [m["slug"] for m in models]
        return ModelHealth(
            provider=self.name, available=True,
            model=self.model or (slugs[0] if slugs else "codex"),
            models=slugs,
        )

    # --- subprocess helpers ---

    async def _run(self, argv: list[str], timeout: int) -> str | None:
        """Run argv, return stdout text if rc==0 else None. Raises on spawn/timeout."""
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            **_no_window_kwargs(),
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise
        if proc.returncode != 0:
            return None
        return (stdout or b"").decode("utf-8", "replace")

    async def _run_rc(self, argv: list[str], timeout: int) -> int:
        """Run argv, return its exit code. Raises on spawn/timeout."""
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            **_no_window_kwargs(),
        )
        try:
            await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise
        return proc.returncode if proc.returncode is not None else 1


def _extract_json(text: str) -> dict:
    """Parse JSON from text, tolerating surrounding prose. Raises if none found."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Find the first balanced {...} or [...] span.
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
    raise RuntimeError("Codex structured output contained no parseable JSON")
