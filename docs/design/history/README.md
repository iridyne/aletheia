# Design History

This directory preserves retired or superseded design material. It is evidence
for provenance, not current implementation authority.

Do not update these files as live contracts or work trackers. A mechanism that
returns requires a new qualifying Proposal and current Contract rather than
reactivating a historical document in place.

- [Former three-category model providers](model_provider_profiles_and_access_methods.md):
  superseded CLI/OAuth/API support design; current endpoint convergence uses
  the accepted HTTP-only boundary.

- [Former persistence Gateway recovery](gateway_recovery.md): superseded PS
  dual-write/recovery design; current startup uses canonical Core order/close
  evidence, not historical PS lifecycle state.

- [Retired ArtifactStore](artifact_store.md): consumer-less filesystem artifact
  store whose "immutable manifest" claim never had a production producer.

- [Local simulated account controls](local_paper_account_controls.md): retired Settings/API surfaces, preserved only as history.
- [Deterministic control-loop simulator](deterministic_control_loop_simulation.md): retired Python-Operator harness and its simulated-account tests.

- [Local simulated protection and exit experiments](local_simulated_protection.md) — archived runtime and evidence definitions.

- [Local simulated economic viability](local_simulated_viability.md): retired report generation; persisted reports remain readable.

- [Local simulated contribution and qualification](local_simulated_qualification.md): retired Risk/Execution projections and frozen whole-policy compiler.

- [Local simulated Risk and execution contracts](local_simulated_risk_execution.md): retired admission, matching, cash and protection definitions.

- [Local simulated mandate provenance](local_simulated_mandate.md): retired automatic attribution producer; historical identities remain readable.

- [Local simulated runtime scope](local_simulated_runtime_scope.md): retired startup and update exemptions, replaced by explicit external-account scope.

- [Local simulated position observation](local_simulated_position_observation.md): retired simulated path recovery and protection-derived observation identity.

- [Universe Twin shadow experiment](universe_twin_shadow_contract.md) and [relative-rank shadow](universe_relative_rank_shadow_contract.md): prospective ranking experiments whose producers were removed on 2026-08-11 (#900); the Twin tables are now written only by Candidate Selection Utility.
- [Python System Operator design decisions](PI_OPERATOR_DESIGN_DECISIONS.md) and [operator layer concept](operator_layer.md): the retired Python Operator loop; the current Operator is [operator-shell](../architecture/OPERATOR_SHELL.md).
- [System architecture guide (2026-07)](SYSTEM_ARCHITECTURE_GUIDE.md): whole-system narrative that still describes retired Ranker/Observer/Python-Operator stages; current navigation is [SYSTEM_MAP](../architecture/SYSTEM_MAP.md).

- [Stability target architecture](STABILITY_TARGET_ARCHITECTURE.md): the 2026-07 self-healing runtime roadmap; current probes live in the RUNBOOK.
- [Retired entry-decision lifecycle](decision_lifecycle_retired_entry.md): TradingDecision, PiDecision, `signal_pending` and admission-slot semantics of the retired Ranker/`paper_trader` entry path.

- [Former Paper Internal development unblock runbook](paper-trading-dev-unblock.md): historical commands and repair guidance; not a current operating procedure.

- [Previous local-exchange capability review](local_simulated_capability_review.md): original capability labels and evidence, superseded by the external-account scope.

- [Former product scope](local_simulated_product_scope.md): superseded simulator mainline, support claims and duplicated Learning gates.

- [Local simulated Alpha audit](local_simulated_alpha_audit.md): former report outputs and contribution definitions.

- [Former product gates and metrics](local_simulated_product_gates.md): retired simulator accounting prerequisites and legacy Learning promotion definitions.

- [Local simulator replay pack](local_simulated_replay.md): retired generator and qualification claims; frozen artifacts remain readable.

- [Former execution and architecture diagrams](diagrams/README.md): original local-simulator and Operator-entry diagrams, including their rendered assets.

- [Original v1 implementation specification](implementation_guide_v1.md): archived local-simulator tools, API examples, project layout and milestones.

- [June 2026 execution-path analysis](execution_path_honesty_202606.md): historical Paper Trader, RuleSet and local-simulator findings.

- [June 2026 framework module map](framework_module_map_202606.md): retired simulator-centered module inventory and promotion loop.

- [Former Universe Fast Trigger Watch proposal](universe_fast_trigger_watch.md): retired Setup adapter, local execution and acceptance requirements.

- [Former OpenWiki architecture snapshot](openwiki/README.md): original simulator, account, entry-loop, Learning and configuration pages, retained as a linked historical set.

- [Former CN Equity Internal Paper proposal](cn_equity_internal_paper.md): retired CNY simulator/account and execution qualification design.

- [Former packaged Runtime operations](packaged_runtime.md): removed signed-bundle/tray install lane (`native/`, `packaging/`, `scripts/release/`, packaged update path).

- [Former packaged Runtime dogfood playbook](packaged_runtime_dogfood_playbook.md): removed one-Mac dogfood and signed-Feed promotion procedure.

- [Former packaged Runtime power optimization](packaged_runtime_power_optimization.md): macOS tray power analysis for the removed native host.

- [Former modular-monolith release bundle](modular_monolith_release_bundle.md): removed immutable release-bundle build/signing/rollback contract.

- [Archived platform-specific Gateway service backends](platform_specific_gateway_services.md): retained-but-unmaintained macOS/Windows service modules, with the boundary that keeps the Linux systemd service live.

- [Retired native Bitget trading implementation](native_bitget_trading_client.md): native client restricted to private reads under #3134.
- [Retired OKX and Binance private clients](legacy_okx_binance_private_clients.md): removed concrete private clients; CCXT adapters are the boundary.
- [Retired observed-stocks control](observed_stocks_settings_control.md) and [tokenized-equity config alias](observed_xstocks_settings_control.md): removed duplicate settings inputs.
- [Archived SHA-bound UTA Qualification](uta_qualification.md): retired by #3035.
- [Former Promotion Gates contract](promotion_gates_contract.md): retired strategy-promotion mechanism.
- [June 2026 Operator specs](operator-specs): merge postmortem and PI Operator dual-process design/audit/crosswalk.

Moved here on 2026-10-09 (each file carries a dated header note):

- [Blotter design](blotter_design.md): former `.bl-*` Blotter; code comments still cite its Performance backlog.
- [Usage insights design](usage_insights_design.md): superseded by the implemented Usage v2 proposal.
- [macOS Gateway watchdog](macos_gateway_autostart.md): retired LaunchAgent watchdog, kept to identify old registrations.
- [Runtime continuity SOP](runtime_continuity_sop.md): retired PI runtime-continuity mechanism (`retired_runtime_keys`).
- [Retired knowledge consumers](knowledge_retired_entry_consumers.md): former Ranker weighting and Operator entry-prompt injection (knowledge_contract §8.1/§8.2).
- [Retired shadow_trades write path](shadow_trades_write_path.md): former shadow writer/resolver entry points and CF-I02/I04/I05/I06 (#3704).
- [Retired Ranker / PaperTrader filter-evidence rules](ranker_paper_trader_filter_evidence.md): former batch selection, Stage E record and completion criteria (filter_evidence_contract §7/§9/§11).

## Text moved out of current documents on 2026-10-10

Verbatim passages removed when the current Contracts and operations docs were rewritten
into the golden-standard form; see the [review evidence](../../research/audits/doc-golden-standard-2026-10-10/README.md).

- [Agent Contracts — retired, duplicated and summarized sections](agent_contracts_retired_sections.md)
- [Audit Chain Contract — retired and historical sections](audit_chain_retired_sections.md)
- [Candidate Selection Utility — superseded contract text](candidate_selection_utility_superseded_passages.md)
- [History snapshot: `data_collector_health.md` before the 2026-10-10 rewrite](data_collector_health.md)
- [History snapshot: `data_layer.md` before the 2026-10-10 rewrite](data_layer_2026-10-10.md)
- [History snapshot: `data_model.md` before the 2026-10-10 rewrite](data_model_reference_2026-10-10.md)
- [Decision Lifecycle — superseded contract text](decision_lifecycle_superseded_passages.md)
- [Deployment — retired and superseded sections](deployment_retired_sections.md)
- [Derivatives Economic Semantics: convergence narrative](derivatives_semantics_convergence_notes.md)
- [Entry Plan Contract: retired and superseded passages](entry_plan_contract_superseded_passages.md)
- [Entry Plan DRAFT: ATR-relative stop-floor policy](entry_plan_stop_floor_draft.md)
- [Execution contract: retired narrative](execution_contract_retired_narrative.md)
- [Filter Evidence — retired entry-chain text](filter_evidence_retired_chain.md)
- [jj workspace playbook — retired sections](jj_workspace_playbook_retired_sections.md)
- [LLM Inference Harness Contract — retired and superseded text](llm_inference_harness_retired_sections.md)
- [History snapshot: `market_data_observability.md` before the 2026-10-10 rewrite](market_data_observability.md)
- [Observation Experiment Ownership V2 Migration Contract (pre-2026-10-10 text)](observation_experiment_ownership_v2_migration.md)
- [Observation Lab Joint Backup / Restore Contract](observation_lab_backup_contract.md)
- [Observation Lab v1 Contract (pre-2026-10-10 text)](observation_lab_retired_text.md)
- [Observation Lab v1 — Current Design](observation_lab_v1_design.md)
- [Persistence Contract — retired and historical sections](persistence_recovery_retired_sections.md)
- [Profit-loop, Profit-gradient and trade-rationale contract text (moved)](profit_loop_and_rationale_retired_text.md)
- [History snapshot: `RETENTION_POLICY.md` before the 2026-10-10 rewrite](retention_policy_2026-10-10.md)
- [Risk Engine, Kill Conditions and Live Unlock — retired contract narrative](risk_kill_live_contract_narrative.md)
- [Scheduler Contract — retired, target and duplicated sections](scheduler_contract_retired_sections.md)
- [Serenity Lens Contract — retired and historical sections](serenity_contract_retired_sections.md)
- [Settings switch inventory (retired copy)](settings_control_switch_inventory.md)
- [Technical Analysis Contract: retired and unwired passages](technical_analysis_retired_sections.md)
- [Trading Diagnostics — legacy Opportunity/Ranker funnel view](trading_diagnostics_legacy_funnel.md)
- [Retired `.bl-*` Blotter primitives](ui_design_system_retired_bl_classes.md)
