# Archived local simulated account controls

This is historical documentation for the retired Settings/API controls of
`paper_internal` (Paper Internal). These routes no longer exist. This archive
does not authorize account mutation, simulated execution, settlement or
restoration of the product feature. See the [accepted retirement](../proposals/retire_local_simulated_exchange.md).

The former `PaperAccountEvent` construction model is deleted; historical ledger
queries continue to read the stored rows. The unused UTA `local_paper` environment
label is removed from current account identities; UTA profiles represent external
exchange accounts. Existing raw records and migration history are unchanged.

## Former API

| Method | Path | Historical behavior |
|---|---|---|
| POST | `/proposals/{id}/approve-paper` | Former owner approval and execution of a stored local simulated proposal, preserving its ID. Removed with the simulator; no replacement approval alias. |
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


## Retired accounting maintenance

The following commands and automatic repairs belonged to the archived simulator.
They are removed from the active runtime; historical records remain readable.

- `data reconcile-local-paper-accounting`
- `data repair-paper-close-timestamps`
- `data reconcile-paper-funding`
- Scheduled `paper_funding_settler` and inline cash-ledger repair during loss queries.

Former data-model definition:

`aletheia data reconcile-local-paper-accounting` is dry-run by default; `--apply`
may only restore an exact missing reverse backlink and write this table, inside a
transaction.  It never changes raw `positions.realized_pnl`, eligibility, Risk,
paper cash events, or exchange data.  Its amounts are exposed as historical
reconciled accounting, never combined with verified strategy PnL, Learning,
PortfolioSnapshot, or cash settlement.

## Retired funding model

Historical definition only; the simulator repair commands are retired.

`paper_internal` 合约使用明确标为 `local_simulation` 的确定性模型：两分钟衍生品采集持续覆盖
所有未平仓本地合约；按 `opened_at < next_funding_time <= closed_at` 的每个结算事件，采用同一
主 venue 在结算前 15 分钟内的最后持久快照。每次结算的名义金额只来自该时刻之前已经成交、
且反链到同一 Position 的 entry Order/Fill；多腿加仓按真实成交时间分段，不能用最终 Position
名义金额回溯早期结算。Order、Proposal、Risk、Position 的币对、市场、方向、执行模式以及每单
与整仓数量必须闭合；首笔/primary Fill 必须与 Position `opened_at` 在一秒持久化写序容差内对齐，
更早且超出该容差的 Fill 视为损坏事实；Fill 恰好落在结算时刻也视为边界不明并 fail closed。完整事件按多头成本为
正、空头返还为负聚合。开仓锚点、每次结算后的下一 schedule 和平仓后的未来 schedule 必须完整，
事件快照随 close Order/Fill 在同一事务写入 `reconciliation_evidence.funding`。中央关闭入口再次
核对证据的 source、Position、symbol、持有时间窗和逐事件合计；不完整/异常结果必须显式覆盖旧
funding 为 `NULL/unavailable`。这只是可复现的本地模拟，不冒充交易所账户扣款。

资金费会计身份使用
`(exchange, canonical_symbol, canonical_settlement_at)`；解析后的时间统一为 UTC，并只把严格小于
一秒的序列化偏差归一到同一整秒边界。原始 snapshot ID 和时间戳继续保留在 evidence 中；同一
canonical bucket 的明确历史结算值优先于结算前 live observation，两个不同历史费率则
`conflicting_settlement_rates` fail closed，绝不静默选值或重复计费。

正常关闭使用 `live_coverage` 证明开仓锚点、逐次 schedule transition 和关闭锚点。延迟恢复可使用
`historical_ledger`：只有精确 entry Fill lineage 和持仓窗内完整、单调、被关闭后首个实际结算
bracket 的历史序列才可完成；公开历史费率在这里仍是本地模拟证据，不是 `broker_ledger`。
恢复成功后 `materialize_position_economics()` 在一个 SQLite writer transaction 中同步 Position、
唯一 full close event、paper cash、reconciliation fingerprint/version、eligibility、outcome-hook outbox
和权威 audit。相同 fingerprint/result 为 no-op；已有数字的 owner-approved 修正保留原 cash 事实，
以带 position/source/fingerprint 的 `manual_credit` 或 `manual_debit` 追加差额，不删除旧事件。
`aletheia data reconcile-paper-funding` 默认只读；apply 必须显式列出 Position IDs 并匹配当次 report
fingerprint。


已完成数值、full-close、paper cash、eligibility 和 outcome hook 都一致，但顶层 funding version/fingerprint
缺失或不匹配的旧行，必须单独报告为 `funding_identity_mismatch`。只有它是唯一差异、且原始 nested
funding evidence 仍可按持仓窗与逐事件合计复核时，apply 才可走 identity-only repair：保留该 nested
evidence（包括 close 时的 `seed_close_anchor`）不变，只补它自身的 canonical version/fingerprint 和
权威 repair audit。任何 funding/PnL、close event、cash、eligibility/reason 或必需 hook 差异都不得被
identity-only 路径吸收；已经 current 的行再次 apply 必须为 no-op。


## Retired executable requirements

The former `spec_pipeline_paper_close_materialization.py` and
`test_pipeline_futures_leverage.py` required local cash, synthetic fills, local
funding settlement/revival and the removed bulk-close API. Those tests and their
simulator fixtures are deleted. Current native input rejection stays in
`spec_pipeline_risk_execution_integrity.py` using an external execution mode.
Historical unknown funding and signed funding projections remain exercised through
the Position API and CSV export in `tests/test_export_routes.py`, with archived
rows loaded directly. The former `DM-S23` constructor/source-text claim is retired;
actual history-read checks cover the surviving ledger.


The former `pipeline_paper_trade` suite is also retired. Its surviving atomic TTL
expiry claims now live in `spec_pipeline_opportunity_expiry.py`. Shared causal backlinks, approved take-profit inheritance and leverage persistence
are checked through external private-fill materialization; same-symbol Risk
rejection uses external Demo accounts. Local-only broker limit rules and the
`paper_tools` signature/constructor requirements are deleted.

### Distinct historical position semantics

The removed local broker kept a separate Position per Proposal. External entry
materialization currently aggregates compatible entry legs by account, symbol,
market, direction, strategy and trade school; each Order retains its own Proposal
and Position backlink. Cross-school fills remain separate. The local one-Proposal
one-Position assertion must not silently become an external contract during
retirement. Mixed-Proposal attribution and whether to change aggregation ownership
need a separate owner decision; this retirement does not change either behavior.

The old Execution claims for simulated broker reachability, symbol-check bypass,
local expired-lease replay and estimated simulator fills are retired. Current
claims use external modes. Completed-Proposal idempotency is checked directly,
without skipping when a simulated entry fails; actual causal links and reconciled
fees are checked through the existing external private-fill materializer.

Shared Outcome fixtures no longer rebuild simulated cash ledgers or invoke local
settlement. Their surviving producer path uses external close evidence. Factor
feedback and selected-mode profitability checks use Bitget Demo and OKX Demo;
archived ledger read-only checks remain in `test_authoritative_account_loss.py`.
The duplicate simulated daily-loss activation test is removed; the existing
external-account loss test retains the threshold and mode-specific brake claim.

Open archived positions no longer enter current mark-to-market calculations,
including in-memory Portfolio and CLI projections. Their stored observations
remain unchanged. The former local mark/cash-equity requirements in
`test_pnl_pipeline.py` are retired; external tests retain price-read consistency,
account/instrument matching, mode isolation and snapshot-window behavior.

Outcome Feedback claims `PL-OF-03`, `PL-OF-12`, `PL-OF-13C`, and `PL-OF-20`
are retired with simulated cash settlement and local funding provenance. Existing
external close/outbox tests retain attribution-read recovery; the remaining
causal-identity and funding-completeness claims use external close evidence.
Serenity feedback tests now verify external closes, exactly-once PostMortem
creation, opportunity status and loss buckets without a simulated account.
