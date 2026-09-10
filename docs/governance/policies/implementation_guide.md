# Aletheia Implementation Guide

This page routes implementation work to current product scope and subsystem
contracts. Read the repository [Agent Guide](../../../AGENTS.md) and every matched
work-area guide before acting. The [original v1 implementation specification](../../design/history/implementation_guide_v1.md)
is archived; its directory sketches, simulated-exchange tools, API examples and
strategy-promotion milestones do not define current requirements.

## Current implementation boundary

The entry mainline is Candidate → Planning Generation → Planner → Compiler →
Entry Plan. Entry Plan currently admits Bitget Demo only. Orders must pass Risk
approval and the backend Execution Gateway, Coordinator and UTA before reaching
an explicitly configured external account. A registered account mode is not
proof of configuration, admission, private fills or runtime qualification.

Live remains disabled by default and requires its separate human-controlled
unlock and execution authority. Agents cannot read or expose credentials, bypass
Risk/Gateway, or grant themselves additional permissions. A missing account must
remain unavailable; it cannot select or fund a fallback account.

Historical records retain their original execution mode, identity, hashes and
economics. Retired simulator records remain readable but cannot create new
trading state or qualify current external execution or Learning evidence.

## Authoritative references

| Work | Current authority |
|---|---|
| Product boundaries and evidence | [Product Scope](../../product/SCOPE.md), [Active Runtime Scope](../../product/ACTIVE_RUNTIME_SCOPE.md), [Admission Gates](../../product/gates/PHASE_GATES.md) |
| Risk and execution | [Risk Engine](../../design/contracts/risk_engine_contract.md), [Execution](../../design/contracts/execution_contract.md), [Live Unlock](../../design/contracts/live_unlock_contract.md) |
| State and data | [State and Data guide](../../operations/development/agent-guides/STATE_AND_DATA.md) |
| Changes, tests, Git and runtime verification | [Development Workflow](../../operations/development/agent-guides/DEVELOPMENT_WORKFLOW.md), [Trust and Change Control](trust_and_change_control.md) |

Use the smallest focused change and evidence that can establish the requested
behavior. This navigation page creates no separate deployment, CI, credential,
trading, promotion or semantic-change authorization.
