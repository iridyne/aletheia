# #3035 SHA-bound UTA Qualification retirement

Owner-approved scoped D2 under #3031; baseline master `0c05294c77ea776e96c578994eadfa03fa98d407`.
Decision: [accepted Proposal](../../design/proposals/retire_uta_qualification.md).

## Code and boundary proof

The only runtime reader was `trust/uta_qualification.py`; its sole production
consumer was Coordinator entry, close, modify/cancel and protection. The dedicated
`qualify_uta.py` writer directly drove adapters and produced the SHA-bound JSON.
Both are removed, along with certificate-only tests and obsolete mock patches.
No current API/UI certificate projection was found. Historical records remain.

`_create_bound_adapter` retains exact comparison on the actual sending instance.
Existing Order persistence, Risk requirement, claim/CAS, ambiguity handling,
private Fill, close ownership and cost completeness are unchanged. Live continues
to require its independent Risk/Gateway Trust authority. No migration or replacement
certificate/flag/fingerprint/ledger is introduced.

## Focused validation and limits

[Validation evidence](evidence-3035/validation.txt): the new no-artifact/unrelated-SHA
regression failed before removal; the affected neighbor set has 402 passed and one
existing #3008 xfail. The 15 adopted-position fixture failures and 13 Entry E2E
activation failures reproduce at the same exact nodes on unmodified master.
No assertions were removed to hide these failures. Entry E2E is therefore not
claimed as passing full Planner-to-exchange proof. A proposed fixture repair was
withdrawn; this batch does not alter Planner provenance or immutable Plan rules.

Passing checks include authoritative Risk/Gateway integrity, Live authorization,
Coordinator identity and adapter failure, submission ambiguity/recovery, external
close/private Fill, protection and canonical Universe Outcome. Local tests use
adapter doubles; they prove no real external order or round-trip. No remote CI
or local Gateway was launched.

## Runtime gate

Merge/deployment verification is pending at this implementation checkpoint.
Before normal update, re-read unresolved external commands and account state;
afterward record running/disk commit, schema/session, health, exact account and
selected historical row hashes. Do not create a certificate or synthetic trade.
Return to the first real natural-path breakpoint in #3031. #3035 completion alone
cannot close #2899's runtime evidence gate or prove profitability.
