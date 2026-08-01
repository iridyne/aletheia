#!/usr/bin/env python3
"""Run the Phase 5 paired harness-header ablation against a configured model."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from aletheia.model_provider.base import ModelRequest
from aletheia.model_provider.fallback import complete_with_fallback
from aletheia.model_provider.router import get_provider


HEADER = """[Aletheia Harness v0.1]
Time: 2026-08-01T00:00:00Z
Run: logical_agent={caller}
Trading mode: paper_internal live_enabled=false
Tool permissions: read_only no:live_order

[Caller system prompt follows]
"""

CALLERS: dict[str, dict[str, Any]] = {
    "market_observer": {
        "system": "You are a crypto market observer. Use only supplied facts. Return ONLY raw JSON with direction_bias, confidence, confidence_score, rationale, key_observations; no markdown or prose.",
        "user": "Market BTC/USDT: price={price}; RSI1h={rsi}; momentum4h={momentum}; spread_bps={spread}. Return one market read.",
        "required": {"direction_bias", "confidence", "confidence_score", "rationale", "key_observations"},
        "max_tokens": 700,
    },
    "pi_trading_decision": {
        "system": "You are the Trading Decision Authority for an autonomous paper trading system. Focus on WHAT to trade, not HOW MUCH. Return ONLY raw JSON with action, direction, conviction, reasoning; no markdown or prose.",
        "user": "Candidate BTC/USDT perp: price={price}; RSI1h={rsi}; momentum4h={momentum}; spread_bps={spread}; risk checks run later. action must be trade, skip, or watch.",
        "required": {"action", "direction", "conviction", "reasoning"},
        "max_tokens": 800,
    },
    "risk_critic": {
        "system": "You are a risk critic. Identify patterns and advise only; never change hard limits. Keep under 200 words.",
        "user": "Recent proposals: {proposals}; rejections: {rejections}; dominant violation: position_size_cap. Write a concise risk lesson.",
        "required": None,
        "max_tokens": 300,
    },
    "research_worker": {
        "system": "You are a market research worker. Use supplied evidence only. Return ONLY a raw JSON array of claims with claim, confidence, and evidence fields; no markdown or prose.",
        "user": "Evidence: BTC price={price}; RSI1h={rsi}; 4h momentum={momentum}; spread={spread} bps. Return 1-2 bounded claims.",
        "required": {"claim", "confidence", "evidence"},
        "array": True,
        "max_tokens": 700,
    },
    "dreaming_agent": {
        "system": "You are a trading lesson extractor. Return ONLY a raw JSON array with summary, category, severity, and evidence; no markdown or prose. Ground every lesson in supplied metrics.",
        "user": "Cases: wins={wins}, losses={losses}, avg_pnl_pct={avg_pnl}, factor=momentum, scanner=breakout. Extract 1-2 lessons.",
        "required": {"summary", "category", "severity", "evidence"},
        "array": True,
        "max_tokens": 700,
    },
}


def _facts(index: int) -> dict[str, Any]:
    return {
        "price": round(60_000 + index * 17.25, 2),
        "rsi": 30 + index % 41,
        "momentum": round(((index % 17) - 8) / 10, 2),
        "spread": round(0.4 + index % 13 * 0.15, 2),
        "proposals": 8 + index % 13,
        "rejections": index % 7,
        "wins": 3 + index % 9,
        "losses": 2 + (index * 3) % 8,
        "avg_pnl": round(((index % 15) - 7) / 20, 3),
    }


def _parse_json(content: str) -> Any:
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    return json.loads(text)


def _compliant(content: str, spec: dict[str, Any]) -> bool:
    required = spec.get("required")
    if required is None:
        return bool(content.strip()) and len(content.split()) <= 250
    try:
        value = _parse_json(content)
    except (json.JSONDecodeError, TypeError):
        return False
    items = value if spec.get("array") else [value]
    return bool(items) and all(isinstance(item, dict) and required <= set(item) for item in items)


async def _one(provider_name: str, caller: str, index: int, with_header: bool) -> dict[str, Any]:
    spec = CALLERS[caller]
    system = spec["system"]
    if with_header:
        system = HEADER.format(caller=caller) + system
    user = spec["user"].format(**_facts(index))
    request = ModelRequest(messages=[{"role": "user", "content": user}], system=system, temperature=0, max_tokens=spec["max_tokens"])
    provider = get_provider(provider_name)
    response = await complete_with_fallback(request, provider_name=provider_name, provider=provider)
    usage = response.usage or {}
    return {
        "caller": caller,
        "case_id": f"{caller}-{index:03d}",
        "arm": "header" if with_header else "control",
        "prompt_hash": hashlib.sha256((system + "\n" + user).encode()).hexdigest(),
        "compliant": _compliant(response.content, spec),
        "input_tokens": usage.get("prompt_tokens", usage.get("input_tokens")),
        "output_tokens": usage.get("completion_tokens", usage.get("output_tokens")),
        "content": response.content if caller == "pi_trading_decision" else None,
    }


async def run(provider: str, samples: int, concurrency: int, output: Path) -> None:
    semaphore = asyncio.Semaphore(concurrency)

    async def bounded(caller: str, index: int, header: bool) -> dict[str, Any]:
        async with semaphore:
            return await _one(provider, caller, index, header)

    tasks = [bounded(caller, index, header) for caller in CALLERS for index in range(samples) for header in (False, True)]
    rows = await asyncio.gather(*tasks)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"provider": provider, "model": "claude-haiku-4-5", "samples_per_caller_per_arm": samples, "rows": rows}, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="anthropic_compatible")
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.provider, args.samples, args.concurrency, args.output))


if __name__ == "__main__":
    main()
