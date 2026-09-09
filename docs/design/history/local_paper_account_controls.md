# Archived local simulated account controls

This is historical documentation for the retired Settings/API controls of
`paper_internal` (Paper Internal). These routes no longer exist. This archive
does not authorize account mutation, simulated execution, settlement or
restoration of the product feature. See the [accepted retirement](../proposals/retire_local_simulated_exchange.md).

## Former API

| Method | Path | Historical behavior |
|---|---|---|
| GET | `/settings/paper-account` | Local simulated cash, reserved margin, available balance, equity, economic-completeness state, and recent ledger events. Available only when `paper_internal` is active. |
| POST | `/settings/paper-account/credit` | Add local simulated USDT: `{"amount_usdt": number, "note"?: string}`. Human/audit logged; never touches an exchange. |
| POST | `/settings/paper-account/debit` | Remove local simulated USDT; cannot make cash negative. Human/audit logged. |
| POST | `/settings/paper-account/set-balance` | Set local simulated cash: `{"cash_balance_usdt": number, "note"?: string}`. An explicit zero remains zero; it is not automatically re-funded. |
| POST | `/settings/paper-account/close-spot` | Close every open local spot Position only after all candidates have a fresh exact configured-venue + symbol + spot mark. Never uses `Position.current_price`; preserves trade history and the human/dashboard actor. |
| POST | `/settings/paper-account/close-all` | Close every open local paper Position only after all candidates have a fresh exact configured-venue + symbol + physical-market mark. Never uses `Position.current_price`; preserves trade history and the human/dashboard actor. |

`GET /settings/paper-account` adds:

| Field | Meaning |
|---|---|
| `economics_complete` | False when any closed local-paper Position still has unavailable net economics |
| `unsettled_closed_positions_count` | Number of those closed Positions |

Both bulk-close endpoints preflight the complete candidate set before the first
close. If any exact mark is missing, they return HTTP `409` with
`detail.code="bulk_close_mark_preflight_failed"`, an empty
`closed_position_ids`, and per-Position `failed` facts; the batch writes no close
Order, Fill, Outcome, or feedback job. If mark preflight succeeds but a later
materialization fails, the endpoint returns HTTP `500` with
`detail.code="bulk_close_partial_failure"` and both the durable
`closed_position_ids` and per-Position `failed` list, so a partial close is never
reported as all-success.

The completeness check covers the complete local close history and is not capped by a dashboard display limit.
When `economics_complete=false`, `cash_balance_usdt` remains the append-only ledger balance,
`available_balance_usdt` continues to reflect confirmed cash minus reserved margin, and `equity_usdt`
continues to reflect confirmed cash plus unrealised P&L. Only the unresolved close-economics funding delta
is unknown; the known ledger values remain finite and spendable. No `position_closed` cash event is
invented for the missing result. The `economics_complete=false` flag is a monitoring signal — it must not
be interpreted as zeroing spendable balance or equity. See also
`docs/design/contracts/execution_contract.md` §11.6 and
`tests/specs/spec_pipeline_paper_close_materialization.py` (regression
`test_unknown_paper_perp_funding_closes_without_zero_equivalent_cash_settlement`).

