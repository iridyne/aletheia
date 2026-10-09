# Aletheia Capability Ground Truth

The accepted current product boundary is **Slim Core + active Bitget Exchange
Extension + configured HTTP Model Endpoint(s)**, under
[#3134](https://github.com/iridyne/aletheia/issues/3134). Core owns exchange-neutral
trading semantics; concrete venue and model transports belong to Extensions.
OKX/Binance, extra markets, research/Learning and optional surfaces are frozen or
optional, not current Slim Product support. Historical decode/read is not a
runtime capability.

[Active Runtime Scope](ACTIVE_RUNTIME_SCOPE.md) declares the Bitget Demo route
and is consumed by the startup gate. Frozen exchange identities remain valid for
historical provenance and recovery, not new-entry support. Scheme A's physical
source layout and absent-safe Core operation are implemented. Runtime and
economic qualification evidence belongs in #3134 checkpoints; this page does
not independently assert completion.
Risk approval must precede Gateway submission; genuine outcomes require verified
economic evidence.

The TS operator-shell is the sole runtime Operator. The former Python assessment
loop and its fallback configuration/manual trigger are retired under #3134;
historical assessments remain readable. This does not replace the separate
Entry Planner daemon or expand shell permissions.

## How to read project truth

This file is the canonical capability boundary; [Active Runtime Scope](ACTIVE_RUNTIME_SCOPE.md)
is the sole machine-readable active-admission declaration. They answer different
questions and must remain consistent, rather than being copied into another
manifest. See the [governance authority map](../governance/policies/documentation_governance.md#authority-and-conflict-resolution)
for approved behavior, development permission and operational evidence.

An accepted target is not an implemented subsystem. Implemented code is not an
end-to-end test result. A passing test is not a deployed-runtime observation, and
a deployed process is not economic qualification. Claims must retain their own
scope and evidence; an Issue/PR being closed does not bridge these gaps or prove
all of an umbrella program's completion criteria. Track outstanding proof in
GitHub rather than maintaining a mutable completion snapshot in this document.

Frozen new-entry support does not delete historical identity or existing
exposure-recovery obligations. Optional/frozen Research and Learning support does
not switch off the existing measurement or explicitly owner-gated influence
semantics declared in Active Runtime Scope and the relevant Contracts. This
classification grants no new trading, risk, credential or Live authority.

## Current capability boundary

| Surface | Current boundary |
| --- | --- |
| External Demo/Testnet routes | Bitget Demo is the current Slim Product qualification route; retained OKX/Binance registrations are frozen, not current support. Registration never proves credentials or end-to-end qualification. |
| Universe-linked Entry Plan | Current Bitget Demo route requires Full permission; other registered routes do not inherit that admission |
| External order, Fill, Position and close settlement | Existing exchange evidence paths; historical simulator acceptance does not qualify these paths |
| Account equity and performance | External account evidence with explicit completeness; historical simulator records keep their original identity and economics |
| Serenity research and preferences | Experimental; retirement does not promote their capability status |
| Live execution | Outside the supported surface; existing explicit owner authority and qualification requirements remain |
| Instrument scope | USDT-margined perpetual futures on crypto assets are the only product. Spot is out of scope and not planned (owner decision 2026-10-09, [DECISIONS](../governance/decisions/DECISIONS.md)); it never enters the universe registry. Perpetuals the venue marks as real-world assets (tokenized equity, metals, oil, indices; Bitget `isRwa=YES`) are not crypto and are registered blacklisted, not scanned or planned. |
| Ordinary equities and prediction markets | Outside active instrument scope; the separately authorized CN extension remains disabled and is not a runtime support claim |
| Retired simulator | Archived; no current execution, account, matching, protection, settlement, or qualification capability |
| Retired CLI/native/OAuth/workspace model providers | Codex, Claude and Pi model subprocesses, fixed-origin native adapters, Databricks workspace/serving transport, subscription OAuth and Codex device login are removed; historical config remains inert and never silently rebinds to an HTTP endpoint. See the [inference boundary](../design/contracts/llm_inference_harness_contract.md). |
| HTTP task-tier model selection | Fast and Deep select explicit endpoint/model pairs, including different models on one endpoint. Settings verifies the selected pair before assignment; unassigned tiers remain unavailable without borrowing Default. Historical provider-only roles are read-only decoded, not runtime aliases. Local deterministic checks do not prove a deployed endpoint or successful remote model call. |
| Bundle import | Namespaced research only; Core Proposal and retired PS runtime tables cannot be merged from an archive. Historical export/read remains distinct from import permission; full managed backup/restore has its own explicit verification boundary. See [Persistence](../design/contracts/persistence_recovery_contract.md). |

Learning and research remain subject to their existing owner admission and
authority gates. Neither registration, a green focused test, nor an archived
capability status grants trading authority or proves deployed runtime health.

## Evidence and previous reviews

The [previous capability review](../design/history/local_simulated_capability_review.md)
is archived intact. Its 101-row inventory, aggregate status counts, Supported
Core profile and detailed review conclusions were evaluated against the former
scope. They are historical evidence, not a current external-exchange acceptance
matrix. The [capability evidence ledger](capability-integrity/README.md) retains
its source context; claims must be checked against the current implementation
before reuse.

The retirement removes the former local execution, protective exits, local close
settlement and cash-account capabilities recorded as EX-01, EX-08, EX-14 and
PF-01, along with their obsolete controls and producers. Historical records and
reports remain readable; they cannot become external account qualification.
See the [accepted retirement boundary](../design/proposals/retire_local_simulated_exchange.md)
and [archived definitions](../design/history/README.md).

Current external-route qualification must name the exact account environment and
instrument capability, exercise the Risk/Gateway/private Fill/verified-close
chain, and identify the tested commit. Deployment claims additionally require
observed runtime identity and health. Repository retirement alone does not prove
whole-project qualification or a completed Fedora deployment.
