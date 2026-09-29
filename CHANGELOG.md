# Changelog

All notable product changes are recorded here. Aletheia uses Semantic Versioning
for the product release, while build identities and data/behavior contracts keep
their own independent versions.

## Unreleased

### Changed

* **market-data:** stop stamping FastMarketStream quote/trade/depth/candle facts with the local receive clock when CCXT Pro omits a source timestamp; a missing/malformed source timestamp now drops the fact instead of inventing exchange time (#3699).
* **security:** reject private Frontdoor diagnostics and WebSocket admission when the owner-auth configuration is unavailable; public minimal probes and known local/remote policy remain unchanged (#1332).
* **market-data:** persist and count only confirmed closed candles in historical downloads, while retaining raw-page pagination so forming tails can be fetched after closing (#3676).
* **economics:** summarize the complete context-artifact window from one streaming SQLite snapshot instead of silently limiting totals to 5,000 detail rows; preserve malformed-value handling and detail pagination (#3677).
* **security:** recursively redact Webhook signing keys and nested credentials in shared backup/sync config exports, preserving public token limits, auth switches, and observed stock symbols (#1379).
* **economics:** evaluate configured hard budgets independently of shadow/tracing switches before inference, without changing safety exemptions or unknown-cost policy; reused requests cannot carry stale denials (#1624).
* **economics:** recover missing settings, tool-profile and context-artifact tables through the shared idempotent initializer, preserving caller transactions and the retired viability reader boundary (#1725).
* **cli:** return exit 1 after download/backfill errors while retaining partial counts, every batch result and successful siblings; valid empty downloads and fully successful batches remain exit 0 (#1395).
* **runtime:** sample health shortly after Gateway startup so release activation and rollback can verify real readiness before their bounded deadline, while retaining the normal ten-minute cadence and fail-closed checks.
* **market-data:** restore Bitget Candidate funding enrichment from the existing perpetual ticker and its exchange timestamp, without changing freshness policy or historical outcomes.
* **entry:** make Candidate/PlanningGeneration/EntryPlan the sole new-entry authority, including source-scoped Serenity publication and separate timing/holding horizons; retain historical decisions and unresolved execution evidence.
* **uta:** route all six external account modes through UTA and the execution coordinator, retire the legacy/shadow selector, and bind private order recovery to durable exact account identity; unproven historical mappings block new entry without rewriting old orders.
* **learning:** make Observation Lab the sole online experiment authority; retire legacy evaluation/lifecycle writers while preserving proof-gated imports and historical evidence. Missing canonical qualifications remain unavailable.
* **deployment:** route Linux manual updates through the existing auto release transaction; remove experimental runtime controls and require verified backup plus standard-directory migration before retiring registry tables.
* **maintenance:** remove obsolete internal/test-double fallbacks and the unused cross-tab listener; preserve configured log retention and read-only scheduler failures, and replace implementation-bound UI checks with rendered behavior coverage.
* **entry:** persist Discovery closed candles for exact-instrument planning; establish missing legacy candle provenance only on a real refetch, preserving existing timestamps on unchanged observations.
* **candidate:** persist each runtime selection evidence bundle and its outcome labels atomically with publication so subsequent economic estimation can load the produced evidence.
* **maintenance:** retire the superseded stock observer manual entry; remove unused UI, shell, host and CI layers, use standard-library JSON/file hashing, and drop the unused multipart dependency. Historical equity evidence and legacy equity configuration remain readable.
* **candidate:** use measured-first Utility selection with explicit signal-based Startup for genuine sample scarcity in Demo; retire Legacy selection/admission controls, preserve historical evidence, acquire exact discovery books, and expose selection/business-progress diagnostics.
* **entry:** recover bounded planning interruptions without inventing economic refusal, and show exact order/fill/position, cash-settlement, and separate four-hour outcome evidence across planning cycles.
* **paper:** settle venue-reduced approved entry quantities and cancelled partial-fill remainders against actual fills while retaining Risk identity and position/cash reconciliation checks.
* **entry:** split Entry Planner PlanIntent production from deterministic EntryPlan compilation, with portable strict structured outputs and operator-configurable deep-tier fallback (including DeepSeek V4 Flash).
* **entry:** fail closed on unmonitorable exact venues, restore Resting Maker, Triggered Maker, and Triggered Taker, cancel new maker Orders after 60 seconds from durable Order creation, and align planner admission with the configured deep provider ([#2546](https://github.com/iridyne/aletheia/issues/2546)).
* **entry:** canonicalize non-authoritative execution fields after a model selects `NO_PLAN`, preserving the decision across strict-schema model defaults ([#2557](https://github.com/iridyne/aletheia/issues/2557)).
* **risk:** enforce Entry Plan stop provenance and directional worst-price bounds at the canonical Plan-to-Proposal authority seam ([#2332](https://github.com/iridyne/aletheia/issues/2332)).

## [0.8.6](https://github.com/iridyne/aletheia/compare/v0.8.5...v0.8.6) (2026-09-09)

### Changed

* **deployment:** admit explicitly classified Paper-role Bitget Demo runtimes through the shared release transaction, requiring the exact `bitget_demo` / `external_exchange` pair and `live_disabled`; preserve all health, settlement, backup and continuity checks.

## [0.8.5](https://github.com/iridyne/aletheia/compare/v0.8.4...v0.8.5) (2026-09-09)


### Features

* add paired Momentum geometry replay ([#2600](https://github.com/iridyne/aletheia/issues/2600)) ([a48c4f4](https://github.com/iridyne/aletheia/commit/a48c4f42e2d97882278b0e18bdd6ef7e3421bfc4))
* **candidate:** integrate measured-first startup selection and entry recovery ([80a7de2](https://github.com/iridyne/aletheia/commit/80a7de2770f5f5df8fbe1d1d2bf9cc552a856895))
* **candidate:** measured-first startup selection and canonical entry recovery ([#2893](https://github.com/iridyne/aletheia/issues/2893)) ([e57e6a5](https://github.com/iridyne/aletheia/commit/e57e6a54c895f461e1be8b5cfae103625a1f1956))
* **charts:** add quick instrument watch pills, instant MA/BOLL/VOL toggles and blotter links ([1a0d342](https://github.com/iridyne/aletheia/commit/1a0d342b8ee78abeaaa881c45accaa976c769606))
* **charts:** align indicator workbench and calculus with R20, add candle countdown and risk simulation console ([9617621](https://github.com/iridyne/aletheia/commit/9617621840e83322e58eac111fccb5266011075c))
* **cmd:** expose dump incident snapshot command in CommandPalette ([57a7879](https://github.com/iridyne/aletheia/commit/57a7879614da60c0c7b976615ef26a4c72272483))
* **contract:** bind Analytics, Settings and Universe routes to typed OpenAPI response models and modularize page styles ([1227521](https://github.com/iridyne/aletheia/commit/1227521974b78df1817696d59be314fd0852a1be))
* **contract:** bind Risk and Universe endpoints to Pydantic response models and generated OpenAPI schemas ([1bf2d55](https://github.com/iridyne/aletheia/commit/1bf2d55812144dc1fee8e2b982dfee1e9e26ba6d))
* **contract:** bind UI bootstrap and Analytics endpoints to Pydantic models and generated OpenAPI schemas ([3c1d3ad](https://github.com/iridyne/aletheia/commit/3c1d3ad4013e8222734404b61c7616bd98f1589d))
* **dev:** add advisory SDD shadow workflows ([#2873](https://github.com/iridyne/aletheia/issues/2873)) ([bbcf53a](https://github.com/iridyne/aletheia/commit/bbcf53a7ddf12f81f85744323aff97177a0ecb8e))
* enable controlled momentum paper treatment ([#2607](https://github.com/iridyne/aletheia/issues/2607)) ([#2608](https://github.com/iridyne/aletheia/issues/2608)) ([c76d4ae](https://github.com/iridyne/aletheia/commit/c76d4ae16a48b5be65f246743dcac6a93f1c90bd))
* **entry-planner:** plan candidates concurrently ([#2411](https://github.com/iridyne/aletheia/issues/2411)) ([4fd6d85](https://github.com/iridyne/aletheia/commit/4fd6d85ae93cdfdc765f995fd14a046a557333cb))
* **entry:** compile grounded AI entry hypotheses ([#2730](https://github.com/iridyne/aletheia/issues/2730)) ([e7235d8](https://github.com/iridyne/aletheia/commit/e7235d853c12bef27a32662552787864f9afa7d0))
* **entry:** generalize grounded planning across mechanisms ([#2798](https://github.com/iridyne/aletheia/issues/2798)) ([575e5b9](https://github.com/iridyne/aletheia/commit/575e5b9b6ea6ebb215b893832ad0cc4066038f1e))
* **frontend+backend:** Item 3 server clock sync + Item 4 position pagination ([66fdac4](https://github.com/iridyne/aletheia/commit/66fdac4136c8db93dbe39849434da3f0deadabe2))
* **frontend:** complete remaining design doc specifications ([8e00845](https://github.com/iridyne/aletheia/commit/8e00845b1e0aaec7f7455ee57b04fdec200f84b4))
* **frontend:** comprehensive financial terminal UI redesign and architectural overhaul ([86f492c](https://github.com/iridyne/aletheia/commit/86f492cd14084f13b9c758c3833491ccd03dc9f1))
* **frontend:** generate full OpenAPI contract, gate contract drift, and purge dead styles ([#2891](https://github.com/iridyne/aletheia/issues/2891)) ([fcd86cd](https://github.com/iridyne/aletheia/commit/fcd86cd89dfa56ce5bb4ca47f7c16de0c7f8ee41))
* **frontend:** UI redesign phases 1-5 ([da3097b](https://github.com/iridyne/aletheia/commit/da3097bf9b6832c902daface6d6053663302ebdf))
* **learning:** qualify Memory/Reflection into Economic Knowledge ([#2530](https://github.com/iridyne/aletheia/issues/2530)) ([ce66d2d](https://github.com/iridyne/aletheia/commit/ce66d2d3ad05534fb77b8f1b1f7ca460c9dce237))
* **navigation:** complete bidirectional deep-links to focused Settings sections across Risk, Prediction, Usage and Charts ([5db4288](https://github.com/iridyne/aletheia/commit/5db4288a7c931bb4141757238886a8f3cf3375e4))
* **observation:** make Observation Lab canonical experiment owner ([#2772](https://github.com/iridyne/aletheia/issues/2772)) ([1125e51](https://github.com/iridyne/aletheia/commit/1125e5183776d4de0317867ab25c896b9845048c))
* observe Momentum geometry v2 in shadow ([#2605](https://github.com/iridyne/aletheia/issues/2605)) ([#2606](https://github.com/iridyne/aletheia/issues/2606)) ([dc54af0](https://github.com/iridyne/aletheia/commit/dc54af0fec8c7d3207ee8b9091b36f24ecaee3c7))
* **observe:** add read-only overview health ([#2628](https://github.com/iridyne/aletheia/issues/2628)) ([bd06447](https://github.com/iridyne/aletheia/commit/bd064470173e8e393b9924fcea272bfd46c4d318))
* **settings:** eliminate second sidebar and implement category hub with focused section view ([eefb5a7](https://github.com/iridyne/aletheia/commit/eefb5a7c4fe71b3d9766fb91a7aa9dacba99c349))
* **ui:** support sticky table headers with maxHeight on DataTable and TradesPositions blotter ([c0c7f71](https://github.com/iridyne/aletheia/commit/c0c7f71b4f431e4843750b777f944e3d95d85b5d))
* **universe:** add Candidate Selection Utility shadow ([#2734](https://github.com/iridyne/aletheia/issues/2734)) ([28fc349](https://github.com/iridyne/aletheia/commit/28fc349b01ed666b911480c13f41eb5ae90f6e26))
* **universe:** add discovery selection benchmark ([#2610](https://github.com/iridyne/aletheia/issues/2610)) ([7f6e1a6](https://github.com/iridyne/aletheia/commit/7f6e1a63aafdd2481b88e2f60d748dc775be743f))
* **universe:** elevate Entry Plans as peer view with expired plan status filter ([4116f04](https://github.com/iridyne/aletheia/commit/4116f04b79c3ba6b830654c62f182acbb40474df))
* **uta:** add static Bitget execution profiles ([#2521](https://github.com/iridyne/aletheia/issues/2521)) ([3a35db3](https://github.com/iridyne/aletheia/commit/3a35db3b0b2e23b5e11d854b73f2a6a793e89dd2))
* **uta:** complete static live wiring and containment ([#2523](https://github.com/iridyne/aletheia/issues/2523)) ([4e9ca00](https://github.com/iridyne/aletheia/commit/4e9ca005e6ae5730abe41577ad87426bedf01548))
* **uta:** implement Phase 5 OKX demo execution slice ([#2510](https://github.com/iridyne/aletheia/issues/2510)) ([d7e283c](https://github.com/iridyne/aletheia/commit/d7e283c31f3be73e7883a419dde353362c9e7c1d))


### Bug Fixes

* **account-state:** expose invalid persisted cache ([#2707](https://github.com/iridyne/aletheia/issues/2707)) ([7fb94d5](https://github.com/iridyne/aletheia/commit/7fb94d513da11a37008b7947e3fceede995f69f7))
* **account-sync:** preserve authoritative OKX equity ([#2527](https://github.com/iridyne/aletheia/issues/2527)) ([ba1484d](https://github.com/iridyne/aletheia/commit/ba1484d09bd056219c9b45d0e4dd3b9d30e250fa))
* **accounting:** align fresh paper portfolio projection ([#2754](https://github.com/iridyne/aletheia/issues/2754)) ([67d0db8](https://github.com/iridyne/aletheia/commit/67d0db89f3007d302ab393df803cdcda6d6f0a2c))
* **accounting:** make funding recovery fair ([#2547](https://github.com/iridyne/aletheia/issues/2547)) ([2e8f721](https://github.com/iridyne/aletheia/commit/2e8f72182acf6684186c0cff0c2f99f9cdf83371))
* **accounting:** materialize exchange partial close economics ([#2175](https://github.com/iridyne/aletheia/issues/2175)) ([#2364](https://github.com/iridyne/aletheia/issues/2364)) ([6851c98](https://github.com/iridyne/aletheia/commit/6851c98e4f5083f6727fd063f9217206ccd5fb79))
* **accounting:** pin paper account read snapshots ([8191b1f](https://github.com/iridyne/aletheia/commit/8191b1f73addf5dfa861845e2ea4446ebac75ae4))
* **accounting:** pin paper account read snapshots ([#2753](https://github.com/iridyne/aletheia/issues/2753)) ([6ebabab](https://github.com/iridyne/aletheia/commit/6ebabab054d6bf35cf4f93c66fb2fb00848ae472))
* **accounting:** reject non-finite account state ([#2656](https://github.com/iridyne/aletheia/issues/2656)) ([eb080b0](https://github.com/iridyne/aletheia/commit/eb080b0d482740574e43f2940456978a64e8a04b))
* **accounting:** rotate paper funding revival ([#2735](https://github.com/iridyne/aletheia/issues/2735)) ([b078d19](https://github.com/iridyne/aletheia/commit/b078d19248c190c05154ef9e6388030b15afe7c0))
* **agent-activity:** preserve failed read states ([#2861](https://github.com/iridyne/aletheia/issues/2861)) ([6b36f3b](https://github.com/iridyne/aletheia/commit/6b36f3bff031532edc16c7e5dbeac2ea80e686b8))
* **agent-runs:** keep jsonl mirror best effort ([#2466](https://github.com/iridyne/aletheia/issues/2466)) ([0a82420](https://github.com/iridyne/aletheia/commit/0a82420a7131415f1201bdb14204241ec7c9a3b3))
* **agent-shell:** fail closed on unknown permission modes ([#2517](https://github.com/iridyne/aletheia/issues/2517)) ([c06b23e](https://github.com/iridyne/aletheia/commit/c06b23e7677df342680c92fbdc09759dd58e2c7f))
* **agents:** distinguish missing run identity ([#2391](https://github.com/iridyne/aletheia/issues/2391)) ([a5d2082](https://github.com/iridyne/aletheia/commit/a5d2082b22f6ff7c99a9c00573beeb7176d3af8f))
* align Entry contracts and failure diagnostics ([#2902](https://github.com/iridyne/aletheia/issues/2902)) ([4b821b2](https://github.com/iridyne/aletheia/commit/4b821b258dcc26a3e5abe5e02bbeac53432c1969))
* align entry generation contracts and failure diagnostics ([84abf58](https://github.com/iridyne/aletheia/commit/84abf58a10387720a2a8b4911dc0d88ffc21d894))
* **analytics:** bind profit qualification cohort identity ([#1145](https://github.com/iridyne/aletheia/issues/1145)) ([#2795](https://github.com/iridyne/aletheia/issues/2795)) ([3e55dc8](https://github.com/iridyne/aletheia/commit/3e55dc8683c0cb8109fb09542e489df1355fbff6))
* **analytics:** bound candidate coverage identity scan ([#2342](https://github.com/iridyne/aletheia/issues/2342)) ([#2493](https://github.com/iridyne/aletheia/issues/2493)) ([acd2aec](https://github.com/iridyne/aletheia/commit/acd2aec386d9c0ba6368058189cbfb7098e97ea3))
* **analytics:** classify every candidate terminal fact ([#2832](https://github.com/iridyne/aletheia/issues/2832)) ([78b8d47](https://github.com/iridyne/aletheia/commit/78b8d473eeab558612ddd4b1069ef7f2b68c9e6d))
* **analytics:** compare equity windows by instant ([#2654](https://github.com/iridyne/aletheia/issues/2654)) ([17df4e5](https://github.com/iridyne/aletheia/commit/17df4e5a53da005c490ac5c4f2f5bbd2cc4834d2))
* **analytics:** count loss-first drawdown ([#2458](https://github.com/iridyne/aletheia/issues/2458)) ([84f0489](https://github.com/iridyne/aletheia/commit/84f04891a67fc526bbe82a80cea0e0d82e5fe387))
* **analytics:** dedupe operator first-exposure ties ([#2784](https://github.com/iridyne/aletheia/issues/2784)) ([78c26d4](https://github.com/iridyne/aletheia/commit/78c26d4479f31d7c9d4e1c8c2c5d2f8fdb0a89e4))
* **analytics:** disclose Trade Insights truncation ([#2834](https://github.com/iridyne/aletheia/issues/2834)) ([64eb4f5](https://github.com/iridyne/aletheia/commit/64eb4f5fffdfb088528f0718b53937b2de06a1a4))
* **analytics:** include funding in cost summary ([#2375](https://github.com/iridyne/aletheia/issues/2375)) ([ade8138](https://github.com/iridyne/aletheia/commit/ade8138f617c1c6c9114621943502717b8c4b1f7))
* **analytics:** preserve unknown fee totals ([#2444](https://github.com/iridyne/aletheia/issues/2444)) ([cbe81a6](https://github.com/iridyne/aletheia/commit/cbe81a6284e83bc66e1340697c203aac100deb3b))
* **analytics:** reconstruct sizing margin caps by mode ([#1323](https://github.com/iridyne/aletheia/issues/1323)) ([#2481](https://github.com/iridyne/aletheia/issues/2481)) ([b04de66](https://github.com/iridyne/aletheia/commit/b04de66ec4d4e9aefa6863eba99a764528b698a0))
* **analytics:** use eligible exit outcomes ([#2502](https://github.com/iridyne/aletheia/issues/2502)) ([3083de4](https://github.com/iridyne/aletheia/commit/3083de4f9903542b6c1087594e3910556d70cdcd))
* **api:** serialize paper proposal approval transition ([#2497](https://github.com/iridyne/aletheia/issues/2497)) ([db2c973](https://github.com/iridyne/aletheia/commit/db2c9732b599ba5e03f698bb7b4d66d68eda3154))
* **audit:** fail closed on invalid context pairs ([#2446](https://github.com/iridyne/aletheia/issues/2446)) ([d486ca5](https://github.com/iridyne/aletheia/commit/d486ca597e5e2fc039254978146d872cb0c35cfa))
* **audit:** make funding mirrors durable ([#2537](https://github.com/iridyne/aletheia/issues/2537)) ([cd4e8f8](https://github.com/iridyne/aletheia/commit/cd4e8f881fd91b9131802b00534c3e23118e0747))
* **audit:** preserve zero effectiveness metrics ([#2408](https://github.com/iridyne/aletheia/issues/2408)) ([3d642fa](https://github.com/iridyne/aletheia/commit/3d642fae1dae4a44175d6d24bd0225b4d390bd57))
* **audit:** query canonical audit events ([#2553](https://github.com/iridyne/aletheia/issues/2553)) ([5a4905e](https://github.com/iridyne/aletheia/commit/5a4905ed05753e0ed1b421aef599f758f3b13129))
* **audit:** reject malformed chain hashes ([#2697](https://github.com/iridyne/aletheia/issues/2697)) ([dcabdd2](https://github.com/iridyne/aletheia/commit/dcabdd2db1c1a31b9f9be72b6f7d3f29a0349ae7))
* **audit:** reject rotated chain reanchors ([#2664](https://github.com/iridyne/aletheia/issues/2664)) ([34d096b](https://github.com/iridyne/aletheia/commit/34d096b8e3d0dbd1a2e611e09b32eaa4e8aaf1a6))
* **audit:** stabilize verification across rotation ([#2696](https://github.com/iridyne/aletheia/issues/2696)) ([12e46c5](https://github.com/iridyne/aletheia/commit/12e46c503d96e0450f0f776ba6f913910fbd6e38))
* **backup:** replace restored audit generations ([#2712](https://github.com/iridyne/aletheia/issues/2712)) ([d2b771a](https://github.com/iridyne/aletheia/commit/d2b771a4a08dfbfbc49416a5a5d72987281d4b2d))
* bound source update continuity checks ([#2638](https://github.com/iridyne/aletheia/issues/2638)) ([35dd1b7](https://github.com/iridyne/aletheia/commit/35dd1b75dfb817e057dec62b22fa6e68cccff18d))
* **calibration:** distinguish unavailable zero ECE ([#1953](https://github.com/iridyne/aletheia/issues/1953)) ([d49bbe1](https://github.com/iridyne/aletheia/commit/d49bbe1bee88684e18457387255f1e787d267ded))
* **capital:** expose retained events through stable pages ([18f4f60](https://github.com/iridyne/aletheia/commit/18f4f602116da6adeb55b4ad85fa207b05555b05))
* **charts:** fall back on typed empty OHLCV ([#2388](https://github.com/iridyne/aletheia/issues/2388)) ([eab0035](https://github.com/iridyne/aletheia/commit/eab0035097d82789df4904027ff372bc948f3ea8))
* **charts:** preserve query failure truth ([#2405](https://github.com/iridyne/aletheia/issues/2405)) ([a8035e4](https://github.com/iridyne/aletheia/commit/a8035e4cd0a9f4c4284dc0eded08ea5e3e85f6a0))
* **charts:** prevent overlapping live polls ([#2383](https://github.com/iridyne/aletheia/issues/2383)) ([cb511e5](https://github.com/iridyne/aletheia/commit/cb511e5869e9eb16b9b26f4edff325644ba5536d))
* **charts:** propagate OHLCV cache bypass ([#2475](https://github.com/iridyne/aletheia/issues/2475)) ([be080eb](https://github.com/iridyne/aletheia/commit/be080eb4c8a0b28d75a15e8677669179d1dbbcdb))
* **charts:** reset data on exchange switch ([#2384](https://github.com/iridyne/aletheia/issues/2384)) ([9549122](https://github.com/iridyne/aletheia/commit/9549122dfdc2a28956f09e4df438ea8af95a9d07))
* **ci:** bound rerun repair attribution ([#2496](https://github.com/iridyne/aletheia/issues/2496)) ([a36cf20](https://github.com/iridyne/aletheia/commit/a36cf20ecd29c05afb7a8806b447f65642d0ee99))
* **ci:** canonicalize planner change statuses ([#2495](https://github.com/iridyne/aletheia/issues/2495)) ([86b8f3b](https://github.com/iridyne/aletheia/commit/86b8f3b08d3ac724e53bc47afb6d59a398d9f972))
* **ci:** require trusted release PR identity ([#2378](https://github.com/iridyne/aletheia/issues/2378)) ([224b5ad](https://github.com/iridyne/aletheia/commit/224b5adfd304d3657dd61883f15f4eb18393007d))
* **ci:** restore deterministic full Python validation ([#2732](https://github.com/iridyne/aletheia/issues/2732)) ([9a9fa17](https://github.com/iridyne/aletheia/commit/9a9fa1748853e351e5199e42079e82093ef0b04b))
* **ci:** restore Node-dependent regression gates ([#2415](https://github.com/iridyne/aletheia/issues/2415)) ([4b3e96f](https://github.com/iridyne/aletheia/commit/4b3e96f4087833fae8939d1cda28251e7357027d))
* **ci:** retire closed PR validation ([#2498](https://github.com/iridyne/aletheia/issues/2498)) ([00478e2](https://github.com/iridyne/aletheia/commit/00478e2c68024e006f21a88952aa57ab3abcfe14))
* **cli:** report dashboard browser launch failures ([#2468](https://github.com/iridyne/aletheia/issues/2468)) ([b15d379](https://github.com/iridyne/aletheia/commit/b15d379da61dc28a4155dea36eeb6f0c72a2ca5f))
* **cli:** surface unavailable update checks ([#2486](https://github.com/iridyne/aletheia/issues/2486)) ([df7f0bb](https://github.com/iridyne/aletheia/commit/df7f0bb2caeae892935d7fa7fd02cc4845754b0a))
* **config:** fsync atomic saves for crash durability ([#1463](https://github.com/iridyne/aletheia/issues/1463)) ([#2484](https://github.com/iridyne/aletheia/issues/2484)) ([dbdfbe3](https://github.com/iridyne/aletheia/commit/dbdfbe3721265d73ad58ee72f665763aa1286354))
* **config:** hide webhook secrets in validation errors ([#2689](https://github.com/iridyne/aletheia/issues/2689)) ([a7c0b3b](https://github.com/iridyne/aletheia/commit/a7c0b3be26d695c291162cdd898f61ba9d4aea39))
* **config:** migrate invalid legacy webhooks ([#2714](https://github.com/iridyne/aletheia/issues/2714)) ([da5d655](https://github.com/iridyne/aletheia/commit/da5d655224b961134417b7ff05dbbddfffd4f795)), closes [#2683](https://github.com/iridyne/aletheia/issues/2683)
* **config:** migrate removed AI-entry keys ([#2770](https://github.com/iridyne/aletheia/issues/2770)) ([2355edb](https://github.com/iridyne/aletheia/commit/2355edb104a22681d4cb884dcf362d9e6705563c))
* **config:** mirror nested risk policy back into its flat aliases ([#2978](https://github.com/iridyne/aletheia/issues/2978)) ([d7f44e6](https://github.com/iridyne/aletheia/commit/d7f44e6ce6f50d5f2fdc0b1426e0da9c343c61d8))
* **config:** reject controls in webhook URLs ([#2702](https://github.com/iridyne/aletheia/issues/2702)) ([7c96002](https://github.com/iridyne/aletheia/commit/7c960025786a594350d41cafeb65364b1be8c4aa))
* **config:** reject unknown persisted keys ([#2519](https://github.com/iridyne/aletheia/issues/2519)) ([7332135](https://github.com/iridyne/aletheia/commit/733213507c7be97fb105825636373c5a199f2e4b))
* **config:** revalidate state before persistence ([#2692](https://github.com/iridyne/aletheia/issues/2692)) ([e6cfb00](https://github.com/iridyne/aletheia/commit/e6cfb00f6b9787caff941709d4913b7d976a277b))
* **config:** stop the settings route persisting a trading-halt execution mode ([93701f3](https://github.com/iridyne/aletheia/commit/93701f3e94ff057e8cd0957dd9ea10cf6082b13f))
* **config:** stop the settings route persisting a trading-halt execution mode ([#2975](https://github.com/iridyne/aletheia/issues/2975)) ([738f801](https://github.com/iridyne/aletheia/commit/738f801906511c61bae4a430075e557a96047a8d))
* converge verified disappearance with unknown entry fee ([cca43b5](https://github.com/iridyne/aletheia/commit/cca43b591b23af45db4898f95e9a9f7ccdb8784c))
* **counterfactual:** compare shadow times by UTC instant ([9c4cf44](https://github.com/iridyne/aletheia/commit/9c4cf44598d2d13eb3a3eb47ad9acf96c56b2181))
* **counterfactual:** fail closed on epoch detection errors ([2eb8a3d](https://github.com/iridyne/aletheia/commit/2eb8a3d05c989bd9861ea9ce99e33e01ac9eae10))
* **css:** unify scrollbar-hide across Firefox and WebKit ([#2947](https://github.com/iridyne/aletheia/issues/2947)) ([7726667](https://github.com/iridyne/aletheia/commit/772666741a17f02647f9e84a7f7a1003fbc8066c))
* **data:** align collector diagnostics and current target coverage ([#2898](https://github.com/iridyne/aletheia/issues/2898)) ([495877a](https://github.com/iridyne/aletheia/commit/495877a8536dd9d73ec2de62463bcc75530cd41f))
* **data:** align derivative coverage freshness ([#2765](https://github.com/iridyne/aletheia/issues/2765)) ([#2800](https://github.com/iridyne/aletheia/issues/2800)) ([4abfb2f](https://github.com/iridyne/aletheia/commit/4abfb2f4863f978f5251097ea993ae82964a4fc4))
* **data:** backfill every active universe member ([#2385](https://github.com/iridyne/aletheia/issues/2385)) ([540ff0f](https://github.com/iridyne/aletheia/commit/540ff0f8df9df7218329a79b1b93ec76fb8e25de))
* **data:** preserve OHLCV physical market identity ([#2801](https://github.com/iridyne/aletheia/issues/2801)) ([eaad186](https://github.com/iridyne/aletheia/commit/eaad186c95950e19fa20610c8430205f9092d6a6))
* **data:** reject invalid derivatives snapshots ([#2533](https://github.com/iridyne/aletheia/issues/2533)) ([5b796a9](https://github.com/iridyne/aletheia/commit/5b796a9e951899d5e1b69547a4a680ac79d218e0))
* **data:** reject invalid live funding overrides ([#2551](https://github.com/iridyne/aletheia/issues/2551)) ([b0004ad](https://github.com/iridyne/aletheia/commit/b0004adff4156178eb2cd11ccde40b53115f7b28))
* **data:** retain future provider timestamps ([#2763](https://github.com/iridyne/aletheia/issues/2763)) ([#2799](https://github.com/iridyne/aletheia/issues/2799)) ([fb7a567](https://github.com/iridyne/aletheia/commit/fb7a567eebb2b3e805fc1373fc8c3a0f211cec56))
* **doctor:** keep diagnostics physically read-only ([#2465](https://github.com/iridyne/aletheia/issues/2465)) ([f04a670](https://github.com/iridyne/aletheia/commit/f04a670f83a8a23875dde40ff2db9504eca68229))
* **doctor:** return nonzero for warnings ([#1461](https://github.com/iridyne/aletheia/issues/1461)) ([#2478](https://github.com/iridyne/aletheia/issues/2478)) ([2736869](https://github.com/iridyne/aletheia/commit/273686903fcec42035bce9c930ae6ab4cd44c0ce))
* **doctor:** structure fix start outcomes ([#2226](https://github.com/iridyne/aletheia/issues/2226)) ([5f5044b](https://github.com/iridyne/aletheia/commit/5f5044ba19ef4fa82b0b0136e8e0d019641a95f9))
* **dreaming:** use central outcome authority for daily pnl ([#2504](https://github.com/iridyne/aletheia/issues/2504)) ([9ac4e64](https://github.com/iridyne/aletheia/commit/9ac4e64c2f4a453bc65756a791b28b56c6a6a0d9))
* **economics:** aggregate complete usage windows ([#1486](https://github.com/iridyne/aletheia/issues/1486)) ([7397e5f](https://github.com/iridyne/aletheia/commit/7397e5f3142a10320d854d8b21e37ee6b448812c))
* **economics:** reject invalid budget caps ([#2644](https://github.com/iridyne/aletheia/issues/2644)) ([0631f1a](https://github.com/iridyne/aletheia/commit/0631f1a0645f381a68e5106ba25afce6dfba7843))
* **economics:** restrict viability v1 to paper mode ([#2459](https://github.com/iridyne/aletheia/issues/2459)) ([2eee3a5](https://github.com/iridyne/aletheia/commit/2eee3a5ce2f59211676be54bbc25b50c4888a5e8))
* **entry-plan:** await complete terminal fill evidence ([#2288](https://github.com/iridyne/aletheia/issues/2288)) ([a9b1646](https://github.com/iridyne/aletheia/commit/a9b1646a0ebc272044cd529185409664bb60a324))
* **entry-plan:** close structured output review gaps ([0093736](https://github.com/iridyne/aletheia/commit/009373665a3384c6b3e29d46e4fba28188bc4590))
* **entry-plan:** converge cancel and IOC terminal states ([#2288](https://github.com/iridyne/aletheia/issues/2288)) ([81ebcb1](https://github.com/iridyne/aletheia/commit/81ebcb1f95e15dfceb74d99910964fd30bce8dce))
* **entry-plan:** enforce protective invalidation geometry ([041627c](https://github.com/iridyne/aletheia/commit/041627ca7e8606be4292fba154233e829855cc8c))
* **entry-plan:** enforce triggered maker timeout ([104f639](https://github.com/iridyne/aletheia/commit/104f639a28a7b1bae4720eb78e4feb847dd6617e))
* **entry-planner:** canonicalize no-plan wire fields ([#2558](https://github.com/iridyne/aletheia/issues/2558)) ([63738d0](https://github.com/iridyne/aletheia/commit/63738d0ce84d51e688db0283ddc2080454616c51))
* **entry-planner:** compile deterministic plans from intents ([51bcda4](https://github.com/iridyne/aletheia/commit/51bcda48b67f4045d64fe9beac0cbc864bcde75c))
* **entry-planner:** compile deterministic plans from intents ([#2535](https://github.com/iridyne/aletheia/issues/2535)) ([64daeee](https://github.com/iridyne/aletheia/commit/64daeee7ed4e2b8e2f329b8bfd461e131195dcee))
* **entry-planner:** preserve admission and urgent priority ([#2288](https://github.com/iridyne/aletheia/issues/2288)) ([7a8f00b](https://github.com/iridyne/aletheia/commit/7a8f00b59c932b71207ec10412fcff266a5e596f))
* **entry-planner:** version planning contract generations ([#2723](https://github.com/iridyne/aletheia/issues/2723)) ([ce9a619](https://github.com/iridyne/aletheia/commit/ce9a6197c122aac4cc24eef3f0854fcb57aa3da0))
* **entry-plan:** persist two-clock execution evidence ([#2288](https://github.com/iridyne/aletheia/issues/2288)) ([dc4637c](https://github.com/iridyne/aletheia/commit/dc4637cf4d60e7b3d0a603ec943980e0e5f6896d))
* **entry-plan:** restore executable planner contract ([#2552](https://github.com/iridyne/aletheia/issues/2552)) ([e06f19b](https://github.com/iridyne/aletheia/commit/e06f19bdeb8058d913a54fad4d791aa7881bd6db))
* **entry-plan:** support paper internal execution ([7c1c2a9](https://github.com/iridyne/aletheia/commit/7c1c2a9e8c5bfb1995ab2b638e47cb8a49a3c633))
* **entry-plan:** validate source market timestamps ([d29e564](https://github.com/iridyne/aletheia/commit/d29e564dae62a33fbd844ec8678762f7f93f5f92))
* **entry-workbench:** complete owner-facing plan controls ([#2288](https://github.com/iridyne/aletheia/issues/2288)) ([853aacc](https://github.com/iridyne/aletheia/commit/853aacc41580acb711b9e469eb206d3342bcb056))
* **entry-workbench:** expose active plan candidate handoff ([#2288](https://github.com/iridyne/aletheia/issues/2288)) ([355d401](https://github.com/iridyne/aletheia/commit/355d4012721bcc66c396c49daf644f97deab6751))
* **entry-workbench:** preserve unavailable reads ([#2433](https://github.com/iridyne/aletheia/issues/2433)) ([#2434](https://github.com/iridyne/aletheia/issues/2434)) ([2e46632](https://github.com/iridyne/aletheia/commit/2e46632269e405af2e81bd761af5a9bcc3f5f330))
* **entry:** admit monitored Binance testnet planning ([#2761](https://github.com/iridyne/aletheia/issues/2761)) ([#2808](https://github.com/iridyne/aletheia/issues/2808)) ([24e58d7](https://github.com/iridyne/aletheia/commit/24e58d787f2984f50593432c48aa5ac4a8175296))
* **entry:** bound model-visible planning evidence ([#2870](https://github.com/iridyne/aletheia/issues/2870)) ([05cb819](https://github.com/iridyne/aletheia/commit/05cb8194a2d455a47f71d871e40d4060b8f25bca))
* **entry:** canonical current Entry contract ([#2875](https://github.com/iridyne/aletheia/issues/2875)) ([b831830](https://github.com/iridyne/aletheia/commit/b8318305e0396e3dbf56432a21fe174953436932)), closes [#2851](https://github.com/iridyne/aletheia/issues/2851)
* **entry:** close candidate plan authority seam ([#2332](https://github.com/iridyne/aletheia/issues/2332) [#2336](https://github.com/iridyne/aletheia/issues/2336)) ([3d1bdb7](https://github.com/iridyne/aletheia/commit/3d1bdb70fa42a4663736261d99846d2f9010f139))
* **entry:** converge grounded AI contracts ([#2744](https://github.com/iridyne/aletheia/issues/2744)) ([095c21f](https://github.com/iridyne/aletheia/commit/095c21f0df2399d3b983d6945402998ba00279c0)), closes [#2725](https://github.com/iridyne/aletheia/issues/2725)
* **entry:** cut over to canonical planning lifecycle ([#2851](https://github.com/iridyne/aletheia/issues/2851)) ([29a155f](https://github.com/iridyne/aletheia/commit/29a155ffd43fe0d6aab9186f53eb41a8f60d7134))
* **entry:** isolate paper outcome evidence ([#2789](https://github.com/iridyne/aletheia/issues/2789)) ([4ac91a9](https://github.com/iridyne/aletheia/commit/4ac91a9e131bcb5eafc4cee45ee23a8f31ad1325))
* **entry:** make grounded planner the default ([#2756](https://github.com/iridyne/aletheia/issues/2756)) ([c7708ca](https://github.com/iridyne/aletheia/commit/c7708ca738e007ef41364f7a279eabdf05d19145))
* **entry:** make paper execution evidence-backed ([#2789](https://github.com/iridyne/aletheia/issues/2789)) ([#2794](https://github.com/iridyne/aletheia/issues/2794)) ([9fa7cf2](https://github.com/iridyne/aletheia/commit/9fa7cf23e6a634b9cbadff79f0124a4a1a2d4c00))
* **entry:** persist planning candles and selection evidence ([b150eff](https://github.com/iridyne/aletheia/commit/b150efff5ebf0e5def2e5f85707ade890e7ff982))
* **entry:** preserve legacy cutover rows ([#2854](https://github.com/iridyne/aletheia/issues/2854)) ([573cba7](https://github.com/iridyne/aletheia/commit/573cba792aee987cef75d13e95c610f608df64e0))
* **entry:** reject obsolete contract at plan activation ([4d5c700](https://github.com/iridyne/aletheia/commit/4d5c7004588dac4538ac91edb08232edbbc4092e))
* **entry:** repair semantically invalid hypotheses ([#2871](https://github.com/iridyne/aletheia/issues/2871)) ([bcef8ce](https://github.com/iridyne/aletheia/commit/bcef8ce909f1e66965e6d0fb7b4f52aba49d94fc))
* **entry:** restore candidate publication and close lifecycle authority gaps ([fa8a2d2](https://github.com/iridyne/aletheia/commit/fa8a2d210dc2723f50f6c1492a4f4acd0c77b4df))
* **entry:** restore canonical candle and selection evidence supply ([#2896](https://github.com/iridyne/aletheia/issues/2896)) ([401b39e](https://github.com/iridyne/aletheia/commit/401b39e37d6c14e63aaef2a25e04530048069a84))
* **entry:** restore legacy refresh timestamp ([#2855](https://github.com/iridyne/aletheia/issues/2855)) ([a8e48de](https://github.com/iridyne/aletheia/commit/a8e48de0ac1ab41c656164f67d5a1af29d077954))
* **entry:** resynchronize a trigger engine poisoned by one sequence anomaly ([#3003](https://github.com/iridyne/aletheia/issues/3003)) ([d648dce](https://github.com/iridyne/aletheia/commit/d648dce7ee05beca244fcf9a97f0d2fd5de57711)), closes [#2932](https://github.com/iridyne/aletheia/issues/2932)
* **exchange:** close owned sessions independently ([#2386](https://github.com/iridyne/aletheia/issues/2386)) ([19675b3](https://github.com/iridyne/aletheia/commit/19675b310ff941926caa2df2e1a89bef16d69708))
* exclude temporally invalid observation outcomes ([#2374](https://github.com/iridyne/aletheia/issues/2374)) ([7f00420](https://github.com/iridyne/aletheia/commit/7f004206c1809b022b793b19990935f46199d143))
* **execution:** centralize order lifecycle transitions ([#2954](https://github.com/iridyne/aletheia/issues/2954)) ([91ed04f](https://github.com/iridyne/aletheia/commit/91ed04f2bb40072b24fea0042d76bcb781835132))
* **execution:** converge terminal stale orders ([#2443](https://github.com/iridyne/aletheia/issues/2443)) ([f7be74c](https://github.com/iridyne/aletheia/commit/f7be74ce3e139f83099d3ae8620b87d87a2e568b))
* **execution:** fail the entry plan when a live order fails ([#2948](https://github.com/iridyne/aletheia/issues/2948)) ([d36c6af](https://github.com/iridyne/aletheia/commit/d36c6af5ef85f53bcf3a2a83d9e706f4e0899f69))
* **execution:** give the TPSL close claim its proven account identity ([#2979](https://github.com/iridyne/aletheia/issues/2979)) ([8195a39](https://github.com/iridyne/aletheia/commit/8195a39f880ddf509330031fe0dafadee4b0c699))
* **execution:** registry market-type vocabulary + the exchange-mode E2E spine that found it ([#3007](https://github.com/iridyne/aletheia/issues/3007)) ([b2e22d6](https://github.com/iridyne/aletheia/commit/b2e22d6b17e170578d5075afe728e75d77389341))
* **export:** fail closed when config cannot be exported ([#1433](https://github.com/iridyne/aletheia/issues/1433)) ([#2741](https://github.com/iridyne/aletheia/issues/2741)) ([311f549](https://github.com/iridyne/aletheia/commit/311f549cad999cb73b6a1a0b8a08cd7718470cf7))
* fail closed when demo quality evidence is unavailable ([#2491](https://github.com/iridyne/aletheia/issues/2491)) ([97cbf19](https://github.com/iridyne/aletheia/commit/97cbf19a7ae6bc6661bcdd78d05d40a422f8d6af))
* fairly cancel all blocked entry plans ([#2598](https://github.com/iridyne/aletheia/issues/2598)) ([1db3710](https://github.com/iridyne/aletheia/commit/1db3710e492ed891fb0793d7849acb859d0dffaa))
* **fast-monitoring:** support Binance public streams ([#2639](https://github.com/iridyne/aletheia/issues/2639)) ([efea6b0](https://github.com/iridyne/aletheia/commit/efea6b04fd7a40875bf5fbba3e3f1717fb98fb30))
* **fast-monitoring:** use managed proxy for Binance streams [skip ci] ([#2648](https://github.com/iridyne/aletheia/issues/2648)) ([42565b0](https://github.com/iridyne/aletheia/commit/42565b0df1d5f16e14f3cf4a378bbd6fdcef789f))
* **fast-trigger:** connect required Binance markets ([#2715](https://github.com/iridyne/aletheia/issues/2715)) ([a80fec3](https://github.com/iridyne/aletheia/commit/a80fec363aa9e9bd34140d9365a3b72b5173b078)), closes [#2669](https://github.com/iridyne/aletheia/issues/2669)
* **fast-trigger:** contain malformed Bitget frame metadata ([#2640](https://github.com/iridyne/aletheia/issues/2640)) ([19cd3a2](https://github.com/iridyne/aletheia/commit/19cd3a2a55e21cedabe0020a7887511b85a2a346))
* **fast-trigger:** isolate crossing maker rejection [skip ci] ([#2652](https://github.com/iridyne/aletheia/issues/2652)) ([606c70e](https://github.com/iridyne/aletheia/commit/606c70eeb676542b3108a9d547f1674158616590))
* **fast-trigger:** keep stale cancellation unresolved ([#2642](https://github.com/iridyne/aletheia/issues/2642)) ([f0cc767](https://github.com/iridyne/aletheia/commit/f0cc767784c71ff0bfd52ee1a930c8b9542ff15f))
* **fast-trigger:** key Binance candles by bar open ([#2695](https://github.com/iridyne/aletheia/issues/2695)) ([ef46788](https://github.com/iridyne/aletheia/commit/ef46788ccfd916260fb685af405afed901462908))
* **fast-trigger:** preserve order ownership on plan integrity failure ([#2494](https://github.com/iridyne/aletheia/issues/2494)) ([52b0339](https://github.com/iridyne/aletheia/commit/52b03393cd1656b639752f4a6651bc92b28eead9))
* **fast-trigger:** reject Binance error acknowledgements ([#2691](https://github.com/iridyne/aletheia/issues/2691)) ([851b1d8](https://github.com/iridyne/aletheia/commit/851b1d8020deae0dd82dbeec7ed6ac203fd2a7ad))
* **fast-trigger:** reject Binance testnet spot ([#2706](https://github.com/iridyne/aletheia/issues/2706)) ([72292dc](https://github.com/iridyne/aletheia/commit/72292dcaa5354f49c65d4304cfb8881453cbdc4e))
* **fast-trigger:** retain restart traceback [skip ci] ([#2650](https://github.com/iridyne/aletheia/issues/2650)) ([dbe788b](https://github.com/iridyne/aletheia/commit/dbe788badd559b52dfb33027e1ca7c0fa525a66f))
* **fast-trigger:** retry Binance spot snapshots ([#2699](https://github.com/iridyne/aletheia/issues/2699)) ([0e310ef](https://github.com/iridyne/aletheia/commit/0e310ef5f9bc8ff9bfe23b9eb687c77d3eec4663))
* **fast-trigger:** revoke Observe permission continuously ([#2360](https://github.com/iridyne/aletheia/issues/2360)) ([ee57606](https://github.com/iridyne/aletheia/commit/ee576067cc48f5527169020397989908521d67d3))
* **filter-evidence:** order first exposure by instant ([#2719](https://github.com/iridyne/aletheia/issues/2719)) ([4a41c02](https://github.com/iridyne/aletheia/commit/4a41c02dbaf0ba0d214c0fc153216264de8e004e)), closes [#2567](https://github.com/iridyne/aletheia/issues/2567)
* finish the StrategyFactory deletion — docs, generated types, orphaned model ([0f72903](https://github.com/iridyne/aletheia/commit/0f7290360e67b6a9310be9911b97612acfc05b6f))
* freeze truthful Universe baseline before family competition ([#2602](https://github.com/iridyne/aletheia/issues/2602)) ([df28362](https://github.com/iridyne/aletheia/commit/df28362562089b6b3df60e97245b8a0bcb715b43))
* **frontend:** contain command palette focus ([#2860](https://github.com/iridyne/aletheia/issues/2860)) ([290622d](https://github.com/iridyne/aletheia/commit/290622d4bbab8c44995cefd2628e39e01c133b4e))
* **frontend:** keep close acknowledgements pending ([#1715](https://github.com/iridyne/aletheia/issues/1715)) ([#2740](https://github.com/iridyne/aletheia/issues/2740)) ([4d0c963](https://github.com/iridyne/aletheia/commit/4d0c96324c4003fdfaf7e4f7cee2f6efb832e49c))
* **frontend:** make update dashboard build type-safe ([#2773](https://github.com/iridyne/aletheia/issues/2773)) ([327b52f](https://github.com/iridyne/aletheia/commit/327b52f2749726412f793a99c3d263b9bded1b08))
* **frontend:** preserve nested table actions ([#2660](https://github.com/iridyne/aletheia/issues/2660)) ([6d646bd](https://github.com/iridyne/aletheia/commit/6d646bd8bd1737bb760877cda14ec2d5ac891e8b))
* **frontend:** reload failed lazy routes on retry ([#2858](https://github.com/iridyne/aletheia/issues/2858)) ([39a253d](https://github.com/iridyne/aletheia/commit/39a253dc5182e1bae560d12ce9fea6ea23b962b4))
* **frontend:** remaining all_pages_deep_dive_audit items ([163306b](https://github.com/iridyne/aletheia/commit/163306b232309e7615c1f6db5ac534bcf7699077))
* **frontend:** rename cmdk-topbar-pill to command-pill to pass bundle performance check ([173e5ca](https://github.com/iridyne/aletheia/commit/173e5cad5e6267eb5b3e422bd89c9c51e371467a))
* **frontend:** repair tests broken by Phase 1-5 changes ([6afc92c](https://github.com/iridyne/aletheia/commit/6afc92c87e49b8a80f063b21c8a9c9233099ae99))
* **frontend:** resolve topbar clutter, CommandPalette reopen bug, and Agent Feed visual styling ([d54b10c](https://github.com/iridyne/aletheia/commit/d54b10cf3305119da3691c03eb1eb5aabeb6cafa))
* **gateway:** accept successful managed stop ([#2622](https://github.com/iridyne/aletheia/issues/2622)) ([ef33174](https://github.com/iridyne/aletheia/commit/ef331748652da24d65e820dc8f4cc4853de9a9b6))
* **gateway:** close circuit breaker recovery gaps ([#2565](https://github.com/iridyne/aletheia/issues/2565)) ([d201bc5](https://github.com/iridyne/aletheia/commit/d201bc59367a724c89d071f18e03ba392116a495))
* **gateway:** make lease acquisition atomic ([#2820](https://github.com/iridyne/aletheia/issues/2820)) ([5c227d1](https://github.com/iridyne/aletheia/commit/5c227d1cabc600056bf05f3c1fbfe892a1ff6bb8))
* **gateway:** migrate legacy macOS watchdog ([#2555](https://github.com/iridyne/aletheia/issues/2555)) ([3d0048e](https://github.com/iridyne/aletheia/commit/3d0048e5c28285cff656c3afd310a3df737a113a))
* **gateway:** preserve recovery during internal stops ([#2562](https://github.com/iridyne/aletheia/issues/2562)) ([fd27d47](https://github.com/iridyne/aletheia/commit/fd27d47cef0e6f86577b1508b14b7d0fbc187ae5))
* **gateway:** surface refused restart as an error ([#2525](https://github.com/iridyne/aletheia/issues/2525)) ([8e7c259](https://github.com/iridyne/aletheia/commit/8e7c25932b8c8070509c3c0e75c330338f9e86be))
* **guard:** commit promotion with durable provenance ([106a1b7](https://github.com/iridyne/aletheia/commit/106a1b71df7799c2dc067a56c7e1ef16c96cf7be))
* **health:** distinguish incomplete collector states ([#1546](https://github.com/iridyne/aletheia/issues/1546)) ([#2817](https://github.com/iridyne/aletheia/issues/2817)) ([f9ac000](https://github.com/iridyne/aletheia/commit/f9ac00000c131ee060323c1e3830cfb9f078f35e))
* **health:** fail closed on invalid collector timestamps ([#2802](https://github.com/iridyne/aletheia/issues/2802)) ([6b6d697](https://github.com/iridyne/aletheia/commit/6b6d697c8c6e7aacec26486658c3a14afa6eb82b))
* **health:** gate collector success on persistence ([#1542](https://github.com/iridyne/aletheia/issues/1542)) ([#2815](https://github.com/iridyne/aletheia/issues/2815)) ([629952d](https://github.com/iridyne/aletheia/commit/629952d70dccd6fadec3da4ca79145570368dd74))
* **health:** keep empty market targets non-applicable ([#2803](https://github.com/iridyne/aletheia/issues/2803)) ([cfb28ef](https://github.com/iridyne/aletheia/commit/cfb28ef09d5f57197e1d35c01dfc5e1d12e7027d))
* **health:** preserve unknown probe states ([#2503](https://github.com/iridyne/aletheia/issues/2503)) ([55306a5](https://github.com/iridyne/aletheia/commit/55306a575c047288749fb42e6ca6ed7350db154e))
* **health:** reject impossible future timestamps ([#1570](https://github.com/iridyne/aletheia/issues/1570)) ([f13848a](https://github.com/iridyne/aletheia/commit/f13848aa0e1fe325f1e1b3c4e6156438a5eda35d))
* honor microstructure member limit ([da43fd2](https://github.com/iridyne/aletheia/commit/da43fd2fef311574670f43a88ec211e3de92b45a))
* **inspect:** apply serenity run status filter ([398a114](https://github.com/iridyne/aletheia/commit/398a1146a1356965cd34a13fc565fdd7ec7ef157))
* **inspect:** group snapshot coverage before limiting ([#2427](https://github.com/iridyne/aletheia/issues/2427)) ([4a706fc](https://github.com/iridyne/aletheia/commit/4a706fc04fd9a03136673416ab2f904a4ecd83e8))
* **inspect:** search all active members for liquidations ([14c7595](https://github.com/iridyne/aletheia/commit/14c75959a8f7812b9902aefdee56fbeb760a87bf))
* integrate verified Orca repairs without unsafe aggregate changes ([#2997](https://github.com/iridyne/aletheia/issues/2997)) ([80555f7](https://github.com/iridyne/aletheia/commit/80555f7fff568d3a03ec082bc3ee963a17890854))
* keep doctor LLM probe accounting outside read-only fence ([#2490](https://github.com/iridyne/aletheia/issues/2490)) ([d1a7405](https://github.com/iridyne/aletheia/commit/d1a74052973758b1c7d9ed1120f5da4d095a76fe))
* **knowledge:** index promoted trading guards ([#2724](https://github.com/iridyne/aletheia/issues/2724)) ([9a6899c](https://github.com/iridyne/aletheia/commit/9a6899cfcd7f94589ecba92ddb4e69538eb738de))
* **learning:** close post-[#2325](https://github.com/iridyne/aletheia/issues/2325) audit gaps ([a9ac82a](https://github.com/iridyne/aletheia/commit/a9ac82a22906cbe40c00110a0b666a466d2a23c7))
* **learning:** guard lesson authority transitions ([#2755](https://github.com/iridyne/aletheia/issues/2755)) ([f783413](https://github.com/iridyne/aletheia/commit/f78341387892d68d29a757f82a82016a6e9aeca4))
* **learning:** keep policy epoch rotation boundaries monotonic ([#2520](https://github.com/iridyne/aletheia/issues/2520)) ([c431122](https://github.com/iridyne/aletheia/commit/c431122cccf4b515c3db16dd144eb2dd66728dbe))
* **learning:** make Knowledge runtime provenance recoverable ([#2534](https://github.com/iridyne/aletheia/issues/2534)) ([214e885](https://github.com/iridyne/aletheia/commit/214e885aef61beb52531ca789c5d4acb17ef1653))
* **learning:** preserve complete Markdown bodies in FTS ([#2524](https://github.com/iridyne/aletheia/issues/2524)) ([a676f26](https://github.com/iridyne/aletheia/commit/a676f260e126b57a09e686c8ac36f8a04a3cc805))
* **learning:** preserve lesson dry-run deadline ([#2718](https://github.com/iridyne/aletheia/issues/2718)) ([4c7b139](https://github.com/iridyne/aletheia/commit/4c7b139d3e8b557c56bc2d14b00a9319e814777d)), closes [#2570](https://github.com/iridyne/aletheia/issues/2570)
* **learning:** report contradiction scan coverage ([#2561](https://github.com/iridyne/aletheia/issues/2561)) ([3f90e24](https://github.com/iridyne/aletheia/commit/3f90e243fb64a2cca164b47ee90b7364f13b4221))
* **learning:** report measurement_unsupported before probing Lab authority ([157fa24](https://github.com/iridyne/aletheia/commit/157fa24fb87e77369f03faeae19c66273fce9537))
* **learning:** verify fixed-look cutoff fingerprint ([#2661](https://github.com/iridyne/aletheia/issues/2661)) ([07554d8](https://github.com/iridyne/aletheia/commit/07554d87dd5bea04fa97c86f6ccc6774fecc8994))
* **llm:** capture default provider identity once ([#2467](https://github.com/iridyne/aletheia/issues/2467)) ([5b2c88d](https://github.com/iridyne/aletheia/commit/5b2c88dc55659708a6fefed5b33561b3616f23dd))
* **llm:** fall back from empty model responses ([#2550](https://github.com/iridyne/aletheia/issues/2550)) ([58648f9](https://github.com/iridyne/aletheia/commit/58648f9d19a0b74b301ce645e414d6b91b5c9737))
* **llm:** record terminal empty response failures ([#2477](https://github.com/iridyne/aletheia/issues/2477)) ([cda9184](https://github.com/iridyne/aletheia/commit/cda918495b1420275c4a4bc8f79d3976cf5158eb))
* make administrative close repair exact and atomic ([#2156](https://github.com/iridyne/aletheia/issues/2156) [#2157](https://github.com/iridyne/aletheia/issues/2157)) ([a0eaf2e](https://github.com/iridyne/aletheia/commit/a0eaf2e819825f09897d846688a48a15f2a2ff55))
* make the timestamp-parser convergence real (it was a rename, not a merge) ([4d58444](https://github.com/iridyne/aletheia/commit/4d584447aaa9fe773b3b601ebf80eb8263e8cc09))
* **market-data:** fall back to acquired_at when provider timestamp missing ([9a191c7](https://github.com/iridyne/aletheia/commit/9a191c718aac7b325b90c1906f2b59f651abf0f5))
* **market-data:** preserve derivatives source time ([#2742](https://github.com/iridyne/aletheia/issues/2742)) ([b28470d](https://github.com/iridyne/aletheia/commit/b28470d7d09a8c879d939ae7671eeb1cb4eeb2ff))
* **market-data:** reject invalid ticker facts ([#2485](https://github.com/iridyne/aletheia/issues/2485)) ([9423020](https://github.com/iridyne/aletheia/commit/94230200fe19b73f9bc50cf600adcf83c3f0f445))
* **market-data:** retain funding provenance changes ([#2788](https://github.com/iridyne/aletheia/issues/2788)) ([5207d26](https://github.com/iridyne/aletheia/commit/5207d261ffa597cd619d4815793e9bb3245689d0))
* **market-data:** validate OHLCV before marking venue healthy ([#2509](https://github.com/iridyne/aletheia/issues/2509)) ([21eaa84](https://github.com/iridyne/aletheia/commit/21eaa845c8b1caa8d1208f3dd7c2fb142cc2ef79))
* **microstructure:** reject impossible orderbooks ([#2471](https://github.com/iridyne/aletheia/issues/2471)) ([9eb501e](https://github.com/iridyne/aletheia/commit/9eb501e1e8c9a768b71e717a1849658aa07dade9))
* **migrations,update:** unblock v70 rollout and bound update preflight ([#2881](https://github.com/iridyne/aletheia/issues/2881)) ([1b27fa1](https://github.com/iridyne/aletheia/commit/1b27fa152240b7f107412ce893d5339b38dba236))
* **model:** fail closed for unconfigured providers ([#1778](https://github.com/iridyne/aletheia/issues/1778)) ([12f0808](https://github.com/iridyne/aletheia/commit/12f0808e0736e0c2c214f1cb44813b3be4c56fe1))
* **models:** bound brokered stream queue ([#1475](https://github.com/iridyne/aletheia/issues/1475)) ([#2482](https://github.com/iridyne/aletheia/issues/2482)) ([73a1280](https://github.com/iridyne/aletheia/commit/73a12809d1122b5839c6beb3e9ff7662edd79b04))
* **models:** preserve model catalogue health contract ([b678133](https://github.com/iridyne/aletheia/commit/b67813390334f9bdcbcf193e8c6907da60b692f1))
* **models:** retain exact provider profile identity ([#2655](https://github.com/iridyne/aletheia/issues/2655)) ([fc134d5](https://github.com/iridyne/aletheia/commit/fc134d529e8f8a110c68ca1193dfbe3a93649968))
* **network:** honor lowercase proxy overrides ([#2703](https://github.com/iridyne/aletheia/issues/2703)) ([7f8a75b](https://github.com/iridyne/aletheia/commit/7f8a75bbf32c48509d4b50d4f8ba8f47b832253e))
* normalize Bitget hedge close fills ([#2440](https://github.com/iridyne/aletheia/issues/2440)) ([f4683ee](https://github.com/iridyne/aletheia/commit/f4683ee5d26c62ee4d01166002888ab7b4148a91))
* **notifications:** keep pnl mode scoped ([#2759](https://github.com/iridyne/aletheia/issues/2759)) ([#2804](https://github.com/iridyne/aletheia/issues/2804)) ([0b42662](https://github.com/iridyne/aletheia/commit/0b42662947580921cad92a4ceb000a483098819c))
* **observation:** accept append-only outbox progress ([#2814](https://github.com/iridyne/aletheia/issues/2814)) ([fd078d7](https://github.com/iridyne/aletheia/commit/fd078d732c245411d41d2b018b765573fc275019))
* **observation:** checkpoint main bridge passes ([#2844](https://github.com/iridyne/aletheia/issues/2844)) ([abd3871](https://github.com/iridyne/aletheia/commit/abd3871223d74d53324ab1907ab65182ed82cae7))
* **observation:** close post-merge correctness gaps ([d759894](https://github.com/iridyne/aletheia/commit/d759894d5fde9a3b9851bb6136f560ecf9aa45cc))
* **observation:** import deferred capture refusals ([51e9f33](https://github.com/iridyne/aletheia/commit/51e9f338c0fc1b20ff095ab69ead318913d252e4))
* **observation:** import deferred capture refusals ([#2809](https://github.com/iridyne/aletheia/issues/2809)) ([75c3fa6](https://github.com/iridyne/aletheia/commit/75c3fa6fdbe5c90a2c0ac35ee5c11400ebfbf060))
* **observation:** keep archive and checkpoint identities stable ([#2779](https://github.com/iridyne/aletheia/issues/2779)) ([bc3c91a](https://github.com/iridyne/aletheia/commit/bc3c91a26f40e62fd155cd1969cb4cb527633df0))
* **observation:** keep checkpoint progress runner-scoped ([#2821](https://github.com/iridyne/aletheia/issues/2821)) ([#2822](https://github.com/iridyne/aletheia/issues/2822)) ([5d7b5ba](https://github.com/iridyne/aletheia/commit/5d7b5ba7a2f4eec681c719bd1d1fcf23337325da))
* **observation:** preserve actual finalization time ([7382c23](https://github.com/iridyne/aletheia/commit/7382c237e85b5172dd9d6f123e201c186b7c894a))
* **observation:** preserve cursor identity across idle bridge passes ([#2862](https://github.com/iridyne/aletheia/issues/2862)) ([3a3f3b2](https://github.com/iridyne/aletheia/commit/3a3f3b20a6650cfed41169543e6c4f1ed857628b))
* **observation:** rebuild verified orphan artifacts ([f2c5098](https://github.com/iridyne/aletheia/commit/f2c509812332cd04e7a754fdc88173e9d2454b47))
* **observation:** reconcile persisted batch admissions ([21f44e5](https://github.com/iridyne/aletheia/commit/21f44e5a473ba1667869390645034130aaa1e728))
* **observation:** retain persisted retry provenance ([73f41d3](https://github.com/iridyne/aletheia/commit/73f41d35a4d97bb73dc36178ac00bc28d305c036))
* **observation:** stabilize pruned outbox checkpoints ([#2835](https://github.com/iridyne/aletheia/issues/2835)) ([86a9863](https://github.com/iridyne/aletheia/commit/86a986394ace13668d7a2ef0fdcd113ef06577aa))
* **observe:** bootstrap built-in status handlers ([#2624](https://github.com/iridyne/aletheia/issues/2624)) ([aaba4f0](https://github.com/iridyne/aletheia/commit/aaba4f08a0d27f4d3a7d1224fd0d3b82a83930ac))
* **observer:** require exact venue technical context ([#2828](https://github.com/iridyne/aletheia/issues/2828)) ([a6a2291](https://github.com/iridyne/aletheia/commit/a6a2291396f4bf47db1990ac0aec6a7080078aef))
* **okx:** collapse contended symbol refreshes ([#2701](https://github.com/iridyne/aletheia/issues/2701)) ([71fb3d1](https://github.com/iridyne/aletheia/commit/71fb3d103687be038bb6bb91847b4a925900c066))
* **okx:** fail closed when demo symbols are unavailable ([#2646](https://github.com/iridyne/aletheia/issues/2646)) ([cdc8bd6](https://github.com/iridyne/aletheia/commit/cdc8bd60ca9190071d40c681ff426c64e3335c9b))
* **operator:** ignore invalid audit timestamps ([#2409](https://github.com/iridyne/aletheia/issues/2409)) ([bc22d6a](https://github.com/iridyne/aletheia/commit/bc22d6aa9ac4eaf700ae018a08b3a8e3f9c573b4))
* **operator:** query complete agent-run supervision facts ([#2831](https://github.com/iridyne/aletheia/issues/2831)) ([ecc2e20](https://github.com/iridyne/aletheia/commit/ecc2e20c93504af49f48cd2fc2366c808d688ba5))
* **operator:** validate health-check payloads ([#2479](https://github.com/iridyne/aletheia/issues/2479)) ([1bee408](https://github.com/iridyne/aletheia/commit/1bee408a5a8baa50d40565c7743fa4253c785d0e))
* **options:** reject non-finite structure inputs ([#1798](https://github.com/iridyne/aletheia/issues/1798)) ([7783ff1](https://github.com/iridyne/aletheia/commit/7783ff1fe94d8c2dde894b8c363fa5f6e3716b6b))
* **paper-account:** record valid losses below cash ([#2469](https://github.com/iridyne/aletheia/issues/2469)) ([666ffcd](https://github.com/iridyne/aletheia/commit/666ffcdc3c1f897afba23867db37d51218d863a6))
* **paper-trader:** fail closed on ownership read errors ([#2193](https://github.com/iridyne/aletheia/issues/2193)) ([#2362](https://github.com/iridyne/aletheia/issues/2362)) ([8c98615](https://github.com/iridyne/aletheia/commit/8c9861579c76a110212879d459eb93fda87e4440))
* **pause:** disclose layered scheduler behavior ([#2393](https://github.com/iridyne/aletheia/issues/2393)) ([edfe4ec](https://github.com/iridyne/aletheia/commit/edfe4ecb9a8b077ba4ae2e18e87f613b3fc10873))
* **persona:** keep failed refreeze pending ([#2705](https://github.com/iridyne/aletheia/issues/2705)) ([4897c63](https://github.com/iridyne/aletheia/commit/4897c63a9fc14004bd5018c6f15b99e21fea4a03))
* **persona:** mark paper drawdown evidence unavailable ([#2704](https://github.com/iridyne/aletheia/issues/2704)) ([9a15c1b](https://github.com/iridyne/aletheia/commit/9a15c1bff52a4ab55155eddca7bfc020e15c93fa))
* **persona:** persist rollout invalidation fence ([#2819](https://github.com/iridyne/aletheia/issues/2819)) ([a350953](https://github.com/iridyne/aletheia/commit/a3509539d12e2967210f9783fae5ffc2fc6e93a0))
* **persona:** refreeze when drawdown evidence is unavailable ([#2649](https://github.com/iridyne/aletheia/issues/2649)) ([7e70fa9](https://github.com/iridyne/aletheia/commit/7e70fa9f0c06e0940b110831b8e75a7c46349309))
* **persona:** reject stale drawdown evidence ([#2709](https://github.com/iridyne/aletheia/issues/2709)) ([9a2529f](https://github.com/iridyne/aletheia/commit/9a2529f324acc733aea70a1c958413bad0d95c36))
* **portfolio:** confine raw position writes to the modules that own the table ([5f7ddb1](https://github.com/iridyne/aletheia/commit/5f7ddb180f848cc0bde4bdc07d2e84b5df5970ab))
* **portfolio:** keep account widgets mode scoped ([#2766](https://github.com/iridyne/aletheia/issues/2766)) ([#2805](https://github.com/iridyne/aletheia/issues/2805)) ([83eff8b](https://github.com/iridyne/aletheia/commit/83eff8bb8aa06eb6b9564237a845c725f697caf0))
* **portfolio:** reuse marks in persisted snapshots ([#2778](https://github.com/iridyne/aletheia/issues/2778)) ([eb12687](https://github.com/iridyne/aletheia/commit/eb1268716f78a42b91cf83f274ba3081e5e44ed4))
* **positions:** order market signals by instant ([#2721](https://github.com/iridyne/aletheia/issues/2721)) ([752eecb](https://github.com/iridyne/aletheia/commit/752eecb950d273d7385ab063915700863aa1f413)), closes [#2566](https://github.com/iridyne/aletheia/issues/2566)
* **prediction:** fail closed on unavailable preferences ([#2859](https://github.com/iridyne/aletheia/issues/2859)) ([6916680](https://github.com/iridyne/aletheia/commit/6916680204ee0989fa7e579eb5a695c8c78025b8))
* **prediction:** return latest probability history window ([#2522](https://github.com/iridyne/aletheia/issues/2522)) ([4f1b38c](https://github.com/iridyne/aletheia/commit/4f1b38cb3c246d5e7714ceac2244fd48fd94cda0))
* **prediction:** serialize category preference updates ([#2381](https://github.com/iridyne/aletheia/issues/2381)) ([d7136f4](https://github.com/iridyne/aletheia/commit/d7136f4ed9891c90e6869d4b3be0e0b4ea6846f9))
* preserve stale snapshots beyond page limit ([412cbc2](https://github.com/iridyne/aletheia/commit/412cbc2bdc85941cdedd7e609b03d76ce52f1332))
* preserve unified cache execution scope ([#2453](https://github.com/iridyne/aletheia/issues/2453)) ([89fa1c7](https://github.com/iridyne/aletheia/commit/89fa1c72fbd6b97d6d411a178ac3a33273581bf4))
* **price-feed:** reset stale event baselines ([#2341](https://github.com/iridyne/aletheia/issues/2341)) ([3932ea9](https://github.com/iridyne/aletheia/commit/3932ea9c2eac1515d36744f33dcc80ae377b5781))
* **price-feed:** retain frozen protection venue ([#2690](https://github.com/iridyne/aletheia/issues/2690)) ([465ea0d](https://github.com/iridyne/aletheia/commit/465ea0d417d70cf170116733091cb5d7be7280e6))
* **proposals:** commit decisions with audit facts ([#2501](https://github.com/iridyne/aletheia/issues/2501)) ([0837381](https://github.com/iridyne/aletheia/commit/08373816d7d812a936a345f66ebd5a9cf07d769b))
* **protection:** preserve trigger venue for close marks ([#2657](https://github.com/iridyne/aletheia/issues/2657)) ([82fcff6](https://github.com/iridyne/aletheia/commit/82fcff68f1ad58c85bf12f2fbe8d96900cce8da3))
* **protection:** require post-bar close marks ([#2824](https://github.com/iridyne/aletheia/issues/2824)) ([90b04ea](https://github.com/iridyne/aletheia/commit/90b04ea523f5c2a1b28b7fad5930995cffaa3221))
* **qualification:** enforce durable preregistration ([#1146](https://github.com/iridyne/aletheia/issues/1146)) ([#2796](https://github.com/iridyne/aletheia/issues/2796)) ([d75bc67](https://github.com/iridyne/aletheia/commit/d75bc677fb81e4ae36f9bb5eb11e4c504926757a))
* **qualification:** preserve explicit concentration caps ([#2500](https://github.com/iridyne/aletheia/issues/2500)) ([6ca6de7](https://github.com/iridyne/aletheia/commit/6ca6de76a4d8be1fe89df3f5f60b26bd5bb7150b))
* **qualification:** require local paper cohort ([#1671](https://github.com/iridyne/aletheia/issues/1671)) ([#2812](https://github.com/iridyne/aletheia/issues/2812)) ([b638e1c](https://github.com/iridyne/aletheia/commit/b638e1c6fb363af41df3a803fc4c088a490f25c0))
* **reconcile:** reject non-finite adoption economics ([#1493](https://github.com/iridyne/aletheia/issues/1493)) ([02a1625](https://github.com/iridyne/aletheia/commit/02a16256d48272370a340ebf6c5f36b1822731b9))
* recover event store jsonl tails safely ([#2637](https://github.com/iridyne/aletheia/issues/2637)) ([eab0b61](https://github.com/iridyne/aletheia/commit/eab0b61441a6e52f1dc2e0c50306cbfcb6082be2))
* **regime:** expire stale market regime authority ([#2464](https://github.com/iridyne/aletheia/issues/2464)) ([b09d2aa](https://github.com/iridyne/aletheia/commit/b09d2aae134e43a209c895460f86e72561950a92))
* reject invalid persisted entry fees ([#2636](https://github.com/iridyne/aletheia/issues/2636)) ([ebab290](https://github.com/iridyne/aletheia/commit/ebab2906058f81428e34e6b0c7df2ef9d6393a3d))
* reject non-native gateway execution ([#2635](https://github.com/iridyne/aletheia/issues/2635)) ([870b910](https://github.com/iridyne/aletheia/commit/870b910cdf9a2396ed93493482133364f617c408))
* repair 24 regressions the batches introduced, and revert one honesty loss ([cbef8f6](https://github.com/iridyne/aletheia/commit/cbef8f60e3b631a0c4a9942eab8ba1ef8adb80c0))
* report unavailable observation drift state ([9e11dda](https://github.com/iridyne/aletheia/commit/9e11dda20665b6fa68b7d328778a69573a8a9992))
* requalify terminal planning from confirmed structure (B1) ([582eafd](https://github.com/iridyne/aletheia/commit/582eafd4b5eb816c29ae04c5f88744070452ad53))
* require complete outcome cohorts ([#2787](https://github.com/iridyne/aletheia/issues/2787)) ([a25096b](https://github.com/iridyne/aletheia/commit/a25096b02d9ddca8e347d42bdb792d372feddbf2))
* restore Candidate Entry planning and canonical settlement ([#2884](https://github.com/iridyne/aletheia/issues/2884)) ([da49d49](https://github.com/iridyne/aletheia/commit/da49d49c7a60b784847c98594123f22772525419))
* restore candidate planning and canonical outcome settlement ([9bee404](https://github.com/iridyne/aletheia/commit/9bee404dddd75cad5820cfe1e11a4ac19c055b7e))
* **retention:** reject nonpositive cleanup windows ([#2658](https://github.com/iridyne/aletheia/issues/2658)) ([64efe4c](https://github.com/iridyne/aletheia/commit/64efe4c49922d6f7cd1a749822bc77250c5e8e8d))
* **risk:** bind proposal execution fields to entry plan ([62c3b6a](https://github.com/iridyne/aletheia/commit/62c3b6aae06f446dc3b2fe15681292c229afd221))
* **risk:** close entry plan proposal envelope ([3f89d0b](https://github.com/iridyne/aletheia/commit/3f89d0b10835a6fb9e5129c90a7af49520d9fbc2))
* **risk:** close entry plan proposal envelope ([#2560](https://github.com/iridyne/aletheia/issues/2560)) ([466e263](https://github.com/iridyne/aletheia/commit/466e263a1761c55be6273aacc36e52a186236911))
* **risk:** close symbol filter mode enum ([#2659](https://github.com/iridyne/aletheia/issues/2659)) ([3670b64](https://github.com/iridyne/aletheia/commit/3670b6473c7735e0171542fc60c15858e2b4c452))
* **risk:** fail closed on unaudited brake release ([#388](https://github.com/iridyne/aletheia/issues/388)) ([#2818](https://github.com/iridyne/aletheia/issues/2818)) ([5f470bb](https://github.com/iridyne/aletheia/commit/5f470bb673092752f5854222b60c858a5a7f8f98))
* **risk:** keep headroom unavailable without runtime mode ([#2377](https://github.com/iridyne/aletheia/issues/2377)) ([3ef4e7b](https://github.com/iridyne/aletheia/commit/3ef4e7bd72505b4fa2b9571a6f47efc42970cd23))
* **risk:** keep historical decisions out of current status ([#2472](https://github.com/iridyne/aletheia/issues/2472)) ([d88ba3c](https://github.com/iridyne/aletheia/commit/d88ba3cbc038c463f7e27f40942c9bccbf153f55))
* **risk:** make final filter evidence atomic ([#2782](https://github.com/iridyne/aletheia/issues/2782)) ([e63e0ea](https://github.com/iridyne/aletheia/commit/e63e0ea82ab4bc4b62e656feedb5633a897e7204))
* **risk:** preserve evaluated policy identity ([#1506](https://github.com/iridyne/aletheia/issues/1506)) ([9cdcdd5](https://github.com/iridyne/aletheia/commit/9cdcdd5f05d617e176b71d823ab781074e0d1e40))
* **risk:** reject future market snapshots ([#1567](https://github.com/iridyne/aletheia/issues/1567)) ([6ca6670](https://github.com/iridyne/aletheia/commit/6ca66705d9275a128deae33374518bed13d8a2ca))
* **risk:** reject invalid account exposure ([#2825](https://github.com/iridyne/aletheia/issues/2825)) ([2ac7c5e](https://github.com/iridyne/aletheia/commit/2ac7c5e9e21c444ed7de435e851cbed0fd22c3d3))
* **risk:** reject leveraged spot proposals ([#2630](https://github.com/iridyne/aletheia/issues/2630)) ([5e057cb](https://github.com/iridyne/aletheia/commit/5e057cb794417a54ffbe7913b5e5695c08b16579))
* **risk:** require USDT trading quotes ([#2826](https://github.com/iridyne/aletheia/issues/2826)) ([10b9c13](https://github.com/iridyne/aletheia/commit/10b9c138630bcb8c022733dbb1f7b2db4d2b2b71))
* **risk:** revalidate paper replay facts ([#2823](https://github.com/iridyne/aletheia/issues/2823)) ([0fcd756](https://github.com/iridyne/aletheia/commit/0fcd756c59578389c35b6194b7b99e97d2c0247c))
* **risk:** validate canonical policy authority ([#2751](https://github.com/iridyne/aletheia/issues/2751)) ([d5d29a9](https://github.com/iridyne/aletheia/commit/d5d29a99911260c50f788d3222b26ca58fbb5678))
* **runbook:** retire fabricated-outcome SQL from paper-unblock runbook ([#3001](https://github.com/iridyne/aletheia/issues/3001)) ([ae5376f](https://github.com/iridyne/aletheia/commit/ae5376fcdbc30374b0076ffb4d8bdf2696abc684))
* **runtime:** bind external session to process ([#2713](https://github.com/iridyne/aletheia/issues/2713)) ([9110c21](https://github.com/iridyne/aletheia/commit/9110c21d611939abd3bcd58ff2085c4db79a7ed6))
* **runtime:** centralize agent run lifecycle ownership ([#2953](https://github.com/iridyne/aletheia/issues/2953)) ([9606e2f](https://github.com/iridyne/aletheia/commit/9606e2fe89a7c4dc5a5b53d0385a6e14b2dabadd))
* **runtime:** keep auto-resume alive on config read failure ([#2651](https://github.com/iridyne/aletheia/issues/2651)) ([497e575](https://github.com/iridyne/aletheia/commit/497e575e48787589759a6cf7f1384bd1f024dcc8))
* **runtime:** move cost tracing off provider loop ([#2112](https://github.com/iridyne/aletheia/issues/2112)) ([#2368](https://github.com/iridyne/aletheia/issues/2368)) ([8236267](https://github.com/iridyne/aletheia/commit/82362673f6663c3fe52af4c3080f62bb704f0a0c))
* **runtime:** preserve and attribute market data freshness ([#2792](https://github.com/iridyne/aletheia/issues/2792)) ([821b960](https://github.com/iridyne/aletheia/commit/821b9609c856d5707b8b5e4225005d8f9eaaf326))
* **runtime:** require valid healthy HTTP readiness ([#2480](https://github.com/iridyne/aletheia/issues/2480)) ([4dc601f](https://github.com/iridyne/aletheia/commit/4dc601f2177f963a166b97fe96cb878ce8de99fc))
* **runtime:** retain auto-resume after recovery errors ([#2694](https://github.com/iridyne/aletheia/issues/2694)) ([9d98136](https://github.com/iridyne/aletheia/commit/9d98136827260453c3850c839d1a872beab3fc73))
* **runtime:** stop the pi heartbeat daemon path failing silently ([#2951](https://github.com/iridyne/aletheia/issues/2951)) ([9b128ff](https://github.com/iridyne/aletheia/commit/9b128ff800f031deafc5b77625a43eb2ec7b6e06))
* **scheduler:** count reconciliation failures per cycle ([#2698](https://github.com/iridyne/aletheia/issues/2698)) ([4438498](https://github.com/iridyne/aletheia/commit/4438498a3230f5691be35cb6c6a741ea4510a8a4))
* **scheduler:** honor Eastern equity market hours ([e56cb37](https://github.com/iridyne/aletheia/commit/e56cb37b002d6e5396915f48d899602b39de9c35))
* **scheduler:** isolate long maintenance IO ([#2827](https://github.com/iridyne/aletheia/issues/2827)) ([f7ac83a](https://github.com/iridyne/aletheia/commit/f7ac83a5ec1f01e9564b7f4ca407b96a4ea83f83))
* **scheduler:** preserve funding settler errors ([#2470](https://github.com/iridyne/aletheia/issues/2470)) ([584f876](https://github.com/iridyne/aletheia/commit/584f876eecae4a46f2bb58f7592b16391394c98a))
* **scheduler:** preserve pause across trigger changes ([#2662](https://github.com/iridyne/aletheia/issues/2662)) ([8f799be](https://github.com/iridyne/aletheia/commit/8f799be8b9758c66073deed979715658135309fe))
* **scheduler:** publish only complete initialization ([#2750](https://github.com/iridyne/aletheia/issues/2750)) ([53719eb](https://github.com/iridyne/aletheia/commit/53719eb466716fb6a215fe442cc54ea56bd14b1a))
* **scheduler:** report event mirror retry failures ([#2663](https://github.com/iridyne/aletheia/issues/2663)) ([c9db5d9](https://github.com/iridyne/aletheia/commit/c9db5d99d2fe44127221b8fca8afb0507a8512cf))
* **scheduler:** serialize paused trigger replacement ([#2693](https://github.com/iridyne/aletheia/issues/2693)) ([a432347](https://github.com/iridyne/aletheia/commit/a4323471fa8c22062b6e1dfca2a12aa15b75fd16))
* **secrets:** enforce private fallback file permissions ([#2455](https://github.com/iridyne/aletheia/issues/2455)) ([8a9dcf1](https://github.com/iridyne/aletheia/commit/8a9dcf1b3fe792623a4a23a40885b29c52c98c84))
* separate gateway warmup from update readiness ([#2641](https://github.com/iridyne/aletheia/issues/2641)) ([e2df006](https://github.com/iridyne/aletheia/commit/e2df006c1f2be3bedc470f7228b97dc81df7b543))
* **serenity:** cascade theme state to every active opportunity ([#1518](https://github.com/iridyne/aletheia/issues/1518)) ([#2738](https://github.com/iridyne/aletheia/issues/2738)) ([cd88888](https://github.com/iridyne/aletheia/commit/cd888889e2f3549f74e7fc6645034514ad155367))
* **serenity:** reject future success freshness ([#2665](https://github.com/iridyne/aletheia/issues/2665)) ([5ebaf21](https://github.com/iridyne/aletheia/commit/5ebaf21ae241331b5eb6ca823ccc542a38b4462e))
* **serenity:** select fresh success below future bound ([#2700](https://github.com/iridyne/aletheia/issues/2700)) ([3c3f31e](https://github.com/iridyne/aletheia/commit/3c3f31ed8d18a304d611479c5d2f4e060a41a4fb))
* **settings:** clear stale profile error on retry ([#2429](https://github.com/iridyne/aletheia/issues/2429)) ([1b6e9ea](https://github.com/iridyne/aletheia/commit/1b6e9eac98fa93f02b65f8dc2727622c81f51f4a))
* **settings:** distinguish broker read failures ([#2404](https://github.com/iridyne/aletheia/issues/2404)) ([993810f](https://github.com/iridyne/aletheia/commit/993810f86deb995648c1286e3e75bffa6de12cc0))
* **settings:** expose pi daemon runtime apply failures ([#2505](https://github.com/iridyne/aletheia/issues/2505)) ([4fb7a5b](https://github.com/iridyne/aletheia/commit/4fb7a5bf93c1d56cd0b67386737e956c0d364bb2))
* **settings:** guard BrowserRouter navigation ([#2830](https://github.com/iridyne/aletheia/issues/2830)) ([d4fb377](https://github.com/iridyne/aletheia/commit/d4fb37790f7ef32f3c783fc5f0870d33f11d3d94))
* **settings:** reject unusable enabled webhook URLs ([#2647](https://github.com/iridyne/aletheia/issues/2647)) ([946b78e](https://github.com/iridyne/aletheia/commit/946b78e2be1f64ea45a7daa985158af98e68be08))
* **settings:** serialize exchange mode changes ([#2380](https://github.com/iridyne/aletheia/issues/2380)) ([e1700eb](https://github.com/iridyne/aletheia/commit/e1700ebfbc72761b2314c45b1df5daf0a063bc38))
* **specs:** clear remaining 5 of post-[#2945](https://github.com/iridyne/aletheia/issues/2945) spec red (10→5 failures) ([#3000](https://github.com/iridyne/aletheia/issues/3000)) ([509c99f](https://github.com/iridyne/aletheia/commit/509c99f47606b833aeaba369e78d1aebb7e6bdc1))
* **specs:** finish the post-[#2945](https://github.com/iridyne/aletheia/issues/2945) spec realignment on top of [#2974](https://github.com/iridyne/aletheia/issues/2974) ([#2999](https://github.com/iridyne/aletheia/issues/2999)) ([4857f36](https://github.com/iridyne/aletheia/commit/4857f369e9eaeddac896e4463869b4ca6cbc6c85))
* **specs:** realign the specs [#2945](https://github.com/iridyne/aletheia/issues/2945) left behind with the contracts that own them ([#2974](https://github.com/iridyne/aletheia/issues/2974)) ([0445b80](https://github.com/iridyne/aletheia/commit/0445b80f25190bfb8504021b58e1ed70bb12fdfe))
* **specs:** retire experiments.py + policy_epochs.py from OEO reference ceiling ([d77ce18](https://github.com/iridyne/aletheia/commit/d77ce189366d759bedd93f4f3337ae9e08a34e12))
* **specs:** seed the frozen UTA account identity in historical chain recovery ([7adcc8b](https://github.com/iridyne/aletheia/commit/7adcc8becdef1d482fdb35c9bdf142f7fdb2c63a))
* **spec:** use unique spot leverage claim id ([#2631](https://github.com/iridyne/aletheia/issues/2631)) ([888df7d](https://github.com/iridyne/aletheia/commit/888df7d21cc60a1baabe4ccc859dc4d28cda5040))
* **storage:** accept valid online backups in runtime retirement ([#2964](https://github.com/iridyne/aletheia/issues/2964)) ([88efe9a](https://github.com/iridyne/aletheia/commit/88efe9a8479c8ab17a6ab409ca00cd65536c95ab))
* **storage:** accept valid online backups in runtime retirement ([#2964](https://github.com/iridyne/aletheia/issues/2964)) ([#2965](https://github.com/iridyne/aletheia/issues/2965)) ([8e2cf4a](https://github.com/iridyne/aletheia/commit/8e2cf4a09be84e1937b32ecdcdde82502da15532))
* **storage:** align filter outcome upsert with its real unique key ([#2949](https://github.com/iridyne/aletheia/issues/2949)) ([8c0c0f2](https://github.com/iridyne/aletheia/commit/8c0c0f2f6a535be8e536efa3776b89b37b4016e4))
* **storage:** allow retire_runtime_continuity_tables to discover backup ([#2962](https://github.com/iridyne/aletheia/issues/2962)) ([d0acb5f](https://github.com/iridyne/aletheia/commit/d0acb5fdff0e0237395e189d90b526fc57d9fbf1))
* **storage:** allow retire_runtime_continuity_tables to discover backup ([#2962](https://github.com/iridyne/aletheia/issues/2962)) ([#2963](https://github.com/iridyne/aletheia/issues/2963)) ([9acd90c](https://github.com/iridyne/aletheia/commit/9acd90c9fc6607855f6bd0aef2b74f98a1e568a0))
* **storage:** distinguish experiment retention floor state ([f52d3d0](https://github.com/iridyne/aletheia/commit/f52d3d0ccb9c396310140cfbbc6a169d0c7fc0cb))
* **storage:** distinguish experiment retention floor state ([#2752](https://github.com/iridyne/aletheia/issues/2752)) ([cc73dc3](https://github.com/iridyne/aletheia/commit/cc73dc3eb3aab963c2a776728ac8a658b3076bac))
* **storage:** isolate concurrent artifact staging ([#2526](https://github.com/iridyne/aletheia/issues/2526)) ([4515914](https://github.com/iridyne/aletheia/commit/45159140229c88a1226340b508ccf34d9f92ef95))
* **sync:** preserve per-row import columns ([#2529](https://github.com/iridyne/aletheia/issues/2529)) ([f06b286](https://github.com/iridyne/aletheia/commit/f06b2867c45f097fcd7a34bfe65cbe457f81878d))
* **telegram:** honor current inbound authorization ([#2743](https://github.com/iridyne/aletheia/issues/2743)) ([59f3e6e](https://github.com/iridyne/aletheia/commit/59f3e6e6821f43e0e609ac94b14c751db17d4abd))
* **telegram:** preserve polling across update failures ([#2745](https://github.com/iridyne/aletheia/issues/2745)) ([1a9f552](https://github.com/iridyne/aletheia/commit/1a9f5523b7e61af537a99b30c2574e87186c8339))
* **telegram:** render notifications as plain text ([#2559](https://github.com/iridyne/aletheia/issues/2559)) ([a1ee2a0](https://github.com/iridyne/aletheia/commit/a1ee2a057b1f85925c6e0aab85b7301cd7a998ff))
* **telegram:** validate delivery before deduplication ([#2746](https://github.com/iridyne/aletheia/issues/2746)) ([4bea3cd](https://github.com/iridyne/aletheia/commit/4bea3cd2ccab073e7485b1d33d9b942c0965b208))
* tolerate recovered systemd restart jobs ([#2643](https://github.com/iridyne/aletheia/issues/2643)) ([1441804](https://github.com/iridyne/aletheia/commit/1441804d59d74e17529608de0dccc7c5bb7a8dfd))
* **ui:** enable sticky cockpit rail on Overview and clarify observation sample state in Universe ([4250733](https://github.com/iridyne/aletheia/commit/4250733af662a45070f76dbf1f3155484905975f))
* **ui:** keep stopped scheduler out of operational state ([#2379](https://github.com/iridyne/aletheia/issues/2379)) ([2ba81d7](https://github.com/iridyne/aletheia/commit/2ba81d763fedb83525e6599a70111e617b4852ad))
* **ui:** preserve CausalChain read states ([#2528](https://github.com/iridyne/aletheia/issues/2528)) ([575e142](https://github.com/iridyne/aletheia/commit/575e142e34340c96eff9858d84fb304536a88043))
* **ui:** unblock sticky table headers across cards using modern CSS overflow clip ([a6b8c9a](https://github.com/iridyne/aletheia/commit/a6b8c9a4a5cc603b8f3edcb54164ce11e17d12e8))
* **universe:** allow missing legacy opportunity context ([#2837](https://github.com/iridyne/aletheia/issues/2837)) ([54749cb](https://github.com/iridyne/aletheia/commit/54749cbc930af1012a70db9222cb9f8952fec9b0))
* **universe:** bind feature fetches to market clients ([#2739](https://github.com/iridyne/aletheia/issues/2739)) ([0234818](https://github.com/iridyne/aletheia/commit/02348188676cde2e936569e3c4fea4625fac80d4))
* **universe:** close candidate qualification review gaps ([#2842](https://github.com/iridyne/aletheia/issues/2842)) ([0715581](https://github.com/iridyne/aletheia/commit/071558122f87bf08fc1ba870b31cd2f8829f59f4))
* **universe:** converge candidate selection semantics ([687c88b](https://github.com/iridyne/aletheia/commit/687c88bf4a83cd87ca046ed845437d49d0040088))
* **universe:** converge candidate selection semantics ([#2806](https://github.com/iridyne/aletheia/issues/2806)) ([94abf64](https://github.com/iridyne/aletheia/commit/94abf6420a3458d164bffe6aa75302a0b336379f))
* **universe:** couple Learning exposure to publication ([#2736](https://github.com/iridyne/aletheia/issues/2736)) ([103535e](https://github.com/iridyne/aletheia/commit/103535ea8e962aa1c1b1aac06aba28ffe18dc720))
* **universe:** enforce frozen simple momentum cohort ([#2629](https://github.com/iridyne/aletheia/issues/2629)) ([8fa9c77](https://github.com/iridyne/aletheia/commit/8fa9c778a9431b332c61a5399cc2722f58a2eda8))
* **universe:** fail closed on outcome publication errors ([#2457](https://github.com/iridyne/aletheia/issues/2457)) ([7341278](https://github.com/iridyne/aletheia/commit/7341278aba51ce59522a0a22b33c6fa446c1d137))
* **universe:** isolate market bootstrap failures ([#2790](https://github.com/iridyne/aletheia/issues/2790)) ([1a0dfa4](https://github.com/iridyne/aletheia/commit/1a0dfa43cbbf00680316e215fba6ae10beefe528))
* **universe:** preserve Bitget ticker funding observation ([#3013](https://github.com/iridyne/aletheia/issues/3013)) ([9fe0636](https://github.com/iridyne/aletheia/commit/9fe063611495791ce78b51561490940cc96dbc25))
* **universe:** reject future market snapshots ([#1445](https://github.com/iridyne/aletheia/issues/1445)) ([#2507](https://github.com/iridyne/aletheia/issues/2507)) ([aaa1898](https://github.com/iridyne/aletheia/commit/aaa18988cfe3653793e2ec5a58eb9e8c3a33b816))
* **universe:** show current Snapshot Candidates ([#2634](https://github.com/iridyne/aletheia/issues/2634)) ([102a0be](https://github.com/iridyne/aletheia/commit/102a0be6bc3a7547f17e608aa555fb57bd3d5880))
* **universe:** skip ineligible candidates in planning ([63d5f4d](https://github.com/iridyne/aletheia/commit/63d5f4d5ba688f36ff6f05051729c80bf385d3d8))
* **universe:** use independent candidate utility units ([#2829](https://github.com/iridyne/aletheia/issues/2829)) ([c3a9ecc](https://github.com/iridyne/aletheia/commit/c3a9ecc6dd8fa9e76f95d275607c8e6f6ee82fd1))
* **universe:** use live derivatives receipt times ([#2783](https://github.com/iridyne/aletheia/issues/2783)) ([c57962e](https://github.com/iridyne/aletheia/commit/c57962ef3de332a9d156da12d88565d6efdab462))
* **update:** compose packaged stage timeout budget ([#2776](https://github.com/iridyne/aletheia/issues/2776)) ([#2807](https://github.com/iridyne/aletheia/issues/2807)) ([cabc72e](https://github.com/iridyne/aletheia/commit/cabc72e28187570c01a6125c934b0b1a8e858cf9))
* **update:** make candidate preflight failures diagnosable and bounded ([0aeba83](https://github.com/iridyne/aletheia/commit/0aeba83b18e7b50a87872e21ef2b416af224ffc7))
* **update:** reconcile timed-out git mutations ([#2777](https://github.com/iridyne/aletheia/issues/2777)) ([48ab138](https://github.com/iridyne/aletheia/commit/48ab13866f6a4da715bee83c48a0bae12c05585d))
* **update:** reuse active interpreter for candidate preflight ([#2960](https://github.com/iridyne/aletheia/issues/2960)) ([b03fe3d](https://github.com/iridyne/aletheia/commit/b03fe3d8947444923ec89f8c81c3b4bceb307b35))
* **update:** reuse active interpreter for candidate preflight ([#2960](https://github.com/iridyne/aletheia/issues/2960)) ([#2961](https://github.com/iridyne/aletheia/issues/2961)) ([6257f9d](https://github.com/iridyne/aletheia/commit/6257f9d502f375836a6626ae035255de758511f5))
* **update:** separate readiness polling from continuity capture ([#2863](https://github.com/iridyne/aletheia/issues/2863)) ([3ecbc6a](https://github.com/iridyne/aletheia/commit/3ecbc6ab05235a9a72921acbfc2b1cf6828efdcb))
* **update:** settle Observation data before activation ([#2857](https://github.com/iridyne/aletheia/issues/2857)) ([934417d](https://github.com/iridyne/aletheia/commit/934417dc8044dd84709268e54eebbdaee6821be8))
* **update:** transact schema-bearing commit updates ([#2726](https://github.com/iridyne/aletheia/issues/2726)) ([#2727](https://github.com/iridyne/aletheia/issues/2727)) ([403b17e](https://github.com/iridyne/aletheia/commit/403b17e82645a5632712413476f26402c7f426c3))
* **update:** use target-fresh readiness probes ([#2781](https://github.com/iridyne/aletheia/issues/2781)) ([358d44f](https://github.com/iridyne/aletheia/commit/358d44fd4ad6a592cbc9a007aa595fd1062f09ed))
* **uta:** align rejected close recovery state ([#2441](https://github.com/iridyne/aletheia/issues/2441)) ([76b164c](https://github.com/iridyne/aletheia/commit/76b164c058f61069363083cca6f2b1fc5d51b85b))
* **uta:** bind mutation qualification to sending adapter ([#2722](https://github.com/iridyne/aletheia/issues/2722)) ([8ecfdbe](https://github.com/iridyne/aletheia/commit/8ecfdbec649be5eb0d24179fd560c315a8bb9e1c))
* **uta:** cover OKX archived funding bills ([#2407](https://github.com/iridyne/aletheia/issues/2407)) ([de924e0](https://github.com/iridyne/aletheia/commit/de924e04aaae7f83fabbaab52797c9082e346a41))
* **webhook:** expose sanitized delivery outcomes ([#2749](https://github.com/iridyne/aletheia/issues/2749)) ([a2bd4b5](https://github.com/iridyne/aletheia/commit/a2bd4b5f2d75e00d74c25998a43a3e47248c4bc5))


### Performance

* **storage:** hardened SQLite PRAGMA baseline with workload-fit tuning ([445db78](https://github.com/iridyne/aletheia/commit/445db78e87b24c4435fe6008fda7805b21b83ac7))
* **storage:** hardened SQLite PRAGMA baseline with workload-fit tuning ([#2878](https://github.com/iridyne/aletheia/issues/2878)) ([3685e8d](https://github.com/iridyne/aletheia/commit/3685e8d43e07008c1b343cae7fa8ccf89210acf8))
* **update:** minimize Observation settlement hot path ([#2872](https://github.com/iridyne/aletheia/issues/2872)) ([2678d2c](https://github.com/iridyne/aletheia/commit/2678d2cbdb6f8d97cb729a5a3a1b17d6c6771c3d))
* **update:** skip recovery backup when target has no pending migration ([483b272](https://github.com/iridyne/aletheia/commit/483b272d2f3a180f5cb6dd579756684efed15449))
* **update:** skip recovery backup when target has no pending migration ([#2882](https://github.com/iridyne/aletheia/issues/2882)) ([e5a1ff4](https://github.com/iridyne/aletheia/commit/e5a1ff465f2efd16420815e51e8e453743b4153e))

## [0.8.4](https://github.com/iridyne/aletheia/compare/v0.8.3...v0.8.4) (2026-08-27)


### Features

* **entry:** converge Candidate planning and maker-first execution ([#2288](https://github.com/iridyne/aletheia/issues/2288)) ([1a96418](https://github.com/iridyne/aletheia/commit/1a96418ac2793744f41937a1916d6e2741f628c6))
* **market-data:** add causal market structure provider ([#2309](https://github.com/iridyne/aletheia/issues/2309)) ([abdfbec](https://github.com/iridyne/aletheia/commit/abdfbecab65e3e93090ab85234ead8d53ae19be1))
* **uta:** add Phase 1 read-only CCXT shadow profiles ([#2307](https://github.com/iridyne/aletheia/issues/2307)) ([d54957c](https://github.com/iridyne/aletheia/commit/d54957c986f98afd9725ee9540add43ef4ffb9b8))
* **uta:** realize unified account contract v1 ([#2305](https://github.com/iridyne/aletheia/issues/2305)) ([bdd4931](https://github.com/iridyne/aletheia/commit/bdd4931c815c9a7e20c1cb35069e016c133376f2))


### Bug Fixes

* **frontend:** gate source update apply action ([#2248](https://github.com/iridyne/aletheia/issues/2248)) ([9d440ba](https://github.com/iridyne/aletheia/commit/9d440ba04bf87a21f294b22bd6f4e28457d5f1db))
* **learning:** isolate filter evidence cost cohorts ([#2199](https://github.com/iridyne/aletheia/issues/2199)) ([d96fe9f](https://github.com/iridyne/aletheia/commit/d96fe9f2a312485d16cdf9f86f81b667b0bb7120))
* **learning:** preserve draft skill lineage ([#2245](https://github.com/iridyne/aletheia/issues/2245)) ([fe2c7a0](https://github.com/iridyne/aletheia/commit/fe2c7a0a3bbf809180c6b6b6489fedfeb390a25e))
* **learning:** retry incomplete startup catchup ([#2256](https://github.com/iridyne/aletheia/issues/2256)) ([227bbf5](https://github.com/iridyne/aletheia/commit/227bbf55a4559e759a67237a7507db31b51ea3cf))

## [0.8.3](https://github.com/iridyne/aletheia/compare/v0.8.2...v0.8.3) (2026-08-27)


### Features

* **analytics:** whole-policy profitability qualification for frozen cohort — [#988](https://github.com/iridyne/aletheia/issues/988) ([84ac3f4](https://github.com/iridyne/aletheia/commit/84ac3f434ad6cd1e9073b7448c447e46fc7ebf18))
* **analytics:** whole-policy profitability qualification framework — [#988](https://github.com/iridyne/aletheia/issues/988) ([#1132](https://github.com/iridyne/aletheia/issues/1132)) ([88f5477](https://github.com/iridyne/aletheia/commit/88f54771fa23454db890272e5d7ea3b61ab8c5f0))
* **ci:** add CI yield baseline collector ([#2172](https://github.com/iridyne/aletheia/issues/2172)) ([346c5c2](https://github.com/iridyne/aletheia/commit/346c5c2ea2b2ef66529edd24b021aeb20e108478))
* **ci:** add same-head metadata preflight ([#2170](https://github.com/iridyne/aletheia/issues/2170)) ([ba1d50e](https://github.com/iridyne/aletheia/commit/ba1d50e9b7d8532eabd6cc54932b8c222ea7b1d0))
* **ci:** drop Connector gate — CI pass is sufficient for auto-merge ([#1177](https://github.com/iridyne/aletheia/issues/1177)) ([3879bdf](https://github.com/iridyne/aletheia/commit/3879bdf8d4223416dbf8865dcd1667793cbca86d))
* **cli:** add aletheia uninstall command + fix git_ops missing import os ([11e29c0](https://github.com/iridyne/aletheia/commit/11e29c07b9a83c7b3b1b1cf8567ab923a3f33d41))
* **cli:** add global aletheia command installer and first-run hint ([#1200](https://github.com/iridyne/aletheia/issues/1200)) ([6061b12](https://github.com/iridyne/aletheia/commit/6061b12b69afc52b0981b6463e877760fa3ca582))
* **data:** add free crypto options market structure support (GEX/gamma) — [#1026](https://github.com/iridyne/aletheia/issues/1026) ([#1154](https://github.com/iridyne/aletheia/issues/1154)) ([34cea1d](https://github.com/iridyne/aletheia/commit/34cea1d343da62016d16b23675a69a2a8f86ab50))
* **execution:** clipped Bitget demo closes for the venue match-iteration cap ([c3f417f](https://github.com/iridyne/aletheia/commit/c3f417f3730c21ca7d315c995c29267ec3a91449))
* **execution:** complete UTA v3 profile — enable bitget_profile=uta_v3 ([a9982b4](https://github.com/iridyne/aletheia/commit/a9982b4fb1f6f5705d353e7cfa0f01683b4c26fa))
* **execution:** explicit Bitget classic_v2 / uta_v3 broker profiles ([#1124](https://github.com/iridyne/aletheia/issues/1124)) ([77aadcc](https://github.com/iridyne/aletheia/commit/77aadcc5b8c20cb96f65ff42d02168c26cb9259a))
* **learning:** add minimal causal red-team hypothesis gate ([#1049](https://github.com/iridyne/aletheia/issues/1049)) ([1c3bcec](https://github.com/iridyne/aletheia/commit/1c3bcec4a6446827ab5e40dbdd51f8335fb84fce))
* **observation:** close deployment gates G3+G4+G5 — [#1072](https://github.com/iridyne/aletheia/issues/1072) ([#1122](https://github.com/iridyne/aletheia/issues/1122)) ([62d6b5e](https://github.com/iridyne/aletheia/commit/62d6b5eb0edda28446510aebc67468ff752ca70c))
* **observation:** close Packet-2 prep gates G1+G2 — [#1072](https://github.com/iridyne/aletheia/issues/1072) ([#1123](https://github.com/iridyne/aletheia/issues/1123)) ([cd19038](https://github.com/iridyne/aletheia/commit/cd190389984ab261b717e613e16d0e9a810f1de7))
* **observation:** detach Observation cycle execution ownership — [#1072](https://github.com/iridyne/aletheia/issues/1072) ([#1126](https://github.com/iridyne/aletheia/issues/1126)) ([27d751f](https://github.com/iridyne/aletheia/commit/27d751f5aa5c364f006cc16fe2fdf0ea71292ffb))
* **observation:** ExecutionManifest + path-agnostic resolver — [#1072](https://github.com/iridyne/aletheia/issues/1072) Cluster 3 ([#1118](https://github.com/iridyne/aletheia/issues/1118)) ([d745dde](https://github.com/iridyne/aletheia/commit/d745dde8303d7fe6c4495651869b353444111a08))
* **observation:** joint app.db + lab.db + manifest checkpoint — [#1072](https://github.com/iridyne/aletheia/issues/1072) Cluster 5 ([#1120](https://github.com/iridyne/aletheia/issues/1120)) ([9f26bfb](https://github.com/iridyne/aletheia/commit/9f26bfb5228ea01a1d17ce36f5b3ec377defc97d))
* **observation:** multi-window bounded Lab runner — [#1072](https://github.com/iridyne/aletheia/issues/1072) Cluster 2 ([#1117](https://github.com/iridyne/aletheia/issues/1117)) ([b4ec946](https://github.com/iridyne/aletheia/commit/b4ec946048f86e821caa1b62e16adaa3d5cc5583))
* **observation:** Observation Lab v1 — filter POC + UFC Packet 1 shadow-equivalence ([#1072](https://github.com/iridyne/aletheia/issues/1072)) ([#1083](https://github.com/iridyne/aletheia/issues/1083)) ([a72a0d3](https://github.com/iridyne/aletheia/commit/a72a0d35a97d17327e557d04ba524a7f57913666))
* **observation:** schedule UFC shadow-equivalence in observe cycle — [#1072](https://github.com/iridyne/aletheia/issues/1072) Cluster 1 ([#1116](https://github.com/iridyne/aletheia/issues/1116)) ([8153d93](https://github.com/iridyne/aletheia/commit/8153d93346178927f6871729447ce757a164bc7c))
* **observation:** typed exposure fact + manifest-conflict resolver — [#1072](https://github.com/iridyne/aletheia/issues/1072) Cluster 4 ([#1119](https://github.com/iridyne/aletheia/issues/1119)) ([a037e6a](https://github.com/iridyne/aletheia/commit/a037e6abb6591635dccb1da323bb4a4c9cf1fb8a))
* **operator:** add apex operational observatory anomaly signals ([#1140](https://github.com/iridyne/aletheia/issues/1140) Packet 0) ([#1289](https://github.com/iridyne/aletheia/issues/1289)) ([8154d18](https://github.com/iridyne/aletheia/commit/8154d182d1e17d90275d4e20205cc7c7b36a3cb9))
* **operator:** replace free-form AI leverage with deterministic post-debate resolver — [#835](https://github.com/iridyne/aletheia/issues/835) ([#1138](https://github.com/iridyne/aletheia/issues/1138)) ([3c9a519](https://github.com/iridyne/aletheia/commit/3c9a519d992e55940bc56ad85d4627707785c425))
* **overview:** clarify equity and PnL curves ([#1056](https://github.com/iridyne/aletheia/issues/1056)) ([0ba3e88](https://github.com/iridyne/aletheia/commit/0ba3e88746486e7a4248712cdcc5f19f700b8c67))
* **profit-loop:** granular cost-evidence rejection codes — [#987](https://github.com/iridyne/aletheia/issues/987) ([#1131](https://github.com/iridyne/aletheia/issues/1131)) ([19f61bc](https://github.com/iridyne/aletheia/commit/19f61bc880c81376db6b1e338e29c6c272b16675))
* **release:** automate release and paper deployment ([#1043](https://github.com/iridyne/aletheia/issues/1043)) ([34648b0](https://github.com/iridyne/aletheia/commit/34648b05413c88f7635e0ad6f422fb8b8bf8d20f))
* **universe:** consolidate entry TTL + emit_dedup config schema (W0) ([#1326](https://github.com/iridyne/aletheia/issues/1326)) ([eebde57](https://github.com/iridyne/aletheia/commit/eebde57eb8a1e4c462397a3dd884522b7d75ebe5))
* **universe:** horizon-separated evidence for UFC candidate nomination — [#958](https://github.com/iridyne/aletheia/issues/958) ([#1129](https://github.com/iridyne/aletheia/issues/1129)) ([8af8f35](https://github.com/iridyne/aletheia/commit/8af8f3522dc603232b8725ea7260d173d471a446))
* **universe:** shadow momentum-short exhaustion signal for direction-aware admission observation — [#956](https://github.com/iridyne/aletheia/issues/956) ([44abff6](https://github.com/iridyne/aletheia/commit/44abff67a7d973954eb569a9c7dd343648ba6d2e))
* **universe:** shadow momentum-short exhaustion signal for direction-aware admission observation — [#956](https://github.com/iridyne/aletheia/issues/956) ([#1136](https://github.com/iridyne/aletheia/issues/1136)) ([1e7cf30](https://github.com/iridyne/aletheia/commit/1e7cf307f9524a2300213b0a7c28ca2b5e27aaef))
* **update:** add Braille TUI spinner with phase progress to aletheia update ([09be8d6](https://github.com/iridyne/aletheia/commit/09be8d69c6ac5cd6423d1afce0105de2c8a5c9f2))
* **update:** Braille TUI spinner with phase progress ([#1264](https://github.com/iridyne/aletheia/issues/1264)) ([25ed577](https://github.com/iridyne/aletheia/commit/25ed577fa8a2b8133ba3a91e4691418675661e8b))
* **update:** make source updates Observer-aware — [#1135](https://github.com/iridyne/aletheia/issues/1135) ([31747ae](https://github.com/iridyne/aletheia/commit/31747ae75775062beccb60d69fae2c23ac1e1cfe))


### Bug Fixes

* 1238: expose profit-loop diagnostic query failures as unavailable, never zero ([#1354](https://github.com/iridyne/aletheia/issues/1354)) ([1e8f88b](https://github.com/iridyne/aletheia/commit/1e8f88bd6ad1c8c2b3753b9fb069a8f99ae1525b)), closes [#1238](https://github.com/iridyne/aletheia/issues/1238)
* **accounting:** neutralize invalid paper close cash ([#2182](https://github.com/iridyne/aletheia/issues/2182)) ([#2188](https://github.com/iridyne/aletheia/issues/2188)) ([3916558](https://github.com/iridyne/aletheia/commit/3916558be1d5c6912ca8adcf080350db754906ef))
* **accounting:** project inspect positions pnl ([#2225](https://github.com/iridyne/aletheia/issues/2225)) ([985185c](https://github.com/iridyne/aletheia/commit/985185cab137c511cb63d5f22a9fabf24c1c8436))
* **account:** label portfolio read failures ([#2261](https://github.com/iridyne/aletheia/issues/2261)) ([aab5d5a](https://github.com/iridyne/aletheia/commit/aab5d5a61c9075991a63f12d58b660123efebf84))
* **account:** preserve unified sync metadata ([f3e269d](https://github.com/iridyne/aletheia/commit/f3e269df0a59ae7cc04c0cdebae3ac7616bb8cb5))
* **account:** reject future exchange cache timestamps ([#2255](https://github.com/iridyne/aletheia/issues/2255)) ([5e963a1](https://github.com/iridyne/aletheia/commit/5e963a1ca3614462ae1c79c92d544d3a767e98fd))
* **admission:** atomic priority replacement (Issue [#1235](https://github.com/iridyne/aletheia/issues/1235)) ([5e3feb9](https://github.com/iridyne/aletheia/commit/5e3feb9e4f3709199ae75a172aa1d681b88fb534))
* **admission:** guard manual prediction observer starts ([#2051](https://github.com/iridyne/aletheia/issues/2051)) ([7ed81e9](https://github.com/iridyne/aletheia/commit/7ed81e9f34cedb377131eb2a331e846e1d4e3927))
* **admission:** guard/settings validation, fail-closed enforcement ([#1215](https://github.com/iridyne/aletheia/issues/1215) [#1216](https://github.com/iridyne/aletheia/issues/1216) [#1218](https://github.com/iridyne/aletheia/issues/1218) [#1219](https://github.com/iridyne/aletheia/issues/1219) [#1221](https://github.com/iridyne/aletheia/issues/1221) [#1239](https://github.com/iridyne/aletheia/issues/1239) [#1244](https://github.com/iridyne/aletheia/issues/1244) [#1245](https://github.com/iridyne/aletheia/issues/1245)) ([#1372](https://github.com/iridyne/aletheia/issues/1372)) ([22a5f4b](https://github.com/iridyne/aletheia/commit/22a5f4b655d1fd203af64ac669f68a3b346a6c5e))
* **agent-shell:** incomplete entry plan raises instead of silent 1x fallback ([#1335](https://github.com/iridyne/aletheia/issues/1335)) ([6e5f267](https://github.com/iridyne/aletheia/commit/6e5f2677cbeb3ea1453014408a8977156b9d2a81))
* **agents-ui:** remove three unresolvable catalog entries and drive Dashboard from backend ([#1304](https://github.com/iridyne/aletheia/issues/1304)) ([5ae45ce](https://github.com/iridyne/aletheia/commit/5ae45ce0f4a8534fc479752254623c7f4a909ff2)), closes [#1168](https://github.com/iridyne/aletheia/issues/1168)
* **analytics:** apply candidate outcome temporal eligibility ([#2208](https://github.com/iridyne/aletheia/issues/2208)) ([4eaae14](https://github.com/iridyne/aletheia/commit/4eaae14cd7868f7c7d92d518eb38e0bd65a0e38a))
* **analytics:** keep candidate diagnosis read-only ([#2186](https://github.com/iridyne/aletheia/issues/2186)) ([8633e9a](https://github.com/iridyne/aletheia/commit/8633e9a4ad1c1316917dc84e96081d7a64214251))
* **analytics:** normalize temporal pnl buckets to utc ([#2232](https://github.com/iridyne/aletheia/issues/2232)) ([bf67d24](https://github.com/iridyne/aletheia/commit/bf67d2495f550396493395404eb234e9c3dd2e7d))
* **analytics:** preserve execution-mode identity across reports ([#2192](https://github.com/iridyne/aletheia/issues/2192)) ([243aca8](https://github.com/iridyne/aletheia/commit/243aca80d086c65eea01a858eb79b33085f0b12e))
* **analytics:** preserve trade insights market identity ([#2243](https://github.com/iridyne/aletheia/issues/2243)) ([1a8f9c6](https://github.com/iridyne/aletheia/commit/1a8f9c6b0871cfc73e9a3a2a298ccfaa6129ba43))
* **analytics:** profit-loop qualification, trade-insights, and diagnosis fixes ([#1249](https://github.com/iridyne/aletheia/issues/1249) [#1251](https://github.com/iridyne/aletheia/issues/1251) [#1254](https://github.com/iridyne/aletheia/issues/1254) [#1256](https://github.com/iridyne/aletheia/issues/1256) [#1259](https://github.com/iridyne/aletheia/issues/1259) [#1261](https://github.com/iridyne/aletheia/issues/1261) [#1269](https://github.com/iridyne/aletheia/issues/1269)) ([#1368](https://github.com/iridyne/aletheia/issues/1368)) ([134c35a](https://github.com/iridyne/aletheia/commit/134c35a8ccfa6345d29bd235f8a4b314299b9de3))
* **analytics:** scope profitability surfaces to one execution_mode ([#1939](https://github.com/iridyne/aletheia/issues/1939)) ([#2178](https://github.com/iridyne/aletheia/issues/2178)) ([1b052f2](https://github.com/iridyne/aletheia/commit/1b052f23e7fcafd389c97dc3156d5e2c94acfc73))
* **analytics:** unpack r_multiple_distribution + runtime array guards ([#2061](https://github.com/iridyne/aletheia/issues/2061) [#2063](https://github.com/iridyne/aletheia/issues/2063)) ([b57aaa0](https://github.com/iridyne/aletheia/commit/b57aaa06f63beef1ef89824c9d424f8126b13315))
* **analytics:** unpack r_multiple_distribution bins to list on wire ([#2061](https://github.com/iridyne/aletheia/issues/2061)) ([#2062](https://github.com/iridyne/aletheia/issues/2062)) ([524d707](https://github.com/iridyne/aletheia/commit/524d707c9daf2105330fc9774578623afb374701))
* **analytics:** use measurable hold time denominators ([#2262](https://github.com/iridyne/aletheia/issues/2262)) ([45c8431](https://github.com/iridyne/aletheia/commit/45c8431969ab6353d4dba9b09af5ea2308f4120f))
* **api:** clamp market snapshot limit to 1..1000 ([#1241](https://github.com/iridyne/aletheia/issues/1241)) ([#1362](https://github.com/iridyne/aletheia/issues/1362)) ([e67f152](https://github.com/iridyne/aletheia/commit/e67f152ce4a3a4f008df965c3023e5ec4ad752d6))
* **api:** convert 75 zero-await async route handlers to sync def ([#1188](https://github.com/iridyne/aletheia/issues/1188)) ([#1319](https://github.com/iridyne/aletheia/issues/1319)) ([486f62b](https://github.com/iridyne/aletheia/commit/486f62bdfea94aa5218b01aadf9e8cba8d154c26))
* **auth:** enforce owner auth on /api/ws via short-lived tickets ([#1288](https://github.com/iridyne/aletheia/issues/1288)) ([6b49fbe](https://github.com/iridyne/aletheia/commit/6b49fbe85e07f80bf4226cc3d43dd42faf8d2dac))
* **auth:** enforce remote owner-auth on frontdoor infra routes — [#1075](https://github.com/iridyne/aletheia/issues/1075) ([#1162](https://github.com/iridyne/aletheia/issues/1162)) ([b573c3c](https://github.com/iridyne/aletheia/commit/b573c3cf2f036cd3af302c1880191f805cfda4f0))
* backoff factor ge=1.0 ([#1357](https://github.com/iridyne/aletheia/issues/1357)), json_valid guard ([#1358](https://github.com/iridyne/aletheia/issues/1358)), CI runner contract doc ([#1356](https://github.com/iridyne/aletheia/issues/1356)) ([#1490](https://github.com/iridyne/aletheia/issues/1490)) ([c86e554](https://github.com/iridyne/aletheia/commit/c86e554cfe0ae6e6311d63a93479420770dcd595))
* **backup:** redact config credentials before archiving ([#1175](https://github.com/iridyne/aletheia/issues/1175)) ([#1311](https://github.com/iridyne/aletheia/issues/1311)) ([070dad9](https://github.com/iridyne/aletheia/commit/070dad90ae9336399743f6390b993804c84d4879))
* bind live unlock to selected exchange trust evidence ([#1050](https://github.com/iridyne/aletheia/issues/1050)) ([a97eaed](https://github.com/iridyne/aletheia/commit/a97eaed8ca4af5b949e988bddd274ac3b5463047))
* **blotter:** keep conviction deferrals out of Candidate Ledger ([#1054](https://github.com/iridyne/aletheia/issues/1054)) ([5561348](https://github.com/iridyne/aletheia/commit/5561348298111f7c97b5c6c3491cbb746517ca19))
* **capital:** read failures no longer render as zero deposits ([#1317](https://github.com/iridyne/aletheia/issues/1317)) ([c243ca7](https://github.com/iridyne/aletheia/commit/c243ca727114e2d1238507b2591f8c6a825c1a84))
* **capital:** reject non-finite amount_usdt at trust boundary ([#1316](https://github.com/iridyne/aletheia/issues/1316)) ([3fa8ca8](https://github.com/iridyne/aletheia/commit/3fa8ca82245069b2d7d02db896dff59f4b693639))
* **ci:** bound control-plane runner queue ([0cb46b7](https://github.com/iridyne/aletheia/commit/0cb46b7219a0f0770185423b606666d3452d269b))
* **ci:** recover metadata-blocked exact-head runs ([#2171](https://github.com/iridyne/aletheia/issues/2171)) ([afaf756](https://github.com/iridyne/aletheia/commit/afaf756f611dfe25e5dd8d709e6132451407b5a4))
* **ci:** repair master regression contracts ([#2159](https://github.com/iridyne/aletheia/issues/2159)) ([c6c70ca](https://github.com/iridyne/aletheia/commit/c6c70ca8929e80b70c0e3092c427dc9c783cae97))
* **ci:** resolve Change Contract exemptions from PR diff ([#2136](https://github.com/iridyne/aletheia/issues/2136)) ([a12519c](https://github.com/iridyne/aletheia/commit/a12519c2acce8cb9bc9b4204b7527d5797210378))
* **ci:** resolve cherry-pick gaps — skip_policy B1.9 constants, schema eligibility migration, fallback model names, settings types, ranker import ([dbdea78](https://github.com/iridyne/aletheia/commit/dbdea78214e3a462168302c75bf283c38cefdf4f))
* **ci:** RiskConfig null-eligibility migration, demo alias setattr, riskFields test policy ([8dd74ab](https://github.com/iridyne/aletheia/commit/8dd74abcfea31814bd921b704a3f4e4e531ddd3d))
* **cli:** window-bounded coverage counts for control status liveness probe ([462e337](https://github.com/iridyne/aletheia/commit/462e33713657317e12a1aff947b822dc75f0be4a))
* close linked issues after bot merge ([81e0c42](https://github.com/iridyne/aletheia/commit/81e0c420ba2a0702c8f2a56fbecb5965eba901ca))
* **coinalyze:** plan requested symbol coverage ([#2275](https://github.com/iridyne/aletheia/issues/2275)) ([81aae34](https://github.com/iridyne/aletheia/commit/81aae340a11da875891ba07d240010816c54f185))
* **coingecko:** preserve provider source time ([#2276](https://github.com/iridyne/aletheia/issues/2276)) ([41a0e7e](https://github.com/iridyne/aletheia/commit/41a0e7e15fd33cea7548d35b7873ab67ca626cc7))
* **collector-health:** register options_structure in the health domain registry ([#1297](https://github.com/iridyne/aletheia/issues/1297)) ([9a30cc9](https://github.com/iridyne/aletheia/commit/9a30cc99cfbdd9d61386dd9e24759cb895bcd5c0)), closes [#1160](https://github.com/iridyne/aletheia/issues/1160)
* **config:** add cross-process write lock to prevent lost-update race — [#1074](https://github.com/iridyne/aletheia/issues/1074) ([#1156](https://github.com/iridyne/aletheia/issues/1156)) ([9db55d9](https://github.com/iridyne/aletheia/commit/9db55d9a39701eb15b104c3cbf53bb18228dc133))
* **config:** move scheduler/loop bounds into Pydantic schema models ([#1350](https://github.com/iridyne/aletheia/issues/1350)) ([4c27f18](https://github.com/iridyne/aletheia/commit/4c27f187c2aee9d76f037bf3d99640828d83a864))
* **config:** use mutate_config for Settings writes to prevent silent overwrites ([#1347](https://github.com/iridyne/aletheia/issues/1347)) ([f927fab](https://github.com/iridyne/aletheia/commit/f927fab6a2ac7561abc1d5805dba65f78a1a7fb0))
* **control:** expose UFC, profit-loop, observer, and pause-owner truth ([#1066](https://github.com/iridyne/aletheia/issues/1066)) ([fed5e13](https://github.com/iridyne/aletheia/commit/fed5e13c2ffb64c7f6bee9802c26abaae7c72492))
* **counterfactual:** enforce single-epoch integrity on cross-candidate aggregations ([#1169](https://github.com/iridyne/aletheia/issues/1169)) ([#1306](https://github.com/iridyne/aletheia/issues/1306)) ([8ee7fb0](https://github.com/iridyne/aletheia/commit/8ee7fb03ce5292421c16edcd247004c8be0dae5f))
* **css:** finish canonical breakpoint and token migration ([#2046](https://github.com/iridyne/aletheia/issues/2046)) ([d775ac1](https://github.com/iridyne/aletheia/commit/d775ac11df4c512945f43742973d76623843f728))
* **defillama:** reject empty onchain aggregates ([#2277](https://github.com/iridyne/aletheia/issues/2277)) ([a3d986f](https://github.com/iridyne/aletheia/commit/a3d986f036f24b37191fcf9e584b0ed76ef65737))
* **demo:** stand down mean-reversion in sideways and widen noise-stop guards ([1e2fdba](https://github.com/iridyne/aletheia/commit/1e2fdba45e06305aeb8fc0ed920f4bc28be001e1))
* **docs:** align permission mode names and risk commands with CLI tree (issue [#1242](https://github.com/iridyne/aletheia/issues/1242)) ([#1364](https://github.com/iridyne/aletheia/issues/1364)) ([31affeb](https://github.com/iridyne/aletheia/commit/31affeb53c16f65f541d375d8d336e8e432ef44e))
* **docs:** resolve EX-04 dual-status contradiction in CAPABILITY_GROUND_TRUTH ([#1314](https://github.com/iridyne/aletheia/issues/1314)) ([5e5d079](https://github.com/iridyne/aletheia/commit/5e5d079c17f5c8938f7c6f4d1b869c121551ff3a)), closes [#1179](https://github.com/iridyne/aletheia/issues/1179)
* **doctor:** filter active agent runs from failures ([#2227](https://github.com/iridyne/aletheia/issues/2227)) ([11208b4](https://github.com/iridyne/aletheia/commit/11208b4d4ddc875053a829b27f9422a553451639))
* **economics:** compute session budget usage ([#2263](https://github.com/iridyne/aletheia/issues/2263)) ([b35e867](https://github.com/iridyne/aletheia/commit/b35e867dddd55a707397ee8e86a9e3bdc7e05d77))
* **economics:** fail closed pricing refresh admission ([#2216](https://github.com/iridyne/aletheia/issues/2216)) ([0cdf63e](https://github.com/iridyne/aletheia/commit/0cdf63e3f43fa893b6e265ea78415eb151cff7ca))
* **economics:** filter would-block scope before limit ([#2266](https://github.com/iridyne/aletheia/issues/2266)) ([1acd272](https://github.com/iridyne/aletheia/commit/1acd272068f1cf0db8f28c3d7a951d42546f7f18))
* **economics:** prefer exact pricing matches ([#2205](https://github.com/iridyne/aletheia/issues/2205)) ([8e2139d](https://github.com/iridyne/aletheia/commit/8e2139dc18b0c912c9acfa046535ed55030b5562))
* enforce non-overlapping profit gradient reconciliation ([#1051](https://github.com/iridyne/aletheia/issues/1051)) ([6eec6b9](https://github.com/iridyne/aletheia/commit/6eec6b94c5774e31d246456e63fdcfc7eabcbed4))
* **event-store:** lock jsonl mirror append ([#2274](https://github.com/iridyne/aletheia/issues/2274)) ([f623c57](https://github.com/iridyne/aletheia/commit/f623c5756f7c505d8367f648d0567d110d1b0fdd))
* **execution:** Bitget/Binance recovery paths — clientOid lookup, origClientOrderId, 7-day window ([#2006](https://github.com/iridyne/aletheia/issues/2006)) ([4f28dc7](https://github.com/iridyne/aletheia/commit/4f28dc75ccee96e86c6792242ed4dd34949fc7a9))
* **execution:** bound exchange close convergence ([#1065](https://github.com/iridyne/aletheia/issues/1065)) ([d8d7572](https://github.com/iridyne/aletheia/commit/d8d7572b1d41a78ec0d00c3af19f2afabfefc38e))
* **execution:** classify unknown adapter ack status ([#2213](https://github.com/iridyne/aletheia/issues/2213)) ([cf56e9a](https://github.com/iridyne/aletheia/commit/cf56e9a48139b1a216b57e67b0d5cea9f60bdc4e))
* **execution:** consolidate venue capability boundary ([#2189](https://github.com/iridyne/aletheia/issues/2189)) ([#2190](https://github.com/iridyne/aletheia/issues/2190)) ([0bda7d6](https://github.com/iridyne/aletheia/commit/0bda7d6eb4c4f683d7dad11db45e9a7ea10295c1))
* **execution:** fill materialization fail-closed — non-finite values, BGB fees, explicit rejection, late ack ([#2004](https://github.com/iridyne/aletheia/issues/2004)) ([f551fe4](https://github.com/iridyne/aletheia/commit/f551fe4225f80e6fb08eb27f11dffeac53430d48))
* **execution:** full-quantity marketable-limit closes; converge completed clips on replay ([3921ddd](https://github.com/iridyne/aletheia/commit/3921ddd950ee870dfaa2b9b8c08ffdfe78252c78))
* **execution:** gate complete-close on Position quantity ([#2145](https://github.com/iridyne/aletheia/issues/2145)) ([#2154](https://github.com/iridyne/aletheia/issues/2154)) ([0edd9d1](https://github.com/iridyne/aletheia/commit/0edd9d178cbda1da0a0cd2c19333169a321a55b7))
* **execution:** keep exchange I/O outside recovery lock ([#2197](https://github.com/iridyne/aletheia/issues/2197)) ([4785ccc](https://github.com/iridyne/aletheia/commit/4785ccc0893fad0eac9b9767982abf04e837176d))
* **execution:** marketable-limit close fallback for symbol-level demo market-path failure ([917e721](https://github.com/iridyne/aletheia/commit/917e7219410e6e17a84f6ca64115b368edbc92a7))
* **execution:** owner unlock also covers unreached-venue submission-rejected closes ([5af208e](https://github.com/iridyne/aletheia/commit/5af208e3a9b32f6fdf90c3c5edbbf95bdd69b561))
* **execution:** owner-authorized release for Bitget 40725-parked close claims ([e1391ce](https://github.com/iridyne/aletheia/commit/e1391ce5a9f5ccbb166a2d9ddd2d6fac71e05aa5))
* **execution:** preserve reconciled broker ack winners ([#1553](https://github.com/iridyne/aletheia/issues/1553), [#2044](https://github.com/iridyne/aletheia/issues/2044)) ([#2102](https://github.com/iridyne/aletheia/issues/2102)) ([9adbace](https://github.com/iridyne/aletheia/commit/9adbace4fb0071a7251545400dd2670f128beb56))
* **execution:** reject invalid paper cost settings ([#2191](https://github.com/iridyne/aletheia/issues/2191)) ([6cbf51b](https://github.com/iridyne/aletheia/commit/6cbf51b988a32aed55fbdea0a9655e400eaca92b))
* **execution:** reject malformed private fill batches ([#2069](https://github.com/iridyne/aletheia/issues/2069)) ([032f1b0](https://github.com/iridyne/aletheia/commit/032f1b094aa42cb6831e27d1cb8927bd8261224a))
* **execution:** reject when loaded venue registry has no requested market_type ([#1352](https://github.com/iridyne/aletheia/issues/1352)) ([f4226ec](https://github.com/iridyne/aletheia/commit/f4226ecc0ee2cebfc8c25aae0e0037ea11bbc63d))
* **execution:** require live-unlock authorization on every new live Proposal — [#1004](https://github.com/iridyne/aletheia/issues/1004) ([#1125](https://github.com/iridyne/aletheia/issues/1125)) ([a7991de](https://github.com/iridyne/aletheia/commit/a7991de981275c7d96d29510f35aa356475369b4))
* expose model health observation freshness ([#1069](https://github.com/iridyne/aletheia/issues/1069)) ([855669a](https://github.com/iridyne/aletheia/commit/855669a956d70582ba76fcf3caea09a1f63bc3de))
* **frontdoor:** readyz returns 503 when backend is unreachable ([#1330](https://github.com/iridyne/aletheia/issues/1330)) ([fae85ea](https://github.com/iridyne/aletheia/commit/fae85eab218efc248cdcdfb6b6fe4054da2da092))
* frontend UX issues – prediction link, polling guard, dirty nav ([#1186](https://github.com/iridyne/aletheia/issues/1186) [#1189](https://github.com/iridyne/aletheia/issues/1189) [#1191](https://github.com/iridyne/aletheia/issues/1191) partial [#1187](https://github.com/iridyne/aletheia/issues/1187) [#1192](https://github.com/iridyne/aletheia/issues/1192)) ([#1397](https://github.com/iridyne/aletheia/issues/1397)) ([1303c37](https://github.com/iridyne/aletheia/commit/1303c3720e88d1f428870c9991b78650fe41161a))
* **frontend:** clear remaining lint warnings ([#2038](https://github.com/iridyne/aletheia/issues/2038)) ([59404d6](https://github.com/iridyne/aletheia/commit/59404d657b3f650f95e9e7918992626e74193ab4))
* **frontend:** isolate chart tests and show route loading ([#2039](https://github.com/iridyne/aletheia/issues/2039)) ([0fe4690](https://github.com/iridyne/aletheia/commit/0fe4690da8673b211d6f6d2fce7625150963a246))
* **frontend:** make operator mutations fail visibly ([#1079](https://github.com/iridyne/aletheia/issues/1079)) ([c72a262](https://github.com/iridyne/aletheia/commit/c72a262799d7fe6c83a662c7f713e667998cc827))
* **frontend:** preserve legacy route tab identity ([#2242](https://github.com/iridyne/aletheia/issues/2242)) ([3635218](https://github.com/iridyne/aletheia/commit/36352182b29bfa29264e8a733b73788a44666f24))
* **frontend:** preserve serenity prediction link ids ([ee8edc5](https://github.com/iridyne/aletheia/commit/ee8edc50b0f99aac1566673b78228a28a73708dc)), closes [#1938](https://github.com/iridyne/aletheia/issues/1938)
* **frontend:** render failed queries as unavailable instead of empty ([#1224](https://github.com/iridyne/aletheia/issues/1224)) ([#1339](https://github.com/iridyne/aletheia/issues/1339)) ([4a1f1c4](https://github.com/iridyne/aletheia/commit/4a1f1c4b5717205ba352374dd1338a4c98fb76bd))
* **frontend:** restore StatusBar component dropped in merge + align PnLZone "Today" metric ([388d031](https://github.com/iridyne/aletheia/commit/388d03165871e09211199f9482703029b2fb13ba))
* **frontend:** serialize paper account actions ([#2247](https://github.com/iridyne/aletheia/issues/2247)) ([dceb770](https://github.com/iridyne/aletheia/commit/dceb77081f479b18581ab156a368139af5fa0f26))
* **gateway:** fail status when front door is unavailable ([3f33537](https://github.com/iridyne/aletheia/commit/3f33537aacb916215db9c395cb9c1a8780af26ae))
* **gateway:** reconcile_fills passes venue_trade_id as venue, missing known fills ([#1295](https://github.com/iridyne/aletheia/issues/1295)) ([40df6cc](https://github.com/iridyne/aletheia/commit/40df6cca687e49083ece816299aa8566e160fb59)), closes [#1115](https://github.com/iridyne/aletheia/issues/1115)
* **gateway:** verify machine service readiness ([658afd4](https://github.com/iridyne/aletheia/commit/658afd4085a1e7e90163d6a7a804551309e90bf2))
* get_order_fills uta_v3 routing + _safe_position_float + short_bias sideways penalty ([d3e41cb](https://github.com/iridyne/aletheia/commit/d3e41cb5302041ceeb1e6b369bf2a9581242b41c))
* **go-live-scorecard:** scope outcome queries to current execution_mode ([#1279](https://github.com/iridyne/aletheia/issues/1279)) ([5e9cdd7](https://github.com/iridyne/aletheia/commit/5e9cdd700a25de38e5ab8eef83dea117fa8a4491))
* **governance:** align PR template with Change Contract gate ([#2136](https://github.com/iridyne/aletheia/issues/2136)) ([674f3f3](https://github.com/iridyne/aletheia/commit/674f3f3d49100bc0ed0b6367dffeecc342b79fc0))
* **governance:** converge Change Contract template and CI exemptions ([#2144](https://github.com/iridyne/aletheia/issues/2144)) ([703e4a0](https://github.com/iridyne/aletheia/commit/703e4a0046262cfb0a08f7d41f107a03f3669416))
* **guard:** reject nonpositive matching-trade thresholds ([#1337](https://github.com/iridyne/aletheia/issues/1337)) ([03d121f](https://github.com/iridyne/aletheia/commit/03d121fdb6923a55ee1a497676f6672bc70b879d))
* **inspect:** filter query results before limit ([#2278](https://github.com/iridyne/aletheia/issues/2278)) ([7b83bfe](https://github.com/iridyne/aletheia/commit/7b83bfe4f3db57f421698ff8aecef39932503a08))
* **inspect:** format factor win rates as percentages ([#2234](https://github.com/iridyne/aletheia/issues/2234)) ([af96599](https://github.com/iridyne/aletheia/commit/af965998efc52b60ff3c4a1939914764fe7d03be))
* **inspect:** label today pnl sample scope ([4d38172](https://github.com/iridyne/aletheia/commit/4d38172eb0565bfe5b22136f29c202919d7aed8d)), closes [#1935](https://github.com/iridyne/aletheia/issues/1935)
* **inspect:** report exchange account availability ([#2231](https://github.com/iridyne/aletheia/issues/2231)) ([9d628ae](https://github.com/iridyne/aletheia/commit/9d628ae29e1a4619ca572b41d081363da1201123))
* **inspect:** report invalid strategy parameters ([#2233](https://github.com/iridyne/aletheia/issues/2233)) ([eb48e52](https://github.com/iridyne/aletheia/commit/eb48e52f31b58b0f0670f612e78c3b5ed4005783))
* **inspect:** use account equity snapshot fields ([#2235](https://github.com/iridyne/aletheia/issues/2235)) ([5403a7d](https://github.com/iridyne/aletheia/commit/5403a7d78ec547f1192c3f8b8e6433a424e55558))
* **knowledge:** serialize semantic dedup writes ([ce90d17](https://github.com/iridyne/aletheia/commit/ce90d17dae67e8147ab25439eb5f96918fb065ab))
* **learning:** align measurement catchup with cadence ([#2085](https://github.com/iridyne/aletheia/issues/2085)) ([abb3932](https://github.com/iridyne/aletheia/commit/abb39329e2cdb0e0268c61dbe7832c75fca31bcf))
* **learning:** compare memo cohorts by instant ([#2204](https://github.com/iridyne/aletheia/issues/2204)) ([2bb68fc](https://github.com/iridyne/aletheia/commit/2bb68fc22a81c907991e214499ae5fa91e5e7a06))
* **learning:** couple outcome prune audit to delete ([#2230](https://github.com/iridyne/aletheia/issues/2230)) ([43fd5eb](https://github.com/iridyne/aletheia/commit/43fd5ebbcbc3796d4ae9bde3ea57212e387e8418))
* **learning:** experiment-state read errors now fail closed ([#1231](https://github.com/iridyne/aletheia/issues/1231)) ([8a91987](https://github.com/iridyne/aletheia/commit/8a91987f386d03f0133c2bfdb4999f308556c0e9))
* **learning:** gate alpha monitoring on current LCO evidence ([#2211](https://github.com/iridyne/aletheia/issues/2211)) ([a76399f](https://github.com/iridyne/aletheia/commit/a76399f50b36bfe2954794242b24b08d9a1f1b9d))
* **learning:** key trade cases by position identity ([#2251](https://github.com/iridyne/aletheia/issues/2251)) ([0f61cb2](https://github.com/iridyne/aletheia/commit/0f61cb238e2ca87537cd9e2f3b49b46e52d955e0))
* **learning:** lifecycle allocation fail-closed, concurrent transition guard ([#2032](https://github.com/iridyne/aletheia/issues/2032)) ([bbb0beb](https://github.com/iridyne/aletheia/commit/bbb0bebc56dbd5aaed255d34ad306fade657bd21))
* **learning:** memo rotation window, effectiveness cohort, regime feedback, attribution errors ([#1263](https://github.com/iridyne/aletheia/issues/1263) [#1978](https://github.com/iridyne/aletheia/issues/1978) [#1981](https://github.com/iridyne/aletheia/issues/1981) [#1248](https://github.com/iridyne/aletheia/issues/1248)) ([#2026](https://github.com/iridyne/aletheia/issues/2026)) ([ed79bfa](https://github.com/iridyne/aletheia/commit/ed79bfa5cc487a93e9967b667895acf38939aec7))
* **learning:** order measurement verdicts by exact instant ([#2202](https://github.com/iridyne/aletheia/issues/2202)) ([38f2741](https://github.com/iridyne/aletheia/commit/38f2741b27f3c64d9485ce8a4a1f1975a2b94a4e))
* **learning:** prioritize current outcome horizons ([#2206](https://github.com/iridyne/aletheia/issues/2206)) ([3bca9e2](https://github.com/iridyne/aletheia/commit/3bca9e2605bf24bd429ecfcd31dce04662e0e492))
* **learning:** reject temporally invalid candidate outcomes ([#2079](https://github.com/iridyne/aletheia/issues/2079)) ([0da191d](https://github.com/iridyne/aletheia/commit/0da191d5ec6086c0ab409b17b3c282731c9def63))
* **learning:** reject unknown outcome market identities ([#1913](https://github.com/iridyne/aletheia/issues/1913)) ([#2103](https://github.com/iridyne/aletheia/issues/2103)) ([cd7a632](https://github.com/iridyne/aletheia/commit/cd7a63253ba359cf488c32c50aec6e8edd19d31c))
* **learning:** restore missing return and fix 3-tuple mocks ([#1246](https://github.com/iridyne/aletheia/issues/1246) followup) ([d9c8226](https://github.com/iridyne/aletheia/commit/d9c8226c4f8991217b01ec2d4daa50c6ea30ddf9))
* **learning:** retire idle experiments safely ([#1058](https://github.com/iridyne/aletheia/issues/1058)) ([5f52e79](https://github.com/iridyne/aletheia/commit/5f52e79925caaec2cc136fca95722b02e5167cfd))
* **learning:** rotate epoch on blacklist changes ([#2072](https://github.com/iridyne/aletheia/issues/2072)) ([c5a6979](https://github.com/iridyne/aletheia/commit/c5a6979597e2aca540470206a56d89df740756bf))
* **learning:** share candidate evidence as-of ([362874e](https://github.com/iridyne/aletheia/commit/362874efdca60d95a61655801cd282f1042954e3))
* **learning:** surface case-builder failures to AgentRun health ([#1246](https://github.com/iridyne/aletheia/issues/1246)) ([#1361](https://github.com/iridyne/aletheia/issues/1361)) ([dc72e02](https://github.com/iridyne/aletheia/commit/dc72e02ab7a6e29d6725a8f343c46ba00c4ae75a))
* **lint:** replace forward-ref string annotations with object in atomic_replace_queued_carrier ([254f550](https://github.com/iridyne/aletheia/commit/254f5501e4f1a36b0f83c1e9ca7d3c250c9ee4d8))
* **lint:** resolve three ruff F821 undefined-name errors ([cd28814](https://github.com/iridyne/aletheia/commit/cd288145827dd0be349a969be91c0012d48b7fd1))
* **llm:** route trade JSON to glm-5.2 and enable json_object mode ([9257858](https://github.com/iridyne/aletheia/commit/9257858cf264ba75c5906fc59bac769e7e623f65))
* **llm:** treat HTTP 500 and missing chat choices as fallback-eligible ([c4419b5](https://github.com/iridyne/aletheia/commit/c4419b55a0c41f5d050b0716ab44271e6b04e107))
* **llm:** try flash before glm for structured trade JSON ([d8965eb](https://github.com/iridyne/aletheia/commit/d8965eb9848ce35ebb8f7cb9b2bb8c0f986462fd))
* **macro:** keep empty source ticks unavailable ([#2271](https://github.com/iridyne/aletheia/issues/2271)) ([40c7842](https://github.com/iridyne/aletheia/commit/40c7842c28cb4c58dd74a2b6c9e9e530533dc0bf))
* **mandate:** exclude open durations from compliance ([5dc98c5](https://github.com/iridyne/aletheia/commit/5dc98c5f1b32c3da49207dc341317dc5c69f768b))
* **mandate:** verify proposal hash before compliance evidence ([#2246](https://github.com/iridyne/aletheia/issues/2246)) ([5d61223](https://github.com/iridyne/aletheia/commit/5d612232de623a4fb1d6824f5dd5c69569ca6251))
* **market-data:** add exchange dimension to derivatives backfill sparse/dedup checks ([#1312](https://github.com/iridyne/aletheia/issues/1312)) ([014274e](https://github.com/iridyne/aletheia/commit/014274efaff3763fdd40b39bf9cbf3358c5c2ae8))
* **market-data:** add options_structure to T1 source taxonomy ([#1159](https://github.com/iridyne/aletheia/issues/1159)) ([#1296](https://github.com/iridyne/aletheia/issues/1296)) ([2f68200](https://github.com/iridyne/aletheia/commit/2f68200de3713f4e35eed0899ae0251e6be65d9d))
* **market-data:** apply fail-closed freshness gate to options_structure feed ([#1294](https://github.com/iridyne/aletheia/issues/1294)) ([a119b13](https://github.com/iridyne/aletheia/commit/a119b13cc10dfe3bb0c4205ec9f83e661681012f)), closes [#1158](https://github.com/iridyne/aletheia/issues/1158)
* **market-data:** preserve identical ohlcv provenance ([#2200](https://github.com/iridyne/aletheia/issues/2200)) ([f14665f](https://github.com/iridyne/aletheia/commit/f14665f9d242fea7637bdae14cdc1198f2402ee8))
* **model-provider:** admit fallback under physical provider ([#2196](https://github.com/iridyne/aletheia/issues/2196)) ([0483346](https://github.com/iridyne/aletheia/commit/04833467eee5b6f61608ba847a1a0aef5520283c))
* **model-provider:** type policy-reload timeouts as InferencePolicyReload — [#710](https://github.com/iridyne/aletheia/issues/710) ([#1153](https://github.com/iridyne/aletheia/issues/1153)) ([78a072b](https://github.com/iridyne/aletheia/commit/78a072b6443322801828cc7e6a06304634bea74d))
* **models:** accept completed ping envelopes ([#2218](https://github.com/iridyne/aletheia/issues/2218)) ([57cc4ed](https://github.com/iridyne/aletheia/commit/57cc4ed88b24ae221985b78591e44501866b8934))
* **models:** async Agent paths now refresh cached expired OAuth tokens ([#1276](https://github.com/iridyne/aletheia/issues/1276)) ([#1331](https://github.com/iridyne/aletheia/issues/1331)) ([cfc8ec6](https://github.com/iridyne/aletheia/commit/cfc8ec6a18992e94e9d1e1e8d8a0fdf1bc3d8686))
* **model:** separate configured, reachable, verified, and unavailable states ([#1071](https://github.com/iridyne/aletheia/issues/1071)) ([f8f1516](https://github.com/iridyne/aletheia/commit/f8f15165f6b639ee6a67ec95c566256a739c7e8e))
* **models:** make fallback health transitions atomic ([#2214](https://github.com/iridyne/aletheia/issues/2214)) ([4c1bf5d](https://github.com/iridyne/aletheia/commit/4c1bf5d96abc0576dd9453f5539328850291cd6c))
* **models:** reject empty text completions ([#2220](https://github.com/iridyne/aletheia/issues/2220)) ([435cd0b](https://github.com/iridyne/aletheia/commit/435cd0b8c8ebbc3177b4bbe89756a8677943c136))
* **models:** retain failed inference identity ([#2219](https://github.com/iridyne/aletheia/issues/2219)) ([4823941](https://github.com/iridyne/aletheia/commit/48239413d0aae514fd44df2f47decef8e300286c))
* **models:** run cli providers off selector loop ([#2215](https://github.com/iridyne/aletheia/issues/2215)) ([f0540d0](https://github.com/iridyne/aletheia/commit/f0540d08118ce583cf5a152a83468d2d9a708b58))
* **models:** separate budget denials from provider health ([#2264](https://github.com/iridyne/aletheia/issues/2264)) ([02ad97c](https://github.com/iridyne/aletheia/commit/02ad97ca70cfb850269e96c58dfd020392262e69))
* **models:** validate profile protocols ([#2224](https://github.com/iridyne/aletheia/issues/2224)) ([6dd2dd7](https://github.com/iridyne/aletheia/commit/6dd2dd7ea8a505425bd8a6000c794185898e605b))
* **net:** close discarded proxy response before direct fallback ([#1308](https://github.com/iridyne/aletheia/issues/1308)) ([0421f07](https://github.com/iridyne/aletheia/commit/0421f070815c2c13423b6af6d9c928e17d5aca3e)), closes [#1088](https://github.com/iridyne/aletheia/issues/1088)
* **net:** config-read failure raises NetworkPolicyUnavailable instead of permitting direct egress ([#1315](https://github.com/iridyne/aletheia/issues/1315)) ([e94b02a](https://github.com/iridyne/aletheia/commit/e94b02ac6c1eb95d6a4ecba0156a9c9817d6b8d7))
* **notifications:** decouple webhook delivery from Telegram gating ([#1336](https://github.com/iridyne/aletheia/issues/1336)) ([a170607](https://github.com/iridyne/aletheia/commit/a17060752b363ee42c151f95161086bae9e5b9a3))
* **observation:** 3 long-term-stability hardening items — [#1072](https://github.com/iridyne/aletheia/issues/1072) follow-up ([#1128](https://github.com/iridyne/aletheia/issues/1128)) ([e338b5a](https://github.com/iridyne/aletheia/commit/e338b5a355bc3ebc48edaf7a23e45fe24e3a5367))
* **observation:** config-read failure now returns owner_unavailable, not main ([#1147](https://github.com/iridyne/aletheia/issues/1147)) ([#1283](https://github.com/iridyne/aletheia/issues/1283)) ([31c034b](https://github.com/iridyne/aletheia/commit/31c034b4ed7886a6d46be3fc0d1fcd74e2cc3f09))
* **observation:** propagate evidence pass failures to CLI exit code and scheduler health ([#1240](https://github.com/iridyne/aletheia/issues/1240)) ([#1360](https://github.com/iridyne/aletheia/issues/1360)) ([e77afc5](https://github.com/iridyne/aletheia/commit/e77afc539c243273403613b0a6bb0cb2e4d2f521))
* **observation:** UFC equivalence only compares verdict, not sample counts — [#1072](https://github.com/iridyne/aletheia/issues/1072) ([#1127](https://github.com/iridyne/aletheia/issues/1127)) ([730de2a](https://github.com/iridyne/aletheia/commit/730de2a5cbc9509cef3cf4daa106ea699f85758c))
* **observer:** persist technical context provenance ([98b1270](https://github.com/iridyne/aletheia/commit/98b127056ae3aae00ef2420cde0f046f274eea51))
* **observer:** prefer open path buckets over closed-history recovery ([7ce6b77](https://github.com/iridyne/aletheia/commit/7ce6b772c42a0e6e5e772dbcf117c814a737df1d))
* **oom:** eliminate unbounded SQL table scans across analytics and CLI paths ([#1334](https://github.com/iridyne/aletheia/issues/1334)) ([1edd21f](https://github.com/iridyne/aletheia/commit/1edd21f020fc7dfe67a962770b813afabf9b6c1a))
* **operator-auth:** file store is authoritative for service token rotation ([#1287](https://github.com/iridyne/aletheia/issues/1287)) ([c5f3c06](https://github.com/iridyne/aletheia/commit/c5f3c06767aabcd00886a704ae12365a15751ae3))
* **operator:** align B2 evidence horizon with entry intent — [#954](https://github.com/iridyne/aletheia/issues/954) ([#1130](https://github.com/iridyne/aletheia/issues/1130)) ([ddf8751](https://github.com/iridyne/aletheia/commit/ddf87517215300b5e6284decbf1cedbbdf43a231))
* **operator:** converge System Operator / Apex Loop / Trading Decision terminology ([#1143](https://github.com/iridyne/aletheia/issues/1143)) ([#1290](https://github.com/iridyne/aletheia/issues/1290)) ([a47ec6d](https://github.com/iridyne/aletheia/commit/a47ec6d45f0fd9e73de1a948ae644cc804262d4d))
* **operator:** enforce a single system operator authority ([#1092](https://github.com/iridyne/aletheia/issues/1092)) ([b10f2a6](https://github.com/iridyne/aletheia/commit/b10f2a6d8aa45eb41ff885c6cbf63008747d7d5c))
* **operator:** keep local shell alive under owner auth ([#1097](https://github.com/iridyne/aletheia/issues/1097)) ([0815220](https://github.com/iridyne/aletheia/commit/081522093a0f4b4145c0345475b8bc3b35bb7580))
* **operator:** per-process credentials prevent live-shell respawn from invalidating concurrent shadows ([#1112](https://github.com/iridyne/aletheia/issues/1112)) ([2b7c939](https://github.com/iridyne/aletheia/commit/2b7c93998324a0dab442ecafd9512078d617aaa8))
* **operator:** raise structured token budget for assess/reassess/debate to 4096 ([56c33dd](https://github.com/iridyne/aletheia/commit/56c33dd061f094e2c2e5ac4aa6e623d958390ad9))
* **operator:** require authoritative post-action verification before market scan task completion ([#1281](https://github.com/iridyne/aletheia/issues/1281)) ([7a4d356](https://github.com/iridyne/aletheia/commit/7a4d3561dc08e5bf4cd0a4f49d83bb2a4476be3c))
* **operator:** restrict service credential to non-behavior agents ([#1100](https://github.com/iridyne/aletheia/issues/1100)) ([#1286](https://github.com/iridyne/aletheia/issues/1286)) ([2e34a8e](https://github.com/iridyne/aletheia/commit/2e34a8e193c2982083f38664fce31b55ec0a1f76))
* **operator:** use explicit file list in npm test to remove shell glob dependency ([#1110](https://github.com/iridyne/aletheia/issues/1110)) ([53da9ff](https://github.com/iridyne/aletheia/commit/53da9ff3616b9ffa5a38e1bed903932c8395bd5c)), closes [#1107](https://github.com/iridyne/aletheia/issues/1107)
* **opportunity:** persist feature snapshot before setup motive ([#2292](https://github.com/iridyne/aletheia/issues/2292)) ([88cfeb9](https://github.com/iridyne/aletheia/commit/88cfeb9378309f044e2713bbee5b3ae2010578f8))
* **options-math:** replace cumulative-strike gamma flip with true spot-sweep GEX zero ([#1299](https://github.com/iridyne/aletheia/issues/1299)) ([3c0fded](https://github.com/iridyne/aletheia/commit/3c0fdedfac41b4d0d6d18ee029317e30d0dc0972))
* outcome settler fail-closed, future timestamp refreeze, concurrent close guard ([#2100](https://github.com/iridyne/aletheia/issues/2100)) ([7ed602b](https://github.com/iridyne/aletheia/commit/7ed602bf1f0bd94109e8d48b26407cb00b7830e8))
* **overview:** address equity curve review findings ([#1057](https://github.com/iridyne/aletheia/issues/1057)) ([0cdad53](https://github.com/iridyne/aletheia/commit/0cdad53cedb4c2966adb1a3dedd9b79497d65e7c))
* **overview:** guard risk all-clear behind successful bootstrap data ([#1318](https://github.com/iridyne/aletheia/issues/1318)) ([286df29](https://github.com/iridyne/aletheia/commit/286df2997e859da8ac7f4005adec4177a8cc0fcc)), closes [#1184](https://github.com/iridyne/aletheia/issues/1184)
* **overview:** restore semantic honesty for unknown/loading states — [#689](https://github.com/iridyne/aletheia/issues/689) ([#1152](https://github.com/iridyne/aletheia/issues/1152)) ([98777b9](https://github.com/iridyne/aletheia/commit/98777b9869304476f5a25712aa25d4a71c17c59d))
* **persistence:** re-raise IntegrityError when idempotency_key had no winner ([#1291](https://github.com/iridyne/aletheia/issues/1291)) ([5e02954](https://github.com/iridyne/aletheia/commit/5e02954310d2a017d991cdfb6e95d84609ba93d1))
* **persona:** make progressive unlock durable and evidence-gated ([#1052](https://github.com/iridyne/aletheia/issues/1052)) ([f8d8365](https://github.com/iridyne/aletheia/commit/f8d8365052eee4dc18ec964bd8f1543001a61a42)), closes [#409](https://github.com/iridyne/aletheia/issues/409)
* **pi-daemon:** startup gate exception now fails closed instead of permissive pass ([#1284](https://github.com/iridyne/aletheia/issues/1284)) ([d9b3e1b](https://github.com/iridyne/aletheia/commit/d9b3e1b8978cf916c6c7000a46815e90d8f5e1c1)), closes [#1103](https://github.com/iridyne/aletheia/issues/1103)
* **pi:** report daemon liveness from the gateway process ([9176e85](https://github.com/iridyne/aletheia/commit/9176e85ece87932c7994df742584279ba8ad6828))
* **pnl:** scalar today_realized_pnl fails closed when any close is incomplete ([#1365](https://github.com/iridyne/aletheia/issues/1365)) ([b7e87d3](https://github.com/iridyne/aletheia/commit/b7e87d3d82bafa36f8c690da40539fff4597ba0c))
* **pnl:** use exact (venue, symbol, market_type) mark for scheduler persist ([#1174](https://github.com/iridyne/aletheia/issues/1174)) ([#1310](https://github.com/iridyne/aletheia/issues/1310)) ([7dcf857](https://github.com/iridyne/aletheia/commit/7dcf857f3fb1407ac4ac29070b0dda08f0800c61))
* **portfolio:** compare snapshot windows by instant ([#2269](https://github.com/iridyne/aletheia/issues/2269)) ([2ca0faa](https://github.com/iridyne/aletheia/commit/2ca0faa93aa6e9be99bd27b7236255700a66dd60))
* **portfolio:** direct-query failsafe before reconciled_gone administrative close ([84c7a83](https://github.com/iridyne/aletheia/commit/84c7a83c648802b16d8cf4793596c22bfd400b89))
* **portfolio:** fail closed mixed symbol pnl ([#2268](https://github.com/iridyne/aletheia/issues/2268)) ([efc7559](https://github.com/iridyne/aletheia/commit/efc7559408c944115cacde12e58758cd023b17ee))
* **portfolio:** preserve zero symbol unrealized pnl ([#2260](https://github.com/iridyne/aletheia/issues/2260)) ([c7ce93a](https://github.com/iridyne/aletheia/commit/c7ce93a522994cc2b8ba5c9a5bee60a27b09be27))
* **portfolio:** qualify exchange unrealized PnL identity ([#1060](https://github.com/iridyne/aletheia/issues/1060)) ([2c0eec6](https://github.com/iridyne/aletheia/commit/2c0eec60f0f1edfb2d69cb8dff7da587f2f1b277))
* **positions:** reject future market signal evidence ([#2257](https://github.com/iridyne/aletheia/issues/2257)) ([3128f14](https://github.com/iridyne/aletheia/commit/3128f1446b78b5d757c488b7e75e7bf0b38c7f0e))
* preserve pending close rationale through settlement ([#2088](https://github.com/iridyne/aletheia/issues/2088)) ([42985c2](https://github.com/iridyne/aletheia/commit/42985c2fef0812d8b101e443213248a44837a709))
* **profit-loop-qualification:** enforce preregistration contract for cutoff and required fields ([#1282](https://github.com/iridyne/aletheia/issues/1282)) ([bc074f7](https://github.com/iridyne/aletheia/commit/bc074f745987fab54eb604ef31d85035d09eb818))
* **profit-loop-qualification:** enforce runtime_session_id identity filter on cohort selection ([642ab67](https://github.com/iridyne/aletheia/commit/642ab67915891dde86b1170ad580f09f61af36b6))
* **profit-loop-qualification:** enforce runtime_session_id identity filter on cohort selection ([ae80f79](https://github.com/iridyne/aletheia/commit/ae80f793df4655d529426034c1fc2f860d6b269e)), closes [#1145](https://github.com/iridyne/aletheia/issues/1145)
* **profit-loop:** bound candidate facts to evidence epoch ([#2203](https://github.com/iridyne/aletheia/issues/2203)) ([5163a4f](https://github.com/iridyne/aletheia/commit/5163a4fd1c96fc704598fee6b0dbaa41bed87e3c))
* **profit-loop:** evidence integrity fixes for candidate_facts and position_replay ([#1366](https://github.com/iridyne/aletheia/issues/1366)) ([9341473](https://github.com/iridyne/aletheia/commit/9341473503995b602a2c140a24d7a28f429ebd29))
* **profit-loop:** make Profit Gradient evidence fail closed ([#2177](https://github.com/iridyne/aletheia/issues/2177)) ([c5f3c07](https://github.com/iridyne/aletheia/commit/c5f3c07fe5b70bd0ac133c53d27d6ad85cd432e9))
* **profit-loop:** validate and isolate outcome economics ([#2104](https://github.com/iridyne/aletheia/issues/2104)) ([daac506](https://github.com/iridyne/aletheia/commit/daac50673562391e2e9c35d4dcecb3e9b881ca1c))
* **proposal:** submit prepared paper intent ([#2270](https://github.com/iridyne/aletheia/issues/2270)) ([8bd6d9a](https://github.com/iridyne/aletheia/commit/8bd6d9a0fb691e8d61df8ab770b4dec68f8d7585))
* **proposal:** validate exit intent before position lifecycle ([#2253](https://github.com/iridyne/aletheia/issues/2253)) ([fabf094](https://github.com/iridyne/aletheia/commit/fabf094823d6a184b22097d954fb1def173b25c3))
* **provenance:** service-delegated agent runs now persist as operator_service, not manual ([#1285](https://github.com/iridyne/aletheia/issues/1285)) ([32b8b2e](https://github.com/iridyne/aletheia/commit/32b8b2ee672f1b857d20978d49b59424977ea755)), closes [#1102](https://github.com/iridyne/aletheia/issues/1102)
* **qualification:** exclude generated_at from content_hash so reruns of frozen snapshots are hash-stable ([#1292](https://github.com/iridyne/aletheia/issues/1292)) ([458e8d3](https://github.com/iridyne/aletheia/commit/458e8d32850f6daff9ecdfc0f572514e039b81e7)), closes [#1149](https://github.com/iridyne/aletheia/issues/1149)
* **qualification:** zero-loss cohort passes profit_factor gate ([#1148](https://github.com/iridyne/aletheia/issues/1148)) ([bba9ae7](https://github.com/iridyne/aletheia/commit/bba9ae70d4cc6012657c0a8f3835162ad2a1320c))
* **qualification:** zero-loss cohort passes profit_factor gate ([#1148](https://github.com/iridyne/aletheia/issues/1148)) ([#1293](https://github.com/iridyne/aletheia/issues/1293)) ([d175795](https://github.com/iridyne/aletheia/commit/d1757957b637cf91eebf667a2f8c1e7a72177707))
* reject stale broker funding settlement snapshots ([#2101](https://github.com/iridyne/aletheia/issues/2101)) ([14ead49](https://github.com/iridyne/aletheia/commit/14ead49a5938208ff507ac1ddbbb074861edbb1b))
* **release:** authorize repository admins ([#2161](https://github.com/iridyne/aletheia/issues/2161)) ([cda8c5e](https://github.com/iridyne/aletheia/commit/cda8c5e163ed059c4d426c0e0423c38f88a9f9d1))
* remove shadowing local import of get_secret in get_provider_async ([#2048](https://github.com/iridyne/aletheia/issues/2048)) ([a4473a3](https://github.com/iridyne/aletheia/commit/a4473a3f07178ba6f678dc9521dcf42a55b0c487))
* **report:** isolate performance sections ([27254ae](https://github.com/iridyne/aletheia/commit/27254aea8733a1ffca824d166fa6fb35f3ebc2ab)), closes [#1986](https://github.com/iridyne/aletheia/issues/1986)
* resolve pydantic validate_assignment violations across test suite ([#1355](https://github.com/iridyne/aletheia/issues/1355)) ([b17bb4b](https://github.com/iridyne/aletheia/commit/b17bb4b310c0cdd0e1bba97cadc35182435d2841))
* restore trustworthy Usage cost fallback ([#2298](https://github.com/iridyne/aletheia/issues/2298)) ([ada6ab6](https://github.com/iridyne/aletheia/commit/ada6ab6e2c8a4fd154bbb6b3900d7197f234140e))
* **risk-settings:** enforce Live safety ceilings on all nested policy write paths ([#1303](https://github.com/iridyne/aletheia/issues/1303)) ([cb0a666](https://github.com/iridyne/aletheia/commit/cb0a66678adec487330410e55b832fca85e848d9))
* **risk-ui:** exposure and limit reads show unavailable instead of empty ([#1344](https://github.com/iridyne/aletheia/issues/1344)) ([f35519b](https://github.com/iridyne/aletheia/commit/f35519b3b6cd63b1d698bf041b5305196f4051a0))
* **risk-ui:** hard rejection codes default to block, warn only for advisory ([#1348](https://github.com/iridyne/aletheia/issues/1348)) ([abc8f0d](https://github.com/iridyne/aletheia/commit/abc8f0db9280f8d00b2a0b3fba811b9397f60ccc)), closes [#1233](https://github.com/iridyne/aletheia/issues/1233)
* **risk-ui:** ignore closed positions in exposure labels ([a4a3694](https://github.com/iridyne/aletheia/commit/a4a369408ce3c4398c6ea8a26d7c1fea7d230116))
* **risk-ui:** remove kill-switch setQueryData, enforce refetch authority ([#1190](https://github.com/iridyne/aletheia/issues/1190)) ([#1321](https://github.com/iridyne/aletheia/issues/1321)) ([dfab95d](https://github.com/iridyne/aletheia/commit/dfab95d48aad85accd39e70d2fe68908ec4b741e))
* **risk:** account leverage uses gross notional, not notional×leverage ([#1164](https://github.com/iridyne/aletheia/issues/1164)) ([#1301](https://github.com/iridyne/aletheia/issues/1301)) ([1c81b9b](https://github.com/iridyne/aletheia/commit/1c81b9bc69dc4d7a03933ed2be17948a58d52a8b))
* **risk:** bind causal chain to order authority ([#2259](https://github.com/iridyne/aletheia/issues/2259)) ([f1508c8](https://github.com/iridyne/aletheia/commit/f1508c8c89103284657296502beb202e8a5f6583))
* **risk:** eliminate hardcoded dollar caps — derive all limits from sizing ([#1157](https://github.com/iridyne/aletheia/issues/1157)) ([4ae3934](https://github.com/iridyne/aletheia/commit/4ae393493b8cd14e96fef2a1258386c93bb20c1b))
* **risk:** harden demo stops, contrary entries, and horizon hold ([b30153a](https://github.com/iridyne/aletheia/commit/b30153af1c06d0dfacdaa3e3c4ee116d397d1754))
* **risk:** partial-close ledger read failure now marks snapshot unavailable ([#1170](https://github.com/iridyne/aletheia/issues/1170)) ([#1305](https://github.com/iridyne/aletheia/issues/1305)) ([7268cea](https://github.com/iridyne/aletheia/commit/7268cea5e0c9e03cc8d7edbd4c4c55db66277a05))
* **risk:** rename max_position_notional to max_position_margin (closes [#1167](https://github.com/iridyne/aletheia/issues/1167)) ([#1322](https://github.com/iridyne/aletheia/issues/1322)) ([821b0a7](https://github.com/iridyne/aletheia/commit/821b0a77ee0e42e2caea7688e8438488a75c8f1f))
* **risk:** rename max_position_notional_usdt to max_position_margin_usdt (closes [#1167](https://github.com/iridyne/aletheia/issues/1167)) ([932834f](https://github.com/iridyne/aletheia/commit/932834feb32b61ee51cbbd09e5fb6b3ee26752ed))
* **risk:** sector-concentration depth gate must not suppress portfolio-beta guard ([#1302](https://github.com/iridyne/aletheia/issues/1302)) ([669776f](https://github.com/iridyne/aletheia/commit/669776f4fa0de86e41642631187cde3e9d423415)), closes [#1166](https://github.com/iridyne/aletheia/issues/1166)
* **risk:** unknown funding makes daily-loss snapshot unavailable ([#1173](https://github.com/iridyne/aletheia/issues/1173)) ([#1309](https://github.com/iridyne/aletheia/issues/1309)) ([881d315](https://github.com/iridyne/aletheia/commit/881d315282140e4195264f1f034c953f99e3fd5b))
* **runtime-continuity:** re-prove shadow readiness at promotion boundary (closes [#1201](https://github.com/iridyne/aletheia/issues/1201)) ([#1324](https://github.com/iridyne/aletheia/issues/1324)) ([cdb944a](https://github.com/iridyne/aletheia/commit/cdb944a519cc72c4188fca1b5e1a68d8a0eabf40))
* **runtime:** block startup on unresolved core orders ([#2212](https://github.com/iridyne/aletheia/issues/2212)) ([e5b11b5](https://github.com/iridyne/aletheia/commit/e5b11b5d88d58494693a6f26f8f906051a70d547))
* **runtime:** enforce durable multi-owner pause safety ([#391](https://github.com/iridyne/aletheia/issues/391)) ([#1048](https://github.com/iridyne/aletheia/issues/1048)) ([7001410](https://github.com/iridyne/aletheia/commit/7001410418063dc4ae99dc3110b8e1fb74c96fef))
* **runtime:** gate Frontdoor write before registry DB commit to prevent split-brain ([#1105](https://github.com/iridyne/aletheia/issues/1105)) ([#1113](https://github.com/iridyne/aletheia/issues/1113)) ([f5b4cc1](https://github.com/iridyne/aletheia/commit/f5b4cc1386af022189a5f757020eb4ff9fb89293))
* **runtime:** pending-candidate mark truth across process boundaries ([#1098](https://github.com/iridyne/aletheia/issues/1098)) ([2fe38cc](https://github.com/iridyne/aletheia/commit/2fe38cca37512b39663d1f7c01ffa52d751d00b9)), closes [#1096](https://github.com/iridyne/aletheia/issues/1096)
* **secrets:** add cross-process write lock to prevent lost-update race — [#1101](https://github.com/iridyne/aletheia/issues/1101) ([#1220](https://github.com/iridyne/aletheia/issues/1220)) ([3e6c18d](https://github.com/iridyne/aletheia/commit/3e6c18d7ef1b0127ad554fc672ec759ad9927da3))
* **security:** normalize secret keys across trust boundaries ([#1064](https://github.com/iridyne/aletheia/issues/1064)) ([95e0f93](https://github.com/iridyne/aletheia/commit/95e0f931be296a3a0ae251a42e72b7e11dceb786)), closes [#1010](https://github.com/iridyne/aletheia/issues/1010)
* **security:** symlink blocking in export source trees and restore destinations ([#2082](https://github.com/iridyne/aletheia/issues/2082)) ([9708f8e](https://github.com/iridyne/aletheia/commit/9708f8e45025d5ee2056e43322580f5c09c32ba9))
* **serenity:** fail unproven artifact writes ([7c68377](https://github.com/iridyne/aletheia/commit/7c68377ae41edcac2401d8420bee7058391e7f79))
* **settings:** give every switch a programmatic name ([#1067](https://github.com/iridyne/aletheia/issues/1067)) ([f99727c](https://github.com/iridyne/aletheia/commit/f99727c326a1089548d3208d4f5774ee2b336e08))
* **settings:** initial API failure shows error panel instead of eternal skeleton ([#1320](https://github.com/iridyne/aletheia/issues/1320)) ([2098ed3](https://github.com/iridyne/aletheia/commit/2098ed3aff6eec1bcf0785c2d3d9f9ebc03040fe)), closes [#1185](https://github.com/iridyne/aletheia/issues/1185)
* **settings:** keep mobile Settings navigation at 52px ([#687](https://github.com/iridyne/aletheia/issues/687)) ([#1047](https://github.com/iridyne/aletheia/issues/1047)) ([e4a1987](https://github.com/iridyne/aletheia/commit/e4a19875d186db659437e112d2c5cd88994973d7))
* **settings:** make staged controls explicit ([#999](https://github.com/iridyne/aletheia/issues/999)) ([#1068](https://github.com/iridyne/aletheia/issues/1068)) ([d8c202b](https://github.com/iridyne/aletheia/commit/d8c202bd55ff3ee2cd3e75e6fadb92e27974b6a0))
* **settings:** preserve unsaved drafts across refreshes ([f5b560a](https://github.com/iridyne/aletheia/commit/f5b560a74465b14008116138e08fe3602d336550))
* **settings:** validate notification hours and webhook timeouts ([#1229](https://github.com/iridyne/aletheia/issues/1229)) ([#1340](https://github.com/iridyne/aletheia/issues/1340)) ([c1863b7](https://github.com/iridyne/aletheia/commit/c1863b742f7529a7219afbb9ca05cbfb2bb8b9cd))
* **sizing:** scope ATR lookup to exact instrument identity — [#947](https://github.com/iridyne/aletheia/issues/947) ([#1141](https://github.com/iridyne/aletheia/issues/1141)) ([d9a903f](https://github.com/iridyne/aletheia/commit/d9a903f1a56ee931da515d6091955f7c9a2e5b7a))
* **stability:** fail drive on degraded agent runs ([709161c](https://github.com/iridyne/aletheia/commit/709161c6a730622fe65163a595c469e6c633a2ff))
* **sync:** bind INSERT values by column name, not object iteration order ([#1171](https://github.com/iridyne/aletheia/issues/1171)) ([#1307](https://github.com/iridyne/aletheia/issues/1307)) ([f4a49ee](https://github.com/iridyne/aletheia/commit/f4a49ee0461485a372c23a965efc43e55c70e682))
* **test:** adjust startup_gate tests for Pydantic ge=5 constraint and restore PiDaemonConfig docstring ([#1353](https://github.com/iridyne/aletheia/issues/1353)) ([ddf2002](https://github.com/iridyne/aletheia/commit/ddf200223659f952d2e776572512b7f62c3e3001))
* **test:** keep macro collector unit tests offline ([#2160](https://github.com/iridyne/aletheia/issues/2160)) ([f9b658f](https://github.com/iridyne/aletheia/commit/f9b658fe29c605c3689f61e681b25d6b392c331f))
* **tests:** patch async startup provider boundary ([#1338](https://github.com/iridyne/aletheia/issues/1338)) ([b8fcb17](https://github.com/iridyne/aletheia/commit/b8fcb176df5760aaeee9eb42512a5ebc36915fa5))
* **tests:** update scheduler_timeouts for options_structure collector ([#1155](https://github.com/iridyne/aletheia/issues/1155)) ([da121a0](https://github.com/iridyne/aletheia/commit/da121a0d15d881f862e736597fd12d02d116a7a9))
* **test:** update notional→margin rename in settings bounds and tests ([#1327](https://github.com/iridyne/aletheia/issues/1327)) ([02ddce0](https://github.com/iridyne/aletheia/commit/02ddce033c7ec7006b4873750a85be1e9c19afba))
* **trades:** keep drawer selection current ([#2222](https://github.com/iridyne/aletheia/issues/2222)) ([cb95e54](https://github.com/iridyne/aletheia/commit/cb95e549ce3a085fadc21f885390caa9b8116154))
* **trades:** label close order intent ([#2221](https://github.com/iridyne/aletheia/issues/2221)) ([930e8c9](https://github.com/iridyne/aletheia/commit/930e8c95b304c678551c3f0a553d6906ad27d2be))
* **trades:** render zero pnl neutrally ([91e9116](https://github.com/iridyne/aletheia/commit/91e91161e0cef1efd5af678e8d5ffffd7a7eb7f9))
* **ui:** add named popover labels for LOW tier health issues ([#2059](https://github.com/iridyne/aletheia/issues/2059)) ([e98b34b](https://github.com/iridyne/aletheia/commit/e98b34bb1e3b5c4d9b2a0030504fd0cacca5db61)), closes [#2056](https://github.com/iridyne/aletheia/issues/2056)
* **ui:** align status tones and disclosure chevrons ([#2037](https://github.com/iridyne/aletheia/issues/2037)) ([e34b8d4](https://github.com/iridyne/aletheia/commit/e34b8d45de3e68af95f534dde0f815c0cbdffceb))
* **ui:** contain mobile navigation focus ([#2033](https://github.com/iridyne/aletheia/issues/2033)) ([9d116d3](https://github.com/iridyne/aletheia/commit/9d116d3d8b36f9f856f3d3b833a866dbeb505b35))
* **ui:** distinguish worker and operator permissions ([#2028](https://github.com/iridyne/aletheia/issues/2028)) ([fa3c8c1](https://github.com/iridyne/aletheia/commit/fa3c8c142ca61e29764052bb6f422238e10df3c9))
* **ui:** elevate critical health issues to master state ([#2055](https://github.com/iridyne/aletheia/issues/2055)) ([c7f72b5](https://github.com/iridyne/aletheia/commit/c7f72b5d5f919e0d4155d20ff91f67be6643fea7))
* **ui:** format Position AI Decision and add Copy Full JSON ([#1099](https://github.com/iridyne/aletheia/issues/1099)) ([#1108](https://github.com/iridyne/aletheia/issues/1108)) ([2227ce0](https://github.com/iridyne/aletheia/commit/2227ce0f0bf4d39d32df2354537f7494b12e86df))
* **ui:** make news detail keyboard accessible ([#2036](https://github.com/iridyne/aletheia/issues/2036)) ([5175fb5](https://github.com/iridyne/aletheia/commit/5175fb518450166f05ef715578c97d46d3f51d73))
* **ui:** make prediction market entries keyboard accessible ([#2047](https://github.com/iridyne/aletheia/issues/2047)) ([ae3737e](https://github.com/iridyne/aletheia/commit/ae3737e3b2b6cc9074774c5ed655f2ea7223d074))
* **ui:** make prediction status explicit ([#2034](https://github.com/iridyne/aletheia/issues/2034)) ([0acfd74](https://github.com/iridyne/aletheia/commit/0acfd745e9ae3434283546e54fa8e9c1b7401468))
* **ui:** make Serenity research controls keyboard accessible ([#2035](https://github.com/iridyne/aletheia/issues/2035)) ([9691ed4](https://github.com/iridyne/aletheia/commit/9691ed4f34256a947776242fe36c6c8b97f33b60))
* **ui:** make status popover accessible ([#2031](https://github.com/iridyne/aletheia/issues/2031)) ([fa12514](https://github.com/iridyne/aletheia/commit/fa12514cdfa6da7d73f1ea97f992d117c4ca758c))
* **ui:** replace native confirmation dialogs ([#2029](https://github.com/iridyne/aletheia/issues/2029)) ([599d084](https://github.com/iridyne/aletheia/commit/599d0845c0d272b23b87044f210e9b7879b59810))
* **ui:** restore system diagnostics and truthful status states ([#2012](https://github.com/iridyne/aletheia/issues/2012)) ([0b2e287](https://github.com/iridyne/aletheia/commit/0b2e28731be7928d9a96a8fa9c161e8b3c3d3865)), closes [#1578](https://github.com/iridyne/aletheia/issues/1578) [#1984](https://github.com/iridyne/aletheia/issues/1984) [#2009](https://github.com/iridyne/aletheia/issues/2009) [#2010](https://github.com/iridyne/aletheia/issues/2010) [#2011](https://github.com/iridyne/aletheia/issues/2011)
* **ui:** simplify overview PnL charts to single line each + CI fixes ([d854611](https://github.com/iridyne/aletheia/commit/d8546110ea9ced434420ec10a53f6bf4bb62bb7e))
* **ui:** surface degraded health in master status ([#2027](https://github.com/iridyne/aletheia/issues/2027)) ([442937a](https://github.com/iridyne/aletheia/commit/442937a0fe3dc09f1ecd30dc036debfbdc71f60d))
* **ui:** surface operator trading-daemon block in master status ([#2052](https://github.com/iridyne/aletheia/issues/2052)) ([68618ac](https://github.com/iridyne/aletheia/commit/68618ac0ae82ef9de1753019459753a6e1415556)), closes [#2049](https://github.com/iridyne/aletheia/issues/2049)
* **ui:** surface prediction read failures honestly ([#2050](https://github.com/iridyne/aletheia/issues/2050)) ([94ec092](https://github.com/iridyne/aletheia/commit/94ec092d7655630b1d882e5e88bcf7204bb7fcfe))
* **ui:** surface Serenity mutation failures ([#2030](https://github.com/iridyne/aletheia/issues/2030)) ([de791b0](https://github.com/iridyne/aletheia/commit/de791b079731e1fa4c693f97be5aaca8582f5829))
* **universe:** mean-reversion scorer fails closed to directionless on opposing evidence — [#953](https://github.com/iridyne/aletheia/issues/953) ([#1134](https://github.com/iridyne/aletheia/issues/1134)) ([0fd5504](https://github.com/iridyne/aletheia/commit/0fd5504b8b69b51d4da8bd358c56c568890e502e))
* **universe:** preserve funding source time in setups ([#2293](https://github.com/iridyne/aletheia/issues/2293)) ([9125fdf](https://github.com/iridyne/aletheia/commit/9125fdf16475462512f27b94a8fdf50be28e2fef))
* **universe:** reload fast trigger watchers on cadence ([#2291](https://github.com/iridyne/aletheia/issues/2291)) ([aace8b4](https://github.com/iridyne/aletheia/commit/aace8b4a64f9f9fae59c42944ee0a58e576c6403))
* **universe:** route stock-linked perps with instrument-aware context ([#2187](https://github.com/iridyne/aletheia/issues/2187)) ([48ef5d6](https://github.com/iridyne/aletheia/commit/48ef5d6794b9b148921348534f6da806586df62c))
* **universe:** scope admission freshness by venue ([#2272](https://github.com/iridyne/aletheia/issues/2272)) ([41815be](https://github.com/iridyne/aletheia/commit/41815be472e5462ef1b21001ff50cee5b92d8ed8))
* **universe:** scope snapshot freshness to venue ([#2198](https://github.com/iridyne/aletheia/issues/2198)) ([a21de9d](https://github.com/iridyne/aletheia/commit/a21de9d0ef5f89296f88611190e62db50ab5373c))
* **update:** accept managed uv venv and untrack dashboard artifacts ([#2165](https://github.com/iridyne/aletheia/issues/2165)) ([#2166](https://github.com/iridyne/aletheia/issues/2166)) ([48b68be](https://github.com/iridyne/aletheia/commit/48b68be98619de50a6c1554fd1ca8ba1da7877d0))
* **update:** close Fedora auto/health/checkpoint continuity gaps — [#1135](https://github.com/iridyne/aletheia/issues/1135) ([65dc93b](https://github.com/iridyne/aletheia/commit/65dc93bd5a389f500394adeda5b19370803d3126))
* **update:** close Observer continuity proof gaps — [#1135](https://github.com/iridyne/aletheia/issues/1135) ([92e1bfb](https://github.com/iridyne/aletheia/commit/92e1bfb06ccd57e991c2393f622033d829648dbd))
* **update:** downgrade main_health_degraded to warning when pre-existing ([637a448](https://github.com/iridyne/aletheia/commit/637a448d16f1ea027f06f7e80535ef80557eca1c))
* **update:** downgrade main_health_degraded to warning when pre-existing ([#1273](https://github.com/iridyne/aletheia/issues/1273)) ([182422e](https://github.com/iridyne/aletheia/commit/182422ef07e76d5289d2f5bd8ac41c5fd1bd90cc))
* **update:** fail unproven continuity ([e85e54a](https://github.com/iridyne/aletheia/commit/e85e54a630b0dcba075edcc9c364e7400151eddb))
* **update:** fall back to HTTPS when SSH fetch fails during self-update ([#1228](https://github.com/iridyne/aletheia/issues/1228)) ([44cf520](https://github.com/iridyne/aletheia/commit/44cf520680984be1320d9f242e5bf7c3645a3290))
* **update:** humanize all 33 failure/proof-gap codes in update error output ([f872d17](https://github.com/iridyne/aletheia/commit/f872d177659d8bd0d6701608eb376bd8bc7490c9))
* **update:** make post-pull applier target-fresh — [#1135](https://github.com/iridyne/aletheia/issues/1135) ([56f3461](https://github.com/iridyne/aletheia/commit/56f3461b921e51dc5983721750e2e59605ef14f3))
* **update:** prove live Observation owner exclusivity — [#1135](https://github.com/iridyne/aletheia/issues/1135) ([846b12e](https://github.com/iridyne/aletheia/commit/846b12e64111891941cf3673ca98a84ca908a2f3))
* **update:** re-anchor observation checkpoint after migrations to prevent recurrence of migration drift preflight failure ([0f66e32](https://github.com/iridyne/aletheia/commit/0f66e328fefb44bb8e782c6e12f1f83eb31e84e1))
* **update:** report runtime version transitions ([#1223](https://github.com/iridyne/aletheia/issues/1223)) ([11af89a](https://github.com/iridyne/aletheia/commit/11af89ae8c9dd0a4ec92d8756efd3764adb89c3f))
* **update:** show human-readable detail and fix hint on preflight failure ([7c62a95](https://github.com/iridyne/aletheia/commit/7c62a95af9c13f4d276abb7455b60f9963b13c84))
* **update:** treat gateway slow start (exit 2) as success, not rollback ([#1359](https://github.com/iridyne/aletheia/issues/1359)) ([19f3927](https://github.com/iridyne/aletheia/commit/19f39270dc6937c5b9d7900dcc04bbfab47c4570))
* **update:** use GIT_CONFIG env rewrite for HTTPS fallback; surface git stderr ([#1268](https://github.com/iridyne/aletheia/issues/1268)) ([7a5888b](https://github.com/iridyne/aletheia/commit/7a5888b47d10a4e51caa81113e63c8ecf4fdf08f))
* **update:** use GIT_CONFIG env rewrite for HTTPS fallback; surface git stderr in errors ([1c72575](https://github.com/iridyne/aletheia/commit/1c72575ced19bf2260e97197ff06fa18069852b5))
* **usage-insights:** token totals, public pricing catalog, and trend coverage ([#2158](https://github.com/iridyne/aletheia/issues/2158)) ([301cfdd](https://github.com/iridyne/aletheia/commit/301cfdd7707a5a9277c3bf6fc16f5c36ac6c426e))
* **usage:** preserve durable observability ([#2162](https://github.com/iridyne/aletheia/issues/2162)) ([5a3ff3f](https://github.com/iridyne/aletheia/commit/5a3ff3f37fe7277395753d84856c63c8e1dee7ba))

## [0.8.2](https://github.com/iridite/aletheia/compare/v0.8.1...v0.8.2) (2026-08-17)


### Bug Fixes

* **frontdoor:** serve root dashboard as HTML ([#1041](https://github.com/iridite/aletheia/issues/1041)) ([1c6ec28](https://github.com/iridite/aletheia/commit/1c6ec2803d8057383b2d1a13e212c3fc733b01e4))
* **health:** use observer run heartbeat for freshness ([#1040](https://github.com/iridite/aletheia/issues/1040)) ([12e969d](https://github.com/iridite/aletheia/commit/12e969d44da15b6ab1bc4e65988a70af2dc47d54))

## [0.8.1](https://github.com/iridite/aletheia/compare/v0.8.0...v0.8.1) (2026-08-16)


### Features

* **learning:** add ranker case shadow measurement ([#1018](https://github.com/iridite/aletheia/issues/1018)) ([23515c2](https://github.com/iridite/aletheia/commit/23515c2bd5309257e950846d403adf421bb6f4e0))
* **learning:** converge Packet 0 runtime handoff ([#1015](https://github.com/iridite/aletheia/issues/1015)) ([3e3e4b3](https://github.com/iridite/aletheia/commit/3e3e4b3f39a3ac8c2dcd813975e61cdb187a86c1))


### Bug Fixes

* **observer:** batch deferred buckets per position ([#1022](https://github.com/iridite/aletheia/issues/1022)) ([c19ef42](https://github.com/iridite/aletheia/commit/c19ef42599ff90a14463f1d4b50744a9d318461a))
* prevent dashboard runtime stalls outside [#1025](https://github.com/iridite/aletheia/issues/1025) ([#1033](https://github.com/iridite/aletheia/issues/1033)) ([46f111b](https://github.com/iridite/aletheia/commit/46f111b0cdf0b1a2430c2e8950e8f0b6cabbed83))
* **release:** keep pre-1.0 features on patch versions ([#1036](https://github.com/iridite/aletheia/issues/1036)) ([2fc50e8](https://github.com/iridite/aletheia/commit/2fc50e8ccbdeca1cddd62f29b8e39146811362bc))
* **risk:** size operator entries from account equity ([#1028](https://github.com/iridite/aletheia/issues/1028)) ([bbe1046](https://github.com/iridite/aletheia/commit/bbe10465d413c72bb242117979fb611cc4c18d92))

## [0.8.0](https://github.com/iridite/aletheia/compare/v0.7.9...v0.8.0) (2026-08-15)


### Features

* add profit-loop replay and gradient evidence ([#965](https://github.com/iridite/aletheia/issues/965)) ([87ed013](https://github.com/iridite/aletheia/commit/87ed013cc8aa004c283748ea74b457a3e9770752))
* **analytics:** compile profit-gradient evidence artifacts ([#993](https://github.com/iridite/aletheia/issues/993)) ([9ffe8e7](https://github.com/iridite/aletheia/commit/9ffe8e77eece75c342d86828dbf3a3aaa754d7b4))
* **analytics:** expose complete Opportunity lifecycle evidence ([#980](https://github.com/iridite/aletheia/issues/980)) ([dd20f49](https://github.com/iridite/aletheia/commit/dd20f49e8a8b1375dfa5d55dae8918c6efe8089d))
* **analytics:** preserve position paths for loss attribution ([#963](https://github.com/iridite/aletheia/issues/963)) ([d7ca7f2](https://github.com/iridite/aletheia/commit/d7ca7f2a333b2966de7ca6d68e510a0d095fb0e8))
* **governance:** add observe-only Trading Mandate evidence ([#994](https://github.com/iridite/aletheia/issues/994)) ([6fbdf0e](https://github.com/iridite/aletheia/commit/6fbdf0e9503502698251f27c0949fb0d990f8859))
* **ui:** charts indicator panes, universe reasons, settings overflow fix ([#979](https://github.com/iridite/aletheia/issues/979)) ([69d5c42](https://github.com/iridite/aletheia/commit/69d5c42a24d6164057c1bb83caff520482ff4cfa))


### Bug Fixes

* **analytics:** bind profit-loop evidence to runtime epoch ([#975](https://github.com/iridite/aletheia/issues/975)) ([4be1bfb](https://github.com/iridite/aletheia/commit/4be1bfbe3c2c2eaa554af0ed86f68eb823f1f127))
* **analytics:** declare first-hour counterfactual scope ([#990](https://github.com/iridite/aletheia/issues/990)) ([3c57030](https://github.com/iridite/aletheia/commit/3c57030655a03af41634be0d5284c68c904987eb))
* **analytics:** expose periodic Position path in lifecycle story ([#1009](https://github.com/iridite/aletheia/issues/1009)) ([d515e1d](https://github.com/iridite/aletheia/commit/d515e1d4c2964fe467d1b2ba3f67436f64141c06))
* **analytics:** make profit-gradient gates comparison-aware ([#989](https://github.com/iridite/aletheia/issues/989)) ([71bef5e](https://github.com/iridite/aletheia/commit/71bef5ebb6839a38e279db4b218e756f1e9de97e))
* **analytics:** reject malformed epoch timestamps ([#978](https://github.com/iridite/aletheia/issues/978)) ([fa6dc1b](https://github.com/iridite/aletheia/commit/fa6dc1b3aa6f3e64223da1a3619d6ea213e44189))
* **observer:** recover missed Position Path buckets ([#992](https://github.com/iridite/aletheia/issues/992)) ([6c9304d](https://github.com/iridite/aletheia/commit/6c9304d5dafc5e3dcfed9a90cb5f6f93127f3fc6))
* **operator:** disambiguate outcome and funding signs ([#961](https://github.com/iridite/aletheia/issues/961)) ([8985ccf](https://github.com/iridite/aletheia/commit/8985ccf74bc8ed96bec1f4ea181efc55638e64f6))
* **operator:** retire unauthorized producer carriers ([#960](https://github.com/iridite/aletheia/issues/960)) ([4954ff1](https://github.com/iridite/aletheia/commit/4954ff11a43d11dd52d4f174441307285b5c5376))
* **runtime:** refresh exact pending candidate market facts ([#1005](https://github.com/iridite/aletheia/issues/1005)) ([b0db1e8](https://github.com/iridite/aletheia/commit/b0db1e8b01e86f20217075e5c668994cf1b3495d))

## [0.7.9](https://github.com/iridite/aletheia/compare/v0.7.8...v0.7.9) (2026-08-12)


### Bug Fixes

* **operator:** persist B2 evidence horizon ([#955](https://github.com/iridite/aletheia/issues/955)) ([9f1eba5](https://github.com/iridite/aletheia/commit/9f1eba5f1a7607533dd7c5c72ee5edbc09c75066))

## [0.7.8](https://github.com/iridite/aletheia/compare/v0.7.7...v0.7.8) (2026-08-12)


### Bug Fixes

* **update:** persist release deployment provenance ([#951](https://github.com/iridite/aletheia/issues/951)) ([3fda224](https://github.com/iridite/aletheia/commit/3fda2245755de939e81fc6df0fed7095eb0917a9))

## [0.7.7](https://github.com/iridite/aletheia/compare/v0.7.6...v0.7.7) (2026-08-12)


### Bug Fixes

* **analytics:** explain pre-signal funnel rejections ([#949](https://github.com/iridite/aletheia/issues/949)) ([6e290dc](https://github.com/iridite/aletheia/commit/6e290dcaab1c7c7bc45c9d686f60e8304f821a6c))

## [0.7.6](https://github.com/iridite/aletheia/compare/v0.7.5...v0.7.6) (2026-08-12)


### Bug Fixes

* **analytics:** recover immutable entry ATR lineage ([#945](https://github.com/iridite/aletheia/issues/945)) ([56131f7](https://github.com/iridite/aletheia/commit/56131f789845a719c7a7269836d15ad71386cc1f))

## [0.7.5](https://github.com/iridite/aletheia/compare/v0.7.4...v0.7.5) (2026-08-12)


### Bug Fixes

* **ci:** merge verified unstable release PRs ([#943](https://github.com/iridite/aletheia/issues/943)) ([92a9c9e](https://github.com/iridite/aletheia/commit/92a9c9e464009fbb51984389b4d92b670cc54636))

## [0.7.4](https://github.com/iridite/aletheia/compare/v0.7.3...v0.7.4) (2026-08-12)


### Bug Fixes

* **ci:** attest trusted release candidates ([#941](https://github.com/iridite/aletheia/issues/941)) ([66aca52](https://github.com/iridite/aletheia/commit/66aca52757a6d56d3aa1cd2e484dc3d3a7feefdb))

## [0.7.3](https://github.com/iridite/aletheia/compare/v0.7.2...v0.7.3) (2026-08-12)


### Bug Fixes

* **ci:** retry gitleaks download ([#940](https://github.com/iridite/aletheia/issues/940)) ([11256cf](https://github.com/iridite/aletheia/commit/11256cf324308523bc0f96bdca7dc5cc3d545094))
* **exit:** expose trailing enrollment gate ([#937](https://github.com/iridite/aletheia/issues/937)) ([9c87d4c](https://github.com/iridite/aletheia/commit/9c87d4c0b9b9ead237c65b0a00c9f96a439f50f7))
* **runtime:** anchor evidence epochs on startup ([#938](https://github.com/iridite/aletheia/issues/938)) ([d2bbed5](https://github.com/iridite/aletheia/commit/d2bbed57040ddac467ae3bec4a2644a3850195a5))

## [0.7.2](https://github.com/iridite/aletheia/compare/v0.7.1...v0.7.2) (2026-08-12)


### Bug Fixes

* **update:** honor public health status ([#934](https://github.com/iridite/aletheia/issues/934)) ([05756ae](https://github.com/iridite/aletheia/commit/05756ae290591a740bf8cecff671df81027c7a5e))

## [0.7.1](https://github.com/iridite/aletheia/compare/v0.7.0...v0.7.1) (2026-08-12)


### Bug Fixes

* **update:** parse release-marked versions ([#930](https://github.com/iridite/aletheia/issues/930)) ([765bbbe](https://github.com/iridite/aletheia/commit/765bbbef352434361f6e46c61b9b02c3ac01458d))

## [0.7.0](https://github.com/iridite/aletheia/compare/v0.6.0...v0.7.0) (2026-08-12)


### Features

* **analytics:** add read-only ranker objective audits ([#915](https://github.com/iridite/aletheia/issues/915)) ([4b94487](https://github.com/iridite/aletheia/commit/4b944875e0e09699a183d7036cb8cc2c642e123f))
* **analytics:** report exit shadow uncertainty ([#859](https://github.com/iridite/aletheia/issues/859)) ([6b18e8c](https://github.com/iridite/aletheia/commit/6b18e8c683fe87d8e2520181995ad0a354ef166a))
* **data:** add auditable Position X research pipeline ([#892](https://github.com/iridite/aletheia/issues/892)) ([a34b7d3](https://github.com/iridite/aletheia/commit/a34b7d3b8eaaa541b13faeec07e70a0de2b587d1))
* **exit:** default cost-aware paper trailing ([#888](https://github.com/iridite/aletheia/issues/888)) ([c05bc68](https://github.com/iridite/aletheia/commit/c05bc68c97651aa6c4a7087c45be8dcfd77d0aa7))
* **exit:** register trailing recovery evidence ([#896](https://github.com/iridite/aletheia/issues/896)) ([0d51c89](https://github.com/iridite/aletheia/commit/0d51c890c42f3489afc062459ae4a1392410fbe6))
* **exit:** retain trailing qualification control ([#891](https://github.com/iridite/aletheia/issues/891)) ([62ceaab](https://github.com/iridite/aletheia/commit/62ceaab67f60c326f3603dd8096cc653c375bc0d))
* **update:** add one-command source deployment ([#909](https://github.com/iridite/aletheia/issues/909)) ([20ca099](https://github.com/iridite/aletheia/commit/20ca099e3ccc6acc66f7d413a72da8abdbb1b525))
* **update:** automate stable release tag deployment ([#925](https://github.com/iridite/aletheia/issues/925)) ([87168f2](https://github.com/iridite/aletheia/commit/87168f266038e338267bc30ecbe8c1dff8351aea))
* **usage:** add progressive cost insights ([#919](https://github.com/iridite/aletheia/issues/919)) ([caf1487](https://github.com/iridite/aletheia/commit/caf1487c385cb39a7281aa8977b09222c4ee8e42))


### Bug Fixes

* **analytics:** align R:R audit contract ([#921](https://github.com/iridite/aletheia/issues/921)) ([3e2241b](https://github.com/iridite/aletheia/commit/3e2241b3a486ff6bbc8941fa8da422e5b17ef208))
* **analytics:** distinguish deferred operator signals ([#880](https://github.com/iridite/aletheia/issues/880)) ([e2723d8](https://github.com/iridite/aletheia/commit/e2723d80db00f98ecaee8af17b04c5ab48b9cf9c))
* **analytics:** exposure-match sizing shadow ([#894](https://github.com/iridite/aletheia/issues/894)) ([d234678](https://github.com/iridite/aletheia/commit/d234678ad7010eb9d3a06664ea7757b524418859))
* **analytics:** render deferred operator signals ([#882](https://github.com/iridite/aletheia/issues/882)) ([03c634c](https://github.com/iridite/aletheia/commit/03c634c8765309a61d9d86a8c47fceefe6c83f84))
* **ci:** avoid competing node cache saves ([#881](https://github.com/iridite/aletheia/issues/881)) ([55f02ea](https://github.com/iridite/aletheia/commit/55f02ea60e893f6ee7e21ab27f3fe131905d4bb7))
* **ci:** release runner after enabling auto-merge ([#884](https://github.com/iridite/aletheia/issues/884)) ([c6972ce](https://github.com/iridite/aletheia/commit/c6972cedc46fb31b182a9dd9d99ff1d7b9f9377c))
* **ci:** report regression tiers independently ([#914](https://github.com/iridite/aletheia/issues/914)) ([45cb22c](https://github.com/iridite/aletheia/commit/45cb22c85e25bacc3ca6372d29c65da3d6d777bf))
* **diagnostics:** surface unreconstructable skip evidence ([#879](https://github.com/iridite/aletheia/issues/879)) ([accb59c](https://github.com/iridite/aletheia/commit/accb59c74c87ef53a64a747f35a1b5066fbb25aa))
* **exit:** freeze paper trailing cost contract ([#890](https://github.com/iridite/aletheia/issues/890)) ([d1d10db](https://github.com/iridite/aletheia/commit/d1d10dbe0392b740b17b3dc00219b2249aa7e5d7))
* **exit:** make trailing revisions causal and atomic ([#893](https://github.com/iridite/aletheia/issues/893)) ([766676e](https://github.com/iridite/aletheia/commit/766676e3bae9d6aad3d6b35063f4fbab3fc3c998))
* **exit:** retain trailing recovery evidence ([#897](https://github.com/iridite/aletheia/issues/897)) ([e572578](https://github.com/iridite/aletheia/commit/e572578ea8e78428a955eed0c8f8eb65f2a5f4ca))
* gate auto updates on deployment health ([#928](https://github.com/iridite/aletheia/issues/928)) ([2169a57](https://github.com/iridite/aletheia/commit/2169a570059f6679664c51e710d68ef5fb6a2363))
* **learning:** align lesson reviewer inference contract ([#918](https://github.com/iridite/aletheia/issues/918)) ([637d094](https://github.com/iridite/aletheia/commit/637d0948591ea40bd73a562ba063fcfa04767cca))
* **learning:** canonicalize active UFC Paper identity ([#922](https://github.com/iridite/aletheia/issues/922)) ([bb6f7de](https://github.com/iridite/aletheia/commit/bb6f7de7032cdd1efa7c0f703a7a5da09f7b3e93))
* **learning:** consolidate observation workloads ([#900](https://github.com/iridite/aletheia/issues/900)) ([578c9ab](https://github.com/iridite/aletheia/commit/578c9abe0032a80c8ad6e79e092a66f3e4dcd05d))
* **learning:** make insights decision-ready ([#920](https://github.com/iridite/aletheia/issues/920)) ([1929409](https://github.com/iridite/aletheia/commit/1929409f3c674247fe3a8155fa5158e227828bc0))
* **learning:** persist hypothesis contract identity ([#857](https://github.com/iridite/aletheia/issues/857)) ([fe24431](https://github.com/iridite/aletheia/commit/fe24431b0ef545b927af77b62dd2cb68f52e341c))
* **learning:** preserve exact UFC Paper identity ([#912](https://github.com/iridite/aletheia/issues/912)) ([1202d96](https://github.com/iridite/aletheia/commit/1202d96622ded969d3a01165c0dd84e12fc47c50))
* **learning:** structure lesson review judgments ([#847](https://github.com/iridite/aletheia/issues/847)) ([6db25f4](https://github.com/iridite/aletheia/commit/6db25f4e4d56387ec9de5c381e23e662402667f0))
* **migrations:** freeze UFC v25 fingerprint contract ([#923](https://github.com/iridite/aletheia/issues/923)) ([a5aa6d5](https://github.com/iridite/aletheia/commit/a5aa6d508b382bb80aeab2bf8a2bee69ab6e362f))
* **operator:** persist priority queue evictions ([#885](https://github.com/iridite/aletheia/issues/885)) ([69c17d5](https://github.com/iridite/aletheia/commit/69c17d5b5335602967242e7434459327115628ea))
* **operator:** preserve validated decision evidence values ([#877](https://github.com/iridite/aletheia/issues/877)) ([48daa7e](https://github.com/iridite/aletheia/commit/48daa7e6012dc909d5c911015949adfe03cd0f01))
* **operator:** reopen expired direct-skip defers ([#883](https://github.com/iridite/aletheia/issues/883)) ([f85033b](https://github.com/iridite/aletheia/commit/f85033b4b0a4613655a5c6e5d2c3c00427026651))
* **operator:** rotate corrected skip evidence epoch ([#886](https://github.com/iridite/aletheia/issues/886)) ([4309bda](https://github.com/iridite/aletheia/commit/4309bda09b8ed76f68243456619b925694f3c032))
* **p1-p4:** audit startup decoupling, exact-SHA updater, evidence epoch, control status ([#913](https://github.com/iridite/aletheia/issues/913)) ([ec3dd45](https://github.com/iridite/aletheia/commit/ec3dd4548450ed11f4e80b46fb888552b8361467))
* **scheduler:** isolate Universe recovery audit retries ([#908](https://github.com/iridite/aletheia/issues/908)) ([4dc7aa8](https://github.com/iridite/aletheia/commit/4dc7aa88951557ed3c26fddfc542ff4c1cf57a39))
* **sizing:** clarify policy semantics ([#889](https://github.com/iridite/aletheia/issues/889)) ([870dafd](https://github.com/iridite/aletheia/commit/870dafdc6ac48b2ec04b7a16f7e99e303cda6084))
* **tests:** reset config cache for isolated runtimes ([#926](https://github.com/iridite/aletheia/issues/926)) ([56a5fbd](https://github.com/iridite/aletheia/commit/56a5fbdf232b1739da758c00516b11419e0f383d))
* **trading:** enforce deterministic observer eligibility ([#895](https://github.com/iridite/aletheia/issues/895)) ([f4a480e](https://github.com/iridite/aletheia/commit/f4a480ebd9b382db2b27156548966b1543b6700a))
* **universe:** reconcile scanner runs on Gateway restart ([#902](https://github.com/iridite/aletheia/issues/902)) ([a617b5b](https://github.com/iridite/aletheia/commit/a617b5b4deb4d1760a550fb2489dc70809f93a63))
* **universe:** retry recovery audit mirror in-process ([#906](https://github.com/iridite/aletheia/issues/906)) ([8fd0f26](https://github.com/iridite/aletheia/commit/8fd0f26d94028929bfbbec5250d009c6164dd1f7))
* **update:** pin pull() to fetched SHA and add version-boundary fallback ([#911](https://github.com/iridite/aletheia/issues/911)) ([46b2143](https://github.com/iridite/aletheia/commit/46b2143f8e17ac4d5b4847c3ba5bc6be85b6f674))

## [0.6.0](https://github.com/iridite/aletheia/compare/v0.5.0...v0.6.0) (2026-08-10)


### Features

* **agent:** structure trade motivation ([#856](https://github.com/iridite/aletheia/issues/856)) ([f55094f](https://github.com/iridite/aletheia/commit/f55094fd21e61570445a709d1089ec35afe0cafc))
* **observer:** add compact technical context ([dbed0ac](https://github.com/iridite/aletheia/commit/dbed0acb178f3c02ff98ae49f1ab002613eb836c))
* **observer:** add technical-context-v1 ([#853](https://github.com/iridite/aletheia/issues/853)) ([a91f49e](https://github.com/iridite/aletheia/commit/a91f49e5d41dd2a6967e4de3313576b5abf89f35))
* **release:** establish product version closure ([#855](https://github.com/iridite/aletheia/issues/855)) ([99b0467](https://github.com/iridite/aletheia/commit/99b046703bcb6cb427722ecc77a21327381e4ecc))


### Bug Fixes

* **analytics:** count deferred operator observations in funnel ([#868](https://github.com/iridite/aletheia/issues/868)) ([98b3dfa](https://github.com/iridite/aletheia/commit/98b3dfa7ce506a986d8e35e149a708f9e3cdbc11))
* **doctor:** disclose truncated funding probe ([#861](https://github.com/iridite/aletheia/issues/861)) ([feaea43](https://github.com/iridite/aletheia/commit/feaea439ef8f13336210baca2c0a76bc7863262e))
* **gateway:** stabilize service python path ([#873](https://github.com/iridite/aletheia/issues/873)) ([9ca98b1](https://github.com/iridite/aletheia/commit/9ca98b186b7e8630a2dd25ad84a208c5df9117b6))
* **llm:** enforce context evaluation integrity ([9d65765](https://github.com/iridite/aletheia/commit/9d657659d73e38b8db54299e4b82783b22c30df6))
* **observer:** preserve macro prompt decision ([30f8a7f](https://github.com/iridite/aletheia/commit/30f8a7fc2cbde52e498ab118861fa21009052459))
* **observer:** preserve technical context integrity ([c7aea4a](https://github.com/iridite/aletheia/commit/c7aea4a54342e78a197c566623afbf377572e439))
* **operator:** drain no-model defer backlog promptly ([#870](https://github.com/iridite/aletheia/issues/870)) ([c935c47](https://github.com/iridite/aletheia/commit/c935c47e7a5ae1d9d1cef5c3d301bcd990046171))
* **persistence:** report dormant gateway status honestly ([#863](https://github.com/iridite/aletheia/issues/863)) ([7d52bcf](https://github.com/iridite/aletheia/commit/7d52bcf62891332bd04b1a1bad1cefd61d04b466))
* **release:** allow candidate hold removal ([#876](https://github.com/iridite/aletheia/issues/876)) ([83cfd56](https://github.com/iridite/aletheia/commit/83cfd56b64b4b6fec0ded352545aa1ab70dd93a5))
* **release:** automate verified release closure ([#871](https://github.com/iridite/aletheia/issues/871)) ([1c788e0](https://github.com/iridite/aletheia/commit/1c788e04838f78c4b87961824b876b7630dc98e3))
* **release:** dispatch after trusted merge ([#862](https://github.com/iridite/aletheia/issues/862)) ([03d5739](https://github.com/iridite/aletheia/commit/03d5739d5264d784c68fa3d77e9237a108bf3087))
* **release:** hand off verified candidate explicitly ([#874](https://github.com/iridite/aletheia/issues/874)) ([623dd10](https://github.com/iridite/aletheia/commit/623dd106d36090ea42d483bba25de025f86fea7f))
* **release:** honor actions allowlist ([#860](https://github.com/iridite/aletheia/issues/860)) ([7db5a1f](https://github.com/iridite/aletheia/commit/7db5a1f744b216d7c6a2f6063f69a7c2dffff2a1))
* **release:** preserve generated PR hold ([#866](https://github.com/iridite/aletheia/issues/866)) ([5aae48a](https://github.com/iridite/aletheia/commit/5aae48a9d5bf996fb65d042a0b4bd6e0bc0853c4))
* **scheduler:** expose position manager error facts ([#865](https://github.com/iridite/aletheia/issues/865)) ([e689769](https://github.com/iridite/aletheia/commit/e689769e098c4afa03233c2bd7d075b60040fded))
* **serenity:** make cancellation lifecycle truthful ([#854](https://github.com/iridite/aletheia/issues/854)) ([2df1b0e](https://github.com/iridite/aletheia/commit/2df1b0ec550ab1ce27024bab81c19c7734d3747e))

## [Unreleased]

### Versioned incremental technical analysis

- Added a batch reference analyzer and append-fast incremental engine for
  closed-bar SMA/EMA, RSI, ATR, Bollinger, momentum, volume, VWAP, Donchian, and
  OBV facts. Revisions and gaps are explicit degraded-data events.
- Market Observer and Universe feature collection now reuse a process-wide,
  exact-instrument technical snapshot service. Persisted feature rows carry the
  algorithm version, input fingerprints, warm-up state, and data-quality facts;
  the Operator material fingerprint includes technical provenance.
- Added a format-neutral hot/cold OHLCV tier manager with complete-chunk
  planning, durable receipt validation, and read-back verification. SQLite
  remains the hot authority; ZSTD and the BLOB-vs-file choice remain deferred.
- Added a repeatable micro-benchmark and focused contract/integration coverage.

### Cascade opportunity expiry when parent snapshot is superseded

Fixes an unbounded-growth backlog exposed by the change-suppression valid_until
slide + soft-TTL filter fixes: after the pre-restart baseline of 52 open
opportunities, the pool grew to 72 in the first hour post-restart, with
observed 63/72 opps belonging to *superseded* universe snapshots. Root cause
was a design gap: `expire_universe_snapshots` marks snapshots superseded but
never cascaded to their child opportunities. Before the soft-TTL fix those
would age out naturally at the family-TTL cliff; with soft-TTL they linger
indefinitely at `status='open'`.

- `repositories.expire_opportunities_from_superseded_snapshots(market_type)`:
  single UPDATE that transitions `status='open'` opportunities linked to
  any `universe_snapshots.status='superseded'` row into `status='expired'`.
  Idempotent — no-op once pool is clean. Terminal states preserved.
- `service.py` calls it in *both* branches of `UniverseService.refresh`:
  after each new-snapshot publication AND after each change-suppressed
  reuse. Self-healing at 10-min cadence regardless of fingerprint stability.
- One-shot repair of the 63 orphans observed at diagnosis time: run
  `python -c "from aletheia.storage.repositories import
  expire_opportunities_from_superseded_snapshots as f; print(f('crypto'))"`.
  Post-restart the running gateway will keep the pool tidy on its own.

### Gateway CLI: no-browser as default

Flips the `gateway start` / `gateway restart` default: the browser no longer
pops open on every restart. Owner has to explicitly opt in with `--browser`,
or use the already-existing `aletheia gateway open-dashboard` helper any time.

- `--browser/--no-browser` is a two-way typer flag; default is `--no-browser`.
- Backward compatible: existing scripts and docs passing `--no-browser`
  continue to work (the flag becomes redundant but not an error).
- `cfg.app.auto_open_browser` config option is untouched; the CLI default
  short-circuits it. To restore auto-open on start, pass `--browser`
  explicitly or invoke `aletheia gateway open-dashboard` after boot.

### Approval Desk records final-stage rejections

Closes the loop on "why didn't this reach me?" once a candidate crosses into
the operator stage — the Funnel card covers everything BEFORE that boundary,
the Approval Desk now records everything AFTER.

#### feat(overview): Approval Desk Recent decisions section + trace drawer

- Adds a collapsible "Recent" section beneath the pending approval list.
  Shows outcome counts (trade / operator_skip / user_reject / auto_reject /
  operator_watch) with distinct icons and colored left borders.
- Each row shows symbol / direction / outcome label / relative timestamp +
  the operator's own one-line reason (from pi_decisions.reasoning or the
  reject audit event message).
- Clicking a row opens a DetailDrawer that fetches the full lifecycle trace
  (emit → filter_trace → pi_decision → risk_decision → proposal → order →
  fill → position → close) via a new /opportunity-trace endpoint.
- `user_reject` vs `auto_reject` distinguished at the endpoint layer by
  joining trade_proposals with audit_events (actor='human' → user, else
  auto). Users see clearly whether *they* pressed reject or the system did.

#### feat(api): two new analytics endpoints

- `GET /api/analytics/operator-decisions?hours=24&limit=50` — merges
  pi_decisions (action IN 'trade'/'skip'/'watch'/'flip'/'scale_in') with
  rejected trade_proposals into a unified outcome timeline. Actor split
  computed from audit_events.
- `GET /api/analytics/opportunity-trace/{opportunity_id}` — thin route over
  `analytics.opportunity_flow.trace_opportunity_flow`. Reuses the existing
  CLI trace builder (`aletheia opportunity trace --id`) with no logic
  duplication.

#### docs

- `api_reference.md`: two new endpoint rows.

### Inline paper_trader nudge on universe events

Cuts new-opportunity → ranker-review latency from up to 3 minutes to ~1
second, without introducing a daemon. Debounced (3s) and gated by
APScheduler's `max_instances=1` so a burst of universe emits collapses into
one wake and never races an in-flight run.

- `agent_runtime/scheduler.py::notify_paper_trader(source)`: pulls the
  paper_trader job's `next_run_time` forward to `now + 0.5s` via
  `_scheduler.modify_job`. Fire-and-forget, thread-safe, non-raising.
  Skips silently when scheduler is down / paused / job absent.
- `universe/service.py`: calls it after a new snapshot is published, and
  from the change-suppression branch when `refresh_active_snapshot_ttls`
  actually slid opportunities forward (previously-expired now eligible).

Docs: `agent_contracts.md` describes the on-demand trigger alongside the
scheduled 3min cadence. Follow-up upgrade paths (Tier 2 — wider notify
surface; Tier 3 — full event-driven daemon) filed as a plan at
`docs/archive/legacy/plans/trading-pipeline/ranker_on_demand_upgrade_plan_2026-07-17.md`
with explicit observation gates to prevent premature escalation.

### Opportunity funnel Tier-2: change-suppression TTL slide, ranker cadence, soft TTL, dashboard redesign

Follows the initial funnel card (commit 25a886d8) with a redesigned UI and three
root-cause fixes for the "everything expires before review" pattern the funnel
made visible.

#### fix(universe): change-suppression must refresh valid_until (service.py, repositories.py)

- `repositories.refresh_active_snapshot_ttls(snapshot_id, now)` batches
  `UPDATE universe_members.expires_at` and `UPDATE opportunities.valid_until`
  forward-only (`WHERE expires_at < new_expires`). Idempotent, transactional
  against the shared writer slot.
- `UniverseService.refresh` calls it from the `reuse_previous_snapshot` branch.
  Previously, when the allocation fingerprint stayed stable for 30–60min, every
  carrier of the reused snapshot got hard-rejected as `ttl_expired` on the next
  ranker scan — even though the same working set had just been reconfirmed.
  Reuse is now a genuine no-op, not a wall-clock cliff.

#### chore(scheduler): paper_trader cadence 5min → 3min (scheduler.py)

- Momentum (30min TTL) now gets ~10 review passes (was 6); mean_reversion (60min
  TTL) gets ~20 (was 12). Well below the `agents` executor budget.

#### feat(ranker): soft-TTL filter with score decay (paper_trader_ranker.py)

- New `_ttl_soft_penalty_factor` returns `0.5 ** (expired_sec / family_ttl_sec)`
  — half-life = family TTL. Applied multiplicatively to `trade_score` at end of
  `_evaluate_candidate` (after guard penalty).
- Only past `_TTL_SOFT_HARD_CAP_MULT × ttl_sec` (3× family TTL) does the ranker
  hard-reject with `skip_reason=ttl_expired`. `paper_trader._settle_permanent_skips`
  continues to clean these up.
- `filter_trace` records new reason codes: `opportunity_ttl_soft_penalty` /
  `universe_member_ttl_soft_penalty` under `passed_with_penalty` decision.
- Symptom this fixes: signals aging just past their family window would vanish
  entirely; now they degrade gracefully so a late-arriving ranker can still
  consider them when there's nothing fresher.

#### feat(overview): funnel card redesign per ASCII spec (Overview.tsx, index.css)

- Three sections: (1) top diagnosis headline ("Idle: all N open signals expired
  before review" / "Confidence gate rejected all N candidates" / "Healthy"); (2)
  24h cumulative bars — Discovered / Reviewed / Passed gates / Proposed + accept
  rate; (3) working set now — ✓ accepted / ✗ rejected + per-reason breakdown
  with dot (●) for count>0 and hollow dot (○) for count=0.
- TTL example rows now render "expired 42 min ago · ttl was 60 min" — no more
  `score -999` mystery numbers.
- Footer surfaces `min_confidence_to_trade` (clickable → Settings) and
  `universe_snapshot_age` so the change-suppression interaction with TTL is
  visible to the operator.

#### feat(api): /analytics/opportunity-funnel payload enrichments (analytics.py, trading_context.py, paper_trader_ranker.py)

- Adds `reviewed_count` / `still_open_count` / `universe_snapshot_age_sec` /
  `family_ttl_sec` (map).
- `excluded_by_reason` examples now carry `opportunity_id`, `valid_until`, and
  `expired_seconds` — required for the redesigned UI's "expired X min ago" line.
- `paper_trader_ranker._make_candidate` and `trading_context.ranker_candidates`
  propagate `valid_until` end-to-end.

#### docs

- `scheduler_contract.md`, `agent_contracts.md`, `SYSTEM_MAP.md`: paper_trader
  cadence 5min → 3min.
- `agent_contracts.md` (paper_trader_ranker): documents the TTL soft-filter
  contract (half-life formula, hard cap, filter_trace reason codes).
- `filter_evidence_contract.md`: two new paragraphs — soft-filter semantics and
  the change-suppression TTL slide invariant.
- `api_reference.md`: new `/analytics/opportunity-funnel` endpoint entry.

---

### Universe scanner signal improvements + filter closed-loop feedback

#### Universe Scanner — Signal Quality (scanners.py)

Five targeted fixes to scanner scoring quality and correctness:

1. **`oi_price_divergence` into derivatives scanner**: The +1 (OI↑/price↓ accumulation) and
   -1 (OI↓/price↑ weak-rally) divergence signal was collected but never consumed. Each
   non-zero reading now contributes ±15 pts to `score_derivatives`, raising the family's
   max achievable score from ~85 to ~100.

2. **`short_bias` score ceiling raised**: Was capped at ~78 pts vs ~100 for other families,
   causing consistent under-selection. Added `oi_delta_1h` (+10–12 pts for liquidation or
   new-shorts flow) and `rsi_4h` gate/bonus (+10 pts room-to-fall, early exit if already
   oversold on 4h). Max now ~100.

3. **`mean_reversion` gains `rsi_4h`**: 4h RSI ≥75 (overbought) or ≤25 (oversold) adds 15
   pts and sets direction, complementing the existing 1h RSI extremes.

4. **Volume penalty ordering bug fixed** (volatility + event scanners): `score *= 0.5`
   penalty was applied mid-accumulation (after ATR but before BB/taker/4h signals). The
   penalty was then effectively cancelled by subsequent additions. Moved to end of signal
   accumulation in both scanners; added `score *= 0.7` for unknown volume.

#### Universe Filter — Two-Pass Architecture + Depth Gate (filters.py, service.py)

- **`apply_coarse_tradability_filters`** (pre-scan, spread threshold 3× max_spread_bps):
  runs before scanners assign families. Borderline spread assets (2×–3×) survive.
- **`apply_fine_tradability_filters`** (post-scan, per-family tolerance × 2×):
  applies after scanners run, using per-family spread tolerance multipliers from
  `filter_advisor`. Records rejections with `scanner_family` populated.
- **Depth hard gate**: rejects when `depth_10bps_usd < 10%` of configured minimum (only
  when depth data is available; missing data does not block).
- **Depth scoring in `_compute_tradability_score`**: +10 for deep (≥3× min), +5 for
  adequate (≥min), 0 for marginal (≥25% min), −10 for critically shallow (<25% min).

#### Filter Feedback Loop — filter_advisor (filter_advisor.py, repositories.py)

Closes the feedback gap where filter-rejected candidates had no path back to scanner
calibration. Fine-filter rejections now populate `filter_candidate_outcomes` with
`scanner_family` and `direction`, enabling:

- **`repositories.filter_pressure_stats()`**: aggregates missed-alpha by family from
  settled fine-filter rejections.
- **`repositories.family_spread_tolerance_stats()`**: per-family spread sensitivity from
  `universe_forward_outcomes` (split by entry_spread_bps ≤15 / >15).
- **`filter_advisor.get_family_spread_tolerances()`**: computes per-family tolerance
  multipliers with empirical Bayes shrinkage. Initially all 1.0 (no behavioral change);
  calibrates as data accumulates over ~30 settled outcomes per family.
- **`filter_advisor.get_family_filter_pressure_summary()`**: surfaced in
  `operator_loop.SystemAssessment.filter_pressure` and logged by `outcome_recorder`.

#### Dashboard — Opportunity Funnel Card (Overview.tsx, analytics.py)

New `OpportunityFunnelCard` in Overview surfaces why opportunities don't reach the
operator: emitted → proposed cumulative counts + point-in-time ranker snapshot (excluded
by reason, confidence floor, main_reason). Backed by new `/api/analytics/opportunity-funnel`
route reading `candidate_supply_snapshot` events.

## [0.5.0] - 2026-06-05 — Codex subscription provider (Web UI login + model listing)

Delivers guide §12.1.3 properly: the "ChatGPT Codex subscription / local
authenticated CLI mode" provider. Previously `codex_cli.py` was a bare
`codex exec <prompt>` wrapper with no login detection or model listing. Real
OAuth + token stay owned by the codex CLI (`~/.codex`); we store **no token**.
Zero change-controlled files touched. 272 tests passing (was 253).

### Provider (`model_provider/codex_cli.py`)
- `complete()` runs `codex exec --json` with the prompt on **stdin** and
  `-m <model>`, hardened with `--sandbox read-only --ask-for-approval never` (a
  text completion must never gain shell/write/approval surface). Parses the JSONL
  event stream, tolerantly skipping interleaved non-JSON log lines.
- `structured()` extracts JSON even from prose-wrapped output.
- `healthcheck()` is now 3-state: `not_installed` (binary absent) /
  `not_logged_in` (`codex login status` ≠ 0) / `available` (+ model list).
- `list_models()` parses `codex debug models --bundled` (visibility=="list").
- `ModelHealth` gained `models`; `ProviderConfig` gained `codex_model`.

### Web-triggered subscription login (`model_provider/codex_auth.py`, new)
- In-memory device-auth session: `start_login()` spawns
  `codex login --device-auth`, defensively parses the verification URL + user code
  from stdout to show in the dashboard; `poll_status()` asks the authoritative
  `codex login status`; `cancel()` aborts. No token is ever handled or returned.
  If the (undocumented) device-auth output can't be parsed, it fails with
  `device_auth_parse_failed` and the UI tells the user to run `codex login` in a
  terminal — honest, no fake success.

### API + UI + CLI
- Routes: `POST /api/model/codex/login-start|login-cancel`,
  `GET /api/model/codex/login-status|models`; `/api/settings/model` accepts
  `codex_model`; `/api/model/health` surfaces the model list. Audited; never
  returns a token.
- Settings → Model Provider shows a Codex panel: honest `not configured` /
  `not logged in` (+ Connect Codex device-auth flow) / `connected` (+ model
  dropdown). The green `connected` badge is backed only by a real
  `codex login status` exit 0.
- `model test` reflects the 3-state and shows the model count when available.

### Notes
- `config/secrets.py` deliberately untouched — codex self-manages its token, so
  there is nothing for us to store. No change-controlled module changed.
- Known pre-existing CLI quirk (out of scope, web path unaffected):
  `aletheia model set --enabled true` doesn't toggle the flag because Typer treats
  `Optional[bool]` as a `--enabled/--no-enabled` switch. Use the dashboard/API.

## [0.4.0] - 2026-06-04 — Go-live MVP: autonomous paper loop closes + long-run survival

Makes the system safe to run unattended for long stretches, accumulating real
paper PnL and strategy lessons. Strictly in-guide; only 3 change-controlled files
touched (all in execution/). 253 tests passing (was 217).

### C1 — Position management + automatic close (the keystone)
- New `portfolio/position_manager.py` closes open PAPER positions by stop-loss /
  take-profit / max-hold. This was the missing producer of closed positions — it
  activates realized PnL, the daily-loss breaker (risk §8), and the strategy
  promotion gates, which were all previously dead.
- positions table gains `stop_price` / `take_profit_price` / `close_reason`
  (additive migration). `close_position` now records the reason and writes a
  `position_closed` audit event (trust §1.4).
- **CHANGE-CONTROLLED** `execution/paper_broker.py`: carries the proposal's stop +
  a config-derived take-profit onto a newly opened position (averaging-in keeps
  the original stop). `execution/gateway.py`: wires `stop_price` into the paper
  call. Causal chain unchanged; no closing order is fabricated (paper close is a
  projection update + audit only).
- LIVE positions are NEVER auto-closed; the kill switch does NOT block closing
  (closing reduces risk); no latest price → skip (never fabricate an exit).
- New `TradingConfig` (stop% / TP% / max-hold / min-confidence).

### C2 — Entry logic upgrade (trades stop being noise)
- `market_observer` now uses the model provider's structured() to produce a real
  `{direction_bias, confidence}` (graceful fallback to neutral/speculative). The
  opportunity is ALWAYS recorded, even when not traded (kept for analysis).
- `paper_trader` skips neutral and below-`min_confidence_to_trade` signals (marks
  them `reviewed`, never discards) and sets a protective stop from config.
- **CHANGE-CONTROLLED** `execution/paper_tools.py`: optional `stop_price`
  passthrough; the paper-only hard-reject is unchanged.

### C3 — Data retention / cleanup (long-run survival)
- Whitelist-based pruning of high-volume telemetry only (agent_runs, run_steps,
  tool_calls, market/portfolio snapshots). Trust-bearing tables (proposals, risk
  decisions, orders, fills, positions), audit, and knowledge are NEVER pruned.
- JSONL size-cap rotation to a single `.1` generation (content never deleted).
- Daily `cleanup` scheduler job + `RetentionConfig`.

### C4 — Self-upgrade safety (a bad pull can't brick the box)
- `update/smoke.py`: post-pull import gate (fresh subprocess) + fast pytest subset.
- `apply_update` now verifies the pulled code and, on failure, performs a
  user-authorized controlled `git reset --hard` back to the pre-pull commit,
  restores deps + config, and writes an `update_rolled_back` audit. Never
  auto-restarts. Still not agent-callable.

### C5 — Process liveness + health visibility (safe to walk away)
- `_gateway_runner.py` is now a supervisor: it respawns the inner uvicorn child on
  abnormal exit with backoff, gives up after a bounded number of restarts (audited
  + alerted), and routes the inner server's stderr to `data/logs/errors.log`. Pid
  semantics preserved (written once; `gateway stop`/`status` unchanged).
- New `agent_runtime/health.py` + 10-min `health_check` job: honest signals
  (agent-failure ratio, kill switch) with transition-only, de-duped Telegram
  alerts. Never fabricates a positive "healthy" push. `HealthConfig` added.

## [0.3.0] - 2026-06-03 — Full MVP: risk hardening, live path, reconciliation

Closes the remaining functional gaps between 0.2.0 and a feature-complete MVP per
the implementation guide. Strictly in-guide scope (no roadmap §30 items). 178 tests
passing (was 137). Frontend rebuilt.

### Risk Engine — all 13 §21.3 checks (CHANGE-CONTROLLED, guide §31.3)
- Added the 7 missing checks: max_daily_loss (global; auto-trips kill switch),
  account_mode_match, max_leverage (futures), data_freshness (live),
  max_single_trade_risk (live, stop-based), strategy_lifecycle_state (live).
- New checks gate **live** primarily; paper/demo stays permissive. Daily-loss and
  kill-switch are global brakes across all modes.
- `TradeProposal` gained optional `leverage` / `stop_price` (additive migration).

### Kill Switch (§21.3, §2.4)
- `risk/kill_switch.py`: activate/deactivate/is_active, persisted in
  `runtime.kill_switch_active` (survives restart). Risk engine rejects every
  proposal when engaged. Auto-trips on daily-loss breach.
- Surfaces: CLI `aletheia risk kill|resume|status`, API `GET/POST /api/risk/kill-switch`,
  `GET /api/risk/boundaries`, dashboard Settings + Overview. All changes audited.

### Live order path (CHANGE-CONTROLLED, guide §31.3)
- `execution/bitget_broker.py`: approved live-limited proposals now actually place
  a Bitget order via `BitgetClient.place_order` and record order (with
  `exchange_order_id`) / fill / position. Gateway wires the `bitget_live_limited`
  branch to it; still blocked when `live_disabled` (defense in depth) + audited.

### Bitget adapter — full §19.3 interface
- Added get_account, get_positions, get_open_orders, cancel_order, get_fills
  (was 5/9 methods; now 9/9).

### Reconciliation (§7, §20.2)
- `execution/reconciliation.py`: compares local live orders vs exchange open
  orders, marks vanished orders filled, audits divergence. Never places/cancels.
  Runs on a scheduler interval (live mode only).

### Portfolio: position close + realized PnL
- `close_position` (realizes PnL, marks closed) and `today_realized_pnl` (source
  for the daily-loss check). Scheduler stores periodic portfolio snapshots.

### Optional Git versioning (§2.2, §9.4)
- `files/git.py`: opt-in (`runtime.git_enabled`, default off) versioning of
  knowledge/playbooks/reports; never stages secrets.enc/app.db/logs. Graceful
  when git is absent.

### Scheduler
- Added reconciliation (2 min, live-only) and portfolio-snapshot (10 min) jobs.

### Fix — `gateway stop` on Windows (§32 #3)
- `is_running()` used `os.kill(pid, 0)`, which TERMINATES on Windows rather than
  probing liveness, so the PID file was cleared while the server kept running and
  `stop` couldn't find it. Now uses a platform-aware `_pid_alive` (tasklist on
  Windows). Verified start→status→stop end-to-end.

---

## [0.2.0] - 2026-06-03 — Functional MVP completion

Completion pass after an audit found the initial "MVP" was ~35% of the dashboard
spec with several non-functional core pieces. This release closes those gaps so the
system is configurable and usable entirely from the dashboard.

### P1 — Backend config + secrets surface
- `ProviderConfig.api_key_secret_ref`; router injects API key from secret storage
- Real secrets CLI (`set` hidden prompt, `list`, `test bitget`); `model set` CLI
- Routes: `GET/POST /api/settings/model`, `GET /api/model/health`,
  `POST /api/bitget/test-connection`, `POST /api/settings/exchange-credentials`,
  `GET /api/bitget/credentials-status`
- Write-only secrets: GET endpoints never return raw keys (configured + last4 only)

### P2 — Dashboard configuration UX (OpenAlice-style)
- Settings page with all 8 §23.12 controls: model provider + write-only API key,
  Bitget demo/live credentials + test connection, exchange-mode switch,
  live unlock/lock, risk display, telegram, permission, pause/resume
- SetupBanner onboarding nudge; Overview shows account/PnL/positions/latest activity

### P3 — Runtime (the system now actually runs)
- FIX: scheduler now starts on gateway boot (was never invoked)
- Real pause/resume (halts scheduler jobs); market-data loop writes market_snapshots
- Agents call the LLM with graceful degradation; tool calls logged
- 4 new agents: strategy_designer, risk_critic, portfolio_agent, post_trade_reviewer
- portfolio/pnl.py: mark-to-market PnL + portfolio snapshots

### P4 — Trading workflow + dashboard actions
- §25 routes: proposal approve/reject, strategy get/promote/retire, portfolio
- FIX: gateway passed empty risk_decision_id (FK violation); now passes real id
- Dashboard action buttons (approve/reject, promote/retire, run-agent); KB tabs;
  Positions grouped + exposure; 5 seed playbooks on bootstrap

### P5 — Charts
- lightweight-charts: candlestick price chart + trade markers, PnL line chart
- Charts page, Reports page renders real summaries

### P6 — Test matrix + acceptance hardening
- Security tests: secrets never in logs/config/GET responses; restricted tools
  unreachable; live blocked by default; permission/live changes audited
- FIX: tool registry now initialized in orchestrator (agents were silently no-op
  when run before the app lifespan registered tools)
- §32 acceptance tests through the API/CLI
- 137 tests passing (was 84)

---

## [0.1.0] - 2026-06-03

### Initial milestones M0–M11 (function-level)

#### M0 — Repo Skeleton
- Python 3.12 project with uv, pyproject.toml
- CLI skeleton (aletheia/ato) with Typer
- Config schema + loader + YAML persistence
- Secrets handling (keyring + encrypted file fallback)

#### M1 — Gateway + Local Server
- FastAPI server on localhost:8787
- Gateway start/stop/status/restart/pause/resume
- PID file management, browser auto-open
- Health and status API endpoints

#### M2 — Storage
- SQLite schema (17 tables)
- JSONL event stream writer/reader
- Markdown writer for knowledge documents
- Repository CRUD functions
- Audit event system

#### M3 — Dashboard MVP
- React/Vite/TypeScript frontend (11 pages)
- Overview, Agent Runs, Dreaming, Knowledge Base, Opportunities, Proposals, Paper Trades, Positions, Strategy Lifecycle, Reports, Settings
- Dark theme, sidebar navigation
- Built to static files served from FastAPI

#### M4 — Model Provider Layer
- OpenAI-compatible (chat_completions + responses)
- Anthropic-compatible (Messages API)
- Codex CLI (graceful when unavailable)
- Router with caching and healthcheck

#### M5 — Agent Runtime
- APScheduler-based scheduler
- Tool registry with 5-level permission enforcement
- Agent orchestrator with full lifecycle logging
- Market Observer, Research Agent, Dreaming Agent

#### M6 — Knowledge Base
- Research Notes, Strategy Memory, Dreaming Logs
- Confidence levels (high/medium/speculative/deprecated)
- Archive/deprecate (no hard delete enforced)
- SQLite index

#### M7 — Bitget Adapter
- Native HMAC-SHA256 auth (no ccxt dependency)
- Demo mode header support (paptrading: 1)
- Spot + USDT futures market data
- Account/balance/ticker/candle/order endpoints

#### M8 — Risk Engine + Paper Broker
- Hard limits (immutable to agents)
- 7+ risk checks (symbol, market, live state, permission, position size, etc.)
- Paper broker with simulated fills
- Execution gateway (full pipeline)

#### M9 — Strategy Lifecycle
- 10 lifecycle states (draft -> live_limited -> retired)
- Forward-only transitions enforced
- Promotion gates for live candidacy

#### M10 — Telegram Light Adapter
- /status /pause /resume /latest /help commands
- Allowed chat IDs enforcement
- Audit events for all actions

#### M11 — Live-Limited Gated Path
- live_disabled by default
- Explicit human confirmation required for unlock
- Risk engine blocks all live orders when disabled
- Lock/unlock via API and dashboard

### Test Coverage
- 84 tests passing (unit + integration + security)
- Config, secrets, permissions, risk, lifecycle, API, gateway, telegram
