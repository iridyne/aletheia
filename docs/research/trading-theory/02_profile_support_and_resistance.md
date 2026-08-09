# Profile, Support, and Resistance

Date: 2026-08-09
Role: Research evidence

## Research question

What do Market Profile, Volume Profile, POC, value area, support, and resistance
measure, and which source data are required to compute them honestly?

## Market Profile is not Volume Profile

### TPO Market Profile

Time Price Opportunity (TPO) counts whether price traded in a price bracket
during successive time brackets. Its Point of Control is the price bracket with
the greatest time acceptance. A value area commonly expands around the POC
until it contains a configured share, often 70%, of TPO activity.

The original CBOT study material is available as a
[Market Profile study guide](https://www.profiletrading.com/cbot-a-six-part-study-guide-to-market-profile.pdf).

### Volume Profile

Volume Profile allocates executed volume to price brackets. Its volume POC is
the bracket with maximum assigned executed volume; VAH and VAL bound the chosen
value-area share. HVN and LVN identify relatively high- and low-volume nodes.

TPO POC and volume POC can differ. Aletheia must preserve the profile type in
every field name and provenance record instead of exposing an ambiguous `poc`.

## Data requirements

| Output | Minimum honest input |
|---|---|
| TPO profile | time-sequenced price observations or sufficiently fine bars |
| Exact venue volume profile | venue trades containing price and size |
| Approximate candle profile | OHLCV plus an explicit allocation model |
| Position-cost profile | position entry prices and notionals, not trade volume |

Ordinary candles contain total bar volume but not the prices at which that
volume traded. Uniformly spreading candle volume across the high-low range, or
assigning it all to the close, creates a model estimate rather than observed
volume-at-price. Such an estimate must never be labelled authoritative POC.

## Session identity

A profile is undefined without a frozen boundary:

- venue and physical market;
- timezone and session start/end;
- tick or price-bin size;
- TPO bracket duration;
- value-area percentage and expansion algorithm;
- regular versus extended hours, where applicable;
- rolling, daily, weekly, or anchored range.

Crypto trades continuously, so `UTC day`, `venue day`, `rolling 24h`, and an
event-anchored profile are different facts. Mixing them creates silent drift.

## Point-in-time rule

At decision time `d`, the model may receive only the profile built from events
with timestamps at or before `d`. A completed daily profile cannot be used to
explain an earlier intraday decision. Useful prospective fields include:

- developing TPO/volume POC;
- developing VAH and VAL;
- distance from price in basis points and ATR;
- prior completed-session POC/VAH/VAL;
- POC migration between completed snapshots;
- value-area acceptance or rejection defined by closed bars.

## Support and resistance

Support and resistance can be represented as price zones derived from
point-in-time inputs. New York Fed research using levels published by trading
firms found evidence that such levels predicted some intraday trend
interruptions, while performance varied by firm and currency:
[Osler, 2000](https://www.newyorkfed.org/medialibrary/media/research/epr/00v06n2/0007osle.pdf).

Possible reproducible level families are:

- confirmed swing-high and swing-low clusters;
- prior session high, low, close, POC, VAH, and VAL;
- Donchian or rolling extrema;
- round-number levels with a fixed tick rule;
- observed volume-profile nodes;
- position-cost or liquidation walls when their source population is known.

Each level should carry `source_family`, `formed_at`, `confirmed_at`, `price`,
`zone_width`, `touch_count`, `last_touch_at`, `timeframe`, and provenance.

## Reactions are outcomes, not definitions

A support level is not validated because price later bounced. The level must be
frozen before the test. Outcomes should separately measure:

- touch or penetration;
- maximum favorable and adverse excursion after touch;
- close-based rejection or acceptance;
- breakout continuation over fixed horizons;
- net result after estimated costs.

## Relation to existing Aletheia research

The Position-structure report discusses a different profile: weighted position
entry prices, break-even walls, and liquidation fuel. Those fields may resemble
Volume Profile visually but represent participant cost and leverage, not venue
executed volume. They require distinct names and data contracts.

## Research disposition

| Feature | Data availability | Initial authority |
|---|---|---|
| Closed-bar swing S/R | Existing OHLCV | Observation-only candidate |
| Prior session OHLC levels | Existing OHLCV | Observation-only candidate |
| TPO profile | Fine price path needed | Research, then observation |
| Venue Volume Profile | Trade-by-price feed needed | Unavailable until sourced |
| Position-cost POC | Position population needed | Separate research path |
| Predicted future support/resistance | Model output | Never a market fact |
