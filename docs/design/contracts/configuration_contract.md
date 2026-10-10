# Configuration Loading Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `CF` · **Owner code:** `core/aletheia/config/loader.py::load_config`

## Scope

The persisted configuration (`config.yaml` under the runtime data directory) is decoded
into the `AletheiaConfig` model before any authority — Gateway, scheduler, Risk,
Settings — reads it. This contract owns the strict-load rule, the explicit legacy
decodings, and the network-proxy credential boundary. It does not own defaults,
strategy or execution policy, or the meaning of any individual section; those belong
to the section's own Contract.

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `core/aletheia/config/loader.py::load_config` | call | every runtime reader (Gateway start, scheduler, routes) |
| `core/aletheia/config/loader.py::edit_config` | call (locked read-modify-write) | Settings routes, `aletheia network set` |
| `core/aletheia/diagnostics/probes/environment.py::_probe_config_gates` | Doctor probe | `aletheia doctor` |
| `core/aletheia/server/routes/settings.py::update_network_config` | API route `POST /api/settings/network` | Settings Network card |
| `core/aletheia/cli/network_cmd.py::set_cmd` | CLI `aletheia network set` | operator |
| `core/aletheia/server/routes/settings.py::update_observed_equities` | API route `POST /api/settings/equities` | Settings (frozen workset, see below) |

## Durable state

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| `config.yaml` | `core/aletheia/config/loader.py::save_config` (via `edit_config` or a load-time migration) | `load_config` | replaceable document; never rewritten by a failed load |
| in-process config cache | `load_config` | `load_config` callers (deep copy per call) | replaceable cache, short TTL |

## Requirements

### Strict persisted config

#### CF-C01 Unknown fields are rejected recursively

`When load_config decodes config.yaml, the loader shall validate AletheiaConfig with unknown model fields forbidden at the root and in every nested model before the result is cached, projected into runtime authority, or written back by a migration.`

- WHEN `remote_control.require_tokn` is present THEN `load_config` raises a Pydantic `ValidationError` whose `loc` is `("remote_control", "require_tokn")`.
- WHEN the file is valid THEN the loaded model round-trips through save and reload unchanged.

Evidence: `core/aletheia/config/loader.py::load_config` (`AletheiaConfig.model_validate(raw, extra="forbid")`).

#### CF-C02 Rejection never echoes the value and never rewrites the source

`If load_config rejects an unknown field, then the loader shall raise a Pydantic ValidationError that keeps the dotted rejected path, omits the rejected input value from its string, JSON and error-mapping projections, and leaves config.yaml byte-for-byte unchanged.`

- WHEN the rejection is raised THEN `str(exc)` and `exc.errors()` contain no rejected value (`hide_input=True`, `input`/`ctx` stripped).
- WHEN a later Settings save runs after the rejection THEN it cannot erase the malformed key, because no config object was produced.

Evidence: `core/aletheia/config/loader.py::load_config` (`ValidationError.from_exception_data(..., hide_input=True)`).

#### CF-C03 Doctor reports a rejected config as a blocker

`When the persisted configuration fails validation, Doctor shall report the config.validation blocker naming the rejected dotted paths and a repair hint, without the rejected values.`

- WHEN an unknown key is present THEN `aletheia doctor` shows `config.validation` at level `blocker` with `persisted configuration rejected at: <paths>`.

Evidence: `core/aletheia/diagnostics/probes/environment.py::_probe_config_gates`.

#### CF-C04 Dynamic mapping keys are data

`The loader shall accept arbitrary keys inside typed dynamic mappings, including model-provider names and Universe family quotas, because they are mapping values and not model fields.`

- WHEN `models.providers.operator_selected` and `universe.family_quota.momentum` are present THEN the config loads and both keys survive.

#### CF-C05 Legacy migrations are explicit and deterministic

`When a supported retired key is present, the loader shall remove or translate it by a named migration in load_config and, where persist_migrations is true, write the migrated config back once.`

- WHEN `strategy_factory` or `exchange.bitget_profile` is present THEN it is dropped by its named migration, not ignored by a lenient parser.
- WHEN `market_data.coinalyze_api_key` is present THEN load fails with a message that names the field and does not show its value.
- WHEN `persist_migrations=False` THEN the migrated model is returned and the source file is not written.

Evidence: `core/aletheia/config/loader.py::load_config` (the `removed_*` / `webhook_migrated` branches).

### Canonical equity and tokenized-equity observation settings (frozen, outside product scope)

Ordinary equities and tokenized equities are outside the active instrument scope
(USDT-margined perpetuals only, see
[Capability Ground Truth](../../product/CAPABILITY_GROUND_TRUTH.md)). The worksets below
and their consumers — `extensions/research/equity_observer.py`, scheduled as
`equity_observer` by `core/aletheia/agent_runtime/scheduler.py`, and the readers in
`core/aletheia/trading/instruments.py` and `core/aletheia/trading/market_data.py` — are
residual code for a closed scope. They are described so their decoding stays
deterministic until they are retired; they are not supported product behavior.

#### CF-C06 `observed_equities` is the only equity workset

`When config decoding meets historical observed_stocks, the MarketDataConfig decoder shall use it only if observed_equities is absent, and runtime objects and saves shall contain no observed_stocks field.`

- WHEN `observed_equities: []` and `observed_stocks: [TSLA]` are both present THEN the loaded list is `[]` and the source file is not rewritten.
- WHEN `POST /api/settings/equities` saves `[]` THEN reload returns `[]` and the file has no `observed_stocks`.

Evidence: `core/aletheia/config/schema.py::MarketDataConfig.decode_historical_observation_lists`; `core/aletheia/server/routes/settings.py::update_observed_equities`. The former control is [historical](../history/observed_stocks_settings_control.md).

#### CF-C07 `observed_stock_tokens` is the only tokenized-equity workset

`When config decoding meets historical observed_xstocks, the MarketDataConfig decoder shall merge it once, de-duplicated, after the canonical (or default) observed_stock_tokens list, and saves shall contain no observed_xstocks field.`

- WHEN `observed_stock_tokens: []` and `observed_xstocks: [AAPLON/USDT]` are present THEN the loaded list is `[AAPLON/USDT]` and the source is not rewritten.
- WHEN `POST /api/settings/market-data` saves `observed_stock_tokens: []` THEN reload yields no tokens and the file has no `observed_xstocks`.

Evidence: `core/aletheia/config/schema.py::MarketDataConfig.decode_historical_observation_lists`. The former alias is [historical](../history/observed_xstocks_settings_control.md).

#### CF-C08 The generic market-data route refuses legacy and duplicate workset keys

`If POST /api/settings/market-data carries observed_stocks, observed_equities or observed_xstocks, then the route shall reject the request without saving.`

- WHEN any of the three keys is sent THEN the strict request model (`extra="forbid"`) returns 422 and the config is unchanged.

Evidence: `core/aletheia/server/routes/settings.py::MarketDataConfigUpdate` (subclass of `SettingsRequest`, which forbids extra keys).

### Network proxy settings

#### CF-C09 Proxy fields never carry credentials

`If network.proxy_host or network.proxy_url contains a user[:password]@ part, then the NetworkConfig schema shall reject it with a message that does not contain the rejected value.`

- WHEN `proxy_url: http://u:p@host:7890` is loaded THEN `config.validation` is a blocker and the source is not rewritten.

Evidence: `core/aletheia/config/schema.py::NetworkConfig._reject_proxy_userinfo`, `NetworkConfig.recompose_proxy_url`, `NetworkConfig.parse_proxy_url`.

#### CF-C10 Every writer goes through the same proxy validator

`When a proxy value is written through Settings or the CLI, the writer shall validate it through NetworkConfig and, on rejection, leave the saved configuration unchanged.`

- WHEN Settings posts a proxy with userinfo THEN the response is `422 invalid_configuration` with the field path and no value.
- WHEN `aletheia network set --host u:p@host` runs THEN it exits non-zero without saving.

Evidence: `core/aletheia/server/routes/settings.py::_validated_settings_section`, `update_network_config`; `core/aletheia/cli/network_cmd.py::set_cmd`. Display redactors for proxy URLs remain defense in depth for archives and historical files only.

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| unknown persisted field | raise `ValidationError`, no cache, no write | Doctor `config.validation` blocker; every `load_config` caller raises | no substituted default is ever persisted |
| proxy with userinfo | schema rejection | Doctor blocker / `422 invalid_configuration` / CLI exit 1 | rejected value never shown |
| retired key with a named migration | migrate, write back once if allowed | normal load | — |
| `coinalyze_api_key` present | raise `ValueError` | load error naming the field only | value never shown |

## Authority

Configuration loading may decode, migrate and reject; it never adds defaults on
failure, changes Risk limits, unlocks Live, or rewrites a source file it rejected.

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| CF-C01, CF-C02 | `tests/specs/spec_configuration.py::CFG-S01`; `tests/test_config.py` |
| CF-C03 | `tests/test_doctor.py` |
| CF-C04 | `tests/specs/spec_configuration.py::CFG-S02` |
| CF-C05 | `tests/test_config.py::test_removed_entry_keys_are_migrated_before_strict_validation` |
| CF-C06 | `tests/specs/spec_configuration.py::CFG-S03`; `tests/test_settings_honesty.py` |
| CF-C07 | `tests/specs/spec_configuration.py::CFG-S04` |
| CF-C08 | `tests/test_settings_honesty.py::test_market_data_cannot_bypass_the_canonical_equities_writer` |
| CF-C09, CF-C10 | `tests/test_config.py`, `tests/test_config_routes.py::test_network_route_rejects_proxy_userinfo`, `tests/test_network_cli.py` |

## Decision references

- Unknown persisted keys rejected: #2519.
- `observed_stocks` / `observed_xstocks` runtime aliases retired under #3134; explicit empty equity workset preserved: #3397.
- Authenticated proxies unsupported: #1642, owner decision 2026-10-09.
- Spot and non-crypto instruments outside product scope: [DECISIONS 2026-10-09](../../governance/decisions/DECISIONS.md) (#3440).
