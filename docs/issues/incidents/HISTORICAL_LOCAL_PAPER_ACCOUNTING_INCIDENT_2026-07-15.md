# Historical local-paper accounting incident — 2026-07-15

## User impact

The Overview realized-PnL value became unavailable (`—` / not verifiable) even
though older local-paper Positions contained numeric close values.  The same
condition made the CLI's strict verified PnL report exclude those rows.

## Root cause

This was a real persistence/compatibility failure, not a market-data collector
failure and not an arbitrary UI cache issue.

1. Older local-paper entry materialization persisted `positions.order_id` but
   did not persist the reverse `orders.position_id` backlink.
2. The later funding-economics implementation correctly required that reverse
   link in order to enumerate immutable entry fills.  Old rows therefore became
   `missing_entry_orders`, despite retaining a deterministically matching
   primary Order and Fill.
3. The later authoritative-Risk identity contract intentionally marks older
   decisions as `legacy`; those rows lack the current proposal hash and
   evaluated quantity.  Reclassifying them as current verified outcomes would
   be false, so their strict verified PnL must remain unavailable.
4. The all-or-nothing Portfolio total made any one incomplete historical close
   blank the aggregate rather than exposing a separate reconciled historical
   layer.
5. The SDD/migration design guarded newly-produced rows but had no executable
   historical-upgrade contract requiring a deterministic backlink backfill and
   separate legacy read model before the stronger verification rule shipped.

## Corrective action

`historical_accounting_reconciliations` is an additive, versioned table.  The
repair command is dry-run by default and can only:

- restore an empty reverse backlink when the Position's primary Order, identity,
  source, status, and proposal match exactly;
- reconstruct local-paper economics from immutable entry fills, complete
  persisted funding schedules, and the stored close inputs; or record the
  precise reason evidence is unavailable.

It cannot modify raw Position PnL, current Risk authority, outcome eligibility,
Learning inputs, paper-account cash, exchange data, or live trading state.

## Prevention

- Current entry materialization already writes the reverse backlink; the new
  `HLPA-01` regression test protects strict current funding validation from the
  historical compatibility path.
- `HLPA-02` requires missing schedule evidence to stay unavailable.
- The data-model contract requires the new additive reconciliation record.
- Operators can inspect strict verified PnL and historical reconciled PnL as
  separate quantities through `aletheia inspect pnl`; the Web Overview labels
  historical reconciled closes separately instead of replacing verified PnL.
