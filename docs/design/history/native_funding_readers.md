# Retired native funding readers

Under #3134, the native Bitget, OKX and Binance client methods named
`get_funding_ledger` were removed. They implemented separate HTTP funding-history
readers returning the older accounting ledger dictionary.

Production funding reads use the registered `CCXTUTAAdapter` and venue profile,
returning the Core `FundingLedgerReport`. Reconciliation and delayed settlement
consume that normalized evidence. The native client methods were no longer
called by those paths; their direct-call tests described retired implementations.

The old raw-bill conversions now live only in `tests/broker_funding_fixture.py`
to reproduce historical ledger dictionaries, including direction and signed
cost fields. Core retains complete-ledger validation and settlement computation;
current venue parsing belongs to the Exchange profiles.

The remaining native clients do not regain funding authority. Their other
methods require separate reachability review. Historical ledger economics and
their parsers are not changed by this deletion, nor are settlement completeness,
Outcome eligibility, account identity or funding-retention rules.
