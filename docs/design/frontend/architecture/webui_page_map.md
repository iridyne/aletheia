# WebUI page map

Status: current route and ownership map. Historical migrations live in their
dated design documents and archive; they are not active page authority.

## Global routes

| Route | Owner-facing purpose | Primary facts |
| --- | --- | --- |
| `/` | Overview | Account/PnL, Entry summary, Entry Plan activity, Risk and runtime state |
| `/command-center` | Command Center | Operator commands and supervised runtime actions |
| `/universe` | Entry Workbench | Candidate attention, Entry Plans, Planning History |
| `/agent-activity` | Agent Activity | Agent runs and genuinely independent legacy Opportunity activity |
| `/trades` | Trades & Positions | Orders, fills, positions, exact causal chain |
| `/charts` | Markets | Instrument data and exact Entry Plan overlay when linked |
| `/risk` | Risk & Controls | Risk state, limits, kill switch |
| `/learning` | Learning | Learning evidence and governance |
| `/settings` | Settings | Configuration, including Universe/Candidate attention bounds and protected Candidate Selection qualification/admission/rollback |
| `/usage` | Usage | Runtime/model usage observability |
| `/prediction-markets` | Prediction Markets | Prediction observer facts |
| `/serenity-lens` | Serenity Lens | Long-horizon research |
| `/news` | News Intel | Collected news evidence |

`/blotter` and `/proposals` are compatibility redirects to
`/universe?tab=plans`. They own no unique UI. `/paper-trades` and `/positions`
remain compatibility redirects into Trades & Positions.

## Navigation information architecture

- Global: Overview, Command Center.
- Research: Markets, Prediction Markets, Serenity Lens, News Intel.
- Decision: Entry Workbench, Agent Activity.
- Portfolio: Trades & Positions, Risk & Controls.
- Global utilities: Learning, Usage, Settings.

The command palette uses the same destination names. Entry Workbench is the
primary owner page; Universe remains a backend/Settings subsystem name.

## Entry Workbench

See [entry_workbench_design.md](entry_workbench_design.md). The default tab reads
the Entry Plan ledger directly. Candidates read active planning-generation
attention, not fallback symbols or a newest snapshot join. Planning History is a
bounded projection over existing ledgers. There is no Manual Review tab because
no active producer persists explicit human-review authority.

Exact deep links:

- `/universe?tab=plans&plan_id=<id>`
- `/universe?tab=candidates&candidate_id=<id>`
- `/trades?tab=blotter&entry_plan_id=<id>`
- `/charts?symbol=<symbol>&exchange=<venue>&market_type=<type>&entry_plan_id=<id>`

## Overview and execution ownership

Overview's EntrySummaryStrip reads active Entry Plans, Candidate work needing
planning, and five-minute warnings from the backend ledger projection. AgentFeed
emits exact Entry Plan identity for the current path. AccountSnapshot shows Working
Orders rather than pre-trade pending Proposal counts.

Trades & Positions owns execution inspection. For current Entry Plan orders its
causal chain is Candidate → Entry Plan → Risk → Order → Fill → Position. Older
independent paths retain their persisted Opportunity/Proposal stages and are
labelled as compatibility facts rather than renamed.

## Availability and responsive contract

Missing data is never converted to zero, healthy, or an empty population. Pages
distinguish loading, unavailable, stale cached, successful empty, disabled, and
not-configured states. Shared tabs retain arrow/Home/End keyboard behavior. The
canonical breakpoint scale is 640, 768, 1024, and 1280 pixels; narrow layouts keep
semantic reading order and use table overflow rather than removing fields.
