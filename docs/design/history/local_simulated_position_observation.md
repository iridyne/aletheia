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
