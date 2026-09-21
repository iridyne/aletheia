# Configuration Loading Contract

The canonical persisted configuration is loaded by
`aletheia.config.loader.load_config()` into the `AletheiaConfig` model. Known
legacy migrations remain explicit and deterministic. Before a configuration is
cached, projected into runtime authority, or written back by a migration, the
canonical model is validated with unknown model fields forbidden recursively.

Unknown root or nested fields are a configuration blocker. The loader raises a
Pydantic validation errors retain the dotted rejected path, but unknown-key
failures never include the rejected input value in their string, JSON, or error
mapping projections. The source file remains untouched, so a later Settings
save cannot erase malformed input or silently persist a substituted default.
Doctor exposes this condition as the `config.validation` blocker with a repair
hint. Dynamic dictionary keys, including model-provider names and family quotas,
remain valid data because they are values of typed mappings rather than model
fields.

Valid configurations and supported legacy migrations retain their existing
round-trip and persistence behavior. This contract does not add migrations,
change defaults, alter authority ownership, or change strategy and execution
policy.

## Canonical equity observation settings

`observed_equities` is the sole runtime equity-ticker workset. Historical config
decoding validates and consumes `observed_stocks`, using it only when the canonical
list is absent or empty, preserving the former effective workset. Decoding does
not write the source file. Runtime objects and normal saves contain no legacy
field, so an explicit empty canonical save remains empty after reload.

`POST /api/settings/equities` owns normalized equity edits through the existing
locked config mutation. The generic market-data endpoint rejects both the retired
`observed_stocks` input and the duplicate `observed_equities` input. Scheduler,
observer and Settings use only the canonical list. This retirement under #3134
does not change observation-only classification or tokenized-equity configuration.
The former control is [historical](../history/observed_stocks_settings_control.md).

## Canonical tokenized-equity observation settings

`observed_stock_tokens` is the sole runtime tokenized-equity workset. Historical
decoding validates and consumes `observed_xstocks`, merging it with the historical
canonical/default token list once without rewriting the source file. Normal
saves contain no legacy alias; an explicit canonical empty save stays empty after
reload. The generic market-data endpoint rejects `observed_xstocks` writes.
Collector, equity observer and research context consume only the canonical list;
confirmed delisting removes tokens from that list under the existing rule.
This does not change listing evidence, observation-only admission or trading policy.
The former alias is [historical](../history/observed_xstocks_settings_control.md).

## Executable evidence

- `tests/test_config.py` covers unknown root/nested fields, safe error paths,
  no source rewrite, dynamic mappings, and the existing migration path.
- `tests/test_doctor.py` covers the Doctor blocker projection and redaction.
- `tests/specs/spec_configuration.py` registers the strict-load and dynamic-map
  claims in the executable conformance matrix.
