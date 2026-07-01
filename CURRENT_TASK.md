# Current Task

> 只写意图/计划,不写运行态事实(regime/持仓/db 大小/gateway 状态——这些换台机器必过期)。
> 真实状态一律 `aletheia doctor`。详细意图叙事见 `docs/_ai/CURRENT_TASK.md`(canonical)。

## Status: Phase 2A + 2A+ 全 CLOSED —— 进入「先跑攒干净窗」观察阶段

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
