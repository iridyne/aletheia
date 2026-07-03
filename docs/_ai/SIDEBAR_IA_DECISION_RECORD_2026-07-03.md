# Sidebar IA Decision Record - 2026-07-03

## Boundary

Touched only frontend navigation honesty and this decision record.

Did not add a Strategy page, did not change backend routes, did not change trading,
risk, execution gateway, credential, live-unlock, or data-model behavior.

## Finding

The main sidebar already matched the current IA:

- Overview: 1 item.
- Observe: 4 items, including Serenity Lens.
- Decide: 2 items.
- Execute: 1 item.
- Evolve: 1 item, Learning.
- Operate: 4 items.

That is 13 active navigation entries across the shell. There is no active Strategy
Lifecycle nav item and no `/strategies` route in `App.tsx`.

However, stale internal controls still pointed at removed or renamed UI paths:

- 5 `/strategies` actions across proposal detail/cards, opportunity detail,
  causal chain, and command palette.
- 1 old `/serenity` link from Prediction Lens to Serenity Lens.
- 1 Strategy Lifecycle quick action in Command Center.

## Decision

Do not recreate `/strategies`. Strategy IDs remain honest recorded facts, but are
not clickable unless a real implemented destination exists. Serenity Lens remains
an Observe surface because it is supply-chain research/discovery input, not a
learning/evolution loop.

## Changes

- Replaced strategy-id links in Proposal and Agent Activity drawers with plain
  monospace IDs.
- Removed the proposal-card "Strategy" action when only `strategy_id` exists.
- Removed the CausalChain Strategy node's `open` action.
- Removed the Command Palette "Go to Strategies" command.
- Changed Prediction Lens cross-link from `/serenity` to `/serenity-lens`.
- Replaced Command Center's "Strategy Lifecycle" quick action with "Learning Review".
- Added `tests/test_frontend_navigation_ia.py`.

## Quantified Result

- Dead `/strategies` frontend actions: 5 -> 0.
- Old `/serenity` frontend links: 1 -> 0.
- Stale Strategy Lifecycle quick actions: 1 -> 0.
- Strategy entries in active sidebar NAV: 0.
- Serenity entries in active sidebar NAV: 1, under Observe.
- Evolve sidebar entries: 1, Learning.

## Verification

- `uv run pytest tests/test_frontend_navigation_ia.py -q` -> 2 passed.

