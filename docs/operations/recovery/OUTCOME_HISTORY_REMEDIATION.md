# Outcome History Remediation

This maintenance path keeps original close, cash, knowledge and audit evidence. It can
add deterministic classification to known administrative/maintenance closes and archive
polluted derived knowledge, but it never deletes history or applies cash compensation.

Preview first (the default):

```bash
uv run aletheia knowledge remediate-outcomes
```

The JSON report includes the stable batch/version, clean-window identity, planned
classification and archive counts, and paper-cash compensation candidates. Each cash
candidate is explicitly `owner_approval_required`; this command has no option or code
path that applies one.

Apply the classification/archive batch only after reviewing the preview:

```bash
uv run aletheia knowledge remediate-outcomes --apply
```

Apply is transactional and idempotent. It fills only deterministic classification
columns, archives affected active derived items, writes separate supersession markers,
rebuilds only deterministic non-LLM reports, and refreshes the search index. Ambiguous
legacy closes stay unverified. Repeating apply creates no additional classification or
supersession changes.

Any decision to compensate the paper cash ledger is a separate owner-approved task. A
future compensation must append a new explainable event; the original paper-account
event must never be edited or deleted.
