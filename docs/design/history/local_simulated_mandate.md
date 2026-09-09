# Archived local simulated mandate provenance

The native Paper Internal mandate-provenance producer is retired. Its config reads,
Opportunity enrichment and pair-freezing helpers are removed. Existing Proposal
hashes and evidence are unchanged; current entries keep their independent position
hypothesis and do not inherit this feature. Current configuration and read-only
inspection remain defined in the [Trading Mandate Contract](../contracts/trading_mandate_contract.md).

## Former producer contract

For a new native local-Paper entry Proposal, the pair below is frozen in the
existing hash-covered `trade_proposals.attribution_snapshot` JSON before the
Proposal hash and existing `position_hypothesis/v1` snapshot are finalized:

```json
{
  "trading_mandate_identity": {},
  "trading_mandate_fit": {}
}
```

The pair is frozen exactly once. Equal repeated freezes are idempotent and a
conflicting rewrite fails closed. The persistence path does not add a table or
migration. Demo, adopted, administrative, legacy, and unsupported paths are not
assigned guessed mandate evidence. Historical rows without the pair report
`not_recorded`; there is no backfill or reconstruction from current config.

Position Replay exposes the exact pair inherited through
`positions.proposal_id -> trade_proposals.id`, and preserves explicit
unavailable/malformed states. It does not re-evaluate old Positions using the
current mandate.

Adding this evidence to the existing hash-covered attribution snapshot rotates
new Proposal `commit_hash` values. Behavior remaining observe-only does not make
qualification or evidence identity automatically comparable.

