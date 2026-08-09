
# research/ - Evidence And Investigations

Research files are evidence, audits, and explorations. They can guide work but do not override contracts.

## Subfolders

| Folder | Use |
|---|---|
| `audits/` | Code, system, data-flow, platform, and lifecycle audits |
| `agents-learning/` | Agent behavior, OpenAlice/Serenity, learning-system research |
| `trading-strategy/` | Strategy, sizing, entry/exit, position research |
| `market-data/` | Market context, new data sources, xStocks support |
| `integrations/` | Demo/live parity and infrastructure integrations |
| `knowledge-bases/` | Imported or synthesized domain knowledge bases |
| `explorations/` | Smaller exploratory reports and postmortems |
| `tokenomics/` | LLM harness and cost-control research feeding the `tokenomics` branch |

## Current audit entry points

- [Minimum Profit Loop Convergence Evidence — 2026-08-08](audits/MINIMUM_PROFIT_LOOP_CONVERGENCE_2026-08-08.md)
  records the merged PR chain and a dated Fedora checkpoint for the narrowed
  scan-to-Learning loop. It is historical evidence, not current Runtime truth.
- [TickFlow first-batch selection — 2026-08-09](explorations/TICKFLOW_FIRST_BATCH_SELECTION_2026-08-09.md)
  records the seven selected implementation boundaries and their acceptance
  evidence. Git and pull requests, not the document, carry current progress.
- [TickFlow first-batch implementation evidence — 2026-08-09](explorations/TICKFLOW_FIRST_BATCH_IMPLEMENTATION_EVIDENCE_2026-08-09.md)
  records the resulting design decisions, focused validation, observed bundle
  effect, and remaining ceilings after implementing the selected boundaries.

## Agent Rule

Use research to find evidence and hypotheses. If a conclusion becomes authoritative, promote it into `design/`, `product/`, `governance/`, or `operations/`.
