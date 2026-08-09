---
type: overview
title: Web Frontend Overview
description: React/Vite dashboard (14 pages) served through the FastAPI gateway front door. Includes the AI Jury committee workbench, trading blotter, risk controls, agent activity, and local-first auth.
tags: [frontend, web, dashboard, react, vite, monitoring]
---

# Web Frontend Overview

The **Web Frontend** (`frontend/src/`) is Aletheia's React/Vite browser-based monitoring and control interface. The FastAPI gateway (`gateway/frontdoor.py`) serves the built SPA and proxies `/api/` routes to the backend. Authentication is **local-first by default** — when neither tunnel nor remote control is enabled, the front door skips auth entirely (trusted local loopback + operator shell). When a tunnel or remote-control-with-token is configured, the `_auth_middleware` gates `/api/` requests behind session cookies, with public allowlists for `/_infra/`, `/api/auth/`, `/login`, `/api/operator/`, `/api/ready`, `/api/status`, `/api/health`, and static assets.

## Architecture

```mermaid
flowchart TD
    A[Browser] --> B[FastAPI Front Door\n(gateway/frontdoor.py)]
    B --> C{Local-first?}
    C -->|No tunnel + no remote token| D[Auth skipped]
    C -->|Tunnel or remote token| E[Session cookie gate]
    D --> F[React/Vite SPA\nfrontend/src/]
    E --> F
    F --> G[/api/ proxy]
    G --> H[Backend services]
```

## Technology Stack

| Layer | Technology |
|---|---|
| **Framework** | React + Vite |
| **Charts** | Recharts |
| **Data fetching** | @tanstack/react-query |
| **Routing** | React Router |
| **State** | React hooks |
| **Backend proxy** | FastAPI front door (`gateway/frontdoor.py`) |
| **Testing** | Vitest |

## Key Pages

| Route | Component | Description |
|---|---|---|
| `/` | `Overview` | System status, P&L, regime |
| `/charts` | `Charts` | Market charts |
| `/news` | `NewsIntel` | News intelligence |
| `/blotter` | `Blotter` | Trading blotter (proposals, approve/sign-off) |
| `/agent-activity` | `AgentActivity` | Agent run transparency |
| `/committee` | `Committee` | **AI Jury** — standalone Investment Committee workbench (persisted committee_reports) |
| `/trades` | `TradesPositions` | Trades & positions |
| `/risk` | `RiskControls` | Risk & controls |
| `/serenity-lens` | `SerenityLens` | Regime assessment |
| `/prediction-markets` | `PredictionLens` | Prediction market signals |
| `/universe` | `Universe` | Universe scanner board |
| `/learning` | `Learning` | AT-01 learning system |
| `/usage` | `Usage` | Usage analytics |
| `/settings` | `Settings` | System settings |

## AI Jury (Committee Workbench)

The `/committee` route renders the standalone **AI Jury** workbench (`frontend/src/pages/Committee.tsx`). It consumes `GET /api/committee/reports` (`api.getCommitteeReports({ symbol?, limit? })`) which returns:

- `CommitteeOverviewResponse` with `status: "ok" | "read_failed"`, a `window` descriptor, a `summary` (direction mix, avg confidence, avg explainability, blocked/fallback counts, model usage, symbol count, latest_at), and a list of `CommitteeEvaluation` (parsed opinions, evidence, risks, explainability score, model used).
- Advisory only: no writes, no trade/risk influence. Read failures render as honest "not available" (`status="read_failed"`).

The page is a cross-opportunity ledger over the same append-only `committee_reports` rows that power the per-opportunity `DecisionTraceDrawer`.

## API Surface (selected)

**Source:** `frontend/src/api/client.ts`

| Method | Description |
|---|---|
| `api.getCommitteeReports({ symbol?, limit? })` | Recent persisted committee evaluations (newest first) + window summary. Read-only advisory surface. |
| `api.getHealth()`, `api.getReady()`, `api.getStatus()` | System health/ready/status |
| `api.getDecisionReport(opportunityId)` | Full decision report for an opportunity |

## Command Palette

`frontend/src/components/CommandPalette.tsx` wires keyboard navigation to every page, including `nav-committee` ("Go to AI Jury").

## Test Coverage

- `frontend/src/pages/Committee.test.tsx` — AI Jury workbench honesty: failure/absence shapes render "not available" / honest notes — never invented verdicts, never benign zeros.

## Relationship to Other Components

- [Investment Committee](../components/committee.md) — AI Jury renders persisted committee_reports
- [Investment Committee System](../components/investment-committee.md) — Committee evaluation system
- [Risk Engine](../components/risk-engine.md) — Risk metrics
- [Position Manager](../components/position-manager.md) — Position display
- [Data Layer](../data-layer/overview.md) — Database access
- [Learning System](../components/learning-system.md) — AT-01 display
