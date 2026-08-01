# Harness Header Ablation — 2026-08-01

Issue: #547. Model: `claude-haiku-4-5` through the configured CSI LiteLLM endpoint.

## Protocol

- Five preregistered caller classes: market observer, PI trading decision, risk critic, research worker, dreaming agent.
- 100 deterministic synthetic cases per caller, paired control/header arms: 1,000 physical calls total.
- Same provider, model, temperature (`0`), user facts, output contract, and token ceiling in each pair.
- The only arm difference was the proposed compact Harness v0.1 context header.
- Runtime prompts were not copied from the economics ledger. The ledger intentionally stores attribution and usage, not prompt bodies. Inputs were versioned, synthetic, and contained no account, credential, path, or runtime free text.

## Automated Results

| Caller | Control compliance | Header compliance | Input tokens control/header | Output tokens control/header |
|---|---:|---:|---:|---:|
| dreaming_agent | 100% | 100% | 84 / 158 | 191.61 / 232.50 |
| market_observer | 100% | 100% | 91 / 164 | 190.76 / 198.22 |
| pi_trading_decision | 100% | 100% | 109 / 184 | 119.26 / 147.10 |
| research_worker | 100% | 100% | 87 / 160 | 148.99 / 153.56 |
| risk_critic | 100% | 100% | 61 / 134 | 259.46 / 269.97 |

The header added 73–75 input tokens per call. Structured/format compliance did not regress in this corpus. Output length increased in every caller, from 4.57 tokens for research to 40.89 for dreaming. Therefore the cost effect is not limited to the static header: model verbosity also changed.

## Gate Status

- Schema-compliance gate: passed on this synthetic corpus.
- Token-cost gate: measured, but not yet accepted. No immutable pricing snapshot existed, so this report makes no USD claim.
- PI decision-quality gate: pending owner blind review of 30 paired responses in `harness_header_blind_review_2026-08-01.json`.
- Rollout: `harness_header_default_on` remains `false`. No production prompt mutation is authorized by this evidence alone.

## Limitations

- Synthetic replay isolates the header but does not represent the full size or diversity of production contexts.
- Free-text callers have format/non-empty checks rather than domain truth labels.
- The owner blind review is mandatory; model self-scoring is not a substitute.
