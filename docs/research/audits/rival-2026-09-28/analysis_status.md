# 专项分析状态与剩余范围

初次快照：2026-09-28，源码基线 `0318e5efc6bca6ac5be3393c68fcd90cc5195d70`。本页替代“已 review / 从未 review”二分法：结构扫描、调用链审查、行为验证、方案、实现分别记录。该轮由主 Agent 独立 review，当时未创建 issue 或实施修复。

**后续推进补充（同日 22:45 +0800）**：Owner 后续要求编写可由新 Agent 领取并修复到合并的 issue body，已新增三条、完善五条，见 [发布快照](issue_bodies/README.md)。截至本次 GitHub 核对，其中六项已有开放 PR，详见 [全局判断](project_convergence_direction.md)；开放不等于已合并。本会话未派发 Agent，也未实施业务修复。后续状态以 GitHub 为准。

## 已落地分析

| 范围 | 当前产物 | 已做到 | 尚未做到 |
|---|---|---|---|
| CLI | [扫描报告](cli_shitmap.md) | 33 文件源码阅读、重点隔离探针、根因/最小修法/回归建议 | 尚未将全部发现整理成与 Server 同等完整的实施方案；无业务修复 |
| Server | [扫描报告](server_shitmap.md)、[完整方案](server_extensions_repair_plan.md) | 29 文件结构扫描、重点调用链、逐项修法与验收 | 非逐路由行为验证；无业务修复 |
| 九组 extensions | [扫描报告](extensions_shitmap.md)、[完整方案](server_extensions_repair_plan.md) | analytics/research/exchanges/knowledge/telegram/model_endpoints/sync/counterfactual/regime，78 文件结构扫描及重点调用链 | 非全部扩展，也非每个文件深审；无业务修复 |
| Core analytics / economics | [分析与方案](core_analytics_economics_review_plan.md) | 本轮 63 文件结构扫描；4 项确认问题的证据、修法、验收，其中 3 项复用旧 issue | 其余 cohort/身份/快照路径深审未穷尽；无行为复现 |
| model_provider / config | [分析与方案](model_config_review_plan.md) | 本轮 17 文件结构扫描；2 项已有问题在当前源码仍成立，补齐修法 | 配置组合、全部 writer、取消/热切换并发未验证 |
| Core data + extensions/data | [分析与方案](data_review_plan.md) | 本轮 50 文件结构扫描；历史下载形成中 K 线入库证据链及方案 | 各 collector 和来源时钟/provenance 深审未穷尽；无行为复现 |

本轮新增覆盖共 130 个 Python 文件、53,609 物理行、516 个 AST except。**七项确认问题包含五项已有 OPEN issue 和两项限定查重未命中的根因；不是七个新缺陷或七项已修复。** 模型健康形状问题归 Core A4，仅计一次。报告中的测试均为待实施验收设计，本轮只做文档检查。

## 剩余分析

| 工作 | 真实状态 | 下一步应产出 |
|---|---|---|
| agents、files、maintenance、backup | 本专项尚无完成报告；不等于全历史从未被审过 | 实际生产入口、失败传播/持久化证据与最小完整修复方案 |
| trust、update、infra、utils、uta | 本专项尚未打包审查 | 按真实调用边界分组，不以目录小就免审 |
| extensions/learning | 旧审计存在；18a 的 20-gap 重新核验没有结果文件 | 每条旧 gap 的当前源码/契约/状态；删除过时假设 |
| Admission GAP-3/4/5 | 18b 仍是提议 brief，没有当前核验结果 | 逐条复核，不能从旧报告直接改交易准入 |
| `_run_coro` 跨 loop | 18d 仍是提议，没有复现结果 | 先定位实际 caller 与所有权，再决定安全的隔离复现；Risk 冻结保持 |
| Core analytics/economics 的旧 18c | 已由本轮扫描与方案接替，不再要求每维强凑五项 | 剩余深审范围见新报告；不宣称逐行全审完成 |
| CLI 完整方案 | 现有报告已有局部修法，但格式和实施闭环尚未统一 | 按实际根因补充改动边界、实施顺序和完整验收；复用原证据 |
| 已审过模块的部分覆盖 | universe/trading/execution/agent_shell/agent_runtime/portfolio/storage 等此前多为局部 review | 需要时明确文件/路径粒度补盲，不能把旧局部审查称为模块全部完成 |

上表保留尚未分析范围。**下一步优先级已由后续全局复核更新**：先推进已知高影响修复、运行持续性与当前主线经济证据；暂缓扩大目录扫描。Learning / Admission 旧 gap 按需要单独核验，不与普通清理混成政策变更；Risk 不纳入本轮改动范围。依据见 [全局判断与证据](project_convergence_direction.md)。

## 历史文档的使用边界

17 / 19 保留为历史材料，里面的外派、并行度、默认 retention 数值和按表捆绑 PR 的安排不再是执行指令。18a/b/d 仅保留待核问题，18c 由新报告接替。没有发给其他 Agent，也没有自动启动旧 Wave 或 P1。

“异常多”“文件大”“符号 grep 无引用”不足以证明缺陷、重复实现或可删除。后续仍先证明调用可达、真实语义和权威来源，再写最小修复方案。实现完成需要独立代码证据与验收；commit 仅能证明本轮文档已保存，不能证明功能修复或 runtime 合格。
