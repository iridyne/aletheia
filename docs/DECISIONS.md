# Decisions

## 2026-06-07 - Merge: adopt remote cockpit, discard local 5-zone shell

### Context

Local branch (`9d91b15`/`edb791b`/`02449f0`) and origin (`ab29932`..`eb50f7f`) independently built an "Agentic Trading Cockpit" touching the same frontend files (`App.tsx`, `index.css`, all `pages/*`). Three-way merge was structurally impossible; one side had to win wholesale. Merge `ace780a` chose origin. This entry is the post-hoc review and ratification.

### Decision

Keep `ace780a` as-is. None of the 18 local-only files (`LeftNav`, `RightInspector`, `BottomTimeline`, `CommandCenter`, `InspectorContext`, `shared/*`, consolidated `Portfolio.tsx`, beefier `NewsIntelligence`, `statusColor.ts`, build artefacts) get cherry-picked back.

### Reasoning

- AGENTS.md priority order ranks user-facing capability at #6; UI shell rework belongs there. State consistency, safety, audit, UI honesty (#1–#4) are not improved by porting any of the 18 files.
- AGENTS.md "No Uncontrolled Expansion" forbids large refactors / styling rewrites — porting a 5-zone shell qualifies.
- north_star_references.md §7 places the project in stage 0 (prove stable run); UI revamp is not a stage 0 task.
- The single local file that maps to a real north-star (`pages/Portfolio.tsx`, consolidated three-tab view) does not actually fix `aletheia-portfolio-sync-gap` — its data still comes from local SQLite only. The real fix is a backend Exchange Account Sync layer; building the storefront before the merchandise inverts the order. Filed as a backend issue (BACKEND_ISSUES.md §2.3) with the consolidated page noted as a candidate UI for after the backend lands.
- News Intelligence local variant conflicts with `issue_register.md::ISSUE-008` (news ingestion should be marked experimental, not expanded).

### Consequences

- `frontend/src/` and `aletheia/dashboard_static/` stay byte-identical to origin.
- Discarded local commits remain reachable through `02449f0` for archaeology.
- `docs/AI_HANDOFF.md` Known Issues list is updated to surface the two unfixed north-star gaps that the previous "no remaining open items" line obscured.
- Full audit and 18-file judgement table archived at `docs/superpowers/specs/2026-06-07-merge-postmortem-design.md`.

### Alternatives Considered

- Cherry-pick the consolidated Portfolio page only — rejected: storefront-before-merchandise; data-source gap is in backend.
- Revert the merge and rebuild on local cockpit — rejected: would discard ~60 backend hardening files for a UI preference, inverting AGENTS.md priority order.

---

## 2026-06-07 - Gateway: Pure ctypes for Windows process management

### Context

The gateway watchdog (every 5 min via Task Scheduler) and crash-recovery path spawned external processes (wmic.exe, tasklist.exe, powershell.exe, taskkill.exe) for liveness checks and orphan cleanup. Despite CREATE_NO_WINDOW flags, these console-subsystem executables still occasionally flashed Windows Terminal windows on Win11.

### Decision

Replace all subprocess-based process queries with pure ctypes Win32 API calls (kernel32 + ntdll):
- `_pid_alive`: OpenProcess + NtQueryInformationProcess(ProcessCommandLineInformation=60)
- `_list_processes`: CreateToolhelp32Snapshot + Process32First/Next
- `kill_pid_tree`: TerminateProcess + BFS child traversal

### Reasoning

1. Zero subprocess = zero chance of console window allocation
2. Faster: in-process ctypes calls vs. spawning + waiting on external .exe
3. NtQueryInformationProcess(60) is stable API available since Windows 10 1511
4. Keeps POSIX paths unchanged (no regression risk for future Linux support)

### Consequences

- Windows-specific code isolated in `aletheia/gateway/_win32_proc.py`
- Tests must be Windows-only for ctypes paths (pytest.mark.skipif)
- If targeting Windows < 10 1511 in future (unlikely), would need fallback

### Alternatives Considered

- Keep subprocess with CREATE_NO_WINDOW: still occasionally flashes on Win11
- Windows Service (pywin32/nssm): adds external dependency, overkill for current scope
- Named pipe heartbeat: more complex, doesn't solve orphan discovery

---

## 2026-06-06 - AI Workspace Initialized

### Context

Project needs cross-device AI collaboration support for seamless handoff between machines.

### Decision

Adopted standard AI workspace structure with context recovery and handoff protocols.

### Reasoning

Enables seamless project handoff between devices and AI tools (Claude Code, Codex, etc.).

### Consequences

All AI sessions should follow start/end protocols documented in AGENTS.md.

### Alternatives Considered

None — process improvement, not technical tradeoff.
