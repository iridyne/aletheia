# Development Log

## 2026-06-07

### Changed

- Documentation housekeeping: archived all fixed items in BACKEND_ISSUES.md (§10) and FRONTEND_ISSUES.md (§10.1–10.24); removed root-level IMPLEMENTATION_GUIDE.md duplicate (canonical copy in docs/)
- Gateway: replaced all Windows subprocess calls (wmic/tasklist/powershell/taskkill) with pure ctypes Win32 API — fully invisible operation, zero console window flashes

### Decision

- Process introspection on Windows uses kernel32/ntdll ctypes instead of spawning external executables (see DECISIONS.md)

## 2026-06-06

### Added

- CLAUDE.md (project overview for Claude Code)
- .claude/ directory (settings, commands: start-session, end-session, update-handoff, project-audit)
- .codex/config.toml (Codex project config)
- docs/PROJECT_CONTEXT.md, docs/CURRENT_TASK.md, docs/AI_HANDOFF.md, docs/DECISIONS.md, docs/DEVLOG.md, docs/AI_WORKFLOW.md

### Fixed (major pass)

- **Backend (80+ fixes)**: All items in BACKEND_ISSUES.md §1-§3 — trust invariants (strategy_id causal chain, execution gateway isolation, git ff-only, promotion gates), data integrity (PnL fees, KB status filtering, CORS, audit symmetry, risk evaluation, scheduler error handling, telegram hardening), dead code cleanup
- **Frontend (24 fixes)**: All items in FRONTEND_ISSUES.md §1-§3 — shared types/DataTable/DetailDrawer components, risk_decision display, KB markdown reading, autonomous_paper_trading toggle, effective config card, promotion gates UI, chart-trade linking, dreaming 6-section layout, overview sparkline, cross-page navigation, agent run tool calls

### Notes

AI workspace initialization. Existing AGENTS.md preserved as-is (already comprehensive).
