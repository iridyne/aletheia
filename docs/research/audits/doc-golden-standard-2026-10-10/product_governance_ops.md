# 文档审计：product / governance / ops 域

> 审查员只读审计，工作树 docs-spec-cleanup @ 356d346a4。证据格式 `doc:line` / `code:line`。

## docs/product/SCOPE.md（120 行）

**角色**：Product（纳入/延后/排除）。基本单一，但 §"Approved implementation scope"（CN Equity，L54-72）与 §"Current Learning scope"（L74-83）在复述 ACTIVE_RUNTIME_SCOPE 与 learning_contract 已拥有的事实。

**真相核对**
- 缺失 2026-10-09 owner 决定：全文无 "perpetual/futures/spot" 字样（`grep -n -i spot` 零命中）。L41 "Crypto market facts required by the Bitget route" 未写"仅 USDT 本位加密永续，现货不在范围、RWA 永续拉黑"。产品边界页反而比 GROUND_TRUTH:56 少一条最硬的范围约束。
- L44 "Active Bitget Extension" 未提 UTA v3 是目标协议、classic_v2 只为演示盘容忍。
- L47 把 Learning 定位为 "Optional research Extension, not required Slim Core capability"——与 owner 目标（复盘→教训→下次决策是主干）和 spec 0001 Goals 1-4 冲突；也与 DIRECTION.md:68-78 "Complete the evidence-to-learning loop" 作为第 2 步冲突。
- L93 "Full Learning owner workbench … (#614 closed)" 是 issue 状态当内容。
- CN Equity（L54-72）：ACTIVE_RUNTIME_SCOPE:5-15、75-83 已完整声明；此处第三份副本。

**具体性**：表格是承诺式 prose，没有可检要求；作为 Product 页可接受，但"Excluded"列表是本页唯一不可替代的内容。

**合理性**：有存在价值（排除清单），但"纳入范围"表与 GROUND_TRUTH 当前边界表一一对应，是重复。

**判决：MERGE into CAPABILITY_GROUND_TRUTH.md**（保留"Excluded"+"Deferred"两表，迁入 GROUND_TRUTH 末尾；SCOPE.md 删除，或降为 5 行路由页）。若 owner 坚持保留独立 SCOPE，则 REWRITE。

**具体修改**
1. L37-48 "Included current scope" 整表删除——每行都已由 GROUND_TRUTH:48-61 拥有。
2. 新增（或并入 GROUND_TRUTH）一行硬约束："Instrument scope: USDT-margined crypto perpetuals only; spot is out of scope and not planned; venue RWA perps are blacklisted." 并链 DECISIONS 2026-10-09。
3. L47 Learning 改为："Decision-level learning (review → Playbook rule → next Entry Planner decision) is a mainline capability defined by `spec 0001`; it gains no Risk/execution/credential authority." 删除 "Optional research Extension"。
4. L54-72 CN Equity 删除，替换为一句链接 ACTIVE_RUNTIME_SCOPE `authorized_extensions.cn_equity`。
5. L74-83 "Current Learning scope" 删除（learning_contract + spec 0001 拥有）。
6. L93 删除 "(#614 closed)…" 行；L91-92 两条 Learning 延后项改由 spec 0001 的 advisory/authority 分级表述。
7. L110-120 "How scope is evaluated" 与 DIRECTION:8-13、GROUND_TRUTH:25-31 三处重复路由，留一份。

## docs/product/definition/DIRECTION.md（149 行）

**角色**：Product（长期方向）。角色单一，无进度/issue 噪声，写得干净。

**真相核对**
- L15 "Last updated: 2026-07-30"——早于 2026-10-09/10 的三条 owner 决定（futures-only、AI 像人一样交易而非量化、效率第一对标 AstraQuant），方向页没有吸收它们。
- L21 北极星 "personal, autonomous trading **research** and decision system"；L30-35 "durable learning apparatus"——偏"研究装置"叙事，与 owner 2026-10-10 "AI 像人一样交易：复盘→教训→下次决策；alpha=AI 判断质量；框架做好不赚钱" 的定位有张力（不矛盾，但没把"判断质量"作为成功定义）。
- L94-95 "recommendation → bounded paper influence → supervised exchange interaction → live"：`paper` 在当前语境里会被误读为已退役的本地模拟器；当前路线是 Bitget Demo。
- L103-112 "Broaden … more market structures"：未写明现货已被 owner 永久排除（不是"以后再扩"）。
- 无代码声明可核（纯方向页），跳过 spot-check。

**合理性**：有用；"Directional decision test"（L129-142）是可复用的评审问题。与 SCOPE.md 的关系：SCOPE:4-5 说"purpose 只由 DIRECTION 定义"，DIRECTION:8-9 又说"supported scope 见 SCOPE"——互相指，OK；但 DIRECTION 自称 "single canonical product-definition document"，而 GROUND_TRUTH:27 自称 "canonical capability boundary"，两者问题不同不冲突。

**判决：KEEP（小改）**

**具体修改**
1. 删除 L15 "Last updated"（日期是进度信号，git 拥有）。
2. L21-28 北极星补一句 owner 定位："The AI trades like a discretionary trader — it reviews its own decisions, turns recurring mistakes into rules, and applies them to the next decision. Edge is judgment quality, not mined factors." 链 spec 0001。
3. L94-95 "bounded paper influence" → "bounded influence on the Bitget Demo route"。
4. L103-112 加一句："Instrument breadth is bounded by Capability Ground Truth: spot and non-crypto (RWA) perpetuals are excluded, not deferred."

## docs/product/CAPABILITY_MAP.md（434 行）

**角色**：Product registry（生命周期能力定义 + L0-L4 成熟度梯 + 每能力 probe + L4 判据）。单一角色，明确声明不存放当前级别（L10-21），符合 stable-content rule。是本域质量最高的文档。

**真相核对（probe 抽查：35 个代码符号 + 41 个测试文件，全部存在）**
- 全部存在：`registry.py:_is_excluded`、`economic_utility.py:estimate_economic_utility`、`entry_plan_compiler.py:compile_entry_plan`、`fast_trigger_watch.py:_load_entry_plan_watchers`、`risk/engine.py:_entry_plan_authority_violations`、`stops.py:replace_exchange_protective_stop`、`position_manager.py:close_position_routed`、`broker_funding_settlement.py:settle_unsettled_exchange_closes`、`outcome_integrity.py:read_complete_outcome_cohort`、`lesson_reviewer.py:run_lesson_reviewer`、`experiments.py:apply_numeric_treatment` 等；`.claude/commands/capability-review.md` 存在。
- **现货残留**：L93 "are crypto spot/perp enter scanning"——与 GROUND_TRUTH:56 和代码 `core/aletheia/universe/registry.py:92,139`（"spot never enters scanning or planning"）冲突。L184 "spot never exceeding cash" 描述已出范围的路径。
- **与 spec 0001 冲突**：
  - CAP-LEARN-02（L413-420）"Evidence-gated lesson promotion … with expiry"，probe `lesson_reviewer.run_lesson_reviewer`——spec 0001 §REMOVED（spec.md:282-285）删除 `ps_lessons` 生命周期、30 天 TTL、`lesson_reviewer` forward-return 验证与胜率降级。
  - CAP-LEARN-03（L422-430）caller 列 `extensions/knowledge/guards.py`——spec 0001:285 "Learned guards as a separate store … become AVOID rules"。
  - CAP-LEARN-01 "deterministic case and a review"——spec 0001 的单元是**每个 ENTRY_PLAN 决策**（LL-R02，含未成交的），而非仅 eligible close；LL-R06 blind process review 未体现。
  - EPLAN/PLAN 节没有 LL-R01 需要的 `p_target_first`、`lessons_considered` 字段能力。
- L4 判据写得具体（样本数、置信下界、owner accept），是可检要求，好。

**Bloat**：L5-8 批准日期+提案链接属 Decision 内容，可缩成一个链接。L53-54 "Targets below are **proposed** defaults pending owner review"——DECISIONS:1303 已记录批准，若 targets 已批则此句过时，若未批则须保留（需 owner 确认）。

**判决：KEEP（小改）**

**具体修改**
1. L93 → "only USDT-margined crypto perpetuals that are exactly tradable on the active venue enter scanning; spot never enters, and venue-RWA perps (equity, metal, index) and unknown profiles are excluded fail-closed."
2. L184 删 "and spot never exceeding cash"。
3. LEARN 节按 spec 0001 重写（ID 不复用规则下）：CAP-LEARN-02 移入 "Retired IDs"（理由：spec 0001 REMOVED）；新增 CAP-LEARN-04 "Judgment label for every Entry Plan decision"（LL-R02–R05）、CAP-LEARN-05 "One Playbook, advisory context to the Entry Planner"（LL-R09–R14）、CAP-LEARN-06 "Playbook on/off randomisation and harm stop"（LL-R16–R18）；CAP-LEARN-03 的 caller 去掉 `guards.py`，contract 加 spec 0001 LL-R19。注意：spec 0001 尚未 implemented，新 CAP 的 probe 写 "L0 until LL-Rnn implemented"。
4. CAP-LEARN-01 Outcome 改为"每个 Entry Plan 决策获得 judgment label 与 blind process review"，或保持当前定义并注明 spec 0001 将替换。
5. L5-8 缩为 "Approved: DECISIONS 2026-10-09 — Lifecycle capability map."

## docs/product/gates/PHASE_GATES.md（113 行）

**角色**：Product（准入/晋级门）。单一角色，但 Gate B-D（L56-80）基本是"去看 learning_contract"+免责声明，无门槛内容。

**执行者核对（门禁的执行者是否存在）**
- Gate A（L14-33）：无专属执行器。`grep -rn "Gate A" core extensions scripts tests` 只命中 `tests/e2e/test_pipeline_exchange_entry_to_position.py:518,608` 的 docstring 引用；`scripts/ci/check_docs.py:1464-1489` 只校验本文件不含已退役的策略门字样。判定方式是 L33 "uv run aletheia doctor + runtime evidence"（`core/aletheia/cli/doctor_cmd.py` 存在）——即人工判读，没有一个产出 PASS/FAIL 的程序。L22 "Startup -> Scheduler -> Opportunity -> Decision -> …" 与 CAPABILITY_MAP 的 11 阶段（CAND…OUT）不是同一词表。
- Gate CN（L35-54）：`grep -rli "cn_equity|CN V2" --include=*.py` **零命中**。三态 disabled/observe/shadow 无任何代码执行者；与 ACTIVE_RUNTIME_SCOPE:5-15 `implementation_state: authorized_not_implemented` 一致，但门本身是对不存在代码的预约束。CN Equity 与"futures 产品、尽快上线"目标无关。
- Gates B-D（L56-80）：执行者 = Observation Lab（`extensions/learning/read_models.py` 有 lco-v1；路由 `core/aletheia/server/routes/analytics.py:2321 /learning-contribution`）。存在，但本页不定义任何门槛，只是指针。spec 0001 LL-R16-R18（Playbook 随机化+harm stop）将成为新的 Learning 门，本页未提及。
- Gate E（L82-99）：执行者存在——`core/aletheia/analytics/go_live_scorecard.py:49 compute_scorecard`、`core/aletheia/server/routes/live.py:34 live_unlock`、`core/aletheia/trust/live_unlock.py`。
- L109-110 Retired gates 列表 + L112-113 "June 2026 Round 1" 是历史内容。

**合理性**：Gate A 和 E 有意义；Gate A 应与 CAPABILITY_MAP 的 L3/L4 判据合并——两者都在回答"何时算主线通过"，现在是两套词表、两种判法。Gate CN 是化石预约束。

**判决：REWRITE**

**具体修改**
1. Gate A 改为引用 CAPABILITY_MAP：「Gate A passes for a Runtime when every mainline capability CAP-CAND-01…CAP-OUT-01 is at least L3 on one exact deployed commit (per CAPABILITY_MAP maturity ladder), with Live disabled.」删 L22 的另一套阶段名。保留 L29-30 unavailable≠zero。
2. Gate CN（L35-54）整节删除，改为一句："CN Equity has no runtime gate until code exists; see ACTIVE_RUNTIME_SCOPE `authorized_extensions.cn_equity`."（或建议 owner 撤销 CN 授权，见跨文档节）。
3. Gates B-D 缩为 3 行指针：learning_contract + observation_lab_contract + spec 0001 LL-R16–R19。删 L71-76 lco-v1 细节（METRICS §2 已写）。
4. L101-113 "Retired gates" 移到 `docs/design/history/local_simulated_product_gates.md`（该文件已存在）。check_docs.py:1483-1489 的守卫若依赖本节字样需同步。

## docs/product/gates/METRICS.md（116 行）

**角色**：Product（指标含义与来源）。基本单一；§2 是 Observation Lab 读模型的说明，属 contract 细节的复述。

**真相核对**
- §5 Go-Live Scorecard（L79-103）：与代码一致。`core/aletheia/analytics/go_live_scorecard.py:49 compute_scorecard`，权重 0.135/0.18/0.135/0.135/0.09/0.09/0.045/0.045/0.045/0.10（`:166,288,309,326,363,408,446,471,515,521`）全部吻合；300s 连通性（`:386`）吻合；红/黄/绿阈值 `:72`。
- **文档未披露的 fail-open**：代码在 risk_clean（`:427-438`）、model_health（`:474`）、reconciliation（`:515`）"cannot verify" 时给 **50 分**——与本页 L76-77 "A zero must never stand in for a failed read" 同族问题（这里是 50 代替了 unavailable）。代码注释自承 "fail-open scorecard behaviour"。文档应说明或代码应改为 unavailable。
- **Conviction calibration（L97，权重 0.10）的数据源不在主线上**：`extensions/analytics/confidence_calibration.py:1,40-47` 读 `pi_decisions` 的 `conviction` 且要求 `action in (trade, scale_in, flip)`；当前主线 `core/aletheia/agent_shell/entry_planner.py`、`core/aletheia/execution/entry_plan_execution.py`、`entry_plan_compiler.py` 中 `conviction`/`pi_decision` **零命中**。即 Entry Plan 主线产生的交易不会给这个 10% 权重的指标供数（推断：该项在主线上恒为 "insufficient"/0；未在运行时验证）。spec 0001 LL-R01 新增的 `p_target_first` 才是天然的校准输入。
- §4 L72 "conviction calibration/ECE"、L73 "family, factor, thesis … attribution"——"factor" 归因与 owner "不是量化挖因子" 的定位不符，且 CAPABILITY_MAP CAP-OUT-02 用的是 entry/management/exit/cost 归因。
- §3 L58 "Paper authority allocation"——`paper` 字样易与已退役模拟器混淆（PHASE_GATES:75-76 专门为此写了免责声明，说明这是已知的命名债）。
- `GET /api/analytics/learning-contribution` 存在（`core/aletheia/server/routes/analytics.py:2321`）。

**合理性**：§1、§3、§5、§6 有用；§2 是 Observation Lab 读模型规格，应归 observation_lab_contract。

**判决：REWRITE（小）**

**具体修改**
1. §2（L26-46）缩为 2 行指针到 observation_lab_contract；删除重复的 lco-v1 字段表。
2. §4 L72-73：conviction → "Entry Planner probability calibration (`p_target_first`, spec 0001 LL-R01) — unavailable until implemented"；删 "factor"，改为 "entry / management / exit / cost attribution (CAP-OUT-02)"。
3. §5 表 Conviction calibration 行加注："Source `extensions/analytics/confidence_calibration.py` reads `pi_decisions.conviction`, which the Entry Plan mainline does not write; the criterion is currently not fed by the mainline." 并开 D0 Issue（scorecard 输入与主线脱节）。
4. §5 L99 后加一句披露 fail-open："risk_clean, model_health and reconciliation score 50 when they cannot verify (code `go_live_scorecard.py:427-438,474,515`)"——或开 D0 修成 unavailable（与 L76-77 原则一致，推荐后者）。
5. §3 L58 "Paper authority allocation" → "Treatment allocation"。

## docs/product/experiments/EXPERIMENT_PROTOCOL.md（336 行）

**角色**：混合。§1-4、§6-7 是通用实验/观察窗口治理（Product/Policy）；§5（L137-205）是 Learning 路径注册表与 UFC Shadow 判定细则（Contract 内容，learning_contract/observation_lab_contract 才是 owner）；§9（L257-336）是 Universe Discovery Benchmark 的完整算法规格（Contract/Spec 内容，统计方法、K、bootstrap 次数）。

**真相核对**
- §5 L139-146 列 4 条 measurement-supported 路径，代码都存在：`extensions/learning/registry.py`（`ranker_case_score`、`mechanism_entry_learning`）、`core/aletheia/trading/momentum_entry_geometry.py`、`extensions/learning/cohorts.py`（UFC）、`extensions/learning/verdicts.py`（`ufc-shadow-trial-integrity`）。
- **退役机制残留**：`ranker_case_score` 在 `extensions/learning/registry.py:83` consumer 写的是 `"paper ranker"`——Ranker/PaperTrader 已退役，这条路径无生产消费者，是化石注册项；本页 L140、L145-146、L156、L160 仍把它当现行 Shadow 路径。L122 "System Operator/ranker/filter behavior" 同。
- **现货残留（与 futures-only 冲突）**：§9.2 L300-301 `simple_momentum` 基线 "over positive-return spot assets and admit only spot long"，代码 `core/aletheia/universe/discovery_benchmark.py:161,355` 一致；但 `core/aletheia/universe/registry.py:92,139` 声明 spot 永不进入扫描。推断：基线 cohort 结构性为空 → §9.3 L332-334 `supported_best` 的"beats … the simple baseline"永远无法成立（未在运行时验证）。L276 "spot/perpetual de-duplication" 同样过时。
- §9 benchmark 有执行者：`core/aletheia/cli/evidence_cmd.py:67-119`（start/status/abort/finalize）、`core/aletheia/universe/service.py:527 capture_completed_run`。
- `paper_10/25/50/active` 阶段名（L184-196）仍在代码 `extensions/observation_lab/canonical.py` 等；L12-15 专门免责说明它们不是模拟器——命名债。
- 与 spec 0001 冲突：spec LL-R16 要求 Playbook on/off 由 "dedicated Observation Lab adapter that is mutually exclusive with the entry_learning adapter"（spec.md:212）；本页 §3.1 L104-107 "only one behavior-changing treatment … at a time" 原则一致，但 §5 未登记 Playbook 路径，且 `mechanism_entry_learning`（L147-148）与 Playbook 将互斥——应在 learning_contract 落地时同步。

**Bloat**：L8-10 June 2026 Round 1 历史；L253 "the former #470 umbrella is closed"；L257 标题带 "#2556"、L296 "#2601"——issue 号当内容。§8 与 §9 之间缺空行（L256-257）。

**合理性**：§1-4、§6-7 的"窗口身份/冻结/不得合并跨 epoch"规则对"AI 像人交易+可度量"仍有价值，且 spec 0001 的随机化需要它。§5 细则过深且含化石。§9 Benchmark 是对"Universe 选择是否有用"的只读测量——合理，但现货基线已失效。

**判决：SPLIT**
- §1-4、§6-7 留作 Product/Policy（≈150 行）。
- §5 → 移入 `docs/design/contracts/learning_contract.md`（或 observation_lab_contract）的路径注册节，本页只留一句"measurement-supported paths are declared in `extensions/learning/registry.py` and learning_contract §N"。
- §9 → 移入新 Contract `docs/design/contracts/universe_discovery_benchmark_contract.md`（代码已存在，属 current behavior），或 universe 相关现有 contract 的子节。

**具体修改**
1. 删 L8-10、L253 括号、L257 "(#2556)"、L296 "#2601"。
2. L122 "System Operator/ranker/filter" → "Entry Planner prompt/context, Candidate selection, filter"。
3. §5：删除 `ranker_case_score` 作为现行路径的描述，并开 D0 Issue 退役 `extensions/learning/registry.py:83` 该注册项（消费者已不存在）。
4. §9.2 L300-301 `simple_momentum` 改为 perp 双向基线（"abs(return_4h) over perps, direction = sign(return_4h)"）——这是 D1 语义变更，需 owner 批；在批之前文档须如实写"spot baseline is structurally empty under futures-only scope; family `supported_best` cannot be reached"。L276 删 "spot/perpetual de-duplication"。
5. §4 L132 "Startup -> Scheduler -> Outcome -> Verdict" 与 PHASE_GATES:22 不同词表，统一引用 CAPABILITY_MAP 阶段键。

## docs/product/capability-integrity/（README 53 行 + 9 份快照共 2004 行）

**判断对象**：整个子目录该留在 `docs/product/` 还是迁 `docs/research/audits/`。

**事实**
- README.md:7-9 自称 "frozen evidence from the completed review; not a live issue queue"；除 AUDIT_METHOD.md 外其余 8 份文件头均为 "**Archived capability review.** … including the retired local simulator. They do not qualify the current external exchange mainline."（如 `CAPABILITY_INVENTORY.md:3-5`）。最早提交 2026-07-28（#431/#432），即模拟器时代。
- GROUND_TRUTH:67-75 已把它定义为"historical evidence, not a current acceptance matrix"，且平级的 `docs/design/history/local_simulated_capability_review.md` 已存档前一次审查。
- **唯一仍被现行文档依赖的内容**：`AUDIT_METHOD.md:34-36` 的 E1/E2/E3 证据等级——`docs/product/CAPABILITY_MAP.md:34-35` 的成熟度梯直接引用它定义 L2/L3。
- **check_docs 耦合**（迁移必须同步改脚本）：`scripts/ci/check_docs.py:91`（把整个目录列为 CURRENT_DOC_PREFIXES——即把冻结快照当"current doc"做导航校验，本身就是角色错配）、`:115`（README 列为 current file）、`:484`（CAPABILITY_INVENTORY 列入 CODE_PATH_DOCS，强制其中 .py 路径存在——迫使冻结快照随代码删除而被改写，违背"冻结"）、`:1426-1428`（要求 INVENTORY 含 "LN-08 | Learned guard generation and observation"、FACT_CARDS 含 "no learned block/penalty" 作为 semantic truth marker——把历史快照里的字符串当现行真相锚点；且 "Learned guard" 正是 spec 0001 REMOVED 的对象）。
- 入链：`docs/product/README.md:30`、`CAPABILITY_GROUND_TRUTH.md:73`、`CAPABILITY_MAP.md:35`、`docs/design/proposals/lifecycle_capability_map.md:29`、`docs/design/history/local_simulated_capability_review.md`。

**判断**：这 9 份是 Evidence（审计快照），不是 Product。按 documentation_governance 六角色，Evidence 归 `docs/research/audits/`（该目录已有 44 份 dated audit，含同类 `*_AUDIT_2026-*.md`）。留在 product/ 会让读者和 check_docs 都把它们当现行真相——check_docs:1426-1428 已经实际发生了这种误用。

**判决：ARCHIVE → `docs/research/audits/capability-integrity-2026-07-28/`**（整目录迁移，保留文件名；README 头加 "Evidence, frozen 2026-07-28, simulator-era scope"）。

**具体修改**
1. 先把 `AUDIT_METHOD.md:34-36` 的 E1/E2/E3 三行定义**复制进** `docs/product/CAPABILITY_MAP.md` 的 Maturity ladder 节（L31-35），删去对 AUDIT_METHOD 的引用——这是唯一的现行依赖，迁移前必须内联，否则 L2/L3 失去定义。
2. `git mv docs/product/capability-integrity docs/research/audits/capability-integrity-2026-07-28`。
3. `scripts/ci/check_docs.py`：删 `:91` 前缀和 `:115` 文件项；从 `:484` CODE_PATH_DOCS 移除 INVENTORY（冻结证据不应受代码路径校验）；删 `:1426-1428` 两条 semantic marker（与 spec 0001 冲突，且锚错了角色）。
4. 更新入链：`docs/product/README.md:30` 删行；`CAPABILITY_GROUND_TRUTH.md:73` 改新路径；`lifecycle_capability_map.md:29` 改新路径。
5. 不改快照正文（冻结证据不改写）。

## docs/governance/decisions/DECISIONS.md（1404 行；精读 L1205-1404，并扫描全部 44 个条目标题）

**角色**：Decision log。单一角色，每条 Decision/Reason/Guardrail 结构良好。

**结构问题（全文）**
- **排序断裂**：L3-981 为新→旧（2026-10-10 … 2026-06-06），L982-1404 又是旧→新（2026-07-01 … 2026-10-10）。2026-10-10 的条目分散在 L3、L21 和 L1354、L1382 两头；读者"读最近 200 行"会漏掉文件顶部的 SDD 与学习两级决定。
- **缺失 superseded 标记**：44 条中只有 L978 一条带"已被取代"注。至少以下条目与现行真相冲突却无标记：L821 "2026-06-08 Post-MVP scope expansion: multi-asset observation + Polymarket"（与 futures-only、GROUND_TRUTH:57 冲突；代码 `core/aletheia/config/prediction.py` 仍在）；L1089 "Remote CI 维持 manual-only"（AGENTS.md 已改为 "Remote CI is retired: never start"，但 `.github/workflows/ci.yml` 仍存在）；L1136 "2026-09-26 提案不再是前置条件；测试是权威载体" 与 L3 "2026-10-10 approved 规格是实现 PR 的金标准" 未互相说明关系；L677 "CLI 分层 + 运营命令软弃用"（软弃用与 Convergence-over-compatibility 冲突）。

**真相核对（最近 200 行，抽查 12 项）**
- 存在：`core/aletheia/uta/identity.py identity_relation`、`net_clears_tradable_edge_sql`（`core/aletheia/storage/repositories_universe.py` 等）、`core/aletheia/analytics/outcome_semantics.py classify_counterfactual`、`core/aletheia/config/exchange_alignment.py market_data_exchange`、`config/exchanges.py ENTRY_ROUTE_POLICIES`、`universe/features.py CANDIDATE_FEATURE_ACQUISITION_FINGERPRINT`、`economic_utility.build_economic_utility_cohort_index`、测试 `test_reconciliation_covers_every_registered_account_through_one_path`（`tests/test_execution_mode_identity.py`）、`test_one_incomplete_row_does_not_void_a_mature_cohort`。
- 已删除（与"删除"决定一致）：`_reconcile_multibroker_orders`、`resolve_orders_missing_from_exchange`、`_BITGET_ACCOUNT_MODES` 零命中 ✔；`classic_v2` 在 core/extensions .py 零命中 ✔（L89-107 决定已落地）。
- L1218 "Fedora Paper timer"——"Paper" 指 Fedora 运行实例，非模拟器，但措辞易混。

**Bloat（最近 200 行）**：大量 issue 号/commit hash/comment id 当正文（L1229 "#3786 方案 B"、L1231 "4315896b4 … b1ce6baa6"、L1343 "#3765（comment 6079617472）"、L1352 "#3484 已被 owner 关闭"）；运行时快照数字（L1252 "16,097 行里只有 173 行"、L1318-1322 "open issue 615 个…"）——作为当时的 Reason 证据可以保留，但不应再被引用为现状。L1371-1374 "还没收敛的身份键 … 是后续工作" 是进度状态，应移到 Issue。

**合理性**：决策日志本身必要；最近条目（一能力一实现、对账单路径、交易所单开关、准入门槛去重复扣成本）都在主线上，质量高。

**判决：REWRITE（结构）**，内容基本 KEEP。

**具体修改**
1. 统一为新→旧：把 L982-1404 的 18 条按日期插回顶部序列（纯移动，不改文字）。在文件头加一句 "Newest first. An entry that is replaced gets `> Superseded by <date — title>` under its heading; entries are never deleted."
2. 给以下条目加 superseded/clarified 头注：L821（→ 2026-10-09 futures-only, L53）；L1089（→ AGENTS.md "Remote CI is retired"，若 owner 确认则新增一条 2026-10-xx 决定并建议删除 `ci.yml`）；L1136（→ 由 L3 2026-10-10 澄清：spec 仅对 substantial change 是金标准，测试仍是机械门）；L677（软弃用 → Convergence over compatibility）。
3. L1371-1374 "还没收敛的身份键…后续工作" 改为链接一个 Issue，删除进度措辞。
4. 不改 Reason 中的历史数字，但在 L1252、L1318 处加 "(observed 2026-10-0x)" 已有则不动。

## docs/governance/audit_gates_registry.md（74 行）

**角色**：Governance registry（合同条款 → 可执行门）。单一角色，形式正确：每门一行，"契约是权威、门只是检查"（L3-5）。

**执行者核对**：表中 16 个文件全部存在（`tests/audit/` 下 13 个 test + `_segment_guard.py`、`tests/helpers/authority_writer_checks.py`、`tests/specs/spec_agent_contracts.py:292 test_registered_scheduler_jobs_record_swallowed_failures`）；`tests/audit/` 下无未登记的门。

**问题**
- **门的声明超过门的实际检查**：L26 `test_minimum_profit_loop_chain.py` 声称 "The minimum profit loop (Universe → Candidate → EntryPlan → Exit → Outcome) stays wired end to end"。实际该文件（125 行）5 个测试为 `test_time_utils_return_valid_values`（:37）、`test_json_utils_produce_sorted_stable_output`（:51）、`test_table_utils_returns_false_for_none_db`（:69）、`test_entry_snapshot_regime_returns_string`（:79）、`test_candidate_selection_and_planning_chain`（:93）——**没有任何 Exit 或 Outcome 检查**；其 docstring（:1-26，韩文）自己也只列 S1-S4（Candidate/编译器/snapshot/工具函数）。且所引 "contract clause" `CAPABILITY_GROUND_TRUTH.md` 并未定义该链条（grep "minimum profit" 零命中；链条定义在 ACTIVE_RUNTIME_SCOPE `causal_chain`）。这正是 L10 所说"A gate that cannot fail is worse than no gate"的反例：读者会以为主线端到端有门守护。
- **契约条款指向 Evidence 文档**：L22 "R20 analysis §G1"、L23-25 "five-pattern sweep §5.1/5.2/5.3"（`docs/research/audits/FIVE_PATTERN_DISCONNECTION_SWEEP_2026-10-09.md`）——研究审计是 Evidence 角色，不是 Contract；与 L3-5 "contract text stays the authority" 自相矛盾。应指向 trust_and_change_control.md / data_model.md 中的具体条款，审计只作 "origin"。
- L54-66 "Current-contract reconciliation of #3561"：一次性 issue 的处置记录（历史），不是登记内容。

**合理性**：有价值的支撑功能，结构是本域最接近 spec traceability 的文档。

**判决：KEEP（小改）**

**具体修改**
1. L26 改为如实描述："Candidate → Planning Generation → Entry Plan compile stays wired at D0; Exit and Outcome are NOT covered by this gate (see `tests/e2e/test_pipeline_exchange_position_to_close.py`, `tests/e2e/test_outcome_integrity_full_lifecycle.py`)." Contract clause 改为 `ACTIVE_RUNTIME_SCOPE.md causal_chain`。另开 D0 Issue：要么扩展该门到 close/outcome，要么改名 `test_candidate_to_plan_chain.py`；并删掉三个工具函数测试（与"主线链条"无关）。
2. L22-25 "Contract clause" 列只放契约条款；把 R20 / five-pattern sweep 移到新增 "Origin" 列或删除。
3. L54-66 整节移到 DECISIONS（或删除，#3561 已关闭）。

## docs/governance/protocols/BUG_TRIAGE_PROTOCOL.md（101 行）

**角色**：Governance protocol。单一角色，但内容与 `EXPERIMENT_PROTOCOL.md §2`（A/B/C 分类，L34-60）**整体重复**，且第三份在 `docs/operations/development/agent-guides/TASK_ROUTING.md:1-21`。

**真相/一致性核对**
- **与 EXPERIMENT_PROTOCOL 直接矛盾**：本页 L10-12、L25 "A 类—立刻修，不触发实验重置 … 无需关闭实验"；EXPERIMENT_PROTOCOL.md:39-43 "If the repair is deployed to a qualification Runtime … the owner must explicitly decide whether the window restarts; current admission defaults to a fresh window"。TASK_ROUTING.md:15-18 又给出第三种说法（"handle that deployment/window consequence under the Qualification protocol"）。三份文档对同一问题三种答案。
- L88-92 "与实验协议的关系" 引用的 EXPERIMENT_PROTOCOL "允许修改列表/冻结区" 字样在现版 EXPERIMENT_PROTOCOL 中已不存在（现为 §2 A/B/C + §4 Observation freeze）——指针过时。
- L94 "`AGENTS.md` 中的 Change Discipline 节有精简版速查表"——`grep "Change Discipline" AGENTS.md` **零命中**，悬空引用。
- B 类表 L37 "排名公式变化"、L42 "Family quota"、L43 "Debate 权重变化 / 多 agent 投票逻辑"——Ranker 已退役；debate 只剩 `core/aletheia/storage/models.py`、`migrations.py`、`cli/inspect_cmd.py` 的历史读取，无生产决策路径。现行主线的 B 类应是 Entry Planner prompt/context、Candidate 选择效用、Compiler 几何、sizing/leverage、Playbook（spec 0001）。
- C 类 L67 "等 A/B 类问题清零、至少一个完整实验窗口跑通之后再动"——与 owner 2026-10-09 "效率第一、尽快上线" 和 AGENTS.md 的 bounded delegation（owner 选择优先级）有张力；也与 AGENTS D0/D1/D2（语义分类）是两套正交词表，TASK_ROUTING:23-27 已解释正交，本页没有。
- L4 "Created: 2026-07-12" 元数据。

**合理性**：A/B/C 的核心问题（"这个修复会不会让同一市场状况下 AI 做出不同的交易决策？" L78）是好的判据，值得保留一份。

**判决：MERGE into `EXPERIMENT_PROTOCOL.md §2`**（保留 L74-82 裁决原则作为 §2 开头的判据句），本文件删除；TASK_ROUTING.md:20 与 `docs/README.md:43` 的链接改指 EXPERIMENT_PROTOCOL §2。

**具体修改**
1. 把 L78 判据句并入 EXPERIMENT_PROTOCOL §2 首段。
2. 在 EXPERIMENT_PROTOCOL §2 A 类统一一种说法（以 EXPERIMENT_PROTOCOL:39-43 为准，或由 owner 裁决；不得两种并存）。
3. B 类示例替换为现行主线：Entry Planner prompt/context、Candidate utility/admission、Compiler geometry、sizing/leverage、Playbook rules（spec 0001）；删 ranker/debate/family quota。
4. C 类删 "等 A/B 清零" 时间门，改为 "requires an owner-selected Issue"。
5. 删除本文件；更新 `docs/README.md:43`、`TASK_ROUTING.md:20` 链接（archive 下的历史引用不改）。

## docs/operations/deployment/DEPLOYMENT.md（339 行）

**角色**：Operations。单一角色，但混入了 release 控制面的历史叙事（L58-63、L98-103 PR #3506/auto-merge 退役经过）和迁移机制说明（L201-209，属 data_model/persistence 合同）。

**运维命令核对（`uv run aletheia … --help` 实测）**
- 全部有效：`gateway start|stop|restart|status|logs`、`--no-browser`（默认即 no-browser）；`data migrate|capacity --deep --history|venue-margins --venue --symbol --show|compact --apply`；`update`（`--no-restart` 存在）、`update auto install|status|run|resume|uninstall`；`runtime retire-experiment-schema`；`doctor --json|--verbose|--bundle`；`knowledge reindex`。未发现失效命令。
- `.github/workflows/release-please.yml:1` "Prepare and tag releases" 存在；`refresh-release-candidate.yml` 已不存在（L101-103 说的退役属实）。

**真相问题**
- **与 AGENTS.md 冲突（远程 CI）**：L58-61 "只有 owner 在当前任务中明确要求远程 CI，才可在候选分支运行 `Aletheia CI`"——AGENTS.md "Remote CI is retired: never start, wait for, or cite it"。
- **与 AGENTS.md 冲突（Windows）**：L3 "本文描述 Windows 11 与当前 Fedora"；L290-298 "Task Scheduler 看门狗 … 系统已在 Windows Task Scheduler 注册"；命令块标 `powershell`（L23、L224、L249、L269）、L274 `Measure-Command { Invoke-WebRequest … }`。AGENTS.md："macOS is development/control only, Fedora the verification target, Windows never a fallback"。代码仍有 `core/aletheia/gateway/autostart.py`、`managed_service.py`，但 Windows 不是运行目标。
- **部署路径与现实不符（需 owner 确认）**：L52-68 把 Release-Tag + `aletheia-auto-update.timer` 描述为"正常路径"，但 L68/L148-151 又说 qualification runtime 锁定、不随 Tag 变；而 AGENTS.md "Before opening/resetting an Observation window … deploy that exact commit to Fedora" 要求的是**精确 commit** 部署，不是 Tag。当前 Fedora 是 pinned qualification 实例，本页没有一节说"如何把某个 exact commit 部署到 pinned qualification runtime 并验证"——这是 AGENTS 规定的必做操作却无 SOP（L113-128 "应急一键更新" 拉的是 `origin/<update.branch>` HEAD，也不是任意 commit）。
- L65-66、L82-84、L138-151 反复用 "`paper` 角色" / "Fedora Paper Candidate"——此 `paper` 是 runtime role，与已退役模拟器同名；L84 已正确限定 `bitget_demo / external_exchange / live_disabled`。
- L258 "产物写入 `aletheia/dashboard_static/`"——实际 `frontend/vite.config.ts:52` 为 `core/aletheia/dashboard_static`。
- L320-321 常见问题 "历史 bug，已修"、"现已修"——历史条目。
- L45 "(#3729/#3741)"、L176 "(#3719)" issue 号。
- L197-199 指向 draft Proposal `fedora_immutable_runtime_updates.md`——target 内容，Ops 页只需一句。

**合理性**：本页是 owner 操作 Fedora 的唯一入口，必要；但"正常路径"写的是一条当前 qualification 实例不走的路。

**判决：REWRITE**

**具体修改**
1. L3 改："Fedora source-checkout runtime (verification target); macOS is development/control only." 删除 L290-298 Windows 看门狗整节（若仍需保留 Windows 自启说明，移到 `CROSS_PLATFORM_SERVICE_ENVIRONMENT.md`）。命令块 `powershell` → `sh`，L274 改 `curl -w '%{time_total}\n' -o /dev/null -s http://127.0.0.1:8788/api/health`。
2. L58-61 删除"可运行 Aletheia CI"的条件，改为 "Remote CI is retired (AGENTS.md); verification is local (`ci_local.py`) and CASI pytest."
3. 新增一节 "Deploy an exact commit to the pinned qualification runtime"（AGENTS 要求的操作）：步骤 = 停 timer/确认无并发 `aletheia update`（`pgrep`）→ 同步到 exact SHA → `data migrate` → `gateway restart` → 验收 L124-128 五项（boot_commit/current_commit、health、模式组合、provider/settlement）。命令须以实际使用的方式为准（需 owner/操作者提供；本审计未验证 Fedora 侧）。
4. L58-103 的 Release-Tag 自动更新降为子节 "Non-qualification runtime auto-update"，删 L60-63、L98-103 的 PR #3506 叙事。
5. L201-209 迁移边界缩为 3 行并链 `docs/design/data/data_model.md §6`。
6. L258 路径改 `core/aletheia/dashboard_static/`；删 L320-321 两行"已修"条目；删 issue 号。

## docs/operations/runbooks/RUNBOOK.md（318 行）

**角色**：Operations（首次启动+日常运维）。单一角色，但 §2.5 与 DEVELOPMENT_PREVIEW 重复、§9 后半与 DEPLOYMENT 重复（L263-275 自己也写了"authoritative in DEPLOYMENT.md"）。

**运维命令核对（`--help` 实测）**：`install|version`、`gateway start|stop|restart|pause|resume|logs|open-dashboard|enable|disable`、`gateway service install|status|uninstall`、`model set --provider openai_compatible --base-url --model --api-key|status|test`、`secrets set`（`core/aletheia/cli/secrets_cmd.py:12-14` 含 `bitget.demo.api_key/api_secret/passphrase`）、`exchange test`、`permission set`、`inspect config`、`risk status|kill|resume`、`update check|--no-restart`、`update auto install|status|resume`、`doctor --verbose|--deep|--bundle|--fetch|--json` 全部存在。`__version__ = "0.8.6"`（`core/aletheia/__init__.py:3`）吻合。

**真相问题**
- **按本页操作不会产生任何入场（已由代码证实）**：§5 L121-129 与清单 L312 要求 `permission_mode = Paper`，并称 "The current supported permission mode is `Paper`"。但 `core/aletheia/config/exchanges.py:120-121` `bitget_demo` 的 `minimum_permission="Full"`，`core/aletheia/trading/entry_authority.py:31-32` 在 `agent.permission_mode` 低于 Full 时返回 `entry_permission_insufficient`；`ACTIVE_RUNTIME_SCOPE.md:71-72`、`CAPABILITY_GROUND_TRUTH.md:51` 也写 "Current Bitget Demo route requires Full permission"。默认值 `core/aletheia/config/schema.py:444 permission_mode: str = "Paper"`。L125 "(`Research`/`ReadOnly` never trade)" 用的是 operator 权限梯（`agent_shell/permissions.py:12-18`），worker 梯是 `Observe|Paper|Full`（`permission set --help`）。
- **退役机制当现行**：L3-4 "take the system live for **paper trading** … accumulating real paper PnL and strategy lessons"；L119 标题 "Run the autonomous paper loop"、L138 "active paper trading pipeline"；L106-108 "Market data … works without credentials … so the observe/research/paper loop runs out of the box. Demo credentials are only needed if you want authenticated demo calls"——本地模拟器已退役，Bitget Demo 是唯一执行路由，没有凭证就没有交易；"(Optional) Bitget demo credentials"（L104、L311）错误。
- L147 "post-trade review / cases / counterfactuals / Learning measurement → owner-gated bounded influence"——未反映 spec 0001（Playbook 建议级进入 Entry Planner）。
- L94、L112、L205、L289 "CLI (deprecated — still works, dashboard is the maintained surface)"——软弃用与 AGENTS.md "no … soft deprecation" 冲突；而 AGENTS 又说"测试下单只做 CLI"。需 owner 决定 CLI 留或删，不能"deprecated but works"。
- L166-178 Tuning：`trading.default_stop_loss_pct/default_take_profit_pct/max_position_hold_hours` 存在（`schema.py:1585-1587`），但在 Entry Plan 主线里止损/目标由 Compiler 按锚点算出（CAP-EPLAN-01），这些键只是 `position_manager.py:561,577,992` 的兜底；文档把它们写成"protective stop distance"会误导调参。schema 注释 `:1583` 仍写 "Paper position exit rules"。
- L82 `aletheia/dashboard_static/` → 实际 `core/aletheia/dashboard_static`（`frontend/vite.config.ts:52`）；L78 `powershell` 块。
- L296 "during MVP/Round 1/Round 2 diagnosis" 历史；L259-260 "packaged runtime lane was removed on 2026-09-28" 历史。
- L279-280 链 `docs/operations/architecture/STABILITY_TARGET_ARCHITECTURE.md`（存在），但它是"roadmap and phase gates"——Ops 页链到 target 文档。

**合理性**：首次运行手册必要；但它是 owner 最可能照做的文档，目前照做的结果是"零入场"。

**判决：REWRITE**

**具体修改**
1. L1-5 改："How to run Aletheia unattended on the Bitget Demo route (USDT perpetuals). Live stays disabled."
2. §4 改为必需步骤："Bitget Demo credentials (required for trading)"，删 L106-108 "works without credentials … paper loop"；清单 L311 去掉 "(optional)"。
3. §5 L121-129：`uv run aletheia permission set Full`，并写明理由 "The Bitget Demo Entry route requires `Full` (`config/exchanges.py ENTRY_ROUTE_POLICIES`); with `Paper` every Entry Plan is refused with `entry_permission_insufficient`." 删 "Research/ReadOnly" 一句。清单 L312 改 `permission_mode = Full`。标题 "autonomous paper loop" → "autonomous Bitget Demo loop"。
4. L147-148 链 spec 0001 描述学习回路（或只留 "review → learning (see learning_contract)"）。
5. 删除四处 "(deprecated — still works…)"，改为单一表述（由 owner 定 CLI 保留与否）。
6. §6 Tuning 加注："Entry Plan stops/targets come from the Compiler; these keys are fallbacks for Positions without a compiled ExitPlan."
7. §2.5 缩为一句链 DEVELOPMENT_PREVIEW；§9 L263-275 缩为一句链 DEPLOYMENT；修 dashboard_static 路径；删 Round/MVP 字样。

## docs/design/architecture/ARCHITECTURE.md（98 行）

**角色**：导航页（自称 L3 "快速导航层"）。角色基本清楚，但 "Module Seams"（L37-49）是实现细节记录，"UTA boundary"（L69-81）是 Contract 内容。

**真相核对**
- 存在：`--loop none`（`core/aletheia/_gateway_runner.py:243`、`gateway/frontdoor_runner.py:34`）；seam 文件 `storage/repositories_{positions,universe,research,market_data,execution}.py`、`diagnostics/probes/{content,storage_trust,environment,data_quality,trading}.py`、`extensions/observation_lab/canonical_{adapters,lifecycle}.py`、`agent_runtime/scheduler_retention.py`、`analytics/profit_gradient_evidence_utils.py` 全部存在；`core/aletheia/dashboard_static/` 正确。
- L18 `_WRITE_LOCK` 在 `core/aletheia/storage/sqlite.py`（存在）。
- **OKX/Binance 当 peer**：L21 "Core … 访问已注册的 `bitget_demo`、`okx_demo`、`binance_testnet`"——未写 OKX/Binance 冻结；L72-75 "UTA 是六个现有外部执行模式的唯一私有账户读路径 … Binance、OKX 与 Bitget UTA-v3 的差异限制在 profile.py"。六个 = `core/aletheia/config/exchanges.py:54-62` 中除 `paper_internal` 外的 6 项（3 demo/testnet + 3 live_limited）。顺带发现：**`paper_internal: "local"` 仍在代码注册表 `config/exchanges.py:55`**——与 "simulator RETIRED" 不一致（交给 execution 域核查，此处仅记录）。
- L58 "System Operator" 节点：未区分 Entry Planner（LLM hypothesis）+ Compiler；与 ACTIVE_RUNTIME_SCOPE:66-69 的 Candidate → Planning Cycle → Entry Plan 链比，缺 Compiler 和 Outcome/Learning 两端。L64 止于 Position Manager，没有 verified close → Outcome → review。
- L71 链 `proposals/unified_trading_account.md` 说"已接受"——accepted Proposal 若已实现应进 Contract。
- L94 "架构全景（8章）→ SYSTEM_ARCHITECTURE_GUIDE"——见下一节，该文件本身有问题。

**Bloat**：L49 "以 Git 历史与 #3561 为准"；L32-33 解释"旧树写错了"的历史。

**合理性**：Tech Stack 表 + 一张主线图 + 链接，是有用的 20 行。Seams 表属代码导航，应归 SYSTEM_MAP（AGENTS.md 规定 SYSTEM_MAP 是唯一代码地图）。

**判决：REWRITE（小）**，并吸收 SYSTEM_ARCHITECTURE_GUIDE 的有效部分（见下节）。

**具体修改**
1. L21 改："Core reaches venues only through the injected Exchange Extension via UTA/Coordinator. Active route: `bitget_demo` (Bitget UTA v3). `okx_demo`/`binance_testnet` registrations are frozen (Capability Ground Truth)."
2. L55-65 主线图替换为 ACTIVE_RUNTIME_SCOPE 链：Universe → Candidate → Planning Generation → Entry Planner (LLM hypothesis) → Compiler → immutable Entry Plan → Proposal → Risk → Gateway → UTA → Order/Fill/Position → Position Manager → verified close → Outcome → review/Playbook（spec 0001）。
3. L37-49 Module Seams 移到 SYSTEM_MAP.md（单一代码地图）。
4. L69-81 UTA 节缩为 2 行链 execution_contract / UTA 合同；"六个模式"改为 "the active route and frozen registrations"。
5. L94 指向待定（若 SYSTEM_ARCHITECTURE_GUIDE 被归档，则删除该行）。

## docs/design/architecture/SYSTEM_ARCHITECTURE_GUIDE.md（223 行）

**角色**：解释性导览（L3 自称"不是能力或运行状态权威"）。但 8 章中约一半篇幅在说"什么已经退役"（L22-24、L73-78、L133-134、L153-154、L175、L181-195、L199-202）——这是 history 内容。

**真相核对**
- 5 张图 `docs/diagrams/rendered/readme_*.svg` 存在（但是否已更新到当前链条未核）。
- L35 "获取 crypto **spot/perpetual** 市场事实"——与 futures-only 冲突。
- **Entry Planner 上下文描述不实**：L100-103 "学习与历史证据：上次决策回顾、**Knowledge Lessons（犯过的错）**、Serenity Research"；L88 "有界 Learning bundle"。`grep -c -i lesson` 在 `core/aletheia/agent_shell/entry_planner.py`、`trading/entry_analysis.py`、`trading/entry_learning.py` 均为 **0**——Entry Planner 现在不读 lessons（与 spec 0001 Problem 节 "no legitimate path from a lesson to the next entry decision" 一致）。本页把 spec 0001 要建的东西写成了现状。
- L73、L211 "equity/prediction observer … 写带来源事实"作为现行组件；代码 `core/aletheia/agent_runtime/scheduler.py:1580-1603` 仍条件注册 `equity_observer`（美股时段）与 `prediction_observer`（每 30 分钟）。GROUND_TRUTH:57 "Ordinary equities and prediction markets: Outside active instrument scope"——文档和调度都留着范围外的生产者（推荐交给 scheduler 域核查是否应删除）。
- L142 "Position Manager 每 30 秒"：`scheduler.py:1498` `seconds=30` ✔。L146-147 "追踪止损（ATR-based）/保本移动"：`position_manager.py:659-678 _resolve_trailing` 只读 ExitPlan 冻结的 trailing 合约；`entry_plan_compiler.py` 中 `trailing` 零命中——Compiler 不产出 trailing，主线上该机制推断为无生产写入方（未在运行时验证）。
- L48-52 `universe_feature_snapshots` 特征 `vol_ratio/relative_strength_24h/oi_price_divergence/taker_buy_ratio` 存在于 `universe/features.py:60-68`。
- L222 `aletheia control status --json` 有效。
- L174 "measurement-unsupported 的 ranker、persona 等 hook"——Ranker 已退役。

**合理性**：与 ARCHITECTURE.md 是两份"架构导览"，与 SYSTEM_MAP.md 是第三份（AGENTS.md 规定 SYSTEM_MAP 为唯一代码地图）。"小型对冲基金"比喻对 onboarding 有一定价值，但当前内容一半是退役清单、一半在复述 entry-plan-contract。

**判决：ARCHIVE**（移到 `docs/design/history/system_architecture_guide_2026.md`），把仍有效的 2 项并入 ARCHITECTURE.md：第三章的主线链（L62-71）与第六章的 Position Manager 表（修正 trailing 行后）。

**具体修改**（若 owner 选择保留而非归档，则按 REWRITE）
1. 删除所有"已退役"段落（L22-24、L73-78、L133-134、L153-154、L175、L181-202），改为一句链 `docs/design/history/README.md`。
2. L35 → "USDT-margined crypto perpetual market facts"。
3. L100-103 删 "Knowledge Lessons"、"Serenity Research"，按 entry-plan-contract 实际 context（market/structure/anchors/portfolio/execution）列出；注明 Playbook 由 spec 0001 引入、尚未实现。
4. L73、L211 删 equity/prediction observer（或标"out of scope; scheduled only if extension present"）。
5. L146-147 trailing/breakeven 行注明 "only when frozen on the ExitPlan; the Compiler does not currently emit trailing"。
6. L174 删 ranker。

## docs/design/operator/operator_layer.md（1312 行）

**角色**：混合且自相矛盾。L2-13 自称 "concept/history and must not be read as implementation status"，现行事实在 `docs/design/architecture/OPERATOR_SHELL.md`（存在，L7-11 反向指回本文为 "design history"）。但文件仍放在 `docs/design/operator/`——`scripts/ci/check_docs.py:101` 把该前缀列为 CURRENT_DOC_PREFIXES，即 check_docs 当它是现行文档。正文包含：产品定义（L88-122 自愈档位/不变量）、2026-06 的 OpenAlice/Pi 研究（§3-6）、提案（§7-9、§13-16 Stage 1-6）、命令注册表（§10）、一段给 Claude Code 的实施提示词（§24 L1200-1270）、愿景（§25）。

**真相核对**
- §10 命令注册表（L538-575）多数不存在：`get_strategy_lifecycle`、`update_paper_playbook`、`create_strategy_draft`、`create_paper_experiment`、`propose_strategy_promotion`、`propose_live_limited_enablement` 在 `core/`、`extensions/`、`operator-shell/src` 中**零命中**；实际注册表 `core/aletheia/agent_shell/registry.py:36-210` 是另一组（`get_operator_status`、`explain_agent_run`、`create_dreaming_log`、`create_improvement_proposal`、`draft_extension_manifest` …）。
- L587-606 "loosen_confidence / increase_sizing — 有界自动恢复例外（Owner decision 2026-07-13）"：`loosen_confidence`、`increase_sizing`、`reduce_sizing` 代码**零命中**；DECISIONS.md 也无 2026-07-13 条目。一条 owner 决定只活在一份 history 文档里，且实现已消失——应在 DECISIONS 记为 retired。
- L94-104 自愈 L1 `clean_stale_heartbeats` 等零命中（文中也说已撤下）；L106-110 `_LEVEL_2_ACTIONS` 零命中 ✔（与"retired"一致）；`pi_operator_loop` 零命中 ✔。
- §20 表（L1074-1085）：`agent_shell_sessions/messages/commands/tasks/artifacts`、`extension_registry/runs` 在 `storage/migrations.py` 均存在；`command_audit_events` 不存在。
- L54-56 引用 `tests/specs/spec_operator_single_source.py`、`spec_operator_contract_parity.py`：存在。
- L608-627 权限梯 ReadOnly/Research/PaperAuto/AllowEditing/Auto 与 `agent_shell/permissions.py:12-18` operator 梯一致，但 "Paper Auto: paper experiments + paper strategy updates" 描述的是已删除的能力。
- 与 spec 0001 命名冲突：L551 `update_paper_playbook`（Operator 改 playbook）与 spec 0001 LL-R09 "One Playbook store"（由学习回路写、compare-and-set）同名不同义。

**合理性**：作为 2026-06 的设计史有参考价值；作为现行文档是误导源（1312 行中现行有效的只有 L112-122 四条自愈不变量）。

**判决：ARCHIVE → `docs/design/history/operator_layer_concept_2026-06.md`**

**具体修改**
1. 把 L112-122 "Invariants for surviving self-heal paths" 4 条迁入 `OPERATOR_SHELL.md`（或 `agent_contracts.md` 的 Operator 节），改为 EARS 句式，例："If an action requires approval, then the operator-shell shall not execute it unless an approval record from an independent actor exists."
2. `git mv` 整份到 history；删除 L14-20 命名说明、L57-84 落锚说明（history 里不需要再声明自己是历史）。
3. `OPERATOR_SHELL.md:7-11` 链接改新路径；`PI_OPERATOR_DESIGN_DECISIONS.md` 同目录也应一并评估（不在本审计范围）。
4. 在 DECISIONS 增一条："2026-07-13 loosen_confidence / increase_sizing bounded auto-recovery — retired with the Python operator loop (#3134); no current implementation."

## 跨文档问题

### 产品四文档的重复与冲突（SCOPE / DIRECTION / CAPABILITY_MAP / GROUND_TRUTH）
| 问题 | GROUND_TRUTH | SCOPE | DIRECTION | CAPABILITY_MAP | 其他 |
|---|---|---|---|---|---|
| 交易品种范围 | L56 仅 USDT 永续、现货出范围、RWA 拉黑 ✔ | 只字未提（L41） | 只说"以后可扩展更多市场"（L103-112） | L93 "crypto spot/perp"、L184 "spot never exceeding cash" ✘ | SYSTEM_ARCH_GUIDE:35 "spot/perpetual" ✘；EXPERIMENT_PROTOCOL:300-301 现货基线 ✘ |
| 学习的地位 | L7-8 "research/Learning … frozen or optional" | L47 "Optional research Extension, not required Slim Core" | L68-78 第 2 步"闭合证据→学习回路" | LEARN 是主线 11 段之一 | spec 0001（approved, D2）+ owner"复盘→教训→下次决策是主干"。**GROUND_TRUTH 与 SCOPE 都与 owner 定位冲突**——GROUND_TRUTH 不在我的范围，但它是冲突源，需一并改 |
| Entry 权限 | L51 Bitget Demo 需 Full | — | — | — | ACTIVE_RUNTIME_SCOPE:71-72 Full；代码 `config/exchanges.py:121` Full；**RUNBOOK §5/L312 写 Paper** ✘ |
| CN Equity | L57 disabled | L54-72 全文复述 | — | — | ACTIVE_RUNTIME_SCOPE:5-15、75-83；PHASE_GATES:35-54。四处描述、**零行代码**（`grep cn_equity --include=*.py` 0 命中），与 futures-only 方向无关 → 建议 owner 决定撤销 `authorized_extensions.cn_equity` 或保持并只留一处 |
| "怎样算主线通过" | — | — | — | L0-L4 + 每能力 L4 判据 | PHASE_GATES Gate A（另一套阶段词表、无执行器）；EXPERIMENT_PROTOCOL:132 又一套 "Startup → Scheduler → Outcome → Verdict" |
| 路由/"怎样读真相"段 | L25-31 | L110-120 | L8-13 | L23-29 | 四份各写一遍 |

结论：GROUND_TRUTH + CAPABILITY_MAP 足以承载产品真相（边界 + 能力定义）；SCOPE 应并入 GROUND_TRUTH；DIRECTION 保留为唯一方向页；PHASE_GATES 的 Gate A 应改用 CAPABILITY_MAP 等级。

### 同一问题多份答案（governance）
- **A 类修复要不要重开窗口**：BUG_TRIAGE_PROTOCOL:10-25（不要）vs EXPERIMENT_PROTOCOL:39-43（默认要，owner 定）vs TASK_ROUTING:15-18（按 Qualification protocol 处理）。
- **远程 CI**：AGENTS.md（已退役，不得启动/引用）vs DECISIONS:1089（manual-only，未标 superseded）vs DEPLOYMENT:58-61（owner 要求时可跑）；`.github/workflows/ci.yml` 仍存在。
- **CLI 软弃用**：RUNBOOK 四处 "deprecated — still works" + DECISIONS:677 "运营命令软弃用" vs AGENTS.md "no … soft deprecation"。
- **Windows**：DEPLOYMENT:3、290-298 vs AGENTS.md "Windows never a fallback"。

### 有代码但缺 Contract 的主线/支撑行为
- Universe Discovery Benchmark（`core/aletheia/universe/discovery_benchmark.py`、`cli/evidence_cmd.py:67-119`）：完整算法规格只在 EXPERIMENT_PROTOCOL §9（Product 文档），无 contract。
- 把 exact commit 部署到 pinned qualification runtime：AGENTS.md 规定的必做操作，DEPLOYMENT/RUNBOOK 都没有 SOP。
- Go-Live Scorecard 的 fail-open 50 分（`go_live_scorecard.py:427-438,474,515`）：METRICS 未披露，live_unlock_contract 是否允许未核。

### 与 spec 0001 的冲突
- CAPABILITY_MAP CAP-LEARN-02（lesson_reviewer + TTL）、CAP-LEARN-03（caller `extensions/knowledge/guards.py`）——spec 0001 §REMOVED（spec.md:282-285）。
- SYSTEM_ARCHITECTURE_GUIDE:100-103 声称 Entry Planner 读 "Knowledge Lessons"——代码零命中；这正是 spec 0001 要新建的能力。
- METRICS §5 conviction calibration 读 `pi_decisions.conviction`，主线不写；spec 0001 LL-R01 `p_target_first` 是替代输入。
- EXPERIMENT_PROTOCOL §5 未登记 Playbook on/off 路径，且 `mechanism_entry_learning` 与 Playbook adapter 须互斥（spec.md:212）。
- operator_layer:551 `update_paper_playbook` 与 spec 0001 Playbook 同名不同义。
- check_docs.py:1426-1428 用冻结快照里的 "LN-08 | Learned guard generation" 当 semantic truth marker——spec 0001 要删 learned guards，届时该锚会阻止或误导修改。

### 顺带发现的代码化石（交给对应域核查，未在本域修）
- `core/aletheia/config/exchanges.py:55` 仍注册 `"paper_internal": "local"`（core 中 17 处 `"paper_internal"` 引用）。
- `extensions/learning/registry.py:83` `ranker_case_score` consumer = "paper ranker"（Ranker 已退役）。
- `core/aletheia/agent_runtime/scheduler.py:1580-1603` 条件注册 `equity_observer`、`prediction_observer`（范围外市场）。
- `extensions/analytics/confidence_calibration.py:40-47` 依赖主线不再写入的 `pi_decisions`。
- `tests/audit/test_minimum_profit_loop_chain.py` 名不副实（无 Exit/Outcome 检查）。

## 总表

| 文档 | 行数 | 角色 | 判决 | 首要问题（一行） |
|---|---:|---|---|---|
| docs/product/SCOPE.md | 120 | Product（纳入/排除） | MERGE into CAPABILITY_GROUND_TRUTH | 漏写 futures-only；把 Learning 定为"可选研究扩展"，与 owner/spec 0001 冲突；纳入表与 GROUND_TRUTH 重复 |
| docs/product/CAPABILITY_MAP.md | 434 | Product registry | KEEP（小改） | 35 个 probe 全部存在；但 CAP-CAND-01 写 "spot/perp"，LEARN 节仍是 spec 0001 要删的 lesson_reviewer/guards |
| docs/product/definition/DIRECTION.md | 149 | Product（方向） | KEEP（小改） | 2026-07-30 后未吸收 futures-only 与"AI 像人一样交易"；"paper influence" 易混 |
| docs/product/gates/PHASE_GATES.md | 113 | Product（门禁） | REWRITE | Gate A 无执行器且与 CAPABILITY_MAP 等级重复；Gate CN 约束零行代码；B-D 只是指针 |
| docs/product/gates/METRICS.md | 116 | Product（指标） | REWRITE（小） | Scorecard 权重与代码一致，但 10% 的 conviction 校准读主线不写的 pi_decisions；未披露 fail-open 50 分 |
| docs/product/experiments/EXPERIMENT_PROTOCOL.md | 336 | 混合（Policy + Contract 细则） | SPLIT | §5 含退役 ranker 路径；§9 benchmark 现货基线在 futures-only 下结构性为空；A 类规则与 BUG_TRIAGE 矛盾 |
| docs/product/capability-integrity/（9 份） | 2057 | Evidence（冻结快照） | ARCHIVE → research/audits/capability-integrity-2026-07-28/ | 模拟器时代的快照放在 product/，check_docs 还把其中字符串当现行真相锚；先把 E1/E2/E3 内联进 CAPABILITY_MAP |
| docs/governance/decisions/DECISIONS.md | 1404 | Decision log | REWRITE（结构） | 排序断裂（前半新→旧、后半旧→新）；44 条仅 1 条有 superseded 标记（Polymarket、远程 CI、软弃用未标） |
| docs/governance/audit_gates_registry.md | 74 | Governance registry | KEEP（小改） | 16 个门全部存在；但"minimum profit loop 端到端"门实际没有 Exit/Outcome 检查；3 个门的条款指向研究审计 |
| docs/governance/protocols/BUG_TRIAGE_PROTOCOL.md | 101 | Protocol | MERGE into EXPERIMENT_PROTOCOL §2 | 与 EXPERIMENT_PROTOCOL 的 A 类规则矛盾；引用不存在的 AGENTS "Change Discipline"；B 类举例是 ranker/debate |
| docs/operations/deployment/DEPLOYMENT.md | 339 | Operations | REWRITE | 命令全部有效；但写的是 Windows+远程 CI+Tag 自动更新，缺 AGENTS 要求的"exact commit → pinned Fedora"SOP |
| docs/operations/runbooks/RUNBOOK.md | 318 | Operations | REWRITE | 照做会零入场：要求 `permission set Paper`，而 bitget_demo 需 Full；凭证写成"可选"，仍是 paper loop 叙事 |
| docs/design/architecture/ARCHITECTURE.md | 98 | 导航 | REWRITE（小） | 把 OKX/Binance 当 peer；主线图止于 Position Manager、无 Compiler/Outcome；Seams 表应归 SYSTEM_MAP |
| docs/design/architecture/SYSTEM_ARCHITECTURE_GUIDE.md | 223 | 解释性导览 | ARCHIVE（有效两段并入 ARCHITECTURE） | 一半篇幅是退役清单；声称 Entry Planner 读 Knowledge Lessons（代码零命中）；写 spot/perpetual |
| docs/design/operator/operator_layer.md | 1312 | 混合（概念/提案/历史/提示词） | ARCHIVE → design/history | 自称历史却在 current 目录、被 check_docs 当现行；§10 命令多数不存在；2026-07-13 owner 决定的实现已消失 |
