# Retired observed-stocks control

Market Data Sources previously exposed `observed_stocks` alongside the dedicated
`observed_equities` editor. Scheduler and observer chose the canonical list when
nonempty and otherwise fell back to the legacy list. Clearing the canonical editor
could therefore reactivate an older workset.

The duplicate UI/API input and runtime field are retired under #3134. Historical
config decoding preserves the old effective list without writing a migration or
retaining a runtime alias. The dedicated equities endpoint uses the existing locked
configuration writer. Explicit empty saves remain empty after reload. Historical
observations and trading admission are unchanged; current behavior belongs to the
[configuration contract](../contracts/configuration_contract.md).
