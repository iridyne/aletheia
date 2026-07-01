# Current Task

> 只写意图/计划,不写运行态事实(regime/持仓/db 大小/gateway 状态——这些换台机器必过期)。
> 真实状态一律 `aletheia doctor`。详细意图叙事见 `docs/_ai/CURRENT_TASK.md`(canonical)。

## Status: 动态 sizing/persona/learning 系统 P0–P4 已实现(全默认休眠)

Operator 在风控硬边界∩persona区间内动态决仓位/杠杆/加仓/止损 + Learning 拟合 + 渐进解冻,
五期全部落地且**默认 OFF = 零行为变化**(commit 3e8015f/ad580a1/9c27dc4/3fbdd79/884e035;
设计 `docs/design/dynamic_sizing_operator_persona_design.md`;memory dynamic-sizing-persona-system)。
唯一未接=金字塔实盘 scale-in 下单(policy 已实现,下单触 execution 边界,需 owner 授权+给 max)。
**下一步(owner)**:分步开启——persona_sizing→跑干净窗→progressive_unlock→learning→(授权)金字塔;
每步 demo + 看 doctor `loop.sizing_policy` + 干净窗,退化关回。

## 前一状态: reconciler 假孤儿 thrash 已修复(5d6bb89)—— 干净窗从此刻重新起算

观察 2A+.10 做空链路时发现并修复一个**污染整个观察窗的根因 bug**:非 Bitget 账户上
reconciler 在 unified 缓存过期时读空的 legacy Bitget 缓存 → 误判全部 OKX 持仓 orphaned →
本地平仓(不动交易所)→ 重认领 → 无限 thrash。41% 仓位 reconciled_gone、56% adopted、
strategy 归因被毁。已 SDD 修复(P0 broker 感知回退 + P1 Scorecard 去污,spec PL-ADOPT-10~12/
AN-S03,full suite 1815/0)。详见 `docs/research/ENTRY_EXIT_ASYMMETRY_ANALYSIS_2026-07-01.md`
+ AI_HANDOFF/DEVLOG。**修复前的 adopted 碎片/thrash 数据不计入策略评估,干净窗从修复后起算。**

**P2 待 owner 批(触 execution 边界)**:adopt 仓下交易所保护单 / adoption 保留 strategy_id /
factor_feedback 是否过滤 thrash。

---

## 上阶段:Phase 2A + 2A+ 全 CLOSED —— 进入「先跑攒干净窗」观察阶段

本会话把 Phase 2A+ 剩余四项(2A+.2/.4/.9/.11)一次收口,ROADMAP 的 Phase 2A(可执行策略)
与 Phase 2A+(方向性)代码层面全部关闭。commit `f093e36`(实现) + `8cea488`(文档),push master。

### 本次完成
- **2A+.2** designer 写 applicable_regimes(`_derive_regime_targeting`;**须写 JSON 数组**,
  否则 ranker `json.loads`+`isinstance(list)` 静默丢弃使过滤失效;SP-S04 升 L2b)
- **2A+.4** 前端 Direction 列 + drawer(`fmtRegimes` 解析 JSON)
- **2A+.9** `family_quota_by_regime` regime 感知配额 + `resolve_family_quota()` + Settings UI(UP-16)
- **2A+.11** `score_volatility` 接 atr_4h/bb_width_4h 4h 多 TF 确认(UP-15,反向验证防死码)
- 验证:conformance 493→496 全绿;full suite 1815 passed / 0 failed;build 通过

## Next

**不再堆 phase**(owner 定「先跑再说」)。唯一剩 2A+.10 端到端 short 验证 = 运行观察项(非代码):
- 观察 bear/volatile regime 下做空链路端到端是否跑通(scanner→机会→signal→PI→执行→退出)
- 观察 bull regime 翻转时 short 是否被 regime gate 正确过滤
- 核心问题:集中做空到底有没有 edge(信号是否仍近随机)——运行态证据留 `data/observation/`(gitignored)

其余活动/并行工作线(TaG 借鉴、账户级纳管)见 `docs/_ai/CURRENT_TASK.md`。
