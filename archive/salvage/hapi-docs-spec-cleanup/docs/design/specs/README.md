# Design Specs (SDD)

A Spec is the approved, verifiable target design for one substantial change. It is the
document every implementing PR answers to until the change becomes current behavior.
This folder replaces `docs/design/proposals/` for new designs
([decision 2026-10-10](../../governance/decisions/DECISIONS.md)); existing Proposals stay
where they are and move here only when they are next revised.

The format follows public Spec-Driven Development practice, adapted to this repository's
document roles:

| Practice | Source | Here |
|---|---|---|
| Spec states WHAT/WHY; design states HOW | GitHub Spec Kit (`spec.md` / `plan.md`) | `spec.md` / `design.md` |
| Requirements in EARS sentences | Kiro, Mavin et al. (EARS) | every requirement uses one EARS pattern |
| Each requirement has WHEN/THEN scenarios | OpenSpec | scenarios are the acceptance criteria |
| Explicit `ADDED / MODIFIED / REMOVED` delta against current specs | OpenSpec | `Contract Delta` against `docs/design/contracts/` |
| Ambiguity is marked, never assumed | Spec Kit `[NEEDS CLARIFICATION]` | `approved` requires zero markers |
| Tasks derived from the plan | Spec Kit `tasks.md`, Kiro `tasks.md` | **not stored here** — GitHub Issues/PRs own execution ([governance](../../governance/policies/documentation_governance.md#stable-content-rule)) |
| Requirements trace to tests | Spec Kit traceability | requirement id = future `SpecClaim.claim_id` in `tests/specs/` |

## Layout

```text
docs/design/specs/
  README.md                 # this format definition
  _template/spec.md         # copy to start a spec
  _template/design.md
  NNNN-short-name/
    spec.md                 # WHAT and WHY: requirements, scenarios, contract delta, decisions
    design.md               # HOW: model, flow, failure semantics, alternatives, traceability
```

`NNNN` is the next free four-digit number. Research and audits stay in `docs/research/`
and are linked from `spec.md`, never copied.

## Lifecycle

`status` in `spec.md` front matter:

| State | Meaning |
|---|---|
| `draft` | Under discussion; may contain `[NEEDS CLARIFICATION: …]` |
| `approved` | Owner approved; zero clarification markers; implementing PRs answer to it |
| `implemented` | The Contract Delta has been applied to the named Contracts and every requirement has an executable claim; the spec is now frozen history |
| `superseded` | Replaced; `superseded_by` names the successor |
| `rejected` | Will not be pursued; kept as evidence |
| `on_hold` | Owner paused it; no implementing work until the hold is lifted |

State is a design decision, not progress. Owners, checklists and PR status stay in GitHub.
`approved` does not mean implemented; a Contract still describes only current behavior
until the implementing change applies the delta.

## Rules

1. **One spec per change, one owner per fact.** A spec names the Contracts it changes and
   the documents it supersedes; superseded documents get `status: superseded` in the same change.
2. **Requirement ids** are `<PREFIX>-R<NN>` (prefix fixed in front matter), unique, never reused.
   Each becomes the `claim_id` of at least one executable claim when implemented.
3. **EARS patterns** — every requirement sentence uses exactly one:
   - Ubiquitous: `The <system> shall <response>.`
   - Event: `When <trigger>, the <system> shall <response>.`
   - State: `While <state>, the <system> shall <response>.`
   - Unwanted: `If <condition>, then the <system> shall <response>.`
   - Optional: `Where <feature is enabled>, the <system> shall <response>.`
4. **Scenarios** use `WHEN … THEN …` and must be checkable by a deterministic test or a
   named runtime observation. A scenario that only a human can judge is not acceptance.
5. **Clarify before approval**: the Clarify table answers error path, empty state,
   boundaries, concurrency, idempotency, authority and observability for the change.
6. **Design traces back**: every design section cites the requirement ids it serves, and the
   traceability table maps each requirement to design, verification and evidence type.
7. **No progress state** in either file: no checkboxes, owners, dates of planned work or
   "current stage". Dated owner decisions go in the spec's Decision log.
8. Prose may be English or Chinese; requirement sentences, ids and scenarios are English
   so they can be copied verbatim into `SpecClaim.invariant`.

`scripts/ci/check_docs.py` enforces the layout, front matter, required headings,
requirement-id uniqueness and the zero-marker rule for `approved`.

## Specs

| Spec | Status | Summary |
|---|---|---|
| [0001-learning-loop](0001-learning-loop/spec.md) | approved | Decision-level learning from own mistakes: replay labels, process review, one playbook, advisory vs authority tiers, bundle on/off measurement |
| [0003-futures-only-convergence](0003-futures-only-convergence/spec.md) | draft | Remove code serving closed scopes: spot residue and defaults, spot fallback, out-of-scope observers, orphan data renderers, persona sizing, uncalled functions |
| [0004-market-environment-context](0004-market-environment-context/spec.md) | draft | Give the Entry Planner the market-wide facts already collected (regime, dominance, sentiment, breadth, cross-venue funding/OI) as advisory context, measured by randomisation |
