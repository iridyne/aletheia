# Manual Close Reconciliation Runbook

Operator procedure for the human-owned `manual_reconciliation_pending` close
state, per [execution_contract §10.2.4](../design/contracts/execution_contract.md).
Nothing here is automatic: a human investigates the venue and authorizes exactly
one transition at a time.

## When this state is reached

A close-side Order is submitted to the venue and the confirmation is lost — the
local Order stays `submission_unknown`. For 24 hours the system keeps trying to
reconcile the claim. At `SUBMISSION_UNKNOWN_TERMINAL_HOURS` (24 h) from the
claim's `pending_close_requested_at` the claim stops being machine-owned:

- `positions.pending_close_status` becomes `manual_reconciliation_pending`;
- one audit row is written under the `manual_close_reconciliation_required`
  event type (identity namespace `manual-close-reconciliation-`);
- one P1 `MANUAL_CLOSE_RECONCILIATION_REQUIRED` operator wake is raised;
- one critical outstanding infra state is recorded.

The Order status is **not** changed, the claim is **not** released, and **no fill
is synthesized**. The position remains open.

## How to observe it

- **Health API**: the outstanding infra-events projection still contains the
  `(source=position_manager, code=manual_close_reconciliation_required,
  entity_id=<position_id>)` critical entry. It stays until an operator resolves
  the claim (diagnostic + resolver). See the health surface's
  `outstanding_infra_events`.
- **Audit table**: query `audit_events` for
  `event_type IN ('manual_close_reconciliation_required', 'pending_close_reconciliation_required',
  'manual_close_claim_resolved')`.
- **Position row**: `positions.pending_close_status = 'manual_reconciliation_pending'`
  together with a non-NULL `pending_close_order_id` is the durable claim.

## Step 1 — Diagnose and capture the fingerprint

Run the read-only diagnostic. It prints, for every manual claim, the Position
row, the close Order snapshot, the local Fills recorded for that Order, and the
manual-close audit trail — followed by a deterministic SHA-256 fingerprint over
exactly that JSON evidence.

```bash
uv run python scripts/manual_close_diagnostic.py --db "${ALETHEIA_DATA_DIR:-$HOME/.aletheia}/app.db"
```

For one claim (and to obtain the fingerprint the resolver needs):

```bash
uv run python scripts/manual_close_diagnostic.py \
    --db "${ALETHEIA_DATA_DIR:-$HOME/.aletheia}/app.db" \
    --position-id <position_id>
```

Add `--json` for a machine-readable array. The fingerprint is stable while the
database is unchanged; run it twice to confirm. Copy the
`Snapshot fingerprint: <sha256>` line — it is the `--acknowledge` token for
Step 3.

## Step 2 — Investigate at the venue (human job)

This tool cannot and will not decide for you. Before resolving, verify the real
venue state directly. For a `bitget_demo` symbol such as `KORU/USDT`:

- Open the Bitget demo account admin UI (the same account identified by the
  Position's `uta_account_identity_json`) and inspect **Order History** and
  **Transaction/Fill History** for the instrument and the close
  `client_order_id` / `exchange_order_id` shown by the diagnostic.
- Determine one of three facts:
  1. the close **filled** at the venue (note the fill id, quantity, price);
  2. the close **was never accepted/executed** at the venue;
  3. the position was **settled outside this gateway** (for example a manual
     action in the venue UI), so Aletheia has no matching exchange Order flow.
- Remember: a completed read that returns no matching order does **not** prove
  the venue never received it. Only confident venue evidence is enough.

Do not proceed until you have the raw venue evidence in front of you.

## Step 3 — Dispatch the resolver

The resolver transitions ONE claim. It recomputes the fingerprint from the live
database and refuses (exit `2`) if anything changed since Step 1; it re-reads the
claim under the SQLite writer lock and refuses (exit `3`) if it is no longer
`manual_reconciliation_pending`; and it requires you to type a case-sensitive
confirmation (`RESOLVE <position_id> AS <mode>`). There is no `--force`, no
`--yes`, and no batch mode.

```bash
uv run python scripts/manual_close_resolver.py \
    --db "${ALETHEIA_DATA_DIR:-$HOME/.aletheia}/app.db" \
    --position-id <position_id> \
    --acknowledge <sha256_from_step_1> \
    --mode <mode> \
    --reason "<what the venue showed and where>"
```

| Mode | Use when | Prerequisites | Side-effects |
| --- | --- | --- | --- |
| `venue_confirmed_fill` | The venue shows the close filled. | `--fill-id`, `--fill-quantity`, `--fill-price` from the venue fill. | Inserts a local Fill for the close Order; releases the claim. Order status is left for the ordinary reconciler to materialize. |
| `venue_confirmed_absence` | The venue proves the close never executed. | Confident venue evidence of absence. | Transitions the close Order to `submission_rejected`; releases the claim. Position stays open; a later close gets a freshly allocated `client_order_id`. |
| `operator_settled_outside_gateway` | The position was settled off this gateway. | Confident off-gateway evidence. | Audit only: releases the claim. Order status and Fills are untouched; Position stays open in Aletheia for you to settle separately. |

Every mode writes a `manual_close_claim_resolved` audit row with `actor=human`,
the `--reason`, and the resolution mode, and retires the critical infra entry.
All writes happen in one transaction: a failure leaves the claim exactly as it
was.

## Warnings

- **Never invent a fill.** `venue_confirmed_fill` requires a real venue fill id.
  The anti-fabrication invariant forbids synthesizing evidence. When the
  diagnostic reports `0 fills recorded — anti-fabrication invariant preserved`,
  that is the honest state, not something to paper over.
- **A fingerprint mismatch means state changed under you.** Exit `2` is not an
  error to work around: re-run the diagnostic, re-read the evidence, and only
  then re-acknowledge.
- **`submission_rejected` is irreversible through this tool.** There is no
  transition back out of `submission_rejected`. If a fill for that Order arrives
  afterwards, handle it as a venue-confirmed fill against a **new** close
  attempt — never by reviving the rejected Order.
- **One claim at a time.** Resolve, verify the resulting audit row, then move to
  the next claim.

## Reference

- [execution_contract §10.2.4](../design/contracts/execution_contract.md)
- [`scripts/manual_close_diagnostic.py`](../../scripts/manual_close_diagnostic.py)
- [`scripts/manual_close_resolver.py`](../../scripts/manual_close_resolver.py)
