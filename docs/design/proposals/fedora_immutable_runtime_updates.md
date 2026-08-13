---
id: fedora-immutable-runtime-updates
status: draft
related_contracts:
  - docs/product/ACTIVE_RUNTIME_SCOPE.md
  - docs/operations/deployment/DEPLOYMENT.md
supersedes: []
---

# Fedora immutable Runtime updates

## Summary

Consider replacing the Fedora stable-Tag source-checkout updater with a signed,
immutable Linux Runtime and a headless activation host. This is a
cross-domain trust-boundary change spanning release authorization, frontend and
backend identity, SQLite migration, service ownership, and rollback. It is not
implemented or accepted by this draft.

## Current fact

Fedora is the owner-selected long-running Paper runtime. Its implemented
source-runtime updater follows only strict stable Release Tags under the
[`DEPLOYMENT.md`](../../operations/deployment/DEPLOYMENT.md) contract. A
user-level systemd timer runs a locked transaction, pins the Tag SHA, preflights
migrations against a restored backup, stages dependencies and frontend assets,
verifies provenance/schema/provider/scheduler/settlement/Paper/Live-off gates,
and durably records skip, rollback, or global-hold outcomes. The macOS
`aletheia-tray` is not a Linux service host.

That implementation still builds and activates a mutable source checkout on
the stable host and trusts Git Tag/repository controls rather than a signed
Linux artifact/feed. This draft addresses that remaining distribution and
activation boundary; it does not describe a prerequisite for the current
stable-Tag updater.

## Goals and non-goals

Goals:

- bind backend code and built `dashboard_static` to one exact release manifest;
- accept only an owner-approved, signed exact commit;
- preserve one service owner across stage, quiesce, migrate, activate, verify,
  and rollback;
- fail closed while leaving durable, operator-visible failure evidence;
- prove commit, schema, provider, scheduler, settlement, Paper mode, and Live-off
  state after activation.

Non-goals:

- enabling Live or changing Risk, provider, trading authority, or policy epochs;
- opening an Observation window automatically;
- treating a merge, download, or process start as a successful deployment;
- preserving source checkout or on-host frontend build as the final
  distribution model.

## Proposed design

1. A release workflow builds a `linux-x86_64` artifact only for an exact commit
   that passed existing CI and the owner release gate. Backend code and
   `dashboard_static` share one manifest and Ed25519-signed feed entry.
2. A headless Linux stable host runs under `systemd --user` and uniquely owns
   stop, select, migrate, start, and rollback. The host records the selected
   release and every activation result durably.
3. A timer performs signed-feed check and staging only. It verifies manifest,
   hash, signature, disk capacity, and a restorable database backup; then it
   preflights migrations on a copy and atomically writes `update-request.json`.
   Network, signature, capacity, or preflight failure leaves the current Runtime
   untouched.
4. The stable host quiesces new work, selects the staged immutable release,
   applies the preflighted migration to the authoritative database, starts the
   Runtime, and runs a bounded health soak.
5. Activation succeeds only when the observed commit, migration ledger,
   provider, scheduler, outcome settlement, `paper_internal`, and
   `live_disabled` facts all match the approved release contract. Otherwise the
   host restores the previous release and, where schema recovery requires it,
   the verified pre-update backup.
6. Timer execution uses a single-instance lock, `Persistent=true`, randomized
   delay, and bounded backoff. Rollout begins check-only, advances to canary
   staging, and permits automatic activation only after recovery drills pass.

## Trust and complexity review

The proposal creates a new release-signing authority, immutable artifact/feed,
and stable activation host. The current source updater already supplies a
single-instance lock, durable transaction state, backup-based schema recovery,
and post-activation identity and health gates. It does not supply a signed
artifact boundary: the stable host still fetches Git objects, resolves
dependencies, and builds frontend assets. The proposal is justified only if
removing that on-host build and Git trust surface is worth the extra signing and
artifact lifecycle.

Credentials remain write-only and outside artifacts, logs, manifests, and Git.
The stable host receives no authority to change Live, Risk, provider, or trading
policy.

## Verification contract

Before this design can become Operations or Product Truth, executable evidence
must prove:

- signature and exact-commit rejection paths;
- one manifest for backend and dashboard assets;
- single-owner exclusion across concurrent timer and activation attempts;
- backup restoration and migration-copy preflight;
- crash recovery before selection, during migration, during startup, and during
  health soak;
- exact commit and schema provenance after restart;
- provider, scheduler, settlement, `paper_internal`, and `live_disabled` health;
- unchanged Runtime on check, download, signature, capacity, and preflight
  failures;
- bounded rollback to the previous immutable release and required database
  backup.

A Fedora canary must complete the same recovery drills before automatic
activation is enabled.

## Rollout and rollback

Keep the stable-Tag source updater authoritative while the proposal is draft.
Introduce artifact production and check-only feed verification first, then
canary staging, then supervised activation. Immutable-artifact automatic
activation remains off until the verification contract passes on the
owner-selected Fedora runtime. Any failed gate returns selection to the prior
immutable release and restores the pre-update database backup when schema state
is not backward-compatible.

## Resolution

Draft. If accepted and implemented, move the final operating procedure to
`docs/operations/deployment/DEPLOYMENT.md`, the release and recovery invariants
to their owning Contracts or Decision, and mark this proposal `implemented`.
