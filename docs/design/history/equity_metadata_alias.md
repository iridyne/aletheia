# Retired equity observation metadata alias

The equity observer previously wrote identical token identity/price data under
both `stock_token` and the compatibility alias `xstock` in Opportunity metadata.
The #3134 consumer audit found no production reader of the `xstock` key. New
observations now write only `stock_token`; no forwarding or read fallback is added.

Existing persisted metadata is unchanged. Token identity, price, deviation,
premium/anomaly thresholds, and observation-only execution eligibility retain
their existing semantics. This retirement does not remove token instruments or
change observed-equity configuration.
