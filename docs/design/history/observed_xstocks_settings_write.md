# Retired observed_xstocks settings writer

The market-data request no longer accepts observed_xstocks. The canonical
observed_stock_tokens control displays the existing merged token lists until
the owner explicitly saves. That save replaces observed_stock_tokens and clears
observed_xstocks under the existing config/candidate-publication permit, with
before/after lists retained in the existing audit. Unrelated settings updates
do not clear the legacy list.

This prevents removed tokens from reappearing through the legacy merge. No
instrument identity, eligibility, market type or execution authority changes.
Stored config and runtime legacy reads remain pending full retirement in #3134;
there is no hidden startup migration or rewrite of historical observations.

The runtime helper alias `xstocks_instruments_from_config` is retired. It only
forwarded to `stock_tokens_from_config`; snapshot collection and target-group
health discovery now call that canonical helper directly. The helper's existing
merge, normalization and invalid-token filtering are unchanged.

Confirmed-delist removal also uses the existing locked `mutate_config` owner.
It removes only the confirmed symbol from the latest two lists, preserving
other committed settings; repeated removal is a no-op without another audit.
Indeterminate listing evidence still cannot trigger a config mutation.

The intermediate dual-field behavior above is now retired. Historical config
decoding validates and merges observed_xstocks into observed_stock_tokens once,
preserving canonical defaults when that field is absent and exact-string
deduplication order. It copies input without initiating file writes. Runtime
objects and serialized config contain only observed_stock_tokens; instruments,
Serenity, Observation, settings, delist cleanup and UI no longer read/write the
old config field. Instrument-level canonical-symbol deduplication is unchanged.
Explicit empty canonical saves remain empty on reload. No historical data or
startup migration flag is rewritten or introduced.
