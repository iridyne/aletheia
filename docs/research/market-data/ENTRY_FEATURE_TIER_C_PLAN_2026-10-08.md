# Entry Feature Tier C Plan — 2026-10-08

Role: research evidence and a decision-ready plan for
[#3785](https://github.com/iridyne/aletheia/issues/3785). It authorizes nothing.
Current behavior stays owned by the
[Entry Plan Contract](../../design/contracts/entry-plan-contract.md), the
[Candidate Selection Utility Contract](../../design/contracts/candidate_selection_utility_contract.md),
the [Data Utilization registry](../../design/data/data_utilization.md) and
[Active Runtime Scope](../../product/ACTIVE_RUNTIME_SCOPE.md). Execution tracking
stays in GitHub.

Tier A/B (already computed or derivable from stored OHLCV) is handled in a
separate PR. This document covers the nine Tier C items, which the Issue lists
as needing new data. One finding up front: **four of the nine do not need a new
source** (options, estimated volume profile, predicted funding, perp basis).
Data for them is already stored, or can be derived from data we already store.

## 0. Method and evidence limits

- Code facts are from `origin/master` at `13fada415` (2026-10-08), as `file:line`.
- Venue facts come from unauthenticated public GETs run on 2026-10-08 against
  `api.bitget.com`, `www.okx.com` and `www.deribit.com`. No credentials were read
  or used. A response proves that an endpoint exists and what shape it returned
  that day. It does not prove rate limits, completeness or a stable contract.
  Unmeasured numbers are marked *unverified*.
- The competitor reference is `_refs/r20-quantum-trader` (read-only). Its
  features come from `scripts/factors/okx_quant_factors.py`, called by
  `scripts/factor_library.py`, and are rendered in `scripts/brain/prompt.py`.
  That code was read, not copied.
- No Fedora database was queried for this plan. Fedora claims come from #3785/#3786.
- `extensions/data/collectors/vergex_structure.py` is fixture-only.

## 1. Cross-cutting constraints

### 1.1 Two paths, two fingerprints

| Path | What reads the fact | Fingerprint that rotates when it changes | Cost of rotation |
|---|---|---|---|
| **Universe selection (U)** | `core/aletheia/universe/features.py` writes `universe_feature_snapshots`. Continuous scoring and `opportunity_evidence.py` read that table. | `CANDIDATE_FEATURE_ACQUISITION_FINGERPRINT = sha256(Path(__file__).read_bytes())` (`features.py:32-36`), stamped as `feature_acquisition_version` (`candidate_selection_shadow.py:334`). Mismatched history is excluded at `:503`. | Any byte edit to `features.py` drops all prior mechanism labels from training. #3786 measured 92,427 excluded labels across 5 fingerprints in 36 days. |
| **Entry analysis (E)** | `entry_analysis.py:731-767` reads `quant_context` from the same feature row. `execution_context` (`:858-867`) reads its own `orderbook_snapshots` row. | `entry_contract_fingerprints()` (`trading/entry_contract.py:37-62`) hashes the `EntryAnalysisContext` schema, the prompt, the compiler, the triggers and the profiles into `entry_behavior_fingerprint` and `schema_fingerprint`. | Current planning generations are superseded on the next cycle. Candidate-selection history is **not** affected. |

Consequences for every Tier C item:

1. **Do not add Tier C facts to `features.py`.** Adding a field to `quant_fields`
   means adding it to the feature row, and that edits the fingerprinted file. A
   Tier C fact for the LLM goes in its own typed block in
   `EntryAnalysisContext`, read from its own table. `execution_context` already
   works this way: stored fact, exact venue/symbol, freshness gate
   (`entry_analysis.py:422-432`). The
   [Planner Data boundary](../../design/proposals/slim_core_planner_data_boundary.md)
   requires that path to read qualified stored facts without importing an
   optional collector.
2. **Batch all entry-path wiring into one rotation.** Each schema/prompt change
   rotates `entry_behavior_fingerprint` once. Five separate wiring PRs would
   rotate it five times.
3. **No Tier C fact goes on the universe path until #3786 is decided.** That
   decision is whether the fingerprint stays "whole file bytes" or becomes a
   declared feature manifest. Several `opportunity_evidence.py` gap tokens can
   only be retired on the universe path, so they wait too: the static tuple at
   `:91-94` (`liquidation_flow_unavailable`, `basis_unavailable`,
   `cross_venue_positioning_unavailable`), its duplicate at
   `candidate_opportunity.py:213-216`, and `information_catalyst`, hard-coded
   unavailable at `:114-118`.
4. **Side finding for #3786.** The fingerprint covers `features.py` bytes only.
   A provider change in `extensions/data/ccxt_provider.py` can change what a
   feature field means without rotating it. Example: making `taker_buy_ratio`
   non-null on Bitget (§2.1).

### 1.2 Data-utilization rules that apply to every new source

[`data_utilization.md`](../../design/data/data_utilization.md) R4 says missing
data renders as explicitly unavailable, never as zero. R5 says a new source must
declare its consumer and add a `tests/specs/spec_data_utilization.py` claim.
Precedents #3098, #3099 and #3101 all landed observation-only, with a
DU-S claim, a read-only CLI and a `cutoff` as-of reader. Tier C collectors
should follow the same pattern before any LLM wiring.

Retention limits evaluation. `liquidation_events` is pruned at 7 days and
`options_volatility_snapshots` at 30 days (`agent_runtime/scheduler.py:2979-2981`).
`market_ohlcv` 15m keeps 30 days and 1h keeps 90 (`repositories.py:6186-6193`).
§3 needs about 30+ trading dates, so a new observation table needs ≥60-day
retention, or a frozen export taken before pruning.

### 1.3 Dead render paths (do not revive)

- `core/aletheia/data/context_builder.py:176 render_enriched_context_md` renders
  perp structure, POC/value area, liquidation walls, GEX and cross-venue funding.
- `core/aletheia/trading/market_context.py:331 build_market_context`.

Neither has a production caller (`git grep`: only tests and docs). Their readers
are also uncalled: `repo.latest_perp_structure` (`repositories.py:6312`),
`repo.latest_options_structure` (`:6332`) and `repo.batch_news` (`:6343`).
Reviving them would create a second LLM-context mainline next to
`entry_model_view`. Per the root AGENTS convergence rule, delete them in a
cleanup PR. Tier C facts enter only through `EntryAnalysisContext`.

### 1.4 Competitor baseline in one paragraph

r20 gets every Tier C item from **OKX public REST**. It recomputes them every
60s into one overwritten JSON file (`factor_library.py:302-308`). It keeps no
time series, and the brain never checks the file's age (`brain/packages.py:74-97`).
So r20 can neither replay nor measure any of these features, and its parity is
in prompt surface only. Specific defects not to copy:

- Its "CVD" is the newest, possibly still-open, single taker bucket. It is not
  cumulative (`okx_quant_factors.py:610-656`).
- Its "VAH/VAL" is VWAP ± 1σ, not a 70% value area (`:532-603`).
- Its P/C label may invert OKX's call/put ratio (`:896-897`).
- Its "news sentiment" is the all-account long/short ratio, with no text model
  (`news_sentiment_harvester.py:402-419`).

## 2. Items

Each item uses the same headings. "Path" says U (universe) or E (entry only)
and what that means for the fingerprint.

### 2.1 CVD 5m/1h and CVD-price divergence

**What exists today**
- No CVD and no trade/taker-flow table anywhere.
- `taker_buy_ratio` is in `quant_fields` (`entry_analysis.py:744`). It is fetched
  lazily only when |return_1h| > 3% (`features.py:148-151, 886-891`), through
  `extensions/data/ccxt_provider.py:421-449`:
  - The Binance branch is dead. It tests `ex_id == "binance"`, but the perp
    client is `binanceusdm`, and it calls the nonexistent
    `fapipublic_get_takerlongshortratio`.
  - **Bitget returns `None`.**
  - So on the Bitget route this LLM field is unavailable by construction, which
    answers #3785's "verify non-null rate" question from code. A Fedora count
    would only confirm it.
- `extensions/data/fast_stream_ccxt_pro.py:205-209` calls `watch_trades`, but
  keeps only `data[-1]` per batch, persists nothing, and runs only for
  watchable Entry Plan instruments (`agent_runtime/fast_trigger_watch.py:560`).
  It cannot produce CVD without a rewrite.

**Data source options**

| Option | Endpoint | Auth | History | Notes |
|---|---|---|---|---|
| A (recommended) | Bitget `GET /api/v2/mix/market/taker-buy-sell?symbol=&period=5m\|1h` | public | **latest 30 buckets only**. `startTime/endTime` were ignored when tested, so there is no backfill. | Per-symbol, base-coin `buyVolume`/`sellVolume`. Same venue as execution. Rate limit unverified. |
| B | Bitget `GET /api/v2/mix/market/fills-history` | public | A 10-minute window ~7.6 days back returned data. Max lookback unverified. | Trade-level, with `side`. Bulk backfill is impractical for liquid symbols at 1000 rows per call. |
| C | Bitget WS `trade` channel via the existing CCXT Pro transport | public | none | True trade-level CVD. Must aggregate in memory to 1m signed bars, because raw trades are too large to store. |
| r20 | OKX `/api/v5/rubik/stat/taker-volume` (coin-level, all contracts) | public | — | Frozen venue, coin-level not instrument-level. |

**Point-in-time**
- Use closed buckets only: `bucket_ts + period ≤ cutoff`.
- Store `source_ts` and `acquired_at`, and the as-of reader requires
  `acquired_at ≤ cutoff`. Each poll re-returns the last 30 buckets, so without
  this filter a later poll could make a bucket "known" before it was acquired.
- Freshness: newest closed 5m bucket ≤ 10 min old at cutoff, newest 1h bucket
  ≤ 70 min. Otherwise the field is `unavailable`, not 0.
- CVD_w = Σ(buy − sell) over the closed buckets in window w, normalized by
  Σ(buy + sell).
- Divergence is a frozen rule over aligned closed buckets. Example: sign of
  6×1h price change vs sign of 6×1h CVD. Keep it three-state; do not add r20's
  "bullish/bearish" narrative.

**Cost**
- One collector, one table, one as-of reader, one DU claim: about 300-500 lines
  with tests.
- Polling every 10 min (30 buckets cover 2.5h at 5m) gives about 6 calls/min for
  60 symbols. The 60-symbol count is illustrative; the actual active-universe
  size was not measured.
- Storage is ~288 rows/day/symbol at 5m, roughly 1.7 MB/day for 60 symbols, or
  about 100 MB over 60 days (*estimate*).

**Classification**: D1, a new observation fact plus an additive table; it does
not change Active Runtime Scope. Fixing `get_taker_buy_ratio` for Bitget is a
separate D0-like repair in `ccxt_provider.py`, not `features.py`, but see §1.1.4.
**Path**: E.

**Evaluation**: Use §3 with the feature `sign(CVD_1h) × direction`, comparing the
aligned and opposed buckets. Forward collection only: about 4-6 weeks of
calendar time before the minimum sample.

**Recommendation**: **Do**, as the first new collector. Use option A. Keep the
same table shape open for option C later, only if A shows signal.

### 2.2 Liquidation flow by side

**What exists today**
- `liquidation_events` (`migrations.py:1399-1410`: symbol, exchange, side,
  quantity_usd, price, event_time, observed_at) is written only by
  `extensions/data/collectors/coinalyze.py:300-395`:
  - Endpoint `/v1/liquidation-history`, emitting a `side=long` row and a
    `side=short` row per bucket, with `price=0.0`.
  - Rotating 10-symbol batch, 4h look-back, every 10 min.
  - API-key gated. It returns `errors=["no_key"]` and skips when no key is
    configured.
  - Pruned at 7 days.
- Readers: `context_builder.build_market_context_from_db` (attribution snapshot
  only, `entry_snapshot.py:193`), `derivatives_facts.py:490`, research and
  diagnostics. `features.py` and `entry_analysis.py` do not read it.
- #3099 deliberately keeps "liquidation impulse" unavailable because the
  Coinalyze population/unit/source contract is unproven
  (`data_utilization.md:110-112`).
- *Unverified*: the code comment says `.A` means "aggregated across venues".
  Coinalyze exchange codes may make `.A` a single-exchange code. The request
  also omits an `interval` parameter, and whether Fedora has a key and rows was
  not checked.

**Data source options**
- Bitget public WS liquidation channel. Per
  [03](../trading-theory/03_order_flow_liquidity_and_liquidations.md), it pushes
  1/s and keeps only the largest long and short record per pair in that second.
  That makes it a sampled lower bound, not total notional.
- OKX `GET /api/v5/public/liquidation-orders?instType=SWAP&uly=&state=filled`.
  Public; verified it returns `posSide`, `bkPx`, `sz`, `ts`. Frozen venue, last
  ~100 fills.
- Coinalyze (existing). Credentialed, and its semantics are unproven.

**Point-in-time**: `event_time ≤ cutoff` and `observed_at ≤ cutoff`. Fixed
windows (1h, 4h) of long and short notional. When the collector did not cover
the symbol in the window, the field is `unavailable`; zero is allowed only when
it covered the symbol and saw none. Label coverage: `sampled_largest_per_second`
for Bitget, aggregate for Coinalyze.

**Cost**
- Bitget WS collector plus aggregation table: about 400-600 lines. A WS
  lifecycle is more operational surface than REST.
- Storage is small (two aggregates per symbol-minute at most).
- Retention must go from 7 to ≥60 days for evaluation.

**Classification**: D1, observation-only. No Active Runtime Scope change.
Retiring the `liquidation_flow_unavailable` gap token would be a universe-path
D1 change and is blocked by #3786. **Path**: E now; U only after #3786.

**Evaluation**: §3. The feature is `(short_liq − long_liq)/(short_liq + long_liq)`
over 1h, aligned with direction. Separately, test the `positioning_forced_flow`
mechanism subset.

**Recommendation**: **Do, second wave.**
1. First, the owner confirms whether Coinalyze is configured on Fedora. If it is,
   run §3 offline on the existing rows. That costs zero new code.
2. Only if it is not configured, or the data is unusable, build the Bitget WS
   collector.

### 2.3 Liquidation-level clusters

**What exists today**
- `perp_structure_snapshots` has `short_liq_above_usd`, `long_liq_below_usd`,
  `poc` and others (`migrations.py:1412-1456`).
- Its only real writer is the manual owner-aggregate import
  (`aletheia data ingest-perp-structure`, `cli/data_cmd.py:291-309`). The Vergex
  collector is fixture-only (`vergex_structure.py:1-6, 68-95`), and the reader
  has no callers.

**Data source options**
- A *predicted* liquidation map needs position size, leverage, entry price and
  margin data per position. No public venue endpoint provides that. CoinGlass is
  paywalled (`data_utilization.md:146`). Per
  [03](../trading-theory/03_order_flow_liquidity_and_liquidations.md) it is
  unavailable as market truth.
- Clusters of *past* fills, as r20 does (`okx_quant_factors.py:1053-1123`),
  need per-event bankruptcy prices. Coinalyze rows carry `price=0.0`; the Bitget
  WS and OKX REST give a price.

**Point-in-time**: same as §2.2, and the clusters are recomputed only from
events known at the cutoff.

**Cost**: small once §2.2 has priced events, since it is a pure transform. Without
them it is impossible.

**Classification**: D1 (Grade C estimate, must be labeled). **Path**: E.

**Evaluation**: §3, with distance to the nearest same-side cluster in ATR units.

**Recommendation**: **Defer** until §2.2 has priced events and has shown signal
on its own. **Drop** the predicted heatmap.

### 2.4 Volume profile: POC / VAH / VAL and position in value area

**Check result: computable from stored OHLCV, so this is Tier B-like, not a new
source**, with one caveat: it is an estimate.

**What exists today**
- No POC/value-area code over OHLCV. The grep hits are perp-structure fields,
  `position_x.py` cost-basis bins and the dead renderer.
- `market_ohlcv` retains 15m for 30 days, 1h for 90 and 1m for 2
  (`repositories.py:6186-6193`).
- `entry_analysis` already freezes 96 closed 15m bars per instrument
  (`repositories.py:3456`, 24h) into `candle_windows`. The profile can be
  computed from exactly the bars the model already receives: no fetch, and
  point-in-time by construction.
- [07](../trading-theory/07_local_ohlcv_profile_feasibility.md) already
  established that candles support an *estimated* profile only. A true profile
  needs trades at price.

**Data source options**
- Estimated: `estimated_profile_from_ohlcv_v1` over the frozen 96×15m window.
  Use a fixed bin count, and either typical-price allocation (as r20 does) or
  uniform-range allocation. Use the standard 70% value area grown from POC, not
  VWAP ± σ. 1m bars would be finer but cover only 2 days.
- True: from the §2.1 option C trade stream. That only makes sense if §2.1 shows
  signal.

**Point-in-time**: only bars closed ≤ cutoff, the same bars as `candle_windows`.
If any bar in the window is missing, mark the profile `degraded`. Output the
level distances in ATR and `position ∈ {below_val, in_value, above_vah}`, plus the
allocation rule and bin count.

**Cost**
- One pure function plus a field in a new E-path block: about 150 lines with tests.
- No storage, no API.
- It can be evaluated **retroactively** on FCO entries, because 15m bars for the
  past 30 days are stored.

**Classification**: D1 (new model input; a Grade C estimate must say so).
**Path**: E. It must not be computed in `features.py`.

**Evaluation**: §3 retroactive. Use position-in-value-area × direction, and
distance to POC in ATR. Pre-register the allocation rule; do not tune it on the
outcome.

**Recommendation**: **Do in the first wave**, since no new acquisition is needed.
Owner decision D4 applies: is a labeled estimate acceptable in the LLM context?

### 2.5 Predicted / next funding on the primary execution venue

**What exists today**
- `funding_rate_snapshots` stores `funding_rate`, `funding_rate_annualized`,
  `next_funding_time` and `mark_price` (`migrations.py:1373`). It has no
  predicted-rate column, and `nextFundingRate` appears nowhere.
- The live read for Bitget in features uses `ticker.info.fundingRate` plus
  `fundInterval` (`ccxt_provider.py:451-509`).
- Hyperliquid predicted funding is stored as `exchange="hyperliquid_predicted"`
  (`collectors/hyperliquid.py:183-197`). It is observation-only.

**Venue facts (2026-10-08)**
- Bitget `GET /api/v2/mix/market/current-fund-rate` returns `fundingRate`,
  `fundingRateInterval` and `nextUpdate`. It returns no separate next-period
  rate. The `fundingRate` it returns is the live rate for the period that
  settles at `nextUpdate`. **On Bitget, the current rate is the predicted rate.**
- OKX `/api/v5/public/funding-rate` returned `method:"current_period"` with
  `nextFundingRate:""`. So r20's "predicted funding" field
  (`okx_quant_factors.py:1146-1150`) is blank for current-period instruments.
  Its prompt label promises more than the venue provides.

**Point-in-time**: `source_time ≤ cutoff`, age ≤ 15 min. This matches the
existing rule that derivatives rows older than 15 min leave decisions
(`data_utilization.md` OI row).

**Cost**: none for the rate. The missing piece is `minutes_to_next_funding` and
`funding_interval_h` from the stored `next_funding_time`, about 30 lines.
*Unverified*: that `ticker.info.fundingRate` equals `current-fund-rate`
`fundingRate` at the same instant.

**Classification**: D1, only if a new field reaches the model. **Path**: E.

**Evaluation**: §3 retroactive from `funding_rate_snapshots`, with funding sign ×
direction × time-to-settlement bucket.

**Recommendation**: **Drop as a Tier C source.** Fold time-to-settlement and
interval into the first-wave E-path block.

### 2.6 Elite account long/short ratio

**What exists today**
- Both ratios come from Binance regardless of the execution venue
  (`collectors/derivatives.py:37-38, 56-73`), with period 1h, limit 1:
  - `fapiDataGetGlobalLongShortAccountRatio` covers **all accounts** and is
    stored as `long_short_ratio`.
  - `fapiDataGetTopLongShortPositionRatio` covers **top-trader positions** and
    is stored as `top_trader_position_ratio`.
- Both are folded into the primary venue's `open_interest_snapshots` row, and
  both are already in `quant_fields`.

**Data source options**
- Binance `fapiDataGetTopLongShortAccountRatio`: one more implicit method in
  the existing batch. Cheapest, but it describes a Binance population, not
  the execution venue's traders.
- Bitget (verified public on 2026-10-08):
  - `/api/v2/mix/market/account-long-short` (`longAccountRatio`,
    `longShortAccountRatio`)
  - `/api/v2/mix/market/position-long-short` (`longShortPositionRatio`)
  - `/api/v2/mix/market/long-short` (`longShortRatio`)

  These return latest 30 periods only, with `startTime/endTime` ignored, so
  there is no backfill. *Unverified*: which of these is the elite/top-trader
  population and which is all accounts. Confirm from Bitget's documentation
  before naming any field "elite".
- r20: OKX `long-short-account-ratio-contract-top-trader` (5m), frozen venue.

**Point-in-time**: closed period only, ≤ 2 periods old. Same `acquired_at ≤
cutoff` rule as §2.1.

**Cost**: adding it to the §2.1 collector (same cadence, same table pattern) is
about 100 extra lines. Storage is negligible.

**Classification**: D1. **Path**: E. Replacing the existing Binance ratios in
features would be U, blocked by #3786, and a convergence question in its own
right.

**Evaluation**: §3, with the elite − all divergence aligned with direction. r20's
`RETAIL_TRAP` thresholds (`okx_quant_factors.py:709-731`) can be one
pre-registered rule, but are not evidence.

**Recommendation**: **Do**, in the same collector as §2.1, on Bitget.

### 2.7 Options: ATM IV, 25-delta risk reversal, put/call OI ratio (BTC/ETH)

**What exists today** (#3098, landed and observation-only)
- `OptionsStructureCollector` makes one Deribit
  `public/get_book_summary_by_currency` call per currency
  (`collectors/options_structure.py:37-79`). That one response feeds two outputs:
  - `options_volatility_snapshots` (`migrations.py:8386-8393`). Fields:
    `atm_iv_7d/30d/60d`, term slopes, `call/put_25d_iv_30d`,
    `risk_reversal_25d_30d` and `skew_25d_30d` (`data/options_volatility.py:13-35`).
  - `options_market_structure_snapshots`, including raw `call_oi` and `put_oi`
    (`data/models.py:189-191`).
- The derivation rules and the 30-min freshness rule are in
  `data_utilization.md` §4 "Options volatility observation contract".
- It runs every 10 min with 30-day retention. The only reader is the CLI
  (`options_volatility_at`, `repositories_market_data.py:1056`).
- **The only missing piece is the P/C OI ratio**, which is a pure division of
  stored columns.
- Deribit `public/get_volatility_index_data` (DVOL, verified public, with
  hourly history) could backfill an ATM-IV proxy for evaluation.

**Point-in-time**: already contracted: acquisition and source time ≤ cutoff,
acquisition ≤ 30 min before cutoff, no fallback to an older row.

**Cost**: an E-path block field plus the P/C derivation, about 100 lines. No new
API or storage.

**Classification**: D1 (removes "not in LLM" from DU-S31; needs an update to
the Data Utilization claim). **Path**: E. For a non-BTC/ETH candidate this is
market-wide context, labeled as BTC/ETH.

**Evaluation**: §3. This is market-wide: every symbol shares the value at a given
time, so **only day clustering is valid**. The effective sample is the number of
distinct dates, not rows. Evaluate on existing rows now (≤30 days, minus the
2026-09-30 → 10-08 gateway gap). The DVOL history can extend the test of an
ATM-IV-only hypothesis.

**Recommendation**: **Do in the first wave.** It is the cheapest real data in this
list. Expect a weak result for altcoin entries.

### 2.8 Quarterly basis (annualized) and borrow rate

**What exists today**
- No quarterly futures, borrow-rate, or spot-perp basis computation.
- The closest is `perp_price_snapshots` (mark and index price from the same
  response, `migrations.py:8295`), written by the comparison collector, including
  Bitget. `positioning_evidence.py` derives mark/index basis in bps, but the
  output is CLI-only.
- `basis_unavailable` is a static token (§1.1.3).

**Venue facts (2026-10-08)**
- Bitget `/api/v2/mix/market/contracts?productType=usdt-futures` returned 816
  instruments, all `perpetual`. `coin-futures` returned an empty list. Whether
  that `productType` string is correct is *unverified*. **No quarterly contract
  on the execution venue was found.**
- Bitget `/api/v2/margin/crossed/interest-rate-and-limit` returned
  `40006 Invalid ACCESS_KEY`. **Borrow rate on Bitget is authenticated**, which
  puts it out of scope (no credential reads).
- OKX `/api/v5/public/interest-rate-loan-quota` is public. It is a frozen venue,
  and the rate is a slow-moving quota rate. r20 shows it un-annualized
  (`okx_quant_factors.py:1285-1300`).
- Deribit BTC/ETH futures (`get_book_summary_by_currency?kind=future`) are on
  the same public client the options collector already uses. This was not
  separately tested.

**Point-in-time**: mark and index from the same response, source skew ≤ 5 min,
age ≤ 30 min (the #3099 rule).

**Cost**
- Perp premium (mark − index)/index on Bitget: pure derivation from stored rows,
  about 80 lines.
- Deribit quarterly basis for BTC/ETH: one more call in an existing collector,
  plus table or column work, about 200 lines.

**Classification**: D1. **Path**: E.

**Evaluation**: §3, perp premium × direction. Quarterly basis is market-wide, so
use day clustering (as §2.7).

**Recommendation**:
- **Do** perp premium in the first wave; it needs no new source.
- **Defer** quarterly basis (BTC/ETH only, via Deribit) until the options block
  shows any market-wide signal.
- **Drop** borrow rate, since it needs credentials on the active venue.

### 2.9 News / information sentiment

**What exists today**
- RSS collection runs every 15 min (`extensions/data/news_collector.py`,
  `scheduler.py:1499-1504`). An LLM analyzer runs every 5 min and sets
  `sentiment` and `relevance_score` on `news_items` (`migrations.py:534`).
- The decision reader `batch_news` (`repositories.py:6343`) has **no production
  caller**. The remaining readers are REST routes and research.
- `information_catalyst` is hard-coded unavailable
  (`opportunity_evidence.py:114-118`).

**Point-in-time hazards** (the reason this is last):
- Both `published_at ≤ cutoff` and `analyzed_at ≤ cutoff` are required. The
  analyzer may run after the cutoff.
- A model scoring old headlines in a backtest may already know the outcome
  (training-data leakage), so a retroactive evaluation is not valid. Only
  prospectively analyzed rows count.
- RSS `published_at` is publisher-supplied and can be revised.

**Data source options**: the existing RSS feeds (`config/schema.py:2046-2058`).
Paid news APIs are out of scope. r20 uses keyword importance and a long/short
ratio as its "sentiment", which is not text sentiment.

**Cost**: entity-level sentiment with provenance is a contract-sized piece of
work. Activating `information_catalyst` as a mechanism changes Candidate
mechanism topology.

**Classification**
- E-path news context: D1.
- `information_catalyst` activation: **D2** (a new mechanism in Candidate
  selection), which needs owner admission.

**Evaluation**: prospective only, §3, with ≥30 dates of live-analyzed rows.

**Recommendation**: **Defer**, and keep it as research. Revisit only after the
cheaper items show whether any added context moves the ledger.

## 3. Evaluation protocol (shared by all items)

The baseline to beat comes from #3785/#3786, using the Fedora FCO ledger: 36 days,
84,633 directional rows, mean directional gross of all candidates +0.004%
(t=+0.07 by symbol). Nothing is significant under day clustering.

**Stage 0: observation join, no LLM change (required for every item)**
1. **Ledger**: `filter_candidate_outcomes` (`migrations.py:337`), settled rows,
   `horizon='4h'` as primary, with 1h/24h reported but not used for the decision.
   Deduplicate by `outcome_candidate_key`.
2. **Join**: as-of join on `(venue, symbol, market_type)`. Use the latest feature
   row with `source_ts ≤ entry_ts` and `acquired_at ≤ entry_ts`, within the
   item's freshness rule. Rows outside the freshness rule count as
   `unavailable`. Report coverage, and do not impute.
3. **Metric**: mean `directional_return_pct` (gross, before costs; this answers
   "is there edge"). Compare the pre-registered aligned bucket against the
   opposed or neutral bucket. Report the difference and its t-statistic,
   **clustered by symbol and by UTC day**. The decision uses the day-clustered t.
   Report `net_return_pct` alongside for information.
4. **Minimum sample**: reuse the contract's support floor
   ([candidate selection utility](../../design/contracts/candidate_selection_utility_contract.md)
   §Chain item 4): per bucket, ≥50 independent 4h clusters, ≥10 dates and ≥5
   symbols. For market-wide features (§2.7, quarterly basis), the effective N is
   the date count, so require ≥30 dates.
5. **Pre-registration**: commit the bucket rule, sign and horizon before the
   first read. Hold out the last ⅓ of dates. With ~9 items × several variants,
   require day-clustered |t| ≥ 2 on **both** the discovery and holdout splits.
   This is a proposal; owner decision D5.
6. **Tooling**: reuse an existing clustered estimator if one fits
   (`analytics/profit_gradient_estimators.py:106 _cluster_interval`; fit not
   verified). Do not add a new framework.

**Stage 1: LLM wiring, only for items that pass Stage 0**
- Add them all in one batched E-path block, which means one
  `entry_behavior_fingerprint` rotation.
- Before/after on the same FCO selected-candidate cohort and on Entry Plan
  outcomes. Use the same metric and clustering, and the
  [technical-context evaluation protocol](../trading-theory/09_technical_context_evaluation_protocol.md)
  for the offline paired interpretation check.
- Entry outcomes are thin, so FCO is the primary read and fills are confirmation.

Passing Stage 0 proves only a conditional association in the candidate pool, not
that the LLM will use the fact well. Failing Stage 0 is enough reason not to
wire it.

## 4. Prioritized sequence

The order follows dependencies, with cheapest real signal first. Collectors that
need calendar time start early, so their sample accumulates while the retroactive
items are being evaluated.

| # | Step | New source? | Can it be evaluated now? | Class |
|---|---|---|---|---|
| 0 | Land Tier A/B (separate PR). The owner decides the #3786 fingerprint policy. | — | — | D0/D1 |
| 1 | Stage 0 join script over FCO (a reused estimator, not a framework) | no | — | research tool |
| 2 | **Start forward collection**: Bitget taker-buy-sell 5m/1h plus account/position long/short ratios in one observation-only collector, ≥60-day retention, DU claim (§2.1, §2.6) | yes (public, execution venue) | after ~4-6 weeks | D1 |
| 3 | Stage 0 on stored data: options IV/RR + P/C ratio (§2.7), estimated volume profile from 15m bars (§2.4), perp premium (§2.8), funding time-to-settlement (§2.5) | no | **yes, now** | research |
| 4 | Owner checks the Coinalyze configuration. If rows exist, run Stage 0 on liquidation side flow (§2.2), extending retention first. | no | if configured | research |
| 5 | Stage 0 on step-2 data (CVD, elite − all ratio divergence) | — | after sample | research |
| 6 | One batched E-path `EntryAnalysisContext` block for every item that passed steps 3-5, with Stage 1 before/after | — | — | D1 |
| 7 | Only if §2.2 is needed and Coinalyze is unusable: Bitget WS liquidation collector. Then past-fill clusters (§2.3). | yes | after sample | D1 |
| 8 | Deferred: Deribit quarterly basis; trade-level CVD/true profile (§2.1 C); news (§2.9) | yes | — | D1/D2 |
| — | Separate cleanup PR: delete `render_enriched_context_md`, `build_market_context` and their uncalled readers (§1.3) | — | — | D0 |

Dropped: predicted liquidation heatmap (no public input), borrow rate
(authenticated on Bitget), and a separate "predicted funding" source (Bitget's
current rate already is the predicted rate).

## 5. Owner decisions required

- **D1. #3786 fingerprint policy.** Until it is decided, Tier C stays on the
  entry path only, and the static gap tokens stay. Recommendation: decide #3786
  first. Do not ship any Tier C fact through `features.py`.
- **D2. Admit one new observation-only collector** on Bitget public market
  endpoints (taker-buy-sell plus the long/short ratios), with ≥60-day retention.
  This is D1 and does not change Active Runtime Scope.
- **D3. Coinalyze.** Is a key configured on Fedora? The owner checks; the value
  should not be shared. Is an aggregate third-party population acceptable, or
  must liquidation flow be venue-exact (Bitget WS)?
- **D4. Grade C estimates in the LLM context.** Is a labeled
  `estimated_profile_from_ohlcv_v1` acceptable, or is a trade-based profile
  required?
- **D5. Promotion rule.** Accept §3's pre-registered rule: day-clustered |t| ≥ 2
  on discovery and holdout, with ≥50 clusters, 10 dates and 5 symbols (30 dates
  for market-wide). Or set a different one.
- **D6. One batched `entry_behavior_fingerprint` rotation** for the Stage 1
  wiring.
- **D7. News**: keep as deferred research, or drop. Activating
  `information_catalyst` would be a separate D2.

## 6. Not verified

- The venue rate limits, the exact max lookback of Bitget `fills-history`, and
  which Bitget long/short endpoint is the elite population.
- The Coinalyze `.A` suffix semantics, the required `interval` parameter, and
  whether Fedora has the key or rows.
- The Fedora `market_data.ccxt_exchange` value (the code default is `binance`).
  Bitget alignment comes from earlier deployment notes, not re-checked here.
- Whether Bitget `ticker.info.fundingRate` equals `current-fund-rate` at the same
  instant.
- Whether Deribit futures basis is available through the existing client
  without new configuration.
- Every storage and API-load number is an estimate built on an assumed
  60-symbol active set.
