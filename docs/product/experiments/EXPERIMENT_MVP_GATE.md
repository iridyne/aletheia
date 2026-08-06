# Historical Round 1 MVP Gate

> **Status:** retired historical compatibility pointer.

The June 2026 Round 1 readiness gate and `scripts/mvp_gate_check.py` described an
`okx_demo` experiment, required a legacy tag/freeze marker, and predated the
current `paper_internal` Active Runtime Scope and measured Learning contracts.
Round 1 was terminated on 2026-06-27. Those checks do not authorize or qualify a
current Runtime.

Current authorities are:

- [Active Runtime Scope](../ACTIVE_RUNTIME_SCOPE.md) for the supported trading mainline;
- [Current Admission And Promotion Gates](../gates/PHASE_GATES.md) for admission;
- [Experiment Protocol](EXPERIMENT_PROTOCOL.md) for observation-window governance;
- [Learning Contract](../../design/contracts/learning_contract.md) for current behavior;
- `uv run aletheia doctor`, provenance, logs, and current data for runtime truth.

The legacy script remains only to reproduce the historical Round 1 checklist. Do
not use its `okx_demo`, `ACTIVE`, `EXPERIMENT FREEZE`, or tag checks as a current
gate. Historical detail remains available in Git history and dated research/archive
evidence.
