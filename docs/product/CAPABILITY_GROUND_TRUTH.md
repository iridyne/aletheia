# Aletheia Capability Ground Truth

The current declared scope is `crypto-stock-linked-exchange-mainline-v3` in
[Active Runtime Scope](ACTIVE_RUNTIME_SCOPE.md). It admits explicitly configured
`bitget_demo`, `okx_demo`, and `binance_testnet` accounts for crypto spot/perpetuals
and the narrowly declared, verified stock-linked perpetual profile. System
Operator owns trading decisions; Risk Engine approval precedes Execution Gateway
submission. Genuine outcomes require verified economic evidence.

## Current capability boundary

| Surface | Current boundary |
| --- | --- |
| External Demo/Testnet routes | Registered in active scope; registration does not prove account credentials, exact instrument capability, or end-to-end qualification |
| Universe-linked Entry Plan | Current Bitget Demo route requires Full permission; other registered routes do not inherit that admission |
| External order, Fill, Position and close settlement | Existing exchange evidence paths; historical simulator acceptance does not qualify these paths |
| Account equity and performance | External account evidence with explicit completeness; historical simulator records keep their original identity and economics |
| Serenity research and preferences | Experimental; retirement does not promote their capability status |
| Live execution | Outside the supported surface; existing explicit owner authority and qualification requirements remain |
| Ordinary equities and prediction markets | Outside active instrument scope; the separately authorized CN extension remains disabled and is not a runtime support claim |
| Retired simulator | Archived; no current execution, account, matching, protection, settlement, or qualification capability |
| Retired CLI/native/OAuth/workspace model providers | Codex, Claude and Pi model subprocesses, fixed-origin native adapters, Databricks workspace/serving transport, subscription OAuth and Codex device login are removed; historical config remains inert and never silently rebinds to an HTTP endpoint. See the [inference boundary](../design/contracts/llm_inference_harness_contract.md). |
| HTTP task-tier model selection | Fast and Deep select explicit endpoint/model pairs, including different models on one endpoint. Settings verifies the selected pair before assignment; unassigned tiers remain unavailable without borrowing Default. Historical provider-only roles are read-only decoded, not runtime aliases. Local deterministic checks do not prove a deployed endpoint or successful remote model call. |

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
