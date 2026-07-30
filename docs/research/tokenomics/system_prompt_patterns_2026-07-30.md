# Per-Caller System Prompt Patterns — 2026-07-30

Frozen research evidence collected while scoping the `tokenomics` branch.
Purpose: sample how each LLM caller assembles its `system` string today
so Phase 5's proposed "uniform harness environment header" is grounded
in real prompt shapes, not an abstract wish list.

Sources sampled: `aletheia/agents/dreaming_agent.py`,
`aletheia/agents/market_observer.py`, `aletheia/agents/risk_critic.py`,
`aletheia/agents/lesson_reviewer.py`,
`aletheia/agents/post_trade_reviewer.py`,
`aletheia/agent_shell/trading_decision.py`,
`aletheia/agent_shell/commands.py`,
`aletheia/agent_shell/runtime.py`,
`aletheia/agents/helpers.py`.

## Where prompts live

Two patterns coexist across the sampled files:

- **Module-level constant**, e.g. `market_observer.py::_SYSTEM_PROMPT`
  ("You are a crypto market observer for Aletheia paper-trading…") —
  used when the prompt is stable and reused across the module.
- **Inline literal at the call site**, e.g. the two `system = (…)`
  blocks in `agent_shell/trading_decision.py::_assess_opportunity` and
  `_reassess_direct_skip`, or the multiline literal inside
  `dreaming_agent.py::_extract_lessons`. Used when the prompt varies
  per code path or must interpolate configuration.

There is no shared prompt-template module. `agents/helpers.py` does
one uniform transformation: for structured calls it prepends
`"/no_think\n"` to the caller's system string (needed for Qwen-series
reasoning models; other providers ignore it). No other automatic
prompt augmentation happens today.

## Common shape components

Across the sampled callers, the same four components appear in the
same order:

1. **Role prefix.** A one-sentence identity: "You are a trading
   lesson extractor", "You are the Trading Decision Authority for an
   autonomous paper trading system", "You are the independent
   counterfactual reviewer for a paper-trading decision", "You are a
   crypto market observer for Aletheia paper-trading", "You are a
   risk critic", "You are the system's nightly consolidation
   process", "You are a portfolio risk reviewer for an autonomous
   paper trading system".
2. **Input contract.** What the caller will feed as `user`: which
   fields the snapshot contains, how to interpret them, what to trust
   as ground truth. Frequent phrase: "Use this snapshot as your
   single source of truth — do not invent numbers".
3. **Output contract.** JSON schema hint, allowed enum values, "no
   markdown fences", "no explanation", "Return ONLY the JSON array".
4. **Behavioral constraints.** Domain-specific rules: "do not force a
   trade", "focus on WHAT to trade, not HOW MUCH", "commit if you
   have evidence", "block_* suggestions never become Risk Engine
   rules".

The role, input contract, and output contract are always present.
The behavioural constraint layer is where callers differ most.

## What every prompt is *missing*

None of the sampled prompts mentions any of the following, though the
runtime knows all of them:

- Current UTC time / wall-clock.
- Enclosing `agent_run.id` or `logical_agent` (both available via
  `run_context` ContextVars — see the attribution flow note).
- `runtime_session.current_provenance()` snapshot identity.
- Trading mode (paper vs live) — the caller usually hard-codes
  "paper-trading" in the role prefix instead.
- Remaining budget for this scope (not available today; Phase 2/3).
- Dry-run or degraded-service signal (not available today; Phase 5).
- A pointer to the current tool permission set for the run.

These are the ingredients a Phase 5 harness header could inject
consistently — the same way Claude Code and PIA prepend a stable
environment block to every model call.

## Sketch of a harness environment header

Non-committal proposal, not final. Structure the header as a compact
prefix the harness prepends to whatever the caller supplied:

```
[Aletheia Harness]
Time: 2026-07-30T12:34:56Z
Run: agent_run_id=…    logical_agent=…
Trading mode: paper_internal    live_enabled=false
LLM budget: remaining=$…/day (scope=logical_agent:pi_trading_decision)
Tool permissions: read_only  |  write:paper_state,paper_ledger  |  no:live_order
Harness version: v0.1

[Caller system prompt follows]
<original system string>
```

Design constraints, matching PR #110's spirit:

- The header must never change what the caller wants the model to do.
  It only adds context the model can consult, not new instructions
  that conflict with the caller's own contract.
- The header must always be present, so agents cannot get
  more-permissive behaviour by omitting it. Making it caller-opt-in
  would let a caller sidestep a future guardrail; making it
  caller-opt-out is safer.
- The header must be short. LLM cost is metered in tokens, and a bulky
  header on every call would burn budget the harness itself is
  supposed to protect. Structured, one-line-per-fact form keeps the
  overhead low and lets the model parse it reliably.
- Fields must be honest. "LLM budget: remaining=$…/day" appears only
  when a budget policy actually declares that scope; otherwise the
  line is omitted rather than showing a placeholder. This matches the
  ledger's `unknown` vs `estimated` vs `known` cost-status
  discipline.

## Insertion point

The natural place to prepend this header is inside
`aletheia/agents/helpers.py`, between the caller-supplied `system`
and the physical provider call. That is the same place that already
prepends `/no_think` for reasoning models, so callers already accept
that helpers.py may mutate the system string. The two bypassers
(`startup_gate`, admin `/api/model/probe`) issue trivial probes and
would not need the header; they should probably continue to bypass
it explicitly.

An alternative is a first message-role="system" in `ModelRequest`
prepended by `complete_with_fallback`, but that pushes into the
provider layer that PR #110 has held to "measurement-only". Keeping
the header in `helpers.py` avoids that boundary.

## What Phase 5 does *not* need

- A prompt-template DSL, an inheritance model, or a per-agent prompt
  registry. Every sampled agent's prompt is short enough to remain
  inline; the harness only prepends a stable environment block.
- Automatic tool-schema injection. Tool permissions are enforced by
  the tool registry, not by the prompt. Mentioning them is a
  transparency signal, not an enforcement mechanism.
- Anything that would let the harness rewrite the caller's role,
  input contract, or output contract. Those are caller-owned.
