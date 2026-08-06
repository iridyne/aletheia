# Agent Guide: Safety and Trust

Operational checklist for trading, trust-boundary, credential, permission, and
audit work. The canonical policy is
[trust and change control](../../../governance/policies/trust_and_change_control.md).

## Execution boundary

- Agents, dashboards, and Telegram never place orders directly; only the backend
  Execution Gateway may reach an exchange.
- Never unlock live trading, place a live order, change hard risk limits,
  signer/credential logic, or gateway semantics without explicit approval.
- Risk approval precedes execution. Preserve causal IDs through proposal, risk,
  order, fill, and position; audits name the real actor and source.
- Credentials are write-only secrets. Never expose them in logs, prompts,
  Markdown, tests, or Git; never commit `.env`, `.secret.key`, `secrets.enc`, or
  token files.

## Controlled paths

Touch only when the task explicitly requires it:

`aletheia/risk/`, `aletheia/execution/`,
`aletheia/exchanges/bitget/{auth,client}.py`, `aletheia/config/secrets.py`,
`aletheia/agent_runtime/{permissions,tool_registry}.py`,
`aletheia/agent_shell/registry.py`, `aletheia/files/jsonl.py`, and
`aletheia/server/routes/live.py`.

Before changing one, read the canonical policy and affected `tests/specs/`.
Every behavior change needs focused regression coverage and Git/PR/CI evidence.
Stop when a request unexpectedly spans several controlled paths or conflicts
with the canonical policy in a way that changes trading safety.

## Honest behavior

- Do not fabricate readiness, risk state, reviews, or capability.
- Do not repurpose knowledge types or hard-delete knowledge; archive/deprecate.
- Keep tracked product intent separate from processes, databases, local config,
  credentials, and orchestrator sessions.
- Do not edit instructions to grant more authority; restricted tools remain
  unavailable to LLM-driven agents.
