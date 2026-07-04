# Thesis Lineage V1.1 Observation Window

## Purpose

Thesis Lineage v1 should not immediately become a larger v2 system. The next
stage is a 7-14 day observation window that answers one basic question:

Can current Aletheia trading records reconstruct a complete, auditable judgment
chain from market selection to outcome?

The target chain is:

```text
universe -> opportunity -> decision -> proposal / skip -> shadow / position -> outcome
```

If the chain is mostly complete, v2 can be designed from real evidence. If the
chain is mostly broken, v2 should wait; the next work should be provenance and
data-completeness repair.

## V1.1 Scope

V1.1 only does three things:

1. Run Thesis Lineage v1 against live demo data for 7-14 days.
2. Measure whether the existing records are complete enough to judge.
3. Fix broken links and missing fields that prevent judgment.

This stage does not add intelligence. It checks whether the system leaves enough
evidence for intelligence to be useful later.

## Explicit Non-Goals

Do not add these during V1.1:

- new thesis generator
- new LLM normalizer
- new prompt optimization
- new automatic parameter tuning
- new UI surface
- new trading behavior
- new database table for formal `ThesisRecord`
- decision-time retrieval of similar historical thesis

LLM use remains allowed upstream where thesis is actually produced, such as
market_observer, System Operator, or Serenity. Thesis Lineage and audit code must
stay deterministic: read existing thesis / reasoning / evidence, mark missing
data as missing, and do not write after-the-fact explanations.

## Observation Commands

Run after v1 is merged and the gateway has been restarted on the target machine:

```powershell
uv run aletheia doctor --deep
uv run aletheia thesis audit --days 7
uv run aletheia thesis audit --days 7 --json
uv run aletheia thesis records --days 7 --json
```

For local snapshots, write JSON output under a gitignored runtime folder such as
`data/diagnostics/`. Do not commit raw observation snapshots or machine-specific
runtime facts into git.

## What To Measure

The audit should be read as a data-completeness report first, not as a product
feature demo.

Track whether records have:

- `opportunity_id`
- `signal_source`
- parseable `decision_meta`
- decision reasoning
- proposal link or explicit skip/watch decision
- shadow outcome for skipped/watch records
- position / fill / order links for traded records
- forward return or comparable outcome for non-traded records
- source family such as scanner family or Serenity theme

Track the high-level shape:

- total thesis count
- traded count
- skipped/watch count
- pending count
- missed-alpha count
- failed traded thesis count
- poor-expression count
- parse warning count
- schema warning count

## V1.1 Fix Rules

Allowed fixes:

- preserve missing `opportunity_id` where the system already knows it
- preserve missing `signal_source` where the source is already known
- keep `decision_meta` parseable when it is already produced
- connect existing proposal / order / position / shadow records by their causal IDs
- add focused tests for any provenance repair

Not allowed:

- infer a missing thesis by calling an LLM
- invent a source after the fact
- change ranking, sizing, leverage, risk limits, or live-trading behavior
- add a new storage model before proving existing records are worth formalizing
- treat UI polish as evidence that the judgment chain is complete

## V2 Gate

Do not start v2 until the 7-14 day window has enough evidence to answer:

1. Are most records linkable across the intended chain?
2. Are missing fields concentrated in a few fixable writers, or spread across the system?
3. Can the audit consistently distinguish missed judgment, failed expression, and weak source quality?
4. Are shadow / forward outcomes available often enough to judge skipped opportunities?
5. Does the report produce useful next actions without adding a new LLM layer?

If the answer is yes, v2 candidates can include formal `ThesisRecord`
persistence or decision-time retrieval. If the answer is no, continue V1.1 and
repair provenance first.

## Exit Criteria

V1.1 is complete when a human can look at 7-14 days of Thesis Lineage reports and
make one of two evidence-backed decisions:

- Proceed to v2 because the judgment chain is mostly complete and useful.
- Delay v2 because missing provenance or outcomes are the current bottleneck.

Either result is acceptable. Shipping a smarter-looking module without this
evidence is not.
