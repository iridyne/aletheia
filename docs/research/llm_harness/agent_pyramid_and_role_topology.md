# Agent Pyramid and Role Topology

## Architectural intent

Aletheia has an implicit agent pyramid. The base contains specialised workers
that acquire, analyse, review, or prepare artifacts. Higher layers coordinate,
compare, schedule, and operate the system. The top-level System Operator is a
framework operating role, not a privileged model that can bypass trading
controls.

The former `PIAgent` name describes historical implementation and compatibility
surfaces. It should not force an architectural choice to embed a monolithic
PIAgent component inside the framework. Future design should converge on the
role semantics below while migrating names only when a compatibility review
permits it.

## Research synthesis

Multi-agent literature describes centralised, decentralised, hierarchical, and
adaptive coordination. Hierarchical manager-worker arrangements help task
decomposition and attribution, but can increase debugging depth and create
manager bottlenecks. The evidence does not establish a universally superior
shape. The relevant conclusion is narrower: roles, handoff artifacts, and
verification responsibility must be explicit. See the [multi-agent systems
survey](https://arxiv.org/html/2601.01743v1) and the runtime-governance framing
in [UNU's harness framework](https://unu.edu/publication/engineering-and-governing-agent-harness-technology-and-policy-framework-runtime-layer).

## Proposed roles

| Layer | Role | Responsibility | Must not do |
| --- | --- | --- | --- |
| Base | Worker | Observe, research, analyse, draft a bounded artifact | grant itself tools or execute orders |
| Review | Evaluator / critic | Check artifacts, schemas, evidence, conflicts | silently replace the source of truth |
| Coordination | Coordinator | Schedule bounded work and preserve causal links | collapse independent approvals |
| Operations | System Operator | Diagnose system state, propose/execute approved operations | bypass Risk, Execution Gateway, live gate, or credentials |

The same concrete module may temporarily perform more than one role; the
important property is explicit attribution, not an immediate rewrite into new
classes.

## Required handoff artifact

Every cross-role handoff should be representable without raw prompt replay:

```text
HandoffArtifact
  producer: logical agent + run/task ID
  consumer: logical agent/role
  inputs: artifact IDs and freshness/provenance summary
  output: typed/validated result or sanitized failure
  authority: runtime permission class used
  evaluation: validator/reviewer result where applicable
```

For trading, the existing proposal → risk decision → order/fill causal chain
remains the authority. The pyramid may create analysis and proposals, but it
does not replace risk approval.

## Implementation sequence

1. Publish a stable role taxonomy and map current `logical_agent` values to it.
2. Extend token telemetry with the role and producer/consumer handoff fields
   where those facts already exist.
3. Add typed handoff envelopes first for non-trading worker/evaluator paths.
4. Measure whether hierarchy actually improves recovery, review quality, or
   token efficiency before adding an extra coordinator layer.

## Evaluation questions

- Can an operator trace a decision back through each worker artifact?
- Are role transitions attributable and permission-checked?
- Does an evaluator reduce invalid or ungrounded output versus its token cost?
- Does adding a coordinator remove work duplication, or merely add latency?
