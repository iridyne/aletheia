# Archived local simulated position observation

Before retirement, Position Path Observer recovered open and closed simulated
positions alongside external accounts, including exhausted retry rows. Instrument
identity came first from `position_protection_state`, then from Opportunity /
Universe membership. Historical buckets could project protection prices from
`position_exit_plan_versions`; current buckets copied the Position's plan version.

Those production paths are removed. New observations and recovery metrics use only
registered external accounts, with venue derived from execution mode. Simulated
positions, prior observations, protection plans, deferred queues and hashes remain
unchanged and readable as history. Current lifecycle projection still uses actual
entry/reduction/close facts; it does not recreate protection or settlement history.

## Reconciliation

The retired reconciler classified simulated positions as managed with a
`local_only` status. Administrative-close/reopen detection also scanned simulated
history. Both paths are removed: active classification and incident detection use
registered external accounts. Retired account configuration is unconfigured for
reconciliation, while existing positions and incident records remain unchanged.

## Operator consumption

The former Operator observe-only recommendation selected risk rejections with a
`paper` execution-mode prefix. Current recommendations use the three registered
external Demo/Testnet accounts. This recommendation remains advice; it neither
changes Risk policy nor grants Live execution authority.

Portfolio review formerly read every open Position, including archived simulator
rows. Its current model input and fresh mutation-target lookup both select only
UTA account modes. An archived Position cannot become a review target through a
model response or a stale wake. Stored simulator rows remain readable and unchanged.

The former `test_pipeline_position_to_close.py` required four synchronous local
stop-loss, take-profit and max-hold closes. Those simulator requirements are
retired. External stop/target execution is covered by the exchange pipeline;
max-hold is covered through private-fill reconciliation in `test_position_manager.py`.
The signal-to-Operator review scenario remains, using an exact Bitget Demo
instrument and no simulated execution.

Position Manager specs formerly demanded same-cycle simulated closure and allowed
the local mode to take prices from the configured market-data venue. PL-PM now
uses explicit external identities, pending exchange submission and private-fill
confirmation. Bitget, OKX and Binance marks remain isolated even when the global
market-data venue differs. The deleted local protection-worker hooks and ATR
venue override are no longer test requirements; current manager error logging,
paused protection and pending-close convergence remain covered.
