# Learning Qualification CLI

These commands are read-only. They do not admit Paper, activate a Skill, mutate
trading, or grant Risk/Execution/Live authority.

```text
aletheia learning nominations list
aletheia learning nominations inspect <nomination-id>
aletheia learning knowledge list
aletheia learning knowledge inspect <hypothesis-id>
aletheia learning yield report --start <ISO-8601> --end <ISO-8601>
aletheia learning meta-recommendations --start <ISO-8601> --end <ISO-8601>
```

All output is JSON. `knowledge list` reports `current_qualified_n: 0` and
`production_economic_contribution_state: none` when the Runtime has no current
qualified positive fact. Paper test fixtures demonstrate code semantics only and
are never production economic proof.

Knowledge Yield keeps incompatible path, behavior, metric, outcome, and horizon
identities in separate slices. Missing generation cost or root-outcome lineage is
reported as partial coverage. Meta recommendations use only `EXTEND`, `REPLICATE`,
`DIFFERENT_TEST`, `RETIRE`, or local `HOLD_INSUFFICIENT_EVIDENCE`; every result is
advisory-only.
