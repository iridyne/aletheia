# extensions/ — Providers, venues, model vendors, research

Everything concrete lives here so `core/aletheia` stays provider-agnostic. Root
[`AGENTS.md`](../AGENTS.md) and the [safety guide](../docs/operations/development/agent-guides/SAFETY_AND_TRUST.md) apply.

## Conventions

- Core receives venues and providers as injected factories. The seam is
  `extensions/exchanges/composition.py`: it maps an execution mode (`bitget_demo`, `okx_demo`,
  `binance_testnet`, …) to a profile plus a CCXT adapter and registers it into
  `aletheia.uta.registry`. Core code never imports a specific exchange module.
- Vocabulary translation happens at this edge, into two vocabularies: execution (`spot` /
  `usdt_futures`, `exchanges/ccxt/profile.py`, which also folds CCXT `future` into
  `usdt_futures`) and instrument identity (`spot` / `perp`, `data/ccxt_provider.py`, which
  drops dated futures). Do not emit any other `market_type` value into core.
- Credential and signer code (`exchanges/credential_routes.py`, `exchanges/bitget/client.py`)
  is a controlled path: touch only when the task requires it.
- Market data comes from a fresh `get_market_data_provider()` per call; `BitgetClient` is
  execution-only ([STATE_AND_DATA](../docs/operations/development/agent-guides/STATE_AND_DATA.md)).
- Candidate discovery inputs live here (`data/collectors/volume_scanner.py`, `regime/`); learning,
  counterfactual and research are `learning/`, `counterfactual/`, `research/`.
- Serenity research and Candidate publication live in core (`core/aletheia/trading/serenity_scoring.py`,
  `serenity_publication.py`, `serenity_evidence.py`); there is no `extensions/serenity`.

## Tests

```sh
uv run pytest tests -q -m "domain_market_data"   # providers, collectors, exchange data
uv run pytest tests -q -m "domain_learning"      # learning, knowledge, counterfactual
uv run pytest tests/uta -q                       # adapter / registry contract
```
