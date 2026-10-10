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
field is absent. A present canonical list, including an empty list, takes precedence.
Decoding does not write the source file. Runtime objects and normal saves contain
no legacy field, so an explicit empty canonical save remains empty after reload.

The equity observer and its `POST /api/settings/equities` control are retired
(#3930); the field stays in the schema only until the Fedora config drops it. The
generic market-data endpoint rejects both the retired `observed_stocks` input and
the duplicate `observed_equities` input. This retirement under #3134 does not
change observation-only classification or tokenized-equity configuration.
The former control is [historical](../history/observed_stocks_settings_control.md).

## Canonical tokenized-equity observation settings

`observed_stock_tokens` is the sole runtime tokenized-equity workset. Historical
decoding validates and consumes `observed_xstocks`, merging it with the historical
canonical/default token list once without rewriting the source file. Normal
saves contain no legacy alias; an explicit canonical empty save stays empty after
reload. The generic market-data endpoint rejects `observed_xstocks` writes.
Only the equity observer consumes the canonical list. Tokenised stocks are out of
product scope (USDT perpetuals only): the market-data collector, price feed and
Serenity research context no longer read it, and nothing removes tokens from it
automatically. This does not change observation-only admission or trading policy.
The former alias is [historical](../history/observed_xstocks_settings_control.md).

## Network proxy settings

Authenticated proxies are not supported (#1642, owner decision 2026-10-09).
`network.proxy_host` names a host only and `network.proxy_url` carries no
userinfo: any `user[:password]@` part in either field is a configuration error
at the `NetworkConfig` schema boundary, so config load, Settings
`POST /api/settings/network`, `aletheia network set` and every save reject it
through the same validator. Load reports it through the `config.validation`
blocker without rewriting the source; Settings returns `422
invalid_configuration` with the field path and leaves the saved configuration
unchanged; the CLI exits non-zero without saving. Rejection messages and error
projections never contain the rejected value. Display redactors for proxy URLs
remain as defense in depth for archives and historical files only.

## Executable evidence

- `tests/test_config.py` covers unknown root/nested fields, safe error paths,
  no source rewrite, dynamic mappings, and the existing migration path.
- `tests/test_doctor.py` covers the Doctor blocker projection and redaction.
- `tests/specs/spec_configuration.py` registers the strict-load and dynamic-map
  claims in the executable conformance matrix.
- `tests/test_config.py`, `tests/test_config_routes.py` and
  `tests/test_network_cli.py` cover rejection of proxy userinfo without echoing
  the credential.
