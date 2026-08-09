# Trading Theory Research

Date: 2026-08-09

## Role and authority

This folder contains research evidence. It does not define current product
support, approved trading behavior, Risk policy, or execution authority.
Current technical-analysis behavior remains owned by the
[Technical Analysis and OHLCV Tiering Contract](../../design/contracts/technical_analysis_contract.md).

The goal is to translate trader vocabulary into point-in-time, falsifiable
features before any concept is considered for an AI decision input. A familiar
name, persuasive chart, community example, or Paper result is not Alpha proof.

## Research map

1. [Price Action and Market Structure](01_price_action_and_market_structure.md)
   separates observable price structure from ICT/SMC narrative.
2. [Profile, Support, and Resistance](02_profile_support_and_resistance.md)
   distinguishes TPO Market Profile, Volume Profile, POC, value area, and
   reproducible support/resistance.
3. [Order Flow, Liquidity, and Liquidations](03_order_flow_liquidity_and_liquidations.md)
   identifies which features require trades, L2 events, positions, or venue
   liquidation feeds.
4. [Evidence and AI Injection Policy](04_evidence_and_ai_injection_policy.md)
   defines evidence grades, time alignment, compact context design, and the
   observation-to-decision promotion path.
5. [LLM Context and Tokenomics Alignment](05_llm_context_and_tokenomics_alignment.md)
   maps technical facts onto the existing unified inference harness and its
   content-free token, retry, fallback, and schema telemetry.
6. [Quantitative Market-Structure Methods](06_quantitative_market_structure_methods.md)
   identifies causal primitives that can be reused from academic work and
   separates them from unstandardized ICT/SMC names.
7. [Local OHLCV and Profile Feasibility](07_local_ohlcv_profile_feasibility.md)
   audits the local candle lifecycle and states what kind of profile can and
   cannot be derived from it.
8. [Hyperliquid Public-Data Path](08_hyperliquid_public_data_path.md)
   records current integration coverage and a bounded, zero-credential path to
   trades, L2, and candles without changing execution authority.
9. [Technical-Context Evaluation Protocol](09_technical_context_evaluation_protocol.md)
   turns context selection into an offline paired evaluation followed, only if
   admitted, by Shadow integrity and Paper economic stages.

## Shared vocabulary rule

Every candidate concept must state:

- a mathematical or algorithmic definition;
- exact input data and venue coverage;
- feature timestamp and decision timestamp;
- parameters and timeframes;
- missing/degraded behavior;
- look-ahead and selection-bias controls;
- whether it is unavailable, research-only, observation-only, or eligible for
  a separately approved decision experiment.

If two theories reduce to the same measurable primitive, Aletheia should store
one fact and expose aliases only in explanation. For example, a failed breakout,
liquidity sweep, and some ICT stop-run descriptions may refer to the same
pre-registered price sequence; three labels must not become three votes.

## Current high-level conclusion

- Standard technical indicators are cheap, reproducible summaries of closed
  OHLCV, but they are transformations of the same price history rather than
  independent evidence.
- Price Action and the computable subset of ICT/SMC can become closed-bar
  structure features after definitions are frozen.
- Volume Profile requires volume-at-price inputs. Candle-level OHLCV cannot
  reconstruct an authoritative POC or value area.
- Order-flow imbalance has a stronger microstructure basis but requires
  correctly sequenced trade or order-book events.
- Liquidation feeds are venue-specific and often aggregated. They describe
  observed forced flow, not a complete global liquidation map.
- Any new field that reaches an AI decision prompt is a behavior change and
  requires a new policy identity and prospective observation window.

## Local evidence links

- [Imported community strategy methodology](../knowledge-bases/clustr100-community/02_策略构建方法论.md)
- [Imported ICT/SMC signal examples](../knowledge-bases/clustr100-community/04_信号格式大全.md)
- [Market-context evaluation](../market-data/market_context_eval.md)
- [Position-structure research](../trading-strategy/position_x_deep_research.md)

Imported examples are leads, not validation. Normative conclusions must be
promoted to a Contract, Decision, Proposal, Product Truth, or Operations guide
only after their corresponding authority is approved.
