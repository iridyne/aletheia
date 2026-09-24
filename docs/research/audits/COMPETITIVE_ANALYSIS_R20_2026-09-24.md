# 竞品对标分析：R20 Quantum Trader vs Aletheia

> 分析日期：2026-09-24
> 竞品仓库：`/Users/yixian/Projects/references/r20-quantum-trader`（`github.com/555cute/r20-quantum-trader`）
> 竞品版本：`v8.3.0`，HEAD `34a0d29`，2026-09-24 09:15 +0800（本次分析前已 `git fetch` + `--ff-only` 同步，本地此前停在 2026-09-07，落后 **1011 个 commit**）
> Aletheia 基线：`master@a569a4c36`
> 分析者：Claude Opus 5。所有数字均为本机实测，口径写在各表脚注；推断部分已显式标注。

---

## 0. 一句话回答你的问题

**你没有输在速度上，你输在"产出的去向"上。**

两边每天写的生产代码量几乎一样（R20 每天 2132 行，Aletheia 每天 2297 行，Aletheia 甚至略多）。差别在于：

- R20 的产出去向是 **功能代码 + 门禁测试**（测试/生产比 **2.60**，文档只占产出的 **4%**）
- Aletheia 的产出去向是 **功能代码 + 文档**（测试/生产比 **1.01**，文档占产出的 **55%**）

**R20 把工程纪律编译成了会翻红的测试；Aletheia 把工程纪律写成了需要人去遵守的文字。**

前者的边际成本随代码量增长而下降（写一次门禁，永久拦截）；后者的边际成本随代码量增长而上升（每个 PR 都要人肉核对 12000 行规则）。这是复利方向的差异，不是努力程度的差异。用 Gemini/DeepSeek 还是 Opus 5 与此无关——**模型再强也无法把"文字规则"变成"自动拦截"**。

---

## 1. 量化对比（全部本机实测）

### 1.1 规模与节奏

| 维度 | R20 | Aletheia | 差异 |
|---|---:|---:|---|
| 首个 commit | 2026-08-30 | 2026-06-03 | |
| 开发天数（至 2026-09-24） | **25 天** | **113 天** | Aletheia 4.5× |
| 总 commits | 1,317 | 4,431 | |
| commits / 天 | 52.7 | 39.2 | R20 1.3× |
| 最近 7 天 commits | 423 | 243 | R20 1.7× |
| 生产 Python 行数 | 53,305 | 259,601 | Aletheia **4.9×** |
| **生产代码 / 天** | **2,132** | **2,297** | **基本持平** |
| 测试 Python 行数 | 138,808 | 262,405 | Aletheia 1.9× |
| **测试 / 生产 比** | **2.60** | **1.01** | **R20 2.6×** |
| 测试文件数 | 493 | 740 | |
| 测试用例数 | 7,488（实测执行） | 11,516（实测收集） | |
| 前端行数 | 37,588（Vue 3） | 65,607（React 19） | Aletheia 1.7× |

口径：生产代码 = 排除 `tests/`、`.git`、`node_modules` 的 `.py` 行数；Aletheia 统计 `core/ extensions/ ops/ legacy/`。

### 1.1b 反馈循环速度（实测，这一项直接决定迭代速度）

| 维度 | R20 | Aletheia |
|---|---:|---:|
| 测试收集 | — | 11,516 个 / **3.26s**（很快，不是瓶颈） |
| 全量执行 | 7,488 个 / **74.6s** | 禁止在 macOS 跑（AGENTS.md） |
| **最快层执行** | — | `tier_unit` **8,495 个 / 300s 超时仍未跑完（卡在 93%）** |

**这是被严重低估的一项。** `tier_unit` 是我们**最快的一层**（纯单元/纯函数），8,495 个用例 5 分钟跑不完。这意味着任何一次"改代码 → 看反馈"的循环，最快的完整单元层反馈都是**分钟级**。R20 的全量 7,488 个用例是 **74.6 秒**。

诚实标注：R20 那 74.6s 里有 40 failures + 742 errors，是我本机缺依赖导致的快速失败，所以这个数字**偏乐观**，不能直接当"R20 比我们快 4 倍"用。但"我们最快的一层要分钟级"是一个独立成立的硬事实——它不依赖 R20 的数字。

反馈循环速度对 AI 协作的影响是**乘性的**：AI 每轮迭代都要等这个时间。分钟级反馈下，一个需要 10 轮试错的问题要等 10 分钟以上；秒级反馈下是 1 分钟。这解释了一部分"为什么便宜模型反而更快"——**它们的循环转得更快，而不是想得更对**。

### 1.2 文档与治理（差异最大的一项）

| 维度 | R20 | Aletheia | 差异 |
|---|---:|---:|---|
| Markdown 总行数 | **2,195** | **142,814** | **Aletheia 65×** |
| Markdown 文件数 | 15 | 583 | Aletheia 39× |
| **文档 / 生产代码 比** | **4.1%** | **55.0%** | **Aletheia 13×** |
| AI 治理文件（AGENTS/CLAUDE/GEMINI/.cursorrules） | **0 个** | AGENTS.md 171 行 + 6 份 agent-guides 516 行 | |
| 契约文档 | **0 行** | `docs/design/contracts/` 9,144 行 | |
| 治理政策文档 | **0 行** | `docs/governance/` 2,474 行 | |
| 产品真相文档 | **0 行** | `docs/product/` 3,629 行 | |
| **治理层合计** | **0 行** | **约 15,900 行** | |
| 门禁测试（audit 类） | **48 个文件** | 分散，无独立 audit 目录 | |
| open issues | 无 `.github/` | **200** | |

我逐个核实过：R20 仓库里**不存在** `AGENTS.md`、`CLAUDE.md`、`GEMINI.md`、`QWEN.md`、`.cursorrules`、`.github/`、`docs/governance/`、`docs/design/contracts/`、`docs/**/decisions/`、`proposals/`。零 AI 协作治理文件，零契约层，零 issue 跟踪层。

### 1.3 可交付性

| 维度 | R20 | Aletheia |
|---|---|---|
| 版本发布 | v7.0.0 → **v8.3.0**，20+ 个 git tag | 无 release tag 序列 |
| 容器化部署 | `Dockerfile` + `docker-compose.yml` + `deploy/docker-start.sh` | 无 |
| 服务单元 | 3 个 systemd unit | 手工 + Fedora 远程机 |
| 部署文档 | `STANDALONE.md` + `RECOVERY_GUIDE.md`（含灾备恢复手册） | 分散在 `docs/operations/` |
| 接入交易所 | **OKX + Binance + Gate 三所平权** | Bitget Demo 一所 |
| 端到端真实证据 | 结构优化期间"实盘交易周期零异常"（自述，见 `docs/STRUCTURE_OVERVIEW.md`） | **#2899 仍 open**：Bitget Demo Position → Close → cost-complete Outcome 未取得 |
| 用户可配置面 | 8 大前台可视化配置中心（提示词/投委会/拦截器/风控/LLM/快照/自进化/多所） | Dashboard Settings |

---

## 2. 你困在哪里：四个根因

### G1 · 治理写成了文档，而不是编译成门禁 —— 这是主根因

R20 的做法是：**每一条工程纪律都有一个会翻红的测试**。`tests/audit/` 48 个文件，全是这种门：

| R20 门禁测试 | 它强制什么 | Aletheia 的对应物 |
|---|---|---|
| `test_failure_semantics_doc.py` | 校验 `FAILURE_SEMANTICS.md` 里每个代码锚点路径真实存在、**每个门禁锚点指向的是可加载的真实 TestCase**（不是注释里的同名文本）、每行"方向"出自认可词表 | 无。`docs/design/contracts/` 9,144 行靠人读 |
| `test_directory_docs_current.py` | 磁盘上的 `.py` 模块与 `__init__.py`/README 清单**双向集合比对**，漏登记或指向不存在的文件都翻红 | 无 |
| `test_readme_baseline_numbers.py` | README 里写的测试数字必须与仓内真实用例数同量级 | 无。README 数字会腐烂 |
| `test_circuit_breaker_twin_parity.py` | 熔断判定的两份实现（后端风控面 vs trader 活路径）**决策序列必须逐项相等**，"加固只改一边"立刻红 | 无。靠 AGENTS.md 文字规定"一个能力只留一份实现" |
| `test_no_vacuous_assertions.py` / `test_tests_cannot_pass_vacuously.py` | 防止空洞断言、防止测试因环境原因静默通过 | 无 |
| `test_timestamp_unit_convention.py` | `_ms` 字段必须是毫秒、`_ts` 必须是秒，**例外表已清空**，旧名不得回流 | 无。记忆里我们踩过"跨 venue 单位混用"的坑 |
| `test_day_key_uses_beijing_tz.py` | 日切键必须可证明是 +08:00，裸 `now()`/`utcnow()`/`timezone.utc` 一律翻红 | 无 |
| `test_module_free_names.py` | 模块自由名扫描（防止隐式全局依赖） | 无 |
| `test_production_data_isolation.py` | 测试不得读写生产 data/ | 部分有（`ALETHEIA_DATA_DIR`），非强制门 |

**对比我们的机制**：AGENTS.md 写着"One capability keeps exactly one live implementation... converge them and **delete** the loser"。这是一句**需要 AI 自觉遵守、需要人 review 发现违反**的文字。R20 的 `test_circuit_breaker_twin_parity.py` 是**机器强制**的同一件事。

**这就是为什么我们需要 15,900 行治理文档而 R20 需要 0 行**：文档在替代测试做本该由测试做的事。而文档做不到这件事——它只能描述，不能拦截。

**代价的量化**：我们最近 50 个 commit 里有 18 个带 `SDD Depth`、20 个带 `Contract Delta`。一个典型的 squash commit（`a569a4c36`）包含 6 个治理段落：Linked Issue / SDD Depth / Contract Delta / Does this change alter semantics / Verification / Runtime qualification。R20 的等价 commit message 是**一行**：

```
test(gateway): 通道适配器与事件模型 —— 结构交付与不可变序列化（收口：r20_gateway/channels.py & events.py）
```

R20 不需要在 commit 里声明"我没有改变语义"，因为**对拍门已经证明了这件事**。

### G2 · 审计驱动开发形成了自我指涉循环

我们 200 个 open issue 的构成：

| 标签 | 数量 | 占比 |
|---|---:|---:|
| `type/gap` | 63 | 32% |
| `area/tests` | 44 | 22% |
| `type/umbrella-child` | 18 | 9% |
| `type/umbrella` | 7 | 3.5% |
| `type/audit` | 2 | 1% |
| `type/regression` | 1 | 0.5% |

**63 个 `type/gap` + 44 个 `area/tests` = 一半以上的 issue 是"审计发现代码与契约不符"，不是"用户要这个功能"。**

这形成一个闭环：审计 → 发现 gap → 开 issue → 修 → 触发新审计 → 发现更多 gap。而且我今天实测确认了这个循环在加速：

- #3432（High）由 **#3429 修复本身引入**，post-merge review 于今天 00:12 开出
- #3433（Medium）由 **#3420 修复本身引入**，同批开出

**更关键的是 #3134 已经不是一个 issue，而是一条总线**：最近 120 个 merged PR 里有 **113 个**引用它。这意味着它作为"跟踪单元"已经失去了信息量——它不告诉你任何 PR 在做什么。

R20 没有 issue 跟踪层（无 `.github/`）。它的跟踪单元是"刀"。

### G3 · 工作单元的定义不同：「刀」vs「issue + 治理表单」

R20 用**编号的"刀"**作为工作单元，我从 commit history 里数到**第 169 刀**。每一刀的形状是：

```
第 N 刀 → 一个明确的收口目标（收口：<具体文件>.py）
        → 一个 AST 对拍门（tests/extraction/，共 76 个）
        → 一行 commit message
```

`tests/extraction/test_trader_order_lifecycle_extraction.py` 是个典型样本，它做的事：

1. 记录动工前的 commit（`PRE = "9dccec0"`）
2. `git show PRE:file` 取出改动前的源码，与改动后的**逐个函数体 AST 全等比对**（含嵌套闭包）
3. 任何差异必须进白名单，且白名单**必须断言"恰好出现一次"**（写错立刻红，不会静默放水）
4. 白名单里每条差异都要有配套的正向测试钉住新行为真的生效

这就是 R20 敢把 `ai_factor_trader.py` 从 **3565 行砍到 1108 行**（最大函数 1021 行 → 142 行）、把 `LlmPage.vue` 从 **1762 行砍到 39 行**的原因：**AST 等价性是机器证明的，不需要人来相信"我只是搬了家"。**

我们的等价物是什么？是 AGENTS.md 里的 D0/D1/D2 分类 + `Contract Delta: NONE` 声明 + 人工 review。这是**声明式的信任**，不是**证明**。我记忆里那条"判定 #2945 删除是否回归"的教训——"先读 contracts，只读被删调用点会得出相反结论，我踩过并回退 5 个文件"——如果有 AST 对拍门，那 5 个文件根本不会被错误回退。

### G4 · 交付定义缺失：我们验收"契约一致"，R20 验收"能用"

R20 的验收矩阵（`docs/STRUCTURE_OVERVIEW.md` §2）长这样：

| 检查 | 结果 |
|---|---|
| 后端全量 unittest | 3133 OK |
| 离线套件（无外泄自检） | 3114 OK，`CONFIG_WRITE_ATTEMPTS: []`，EGRESS 自检通过 |
| 前端 `vue-tsc --noEmit` | 干净 |
| 前端 `vite build` | 成功（1.64s） |
| **实盘交易周期 13:00/13:15/13:30/13:45/14:00** | **异常行 0** |
| 标的池 sha256 | 未变（`a08684e8`） |
| 工作树 | 干净 |

注意最后几行：**它把"实盘 5 个周期零异常"和"标的池哈希未变"作为结构重构的验收项**。这是"能用"的验收。

我们的验收是什么？`a569a4c36` 的 Verification 段：217 passed + 169 passed + ruff pass，然后 Runtime qualification 段写着"**Not claimed by this source repair**"。

**我们把"能用"从验收里拿掉了，理由是治理正确——"merge / deployment / runtime proof 是三个独立声明"。这在逻辑上无懈可击，在交付上是灾难**：每个 PR 都合法地不承担"能用"的举证责任，于是 113 个 PR 合并之后，#2899（Bitget Demo 端到端 Outcome）依然 open。

配合我记忆里的事实：Fedora 从未接通过 Bitget 真实凭证、Gate B 卡在风控止损距离门。**我们有 11,516 个测试全绿，但没有一笔完整的真实交易闭环证据。**

---

## 3. 一个必须说清的反论：R20 的做法不是"没有纪律"

我要防止这份报告被误读成"R20 靠糙快猛赢了，我们该放弃严谨"。**恰恰相反。**

R20 的工程纪律在若干维度上**比我们更严**：

- 它的 `FAILURE_SEMANTICS.md` 把"读不到 ≠ 没有"、"不可判定 ≠ 安全"、"日志不说谎"三条纪律落成了 **28 行输入失败语义总表**，每行绑定代码锚点 + 门禁锚点，且有测试校验锚点真实可加载。我们的 `docs/design/contracts/` 有 9,144 行，但没有任何测试校验契约与代码的对应关系。
- 它的测试/生产比是 **2.60**，我们是 **1.01**。
- 它的失败语义表里写着"用户拍板 fail-closed"这种**决策归属标注**，比我们的 D0/D1/D2 分类更直接地记录了"谁决定的"。

**差异不在严谨程度，而在严谨的载体**：R20 的严谨住在 `tests/audit/` 和 `tests/extraction/` 里（会翻红），我们的严谨住在 `docs/` 里（要人读）。

同时，R20 也有我们明显更强的地方，不能只看它的长处：

| 维度 | 谁更强 | 说明 |
|---|---|---|
| 类型安全 | **Aletheia** | 全量 pydantic v2 + typed DTO（`FillReport`/`OrderReport`）；R20 大量 `dict` 传递 |
| 前端工程化 | **Aletheia** | React 19 + 从后端 OpenAPI 生成类型 + `check:api-types` 漂移门；R20 无类型生成 |
| 数据层治理 | **Aletheia** | additive-only 迁移 + schema 版本 + 审计日志；R20 用 JSON 文件 + 单 SQLite |
| 可观测性深度 | **Aletheia** | lineage / 因果链 / policy_epoch；R20 是多级日志 + `/metrics` |
| 学习闭环严谨度 | **Aletheia** | 反事实账本 / OPE / propensity；R20 是 6 小时读台账提炼心法（朴素得多） |
| 交付速度 | **R20** | 25 天 v8.3.0 可部署 |
| 纪律的可执行性 | **R20** | 48 个 audit 门 + 76 个 AST 对拍门 |
| 用户可配置性 | **R20** | 8 大前台配置中心，交易员不碰代码就能改策略 |

**结论不是"抄 R20"，而是"把我们已有的严谨从文档搬进测试，并补上交付验收"。** 我们的架构深度是资产，不是负债——问题是这份深度目前没有被门禁保护，只被文档描述。

---

## 4. 修复方向：四条，按 ROI 排序

### D1 · 把治理文档编译成门禁测试（最高 ROI）

不是删文档，是**给每条已写下的规则配一个会翻红的测试**。优先级按"违反后的代价"排：

| 优先 | 规则（已写在 AGENTS.md/contracts 里） | 应建的门禁 | 参考 R20 实现 |
|---|---|---|---|
| P0 | "一个能力只留一份实现" | 孪生对拍门：两处实现的决策序列逐项相等 | `test_circuit_breaker_twin_parity.py` |
| P0 | 契约文档与代码一致 | 契约锚点门：契约里引用的每个符号/测试必须真实存在且可加载 | `test_failure_semantics_doc.py` |
| P0 | "读不到 ≠ 没有"（我们踩过：`all_realized_pnl_unavailable`、derivatives 恒 0） | 失败语义表 + 锚点门 | `FAILURE_SEMANTICS.md` + 同名门 |
| P1 | 时间戳单位一致（我们踩过跨 venue 单位混用） | `_ms`/`_ts` 命名即语义门，例外表必须为空 | `test_timestamp_unit_convention.py` |
| P1 | 文档不得腐烂 | 模块清单双向比对门 | `test_directory_docs_current.py` |
| P1 | 测试不得空洞通过 | 空洞断言门 + 反重言门 | `test_no_vacuous_assertions.py` |
| P2 | 大重构必须语义等价 | AST 对拍门（每次抽取/搬家一个） | `tests/extraction/` |

**每建一个门，就可以从 `docs/` 删掉对应的文字段落**——规则搬家，不是规则消失。目标是把 15,900 行治理文档压到 3,000 行以内，同时治理强度**上升**。

### D2 · 把交付验收加回 PR 定义

在现有 Verification 段之外，增加一个**必答项**：

```
## 可用性证据（三选一，不得为空）
- [ ] 真实运行证据：<Fedora 实际周期 / 实际订单 ID / 实际 Outcome>
- [ ] 不适用理由：<为什么这个改动不可能有运行证据>
- [ ] 阻塞项：<具体缺什么，谁能解开>
```

现在的 `Runtime qualification: Not claimed by this source repair` 是一个**合法的空答**，它让 113 个 PR 集体免责。上面这个格式不允许空答——第三项会把"缺什么"暴露成可追踪的一条线。

R20 的对应物是把"实盘 5 周期零异常"写进重构验收矩阵。

### D3 · 工作单元从「issue」改成「刀」

具体动作：

1. **#3134 停止作为总线**。它已经吸收 113 个 PR，作为跟踪单元信息量为零。要么拆成可收口的子目标，要么关闭并让 PR 直接对齐具体文件。
2. **引入编号收口单元**。一刀 = 一个明确的 `收口：<file>` + 一个门禁 + 一行 commit。不写 6 段治理表单。
3. **200 个 open issue 分三类处置**：
   - 已被代码修掉的 → 核实后关闭（我今天已确认 **#3214 就是这类**，见 §5）
   - 审计发现但无真实用户影响的 → 降级为门禁测试的 backlog，从 issue 库移出
   - 有真实影响的 → 保留，但必须绑定一个"可用性证据"

### D4 · 补上可部署性

R20 有 `Dockerfile` + `docker-compose.yml` + 3 个 systemd unit + 灾备恢复手册。我们有 Fedora 远程机 + 手工流程 + 一个"从未接通 Bitget 凭证"的历史。

这一项之所以排最后，是因为它是**结果而非原因**——但它也是最容易被无限推迟的一项，所以要有硬期限。

---

## 5. 立即可执行的三件事（今天到本周）

我在扫描中已经验证到 master 的确切状态，这三件可以马上做：

| # | 事项 | 状态（我已核实） | 工作量 |
|---|---|---|---|
| 1 | **关闭 #3214** | 已被 #3407（`e63533c00`）修掉。`reconciliation.py:1324` 现在直接用 dict 行，`_response_rows`（653 行）只服务 fills。需按 DoD 逐条核后关闭 | 30 分钟 |
| 2 | **修 #3432**（High） | 缺陷在 master 存活：`agent_runtime/scheduler.py:826` 的 `return future.result()` 无 timeout，`_run_universe_refresh()`（3727 行）正是 `drain_on_timeout=True` 的调用方。一个卡住的 universe 刷新会永久占死唯一的 `maintenance_io` worker，连带饿死 `outcome_settler` 和 OHLCV backfill | 半天 |
| 3 | **修 #3433**（Medium） | 缺陷在 master 存活：`maintenance/database_capacity.py:351` 的 `attempted += chunk` 按请求页数而非实际回收页数推进。碎片库上 16MiB 预算可能只回收 ~256KiB | 2 小时 |

**#3432 必须先做**：它饿死的 `outcome_settler` 正是 #2899（Bitget Demo cost-complete Outcome）依赖的结算环节。不修它，端到端证据链没有产出的物理条件。

**#3432 的坑**（我读过 issue 和代码）：#3429 的回归测试**显式断言第二次调用 `timeout is None`**。修复时要重新表达那条测试的意图（它想钉的是"不放弃所有权"，不是"永不超时"），不能简单删掉，也不能把 #3429 修的 detached-worker 所有权问题退回去。

---

## 6. ETA 路线图

以下时间基于：单人 + AI 协作、每天有效工作 6 小时、不引入新人。**每个节点都有可验证的验收物**，不接受"契约一致"作为验收。

### Phase 0 · 止血（2026-09-24 → 09-26，3 天）

| 交付 | 验收 |
|---|---|
| 修 #3432 + #3433 | 两个回归测试；`maintenance_io` worker 在 universe 超时后仍可被 `outcome_settler` 取用（用一个超时注入测试证明） |
| 关闭 #3214 及其它已被代码修掉的 issue | 逐条 grep + 读代码核实（不用"有 merged PR 引用"这个信号，它已被证伪） |
| 删除 `HANDOFF_3134_CODEX.tmp.md` | 已确认失效（它描述的 562 staged 工作区不存在，迁移已落 master） |

**09-26 达到**：master 无已知活跃回归；issue 库数字真实。

### Phase 1 · 第一条端到端真实证据（09-27 → 10-03，1 周）

这是**最关键的一周**，目标是拿到我们 113 天来一直没有的东西：一笔完整的真实交易闭环。

| 交付 | 验收 |
|---|---|
| Fedora 接通 Bitget Demo 真实凭证 | `aletheia control status --json` 显示凭证 READY（**需要你提供凭证，这是 owner 决策项，我无法自行解开**） |
| 打通 Gate B 剩余阻塞（风控止损距离门） | 记忆里这条"不得调阈值"——所以要查的是候选的止损几何为何不满足，不是放宽门槛 |
| **一笔完整 Outcome** | Position 开 → 平 → cost-complete Outcome 落库，带真实 order ID 和费用 |

**10-03 达到**：#2899 可以关闭，或者明确知道还差什么（写成一条可追踪的阻塞项，不是 200 个 issue 里的第 201 个）。

**风险**：这一周的成败**主要不取决于我**。凭证是 owner 决策，Gate B 的止损几何可能暴露出更深的候选质量问题。如果 10-03 没拿到，我会给出"还差什么 + 谁能解"，而不是继续修无关的 bug。

### Phase 2 · 治理编译（10-04 → 10-17，2 周）

| 周 | 交付 | 验收 |
|---|---|---|
| 10-04→10-10 | P0 三个门禁：孪生对拍门、契约锚点门、失败语义表+门 | 三个门都能通过"故意违反 → 翻红"的负向验证 |
| 10-11→10-17 | P1 三个门禁：时间戳单位门、文档腐烂门、空洞断言门 | 同上；同时从 `docs/` 删掉被门禁替代的文字段落 |

**10-17 达到**：6 个核心门禁上线；治理文档从 15,900 行压到 8,000 行以内，**治理强度上升**（可用"故意违反"测试证明）。

### Phase 3 · 交付节奏（10-18 → 10-31，2 周）

| 交付 | 验收 |
|---|---|
| PR 模板加"可用性证据"必答项 | 新 PR 无法以空答通过 |
| #3134 拆解或关闭 | 不再有单一 issue 吸收 >10 个 PR |
| issue 库从 200 压到 50 以内 | 每条保留的 issue 都绑定可用性证据或真实用户影响 |
| Docker 化部署 | `docker compose up -d` 在干净机器上拉起，文档化 |

**10-31 达到**：可部署、issue 库真实、每个 PR 承担可用性举证责任。

### 里程碑汇总

| 日期 | 节点 | 可验证效果 |
|---|---|---|
| **09-26** | 止血完成 | master 无活跃回归，issue 数字真实 |
| **10-03** | 首个端到端证据 | 一笔真实 cost-complete Outcome，或明确的阻塞清单 |
| **10-17** | 治理编译完成 | 6 个门禁上线，文档减半，治理强度上升 |
| **10-31** | 交付节奏建立 | 可 Docker 部署，issue ≤50，PR 必须举证可用性 |

**关于 ETA 的诚实说明**：Phase 0 和 Phase 2 的时间我有信心（纯工程工作，依赖可控）。**Phase 1 的 10-03 是最不确定的一个**，因为它依赖你提供 Bitget Demo 凭证，以及 Gate B 止损几何背后可能存在的候选质量问题——后者的深度我现在无法预估。Phase 3 依赖 Phase 1 成立。

---

## 7. 对"为什么便宜模型反而更快"的直接回答

这个现象不是模型能力的反转，是**约束结构的差异**：

1. **弱模型 + 强门禁 > 强模型 + 强文档。** 门禁给模型的是**即时、确定、机器可判**的反馈（红或绿）。文档给模型的是**需要理解、需要权衡、可能误读**的约束。Gemini/DeepSeek 在"跑测试看红绿"的循环里表现和 Opus 5 差距很小；但在"读 15,900 行治理文档并正确推断本次改动的 SDD Depth"这件事上差距很大——**问题是后者这件事本身没有产生任何用户价值**。

2. **我们在用最强的模型做最不需要智力的事**：填治理表单、在 583 个文档间核对一致性、判断 issue 是否已被修掉（我今天就花了大量 token 做这件事，而它本该是一个门禁测试的职责）。

3. **R20 的"刀"给了模型一个可完成的单元**：一刀 = 一个文件的收口 + 一个对拍门。这个粒度下模型很难走偏。我们的"issue + SDD 分类 + umbrella 关系"给模型的是一个**需要全局推理才能定位的单元**，于是 #3134 变成了总线——模型找不到更小的正确挂载点。

**所以换模型不会改善这件事，改约束结构才会。**

---

## 8. 本报告的证据边界（哪些我验证了，哪些没有）

**我实测验证的**：
- 所有代码行数、commit 数、文件数、测试数（本机 `wc`/`git`/`find` 实测）
- R20 零 AI 治理文件、零契约层、零 `.github/`（逐个 `find` 核实）
- #3214 已被 #3407 修掉（读 `e63533c00` 的 diff + 当前 `reconciliation.py` 代码）
- #3432、#3433 缺陷在 master 存活（读 `scheduler.py:826`、`database_capacity.py:351`）
- #3134 被 113 个 merged PR 引用（解析 120 个 PR body 交叉比对）
- Aletheia 测试收集 11,516 个用例耗时 3.26s；`tier_unit` 8,495 个用例 300s 超时未跑完（卡在 93%，且含 1 个 F）

**我没有验证的（不要当结论用）**：
- **R20 的实际盈利能力和真实可用性**。它 README 里的"机构级""1205+ Passing"是营销文案，`docs/STRUCTURE_OVERVIEW.md` 里的"实盘周期零异常"是**它的自述**，我无法独立核实。我在本机跑它的测试得到 7488 tests / 74.6s，但有 40 failures + 742 errors —— 我核实过这是**我的环境缺依赖**（连 `fastapi` 都没装、`frontend/node_modules` 缺失），**不代表 R20 的真实测试状态**。
- **"R20 可用性比我们好很多"这个前提**。这是你告诉我的观察，我采信了它作为分析起点，但我没有独立验证。如果这个前提有偏差（比如它的"可用"只是界面完整度，而非真实盈利），那么 §2 的 G4 需要重新校准——不过 G1/G2/G3 三条根因**不依赖这个前提**，它们是从两边仓库的结构差异直接得出的。
- Phase 1 的技术深度（Gate B 止损几何背后是否有更深的候选质量问题）。

---

## 附录 · 可直接抄的 R20 机制清单（带文件路径）

按抄袭优先级排序。路径均为 `/Users/yixian/Projects/references/r20-quantum-trader` 下的相对路径。

| 优先 | 机制 | 文件 | 为什么值得抄 |
|---|---|---|---|
| 1 | 失败语义表 + 锚点校验门 | `docs/FAILURE_SEMANTICS.md` + `tests/audit/test_failure_semantics_doc.py` | 直接治我们"读不到被渲染成没有"的历史顽疾（derivatives 恒 0、partial-close unverified 都是这个形状） |
| 2 | 孪生实现对拍门 | `tests/audit/test_circuit_breaker_twin_parity.py` | 把"一个能力只留一份实现"从文字变成门禁 |
| 3 | AST 抽取对拍门 | `tests/extraction/` 76 个文件，样本 `test_trader_order_lifecycle_extraction.py` | 让大重构变成可证明的操作；我们的 #2945 回退事故用它可以避免 |
| 4 | 文档腐烂双向门 | `tests/audit/test_directory_docs_current.py` | 583 个文档文件迫切需要这个 |
| 5 | 时间戳单位约定门 | `tests/audit/` 的 `test_timestamp_unit_convention.py` | 我们踩过跨 venue 单位混用 |
| 6 | 空洞断言门 | `tests/audit/test_no_vacuous_assertions.py`、`test_tests_cannot_pass_vacuously.py` | 11,516 个测试里有多少是空洞通过的？现在没人知道 |
| 7 | 「刀」式工作单元 | 见 commit history，`收口：<file>` 格式 | 给 AI 一个可完成、难走偏的粒度 |
| 8 | 模块导航表（带行数） | `scripts/README.md`、`r20_backend/README.md` | 33 个根层模块一张表说清，配门禁强制登记 |
| 9 | Docker + compose + systemd | `Dockerfile`、`docker-compose.yml`、`deploy/` | 可部署性 |
| 10 | 前台策略配置中心 | `frontend/src/views/admin/` | 交易员不碰代码改策略（我们的 Settings 已有基础） |

---

*报告生成：2026-09-24。所有数字可用本文档记录的命令口径复现。*
