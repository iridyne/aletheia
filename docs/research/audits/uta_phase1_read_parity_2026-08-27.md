# UTA Phase 1 read parity

Fixing baseline: `master@fe2c7a0a3bbf809180c6b6b6489fedfeb390a25e`.
Issue: #2299 Phase 1. This is offline, secret-safe shadow evidence; no consumer,
backend selector, credential, runtime, or order path changed.

## Profile matrix

| Profile | Legacy oracle | UTA shadow behavior | Fixture result |
|---|---|---|---|
| Binance testnet | `BinanceTestnetAdapter` / `BinanceTestnetClient` | `binanceusdm`, sandbox before market load; USDT account fields, positions and futures open orders normalized to immutable Decimal reports | Account equity/available/used are equal; position and order quantity uses `contracts * contractSize` (normally contract size 1) |
| OKX demo | `OkxDemoAdapter` / `OKXClient` | `okx`, sandbox before market load; unified `totalEq`, spot plus swap open orders, and swap contracts converted back to coin quantity | `totalEq`, UPL and zero values are equal; `2 * 0.01 = 0.02` coin fixture matches the existing client contract-size rule |
| Bitget demo Classic v2 | `BitgetDemoAdapter` / `BitgetClient(profile="classic_v2")` | Only v2 spot and mix requests; the two account surfaces and spot/swap open-order surfaces retain independent completeness | Fixture preserves 2 spot USDT + 10 mix USDT as 12 total; one failed surface is `partial`, never complete-empty |
| Bitget demo UTA v3 | `BitgetDemoAdapter` fallback / `BitgetClient(profile="uta_v3")` | Every markets/account/position/order request carries the CCXT UTA selector; no v2 endpoint-family fallback | Per-asset `usdValue` fixture produces 11 equity and preserves BTC/USDT balances as Decimal values |

The sanitized response fixture is
`tests/fixtures/uta/phase1_account_parity.json`; executable coverage is in
`tests/uta/test_ccxt_adapter.py`.

## Explicit legacy correction

The current Classic-v2 adapter values spot assets into `coin_usdt`, then sets
`equity` to `equity_from_mix` whenever a mix account exists. That drops the spot
wallet from headline equity even though it remains in the breakdown
(`aletheia/portfolio/uta/bitget_adapter.py`, account mapping around lines 28-103
on the fixing baseline). The UTA fixture deliberately returns 12 rather than the
legacy 10 for 2 spot USDT plus 10 mix USDT. This is a proven legacy aggregation
bug, not an unexplained parity difference. Non-USDT Classic assets remain
`partial/unpriced_asset_balance`; UTA does not invent a price.

## Lifecycle and completeness evidence

- `make_ccxt` is invoked only under the active event loop; sandbox/demo is set
  before `load_markets` and any private request.
- An adapter cannot be reused or closed from another loop. Close is bounded and
  idempotent; teardown timeout cannot modify a returned report.
- Complete empty account evidence maps to explicit zero. Total failure is
  `unavailable`; one-of-many surface failure is `partial`; malformed raw objects
  and future venue timestamps cannot become complete empty reports.
- The Phase 1 adapter implements the complete V1 protocol but every mutation is
  a local `NOT_SENT / UTA_NOT_SUPPORTED` report. Phase 3 evidence surfaces are
  explicitly `unsupported` and do not perform network I/O.

Credential-backed parity is intentionally not claimed by this artifact. The
backend remains `legacy`; later per-profile qualification must bind credentials,
account fingerprint, generation, exact code SHA and private evidence before any
selector can become `uta_v1`.
