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
- [June 2026 Operator specs](operator-specs/): merge postmortem and PI Operator dual-process design/audit/crosswalk.

Moved here on 2026-10-09 (each file carries a dated header note):

- [Blotter design](blotter_design.md): former `.bl-*` Blotter; code comments still cite its Performance backlog.
- [Usage insights design](usage_insights_design.md): superseded by the implemented Usage v2 proposal.
- [macOS Gateway watchdog](macos_gateway_autostart.md): retired LaunchAgent watchdog, kept to identify old registrations.
- [Runtime continuity SOP](runtime_continuity_sop.md): retired PI runtime-continuity mechanism (`retired_runtime_keys`).
