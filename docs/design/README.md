
# design/ - System Contracts And Architecture

This is the main implementation authority after governance and product scope. Read the narrow folder first; do not scan all design files.

## Subfolders

| Folder | Use |
|---|---|
| `contracts/` | Gold-standard behavioral contracts: risk, execution, learning, scheduler, audit, persistence |
| `data/` | Data model, data utilization, market data observability, collectors, source alignment |
| `api/` | API reference and API compatibility rules |
| `architecture/` | System map and architecture guides |
| `operator/` | System Operator and PI Operator design |
| `frontend/` | Web UI information architecture and design system |
| `market-systems/` | Equity, prediction market, freshness, filters, and market-related subsystem designs |
| `process/` | SDD methodology and document atlas |
| `research/` | Design research that has not become a contract |
| `history/` | Frozen historical operator specs and crosswalks |
| `subsystems/` | Smaller subsystem designs without a larger folder yet |

## Fast Routing

| If changing... | Read first |
|---|---|
| Risk checks, kill switch, sizing | `contracts/risk_engine_contract.md` + `../governance/policies/trust_and_change_control.md` |
| Order path, paper/live execution, position closure | `contracts/execution_contract.md` |
| Live unlock or permission surfaces | `contracts/live_unlock_contract.md` |
| API route behavior | `api/api_reference.md` |
| SQLite tables or persistence semantics | `data/data_model.md` and `contracts/persistence_recovery_contract.md` |
| Opportunity → Ranker → Operator → Proposal lifecycle | `contracts/decision_lifecycle_contract.md`, then `contracts/filter_evidence_contract.md` and `api/api_reference.md` |
| Learning or knowledge behavior | `contracts/learning_contract.md`, `contracts/learning_evolution_contract.md`, `contracts/learning_observability_contract.md`, `contracts/knowledge_contract.md`; delivery sequence in `../plans/learning/learning_positive_contribution_implementation_plan_2026-07-12.md` |
| Scheduler behavior | `contracts/scheduler_contract.md` |
| Frontend structure | `frontend/ui_design_system.md`, `frontend/webui_page_map.md` |

## Agent Rule

Contracts describe what the system should do. If code and contract differ, file or update an issue unless the task explicitly asks to revise the contract.
