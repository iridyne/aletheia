# Settings Control Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `SC` · **Owner code:** `frontend/src/components/ui/Switch.tsx`, `frontend/src/pages/Settings.tsx`

## Scope

Settings is the operator's control surface for persisted configuration. This contract
owns two facts: whether a control's change is already persisted or is only a draft
awaiting Save (the persistence boundary of the shared `Switch` primitive and the
Settings draft lifecycle), and what Settings may claim about Webhook delivery. It does
not own the backend mutation routes, payloads, persisted values or schemas; those
belong to each section's Contract (configuration loading:
[Configuration Loading Contract](configuration_contract.md)).

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `frontend/src/components/ui/Switch.tsx::Switch` | UI primitive | every boolean control in `frontend/src` |
| `frontend/src/components/ui/SettingsSection.tsx::SwitchGrid` | UI primitive | staged editors in `RiskTab.tsx`, `RuntimeSystemTab.tsx`, `HealthNotificationsTab.tsx` |
| `frontend/src/features/settings/dirtyGuard.ts::isFallbackDraftControl` | call | `frontend/src/pages/Settings.tsx` draft fallback |
| `core/aletheia/notifications.py::webhook_delivery_status` | read model | `core/aletheia/server/routes/settings.py::get_settings` (`webhook.last_delivery`) |

**Control inventory.** The inventory is the source itself: every `<Switch` call site in
`frontend/src` (some files import it as `Toggle`). It is not copied here. The required
`persistence` prop is enforced by the TypeScript type of `Switch` and `SwitchGrid`
(`npm run build` runs `tsc -b`) and pinned by
`frontend/src/components/ui/switchLabelling.test.ts`. The last checked-in copy of the
per-site table is [history](../history/settings_control_switch_inventory.md).

## Durable state

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| `config.yaml` sections | the section's Settings route (via `edit_config`) | `load_config` | replaceable document |
| `audit_events` rows with `event_type='webhook_delivery'` | `core/aletheia/notifications.py::_record_webhook_outcome` (outbox, hash-chained mirror) | `core/aletheia/storage/repositories.py::latest_mirrored_webhook_delivery_event` | append-only |

Staged drafts live only in component state; they are not durable.

## Requirements

### SC-C01 Persistence mode is explicit at every call site

`The Switch and SwitchGrid primitives shall require persistence="immediate" or persistence="staged" at every call site and shall never infer it from the callback or the surrounding component.`

- WHEN a call site omits `persistence` THEN the frontend type check fails.
- WHEN `SwitchGrid` is given a mode THEN every expanded switch receives that mode.

Evidence: `frontend/src/components/ui/Switch.tsx::Switch` (`persistence: SwitchPersistence`), `frontend/src/components/ui/SettingsSection.tsx::SwitchGrid`.

### SC-C02 Immediate controls show only confirmed values

`When an immediate control changes, the caller shall send the mutation from the change handler and keep the last confirmed value visible until the mutation succeeds, with each concurrent mutation's error visible until its own source succeeds.`

- WHEN an immediate mutation fails THEN the switch shows the previous confirmed value and an error.

### SC-C03 Staged controls change only a draft

`When a staged control changes, the control shall update only a local draft, render as a native checkbox with the staged style and an accessible "Draft value; save required." description, report dirty state through the Settings draft lifecycle, and persist only through its section's Save action.`

- WHEN a staged switch is toggled and Save is not pressed THEN no backend call is made.
- WHEN Save fails THEN the draft stays dirty and the error stays visible.

Evidence: `frontend/src/components/ui/Switch.tsx::Switch` (`settings-staged-description`, `!staged &&` track).

### SC-C04 Leaving with a dirty draft asks before discarding

`If the operator leaves a Settings tab or the Settings page with a dirty draft, then the Settings shell shall ask for explicit discard confirmation before navigating.`

- WHEN a tab switch is requested with a dirty draft THEN "Discard unsaved changes?" is shown and the draft survives a Cancel.

Evidence: `frontend/src/pages/Settings.tsx::discardDrafts`, `frontend/src/features/settings/dirtyGuard.ts::shouldBlockNavigation`.

### SC-C05 Immediate blur-save fields are not offered for discard

`Where a number field saves on blur, the field shall be marked data-persistence="immediate" so the shell's draft fallback never offers to discard an already-persisted value, and the shell shall suppress the focus-transfer blur when focus moves to Discard.`

- WHEN an immediate number field is edited THEN `isFallbackDraftControl` returns false for it.

Evidence: `frontend/src/features/settings/dirtyGuard.ts::isFallbackDraftControl`; `frontend/src/pages/Settings.tsx::suppressDiscardBlur`.

### SC-C06 Trading parameters stay staged

`While no separately reviewed backend Contract changes their persistence semantics, the Risk, execution and demo-trading parameter controls shall be staged.`

- WHEN a control in `RiskTab.tsx`, `ExecutionTab.tsx::ExchangeMarketsRow` or `frontend/src/features/settings/execution/PaperTradingSettings.tsx::TradingParamsCard` is toggled THEN it is a draft until Save.

### SC-C07 Webhook delivery truth

`The Settings read model shall expose only the newest attempted Webhook delivery that is confirmed in the hash-chained audit mirror, as delivered or failed with timestamp, host-only receiver, source entity, HTTP status or exception class, and latency.`

- WHEN a delivery is disabled or category-suppressed THEN it is `not_attempted`, writes no delivery fact and never overwrites the last attempt.
- WHEN the stored outcome carries a URL path, query, HMAC secret, payload, response body or raw exception text THEN none of it is returned.
- WHEN the audit mirror is pending, failed or unreadable THEN `last_delivery` is `null` (unavailable), never a synthesized outcome; the outbox is reconciled on the next read.

Evidence: `core/aletheia/notifications.py::_send_webhook`, `_record_webhook_outcome`, `webhook_delivery_status`; `core/aletheia/storage/repositories.py::latest_mirrored_webhook_delivery_event` (`jsonl_mirror_status='mirrored'`, ordered by `attempted_at`).

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| immediate mutation fails | keep confirmed value | inline error until that source succeeds | — |
| staged Save fails | keep draft dirty | error on the section | — |
| dirty draft on navigation | block until confirmed | discard confirmation dialog | — |
| Webhook audit mirror pending/failed/unreadable | return `null` | "Last Webhook delivery" shows unavailable | never shown as delivered |

## Authority

Settings controls write only the configuration section their route owns; this contract
changes no backend route, payload, Risk limit, credential or Live unlock.

## Out-of-scope controls still rendered (residual)

The product scope is USDT-margined perpetuals on `bitget_demo` with Live off
([Capability Ground Truth](../../product/CAPABILITY_GROUND_TRUTH.md)). The controls below
still render; they follow SC-C01..SC-C06 but configure closed or frozen scope and are
residual, not supported product behavior:

| Control | Source | Scope status |
|---|---|---|
| Spot market | `frontend/src/features/settings/tabs/ExecutionTab.tsx::ExchangeMarketsRow` | spot out of scope |
| OKX / Binance primary exchange and `okx_demo` / `binance_testnet` execution mode | `ExecutionTab.tsx::PrimaryExchangeCard`, `ExecutionTab.tsx::ExchangeModeRow` | OKX/Binance frozen |
| Observed US equities, tokenized-equity list | `frontend/src/features/settings/tabs/SignalsTab.tsx::EquitiesCard`, `SignalsTab.tsx::MarketDataCard` | equities/RWA out of scope (see configuration CF-C06..C08) |
| Prediction observer | `SignalsTab.tsx::PredictionToggleCard` | prediction markets out of scope; `prediction_observer` job still scheduled |
| Enforce in paper ranker | `frontend/src/features/settings/tabs/AgentModelsTab.tsx::AgentModelsTab` | Ranker retired |
| Live risk fields (`kill_switch_enabled_live`, `allow_short_live`, live policy editor) | `frontend/src/features/settings/risk/riskFields.ts`, `RiskTab.tsx::RiskPolicyEditor` (`mode='live'`) | Live off; fields are dormant |
| Learning toggles (Autonomous learning, Contribution measurement, Paper influence, Persona learning, …) | `AgentModelsTab.tsx::AgentModelsTab`, `PaperTradingSettings.tsx::TradingParamsCard` | learning is owned by [spec 0001](../specs/0001-learning-loop/spec.md) |

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| SC-C01, SC-C03 | `frontend/src/components/ui/switchLabelling.test.ts`, `frontend/src/components/ui/switchLabelling.render.test.tsx` |
| SC-C04, SC-C05 | `frontend/src/features/settings/dirtyGuard.test.ts` |
| SC-C07 | `tests/specs/spec_webhook_delivery_truth.py::SETTINGS-WEBHOOK-S01` |
| SC-C02, SC-C06 | not covered by a dedicated test |

## Known conformance gaps

- The out-of-scope controls in the table above are a control surface for capabilities the
  product does not offer; under "one capability, one live implementation" they are
  retirement candidates (D2, owner decision), not supported behavior.

## Decision references

- Staged controls made explicit: #999 (PR #1068).
- Sanitized Webhook delivery outcomes: #2749.
- Source-count inventory test retired: #3595 (commit 67aa9ab3d).
- Futures-only scope: [DECISIONS 2026-10-09](../../governance/decisions/DECISIONS.md) (#3440).
