# Prompt Guardrail Characterization — 2026-08-01

Issue: #546. This Phase 4 work is detect-only: it records stable rule IDs and match counts in the cost ledger and never stores prompt text, matched material, or secret values. It cannot deny, redact, reroute, or otherwise change an LLM call.

## Corpus and result

The corpus is the five deterministic, non-secret caller contracts used by the #547 harness-header study: market observer, PI trading decision, risk critic, research worker, and dreaming agent. Across all five base system/user templates, the detector produced zero findings.

Negative controls containing synthetic OpenAI-like, AWS-like, GitHub-like values and a `.secret.key` filename each triggered their expected rule identifier. The persisted observation contains only `{rule_id, match_count}`; tests assert that no matching value can be reconstructed from the record.

## Rules

- OpenAI-style `sk-…`
- Anthropic-style `sk-ant-…`
- AWS access-key prefixes
- GitHub token prefixes
- `.secret.key` filename tokens

## Decision

The rule set is suitable only for a bounded detect-only window. It must collect real, sanitized aggregate counts before anyone considers a block or redaction behavior. It intentionally does not scan responses, and it does not modify `aletheia/config/secrets.py`.
