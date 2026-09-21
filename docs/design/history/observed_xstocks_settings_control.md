# Retired tokenized-equity config alias

Under #3134, the runtime `observed_xstocks` field and generic settings input were
retired. Previously consumers merged it into `observed_stock_tokens`, so clearing
the canonical workset could silently re-enable a legacy-only token. The deprecated
instrument-helper wrapper was also removed; callers use the existing canonical helper.

Historical config decoding preserves the former merged workset once without
rewriting the source. Runtime consumers and subsequent saves use only
`observed_stock_tokens`; current behavior is defined by the
[configuration contract](../contracts/configuration_contract.md).
