## Linked Issue / Change Contract

References #3134. Implements the owner-approved final Scheme A physical cutover after logical owners converged through #3389.

## SDD Depth

D2 — accepted physical source-layout change only.

## Contract Delta

MODIFIED: relocate the sole Core source from `aletheia/` to `core/aletheia/`, preserving the `aletheia` Python namespace. Existing Extensions/Ops/Legacy owners remain unchanged. Retarget actual source-root consumers, current Contract paths and executable source-boundary checks. No behavioral, Risk, economic, credential or runtime-policy delta.

## Does this change alter semantics or structure?

Structure (D2): the accepted #3134 Scheme A root layout. No new architecture framework, compatibility facade or packaging product.

## Contract convergence

- [x] Implements the accepted #3134 owner direction for the final physical batch; logical boundaries are not reopened.
- [x] Current Contract source paths and existing executable source-path checks follow the moved owner.
- [x] Source and frozen configuration roots are distinguished; frozen resource destination remains `aletheia/dashboard_static`.
- [x] Source-qualified candidate preflight and child-launch path checks preserve existing scope and authority.
- [x] Documentation governance check passes.
- [ ] Runtime / economic qualification: separate Fedora exact-head deployment and qualification gates remain outstanding; no deployment or qualification claim from this PR.

## Verification

Exact published head: `690c1b68020a4d11a9ffcf5b217216febd17ab37`; base master: `cfe364168e0d7b3afd4fdcbee8085f797ff6257c`. Source move commit `dbe635aa9000c567a8e1043b751eef47a56b5f16` and ordinary integration `0fba057539536e65704134ba162a7485ec2e1e2c` have identical tree `df0ce0a95a619292a49e105e93b352dfa83b7308`. The final two-file repair aligns the Runtime Scope Learning owner symbol and prevents an empty AR-S04 source scan; no permission criterion changes.

- Main-agent final exact-head union: **814 passed in 56.78s**, exit 0, across 21 named architecture/writer/optional-absence/credential/Gateway/updater/metadata/source-scope files. Fresh temporary data root, `ALETHEIA_RUNTIME_MODE=test`, `PYTHONPATH=core:.`, existing shared uv environment and `uv run --no-sync`. The final mock fix is present. This is focused local repository validation, not runtime qualification or a full suite.
- Main-agent committed diff check, documentation governance and PR metadata preflight passed on the same exact head.
- Core import probe with `PYTHONPATH=core:.` resolves `aletheia.__file__` to this checkout's `core/aletheia/__init__.py` and config root to this repository.
- Metadata/config/frontend/release fixture checks: 80 passed.
- Optional-owner/source-boundary union: 256 passed.
- Audit fixture-root and network/UTA/Knowledge scanner checks: 54 passed.
- Gateway/runtime locators: 167 passed after adding the missing kill mock (worker receipt). The first Gateway test run lacked that mock and may have attempted to signal real PID 1234; impact is unknown. This is not presented as a fully process-isolated first run.
- Writer/architecture/CI/coverage guards: 450 passed (worker receipt).
- Independent Position field/settlement/raw-writer guard on moved sources: 105 passed.
- Updater candidate provenance/schema/static-root locators: 78 passed (worker receipt).
- Runtime Scope source/authority checks after the final two-file locator repair: 4 passed.
- Independent scoped source reviews report no remaining blockers. No actual wheel/frozen build, remote CI, deliberate Gateway startup, credential use or deployment. The first-run process-signal caveat above remains explicit.

## Non-goals / out-of-scope

No new owner seams, dependency changes, Live enablement, window reset, order submission, state migration or economic semantics. Old deployed updater is not claimed to support a one-hop layout transition; deployment uses separately authorized operational handling.


## Review repair — round 1

Repair head: `043d4f6934a5ce7a684e7fc8bf3ab28744e17ece`. Main-agent combined 26-file focused validation: **947 passed in 67.94s**, exit 0; docs governance, PR preflight and diff check passed. The verified repair tree was committed without further source edits. This replaces, rather than adds to, the overlapping worker test counts. No remote CI or runtime qualification claim.

All eight exact-690c review findings validated and repaired in one bounded batch: Git-object version lookup (current and historical refs), non-vacuous audit/Pydantic scans, controlled-path inventories, Agent/Runtime Scope source locators, historical/current CI-yield classification, and active alpha-attribution source owners. No Risk, economic, credential, window or trading-policy change. Focused repair slices: 120 updater tests, 19 audit/Pydantic tests and 45 classifier/attribution tests passed. The earlier 814 result remains evidence for 690c only; repair-head combined validation is recorded in the readiness checkpoint.
