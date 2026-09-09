# Archived local simulated runtime scope

`crypto-stock-linked-paper-mainline-v2` declared `paper_internal` as its active
execution mode, with `trading_decision.evaluate_trading_opportunity` as decision
authority and a paper wrapper before Gateway. That simulator route required Paper
permission and could pass exchange completeness without an external account.
Startup also accepted supported demo adapters by unioning a hardcoded mode set
with the scope declaration; automatic update accepted `paper_internal/local_paper`
as a healthy deployment target. These definitions and exemptions are retired.

The current [runtime scope](../../product/ACTIVE_RUNTIME_SCOPE.md) describes explicit
external accounts. Startup mode must be both registered and declared. Existing
historical records retain their scope, hashes and economics; they do not become
external-account qualification. The automatic-update gate retains only its
existing Bitget Demo target; retirement does not authorize a new deployment route.

The stability report also counted `paper*` proposals without `strategy_id` as
strategy drift and deducted up to 30 points. That metric belongs to the retired
strategy-to-simulator loop and has been removed from the current report and
score. Current Operator proposals leave this historical binding empty; archived
proposals remain readable without being graded by that retired requirement.
The existing causal-chain and agent-run failure checks remain in place.
