# Current Behavioral Contracts

Files in this directory describe behavior that is currently approved and
observable. They are not roadmaps.

Every Contract should identify:

- the production entry point;
- durable state and final consumer;
- observable end result;
- invariants and trust boundaries;
- failure and recovery semantics;
- executable evidence for current behavior.

Future targets, unwired components, and desired guarantees belong in
[`../proposals/`](../proposals/). Known mismatches belong in GitHub Issues. A
Contract may link to either, but must not express them as current behavior.

Legacy or retired mechanisms belong in [`../history/`](../history/).

Use [template.md](template.md) for new Contracts. Contract changes and their
executable claims must remain aligned, but claim conformance does not determine
Product Support.
