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

After deploying a build that introduces a new Knowledge document schema, run
the current-root-only migration before observing Learning:

```text
aletheia knowledge scan-recovery-sources
aletheia knowledge backfill-content --dry-run
aletheia knowledge backfill-content
aletheia knowledge reindex
```

`scan-recovery-sources` is report-only. Backfill never follows a stored
absolute path into another Runtime; it accepts only the corresponding relative
document under the selected current data root. Hash conflicts remain
unavailable rather than silently selecting file or database content.

All output is JSON. After the Lab-only cutover, current Economic Knowledge and
Knowledge Yield report `unavailable` until canonical consumed-result evidence
supports qualification. Historical positive verdicts remain evidence, not current
qualified facts. Do not interpret the empty projection as zero economic effect.
Nomination provenance remains readable; recommendations stay advisory-only.
Paper fixtures demonstrate code semantics, never production economic proof.
