# Changelog

All notable product changes are recorded here. Aletheia uses Semantic Versioning
for the product release, while build identities and data/behavior contracts keep
their own independent versions.

## Unreleased

### Changed

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
