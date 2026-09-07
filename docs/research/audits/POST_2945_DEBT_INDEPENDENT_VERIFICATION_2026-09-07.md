# #2945 后续债务：独立复核发现（2026-09-07）

本文件只记录**编排者自己跑出来、可复现**的度量与结论。每条给出复现命令。
14 个并行 verify agent 的逐项结论另附。

## 0. 结论提要

上一轮的收尾判断把重点放在「42 个 spec 失败，离 29 基线差 13」。这个框架是错的：
**分母变了。** #2945 在 `tests/` 里删掉了 875 个测试函数，spec 目录里删掉 325 个。
拿 42/1407 去比 29/1728 没有意义。真正的债不是 42 个红，是消失的覆盖。

第二条结论（见 3c）：#2945 删掉的那些能力**在合同层是有意退役的**，删除本身做对了。
债在于**读侧没跟着退役**——健康探针、仪表盘计数、契约登记还在描述已不存在的写入。
判定任何一条「#2945 删错了」之前必须读 `docs/design/contracts/`，
只读被删的调用点会得出相反且错误的结论（我自己踩了一次并回退）。

## 1. #2945 的真实体量

| 度量 | 值 | 复现命令 |
| --- | --- | --- |
| 总 diff | 405 files, +6947, **−83227** | `git show --shortstat 2e69baae9` |
| `tests/` diff | 221 files, +3984, **−46862** | `git show --stat 2e69baae9 -- tests \| tail -1` |
| 删除的生产模块 | **30**（HEAD 仍全部缺失） | `git diff --diff-filter=D --name-only 2e69baae9^ 2e69baae9 -- aletheia` |
| 删除的测试函数（全树） | 7725 → 6850 = **−875** | 见下方脚本 |
| 删除的 spec 测试函数 | 1453 → 1134 = **−319** | 同上，限定 `tests/specs` |
| 当前 spec 收集数 | 1407 | `uv run pytest tests/specs --collect-only -q \| tail -1` |
| 当前 spec 失败数 | 42 | `uv run pytest tests/specs -q -p no:randomly --tb=no` |

被删的 30 个生产模块里，整包消失的有：`aletheia/portfolio/uta/`（7 个文件）、
`aletheia/runtime_continuity/`（5 个文件）。单模块里体量最大的是
`agent_shell/trading_decision.py`、`agents/paper_trader_ranker.py`、
`agents/paper_trader.py`、`universe/signal_emitter.py`、三个交易所 broker
（`execution/{okx,bitget,binance}_broker.py`）、`server/routes/runtime.py`。

## 2. 导入层是干净的（一条否证）

我一开始怀疑残留引用会造成运行时 ImportError。**证伪了。**

```
uv run python -c "import pkgutil,importlib,aletheia; ..."   # 见 /tmp/import_sweep.py
modules scanned: 599  failures: 1
  AttributeError  aletheia.gateway._win32_proc  (ctypes has no attribute 'windll')
```

599 个模块只有 1 个失败，且是 macOS 上必然失败的 Windows-only 模块，不是债。
`runtime_continuity`、`learning.authority`、`fixed_look`、`portfolio.uta` 等名字
在代码里仍能 grep 到，但全部是字符串、注释，或存活模块里的同名帮助函数
（如 `retire_runtime_continuity_tables`、`verify_signal_emitter_admission`），
**没有一条是指向已删模块的 import**。#2945 的删除在导入层做对了。

## 3. `signal_pending`：写路径已删，读路径全留（本轮最硬的一条）

`aletheia/agents/paper_trader.py` + `paper_trader_ranker.py` +
`universe/signal_emitter.py`（合计 3785 行）被删，后继者是
`agent_shell/entry_planner.py`（692 行，由 scheduler.py:2382 `run_entry_planner_batch()` 驱动）。

后继者**不写 `signal_pending`**。全仓 51 处 `signal_pending` 引用里，**零个写入方**：

```
grep -rn "signal_pending" aletheia --include='*.py' | wc -l   # 51
```

全部是读取方：`storage/repositories.py`（4 处 SQL）、`storage/migrations.py`、
`diagnostics/doctor.py`、`analytics/{skip_diagnosis,opportunity_flow,position_replay,
candidate_facts,trade_attribution,filter_registry}.py`。

具体后果，`diagnostics/doctor.py:2882` `_probe_content_attribution`：

```python
rows = list(db.execute(
    "SELECT opportunity_id FROM pi_decisions WHERE action='signal_pending' ..."))
total = len(rows)
if total < 20:
    report.add("content.attribution", "info",
               f"only {total} recent signal_pending decisions — too few to judge attribution")
    return
```

这个健康探针现在**永久返回 `info: only 0 ...`**，既不会绿也不会红。
`doctor.py:2608` 的 `signal_pending_queue_depth` 同理恒为空。
`analytics/filter_registry.py:351` 还把
`"signal_pending insert and opportunity queued transition are atomic"`
登记为一条 current_value——描述一个不存在的写入。

这是「双主线」的教科书形态：**删了写方，留了读方，读方还在报健康。**

## 3b. 完整的死链：候选准入队列

第 3 节的 `signal_pending` 不是孤立现象，它是一条**完整死链**的中段。逐段证据：

| 环节 | 位置 | 状态 |
| --- | --- | --- |
| 排名/择优 | `agents/paper_trader_ranker.py` 的 `build_paper_candidates` / `build_paper_candidate_workset` / `select_best_candidate` | 3 个公开函数**全部按名消失** |
| 入队驱动 | `agents/paper_trader.py` 的 `run_paper_trader` | 消失；scheduler 无 `paper_trader` job |
| 入队状态转换 | `repositories.py:1261` `transition_open_opportunity_to_queued`（`UPDATE opportunities SET status='queued' WHERE id=? AND status='open'`） | **0 个生产调用方**，只有 `spec_pipeline_middle_state_integrity.py:924` 在调 |
| 队列载体 | `pi_decisions.action='signal_pending'` | **0 个写入方**，51 处读取方 |
| 优先替换/驱逐 | `atomic_replace_queued_carrier`、`signal_queue_evicted` | 全部消失 |
| 终态收敛 | `repositories.py:1272` `transition_opportunity_to_reviewed`（`WHERE id=? AND status IN ('open','queued')`） | `'queued'` 分支**不可达** |
| 索引 | `migrations.py:2201-2210` 两个 partial index（`idx_opportunities_trade_unranked_rotation` / `..._retry_rotation`，为 ranker 的轮转游标而建） | 服务对象已删 |
| 报表 | `server/routes/analytics.py:1597` `SUM(CASE WHEN status='queued' ...) AS queued` → 输出 `"queued": queued` | **恒为 0**，仪表盘照常显示 |
| 健康探针 | `diagnostics/doctor.py:2882` `_probe_content_attribution`；`doctor.py:2608` `signal_pending_queue_depth` | 永久 `info: only 0 ...`，不可能变红或绿 |
| 契约登记 | `analytics/filter_registry.py:351` current_value = `"signal_pending insert and opportunity queued transition are atomic"` | 描述一个已不存在的写入 |

后继者是 `agent_shell/entry_planner.py`（692 行，`scheduler.py:2382`
`run_entry_planner_batch()` 驱动）。架构从「排名 → 入队 → 择优出队 → 执行」
改成「直接规划下一个候选」。这**可能是有意的 D2 级设计变更**——但如果是，
上表后 4 行（索引/报表/探针/契约登记）就该同批删除；如果不是，队列需要恢复。
**这一条必须由所有者裁决，不能由清理者默认。**

注意一处我自己的更正：`models.py:109` 的 `"queued"` 属于 `AgentRunStatus`，
与 opportunity 无关，是合法在用的词表。`opportunities.status` 是无 CHECK 约束的
自由文本 `str`（`models.py:257` `status: str = "open"`），所以数据库层不会
挡住任何值，也不会告诉你哪个值已经没人写。


## 3c. 重要更正：退役是有意的，债是残留的读侧

写完 3 和 3b 之后我读了合同层，**必须更正上面两节的定性。**

`docs/design/contracts/agent_contracts.md:204` 有一节
「Historical Ranker / PaperTrader (retired entry)」，原文：

> The former deterministic ranker scored Opportunities, bounded a five-carrier
> pending queue and applied family TTL penalties. PaperTrader then wrote
> `signal_pending` PiDecisions and woke the Operator. These are historical semantics
> only: neither job, queue admission, TTL writer nor **on-demand notification**
> remains an executable entry path.

另有三处一致表述：`:174`（equity_observer「does not wake Ranker」）、
`:191`（「research Opportunities do not wake a retired entry queue」）、
`:468`（「old pending cannot ... wake a retired evaluator」）。

所以：**队列、TTL writer、5 条按需唤醒边的删除全部是有意的、已在合同层记录的。**
#2945 在合同层做对了。第 3、3b 节列的「死链」不是"删错了"，而是
**删对了、但读侧没跟着删**。

我自己踩了这个坑并回退：我先按「唤醒边被删=回归」修了 5 处生产代码
（`guard_promotion` / `equity_observer` / `prediction_observer` /
`divergence_scanner` / `config.loader`），读到合同后**全部回退**。三个 observer
提交的都是 `execution_eligible=False` 的 Opportunity，唤醒入口规划器永远不可能
产生入场——修它等于制造一条违背合同的边。

这正是计划文档那句话的第二次应验：**「a reported defect is a hypothesis until the
calling code is read」——还要加一句，直到合同层被读。** 只读被删的调用点，
5 条边看起来全是回归；读了合同才知道一条都不是。

### 真正剩下的债（读侧残留）

| 位置 | 残留物 | 处置 |
| --- | --- | --- |
| `diagnostics/doctor.py:2882` `_probe_content_attribution` | 探针查 `signal_pending`，永久 `info: only 0 ...` | 删探针 |
| `diagnostics/doctor.py:2608` | `signal_pending_queue_depth` 恒空 | 删 |
| `server/routes/analytics.py:1597,1700` | `SUM(CASE WHEN status='queued'...)` → `"queued"` 恒为 0 | 删该列与字段 |
| `analytics/filter_registry.py:351` | current_value 登记 `"signal_pending insert and opportunity queued transition are atomic"` | 删该条目 |
| `storage/repositories.py:1261` `transition_open_opportunity_to_queued` | 0 生产调用方，仅 `spec_pipeline_middle_state_integrity.py:924` 调 | 删函数 + 删该 spec 断言 |
| `storage/repositories.py:1272` `transition_opportunity_to_reviewed` | `WHERE ... status IN ('open','queued')` 的 `'queued'` 分支不可达 | 收窄为 `status='open'` |
| `storage/migrations.py:2201-2210` | 两个为已删 ranker 轮转游标建的 partial index | 需判断 entry_planner 是否复用；不复用则 drop |
| 其余 45 处 `signal_pending` 读取 | 历史证据读取，合同说「remain read-only evidence」 | **保留**，这是有意的 |

注意最后一行：合同明确写了「Old ranker/funnel outcomes remain read-only evidence」。
所以**不能**一把删掉 51 处引用——读历史证据是合同要求的。要删的只有
**假装当前健康/当前报表**的那 6 处。这个区分是整条清理的关键，弄错就会删掉审计能力。

### 一处已修

`spec_agent_contracts.py` AC-S13 断言的是被删架构（3 分钟 `paper_trader` job + 7 条
唤醒边）。已重写为断言合同的真实承诺，并把退役那半**反向断言为必须缺失**——
让这个 spec 从「红」变成「防止有人把那 5 条边加回来」的守卫。
`spec_agent_contracts.py: 16 passed`（commit `60a354b78`）。


## 4. 被删测试里有多少是覆盖空洞

判据：被删的 spec 测试函数引用的 aletheia 符号，在 HEAD 是否仍存在。

```
deleted spec test functions: 325
  168  MIXED（部分符号仍在）
   84  no_aletheia_import（解析未覆盖，未定）
   72  ALL_SYMBOLS_GONE（合理删除）
    1  ALL_SYMBOLS_STILL_LIVE
```

**169 个上界**（MIXED + ALL_LIVE）是覆盖空洞候选。这是上界不是精确值——MIXED
意味着部分符号仍在，而被测主体消失时删测试是对的。集中在：

| 文件 | 删除数 | 变化 |
| --- | --- | --- |
| `spec_decision_lifecycle.py` | 44 | 85 → 2 |
| `spec_pipeline_paper_trade.py` | 26 | 32 → 6 |
| `spec_learning_authority.py` | 12 | 13 → 1 |
| `spec_learning_ledger.py` | 10 | 13 → 4 |
| `spec_learning_final_consumers.py` | 8 | 13 → 2 |
| `spec_learning_observability.py` | 7 | 13 → 3 |

抽样核实 `spec_pipeline_paper_trade.py` 被删的 26 个：其中
`test_priority_replacement_commits_both_atomically` 等测的是
`atomic_replace_queued_carrier`，该函数在 HEAD **已不存在**，属合理删除。
但同批被删的原子性/回滚契约（`test_signal_enqueue_rolls_back_when_queued_transition_fails`、
`test_permanent_skip_state_rolls_back_without_rejection_fact`）覆盖的正是
候选准入队列——即 2026-07-20 admission gap 审计（GAP-1 defer-blind 5 槽 /
GAP-2 valid_until 不延长）烧过的那块地。那次审计的教训是
「6 次修复都由观察面驱动，从未回到合同层定义 admission slot 语义」。
现在合同层的测试也没了。

## 5. 一个作废的工具（如实记录）

我写过一个「读路径有、写路径无」的泛化扫描（`/tmp/vocab_sweep.py`），
从 migrations 的 `CHECK(col IN (...))` 和 models 的 `Literal[...]` 抽词表，
再判每个值有无写入方。**精度太低，作废。** WRITE 正则把 SQL 的
`WHERE action='X'` 也算成写入，57 条「只读不写」结果几乎全是通用词假阳性
（`treatment`/`current`/`opportunity`），而它本该抓的 `signal_pending`
恰好被 `WHERE pending.action='signal_pending'` 误判成「有写入方」。
第 3 节的结论不依赖这个工具，是直接读代码得到的。

## 6. Phase E 未落地（复核确认）

`AGENTS.md` 与 `docs/operations/development/agent-guides/` 下 **零处**提到
`Policy A` 或 `additive`：

```
grep -rn "Policy A" AGENTS.md docs/operations/development/agent-guides/   # 无输出
grep -rni "additive" AGENTS.md docs/operations/development/agent-guides/  # 无输出
```

SOP-11/12/13 落在 `docs/governance/protocols/DEBUG_AUDIT_PLAYBOOK.md:154/179/203`，
但「additive-only 只约束数据层、不应外溢到代码层」这条防复发规则没有写进任何
agent 会读到的路由文件。


---

# 第二部分：14 路并行复核的结论

一个 verify agent 一项，每项要求「可证伪的证据 + 一条能推翻自己结论的命令」。
14/15 完成（TPSL 那个撞 429 未跑），另有一个 completeness critic 做对抗审阅。

## 原清单 10 项的裁定

| 项 | 裁定 | 生产影响 | 要点 |
| --- | --- | --- | --- |
| D1 position `**kwargs` | PARTIAL | LATENT | 数字错（是 6 个 writer / 38 调用点 / 18 处静态不透明，不是 13）。**「加 trigger」这个方案不可行**——SQLite 无从知道是哪个 Python 组件发的 UPDATE，无法表达 per-owner 归属。#2955 的运行时白名单已按名字挡住跨 owner 写入。真问题是 **3 处裸 `UPDATE positions`** 绕过全部 owner，而守卫测试只禁字符串 `update_position(` |
| D3 trade proposal transition | PARTIAL | 应为 STRUCTURAL | 活体探针证明 `update_trade_proposal(pid, status="totally_not_a_status")` 会落库、`executed→pending` 也会落库。但 agent 自己写「找不到可达的非法写入」——按 D6 的标准这是缺守卫，不是 bug。是 addition + migration，排最后 |
| D4 TPSL uta identity | PARTIAL | **ACTIVE** | 前提真、两个结论都错、漏了真 bug。真影响不在 `pending_close_recovery`（那是**死代码**，0 生产调用方），而在每 2 分钟跑的 `reconcile()` 把该单判 red。**已修** |
| D5 live 模式重复 | PARTIAL | LATENT | 读侧 6 份定义**当前一致**。但 Critic 发现**写侧**是真洞——已修 |
| D6 flat/nested | PARTIAL | **ACTIVE** | 原claim 指的函数是对的（`cfg.risk` 被 rebind 成 view）；真分歧在 `check_account_leverage` + `doctor.py:762` + 33 个前端字段。**已修** |
| D7 deprecated CLI | PARTIAL | ACTIVE | 30 行只有 1 个函数、5 个调用点；docstring 列的 `strategy`/`update` 已不存在。`model set --provider databricks` 退出码 1 |
| D8 benchmark 授权 | **REFUTED** | NONE | 提出的补救**本身就是回归**。不做 |
| D9 entry_compat | CONFIRMED | STRUCTURAL | 293 行里只有 63 行真死（`read_legacy_entry_record` 等），不是整体可退役 |
| D10 shadow dedup | PARTIAL | LATENT | 「零可执行证据」是假的；隐含前提「三者应该等价」方向错了——它们回答不同问题。真缺口是一行 tie-break |
| DEAD 四项 | PARTIAL | LATENT | 三真一误标。**DEAD-4 的「wire 上去」是全批最危险的建议**：`VENUE_TRADE_RATE_LIMITED_STATUS` 不在 `CLOSE_CLAIM_STATES` 里，`has_unresolved_close_claim` 对已 park 的 claim 返回 False，排上调度会导致**二次平仓**→ 正是 park 要防的 Bitget 40725 循环。不做 |

## 42 个 spec 失败的归因（三簇全覆盖，13+20+9=42）

fix_owner 分布：**TEST_CODE 38 / BOTH 4**。真生产 bug 只有 2 个。31/42 由 #2945 引入。

三个共享根因把 N 个失败变成 1 个修复：
1. **7 个** = #2945 新增的 UTA 账户身份前置闸门，3 个 spec 文件的 fixture 没跟上（#2945 只更新了 4 个文件里的 1 个）
2. **4 个** = `_Resp` fake 缺 `.usage`，一行
3. **5 个** = #2945 退役 legacy Learning Experiment runtime，无后继写入方

**一个隐藏更深的问题**：`test_filled_gap_rejects_missing_authority_or_compatible_open_position`
的两个 parametrization 此前有 4 条断言是**空过**的——计数器是闸门产生的，不是它要证明的
授权检查产生的。所以 claim PL-HCR-02 之前是「未被证明」，不只是「红」。修 fixture 恢复的
是真实证明能力，不只是颜色。

## Critic 找到的、原清单完全没有的项

| 项 | 严重度 | 状态 |
| --- | --- | --- |
| `settings.py:752` 接受 `okx_live`/`binance_live` → 一次 POST 全停交易 | 最高（可达） | **已修** |
| `paper_broker.py:215` 缺 `paper_fill_model_version` → 所有 `paper_internal` market 持仓永久 outcome-ineligible，本地纸交易对归因贡献 0 行 | 高 | **见下方保留意见** |
| `_ORDER_TRANSITIONS["filled"] = frozenset()` 挡住 `filled → overfilled`，而 `exchange_materialization.py:1024` 能传 `overfilled` | 待验可达性 | 未做 |
| `learning/demo_entry_quality.py` 的 policy-epoch 指纹硬编码它声称描述的阈值（第 8 份 demo 模式集），改 config 改了执行但**不轮转 epoch** → 归因把变更后的结果算给变更前的 epoch | 中（污染证据层） | 未做 |
| `_ensure_runtime_baseline` 在 migrations.py 里定义**两次**，`SCHEMA_SQL` 在 import 后被重绑 | 结构（无活体缺陷） | 已在 DM-S06 里记录 |
| 同名双模块：`analytics/outcome_integrity.py`(820) vs `maintenance/outcome_integrity.py`(282)；`universe/market_structure.py`(152) vs `data/market_structure.py`(894)；`gateway/reconciliation.py`(260) vs `execution/reconciliation.py`(1744) | 未审 | 未做 |
| 资金/权益三处并行实现（`broker_funding_settlement` / `paper_funding_reconciliation` / `local_funding`；`_cached_available_balance_usdt` / `_account_equity` / `account_equity_for_execution`） | 未审 | 未做 |

### 对 `paper_broker.py:215` 的保留意见（Critic 不可能知道）

Critic 把它排为「全批最高后果」。但项目已有决定：**`paper_internal` 未来整体删除**，
只留真实交易所 Live/Demo。给一个即将删除的模式补 fill-model 版本号是白做工。
而且 agent 自己指出反向读法同样成立：常数滑点的成交**确实不是**已验证的 fill model，
所以这个排除可能就是有意的语义诚实，硬盖 `paper-order-fill/v1` 会伪造证据等级。
**结论：先等 `paper_internal` 退役决定，不要动。**

### Critic 的一处算错

Critic 说「~13 个 spec 失败从未 triage」，依据是计划文档里的 55。**55 是 #2945 刚落
时的数**，本次开始时实际是 42，三簇 13+20+9 正好全覆盖，无遗漏。

## 本次已交付

| commit | 内容 | spec |
| --- | --- | --- |
| `60a354b78` | AC-S13 反向断言退役的 Ranker 唤醒源 | 42→41 |
| `2ddb93198` | settings 路由不再能持久化导致全停的执行模式 | 41 |
| `681e7debe` | TPSL 平仓 claim 带上被证明的账户身份 + fixture 配对（**含 Fedora 前置闸门**） | 41→36 |
| `fc6c71ece` | nested 风控策略镜像回 flat 别名 | 36 |
| `569af8f4c` | 六个 spec 重新指向 #2945 之后的现实 | 36→29 |

spec **42 → 29**，16 个修复，**0 新增失败**（逐条 diff 过）。
全量 **141 →（计划文档记录的 master 数）→ 119 failed / 9635 passed** 于三 commit 处。

### 刻意没做的（每一条都会「变绿但掩盖真问题」）

- `spec_live_unlock` 2 个：改一行就绿，但**有效的 live unlock 现在真的无法把新
  Proposal 路由到 live**——entry-plan compiler 裸读 `paper_execution_mode` 且只接受
  `{paper_internal, bitget_demo}`，gateway `submit_proposal` 已无生产调用方
- `DCLS-S01`：#2945 把模拟器接到真 paper broker 却没记 contract delta；且这个选择与
  `paper_broker.py:215` 互相牵制
- `spec_serenity_promote` 剩 3 个：`serenity_source_evidence_unavailable` 是真实证据要求
- learning 簇 5 个：LE-C06 的 raw-vs-unique 诚实性是退役还是重做，是产品决策

## 剩余排序（按 生产风险 × 证据强度 ÷ 修复体量）

1. D1 —— 改掉 2 处活体裸 SQL、删 `_update_position_economics`、把守卫测试从「禁字符串
   `update_position(`」扩到「禁 `storage/` 之外的 `UPDATE positions`」。守卫是持久的那半
2. `_ORDER_TRANSITIONS["filled"] |= {"overfilled"}` —— 一行，先验可达性；可达则升到第 1
3. D5 —— 六份读侧词表派生自 `config/exchanges.py`（−25/+6）。写侧已在 `2ddb93198` 封堵，
   所以这条现在只是整理
4. D9 —— 删 63 行确证死代码 + 它的一个测试。零风险
5. DEAD-1 —— 删 `update_opportunity`，把唯一的 spec 调用方迁到有守卫的兄弟函数
6. DEAD-3 —— 删 `agents/committee/` + `analytics/committee_overview.py`（1981 行），
   必须与 `inference_contract.py:91` 的 `_spec` 行原子提交
7. `learning/demo_entry_quality.py` —— 证据层的 D6 同病
8. D7 —— 先修 docstring（纯注释），`model set` 的删除必须与 `doctor.py:851/856/1051`
   + `RUNBOOK.md:96` 同一 commit，`DECISIONS.md:501` 已经预言过这个失败模式
9. DEAD-2 —— 只把 `guard_chain.py:173` 的 `5.0` 改成 `1.0`。**不要**本轮做 9 文件删除
10. D10 —— `trades.py:539` 加 `, id DESC`
11. D3 —— proposal Literal + CHECK + `transition_trade_proposal`。是 addition + migration，
    前两次同类迁移各需一个后续 commit 补未枚举的历史状态。延后
12. 第 3、3b、3c 节列的读侧残留清理（6 处假装当前健康/报表的位置）
13. Phase E —— 把「additive-only 只约束数据层」写进 `AGENTS.md`

## 全部未验证的部分（如实声明）

**没有任何一项查过 Fedora 运行时。** 因此以下都是从源码推断：D10 的并列频率、
DEAD-4 的卡住 claim 数、D9 残留的 pre-v70 `plan_json` 行、D8 的「无活跃 legacy
benchmark 行」、D1 的 TOCTOU 窗口。其中 4 项是其严重度标签的承重结构。
`681e7debe` 里那条 SQL 闸门必须在合并前在 Fedora 上跑。
