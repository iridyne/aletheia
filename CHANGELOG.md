# Changelog

All notable product changes are recorded here. Aletheia uses Semantic Versioning
for the product release, while build identities and data/behavior contracts keep
their own independent versions.

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
