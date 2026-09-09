---
id: retire-local-simulated-exchange
status: accepted
github_issue: https://github.com/iridyne/aletheia/issues/2899
related_contracts: []
supersedes: []
---

# Retire the local simulated exchange

Status: accepted. SDD depth: D2. Related change: [#2899](https://github.com/iridyne/aletheia/issues/2899).

The owner has selected complete retirement of the local simulated exchange from
the product, its implementation and current terminology. Keeping an internal
account, matching engine and settlement model alongside external exchanges adds
an execution world that does not establish external exchange correctness.

## Accepted boundary

External exchange environments are the execution targets. Remove local account
controls, simulator dispatch, synthetic cash/fill/funding production, scheduled
simulator recovery and local-exchange defaults. Retire their current product and
contract definitions into design history. Do not replace them with a renamed
simulator, compatibility dispatch or a second account abstraction.

Historical records keep their original mode, identity, hashes and economics.
Reading history must not initialize accounts, settle old positions or convert
simulated evidence into external exchange qualification. Existing migration
history remains intact. An absent exchange configuration must not silently
select a funded account or authorize an order.

Deterministic tests use exchange-response fakes at the existing UTA boundary.
Tests for shared causal identity, Risk approval, partial fills, unknown submission
and recovery remain; tests solely requiring the retired product are removed.
Replay and counterfactual research do not gain account or execution authority.

## Completion evidence

Current executable paths and configuration expose only external exchange
environments. No production caller can create simulated account events, fills
or settlements. Current documentation and UI describe the same scope, and the
retired concept is confined to explicit historical context. Focused regressions
cover external execution and preserved historical reads; frontend and document
checks validate the changed surfaces.

Repository retirement does not prove external exchange economic qualification.
This change does not authorize deployment, credential changes, live trading,
historical data rewrites or new exchange/account routing.
