# 全项目收敛与最小盈利循环：全局判断及下一步方向

## 证据身份与结论

本报告冻结于 **2026-09-28T14:45:34Z（北京时间 22:45）**，是一次分析证据快照，不是执行看板。GitHub Issue/PR 承担后续推进状态。初轮分析绑定 `master@a998070130d2ab892e9ba89b7de48886a1a708e4`；发布前重新拉取到 `84f09def1ef2cea0a7a4126c2e7ff55739a14990`，补充期间新增的布局提交与开放 PR。当前分析分支为 `iridite/rival_analysis`，并不等于 master。

本轮读取 GitHub Issue 正文、最新更正评论、PR 状态/说明和固定版本源码；没有重新读取 Fedora 数据库、运行测试或经济实验。运行数字引用已经发布的历史证据，不能理解为本时刻的实机快照。PR 中报告的测试结果亦为该 PR 的证据，本轮未重跑或独立审核这些修复。

**判断：主链与物理布局已明显收敛，最小自然交易闭环已有证明；下一步应关闭已知高影响缺陷、稳定当前运行版本、取得可比较的经济证据。** 去屎山继续由真实断点、重复权威和证据缺口驱动，暂缓按目录扩大同深度扫描、按函数行数重构以及新增策略/市场机制。已有交易闭环并不等于正盈利边缘已被证明。

## 已完成什么，仍缺什么

| 层面 | 截至快照的判断 | 支撑材料及限制 |
|---|---|---|
| 产品/架构边界 | Scheme A 布局和 Core 缺少扩展时的安全降级已实现；Bitget Demo 是当前资格路线 | [Capability Ground Truth](https://github.com/iridyne/aletheia/blob/84f09def1ef2cea0a7a4126c2e7ff55739a14990/docs/product/CAPABILITY_GROUND_TRUTH.md)、[Active Runtime Scope](https://github.com/iridyne/aletheia/blob/84f09def1ef2cea0a7a4126c2e7ff55739a14990/docs/product/ACTIVE_RUNTIME_SCOPE.md)。它们不宣告整个 #3134 完成，也不授予 Live |
| 重复实现/状态投影 | PnL 算术、最新持仓读取、心跳和 snapshot 新鲜度已取得真实收敛 | 已合并 [#3623](https://github.com/iridyne/aletheia/pull/3623)、[#3615](https://github.com/iridyne/aletheia/pull/3615)、[#3616](https://github.com/iridyne/aletheia/pull/3616)、[#3633](https://github.com/iridyne/aletheia/pull/3633)；局部收敛不能外推为整个模块没有缺陷 |
| Position 到完整 Outcome | #2899 已于 09-26 关闭，有 10 个自然成本完整且合格的 Outcome | [Owner 接受的关闭证据](https://github.com/iridyne/aletheia/issues/2899#issuecomment-5845556673)，证据绑定当时 `84af70e2` 的 Fedora 读取和各 Outcome producer；不是当前 master 的新运行证明，也不是 profitability 证明 |
| 结算最终 owner | #3135 的当前实现已有统一完整关闭经济写者；不应按 OPEN 状态重造它 | [09-27 验收](https://github.com/iridyne/aletheia/issues/3135#issuecomment-5860285029)：306 项相关检查通过，当时部署后无新自然关闭样本；#3135 仍 OPEN，缺少当前构建自然验收不等于缺实现 |
| Operator 可靠性 | 排队结果误读、复盘输出预算、退役命令/陈旧失败建议有实质修复 | [#3647](https://github.com/iridyne/aletheia/pull/3647)、[#3648](https://github.com/iridyne/aletheia/pull/3648)、[#3660](https://github.com/iridyne/aletheia/pull/3660)；[收敛记录 §1o](https://github.com/iridyne/aletheia/blob/84f09def1ef2cea0a7a4126c2e7ff55739a14990/docs/research/audits/CONVERGENCE_FINDINGS_2026-09-28.md) 有特定部署后的观察，不证明后续所有版本健康 |
| 连续运行 | 同步 job 墙钟、监督循环持续存活证明仍有待核/修复点 | [#3645](https://github.com/iridyne/aletheia/issues/3645)、[#3612](https://github.com/iridyne/aletheia/issues/3612)。#3645 尚未逐 job 分类，也未证明 protective 车道真实永久卡死 |
| 经济有效性 | 前瞻账本有按族数据，但尚无足够证据证明当前整条策略的正净边缘 | [#3644 更正](https://github.com/iridyne/aletheia/issues/3644#issuecomment-5864600078)。历史回溯、成交侧归因、当前版本的前瞻验收是不同证据 |

### 发布前新增推进：已有六项修复 PR，尚未合并

前一轮答复中的“首批八项尚未看到对应 PR”只适用于当次查询，**现已过时**。以下关联以本次快照的 GitHub 标题与既有任务对应，状态均为 OPEN；本轮未审核其实现和验收，不把 review-ready 视为 MERGED。

| 既有任务 | 对应 PR | 快照 HEAD 前缀 |
|---|---|---|
| #3675 Preview seed 隔离 | [#3679](https://github.com/iridyne/aletheia/pull/3679) | `69b9fe85` |
| #1379 归档配置脱敏 | [#3680](https://github.com/iridyne/aletheia/pull/3680) | `9fbc802c` |
| #1332 未知鉴权拒绝 | [#3681](https://github.com/iridyne/aletheia/pull/3681) | `db5c62e1` |
| #1624 预算脱离 shadow | [#3682](https://github.com/iridyne/aletheia/pull/3682) | `794bdc50` |
| #3676 历史收盘 K 线 | [#3688](https://github.com/iridyne/aletheia/pull/3688) | `730fce81` |
| #3677 完整窗口汇总 | [#3689](https://github.com/iridyne/aletheia/pull/3689) | `9cd2a075` |

#1725 schema 完整性、#1395 下载失败退出码在本次快照的开放 PR 标题列表中未找到明确对应项；这不证明没有本地工作。领取者先看最新 GitHub，不重复启动六项已有 PR 的实现。另有 #3685/#3686/#3687 的相邻修复在途，不计入上述六项。流程 PR [#3653](https://github.com/iridyne/aletheia/pull/3653) 仍 OPEN，不能把其新验证规则提前当作 master 已生效规则。

`a9980701..84f09def` 另有六个 master 提交，集中在 tests/helpers、脚本按用途分组、文档位置和无用脚本删除。它们体现布局继续整理，不能据此推断交易或经济效果改善。特别是门禁已在新 master 移到 `scripts/ci/`，如 `scripts/ci/dev_check.py`、`scripts/ci/check_docs.py`、`scripts/ci/ci_preflight.py`；[首批 issue 正文快照](issue_bodies/README.md) 中旧路径绑定当时版本，领取者须读最新 AGENTS/Workflow 后用真实存在的入口，不创建旧路径 shim。此分析分支尚未合入这些布局提交，分支自身文档验证仍使用其现有入口。

## 会改变方向的四个证据更正

### OPEN 不等于未实现

[工具函数收敛 #3540](https://github.com/iridyne/aletheia/pull/3540) 已合并为 `12fe11742`，35 个分散 helper 收敛到三个模块，本轮确认该提交为所查 master 祖先；#3523 仍 OPEN 不能解释成所有工作还没做。`360732a12` 也已在 master 中，修正 outcome/decision 保留期并添加 cohort reachability gate。应逐项核对尚缺什么，而非重新运行整批旧计划。

#3134 保持 OPEN 有其完整 DoD；#2899 CLOSED 只证明其有界闭环。#3214 已 CLOSED，旧 #3134 评论中把它列为开放是旧快照；#2901 仍 OPEN。对每条剩余项都应区分实现缺口、证据缺口、过时状态。

### 前瞻按族度量存在，成交侧归因仍缺

[#3644 的后续更正](https://github.com/iridyne/aletheia/issues/3644#issuecomment-5864600078) 显示 `universe_forward_outcomes` 自带 scanner_family，不依赖成交 Proposal 的 opportunity_id；历史读取获得 99,465 行成本完整结果。因此“按族归因结构上不可能”“所有度量停摆”均不能作为当前结论。

实际成交 family_scorecard 仍沿 Position → Proposal.opportunity_id → Opportunity 解析，而 Universe EntryPlan 本来不应伪造 Opportunity。当前源码缺口可以支持修复成交侧读取投影，但不能支持恢复旧准入主线。应沿现有 Candidate/Planning/EntryPlan 的精确来源身份补齐，保留 Serenity 自身祖先语义。两个统计总体不能为了得到非空报表而被强行连接。

### #3569 的首个根因已撤回

[#3569 更正](https://github.com/iridyne/aletheia/issues/3569#issuecomment-5857255919) 明确指出：真正失败的 join 两侧语法原本一致，问题涉及已无生产 caller 的历史 producer、退役 market_observer 条件和保留期反转。不得据原标题改前缀或把方向无关的 member 投射成带方向经济结果。保留期与门禁已部分修复；历史 reader 应诚实表述 retired/historical，不复活 producer 来填满旧仪表盘。

### 样本混合与滑点解释不能继续沿用

[#3621 更正](https://github.com/iridyne/aletheia/issues/3621#issuecomment-5864551306) 记录：曾引用的 446 笔中 94% 是 paper_internal 归档模拟数据。混合 232 笔止损的 12.57 USDT 滑价、18% 命中率和相应反事实，并非当前 Bitget Demo 估计。也不能把“止损贡献了大部分亏损”解释成止损机制无效，或以少量极值样本外推盈利改善。

[#3619 更正](https://github.com/iridyne/aletheia/issues/3619#issuecomment-5864565941) 已撤回旧的收益上界外推；仍应进一步保留账户/模式和版本限制。原生 TPSL 也已有执行时开关修复 [#3431](https://github.com/iridyne/aletheia/pull/3431)，当前 coordinator 按 native_tpsl_enabled 与 adapter capability 判断。无挂单 ID 不能单独证明功能未实现；需核对配置、能力和实际放置结果，不能自动开启功能。

## 下一步的三条工作线

### 关闭已知高影响缺陷，补齐运行持续性

推进首批 issue 的已有 PR 验收和未覆盖根因，优先数据隔离、秘密脱敏、鉴权，再按实际风险安排预算、数据完整性和失败语义；不要对已在途项重复派发实现。每个独立根因的成功标准是可失败回归、实际修复、自审、确切提交与合并结果，之后按需要另取得部署证据。

针对 #3645 先核清真实同步 job、阻塞 I/O 与写者边界，证明受影响车道与恢复要求；仅在外层报 timeout 而让旧写者继续运行、再释放互斥启动新写者，不构成安全恢复。#3612 的健康信号应来自监督循环实际进展，不能来自独立定时器或“近期有交易产物”；合法不交易不应导致重启。这些是验收方向，不是本轮已完成的代码设计或运行复现。

### 复用现有工具，取得当前主线可比较的经济证据

复用 [Profit-loop observability contract](https://github.com/iridyne/aletheia/blob/84f09def1ef2cea0a7a4126c2e7ff55739a14990/docs/design/contracts/profit_loop_observability_contract.md) 已有的 candidate-facts、position-replay、position-story、diagnosis/gradient，不新建度量平台。首先确认同一冻结证据集中：

- Candidate 分母包括被过滤、跳过、拒绝、未成交及成交的状态；不能只选最后成交样本。
- Candidate 的固定 horizon 结果与 Position 的真实净经济结果分别表达，模型成本与实际成交成本来源明确。
- 固定账户、execution_mode、instrument、source、producer/behavior identity、时间窗与成本口径；旧模拟结果不混入当前 Demo。
- 按既有独立单元/聚类约定估计不确定性，不把重复标的、多 horizon 或连续快照当作独立样本。
- 未观测、过期、retired、样本不足和读失败可区分；数据行多不等于证据有效。

重点回答：候选总体哪些组合值得进一步检验；System Operator 选中样本相对同一可比总体呈现何种差异；实际 Execution/Holding/Exit/Costs 如何解释剩余差异。观察到的选择差异不自动构成因果增益，不能从平均数直接宣称 AI 有贡献。

成交族归因与必要持仓路径覆盖是补盲；#3618 的滑点记录是诊断分解，不能重复扣减已按实际 Fill 计算的 PnL。[Execution Contract](https://github.com/iridyne/aletheia/blob/84f09def1ef2cea0a7a4126c2e7ff55739a14990/docs/design/contracts/execution_contract.md#L703) 已明确该边界。

新合并的 [#3671](https://github.com/iridyne/aletheia/pull/3671) 使用 policy 杠杆与 available-balance sizing；[#3672](https://github.com/iridyne/aletheia/pull/3672) 对齐相关 fallback。即使定位为契约修复，也可能改变实际资金占用与金额结果。必须确认它们是否部署，再区分修前/修后 cohort；本轮没有证明 Fedora 已部署这些提交，不能拿旧样本验证新行为。

### 收口 #3134 的剩余验收，再验证一个有界盈利假设

按 [#3134 DoD](https://github.com/iridyne/aletheia/issues/3134) 对每项绑定当前代码/运行证据：已经满足的收口；仅缺自然样本的明确等待条件；仍有主线断点的只修该点。不因多个结算步骤就假定存在多个最终写者，不因文件较大就拆开原子边界。

稳定证据允许提出一个可证伪假设，再按既有 authority 决定受控前瞻验证。旧账本中 momentum 24h、mean_reversion 4h 的回溯统计只能帮助提出假设；其聚类 t 值约 0.69/0.92 不能证明正边缘，也不能支持看到负均值就自动停用某族。观察窗、比较对象、成本、独立单元和停止条件应在后续样本出现前确定。盈利研究不是自动修改准入/Risk 的权限。

## 暂缓事项与完成尺度

暂缓全目录同深度扫描、按行数拆函数、为了填旧报表恢复退役机制、新市场/新策略扩张，以及把验证流程继续扩成新框架。流程 #3653 可按其自身边界收口，但不能代替业务证据，也不能把它的未合并规则套用到现行 master。

接下来衡量推进应看：关闭了哪个确定断点、是否减少重复权威、是否能持续产出可信证据，以及是否得到当前行为版本下的可比较结果。commit/PR 数、减少行数、测试总数和自然成交数量，都不能单独证明盈利或全项目收敛。

本次 Owner 仅要求更新分析并推送远程分析分支，**不合并该分析分支到 master**。本报告不启动实施、合并其他 PR、部署、修改真实凭证、调整 Risk/Live、重写历史或重置观察窗。
