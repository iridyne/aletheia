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

The initial implementation checkpoint preceded merge; deployment results follow below.
Before normal update, re-read unresolved external commands and account state;
afterward record running/disk commit, schema/session, health, exact account and
selected historical row hashes. Do not create a certificate or synthetic trade.
Return to the first real natural-path breakpoint in #3031. #3035 completion alone
cannot close #2899's runtime evidence gate or prove profitability.

## Fedora deployment checkpoint

PR #3036 merged as `51387fb70d2815e8209d356215796b11ff96144e` and normal
`uv run --no-sync aletheia update --verbose` deployed it in 23.480 seconds.
At 06:52:21 UTC, disk and running commit agree, PID 447111, session
`e0bc27dbc722`, schema v79, Bitget Demo/classic_v2, credential generation 29.
Account fingerprint matches the 06:37 baseline. Live remains disabled, scheduler
running, paused=false. Selected historical facts remain identical: 847 Orders,
838 Fills, 419 Positions; exact fields/hashes and before/after snapshots are in
[Fedora evidence](evidence-3035/fedora-deployment.json).

Health is **degraded (`candidate_refresh_failed`)**, not healthy. Startup private
account cache is unavailable; its empty positions list does not prove zero external
exposure. Pre-deploy local audit found no external Orders, no open local Positions
and all 22 Bitget Demo Proposals rejected, so no pending Proposal was replayed.
Initial startup readback returned 503 and a separate cache helper encountered a
writer-permit timeout; subsequent audit reads use direct SQLite mode=ro.

The scanner failure predates deployment: `e1d93da3d1e2` at 06:47:53 UTC failed
on Bitget public coins; `8dd4192a8125` at 06:52:04 failed on public mix contracts.
A subsequent public coins HTTP probe returned 200; it does not establish private
account health. No evidence connects these failures to removed certificate code,
which is not on account-read or scanner paths. Preserve the observation rather
than claiming overall healthy convergence.

All 138 persisted Plans are terminal at this snapshot. Latest canonical natural
chain remains Candidate `d0bf4311fcb5`, Generation `b0a26e335f2f`, Plan
`ee9ae4665093`, Proposal `2afd512012bf`, Risk `74ef7bfc8837`: refused for
`stop_distance_below_vol_target:0.2532<1.5000` and `spot_short_not_allowed`.
No new external Order/Fill/Position or round-trip is proved. The certificate is
absent and its deployed module/issuer are deleted, but no eligible natural command
has yet exercised the removal externally. #3031 returns to
`waiting_for_runtime_evidence`, with scanner/private-account recovery explicitly
outstanding. #2899 Gate B stays open.

The control-status check at 06:54:43 UTC independently verified the durable session
against the live Gateway process tree (`verified_live_gateway=true`); deployed,
disk, running and recorded SHA agree. A 06:55:08 readback still reports the same
scanner/private-account limitation. This bounds #3035's completed code retirement
and rollout verification; it does not certify general runtime health or Gate B.

At 06:55:54 UTC the existing scheduled private sync had recovered without operator
intervention: fetched_at 06:55:19, sync_status=ok, sync_complete=true, exact same
Bitget Demo identity, account/position completeness=complete, zero external
positions in that complete snapshot. Health still carries `candidate_refresh_failed`
from the last scanner attempt. Thus private-account recovery is now observed;
scanner recovery and a new executable natural Plan remain unproved.
