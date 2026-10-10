# Live Unlock Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `LU` · **Owner code:** `core/aletheia/server/routes/live.py`, `core/aletheia/trust/live_unlock.py`
> Retired narrative: [history](../history/risk_kill_live_contract_narrative.md).

## Scope

Live Unlock is the human-only gate that moves `live.state` from `live_disabled` to
`live_limited` after a private preflight of the selected live account and a go-live
scorecard check, and moves it back on lock or when the evidence goes stale. Live is out
of product scope and off by default; unlocking records a permission prerequisite only —
no Entry Plan is routed to a live account (§4b). It does not own order placement
([execution_contract](execution_contract.md)), Risk checks
([risk_engine_contract](risk_engine_contract.md)) or the scorecard's criteria
(`analytics/go_live_scorecard.py`).

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `core/aletheia/server/routes/live.py::live_unlock` | API route `POST /api/settings/live-unlock` | Dashboard Settings → Execution tab |
| `core/aletheia/server/routes/live.py::live_lock` | API route `POST /api/settings/live-lock` | Dashboard Settings → Execution tab |
| `core/aletheia/server/routes/live.py::live_preflight` | API route `POST /api/settings/live-preflight` | Dashboard Settings → Execution tab |
| `core/aletheia/trust/live_unlock.py::revoke_stale_live_unlock` | call | `server/routes/health.py::status` (`GET /api/status`), Risk check #3, `execution/gateway.py::_execute_proposal` |
| `core/aletheia/analytics/execution_mode.py::resolve_selected_execution_mode` | call | `server/routes/analytics.py`, `server/routes/ui.py` |

## Durable state

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| config `live.state`, `live.first_live_confirmation_done`, `live.unlock_evidence` | `live.py::live_unlock` / `live_lock`, `trust/live_unlock.py::revoke_stale_live_unlock` (via `save_config`) | Risk check #3, Gateway, `/api/status` | mutable; every transition audited |
| config `operator.permission_mode` | same writers | `agent_shell/permissions.py::check_permission` | mutable |
| `audit.jsonl` | `files/jsonl.py::write_audit` | audit mirror | append-only (rotation renames, never deletes) |

## Requirements

### 1. Default state

#### LU-C01 Live is off by default
`The configuration shall default live.state to live_disabled, and the Risk Engine shall reject every live-mode Proposal unless live.state is exactly live_limited.`
- WHEN `live.state` holds any other value (typo, unknown) THEN Risk rejects with `live_disabled` (risk check #3).

### 2. Unlock path and human confirmation

#### LU-C02 Explicit confirmation
`If an unlock request lacks confirm=true, then the unlock route shall return 400 and change nothing.`

#### LU-C03 First-unlock acknowledgement
`If live.first_live_confirmation_done is false and the request lacks acknowledge_first_unlock=true, then the unlock route shall return 400 and change nothing.`

#### LU-C04 One-way transition
`The unlock route shall transition only from live_disabled to live_limited.`
- WHEN the state is already `live_limited` with valid evidence THEN it returns `Already unlocked`.
- WHEN the state is `live_limited` with invalid evidence THEN it revokes (LU-C08) and returns 409 `live_unlock_evidence_stale`.
- WHEN the state is anything else THEN it returns 409.

#### LU-C05 Selected-account private preflight
`When an unlock is requested, the unlock route shall require a private, read-only probe of the selected exchange's live account through the UTA adapter that proves a live environment and granted trading permission, and if the probe fails, then it shall return 403 live_preflight_failed.`
- WHEN `exchange.mode` is not `live` or the selected exchange has no live mode THEN the preflight fails (`exchange_mode_not_live`, `selected_exchange_unsupported`).
- WHEN the trading permission is `unknown` THEN it is denied (`permission_unknown`; `PERMISSION_UNKNOWN_POLICY = "deny"`).
- Public reachability, demo/testnet responses and cached state are never evidence (`public_reachability: not_used_as_authority`).

#### LU-C06 Sanitized, expiring evidence
`The preflight shall persist only the public evidence fields (broker, execution mode, environment, account fingerprint, credential and config revisions, permission status, fetched_at, expires_at, evidence_binding), expiring 300 s after fetch.`
- Raw credentials and exchange payloads are never stored or returned (`trust/live_unlock.py::public_evidence`).

#### LU-C07 No commit against changed configuration
`If live.state, the selected exchange, its credentials, UTA profile or config revision changed between preflight and commit, then the unlock route shall return 409 and apply nothing.`
- Token: `live_unlock_config_revision_conflict`.

#### LU-C08 Stale evidence revokes the unlock
`If live_limited evidence is missing, expired, no longer granted, or no longer matches the selected account, then the first reader among /api/status, Risk check #3 and the Gateway shall set live.state to live_disabled, clear the evidence, reset operator.permission_mode to ReadOnly and write a live_unlock_revoked audit event.`
- WHEN the revocation cannot be persisted THEN it is still applied in memory and retried on the next read.

#### LU-C09 The route never trades
`The live unlock routes shall not place, modify or cancel any order.`

### 3. Audit format

#### LU-C10 Unlock is audited before it applies
`When an unlock passes every gate, the unlock route shall write a live_unlocked audit event before changing state, and if the write fails, then it shall return 503 and change nothing.`
- The event carries `actor=human`, `source=dashboard`, `entity_type=live_state`, `before{state, operator_permission_mode}`, `after{state=live_limited, operator_permission_mode=Auto, live_execution_identity}`, the public evidence, and the message `Live trading unlocked to live_limited by human confirmation after selected-account preflight; System Operator elevated to Auto`.

#### LU-C11 Lock always applies
`When a lock is requested, the lock route shall write a live_locked audit event, set live.state to live_disabled, clear the evidence and reset operator.permission_mode to ReadOnly, even if the audit write fails.`
- WHEN the audit write fails THEN the response includes `audit_warning`.

### 4. Scorecard gate (K3)

#### LU-C12 Demo results gate the unlock
`When an unlock passes the preflight, the unlock route shall call compute_scorecard and return 403 scorecard_gate_failed unless composite_score is at least 70, traffic_light is green and blockers is empty.`
- WHEN eligible trades exist and their net realized PnL is ≤ 0 THEN the scorecard is red with a blocker, whatever the weighted score.
- WHEN the outcome cohort cannot be read THEN the scorecard is red with a blocker.
- WHEN `compute_scorecard` raises THEN the request fails and nothing is applied.

### 4b Preparatory authority (Experimental)

#### LU-C13 Operator autonomy follows the unlock
`When an unlock is applied, the unlock route shall set operator.permission_mode to Auto, and the lock route and every revocation shall reset it to ReadOnly.`

#### LU-C14 No live routing for Entry Plans
`The Entry Plan compiler shall take execution_account_mode from exchange.paper_execution_mode, so no Entry Plan targets a *_live_limited mode while live is unlocked.`
- The key keeps its historical name; its value is the demo account mode (for example `bitget_demo`). Owner: `trading/entry_plan_compiler.py`. The Gateway never rewrites a submitted demo Proposal to live.

#### LU-C15 Honest UI boundary
`Before the human confirms an unlock, the Dashboard shall show a warning that unlocking records only a live_limited prerequisite, that autonomous Proposals keep the selected demo execution mode, and that real-fund risk exists only after live Proposal routing is built and verified.`

#### LU-C16 Selected-mode resolution
`The selected-mode resolver shall return the selected broker's *_live_limited mode when exchange.mode is live, live.state is live_limited and current_authorization_ref is not None, the configured demo mode otherwise, and None when configuration is unreadable or the demo mode is unregistered.`
- This read serves analytics, dashboard and CLI only; it does not route Entry Plans (LU-C14).

#### LU-C17 Risk ignores operator autonomy
`The Risk Engine shall not read operator.permission_mode, so Auto exempts no Risk check.`

### 5. Permission modes

#### LU-C18 Two permission ladders
`The permission layer shall rank worker modes Observe < Paper < Full (default Paper) and operator modes ReadOnly < Research < PaperAuto < AllowEditing < Auto (default ReadOnly), and if a mode is unknown, then it shall deny the command.`
- The command set each mode allows is `agent_shell/registry.py::COMMANDS`. `operator.permission_mode` governs agent commands only; it is not a gate on the human unlock route.

### 6. Forbidden intents and restricted tools

#### LU-C19 Forbidden in every mode
`The permission layer shall reject every FORBIDDEN_INTENTS command as permanently forbidden under every worker and operator mode, including Auto.`
- `agent_shell/registry.py::FORBIDDEN_INTENTS` contains at least `enable_live`, `modify_credentials`, `bypass_risk`, `place_live_order`, `modify_execution_gateway`, `delete_knowledge`, `change_risk_limits`, `deactivate_kill_switch`, `modify_permissions`, `modify_own_prompt`; names are lowercase with an underscore.

#### LU-C20 Restricted tools have no permitted mode
`The agent runtime shall register RESTRICTED_TOOLS under the restricted class, whose allowed-mode set is empty.`
- Owner: `agent_runtime/tool_registry.py::RESTRICTED_TOOLS`, `agent_runtime/permissions.py::TOOL_CLASSES`.

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| Preflight timeout / connection / missing credentials | 403, state unchanged | `live_preflight_failed` + `reason_code` (`connection_timeout`, `connection_failed`, `credentials_not_configured`) | permission `unknown`, never granted |
| Trading permission unknown or denied | 403 | `permission_unknown`, `permission_denied` | unknown = deny |
| Scorecard below gate or cohort unreadable | 403 | `scorecard_gate_failed` + score, light, blockers | cohort unreadable = blocker |
| Config changed during preflight | 409 | `live_unlock_config_revision_conflict` | — |
| Unlock audit write fails | 503, state unchanged | `audit_write_failed` | — |
| Lock audit write fails | lock applied | `audit_warning` | — |
| Evidence stale while unlocked | revoke to `live_disabled` + ReadOnly | `live_unlock_revoked` audit, `/api/status` revocation, Risk `live_identity_unverified:*` | — |

## Authority

Only a human Dashboard request may unlock Live and only with confirmation, preflight and
scorecard; no agent command or tool may unlock, change credentials, change Risk limits or
reach the Gateway, and unlocking never routes an Entry Plan to a live account.

## Verification

Claim ids are `SpecClaim` ids in `tests/specs/` — a separate namespace from `LU-C<NN>`.

| Requirement | Executable claim / focused test |
|---|---|
| LU-C01 | `spec_live_unlock.py::LU-S01` |
| LU-C02 | `spec_live_unlock.py::LU-B03`; `spec_pipeline_live_unlock.py::PL-LU-01` |
| LU-C03 | `spec_live_unlock.py::LU-B04`; `spec_pipeline_live_unlock.py::PL-LU-02` |
| LU-C04 | `spec_live_unlock.py::LU-B05`; `spec_pipeline_live_unlock.py::PL-LU-03` |
| LU-C10 | `spec_live_unlock.py::LU-B06`; `spec_pipeline_live_unlock.py::PL-LU-04` |
| LU-C12 | `spec_kill_conditions.py::D8-B02`, `::D8-B02A`, `::D8-C01`; `spec_pipeline_live_unlock.py::PL-LU-NEG` |
| LU-C13 | `spec_live_unlock.py::LU-B07`, `::LU-C04`, `::LU-C06` |
| LU-C15 | `spec_live_unlock.py::LU-S03` |
| LU-C16 | `spec_live_unlock.py::LU-C07` |
| LU-C17 | `spec_pi_decision_chain.py::PCI-G06` |
| LU-C19 | `spec_live_unlock.py::LU-S02`, `::LU-B01`, `::LU-C01`, `::LU-C01b`, `::LU-C02`; `spec_pipeline_live_unlock.py::PL-LU-05` |
| LU-C20 | `spec_live_unlock.py::LU-B02`, `::LU-C03`; `spec_risk_engine.py::RE-C01` |

LU-C05–C09, LU-C11, LU-C14 and LU-C18 have no `SpecClaim`; focused coverage lives in
`tests/` outside `tests/specs/`. `spec_live_unlock.py::LU-C05` is a missing-status tripwire
for a Serenity executor that does not exist.

## Known conformance gaps

- **G1 — Bitget trading-permission scope.** The approved rule is that a Bitget live
  account's `authorities` scope must explicitly include trading, read-only or unknown scope
  being denied. The current probe (`extensions/exchanges/ccxt/profile.py::account_authority`)
  reads only `cantrade` / `can_trade` / `tradepermission` / `order_permission` keys from
  balance responses and never parses `authorities`; the test that asserted
  `authorities: ["read", "trade"]` was removed with the CCXT convergence. The result is
  fail-closed (`permission_unknown` → deny), so a correctly permissioned Bitget live key
  cannot pass LU-C05.

## Decision references

- `BACKLOG-LU-01` rejected: `operator.permission_mode` is an agent-autonomy control, not a gate on the human unlock route (LU-C18).
- GitHub #418 / EX-04: the missing Entry Plan → live-mode join; until it exists Live stays Experimental (LU-C14).
- #388: audit before state change on unlock; lock never refused for audit (LU-C10, LU-C11).
- D8-C01: scorecard gate is the code-level form of "demo performance is the only ticket to Live" (LU-C12; [kill conditions K3](kill_conditions_contract.md)).
- [trust_and_change_control §6.3](../../governance/policies/trust_and_change_control.md): first unlock needs explicit human confirmation; lock/unlock are audited; restricted tools stay uncallable.
