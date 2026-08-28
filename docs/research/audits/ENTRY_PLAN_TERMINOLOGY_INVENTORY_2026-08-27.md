# #2288 historical-term inventory

This inventory is the closure record for the Implementation Review Amendment.
The active Universe opening surface is **Candidate → Entry Plan → Execution →
Order → Fill → Position**; Trigger appears only inside `TRIGGERED` plans.

The snapshot below counts files, not string occurrences, and excludes archived
documents and generated Settings types:

```bash
for term in UniverseMember UniverseSetup ConditionalEntryAuthorization EntryEligibility TradingDecision TradeProposal signal_pending setup_id Opportunity; do
  rg -l "$term" aletheia frontend/src docs \
    --glob '!docs/archive/**' --glob '!frontend/src/api/generated/**' \
    --glob '!docs/research/audits/ENTRY_PLAN_TERMINOLOGY_INVENTORY_2026-08-27.md' | wc -l
done
```

| Historical term | Files | Retained boundary and concrete reason |
|---|---:|---|
| `UniverseMember` | 9 | Physical `universe_members` storage/model/repository and seed/audit code cannot be destructively renamed without a table/model migration. New service/UI code calls the row `Candidate`; the frontend alias is deprecated wire compatibility only. |
| `UniverseSetup` | 6 | Frozen model/repository plus `universe/fast_trigger.py`, `universe/signal_emitter.py`, and the guarded watcher adapter read historical Setup generations. `UniverseService` no longer creates them, and the adapter skips any exact instrument/direction owned by a current Entry Plan. The superseded proposal is retained as audit history. |
| `ConditionalEntryAuthorization` | 6 | Same historical Setup adapter boundary. The model/table and old authorizer facts remain readable for recovery; no Candidate Planning Generation or Entry Planner path writes this authority. The agent contract labels the section superseded. |
| `EntryEligibility` | 4 | Historical model/emitter prose and compatibility documentation only. The active contract uses registered TriggerSpec plus exact-state validation and does not expose EntryEligibility as an object. |
| `TradingDecision` | 10 | Existing independent-producer and portfolio/legacy recovery contract plus research/diagram history. Renaming that separate causal/audit chain would be a cross-contract migration unrelated to Universe entry and risks breaking retained outcomes. Universe-linked planning imports neither the type nor its producer; it uses `agent_shell.entry_planner.EntryPlanDraft`. |
| `TradeProposal` | 46 | Physical immutable Risk/Gateway carrier and long-lived API/audit schema. The execution contract names it owner-facing `Order Intent`; changing the class/table/commit-hash chain would be a disproportionate money/audit migration. Entry Plan binds it one-to-one and it never becomes a second planning owner. |
| `signal_pending` | 59 | Persisted `pi_decisions.action` and queue/recovery protocol for independent producers. Candidate planning uses its own generation claim and PI-daemon wake, never writes or consumes `signal_pending`. Renaming historical actions would invalidate audit identities and many independent lifecycle claims. |
| `setup_id` | 12 | Frozen SQLite columns, migration history, historical Setup adapter, and backward-compatible API/audit projections. New Entry Plan/Order Intent code uses `entry_plan_id`; no new Universe opening authority depends on `setup_id`. |
| `Opportunity` | 132 | Independent Market Observer, Serenity, analytics, Learning and retained outcome contracts still use this distinct analysis carrier. A repository-wide rename would expand #2288 into multiple research/audit wire migrations. Universe-linked executable production is removed: new Candidate publication creates only a Planning Generation, and its sole opening owner is Entry Plan. Universe UI no longer displays Setup or Triggered Opportunity as Candidate state. |

The large retained counts are therefore not aliases for the new opening model.
They are either immutable storage/wire history, an explicit no-parallel-owner
adapter, or independent research/analysis contracts whose rename would expand
authority outside #2288. Any new Universe-linked opening code introducing these
terms outside those boundaries is a contract regression.
