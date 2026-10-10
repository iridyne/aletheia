# 文档金标准审查（2026-10-10，除学习/反事实外全部）

> **角色：Evidence（冻结）。** 一次性审查结论，不是现行权威、不是进度表。现行行为以
> `docs/design/contracts/` 为准，目标设计以 `docs/design/specs/` 为准。

## 基准

- 格式基准：[SDD 规格格式](../../../design/specs/README.md) 与已批准的
  [spec 0001-learning-loop](../../../design/specs/0001-learning-loop/spec.md)：每条要求一句
  EARS（触发 → 响应）、可判定的 WHEN/THEN 场景、失败语义区分 unavailable 与零、
  权限边界写明、叙事和 Issue 号不进正文。
- 产品基准：[Capability Ground Truth](../../../product/CAPABILITY_GROUND_TRUTH.md)、
  [Active Runtime Scope](../../../product/ACTIVE_RUNTIME_SCOPE.md)，以及 owner 2026-10-09/10 的
  方向：只做 USDT 本位加密永续（现货、RWA 永续出范围）、Bitget UTA v3、AI 像人类交易员一样
  决策并从复盘中学习（不是量化挖因子）、一个能力只留一份实现。
- 审查标准：[criteria.md](criteria.md)。每条"代码不存在/无调用方"的结论来自实际 grep。

## 逐域报告

| 域 | 报告 | 覆盖 |
|---|---|---|
| 执行层合同 | [execution_layer.md](execution_layer.md) | execution、entry-plan、entry sizing、risk engine、decision lifecycle、kill conditions |
| 交易流程合同 | [trading_pipeline.md](trading_pipeline.md) | candidate selection、filter evidence、opportunity outcome、trading mandate、serenity、scheduler、agent |
| Universe + 数据 | [universe_data.md](universe_data.md) | market structure、coverage、OHLCV、TA、derivatives、universe baseline/twin/relative-rank、data_model、data_layer |
| 平台/审计/观测 | [platform_contracts.md](platform_contracts.md) | audit chain、persistence、live unlock、LLM harness、profit loop/gradient、rationale、Observation Lab ×4、status/settings/config/dashboard、template |
| 产品/治理/运维 | [product_governance_ops.md](product_governance_ops.md) | SCOPE、CAPABILITY_MAP、DIRECTION、gates、experiment protocol、capability-integrity、DECISIONS、audit gates、bug triage、DEPLOYMENT、RUNBOOK、architecture、operator_layer |
| 提案 + 研究 | [proposals_research.md](proposals_research.md) | design/proposals 全部（除 learning/tokenomics）、research 角色错位清单 |
| 前端/API/导航/开发运维 | [frontend_api_ops.md](frontend_api_ops.md) | frontend、api_reference、其余 data、market-systems、operator 设计、SYSTEM_MAP、OPERATOR_SHELL、decisions、operations |
| 编排者自查 | [orchestrator_notes.md](orchestrator_notes.md) | scheduler 实际注册 job 对照、失效的 pre-commit 钩子 |

## 框架各部分"有没有道理"

按 owner 定义的最小盈利主线（Universe → Candidate → 挂单 → 成交 → 持仓 → 平仓 → 复盘 → 反馈）判断。

| 部分 | 判断 | 依据 |
|---|---|---|
| Universe → Candidate Selection Utility | 有道理，主线 | 唯一发布权威（`config/loader.py` 强制）；但文档里"当前选择权威"有 step-v1/v2/v3 三个错误版本 |
| Planning Generation → Entry Planner → Compiler → Entry Plan | 有道理，主线核心 | AI 只给定性假设，确定性 Compiler 定几何，与"AI 像人一样判断、数字由规则保证"一致；entry-plan 合同内有一张已退役词汇表（`execution_urgency`/`BOUNDED_URGENT`）与现行 `capture_priority` 矛盾 |
| Entry sizing / financing | 有道理，写得最好 | 最接近 spec 形态的一份合同 |
| Risk Engine | 有道理，唯一硬门 | 22 关中有容量/杠杆多处叠加；#15 Lesson Constraints 与 spec 0001 冲突 |
| Execution Gateway / UTA / 对账 | 有道理，主线 | UTA v3 已是现行实现，提案仍写 accepted；合同把 OKX/Binance/现货写成现役 |
| 持仓管理 / 平仓 / 验证平仓 / Outcome | 有道理，主线 | 规则具体但挤在 execution_contract §10–§11（约 45% 篇幅），应独立成合同 |
| TS operator-shell | 有道理（唯一 Operator） | **没有行为合同**：agent_contracts 与 OPERATOR_SHELL.md 互相指向，谁都没写 |
| 市场结构 / OHLCV / 衍生品事实 | 有道理，Planner 的上游 | 结构合同漏写最重要的消费者（Entry Planner/Compiler 的锚点） |
| Observation Lab | 部分有道理 | 入场每次规划都写 outbox，spec 0001 的随机对照要复用它；Worker 模式、filter-shadow、ranker/observer 适配器是化石 |
| Serenity 研究来源 | 实验性，默认关 | 合同未标实验性；xStock 执行与 BTC/ETH 计价放行违反"只做 USDT 永续" |
| news_fetch / news_analyze | 存疑 | 默认开、每 5 分钟调 LLM，唯一消费者 Serenity 默认关；与交易决策抢同一个 agents worker |
| equity_observer / prediction_observer / CN equity | 没道理（出范围） | 产品明确排除；代码仍注册并有页面/端点；CN 代码 0 行但四份文档各写一遍 |
| Trading Mandate | 没道理（化石） | `assess_trading_mandate_fit` 无生产调用者 |
| Universe twin / relative-rank 影子实验 | 没道理（化石） | 生产写入 2026-08-11 已删；量化排序实验与方向不符 |
| persona sizing | 没道理（双实现） | entry sizing 合同明文排除，但 `persona_rollout` 仍每 8h 运行 |
| profit-gradient / profit-loop 量化归因 | 冻结 | 约 6.4k 行，确认层映射为空；单位与 spec 0001 的 R 冲突 |
| Kill conditions K1/K2 | 纸面条件 | 函数只被测试调用，运行时无人执行；K3 与 live_unlock §4a 重复 |
| Python Operator / Ranker / PaperTrader / Market Observer / 本地模拟器 | 已退役 | 退役完成（grep 证实）；但仍有 `PI_OPERATOR_DESIGN_DECISIONS`、`operator_layer`、`SYSTEM_ARCHITECTURE_GUIDE`、`data_utilization` 等把它们写成现行 |

## 共性问题（按影响排序）

1. **会让人做错事的运维文档**：RUNBOOK 要求 `permission set Paper`，而 Bitget Demo 路由要求
   `Full`（`config/exchanges.py`、`trading/entry_authority.py`），照做零入场。
2. **现货/OKX/Binance 现役叙述**散在 20+ 份当前文档；`config/schema.py` 的
   `enabled_markets` 默认仍 `spot: True`，`market_data.ccxt_fallback` 默认 `bybit`。
3. **合同积层**：每次修复往合同里追加带 Issue 号与日期的叙事段，估计执行层 35–40% 是这种内容；
   行号锚点普遍失效。Contract 模板没有编号要求与失败语义表，是根源之一。
4. **提案状态落后**：16 份提案写 accepted，实际已实现或被取代；4 处合同反过来把提案当权威。
5. **主线缺合同**：operator-shell 行为、"只做 USDT 永续 + RWA 黑名单"边界（只在
   `data/symbols.py`）、当前入场理由存哪里（`trade_decision_rationale` 仍写退役的 PiDecision 五项）。
6. **同一问题多个答案**：测试在哪跑（CASI WSL / Fedora / self-hosted runner）、远程 CI、Windows、
   A 类修复是否重开观察窗口。
7. **学习相关的跨域冲突**（交给 spec 0001 实施时处理）：risk_engine #15、CAPABILITY_MAP
   CAP-LEARN-02/03、GROUND_TRUTH 把学习写成"冻结/可选研究"、`extensions/sync/importer.py`
   白名单仍含 `ps_lessons`、spec 0001 Contract Delta 漏列 `risk_engine_contract.md`。

## 需要 owner 裁定的事项

见本次 PR 描述；裁定前文档只标"冻结/出范围"，不删除对应代码。
