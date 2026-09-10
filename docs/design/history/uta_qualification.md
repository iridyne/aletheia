# Archived: SHA-bound UTA Qualification

Retired by owner-approved #3035 (2026-09-10). The former `qualify_uta.py` script
issued `qualifications/uta-<mode>.json` after direct-adapter probes. The runtime
reader bound permission to repository SHA/worktree, profile, account generation
and probe evidence. This evidence did not exercise the normal Risk/Gateway path.

The reader, issuer and certificate-only tests are deleted. Historical artifacts
and audit records may remain as history, with no current mutation authority.
#2330/#2331/#2439/#2445 describe enforcement of this retired mechanism; exact
sending-account comparison survives independently in the Coordinator.

The current authority is defined in the [Execution Contract](../contracts/execution_contract.md)
and the [accepted retirement decision](../proposals/retire_uta_qualification.md).
