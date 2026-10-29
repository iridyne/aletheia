# Trading Diagnostics — legacy Opportunity/Ranker funnel view

> **History (moved 2026-10-10).** Retired or superseded operations text kept as evidence; not current authority. See [design history](README.md).

Moved verbatim from [`TRADING_DIAGNOSTICS.md`](../../operations/runbooks/TRADING_DIAGNOSTICS.md)
when it was rewritten around the current Candidate → Entry Plan → Order → Close path. The
`opportunity audit` / `opportunity trace` commands and the hidden `funnel` / `sizing` / `alpha`
aliases still exist in code (`core/aletheia/cli/opportunity.py`, `core/aletheia/main.py`) and read
legacy Opportunity-era evidence only.

## Trading Diagnostics (as of 2026-10-10)

Use these read-only diagnostics to identify where the trading path loses flow,
alpha, or intended position size.

### Start here

```bash
uv run aletheia doctor --deep
```

Deep Doctor includes compact Alpha, trade-funnel, and sizing summaries. It is
the first diagnostic entry point, not an automated trading-policy controller.
`doctor --fix` remains limited to conservative infrastructure repair and does
not change trading, risk, sizing, leverage, persona, or prompt configuration
from these reports.

### Focused views

```bash
uv run aletheia opportunity audit --view funnel --hours 24
uv run aletheia opportunity audit --view sizing --hours 24
uv run aletheia opportunity audit --view alpha --hours 720
```

- **Funnel**: where flow stops between opportunities, decisions, proposals,
  risk, execution, and positions.
- **Sizing**: why intended or executed sizes are small.
- **Alpha**: which measured layer appears to add or destroy alpha.

Add `--json` for machine-readable output. Other views of the same command:
`overview` (default), `thesis`, `sizing-counterfactual`, `controller`,
`ranker-objective`, `entry-exit` (`_VIEWS` in `core/aletheia/cli/opportunity.py`).

### Opportunity lifecycle trace

`opportunity audit` / `opportunity trace` reconstruct, read-only, why a legacy
Opportunity did or did not become a trade. They never call an LLM, rerun ranker,
guard, debate or Risk, submit proposals or place orders; missing legacy evidence
stays `insufficient_data`.

```bash
uv run aletheia opportunity audit --hours 24
uv run aletheia opportunity audit --hours 24 --stage debate_downgraded
uv run aletheia opportunity audit --hours 24 --verdict missed_alpha_suspect
uv run aletheia opportunity audit --hours 24 --symbol TRX/USDT
uv run aletheia opportunity trace --id <id>
```

`--stage` / `--verdict` / `--symbol` apply to the `overview` view. The stage
vocabulary (`created`, `ttl_expired`, `signal_pending`, `operator_skip`,
`debate_downgraded`, `proposal_created`, `risk_rejected`, `order_executed`,
`position_opened`, `position_closed`, `shadow_resolved`,
`forward_outcome_resolved`, …) is defined by `_causal_stage_order` in
`core/aletheia/analytics/opportunity_flow.py`. `inspect opportunities` remains
the raw row listing.

Hidden compatibility aliases (scripts only; human output points back here):

| Old command | Current command |
|---|---|
| `aletheia funnel audit` | `aletheia opportunity audit --view funnel` |
| `aletheia sizing trace` | `aletheia opportunity audit --view sizing` |
| `aletheia alpha audit` | `aletheia opportunity audit --view alpha` |

### Evidence boundaries

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
