# design/ - System Contracts And Architecture

This is the main implementation authority after governance and product scope. Read the narrow folder first; do not scan all design files.

## Subfolders

| Folder | Use |
|---|---|
| `contracts/` | Gold-standard behavioral contracts: risk, execution, learning, scheduler, audit, persistence |
| `decisions/` | Durable architectural decisions: why long-lived choices were made and how they evolved |
| `proposals/` | Substantial future designs before they become current contracts |
| `data/` | Data model, data utilization, market data observability, collectors, source alignment |
| `api/` | API reference and API compatibility rules |
| `architecture/` | System map and architecture guides |
| `operator/` | System Operator and PI Operator design |
| `frontend/` | Web UI information architecture and design system |
| `market-systems/` | Equity, prediction market, freshness, filters, and market-related subsystem designs |
| `research/` | Design research that has not become a contract |
| `history/` | Frozen historical operator specs and crosswalks |
| `subsystems/` | Smaller subsystem designs without a larger folder yet |

## Fast Routing

| If changing... | Read first |
|---|---|
| Long-lived architecture direction or previous design choice | `decisions/` then affected `contracts/` |
| Risk checks, kill switch, sizing | `contracts/risk_engine_contract.md` + `../governance/policies/trust_and_change_control.md` |
| Order path, paper/live execution, position closure | `contracts/execution_contract.md` |
| Live unlock or permission surfaces | `contracts/live_unlock_contract.md` |
| API route behavior | `api/api_reference.md` |
| SQLite tables or persistence semantics | `data/data_model.md` and `contracts/persistence_recovery_contract.md` |
| Opportunity → Ranker → Operator → Proposal lifecycle | `contracts/decision_lifecycle_contract.md`, then `contracts/filter_evidence_contract.md` and `api/api_reference.md` |
| Learning or knowledge behavior | `contracts/learning_contract.md`, `contracts/knowledge_contract.md`, then any accepted learning proposal under `proposals/` |
| Scheduler behavior | `contracts/scheduler_contract.md` |
| Frontend structure | `frontend/ui_design_system.md`, `frontend/webui_page_map.md` |

## Agent Rule

Contracts describe current approved behavior. Decisions describe why the system
chose its current direction. Proposals describe a substantial future change
without claiming it exists. A Proposal is required only for a major new
capability, a cross-domain change, or a trust-boundary change. Ordinary bugs use
a GitHub Issue and PR. If code and contract differ, verify the current behavior
and file or update a GitHub Issue unless the task explicitly changes the
contract.
