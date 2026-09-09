# SDD Shadow Workflow

## Status and authority

The SDD shadow workflow is D0 developer tooling. It adds no product behavior and
has Contract Delta `NONE`. Aletheia remains the Knowledge Plane: repository
governance, current Contracts, accepted Decisions, Proposals, executable
SpecClaims, GitHub Change Contracts, and runtime/economic evidence retain their
existing authority.

Spec Kit v1.0.4 is an optional, operator-installed Process Plane runtime. The
shadow result is ephemeral, advisory, and non-authoritative. A `GO`, `PASS`,
`completed`, `paused`, or `failed` run is not an Issue state, PR/merge approval,
product support claim, runtime-health claim, or economic qualification. The
workflow is never a required PR or merge gate and never edits GitHub.

This is not a comprehensive SDD migration. It does not introduce vanilla
`spec.md`/`plan.md`/`tasks.md`, OpenSpec, a second constitution, a canonical
`.specify` tree, a task/progress database, generated commands, or a replacement
for `tests/specs/`.

## When to use it

If the owner/task explicitly selects it, use pre-change for a D1/D2 Change Contract
or selected high-risk D0. Use convergence against the exact candidate head after implementation. Do
not spend this ceremony on an ordinary low-risk D0 typo or rename.

The launcher accepts a frozen Change Contract and an optional convergence base
ref plus review/runtime evidence files:

```sh
uv run python tools/sdd_shadow/launcher.py \
  --repo /path/to/clean/aletheia \
  --phase prechange \
  --change-contract /path/to/frozen-issue-body.md \
  --integration omp \
  --output /path/to/external/shadow-results
```

For convergence, add `--base-ref <ref>` and, when available, repeat
`--review-evidence <file>` and `--runtime-evidence <file>`. The default
integration is `omp`; unknown integrations are rejected. `--validate-only`
prepares the archive and provenance without invoking Spec Kit.

The source checkout must be clean, including untracked files. The launcher
refuses a dirty checkout rather than silently analyzing a different state. It
also refuses an output directory inside the source checkout.

## Isolation boundary

The launcher creates a disposable project and populates its `repo/` directory
from `git archive HEAD` (or the selected candidate ref). The archive has no
`.git` directory. Repository-owned workflows, prompts, schemas, and fixtures are
copied into the disposable project. Spec Kit run state is kept only under the
disposable project's `.specify/workflows/runs/`; Agent output is instructed to
remain under `.shadow/<run_id>/artifacts/`.

Only artifacts that pass deterministic JSON validation are copied to the
explicit external output directory. The manifest records source and merge
identities, frozen Change Contract SHA-256, changed files and diff identity
(for convergence), integration/version, phase/run/timestamp, clean-checkout
status, and the explicit non-authoritative statement.

This boundary is for accidental mutation, not hostile-process security. A local
coding Agent with unrestricted host permissions could technically inspect other
paths. The launcher does not claim a cryptographic, container, or hostile
filesystem sandbox. It never puts the canonical checkout path in an Agent prompt
or a workflow shell command, never runs model-generated shell, and never uses
Change Contract text or Agent output as shell input. Workflow shell commands are
fixed calls to the repository-owned validator; the only dynamic location value
is the engine-generated run identifier.

## Stages and evidence

The pre-change stages are authority resolution, clarification,
transaction/durability, concurrency/lifetime, entrypoint, failure semantics,
verification, and synthesis. Convergence independently checks Contract versus
implementation, authority alignment, executable SpecClaim/test coverage,
scope and entrypoints, failure/recovery semantics, supplied review evidence,
repository/runtime/economic qualification, and synthesis.

Every stage emits exactly one bounded JSON object. The deterministic validator
rejects missing, malformed, duplicate-key, oversized, non-finite, unknown-field,
wrong-stage, and extra-artifact cases. Free-form terminal prose is not a machine
contract. Findings identify evidence and rationale but do not change canonical
state.

`fixtures/retrospective-2856.json` is a small expressiveness fixture only. It
covers the historical obligation classes that motivated the lenses and marks
itself as hindsight-biased and non-authoritative. The independent `#2836`
prerequisite remains excluded from implementation scope; the fixture does not
turn it into a task or authority.

Repository proof, runtime/recovery proof, and economic qualification remain
separate. A passing shadow stage or test must not be promoted to a Product
Capability Ground Truth claim.

## Operation and rollback

Install Spec Kit outside the project environment when a real dispatch is wanted:

```sh
uv tool install specify-cli --from git+https://github.com/github/spec-kit.git@v1.0.4
```

Run `--validate-only` first, then a harmless frozen pre-change fixture. Do not
provide credentials, start services, mutate Fedora/Gateway/DB/trading state,
edit GitHub, or dispatch remote CI. Preserve the external result bundle as
review evidence only; GitHub remains the owner of progress, review, and
closure.

Rollback is a clean deletion of the shadow-tool files, focused test, and this
guide. Nothing in application behavior, current Contracts, GitHub state,
runtime state, or stored trading/economic evidence depends on the tool.

## Adoption boundary

Retrospective replay does not establish net value. Issue completion still
requires one prospective D1 Change Contract: freeze pre-change output before
implementation, run exact-head convergence afterward, compare obligations with
later independent review, classify surfaced/missed/implementation-only/false
positive findings, record ceremony/call cost and duplicate-state or false-block
impact, then record an owner decision: `promote narrowly`, `remain shadow`, or
`retire`. That prospective D1 value gate is separate from repository
implementation readiness.
