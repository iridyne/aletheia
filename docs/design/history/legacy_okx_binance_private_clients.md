# Retired OKX and Binance private clients

The former `aletheia/exchanges/{okx,binance}/client.py` implementations were
removed under #3134 after production account construction, submission,
reconciliation and private evidence consumers had converged on UTA.

These clients combined credential lookup, transport construction, immediate
submission polling, order/position normalization, funding reads and verified
exit logic. They were no longer selected by production composition. Keeping
their tests did not make them a supported alternate execution path.

The surviving implementation is the generic CCXT UTA adapter with venue
profiles, under the existing [UTA contract](../proposals/unified_trading_account.md)
and [execution contract](../contracts/execution_contract.md). Current tests
exercise normalized order quantities, exact account identity, bounded private
history, ambiguous submission, signed funding projection and incomplete exit
evidence through that implementation. Aggregated order status does not replace
private Fill evidence. Historical immediate-poll response shapes and private
client teardown internals are not compatibility requirements.

OKX/Binance remain frozen under [Active Runtime Scope](../../product/ACTIVE_RUNTIME_SCOPE.md).
This deletion neither admits new entries nor changes historical account recovery
obligations. Bitget native protection remains separate and was not removed.
There is no rollback selector or forwarding alias to the retired clients.
