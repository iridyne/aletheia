---
id: model-provider-profiles-and-access-methods
status: accepted
related_contracts:
  - docs/design/contracts/agent_contracts.md
  - docs/design/api/api_reference.md
supersedes: []
---

# Model provider profiles and access methods

## Summary

Aletheia will replace the fixed model-provider cards with three explicit access
categories: API profiles, local CLI integrations, and OAuth connections. API
profiles are multi-instance. CLI and OAuth integrations are singleton per
supported implementation. All three remain routable through the existing
`ModelProvider` abstraction.

The change separates four facts that the current implementation conflates:

1. adapter type (`openai_compatible`, `anthropic_compatible`, and so on);
2. stable instance identity;
3. whether an instance is configured and enabled;
4. which routing roles reference the instance.

This is a cross-domain and credential-boundary change, so it is recorded as a
Proposal rather than as a UI-only refactor.

## Current fact

`ModelsConfig.providers` is keyed by fixed provider type names. The Settings UI
renders fixed cards and merges the OpenAI-compatible and Anthropic-compatible
slots into one API card. API-key secret references are also derived from those
fixed names. A second endpoint of the same type therefore cannot coexist with
the first.

The backend already stores `ProviderConfig.type`, and the runtime router can
resolve an adapter from that field. Existing OpenAI-compatible,
Anthropic-compatible, Databricks, Codex CLI, Claude Code CLI, OpenAI OAuth, and
Anthropic OAuth adapters can therefore be retained.

Fresh configuration already permits `default_provider=""` and an empty
provider map. Some diagnostics and UI controls still assume a configured
provider must become the enabled default; those assumptions are not part of the
target behavior.

## Goals and non-goals

### Goals

- Present API, CLI, and OAuth as the three top-level access categories.
- Allow any number of API profiles, including multiple profiles of the same
  protocol or adapter type.
- Keep CLI and OAuth integrations singleton per supported implementation.
- Support Pi Agent, Claude Code, and Codex through a capability-described CLI
  harness, subject to verified non-interactive invocation support.
- Keep configured, enabled, and routing-role state independent.
- Permit a fully clean state with no profiles, no enabled integrations, and no
  Default, Deep, or Fast assignment.
- Let the owner disable or delete an assigned provider after explicit
  confirmation.
- Preserve existing configurations without forcing credentials to be entered
  again.
- Keep credentials write-only and out of logs, audit messages, prompts, and GET
  responses.
- Extract a reusable Settings feature boundary so later sections can move out of
  `Settings.tsx` without inventing a second migration pattern.

### Non-goals for the first implementation

The following are intentionally deferred, not rejected:

- automatic cross-provider health failover;
- policy-driven profile selection based on cost, latency, or quota;
- multiple local installations of the same CLI integration;
- multiple OAuth accounts for one vendor;
- importing private credentials from another CLI without an explicit,
  provider-supported contract;
- allowing arbitrary shell commands as a CLI provider;
- changing prompts, trading decisions, Risk, Execution, permissions, or live
  unlock behavior.

These deferred directions remain recorded here until promoted into a separately
accepted Proposal. They must not be inferred as current support.

## Proposed design

### Identity and category model

Every routable provider instance has a stable ID. Display names are mutable and
never participate in secret addressing.

```text
provider_id   immutable stable identity
kind          api_profile | cli_integration | oauth_connection
adapter_type  registered ModelProvider adapter
display_name  operator-facing label
enabled       explicit operator-controlled activation
```

API profiles may use arbitrary validated IDs. Existing fixed keys remain valid
legacy IDs. CLI and OAuth IDs are supplied by registries and cannot be cloned.
Category is registry metadata rather than a user-controlled string.

### Configuration, enablement, and routing

Configuration does not imply enablement. Enablement does not imply a routing
assignment. A routing assignment does not override the enabled flag.

`default_provider`, `deep_provider`, and `fast_provider` may each be empty. Deep
and Fast retain their existing fallback to Default only when their own value is
empty. Resolving any assigned but disabled provider must fail explicitly; it
must not silently route to another provider.

Assigning a role does not silently enable the provider. The UI presents the
separate enable action and reports an assigned-but-disabled role as inactive.
This preserves owner control and avoids hidden model activation.

### Disable and delete authority

The owner may disable an assigned provider. New calls through that provider fail
with a stable disabled/not-configured error. Role assignments remain visible so
re-enabling restores the chosen routing without silently selecting another
provider.

The owner may delete an assigned provider after a confirmation that names all
affected roles. Deletion atomically:

1. clears every routing role referencing the provider;
2. removes its configuration;
3. invalidates router, model-directory, health, and inference-broker state;
4. prevents new calls from resolving the provider;
5. removes provider-owned secrets through the controlled secret API;
6. writes a redacted audit event.

Already-dispatched remote requests may complete; deletion guarantees that no
new request can resolve the deleted provider. The UI must state this bounded
semantics rather than claim that an external request was revoked.

### API profiles

An API profile owns endpoint, protocol, model, fallback-model, admission, and
credential configuration. Initial adapter types remain:

- OpenAI-compatible Chat Completions or Responses;
- Anthropic-compatible Messages;
- Databricks serving endpoint / gateway.

Create and update retain verify-before-save behavior. Cloning copies
non-secret configuration only. A new profile receives its own secret reference
based on immutable provider ID. Databricks origin-bound token behavior remains
mandatory.

### CLI harness

The CLI registry follows the capability-described adapter pattern demonstrated
by OpenAlice rather than a single generic shell command. Each adapter owns:

- executable discovery and version reporting;
- installed/authenticated/available health states;
- argument composition without shell interpolation;
- non-interactive request execution and cancellation;
- optional session resume/model discovery capabilities;
- environment allow/strip policy;
- output parsing and error redaction.

The central runtime owns timeout, subprocess lifecycle, normalized responses,
and audit-safe diagnostics. Existing Codex and Claude Code implementations are
adapted rather than replaced. Pi Agent is enabled only after its installed CLI
contract is verified. OpenAlice is an architectural reference; its code is not
copied unless licensing is explicitly compatible.

### OAuth connections

OAuth is singleton per vendor. A shared manager owns login-session state,
refresh serialization, cancellation, disconnect, and sanitized status. Existing
OpenAI and Anthropic provider-specific protocol code remains in small
connectors. Tokens remain in Aletheia secret storage and are never returned by a
read API.

Only provider-supported authorization flows may ship. Borrowed client IDs,
browser-token scraping, and undocumented credential extraction are forbidden.
Hermes Agent and OpenClaw are implementation references for registry and token
refresh structure, not authority to use an unsupported flow.

### Settings migration boundary

Model Providers moves to a feature module under the Settings domain. The shared
boundary supplied for later migrations includes:

- a typed Settings section descriptor;
- stable section IDs used by navigation/search/deep links;
- shared error and pending-status slots;
- a shared section shell and responsive layout primitives.

Other Settings sections may migrate to this boundary incrementally. This change
does not rewrite unrelated Settings controls.

### API surface

New profile and routing endpoints use stable provider IDs and explicit actions.
The exact endpoint index moves to the API reference when implemented.

`POST /settings/model` remains a compatibility adapter during migration. It
must call the same application service as the new endpoints rather than retain
separate mutation logic.

## Trust and complexity review

`aletheia/config/secrets.py` is change-controlled. Secure profile deletion needs
a deletion primitive for both encrypted-file and keyring storage, with tests
that prove secrets remain absent from GET responses, logs, audit events, and
agent context.

The smaller UI-only alternative is insufficient because fixed route
validation, fixed secret refs, cache identity, and provider-name routing would
still limit each adapter type to one instance.

No trading trust boundary changes. The model selected by the owner may change
agent output, as it already can through model settings, but the refactor does
not automatically select or fail over models.

## Verification contract

Executable evidence must cover:

- two enabled API profiles of the same adapter type with distinct credentials;
- legacy fixed-key configurations loading without credential re-entry;
- configured-but-disabled profiles and a fully empty clean state;
- assigned-but-disabled providers rejecting new calls without fallback;
- confirmed deletion clearing all role references and provider secrets;
- write-only key and OAuth-token responses;
- independent Default, Deep, and Fast assignments;
- honest CLI installed/authenticated/available states;
- OAuth refresh serialization, cancellation, and disconnect;
- Databricks origin-bound credential protection;
- Settings frontend tests and production build;
- browser acceptance at the supported Settings viewport.

## Rollout and rollback

### Compatibility phases

1. **Dual surface:** new endpoints and UI become canonical;
   `POST /settings/model` delegates to the new service and emits a deprecation
   response header plus audit-safe warning.
2. **Read-only compatibility review:** repository and packaged callers are
   inventoried; documentation and CLI move to the new endpoints. Removal is
   blocked while a supported caller remains.
3. **Removal proposal:** remove the compatibility route only in a later release
   with a documented breaking-change note and a migration command/check in
   `aletheia doctor` for legacy automation.

No date is invented in this Proposal. The removal condition is evidence that
supported callers have migrated, not elapsed time.

Rollback keeps the new config schema because multiple profiles cannot be
losslessly collapsed into fixed slots. The old frontend may be restored only
when the config contains at most one instance per legacy type. Otherwise the
operator must use the new API/CLI surface.

## Resolution

The owner accepted the three-category design with API multi-profile support,
CLI/OAuth singleton integrations, independent enablement and role assignment,
and owner-authorized disable/delete semantics. When implemented, current
behavior moves into the API reference, model-routing executable specification,
and the relevant Settings/product support surfaces. Deferred directions remain
in this Proposal until separately accepted.
