# Discussion Execution Matrix 2026-07-03

来源: `discussion.md` owner 口述 + `docs/_ai/DISCUSSION_ACTION_PLAN.md` 初版拆解。

目标: 把 owner 指出的 10 类问题落到可验证的分支、测试和量化指标上。当前策略是
MVP trust hardening, 只补断点, 不扩张交易权限、不新建交易通道、不绕过 Execution
Gateway / Risk Engine。

## 1. 依赖关系

1. Learning 和 Universe 是同一条质量闭环:
   Universe 负责发现候选, System Operator 负责交易决策, Learning 负责把交易结果
   反馈成可检索教训。任何一段丢字段, 后续都会变成低质量或错误的自学习。
2. Universe skew 的直接表现是 momentum 过多, 但内因分四层:
   family quota/explore 回流、member regime 丢失、spot/perp 去重方向错误、衍生品
   数据没有完整进入 opportunity 和 scheduler 持久化。
3. UI/UX 问题独立于交易安全边界:
   顶栏价格、页面切换 loading、Trades 布局和 tab 合并可以单独合并, 不改变风险或执行语义。
4. 限价单和四字段入场计划触碰执行与风险边界:
   这类改动必须有 SDD 和风险回归测试, 且保持所有订单仍经 backend Execution Gateway。
5. TypeScript 迁移和完整侧边栏 IA 是产品/架构决策:
   当前不应为了修 bug 重写后端或重排整站信息架构。

## 2. 分支矩阵

| 问题 | 分支 / commit | 状态 | 量化结果 | 验证 |
|---|---|---|---|---|
| 顶栏价格横线 + 页面加载反馈 | `master` / `1c889a6` | 已落地 | 顶栏从最近 20 条全市场窗口改为 BTC/ETH/SOL 各自 `limit=1`; 新增全局 2px fetch progress bar | commit 记录已核实关键实时查询保留 10s 轮询 |
| Trades 布局 + Analytics/Insights 合并 + Serenity 移位 | `master` / `fd2f9b7` | 已落地 | Trades subtab 从 3 个减到 2 个; Blotter 改左右并排, 窄屏回单列; Serenity 从 Evolve 移到 Observe | commit 记录保留 `/trades?tab=analytics` 深链 |
| 限价单入场 + 四字段 entry plan | `master` / `a8b2912` | 已落地 | TradeProposal 增加 `take_profit` / `risk_reward`; native limit 入场缺 entry/stop/tp/R:R 任一项时拒单; SDD claim 538 -> 540, implemented 530 | 定向执行/风控/决策/pipeline 测试 257 passed |
| Universe momentum 过度回流 | `master` / `98734cb` | 已落地 | 合成池中 momentum 入选占比 84% -> 39%; mean_reversion 30%, volatility 22%, short_bias 9% | UP-17 GREEN, allocator/regime 相关 39 tests passed |
| Learning SDD 与现状同步 | `master` / `3238b30` | 已落地文档 | 过时断口 A/B/D/E/F/G、R0、lesson->policy 缺失更正为已实现; 剩余 3 项登记为 owner 决策/运维项 | 文档同步, 未改行为 |
| pytest 慢测试治理 | `master` / `659057d` | 已落地 | 实测全量 1699 tests 用时约 110s; 最慢单测 2.41s; 注册 `slow` marker 但当前不硬标慢测 | `PYTEST_OPTIMIZATION_GUIDE.md` 记录基线 |
| `direction=NULL` 被当 long 计入 edge | `fix/direction-null-edge-honesty` / `7b2b6af` | 独立分支完成 | 未知方向从 long 胜率统计中排除; learning residual 从 3 项降到 2 项 | 8 tests passed: calibration/data-health/RISK-14 相关 |
| System Operator wording 残留 | `fix/system-operator-name-residuals` / `7c529c0` | 独立分支完成 | 22 文件 wording 清理, 45 insertions / 45 deletions; 不改 DB 表名、类名、spec id 等技术标识符 | 46 passed, 1 skipped, 1 xfailed |
| Universe member `regime_tag` 丢失 | `fix/universe-member-regime-tag` / `bc88e7d` | 独立分支完成 | 生产库抽样显示最近 500 个 `universe_members.regime_tag` 为 NULL 的比例是 500/500; 新增字段传播后新 member 可保存 regime | `spec_universe_pipeline.py` 18 passed; universe allocator/integration 22 passed |
| Universe spot/perp 去重压制 perp | `fix/universe-market-type-dedupe` / `db3c3f8` | 独立分支完成 | 生产库抽样显示最近 500 个 member 全是 spot; `preferred_market_type=auto` 现在在同 symbol 中优先保留 perp | market type dedup 7 passed; universe/futures 相关 40 passed |
| scanner opportunity 缺衍生品上下文 | `fix/universe-signal-derivatives-context` / `3618b77` | 独立分支完成 | `funding_rate/open_interest/oi_delta_1h` 从 0/3 传递提升到 3/3 | signal emitter 41 passed; futures/context 2 passed |
| Coinalyze scheduler 只 collect 不 persist | `fix/coinalyze-scheduler-persist` / `51b50d6` | 独立分支完成 | DU-T01 从 missing xfail 升级为 L2b; 调度器将 OI 和 liquidation 分别写入两张表; 有 key 写入, 无 key 诚实 skip | DU-T01 1 passed; data_utilization 16 passed; fanout 3 passed |

## 3. 已证明的修复效果

| 维度 | 修复前 | 修复后 | 提升 |
|---|---:|---:|---:|
| Universe momentum 入选占比, 合成偏斜池 | 84% | 39% | 降低 45 个百分点 |
| Universe member regime 缺失, 生产库最近 500 条 | 500/500 NULL | 新路径写入真实 regime | 新增 1 个持久化字段和 1 条回归链 |
| Universe market type, 生产库最近 500 条 | spot 500 / perp 0 | auto 同 symbol 优先 perp | 去重策略从分数覆盖改为配置一致 |
| scanner 衍生品上下文字段 | 0/3 | 3/3 | 补齐 funding/OI/OI delta |
| Coinalyze DU-T01 | xfail/missing | implemented L2b | 从不可证明变为离线可证明 |
| Trades subtab 数量 | 3 | 2 | 减少 1 次人工切换路径 |
| Limit entry plan | 可缺字段 | 4 字段缺一拒单 | entry plan 完整率由风控兜底强制到 100% |
| pytest 速度认知 | 以为很慢 | 1699 tests / 约 110s | 明确无网络超时级慢测 |

## 4. 建议合并顺序

1. `fix/direction-null-edge-honesty`
   先修 learning/risk edge 统计语义, 避免后续反馈继续把 unknown 当 long。
2. `fix/universe-member-regime-tag`
   让 member 层持有 regime, 为后续分析和反馈闭环保留上下文。
3. `fix/universe-market-type-dedupe`
   修正 auto/perp 方向, 防止 spot-only 池继续压制衍生品信号。
4. `fix/universe-signal-derivatives-context`
   把已存在的衍生品 feature 传到 Opportunity。
5. `fix/coinalyze-scheduler-persist`
   补齐 Coinalyze OI/liquidation 的 scheduler 写库闭环。
6. `fix/system-operator-name-residuals`
   最后合 wording, 避免文档/测试描述冲突干扰行为分支。

合并后建议再跑:

```powershell
uv run pytest tests/specs/spec_data_utilization.py tests/specs/spec_universe_pipeline.py tests/specs/spec_market_type_dedup.py tests/test_universe_signal_emitter.py -q
uv run pytest tests/test_universe_calibration.py tests/test_anti_hallucination_e2e.py tests/specs/spec_risk_engine.py::test_re_b17_check14_is_family_evidence_gate -q
uv run pytest tests/specs/spec_pi_decision_chain.py tests/specs/spec_pipeline_position_manager.py::test_ruleset_exit_bypass_removed tests/specs/spec_serenity_attribution.py tests/specs/spec_serenity_promote.py tests/test_pi_daemon.py -q
```

## 5. 剩余 owner 决策

| 项 | 原因 | 建议 |
|---|---|---|
| `reduce_size` 是否从 advisory-only 升级为硬风控 | 会改变 Risk Engine 行为和仓位管理语义 | owner 决策后单独分支 + RISK spec |
| app.db 膨胀和低信号 lesson 生成 | 属运维/数据保留策略, 不能静默硬删 | 先定 retention/archive 策略, 再做 maintenance 分支 |
| 侧边栏完整 IA 大改 | owner 只明确 Serenity 不应在 Evolve, 未给完整目标结构 | 目前已移动 Serenity; 其余等 IA 草图 |
| TypeScript 后端迁移 | 高成本重写, 不能当作 bug fix | 不迁移后端; 后续可做 Pydantic -> TS 类型生成 |
| 历史 docs 中 PI Agent 存档称呼 | 改历史记录会破坏审计语义 | display/runtime wording 已改, 历史归档保留 |

## 6. 当前结论

当前不是“缺一个大功能”, 而是多个上游字段和调度断点导致闭环质量下降。已完成的修复把
owner 口述中的关键问题拆成 12 个可核验落地点, 其中 6 个已在 master, 6 个在独立
分支待合并。剩余项不是可以安全自动改的 bug, 而是需要 owner 决策的风险、数据保留和
产品 IA 边界。
