# Partial and Broken Capability Review

This is the read-only second pass over the 16 `Partial` and 3 `Broken`
capabilities recorded after the original Implemented promotion review.

The review checks current production wiring and focused executable evidence. It
does not treat external network access or natural runtime observation as a
mandatory support gate, and it does not authorize implementation.

## Result

| Capability | Previous | Reviewed status | Current fact |
|---|---|---|---|
| RT-10 Health/Doctor | Partial | Supported | Serving Runtime identity/port and two-context API isolation are executable. Default-branch delivery resolves #426. |
| RT-14 Tunnel lifecycle | Partial | Implemented | Auth fails closed; executable and config-command identity now protect start/status/stop from unrelated PID reuse, with focused lifecycle acceptance. |
| MD-04 Liquidation/macro/cross-venue context | Partial | Supported | Producer, freshness/degradation, observer context and final data-utilization tests pass. |
| MD-09 Market regime detection | Partial | Supported | Detector, durable regime, Universe/ranker/position consumers and attribution tests pass. |
| MD-10 News ingestion and analysis | Partial | Supported | Scheduler, analysis lifecycle, retry/failure state, research consumer and UI/API paths have focused acceptance. |
| DC-08 Agent run/opportunity observability | Partial | Supported | Durable failures, run/step APIs and Agent Activity error rendering form a complete observable path. |
| DC-09 Manual agent triggering | Partial | Supported | POST returns a job identity; bounded queue persists queued/running/final states and rejects overload. |
| EX-04 New-proposal live mode | Broken | Broken | Live unlock is still not consumed at proposal-mode selection; Settings/contract/diagnostics now state the experimental authority-only boundary, `LU-C07` records the missing join, and #418 remains open. |
| EX-07 Exchange-native TP/SL | Partial | Experimental | Broker/private-fill paths are tested, but external venue behavior is intentionally outside the supported product surface. |
| EX-09 Market-aware position management | Broken | Broken | Settings and delivery reporting are honest, but the wake terminates on an empty entry queue; its evidence is not rendered into the Operator prompt; and the active schema rejects hold/adjust/close. |
| EX-16 Exchange-backed cost-complete close | Partial | Partial | Local paper is separately classified as EX-14 Supported; external funding/bill producer remains absent; #386. |
| EX-15 Partial reduce and scale-in | Partial | Experimental | Active Operator and dispatcher reject mutation; the isolated helper is retained only as a reference and no product capability is promised. |
| PF-03 Capital-adjusted curve | Broken | Implemented | Capital Events now feed a separate adjusted account-equity series with visible method, markers, and baseline; raw equity and verified PnL are unchanged. |
| LN-03 Dreaming consolidation | Partial | Supported | Daily scheduler/agent output is executable; extracted `trade_case_study` lessons cross review into the applied Layer3/Operator-prompt consumer. |
| LN-14 Persona base sizing | Partial | Supported | The capability is narrowed to its real promise: persona notional/Kelly reach immutable Proposal quantity; adjacent leverage/stop/add experiments remain LN-16/LN-17. |
| LN-16 Persona progressive unlock | Partial | Experimental | Runtime now ignores reference-only ladder fields and Settings names the lifetime-history limitation. The default-off experiment still lacks a prospective Persona/policy cohort; #409. |
| LN-17 Persona pyramid decision | Partial | Experimental | `pyramid_plan()` is a bounded reference helper; Settings/API block new enablement and preserve an off-ramp for legacy configs; #410. |
| LN-19 Policy history and diff | Partial | Supported | `/policies/diff` precedes the dynamic route; `LP-S22` crosses API diff, detail, and history against two immutable versions. Default-branch delivery resolves #428. |
| OS-03 Exchange credential verification | Partial | Implemented | Bitget, Binance, and OKX candidate/stored checks now use private read-only endpoints; failures close clients and never echo raw secrets. Outside Supported Core. |

## Evidence executed

All focused runs passed:

- 159 checks for News, Agent job queue, Dreaming, TP/SL, credential/settings and
  learning-policy paths;
- 37 regime detector and final-consumer checks;
- 68 macro, enrichment freshness and data-utilization checks;
- 20 Agent run lifecycle, backend handoff and manual-trigger checks.

The policy-diff route order was also reproduced directly: a request for
`/api/persistence/policies/diff` entered `get_policy(policy_type="diff")`
instead of `diff_policy()`.

## Classification change

| Status | Before | After |
|---|---:|---:|
| Supported | 59 | 64 |
| Implemented | 3 | 3 |
| Partial | 16 | 9 |
| Experimental | 19 | 20 |
| Broken | 3 | 4 |
| Total | 100 | 100 |

Five rows were evidence gaps rather than product gaps. One external-exchange
row moved to the intentional Experimental boundary. One previously hidden
route-order defect was found.

Subsequent current-state adjustments do not rewrite that historical pass:
DC-13 first moved from Experimental to Partial when its missing feedback
consumer was confirmed, then returned to Experimental after the UI explicitly
bounded More/Less to history-only behavior. PF-03 moved from Broken to Partial
after its false owner-facing promise was removed, then to Implemented after the
separate adjusted-equity consumer was added. EX-09 remains Broken after a
deeper consumer review showed that honest wake-only copy did not create an
Operator position-decision consumer. LN-19 moved from
Broken to Supported after its route and endpoint acceptance were repaired.
LN-03 moved from Partial to Supported after its existing
`trade_case_study`-lesson consumer was executed with the correct durable source
identity. LN-14 moved from Partial to Supported after base entry sizing was
separated from adjacent Persona experiments and executed through Proposal
quantity. Persona progressive unlock then moved from Partial to Experimental:
unconsumed fields are filtered, while the remaining lifetime-history evidence
gate is honestly default-off and tracked by #409. Subsequent Doctor, policy
diff, credential, and tunnel reviews bring the current totals to 68 Supported,
4 Implemented, 6 Partial, 21 Experimental, and 1 Broken.
