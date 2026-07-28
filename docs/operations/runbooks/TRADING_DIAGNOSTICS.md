# Trading Diagnostics

Use these read-only diagnostics to identify where the trading path loses flow,
alpha, or intended position size.

## Start here

```bash
uv run aletheia doctor --deep
```

Deep Doctor includes compact Alpha, trade-funnel, and sizing summaries. It is
the first diagnostic entry point, not an automated trading-policy controller.
`doctor --fix` remains limited to conservative infrastructure repair and does
not change trading, risk, sizing, leverage, persona, or prompt configuration
from these reports.

## Focused views

```bash
uv run aletheia opportunity audit --view funnel --hours 24
uv run aletheia opportunity audit --view sizing --hours 24
uv run aletheia opportunity audit --view alpha --hours 720
```

- **Funnel**: where flow stops between opportunities, decisions, proposals,
  risk, execution, and positions.
- **Sizing**: why intended or executed sizes are small.
- **Alpha**: which measured layer appears to add or destroy alpha.

Add `--json` for machine-readable output. The older `funnel audit` and
`sizing trace` commands remain compatibility aliases but are deprecated.
For the complete stage vocabulary, filters, and compatibility routing, see
[Opportunity Lifecycle CLI](OPPORTUNITY_LIFECYCLE_CLI.md).

## Evidence boundaries

These reports read local SQLite state and current configuration. They do not
submit orders, create proposals, mutate opportunities or decisions, change
positions, call an LLM or exchange, or trigger scheduler jobs.

Interpret results with these limits:

- historical funnel counts come from persisted lifecycle evidence; a missing
  record is reported as missing evidence rather than reconstructed certainty;
- sizing reconstruction may use current configuration when no historical
  configuration snapshot exists and marks that limitation in its trace;
- thin or unavailable evidence returns `insufficient_data`;
- a diagnostic warning is not an infrastructure blocker and must not
  automatically change trading behavior.
