# <Capability> Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `XX` · **Owner code:** the one module that implements it

A Contract is the *current-behavior* half of the SDD format. It uses the same
requirement form as a [spec](../specs/README.md) so a spec's Contract Delta can
land on numbered requirements and a SpecClaim can cite one by id.

## Scope

Two to five sentences: what this capability does on the trading mainline
(Universe → Candidate → Entry Plan → Risk → Gateway → Position → verified close →
Outcome → review), and what it explicitly does not own.

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `module.py::function` | scheduler job / API route / CLI / call | the one production caller |

Use `file::symbol`, never `file:line` (line numbers rot).

## Durable state

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| `table_name` | `module::function` | consumers | append-only / monotonic status / replaceable cache |

## Requirements

Each requirement is one EARS sentence (`The … shall …` / `When … , the … shall …` /
`While … , the … shall …` / `If … , then the … shall …` / `Where … , the … shall …`),
followed by checkable scenarios. Ids are `XX-C<NN>`, unique and never reused; a
retired requirement keeps its id with `RETIRED — <date>, see <history link>`.

### XX-C01 Requirement title

`When <trigger>, the <system> shall <response>.`

- WHEN … THEN …

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| input missing / stale | fail closed / retry / degrade | reason token, audit event, doctor probe | `unavailable`, never `0` |

## Authority

One line: what this capability may change and what it may never change (Risk limits,
credentials, Live unlock, Gateway submission).

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| XX-C01 | `tests/specs/spec_<name>.py::XX-C01` |

## Decision references

Issue/PR numbers and dated owner decisions that shaped this contract go here, not in
the requirement text.
