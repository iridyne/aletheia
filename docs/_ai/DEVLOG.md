# Development Log

## 2026-07-10 — Opportunity profitability lifecycle review and repair

- 新增完整评审与分阶段修复计划，明确当前可执行范围仍是加密现货/永续；股票、预测市场、FX、RWA 的真实能力分别标为 observation/shadow/unsupported，避免字段存在即被误解为可盈利执行。
- 修复 OKX swap `quoteVolume=None` 导致全部永续候选被当 0 成交额淘汰；统一成交额换算供预筛和特征存储使用。
- 修正 `short_bias` funding 经济含义；同 symbol 的 spot/perp 在 family quota 分配前按配置选唯一市场。
- PI 的 `bear_case` 改为新决策必填并真实进入 parser；proposal 按最终 market type 读取 snapshot。
- pending shadow 不再生成 resolved 事件；漏斗拆分事件吞吐和 Opportunity-ID cohort，所有转化率只来自后者；动态金额风险原因归一聚类。
- doctor 新增 recent Universe feature 的 spot/perp 覆盖；原 `loop.profit` 成功文案改为 proposal-flow healthy / profitability not assessed。
- 保持不动：risk/execution/live/credentials、方向刹车、学习权重和 sizing。后四项先补净收益与逐过滤器对照数据。
- 验证：聚焦批次 75 passed；扩展 Universe/决策/期货 234 passed（后续兼容桩 23 passed）；漏斗/doctor 回归通过；完整 specs 575 passed、7 skipped、6 xfailed；architecture fitness 11 passed。最终全量在排除 3 个已于未修改 master 独立复现的既有失败后为 2588 passed、14 skipped、6 xfailed。既有失败：macOS 环境运行 Windows autostart 路径断言，以及两条 knowledge maintenance 索引清理测试。

---

## 2026-07-10 — Local-paper exchange status honesty

- Root cause: the Home status row used the configured exchange preference as if it
  were an active external connection. Local paper therefore probed an intentionally
  absent exchange and showed a red warning; restored exchange cache data could also
  be shown as local-paper equity.
- Status now exposes whether the active execution path uses an external exchange.
  Local paper labels itself as having no exchange, skips the probe, and ignores
  external account cache/UTA data. Gateway and Telegram status output use the same
  label. External demo/live behavior is unchanged.
- Validation: focused status, exchange-sync, and frontend-source tests passed;
  production frontend build passed.

---

## 2026-07-10 — Separate current risk from decision history

- Root cause: GET /api/risk/status copied the newest rejected risk decision into the
  current violations list. A fixed historical rejection therefore held the dashboard
  in warning state indefinitely.
- Active risk now contains only live kill-switch and live-configuration conditions.
  latest_risk_decision remains immutable history and Overview labels a rejected latest
  check with its timestamp rather than presenting it as a current violation.
- Validation: risk-route and focused risk tests passed; frontend production build
  passed. No risk decision was deleted or rewritten.

---

## 2026-07-10 — Paper simulation false $0 cap repair

- Root cause: paper_internal has no account-equity series, but the drawdown guard
  borrowed exchange_account snapshots. An unrelated historical peak followed by a
  small current exchange balance fabricated a >20% drawdown and multiplied the
  configured $200 cap by zero.
- paper_internal now skips drawdown scaling. It remains bounded by the configured
  position cap, daily-loss guard, max-open-position guard, and all other risk checks.
  Exchange-backed demo/live modes continue to use real exchange-account equity.
- Added behavior tests for local-notional collapse, unrelated external snapshots, and
  external-account loss. Updated the risk contract and executable check RE-B05b.
- Validation: focused risk tests 59 passed; full executable contract suite 573 passed,
  7 skipped, 6 expected skips.

---

## 2026-07-10 — Opportunity lifecycle integrity repair

- 前置快照资格检查改为严格匹配 spot/perp，避免同名现货报价让永续机会误进可执行队列。
- lifecycle CLI 不再凭当前字段虚构 ranker 拒绝；debate 仅在它实际把可交易决策降到门槛以下时记为
  debate downgrade；trace 按因果顺序展示，同时保留真实写入时间。
- 机会的前向收益按 `universe_snapshot_id` 与 `member_id` 精确绑定，避免复用 member 后串结果。
- filter feedback controller 暂停输出具体 filter 的收紧/放松建议，直到管道保存每个 filter 自己的
  放行/拒绝样本；整体 family 表现继续由现有 allocator 校准使用。
- `doctor` 新增 demo/paper notional cap 为零的 blocker，防止“系统健康但每笔 proposal 必拒”的假象。

验证：`719 passed, 7 skipped, 6 xfailed`（相关 specs、机会、universe、doctor、filter 组合）；
`npm run build` 通过。

边界：不改 risk 限额、live、凭证或下单语义；只把现有证据链和诊断说清楚。

---

## 2026-07-10 — Position cap follows cached available balance

- Risk check #5 keeps max_demo_position_notional_usdt=$200 as the default hard
  ceiling/fallback. For exchange-backed demo orders, the effective cap is the lower
  of that value and the matching demo account's cached available USDT.
- Live orders use the same conservative calculation against the live account, while
  retaining the separate live ceiling and all existing live-unlock controls.
- paper_internal has no external account and keeps the configured cap directly.
  Risk evaluation reads only the local exchange-sync cache and never adds an exchange
  request to the proposal path.
- Settings now labels these fields as position hard ceilings and explains the balance
  behavior. Added RE-B18 coverage for demo/live separation, local-paper fallback, and
  the final rejection path. Validation: focused risk tests and all specs passed; the
  frontend production build passed.

---

## 2026-07-10 — Filter Feedback Controller Phase 1（shadow / observe-only，分支 feat/filter-feedback-controller）

**背景**：`ato opportunity audit` 显示 72h 漏斗 523 机会创建 → 仅 2 个开仓（0.38% 通过率）。每个流失点都是一个用固定阈值筛选机会的 filter，没人根据结果回调。目标：把整条机会管道变成负反馈控制系统——丢掉了本该盈利机会的 filter 应放松，放过了亏损机会的 filter 应收紧。

**关键发现**：这个模式在代码里已有三个局部实现，本期是**统一泛化**而非另起炉灶：
- `allocator.get_family_multipliers()`（生产在跑）——按 scanner_family 用前向收益 hit-rate 做有界校准，但只作用于评分乘子
- `operator_loop.PARAM_REGISTRY`——有界 config 调参（neutral/floor/ceiling/step/cooldown/audit/热重载），但只 3 个全局参数、由账户整体健康驱动
- `factor_feedback`——按 factor 分类 watch→blocked→active→preferred，但作用于 factor 非 filter

**本期交付（Phase 1，纯观测，零交易行为改动）**：
- `filter_registry.py`——10 个 FilterSpec 声明式清单（逐条对源码核实），标注每个 filter 的 drop_reason、config 路径/硬编码位置、松紧方向、反馈信号源、是否可调
- `filter_controller.py`——`compute_filter_feedback()` 按 (filter × scanner_family) 用 `calibration_stats` 前向收益 hit-rate 映射 loosen/hold/tighten/insufficient_data 判据（有界 + 样本门控，泛化自 get_family_multipliers）；`run_controller_observe()` 追加写 `controller_recommendations`（status='shadow_only'），**永不写 config**
- `controller_recommendations` 迁移（append-only）；`ControllerConfig` + Web 设置 UI + `/api/analytics/filter-controller` 只读端点 + `ato opportunity audit --view controller`
- 每 6h 的 `filter_controller_observe` 调度 job（read-only，受 `controller.shadow_enabled` 门控）
- `tests/filter_eval/` 新增 9 个测试，含"shadow 模式绝不改 config"不变量断言

**路线图（本期未做，已文档化）**：Phase 2 有界自动调参（3 档审批 + 抽屉回滚 + 抖动退避，先从 scanner_weights/family_quota 起）；Phase 3 变更受控风控参数（人工门控）；补 shadow-trade PnL 校准层 + 因果/反事实分析（owner 要求）；补 observer 预过滤 `pre_filter_rejections` 观测缺口。

### 验证
- `tests/filter_eval/` 27 passed（+1 预存的日期敏感 flake `signal_runaway_extreme` 与本期无关：direction_brake 场景硬编码 2026-07-09 时间戳，24h 窗口相对 now 已过期）；config/settings 131 passed；前端 `tsc --noEmit` 通过；CLI + 设置 API 端到端手测通过。

### 边界
新增分析模块 + 一个只读调度 job + 一个 config 子节 + UI；不改 ranker/debate/risk/execution 决策路径；observe job 只 append 到新表，不碰 config.yaml。commit 单条于 feat 分支，未推送、未合并、未重启 gateway。

---

## 2026-07-10 — 运行时行为问题批次修复 IB-24-07～13（6 issues / 5 commits）

本次修复的 6 个问题均为**静默劣化**类——系统表面正常运行，但核心决策质量、数据采集、结算统计在后台持续退化。详见 `docs/issues/RUNTIME_BEHAVIOR_ISSUES.md` 及 `docs/_ai/RUNTIME_BUG_LESSONS_2026-07-10.md`。

- **IB-24-07 FTS 知识库过滤 bug**（`4bc3707`）：`_ALLOWED_CONFIDENCE = "high_confidence"` 字符串被 `not in` 当字符检查而非集合成员检查，导致 1,405 条 medium_confidence 知识条目完全排除在 FTS 索引外。改为元组后 89→1,507 条；配合从 `aletheia_data.zip` 恢复 515 条旧机器缺失文件，最终达 **2,036 条**。
- **IB-24-11 debate 信号提示方向偏置**（`a747f19`）：`_build_bull_prompt` / `_build_bear_prompt` 硬编码 LONG 视角信号解读（FR<-0.01%=看多等），评估 SHORT 时 LLM 收到误导性 note，系统性低估 SHORT 机会。改为按 `direction` 参数切换信号语义。公式 `advantage = bull - bear` 不变（prompts 已方向感知）。
- **IB-24-12 neutral standalone opp 浪费管道容量**（`96f6275`）：`market_observer.py` 独立路径创建 `direction='neutral'` 的 opp 进入数据库，ranker 100% 丢弃。加早期拦截直接返回。
- **IB-24-13 expire_stale_opportunities 忽略 valid_until**（`96f6275`）：UPDATE 只检查 `created_at < 48h`，动量机会（TTL=30min）实际 open 状态持续 48h，造成漏斗统计失真。加 `OR (valid_until IS NOT NULL AND valid_until < now())` 条件。
- **IB-24-08 signal_settlement 单次 500 行永远追不上积压**（`6729aa13`）：`settle_due()` 单次 limit=500，每 15min 调用一次，12,827 行积压理论需 6.5h 清完。改为循环批次，单次 100s 时间预算内消费到队列清空，max_rows=2000。
- **IB-24-09 volume_scanner 被 Phase B 预算饥饿**（`6729aa13`）：Phase B 共享 40s 预算，CoinGecko+macro 先跑耗尽后 volume_scanner 静默跳过，停运 2.6 天。调整为 volume_scanner 在 Phase B 最前执行。

### 验证
- `tests/filter_eval/` 新增 19 个参数化测试（debate_direction × 5 + direction_brake × 9 + 其他 5）全部通过。
- FTS reindex 后 2,036 条索引，无报错。

---

## 2026-07-09 — 市场类型数据模型子批 B12 + 合并 master（4 findings / 6 commits）

- **同名币现货/合约混淆一组问题**（bug 审查台账 #9/#13/#14/#102）：`market_snapshots` 本来就有 `market_type` 列（推迟理由过时，无需迁移）。`list_market_snapshots` 加可选 `market_type` 过滤；`latest_snapshot(symbol, market_type)` 优先按类型精确匹配、无则退回按符号（#13，触风控信任边界，配 PL-MD-06/07）；`_build_dynamic_instruments` 按标的/持仓自身 market_type 采集（#14）；`_make_candidate` 带上 market_type（#9）；Serenity 待决信号从源机会补回 market_type（#102，不用给 pi_decisions 加列）。
- **#10 判为不修**：spec OD-S01（提交 `db3c3f8`）明确方向分析与合约/现货无关、每个符号只出一个标的，按符号去重符合设计；#11/#68/#106 确认 `034d4c8` 已修。
- **顺带修一个预存的假绿测试**：`test_broker_averaging_and_trail_fixes.py` 的 order-dependent flake（凭证桩打错命名空间 + okx demo-symbol 缓存未重置），在合并前的 `1f9ec16d` 上也复现，确认非本批引入。
- **合并 master**：本分支落后 master 26 提交，merge 解决 4 冲突（1 代码 opportunity_flow.py 按读模型纯度保留直查 DB + 采用 master 常量；3 文档取并集）。

### 验证
- 全量 `tests/` → 2477 passed / 0 failed / 10 skipped / 6 xfailed。

### 边界
只碰风控数据新鲜度辅助函数一处信任边界（配回归）；无 schema/迁移；不改 live/凭证/下单路径；不重启 gateway。

---

## 2026-07-08 — 尾扫延续：prune_system_actions 审计豁免 + gateway API 错误脱敏（2 findings / 2 commits）

- **#44 prune_system_actions 硬删破 no-hard-delete 规则**：知识条目按规矩不 hard-delete，但 `system_action` 是 ~1200 rows/day 的纯 telemetry 噪声，保留反而淹没审计路径。保持 hard delete，但 docstring 显式声明这是 knowledge_items 里**唯一记录的例外**，并在实际删除时 emit `knowledge_items_pruned` audit event 记 deleted_count + cutoff_days —— 保住问责链条不至于失踪。→ `6b9cc40`
- **#8 gateway API 错误响应泄漏 str(e)**：`execution/gateway.py` 三处 `return {..., "message": str(e)}`（demo / live / outer catch）直接把 raw CCXT/requests 异常文本抛给 API，可能带请求 URL / 部分头 / 栈碎片。新增 `_safe_error_message()` 转 `broker_utils.clean_error_message`（已知处理 OKX/Bitget/Binance JSON 形状）+ 截 200 字符。审计写入依然带原始信息，只切 API 表面。→ `1ac2247`

### 验证
`tests/specs/` 554 passed / 0 failed 两条都跑过。

### 边界
仅 storage/repositories.py + execution/gateway.py 各一个函数；未推 / 未 restart。

---

## 2026-07-07 — 尾扫多点：research_worker docstring / /status head_commit 缓存 / SpecClaim 验证（3 findings / 3 commits）

- **#22 research_worker docstring**：`is_serenity_tradable_symbol` docstring 说 stock tokens 是 `asset_class="equity"` observation-only，实际代码 line 769 打的是 `asset_class="equity_tradeable"`（可执行）。改写 docstring 列清两个 class 的区别 + `trading_decision.py:1321` 是分流点。运行时未动。→ `23b3cd2`
- **#6 /api/status 阻塞 git subprocess**：`current_commit()` shell 出去跑 `git rev-parse HEAD`，topbar 每 10s 一 poll，subprocess 30-500ms 阻塞 async loop。加 `_cached_head_commit(ttl=60s)` module-level 缓存（thread-lock，non-raising），HEAD 只在重启/自更新时变，60s 缓存安全。→ `a1869e1`
- **#74 SpecClaim.status 未运行时校验 + 缺 "superseded"**：Literal 只列 implemented/partial/missing，但 spec_pi_decision_chain.py:126 用了 `status="superseded"` —— Literal 只是 type hint 不 enforce，运行时接受了但矩阵渲染 `?`。三件事：Literal 加 "superseded"；SpecClaim `__post_init__` 校验 status ∈ 合法集合（拼写错立即抛）；test_spec_conformance icon map 加 `superseded → s`。→ `37238a1`

### 验证
`tests/specs/` + test_spec_conformance = 559 passed / 0 failed。

### 剩余
- **B8 剩少量**（大件 #163 Settings coverage matrix、#122/#123 CausalChain 源 都需要更多 UI 验证 / backend 改动）；已 deferred 项不变。

### 边界
所有改动在测试树 + 服务器 route + docstring；未推 / 未 restart / 未 enable live。

---

## 2026-07-07 — B8-cc-confirm：Send 按 requires_confirmation 走 window.confirm（1 finding / 1 commit）

### 触发
接审计链修复后 owner「继续推进」。#71 Command Center Send 按钮无视 "confirmation" badge 直接触发 —— 与 backend #67 修复搭配不当（后端要求 confirm=true 参数才执行 confirmation-required 命令，前端不 confirm 就得连点两次）。

### 做法
- Send onClick 里加 `if (commandPreview?.command?.requires_confirmation)` 分支，未确认返回；确认后走 `commandMut.mutate()`。同 pattern 与既有 runtime buttons（Promote/Drain/Rollback）。

### 交付
- 1 finding。1 提交（CommandCenter.tsx Send handler + Fix Progress 表）。
- **验证**：Python `tests/specs/` 554 passed / 0 failed。

### 剩余
- **B8 剩 ~5 finding**（大部分是复杂的 CausalChain 源问题 #122/#123，或需 audit 每字段的 Settings coverage matrix #163；已 deferred 项不变）。

### 边界
仅一个前端文件；未推 / 未 restart。

---

## 2026-07-07 — 审计链头截断修复（1 finding / 1 commit）

### 触发
接 B8-risk-mixed 后 owner「继续推进」。挑一条安全性 finding —— #43 audit_chain 头截断被误判为 benign。

### 做法
- **#43 verify_audit_chain 首行 mismatch 无条件 benign**：旧代码 `if is_first_in_file: benign = True` —— **任何**文件的首行 _prev_hash mismatch 都被当成 rotation-boundary reanchor。攻击者（或误操作 `head -c` / `truncate`）把**最老**的 audit 文件头切掉后，verifier 静默 reanchor，头部记录被静悄悄丢弃。修改 for 循环用 `enumerate`，标记 `is_oldest_file = (path_index == 0)`，条件收紧到 `is_first_in_file and not is_oldest_file` —— 最老文件的首行必须从 `_GENESIS_HASH` chain 起，否则视为真错。
- **新增回归** `tests/test_audit_chain_head_truncation_fix.py`（2 tests / 2 passed）：
  - `test_head_truncation_of_oldest_file_now_reads_as_error` — 种 3 条链、删首行、断言 `valid=False`、`errors` 非空、`reanchors=0`。
  - `test_legitimate_rotation_still_reanchors_benignly` — 手动构造 `audit.jsonl.1` + 一条从 GENESIS 起链的 fresh audit.jsonl，断言 `reanchors=1`（合法 rotation 边界不应回归成 error）。

### 交付
- 1 finding（safety-critical）。1 提交（jsonl.py + 焦点回归）。
- **验证**：`tests/specs/` + audit 回归 = 556 passed / 0 failed。

### 剩余
- **B7 / B9 / B10** 保持完成状态；**B8** 剩 ~6 finding（CausalChain 源、Settings coverage matrix、几处未处理的 tail）。
- 已 deferred 项不变。

### 边界
仅 `aletheia/files/jsonl.py` + 新回归测试；未推 / 未 restart / 未 enable live / 未触 credentials。这是安全类修复，`write_audit` 路径完全没改（`use_cache=False` 已经防了缓存漂移）；只堵住 verifier 的 sensor 盲点。

---

## 2026-07-07 — B8-risk-mixed：RiskControls 同名 spot+perp 可见性（2 findings / 1 commit）

### 触发
接 B8-charts-pred 后 owner「继续推进」。挑一条 RiskControls 显示层的信号丢失。

### 做法
- **#47/#53 marketTypeBySymbol 覆盖**：`marketTypeBySymbol[p.symbol] = p.market_type` 循环里，同 symbol 的 spot + perp 互相覆盖。改成 `Record<symbol, Set<market_type>>` 收集全部；显示用 `_preferPerp(set)` 选出 derivative 类型（perp / usdt_futures / futures / swap 优先，spot 兜底），保证渲染一致（不会随 positions 更新顺序摆动）。加 `hasMixedMarketType(sym)` 判定 —— 同 symbol 同时有 spot + perp/futures 时在 exposure 行旁边显示「(spot+perp)」小灰字 + title「Both spot and perp open — exposure is combined」，告诉操作员这条 exposure 是**合并的**（backend `exposure_by_symbol` 键只有 symbol，`#52` deferred 与 market_type 迁移耦合暂无法拆）—— 前端只做显示诚实，不能修 backend 的合并。

### 交付
- 2 findings。1 提交（RiskControls.tsx + Fix Progress 表）。
- 验证：Python `tests/specs/` 554 passed / 0 failed。

### 剩余
- **B8 剩 ~6 finding**（session B8 累计 29）：CausalChain fill/position 源不对 (#122/#123)、Settings coverage matrix 18 字段错标 (#163)、几处零星（Overview scheduler，AgentActivity strategies_designer，SettingsBanner setup 等已修）+ 已 deferred 与 market_type 迁移耦合的 (#52/#57/#96)。
- 已 deferred 项不变。

### 边界
仅 1 前端文件（RiskControls.tsx）；未推 / 未 restart / 未 enable live / 未跑 npm。

---

## 2026-07-07 — B8-charts-pred：PredictionsTab 类型化 + market_type fallback 顺序（2 findings / 1 commit）

### 触发
接 B8-approve-honesty 后 owner「继续推进」。Charts 页两处诚实性 gap：预测卡读错字段、fmtSymbol fallback 顺序错。

### 做法
1. **#169 PredictionsTab 读不存在字段**：卡里读 `m.question` + `m.volume_usd`，但 `PredictionMarket` 类型是 `event_title` + `volume_24h`（`types.ts:159/162`）—— 两个字段永远 undefined，问题行空白、volume 整行被条件隐藏。改用类型化 `useQuery<PredictionMarket[]>`（丢掉 as any 逃生舱），读 `m.event_title` + `m.volume_24h`。未来 rename 时 tsc 会红。
2. **#170 fmtSymbol 用 board asset-class**：`mtFor = marketType ?? snap.market_type`（`??` 语义是「marketType 有值就用」）—— 当 board 传来 `crypto`（asset-class，不是 spot/perp），perp snapshot 的 market_type 被丢，fmtSymbol 收到 'crypto' → 非 perp 判定 → 带斜杠渲染。反转优先级：`snap.market_type || marketType`—— per-snapshot 权威，board 只当 snap 缺失时兜底。同时改 symbol-chip 那一处的 fmtSymbol 调用同样反转。

### 交付
- 2 findings。1 提交（Charts.tsx）。
- **验证**：Python `tests/specs/` 554 passed / 0 failed；改动在 React 层 + TS 类型收紧。`tsc` 未跑。

### 剩余
- **B8 剩 ~7 finding**（session B8 累计 27）：#122/#123 CausalChain fill/position 源不对（复杂 —— 需 fills 表 route）、#163 Settings coverage matrix 18 字段错标（大 —— 需 audit 每字段）、#96 Markets candlestick market_type forwarding（已 deferred 与 market_type 迁移耦合），加零星几条。
- 已 deferred 项不变。

### 边界
仅 Charts.tsx（+ 一条类型 import）；未推 / 未 restart / 未 enable live / 未跑 npm。

---

## 2026-07-07 — B8-approve-honesty + SetupBanner 覆盖（3 findings / 1 commit）

### 触发
接 B8-chart-follow-up 后 owner「继续推进」。Proposals 审批路径的两个诚实性 gap（#113/#114）+ 首屏 SetupBanner 只认部分 provider（#121）。

### 做法
1. **#113 approve 200-with-error 静默 reset**：backend `/approve-paper` 对成功和「approved 但 execution 时挂了」都返 HTTP 200，只是 body 里 `execution.status='error'`。前端 approve mutation onSuccess 无脑 `clearErr(id)` —— 错误被清，操作员以为 order 落了。改成检查 `r.execution.status`，`error`/`rejected` → 走 `setErr` 保留 inline 错误。
2. **#114 approve confirm 显示 proposal.execution_mode 骗人**：dialog 说「Mode: bitget_live_limited」或「Mode: okx_demo」（proposal 上带的），但 backend 硬编码 `execution_mode="paper_internal"`（data.py:642）。 现在 dialog 显示强制的 `paper_internal (paper simulation — no real order)`；若 proposal 的 stored mode 与之不同，加一句「the proposal was tagged '...', but the /approve-paper route routes every approval through paper_internal. Use the live-unlock flow to place a real order.」诚实告诉操作员该按钮实际做什么。
3. **#121 SetupBanner 只认 Bitget-demo + api_key**：`anyModelKey = providers.some(p => p?.api_key?.configured)` 忽略 OAuth / CLI；`anyDemoCreds = creds.demo?.api_key?.configured` 只看 Bitget。OAuth-Claude + OKX demo 用户永远看到 setup 提示。加 OKX + Binance credential 查询，模型侧检查 api_key OR oauth OR cli-configured 任一存在，交易所侧任一（bitget_demo / okx_demo / binance_testnet）有 api_key 就算就绪。banner 文案改成「add a model endpoint (API key / OAuth / CLI) and at least one exchange's demo/testnet credentials」。

### 交付
- 3 findings。1 提交（Proposals + SetupBanner）。
- **验证**：Python `tests/specs/` 554 passed / 0 failed；改动全在 React 层。`tsc` 未跑。

### 剩余
- **B8 剩 ~9 finding**（session B8 累计 25：cleanup 4 + mini 3 + honesty 3 + topbar 2 + quickies 3 + safety 2 + chart-nav 2 + chart-follow-up 3 + approve-honesty 3）。较大还剩：Settings coverage matrix mislabel 18 字段 (#163)、CausalChain fill/position 源不对 (#122/#123)、Charts prediction fields 错 (#169/#170)、Markets candlestick market-type forwarding (#96 — 与 market_type 迁移耦合，暂缓)。
- 已 deferred 项不变。

### 边界
仅 2 前端文件；未推 / 未 restart / 未 enable live / 未触 credentials / 未跑 npm。

---

## 2026-07-07 — B8-chart-follow-up + 对账 + Palette Enter（3 findings / 1 commit）

### 触发
接 B8-chart-nav 后 owner「继续推进」。补上 #116 依赖的 Charts.tsx 侧读 searchParams，再顺手挑 #117 (ReconciliationCard 零漂移隐藏 stale) + #126 (CommandPalette Enter 劫持) —— 两条独立小改。

### 做法
1. **#116 后续（Charts.tsx 读 searchParams）**：加 `useSearchParams` import + `useEffect` 保持 URL ↔ state 同步。`symbol` state 初值从 `?symbol=`；`marketType` state 初值从 `?market_type=`（默认 'spot'）。ChartPanel 的 `marketType` prop 现在 URL 优先（deep link 的市场类型不能被父 board 的 asset-class 覆盖），否则回退 boardMarketType。View-on-Chart 现在真正在期望的 instrument 上落地。
2. **#117 ReconciliationCard 零漂移时隐藏 stale/error**：之前 `if (external.length===0 && orphaned.length===0) return null` —— 当 syncStatus='stale'/'error'/'unconfigured' 而 drift 恰为零时，整张 Reconciliation 卡不显示。用户不知道对账压根没跑或数据过期。加 `isDegraded = syncStatus && !['ok','unconfigured'].includes(syncStatus)`；drift=0 但 degraded 时仍渲染卡 + 显示 `sync <status>` badge + 解释文字「'no drift' 结论可能基于过时/缺失数据」。
3. **#126 CommandPalette Enter 劫持**：无脑 `if (e.key==='Enter' && input.trim())` navigate 到 command-center —— 3 字符搜索匹配到建议时也劫持，cmdk 无法用 Enter 选建议。加长度门槛 `input.length >= 20`（呼应输入框 placeholder 的「20+ chars for AI」提示），短输入落回 cmdk 走建议选择。

### 交付
- 3 findings。1 提交（Charts + TradesPositions + CommandPalette）。
- **验证**：Python `tests/specs/` 554 passed / 0 failed；改动全在 React 层，不涉及 Python spec 覆盖。`tsc` 未跑。

### 剩余
- **B8 剩 ~12 finding**（session B8 累计 22：cleanup 4 + mini 3 + honesty 3 + topbar 2 + quickies 3 + safety 2 + chart-nav 2 + chart-follow-up/reconcile/palette 3）。大件仍在：Settings coverage matrix mislabel (#163)、CausalChain fill/position 源不对 (#122/#123)、Charts prediction fields 错 (#169/#170)、Proposals approve/confirm 骗人 (#113/#114)、SetupBanner 只 Bitget (#121)、Charts fmtSymbol 用 board asset-class (#170)、PredictionMarket 类型丢失 (#169)。
- 已 deferred 项不变。

### 边界
仅 3 前端文件；未推 / 未 restart / 未 enable live / 未触 credentials / 未跑 npm。

---

## 2026-07-07 — B8-chart-nav：PriceChart marketType + View-on-Chart 带 symbol 跳转（2 findings / 1 commit）

### 触发
接 B8-safety 后 owner「继续推进」。挑两条 chart 页相关的诚实性小改。

### 做法
1. **#115 PriceChart 没 marketType prop**：`<PriceChart symbol="BTC/USDT" ... />` 内部三次 `api.getOhlcv(...)` 都不传 marketType，走 client 签名的默认 'spot' —— 一个 perp instrument 的图表 quietly 拉 spot 蜡烛，价格偏离真实 perp mark。加 `marketType` prop（默认 'spot' 保 backwards compat），三处 getOhlcv 调用都透传。
2. **#116 TradesPositions View on Chart 无 symbol**：Order 抽屉里 `onClick={() => navigate('/charts')}` 不带任何参数，Charts 页永远初始化到默认 BTC/USDT —— 用户要手动重新选 order 的真实 symbol。改成 `navigate({ pathname: '/charts', search: '?symbol=<order.symbol>&market_type=<...>' })`。Charts 页需要在 mount 时读 searchParams（大多数 chart 页都做这个，如没有则是下一批的工作）。

### 交付
- 2 findings。1 提交（PriceChart + TradesPositions）。
- **验证**：Python `tests/specs/` 554 passed / 0 failed；改动全在 React 层。`tsc` 未跑。

### 剩余
- **B8 剩 ~15 finding**（session B8 累计 19：cleanup 4 + mini 3 + honesty 3 + topbar 2 + quickies 3 + safety 2 + chart-nav 2）。较大：Settings coverage matrix mislabel (#163)、CausalChain fill/position 源不对 (#122/#123)、Charts prediction fields 错 (#169/#170)、Proposals approve/confirm 骗人 (#113/#114)、SetupBanner 只 Bitget (#121)、CommandPalette Enter 劫持 (#126)、ReconciliationCard 零漂移隐藏 (#117)、Charts.tsx searchParams 未读 (Charts 需要接收 #116 传的 symbol/market_type)。
- 已 deferred 项不变。

### 边界
仅 2 前端文件；未推 / 未 restart / 未 enable live / 未触 credentials / 未跑 npm。**依赖 follow-up**：Charts.tsx 需在 mount 时读 `searchParams.get('symbol')` + `.get('market_type')` 并作为初值 —— 本批未改 Charts.tsx（大文件、需 UI 测试），下批做。

---

## 2026-07-07 — B8-safety：Settings 保存 onError 横幅 + Serenity assessed_tier（2 findings / 1 commit）

### 触发
接 B8-quickies 后 owner「继续推进」。#164 是 Settings 里一大堆 mutation 缺 onError 的诚实性 gap；#167 是 SerenityLens 显示的 tier 用错来源（advisory 而非 authoritative）。

### 做法
1. **#164 Settings AgentModelsTab 9 个 mutation 缺 onError**：`permMut` / `autoLearnMut` / `bypassDistressMut` / `runtimeMut` / `operatorMut` / `piDaemonMut` / `serenityMut` / `guardMut` / `regimeMut` 都只有 onSuccess。后端 422/403/500 时前端 silent，toggle 视觉「切换成功」但配置从未落库。加一个 tab-局部的 `saveErr` state 与 `onSaveError(label)` 工厂 —— 每个 mutation 加 `onError: onSaveError('Permission mode')` 之类，成功时清空。Tab top 加一条红色 banner「Save failed — <label>: <message>」，用户不再面对沉默失败。
2. **#167 SerenityLens Opportunity 表 Tier 列**：显示 `humanize(o.evidence_tier)` —— 那是**提交时的 proposed**（advisory only）；risk engine 实际用的是 `assessed_tier`（serenity 证据管道打分后落库）。改成 `humanize(o.assessed_tier || o.evidence_tier)` —— assessed 优先；管道尚未打分才回退 proposed。用户看到的 Tier 现在与 risk engine 用的一致。

### 交付
- 2 findings。1 提交（Settings.tsx + SerenityLens.tsx）。
- **验证**：Python `tests/specs/` 554 passed / 0 failed；改动都在 React 层，不涉及 Python spec 覆盖。`tsc` 未跑。

### 剩余
- **B8 剩 ~17 finding**（session B8 累计 17：cleanup 4 + mini 3 + honesty 3 + topbar 2 + quickies 3 + safety 2）。较大的：CausalChain fill/position 源不对 (#122/#123)、Settings coverage matrix mislabel (#163)、Charts prediction fields 错 (#169/#170)、Proposals approve/confirm 骗人 (#113/#114)、TradesPositions 无 symbol (#116)、ReconciliationCard 零漂移隐藏状态 (#117)、SetupBanner 只 Bitget (#121)、CommandPalette Enter 劫持 (#126)、PriceChart 无 marketType prop (#115)。
- 已 deferred 项不变。

### 边界
仅 2 前端文件（Settings 局部 tab + SerenityLens 单行）；未推 / 未 restart / 未 enable live / 未触 credentials / 未跑 npm。#164 只覆盖了 AgentModelsTab 9 个 mutation，其他 tab（外汇/网络/记账/风控）的 save mutation 还需类似处理，留下批。

---

## 2026-07-07 — B8-quickies：CommandPalette /universe + Scheduler 真状态 + AgentFeed 失败集（3 findings / 1 commit）

### 触发
接 B8-topbar 后 owner「继续推进」。三条前端诚实性小改，都是「路由/状态可见但用错源/漏项」。

### 做法
1. **#125 CommandPalette 缺 `/universe`**：`nav-universe` 条目从 NAV bar 一直有但 palette 没挂 —— keyboard shortcut 用户无法跳到 Universe Board。加一行 nav entry（keywords 含 universe/scanner/instruments）。
2. **#118 Overview Scheduler 灯只看 paused**：`light={paused ? 'yellow' : 'green'}` 忽略 `scheduler_running` / `scheduler_state`（/status 明确返回这两）—— scheduler 实际停了（比如崩了/apscheduler shutdown 后）仍显示绿。改成读 `s?.scheduler_running` (bool)，`paused=true → yellow`, `running=true → green`, 其余 → `red`；label 显示 `scheduler_state`。
3. **#120 AgentFeed 只染 `status=='error'` 红**：`failed`/`timed_out`/`stale`/`completed_with_errors`/`rejected` 五种失败态都走 accent-blue（feed icon）+ yellow（badge），被当「进行中」显示，实际是失败。抽 `FAILURE_STATUSES` set，两处（feed-icon color + badge className）都用它 —— 图标 + badge 一致告诉用户「这条真挂了」。

### 交付
- 3 findings。1 提交（CommandPalette + Overview + AgentFeed）。
- **验证**：Python `tests/specs/` 554 passed / 0 failed；改动全在 React 状态映射层。`tsc` 未跑。

### 剩余
- **B8 剩 ~19 finding**（session B8 累计 15：cleanup 4 + mini 3 + honesty 3 + topbar 2 + quickies 3）。
- 已 deferred 项不变。

### 边界
仅 3 前端文件；未推 / 未 restart / 未 enable live / 未触 credentials / 未跑 npm。

---

## 2026-07-07 — B8-topbar：Overview 交易所探针分派 + 顶栏 risk-status 查询（2 findings / 1 commit）

### 触发
接 B8-honesty 后 owner「继续推进」。两条影响 dashboard 首屏状态的诚实性 gap。

### 做法
1. **#171 Overview 交易所灯永远探 Bitget**：`Overview.tsx:31` 无脑 `api.testBitget(exchangeMode)`，OKX/Binance 部署看到的是**过时**的 Bitget 探针 —— 没 bitget creds 会红（假警报），bitget 侥幸活着而真交易所挂了会绿（假 OK）。改成按 `status.exchange`（值为 `okx`/`binance`/`bitget`）选择 `api.testOkx` / `api.testBinance` / `api.testBitget`；三个 client 方法本来就在。
2. **#174 顶栏 riskState 永远为空**：`App.tsx::StatusBar` 读 `data?.risk_status || data?.risk?.status || ''`，但 `/api/status` 从不返回 risk 字段 —— riskState 永远 `""`。deriveMasterState 里 CAUTION / OPERATIONAL 分支永远不激活，健康态误显 STANDBY-绿。加 `useQuery({ queryFn: api.getRiskStatus, refetchInterval: 15000 })`，读 `risk.status` 首选，`/status` 里的 legacy 兼容值兜底。这样与 `RiskControls.tsx:83-86` 用同一数据源，Overview 说 yellow / Risk 说 blue 的旧不一致也顺带消。

### 交付
- 2 findings。1 提交（`App.tsx` + `Overview.tsx`）。
- **验证**：Python `tests/specs/` 554 passed / 0 failed；改动全在 React query 层 + StatusBar 计算式，不涉及 Python spec 覆盖。`tsc` 未跑（无 node_modules），full UI check tracked。

### 剩余
- **B8 剩 ~22 finding**（session B8 累计已修 12：cleanup 4 + mini 3 + honesty 3 + topbar 2）。较大的：CausalChain fill/position 源不对 (#122/#123)、Settings coverage matrix mislabel 18 字段 (#163)、Charts prediction 读不存在字段 (#169/#170)、Proposals approve 忽略 execution.status (#113)、Proposals confirm demo/live 承诺骗人 (#114)、TradesPositions View on Chart 无 symbol (#116)、ReconciliationCard 零漂移时隐藏 stale/error 状态 (#117)、Overview scheduler 只看 paused (#118)、AgentFeed 只染 status=='error' (#120)、SetupBanner 数只 Bitget (#121)、CommandPalette 缺 /universe (#125)、CommandPalette Enter 劫持 (#126)。都需 npm 验证再动，逐条挑机械/低风险的继续。
- 已 deferred 项不变。

### 边界
仅 2 前端文件；未推 / 未 restart / 未 enable live / 未触 credentials / 未跑 npm。

---

## 2026-07-07 — B8-honesty：pause/resume onError + guidance tone + risk-clear 门（3 findings / 1 commit）

### 触发
接 B8-mini 后 owner「继续推进」。B8 挑三条更有影响力的诚实性 gap —— 操作员会实际操作的按钮 / 状态标签。

### 做法
1. **#165 pause/resume/wake 吞 409**：三个 mutation 只有 `onSuccess` 无 `onError`，backend 返 409（LLM-unavailable / kill-switch-active）时前端 silent 「成功」，操作员以为 resume 生效了实际系统还在 paused。三个 mutation 都加 `onError: (e) => toast('error', ...)`，match `App.tsx` 里已存在的 extractResumeError 模式。
2. **#166 CommandCenter runtime guidance 死支**：`<StatusText tone={runtimeBadge(guidance?.action).includes('green') ? 'green' : ...}>` —— `runtimeBadge` 词汇是 **runtime state**（current/candidate/failed 等），guidance.action 是**动词**（promote/rollback/drain/observe 等），永远不匹配 → 永远走 badge-blue → tone 永远 yellow → promote 和 rollback 长得完全一样。新加 `guidanceTone(action)` 助手：promote/start_shadow/prepare_candidate=green（推进），rollback=red（回滚），drain/activate_restart=yellow（谨慎），observe/未知=muted。
3. **#172 RiskControls "within all limits" 误绿**：violation list 空时无脑显示「operating within all limits」+ 绿点 —— 包括 riskState='unknown'/missing 的**初始/没有数据**场景，把「没数据」当「一切正常」显示。加条件：只有 `riskState === 'healthy'` 才显示 clear + 绿；否则显示 `Risk state <state> — not confirming clear until the risk engine reports "healthy"` + 灰点。

### 交付
- 3 findings。1 提交（CommandCenter + RiskControls）。
- **验证**：Python `tests/specs/` 554 passed / 0 failed；改动全在 React 组件层，不涉及 Python spec 覆盖。`tsc` 未跑。

### 剩余
- **B8 剩 ~24 finding**（本 session B8 累计已修 10：cleanup 4 + mini 3 + honesty 3）。较大的还剩 topbar riskState 死支 (#174)、Overview exchange 灯永远 Bitget (#171)、CausalChain fill/position 源不对 (#122/#123)、Settings coverage matrix mislabel (#163)、Charts prediction 读不存在字段 (#169/#170) 等。
- 已 deferred 项不变。

### 边界
仅 2 前端文件（`CommandCenter.tsx` + `RiskControls.tsx`）；未推 / 未 restart / 未 enable live / 未触 credentials / 未跑 npm。

---

## 2026-07-07 — B8-mini：预测偏好 PATCH + NewsIntel analyzed 桶 + 时间线加日期（3 findings / 1 commit）

### 触发
接 B8-cleanup 后 owner「继续推进」。B8 里挑另外 3 条单点小改，都是「client 用错方法 / filter 缺项 / 时间戳不带日期」这类清晰易修。

### 做法
1. **#86 prediction/preferences POST→PATCH**：backend 是 `@router.patch("/preferences")` (prediction.py:232)，client `updatePredictionPreferences` 却是 `post(...)` —— 保存偏好每次 405 Method Not Allowed。换成 `fetchJson(path, { method: 'PATCH', headers, body })`。
2. **#168 NewsIntel 缺 analyzed 过滤桶**：`filter` 只有 all/new/reviewed，auto-analysed 行 status='analyzed' 只能在 all 里看到 —— 用户不知道有哪些被自动分析过。filter row 加 `'analyzed'`，comprehension 加相应分支。
3. **#173 ActivityTimeline HH:MM 撒谎**：只渲染 HH:MM，3 天前的事件跟今天的看起来一样，误导 stale feed 看起来 current。同日事件保 HH:MM（避免噪音），跨日事件前置 "Nov 12" 形式的月日。

### 交付
- 3 findings。1 提交（3 前端文件）。
- **验证**：Python `tests/specs/` 554 passed / 0 failed（不受前端改动影响）。**tsc 未跑**（无 node_modules），full UI 验证 tracked。改动全是 text-level / 加 case 分支 / 加日期条件，无 hooks / 数据形状 / props 契约变化。

### 剩余
- **B8 剩 ~27 finding**（起初 ~35，本 session B8-cleanup 4 + B8-mini 3 = 7 处置）。较大宗未处理：pause/resume 吞 409 (#165)、topbar riskState 死支 (#174)、Overview exchange 灯永远探 Bitget (#171)、CausalChain fill 源不对 (#122/#123)、Settings coverage matrix 错标 18 字段 (#163)、多个 onError 缺失 (#164) 等。都需要 `npm run build` + 浏览器验证才能安全大改。
- 已 deferred 项不变。

### 边界
仅 3 前端文件（`client.ts` + `NewsIntel.tsx` + `ActivityTimeline.tsx`）；未推 / 未 restart / 未 enable live / 未触 credentials / 未跑 npm。

---

## 2026-07-07 — B8-cleanup：fmtSymbol 恢复 + AgentActivity 过时说明 + RuleSet legacy 标签（4 findings 逻辑修 + 1 已applied / 1 commit）

### 触发
接 B10-rename 后 owner「继续推进」。B7/B9/B10 全部收官，进入 B8 前端诚实性组 —— 挑几条最机械、不依赖 UI 运行时的清理，先把易改的先处理。

### 做法
1. **#15 + #175 fmtSymbol 恢复**：`TradeInsightsPanel.tsx:449` 的 `row.symbol.replace('/USDT', '')` + `PnLZone.tsx:173` 的 `sym.replace('/USDT', '')` 都是硬编码剥 USDT，不走 `fmtSymbol()` helper —— 非 USDT quote / perp 会显示错。TradeInsightsPanel 已经 import 了 fmtSymbol；PnLZone 需要加 import。改成 `fmtSymbol(row.symbol, (row as any).market_type)` / `fmtSymbol(sym)`。#175 是 #15 在 SymbolDirectionMatrix 的具体实例，同一次修覆盖。
2. **#95 已在过往会话应用**：`frontend/src/api/client.ts:543` 已有 `// #95: removed dead setStrategyVenue()` 注释，grep 全项目无残留调用点，已 resolved（不占本批工作量，登记）。
3. **#72 AgentActivity 「steps 后端未建」过时**：backend `/api/agent-runs/{run_id}/steps` 早在 `server/routes/data.py:236` 挂了，但 `frontend/src/api/client.ts` 从没加 `getAgentRunSteps` 方法，AgentActivity 抽屉里显示「pending backend — not yet available」。加 `api.getAgentRunSteps(runId)` 客户端方法 + AgentActivity `ReasoningStepsSection` 用 useQuery 拉步骤并渲染成表格（step_number/action/input/output 各 80 字符预览）；顶部模块 docstring 里的「backend gap」段改为「#72 fix：GET /steps 已 live」+ 「note was stale」。
4. **#128 TradeAnalyticsPanel `RuleSet` legacy 标签**：`By Strategy Source` 段列的 `ruleset` 是 Position C 已删规则引擎的产物 —— 只有 legacy 数据行才有。标签从 `RuleSet` 改成 `RuleSet (legacy)`，颜色从 `var(--accent)` 改为 `var(--muted)`（少抢注意力），并在文件 top docstring 明写「legacy attribution 仍展示历史行，新 trade 全是 opportunity」。

### 交付
- 4 finding 逻辑修 + 1 verified-already-done。1 提交。
- **验证**：Python `tests/specs/` + naming registry 共 557 passed / 0 failed（不受 frontend 改动影响）。**tsc 未跑**（本 worktree 未装 node_modules），full UI 验证 tracked as follow-up；本批只做 mechanical 文本+import 层的改动，不改任何组件的 hooks / 数据形状 / props 契约。

### 剩余
- **B8 剩 ~30 finding**：pause/resume 吞 409（#165）、topbar riskState 死支（#174）、Overview exchange 灯永远探 Bitget（#171）、CausalChain fill 从 order 而非 fills 表（#122/#123）、settings coverage matrix mislabel（#163）、多个 onError 缺失（#164）等等。都要看 UI 才能验证；建议在 owner 说「可跑 npm build」时集中做。
- **已 deferred 项** 不变。

### 边界
仅 4 个前端文件（`TradeInsightsPanel.tsx` + `PnLZone.tsx` + `client.ts` + `AgentActivity.tsx` + `TradeAnalyticsPanel.tsx`）；未推 / 未 restart / 未 enable live / 未触 credentials / 未跑 npm。

---

## 2026-07-07 — B10-rename：System Operator naming 注册 + naming-regression（1 finding / 1 commit）

### 触发
接 B10-docs-tail 后 owner「继续推进」。B10 剩最后一条 #97 —— `discussion.md` 已经把「PI Agent」重命名为「System Operator」，但 `pi_daemon` 名字仍散落 config schema / 路由 / 审计事件 / 前端。全项目 rename 成本大，采用 ledger 的「at minimum: 记为 legacy 兼容键 + naming-regression test」路径。

### 做法
1. **配置层单点真相**：`config/schema.py::PiDaemonConfig` 加详细 class docstring 建立 **naming registry** —— 明写 `pi_daemon` = legacy 兼容键、当前产品名 = System Operator daemon，列出 4 处 legacy 契约（field / audit event / REST path / product name）。
2. **路由 docstring 加 legacy 标记**：`server/routes/settings.py::update_pi_daemon_config` 加 docstring 说 `/pi_daemon` 路径 + `pi_daemon_config_changed` 事件 + `pi_daemon_config` entity 都是 legacy 兼容名。audit message 里的文本从 "PI daemon config updated" 改为 "System Operator daemon config updated"（用户/操作员可见的那部分改到当前语言，机器契约保 legacy）。
3. **前端 UI 可见 label 已在 System Operator daemon**：Settings.tsx:553 已是 `System Operator daemon (event-driven)` —— 本批只加 regression 锁住不回归。`pi daemon` 作为搜索别名（line 69）保留，用户肌肉记忆。
4. **naming-regression 测试**（`tests/test_system_operator_naming_registry.py`，3 tests）：
   - 断言 `PiDaemonConfig` docstring 包含 "System Operator daemon" + "legacy" 字样 + 4 处契约 key —— docstring 是 registry，丢了这段本测试会报。
   - 断言 `PiDaemonConfig` 4 个字段（enabled/debounce_sec/cooldown_sec/heartbeat_sec）都还在 —— 防止 silent config break。
   - 断言 `Settings.tsx` 含 `System Operator daemon` **且不含** `PI Agent daemon` / `PI daemon (event-driven)` 之类的 visible label —— 防新代码回退旧命名。

### 交付
- 1 finding。1 提交（config/schema + settings 路由 + 新回归 test）。
- **验证**：`tests/specs/` 557 passed / 0 failed（相较 baseline 554 多 3 —— 新 test 3 条 all green）。
- 未做完整 rename（会破 existing config.yaml + audit trail），改用 ledger 明写建议的 minimum：登记 + 锁住 —— 「full rename (config key + REST alias) deferred as bigger migration」记在 Fix Progress。

### 剩余
- **B10 全部处置**（除 deferred full rename）。至此 **B7 / B9 / B10 均完成**。
- **B8** 前端诚实性仍留（最大一组，约 35 findings，需 `npm run build` 验证）。
- 已 deferred：market_type migration / operator 鉴权 / #109/#143/#102-pending/#17 / 运行时性能 / #154/#155/#156 (B9 尾) / #97 完整 rename。

### 边界
仅 3 文件 + 焦点回归；未推 / 未 restart / 未 enable live / 未触 credentials。未改任何 config.yaml 消费者接口（既有 `cfg.pi_daemon.enabled` 等仍 work）。

---

## 2026-07-07 — B10-serenity-plan：thesis-invalidation marker doc reconciled（1 finding / 1 commit / 纯 docs）

### 触发
接 B10-docs-tail 后 owner「继续推进」。B10 快扫尾一条 #104。

### 做法
`docs/plans/serenity_execution_path.md` §5.2 承诺过「显式 `thesis_invalidated` 标记」作为独立退出触发器，但 SerenityOpportunity 模型 + 迁移都无此字段，`position_manager.py:247-253` 也只查 `opp.status != active`。改写为「status 是唯一控制轴，`invalidated` 语义通过 status 迁到 `dismissed`/`expired` 表达」，并注明若未来要独立布尔 marker 需加 field + 迁移 + spec claim。

### 交付
- 1 finding。1 提交。
- 验证：`tests/specs/` 554 passed / 0 failed。

### 边界
仅一个 doc 文件。

---

## 2026-07-07 — B10-docs-tail：因果链 + CCXT 直构造异常 + Spec 索引（4 findings / 1 commit / 纯 docs+docstring）

### 触发
接 B10-docs-sweep 后 owner「继续推进」。B10 剩几条不涉及 code rename 的 docs drift 一并扫掉。

### 做法
1. **#30 Position 因果链 docs 说 schema 不含**：三份文档都写「positions 从 orders/fills 投影，不直链 proposal」—— 实际 schema 早已带 `proposal_id` / `order_id` / `opportunity_id`，每个 broker 开仓时写，`adopt_position()` 也走同一路径（synthetic-but-honest 链）。修 `docs/governance/trust_and_change_control.md` + `docs/design/execution_contract.md` + `docs/design/webui_page_map.md` 三处，各自把「不直接链」的旧描述改成「已直接链，并说明 adopted 也走同链」。
2. **#49 CCXT 直构造异常清单不全**：`aletheia/data/ccxt_provider.py` 模块 docstring 说「xStocks 是唯一直构造 `CCXTProvider(...)` 的例外」—— 但 B9-data 修的 #91 揭示 universe/registry + universe/features 也直构造。docstring 扩展成正式清单：`xStocks` + `universe.registry.sync_registry` + `universe.features.collect_features`，每条注明理由（唯一交易所 / factory 缺 `_ensure_primary+load_markets` 表面），并加一句「任何新的直构造点应加进这里，缺失即视为 review-flagged 偏差」。
3. **#50 execution_contract 因果链 stale**：ledger 说 execution_contract 声称 position 因果链比实际弱 —— 但该文件本身只在 §1 一处引用了 position 因果链，那处已被 #30 修的同一批次覆盖到（改成「已直接链 — #30/#50」）。视为 #30 fix 顺带 subsumed。
4. **#76 README Spec-as-Code 表旧且不全**：手写 14 行表列了不存在的 `spec_promotion_gates.py`，claim 数（如 `spec_risk_engine.py=7` 但实际 34+）也过时；实际 `tests/specs/` 有 59 个 spec 文件。**放弃手抄枚举**（会一直漂），改为 pointer：跑 `ls tests/specs/spec_*.py | wc -l` + `uv run pytest tests/test_spec_conformance.py` 才是活的真相。README 保留一份「代表性 spec」快速定位清单（按契约类/管道类/Serenity 类/学习类分组），不再声称完整。

### 交付
- 4 findings（#50 是 #30 的 subset，一次修同时覆盖）。1 提交（3 doc + 1 docstring）。
- 无新回归（纯 docs/docstring 无 runtime 行为）。
- **验证**：`tests/specs/` 554 passed / 0 failed。

### 剩余
- **B10 剩最后一条大件**：#97 (System Operator rename 未完 —— `pi_daemon` 名字仍散落 config/API/audit/client contracts)。这条涉及**代码 rename**，改动面明显大于本批 docs-only 批。留作 B10-rename 单独一批（可能是 B10 收官）。
- **B10 完成**：#28（superseded）、#29、#30、#31、#32、#33、#49、#50（subsumed）、#76、#77、#78、#79、#80、#81 —— 全部处置。
- **B8** 前端诚实性（需 `npm run build`）+ 已 deferred 项仍留。

### 边界
仅 3 个 doc 文件 + 1 个模块 docstring；未推 / 未 restart / 未 enable live / 未触 code runtime 行为。

---

## 2026-07-07 — B10-docs-sweep：scheduler + live-unlock + kill-switch + permission-mode 漂移（5 findings / 1 commit / 纯 docs）

### 触发
接 B10-api-ref 后 owner「继续推进」。B10 docs drift 尾第二轮 —— 5 条 stale docs 一次批处理。

### 做法
1. **#29 agent_contracts.md 调度器表旧**：文档写「31 jobs」+ 编号跳空（12→16），漏 5 个真实 job：`lesson_reviewer`、`connectivity_monitor`、`shadow_resolver`、`news_fetch`、`outcome_settler`。用 `grep -oP 'id="[^"]+"' scheduler.py | sort -u` 得实际 33 个 unique ID，全表重写、连续编号、5 条新行按各自 schedule 补齐（cron/interval/executor）。
2. **#31 live_unlock_contract.md scorecard "尚未实现 xfail"**：§4a 写「`server/routes/live.py` 尚未调用 `compute_scorecard()`——D8-B02/D8-C01 是 xfail tripwire，实现后去掉 xfail」。实际 `live.py:30-41` 早已实现（`compute_scorecard()` + `composite_score < 70 → 422`）。改写为「已实现且被 spec 锁定，tripwire 已通过（不再 xfail）」。
3. **#32 api_reference.md kill-switch confirm token「已知缺陷」注**：文档 §Risk / Kill Switch 挂了个 ⚠️ 说 release 路径**不要求** confirm token。实际 `server/routes/risk.py:85-101` 已经强制 `confirm_release=True`（否则 422）。§0 SDD 表 + §Risk 表都补上 `confirm_release` 字段说明；⚠️ 缺陷注改写成 ✅「2026-07-07 已解决」并指向 `docs/issues/BACKEND_ISSUES.md` 的对应条目。
4. **#33 api_reference.md `/settings/permission-mode` 说明误导**：文档写「worker + operator」，实际 `set_permission_mode` 只写 `cfg.agent.permission_mode`（worker）—— operator 是完全独立的 `cfg.operator.permission_mode` 面。改写描述强调 worker-only + operator 是**独立**面（这处正好呼应 B9-diag 的 #180 inspect config namespace 混淆修）。
5. **#28 覆盖回顾**：ledger #28 是 3 条问题的汇总（错 operator trigger + 缺 infra/proposal + dead strategy），三条分别已由 B10-api-ref 的 #78/#79/#81/#77 直接修 —— 本批 Fix Progress 行里标注「superseded by prior batches」。

### 交付
- 5 findings。1 提交（纯 docs）。
- 无新回归（docs-only 无 runtime 行为）。
- **验证**：`tests/specs/` 554 passed / 0 failed；`spec_api_reference.py` 一起跑绿。

### 剩余
- **B10 剩余** #30 (Position causal links docs stale — code has proposal_id/order_id/opportunity_id but docs说没有)、#49 (ccxt_provider docstring xStocks-only 声明错)、#50 (execution_contract causal links 信息性 stale)、#76 (README Spec-as-Code 索引旧)、#97 (System Operator rename incomplete pi_daemon 仍散落)、#104 (serenity 执行 plan 承诺 vs 代码)。下一批候选 #30 + #49 + #50 + #76 (纯 docs)。#97 涉及代码 rename，成本大，单独一批。
- **B8** 前端诚实性（需 `npm run build`）仍留。

### 边界
仅 `docs/design/api_reference.md` + `docs/design/agent_contracts.md` + `docs/design/live_unlock_contract.md`；未推 / 未 restart / 未 enable live / 未触 code。

---

## 2026-07-07 — B10-api-ref：api_reference.md 漂移扫（5 findings / 1 commit / 纯 docs）

### 触发
B9 收官后 owner「继续推进」。B10 docs drift 尾，先扫 `docs/design/api_reference.md` 与实际路由的分歧 —— 一条一条修，全部纯 docs 无 code touch。

### 做法
针对台账 #77–#81，逐条对着实际后端路由更新 `api_reference.md`：
- **#77 strategy 详情/mutation 路由早删了**：文档还写 `/strategies/summary`、`/strategies/{id}`、`PATCH /strategies/{id}/venue`。实际后端只有 `GET /strategies`（read-only 遗留归因）。删掉三行，说明块从「有 promote/retire/gates」改成「所有变异端点全删，只留 legacy list」。
- **#78 operator loop 触发路径**：文档写 `POST /operator/trigger`，代码是 `POST /operator/loop/trigger`（operator.py:938 `@router.post("/loop/trigger")` 挂在 `/api/operator` 下）。修为正确路径。
- **#79 缺 `GET /proposals/{id}`**：data.py:609 有此路由，索引里没有。补一行「Get one proposal by id (404 when absent)」。
- **#80 `/operator/internal/*` 通配折叠掩盖 15 条独立路由**：文档一行通配符「Internal operator-shell bridge endpoints; not public UI contracts」—— 与 §0 「complete endpoint index」承诺冲突。展开为 15 条具体 POST/PATCH，每条一句描述；下面加说明确认「internal-only，不是公共 UI 契约，枚举是为兑现 §0 完整承诺」。
- **#81 缺 `/_infra/doctor` / `/_infra/status` + dev-only 边界**：Infra 段只列了 livez/readyz。补充 `_infra/doctor`（诊断报告 JSON）+ `_infra/status`（infra 快照），并新增「Dev-only (environment-gated)」子段落显式记 `/dev/reload/status`/`/dev/reload/events` —— 生产 dev_reload_router 不挂载，UI 客户不应依赖。

### 交付
- 5 findings。1 提交（纯 docs）。
- 无新回归（docs-only 无 runtime 行为）。
- **验证**：`tests/specs/` 554 passed / 0 failed；`spec_api_reference.py` 一起跑绿（该 spec 会做「文档提到的路径也真存在」/「代码有的路径文档也有」的一致性检查，本批修的正是这层漂移，通过说明修得对）。

### 剩余
- **B10 剩余** #28（api_reference 别处遗漏 —— 与本批部分重叠可能已顺手修）+ #29（agent_contracts.md job table 漂移）+ #30/#31/#32/#33（其它 docs drift）+ #49/#50（docstring 漂移，一行改）+ #76（README Spec-as-Code 索引旧）+ #97（System Operator 命名 `pi_daemon` 仍散落多处 —— 与代码耦合，改动面大）+ #104（serenity 执行 plan 承诺 vs 代码）。下一批候选 **#29 + #30 + #31 + #32 + #33** —— 都是单文件小改。
- **B8** 前端诚实性（需 `npm run build`）仍留。
- 已 deferred：market_type 迁移 / operator 鉴权 / #109/#143/#102-pending/#17 / 运行时性能 #90/#147/#134 / #154/#155/#156（B9 尾）。

### 边界
仅 `docs/design/api_reference.md`；未推 / 未 restart / 未 enable live / 未触 credentials / 未改 code。若后续「complete endpoint index」承诺又漂移，spec_api_reference 会先失败提醒。

---

## 2026-07-07 — B9-data：universe fallback exchange + macro yfinance 出 loop（2 findings / 1 commit）

### 触发
接 B9-llm 后 owner「继续推进」。B9 剩最后两条独立可修的（#91 provider fallback + #92 async 阻塞），形状对偶：都是「本该走既有基础设施但没有」。

### 做法
1. **#91 universe registry+features 用同一交易所做 primary+fallback**：`registry.py::sync_registry` 和 `features.py::collect_features` 都写死 `CCXTProvider(exchange=venue, fallback_exchange=venue)` —— primary 挂了 fallback 是同一个 exchange 等于没有。`get_market_data_provider` 工厂已经在读 `md.ccxt_fallback` 做交叉交易所 fallback，两个地方直接 mirror 这套配置解析：`fallback_venue = md.ccxt_fallback or venue`。工厂本身返回 FallbackChainProvider 缺 `_ensure_primary`/`load_markets`，universe 这两处需要 CCXT 原生接口所以还是直接 `CCXTProvider(...)`，但至少 fallback 现在真的能落到另一个交易所了。
2. **#92 MacroCollector async 内 sync yfinance**：`_fetch_tradfi()` 是同步的，一 ticker 一 HTTP round trip；`collect()` 是 async 但直接 `_fetch_tradfi()` 调它 —— 这跑在 scheduler 的 async worker loop 上，每次宏观 tick 会 block loop 好几秒，其他 async 任务（market observer / heartbeat / exchange sync）全被冻。改成 `await asyncio.to_thread(_fetch_tradfi)` —— 阻塞工作出 loop 到线程池，loop 保持响应。

### 交付
- 2 findings。1 提交。
- **新增回归** `tests/test_data_provider_and_async_fixes.py`（3 tests / 3 passed）：
  - `test_universe_registry_uses_configured_fallback_exchange` — mock `CCXTProvider` 捕获构造参数，`ccxt_exchange="bitget"` + `ccxt_fallback="okx"` 后运行 `sync_registry(cfg)`，断言 `fallback_exchange == "okx"` 不是 `"bitget"`。
  - `test_universe_features_uses_configured_fallback_exchange` — 同套断言在 `collect_features()`。
  - `test_macro_collector_runs_yfinance_off_loop` — 关键测试：mock `_fetch_tradfi` 让它 `time.sleep(0.5)`（真同步阻塞），`asyncio.gather` 里同时跑 `collect()` 和一个 50×10ms 的竞态 async ticker。如果 loop 被阻塞 → 竞态 ticker 达不到 50 次。断言 `competing_ticks == 50` + 总墙钟 < serial 时长（并发不串行）。修前会失败；修后 to_thread 让 sleep 出 loop，竞态 ticker 顺利跑完。
- **验证**：`tests/specs/` 557 passed / 0 failed；本 batch 3 tests。

### 剩余
- **B9 全部处置或标 refuted**：#91/#92（本批）+ #124/#158（B9-analytics）+ #161（B9-llm）+ #177/#178/#179/#180（B9-diag）已修；#154 (divergence_scanner) 属 dormant gated 特性可留待 flag 启用；#155 (OHLCV cache market_type) 与 market_type 迁移耦合暂缓；#156 (Coinalyze OI live schema) 需先核对外部 API 后再修，脱离静态测试范围。
- **B8** 前端诚实性（需 `npm run build`）+ **B10** 文档漂移仍留。B10 大部分是机械改，成本低价值明确 —— 下一批候选。

### 边界
仅 universe/registry.py + universe/features.py + data/collectors/macro.py + 焦点回归；未推 / 未 restart / 未 enable live / 未触 credentials。config schema 未变，只 mirror 既有工厂的解析。

---

## 2026-07-07 — B9-llm：complete_with_fallback tier provider name 透传（1 finding / 1 commit）

### 触发
接 B9-analytics 后 owner「继续推进」。剩下 B9 里 #161 是最简单的 helpers 单点改动，独立无依赖。

### 做法
- **#161 complete_with_fallback provider name 落错**：调用链是 `agents.helpers.llm_complete(provider="deep_provider_xyz")` → `get_provider(name) → ModelProvider obj` → `complete_with_fallback(request, provider=obj)`。complete_with_fallback 首行 `resolved_provider_name = provider_name or cfg.models.default_provider` —— helpers 从来没传 `provider_name=`，所以永远回退 default。后果两条：
  - deep-tier 失败被计到 default_provider 的 model 冷却状态里，tier 自己的 model 永远不冷却
  - candidate_models 走 default_provider 的候选表，不是 tier 的
  修：`helpers.llm_complete` + `llm_structured` 都在 `get_provider(...)` 前把入参 str 记到 `provider_name`，然后 `complete_with_fallback(..., provider=obj, provider_name=provider_name)`。llm_stream 不用改（不经过 fallback，直接 `provider.stream`）。
- **既有 spec 更新**：`tests/specs/spec_gateway_stability.py::_patch_llm` 里的 `fake_complete(req, provider=None)` 现在会收到多一个 kwarg，把签名加 `provider_name=None` 兼容。

### 交付
- 1 finding。1 提交。
- **新增回归** `tests/test_llm_helpers_provider_name_fix.py`（3 tests / 3 passed）：
  - `test_llm_complete_threads_provider_name_to_complete_with_fallback` — mock `helpers.complete_with_fallback`，断言 provider_name="deep_provider_xyz" 被原样透传。
  - `test_llm_complete_forwards_none_when_caller_did_not_specify` — 缺省 None 也如实透传（保 default_provider 回退语义）。
  - `test_llm_structured_threads_provider_name_to_complete_with_fallback` — 结构化路径同样透传。
- **既有测试更新**：`spec_gateway_stability::_patch_llm` fake_complete signature 加 `provider_name=None`。
- **验证**：`tests/specs/` 563 passed / 0 failed（+9 vs 前批 baseline 554，因为多包含了几个 spec_gateway_stability 相关）；本 batch 3 tests + 更新 1 fixture。

### 剩余
- **B9 剩余** #91（universe/registry direct CCXTProvider 绕过 factory）+ #92（MacroCollector sync yf.Ticker inside async blocking hot tick）+ #154（divergence_scanner 方向逻辑 dormant gated）+ #155（OHLCV cache 键漏 market_type，与 market_type 迁移耦合暂缓）+ #156（Coinalyze OI 需先核 live schema）。下一批候选 **#91 + #92** —— 两个都是「构造 provider 时绕过工厂」和「async 内 sync 阻塞」，形状统一。
- **B8** 前端诚实性（需 `npm run build`）+ **B10** 文档漂移仍留。

### 边界
仅 helpers.py + spec_gateway_stability.py fixture + 焦点回归；未推 / 未 restart / 未 enable live / 未触 credentials。API 变化只在 helpers 内部（callers 已通过 provider= 传 str，`llm_complete` 签名未变）。

---

## 2026-07-07 — B9-analytics：equity-curve 窗口 + trade_insights 运算符优先级（2 findings / 1 commit）

### 触发
接 B9-diag 后 owner「继续推进」。两个 analytics 单点小改（#124 SQL、#158 Python 表达式）打包。

### 做法
1. **#124 equity-curve LIMIT 截错方向**：`server/routes/analytics.py::get_equity_curve` 用 `ORDER BY closed_at ASC LIMIT ?`。full_cumulative 模式下账户 close 数 > limit 时，SQL 保留**最老**的 N 条 —— 曲线冻在古老历史里，最近的所有点都被切掉。改成 `SELECT * FROM (SELECT ... ORDER BY closed_at DESC LIMIT ?) ORDER BY closed_at ASC` —— 取**最新** N 条后按时间正序供 cumulative loop 走。
2. **#158 trade_insights 运算符优先级 trap**：`_by_exit_reason` 里的内联三元
   ```python
   r["close_reason"] if "close_reason" in r.keys() else None or "unknown"
   ```
   按 Python 优先级解析为 `X if C else (None or "unknown")` —— `or` 比 `else` 结合更紧。当列存在但值 None（SQLite null），返回 None（不是 "unknown"）。外层 `if reason_of(r) == "unknown"` 匹配为零 → unknown 桶的 `avg_pnl_pct = 0/tc = ~0`（tc 却不为零，因为分桶那步用了 `reason or "unknown"` 二段式，正确落入 unknown）。抽出成 `_reason_of(row)` 独立函数，一次得对，两处复用（分桶 + avg 求和），意图可审计。

### 交付
- 2 findings。1 提交。
- **新增回归** `tests/test_analytics_windowing_fixes.py`（3 tests / 3 passed）：
  - `test_equity_curve_returns_recent_points_when_over_limit` — 种 10 条跨 day-30 到 day-3 的 closed positions，GET `/equity-curve?limit=3` 期望返回 3 条最近的（时间 > 11 天前），不是最老的 3 条。
  - `test_by_exit_reason_unknown_bucket_avg_pnl_is_nonzero_when_pnl_nonzero` — 两条 `close_reason=None` 行，realized_pnl=5/3；断言 unknown 桶的 avg_pnl_pct **非零**（就是当年零的 bug 就是这个）。
  - `test_by_exit_reason_named_buckets_still_work` — 保护 stop_loss/take_profit 命名桶不回归。
- 测试 setup 用 `test_pnl_pipeline._setup` 相同模式（equity-curve 是 sync route，跑在 threadpool worker，需 `monkeypatch.setattr(sq, "db_path", lambda: test_db)` 让 worker 打开自己的连接指向同一个文件）。
- **验证**：`tests/specs/` 571 passed / 0 failed（+17 vs 前批 baseline 554，因为 test_pnl_pipeline 也一起跑）；本 batch 3 tests。

### 剩余
- **B9 剩余** #91 (universe/registry direct CCXTProvider 绕过 factory)、#92 (MacroCollector sync yf.Ticker inside async)、#154 (divergence_scanner 方向逻辑，dormant gated)、#155 (OHLCV cache 键漏 market_type，与 market_type 迁移耦合暂缓)、#156 (Coinalyze OI 需先核 live schema)、#161 (complete_with_fallback 用错 provider name)。下一批候选 **#161** —— 单点 helper 改动，无外部依赖。
- **B8**（前端诚实性）+ **B10**（doc drift 尾）仍留。

### 边界
仅 analytics.py + trade_insights.py + 焦点回归；未推 / 未 restart / 未 enable live / 未触 credentials。SQL 是 SELECT-only 子查询无 schema 变更；trade_insights 抽 helper 无外部 API 变化。

---

## 2026-07-07 — B9-diag：doctor/CLI/knowledge 诚实性四联（4 findings / 1 commit）

### 触发
B7 收官后 owner「继续推进」。挑 B9 中 **doctor / CLI / knowledge router** 单点小改的诚实性 gap 打包成 mini-batch —— 都是「输出与实际状态不符」，成本低价值明确。

### 做法
1. **#178 doctor 严重度三档映射**：`_probe_config_consistency` 用 `sev = "warn" if f.severity == "warning" else "blocker"` 二叉，`config_consistency` 实际发 `"info"/"warning"/"blocker"` 三档 —— info 一律被误升级成 blocker → doctor exit 1。改用 `_SEV_MAP = {"info":"info","warning":"warn","blocker":"blocker"}`，未知 severity 保守回退 blocker。
2. **#179 doctor 审计链无时间戳误分类**：`_probe_audit_chain` 里 `post_fix` list comprehension `if parsed is not None`—— 没时间戳的 break 被排除；如果**全部** break 都无时间戳，`post_fix` 空 → 走「历史 INFO」分支，隐藏真正的 post-fix 损坏（时间戳被抹掉的 tampering）。改成三桶：pre_fix / post_fix / undateable；只有 undateable 时报 WARN + hint「无法自动分类，需人工检查」。
3. **#180 inspect config namespace 错位**：`aletheia inspect config` 在 `"agent"` 块里打 `cfg.operator.permission_mode` —— 两个是不同的权限面（agent 是 trading agent，operator 是 System Operator LLM），把 operator 的值挂在 agent 名下误导读者「哪个 mode 在闸门 trading」。修为 `cfg.agent.permission_mode`，并新增独立的 `"operator": {"permission_mode": cfg.operator.permission_mode}` 块使两个面都可见。
4. **#177 knowledge router 不可达 role**：`_ITEM_TYPES_BY_ROLE` 有 4 条（strategy_designer / market_observer / equity_observer / paper_trader），实际只有 `market_observer.py` 调 `build_advisory_context`。`strategy_designer` 是 #94 已删生命周期残余；`equity_observer` agent 存在但不调 router；`paper_trader` 无调用方。删 3 条不可达项，`AgentRole` Literal 收窄到 `market_observer`；未来任何 caller 再加时同步加回。

### 交付
- 4 findings。1 提交。
- **新增回归** `tests/test_diagnostics_honesty_fixes.py`（4 tests / 4 passed）：
  - `test_knowledge_router_lists_only_wired_roles` — 断言 `_ITEM_TYPES_BY_ROLE.keys() == {"market_observer"}`。
  - `test_doctor_maps_info_severity_to_info_not_blocker` — mock check_config_consistency 返回三档 finding，断言 `info→info, warning→warn, blocker→blocker`。
  - `test_doctor_audit_chain_flags_undateable_breaks_as_warn` — mock `verify_audit_chain` 返回 `errors=[...], error_timestamps=[""]`，断言 doctor 报 warn + detail 含 "unparseable timestamp"。
  - `test_inspect_config_reports_agent_permission_mode_under_agent_namespace` — 配置 agent=Paper / operator=PaperAuto，CliRunner 跑 `inspect config --json`，断言 `payload["agent"]["permission_mode"] == "Paper"` + `payload["operator"]["permission_mode"] == "PaperAuto"`。
- **既有测试更新** `test_knowledge_router.py`：`test_uses_role_item_types` + `test_combines_lessons_and_guards` 从 `"strategy_designer"` 改为 `"market_observer"`（前者已删）。
- **验证**：`tests/specs/` 554 passed / 0 failed；本 batch 4 + 更新 34 = 38 tests all green。

### 剩余
- **B9 剩余** #91/#92/#124/#154/#155/#156/#158/#161 —— 数据源工厂、blocking 调用、analytics 窗口/键、collector schema。下一批候选：`#124` (equity-curve ORDER BY LIMIT before date cutoff — SQL 修) + `#158` (trade_insights 运算符优先级) —— 两个都单行改动。
- **B8** 前端诚实性（需 `npm run build`）+ **B10** 文档漂移仍留。

### 边界
仅 doctor.py + inspect_cmd.py + router.py + 焦点回归；未推 / 未 restart / 未 enable live / 未触 credentials。router 收窄的 Literal 会让静态类型工具捕获未来错误调用 —— 是好副作用。

---

## 2026-07-07 — B7-wal：backup 用 SQLite Connection.backup() 走 WAL 安全路径（1 finding / 1 commit）

### 触发
接 B7-mini 后 owner「继续推进」。B7 最后剩的 raw copy WAL 隐患 —— 收官 B7。

### 做法
- **#59 backup raw-copy WAL 隐患**：`create_backup` 用 `shutil.copy2(app.db)` —— SQLite 生产在 WAL 模式，一条 committed 行还可能存在于 `app.db-wal` sidecar 里没被 checkpoint。裸 copy 只带 `.db` → 那行没进 backup。改用 `sqlite3.Connection.backup(dst_conn)` API —— 官方为 live snapshot 设计，读锁 + 顺 WAL 走页 dependency，产出的 dst 是内部一致的、单文件（无 -wal/-shm 边角）、无 checkpoint 竞态。若 src 不是有效 SQLite（空占位/被裁剪的测试固件），fallback 回 shutil.copy2 —— 不比修前更差。

### 交付
- 1 finding。1 提交。
- **新增回归** `tests/test_backup_wal_safety_fix.py`（2 tests / 1 passed + 1 conditionally skipped）：
  - `test_backup_captures_wal_committed_rows` — 构造 WAL 模式 db、INSERT + commit **但不 checkpoint**，触发 -wal 存在的场景（若 SQLite 自动 checkpoint 了则 skip 这条 —— 测试只在有真 sidecar 时才有意义）；backup → 解压 → 打开复制的 db 读该行 → 断言存在。
  - `test_backup_still_handles_non_sqlite_app_db` — src 不是有效 SQLite（写入 `b"not a sqlite file"`）时 backup 不炸，fallback 到 raw copy。
- **验证**：`tests/specs/` 559 passed / 0 failed（+1 vs 前批 baseline 558）；本会话 2 tests。

### 剩余
- **B7 全部完成**（除已显式暂缓的 #109/#143/#102-pending/#17，各有独立 blocker 记在台账 Fix Progress Deferred）。
- B8（前端诚实性）/ B9（数据·分析·doctor·cli）/ B10（doc drift 尾）三批 + 已 deferred 的 market_type 数据模型迁移仍在。

### 边界
仅 backup/service.py + 焦点回归；未推 / 未 restart / 未 enable live / 未触 credentials。sqlite3 是 stdlib，无新依赖。

---

## 2026-07-07 — B7-mini：backup 覆盖 + trades 导出实体 + capital 审计（3 findings / 1 commit）

### 触发
接 B7-io-honesty 后 owner「继续推进」。挑 B7 中三条**单点小改**的诚实性 gap 打包成 mini-batch —— 都是「文档/行为不一致」，成本低价值明确。

### 做法
1. **#58 backup 缺 audit.jsonl**：`backup/service.py::create_backup` 之前只落 app.db / config.yaml / events/ / artifacts/，**不复制 data/logs/**，而 `data/logs/audit.jsonl` 正是那条 hash-chained 审计链。从这样的 backup 恢复后 `verify_audit_chain` 无从跑（文件根本不在）。加一段 copy `data_dir/logs/` 到 staging，用同一个 `_should_exclude` 过滤secrets，file_count/total_bytes 相应累加。
2. **#65 /trades/export 与 docstring 矛盾**：docstring 写 "tax/PnL reporting"，实际返回 orders 表（无 realized_pnl / entry_fee / exit_fee / closed_at）。改为返回 `list_positions(status="closed")`，CSV 列换成 opened_at/closed_at/symbol/market_type/direction/quantity/entry_price/current_price(=exit)/realized_pnl/entry_fee/exit_fee/slippage_cost/execution_mode/close_reason/btc_entry_price/btc_exit_price/trade_school/position_id。前端只有一个 `<a href={api.exportTradesCsv()} download>` 无 CSV 解析，schema 变更安全。
3. **#87 capital 事件无审计**：`POST /api/capital/events` 写 `capital_events` 表但**从未 write_audit** —— 权益曲线基线一跳无审计链条可查。加 `write_audit({event_type=capital_deposit/capital_withdraw, entity_type=capital_event, after=payload})`，方向 event_type 区分便于 grep。

### 交付
- 3 findings。1 提交。
- **新增回归** `tests/test_mini_honesty_fixes.py`（4 tests / 4 passed）：
  - `test_backup_captures_audit_jsonl_hash_chain` — 种 audit.jsonl → 打 backup → 断言归档成员含 `logs/audit.jsonl`。
  - `test_trades_export_returns_closed_positions_with_pnl_columns` — 种 closed Position → GET /api/trades/export → 断言 CSV header 含 realized_pnl/entry_fee/exit_fee/closed_at + row 里 9.85 (realized_pnl)。
  - `test_capital_deposit_writes_audit_event` + `test_capital_withdraw_writes_audit_event` — POST 之后读 logs/audit.jsonl 里应有 capital_deposit/capital_withdraw entries。
- **验证**：`tests/specs/` 558 passed / 0 failed；本会话 4 tests / 4 passed。

### 剩余
- B7 剩：**#59** backup raw-copies WAL app.db without SQLite checkpoint —— 需真正接 sqlite3.Connection.backup() API + WAL checkpoint，比其他 B7 findings 大一档，留下一批单独做。**#109 broker partial-fill、#143 paper_internal drawdown、#102-pending、#17 dreaming cursor** 仍暂缓（各有独立 blocker，见台账 Fix Progress deferred 列表）。
- B8/B9/B10 保留。

### 边界
只改本会话三处 + 焦点回归；未推 / 未 restart / 未 enable live / 未触 credentials。前端 client 不需改（CSV 只是 raw download 无 schema 解析）。

---

## 2026-07-07 — B7-io-honesty：proposal-pack 过滤 + import 校验/冲突诚实（4 findings / 1 commit）

### 触发
接 B7-broker 后 owner「继续推进」。选 sync 层的三条诚实性 gap —— #62/#162 是同一件事的重复登记，#63/#64 是 import 静默跳过。

### 做法
1. **#62/#162 proposal-pack 过滤**：`sync/exporter.py::_export_proposal` 之前 raise 缺 proposal_id，然后 `where_clause=None` 直接落 —— 结果：一条 pack 命令导出**所有** trade_proposals + 所有 serenity_themes/evidence/claims。重写为：先按 `id=?` fetch 目标 proposal（不存在则 ValueError 明确报错），然后经 `opportunity_id → theme_id` 链条只导出关联的 serenity_opportunities/themes/evidence/claims；没有 opportunity 时直接省略 serenity 子表（不是导出空文件，也不是导出全表）。新增 `_export_by_id` + `_write_rows` 助手。
2. **#63 import 校验缺件静默**：`_verify_checksums` line 116 `if file_path.exists():` 分支缺 else，manifest 列的但归档里没有的文件直接跳过 —— 破损/被裁剪的 bundle 被当作干净的接受。现在缺件 → `result.errors.append("Checksum manifest lists missing file: ...")`。
3. **#64 import 冲突未实现**：`ImportConflict` 类定义了但从没实例化。现有 PK + 载荷不同的行被计为 `skipped_duplicates`。改为：SELECT * 拿本地行，逐列对比；差异列非空 → 追加 `ImportConflict`（含 local_hash / remote_hash / diff 列名的 message）；完全相同 → 保留 `skipped_duplicates`（不倒退）。新增 `_row_fingerprint` 生成规范 JSON SHA-256 指纹。

### 交付
- 4 findings（3 独立 + 1 duplicate 登记合并）。1 提交。
- **新增回归** `tests/test_io_honesty_fixes.py`（6 tests / 6 passed）：
  - `test_proposal_pack_exports_only_the_named_proposal` — 种两个 proposal，pack 只含被点名的。
  - `test_proposal_pack_omits_serenity_when_proposal_has_no_opportunity` — 无 opportunity 时 pack 不带任何 serenity 文件（不是空文件也不是全表）。
  - `test_proposal_pack_rejects_unknown_proposal` — 未知 proposal_id → ValueError。
  - `test_import_dry_run_flags_missing_files_from_checksums` — 缺件 → `result.errors` 有条目。
  - `test_import_diverged_row_recorded_as_conflict_not_skipped_duplicate` — 差异行 → `conflicts` 1 条，`skipped_duplicates=0`，message 含差异列名 `summary`。
  - `test_import_identical_duplicate_still_counted_as_skipped` — 相同行 → `conflicts=[]`，`skipped_duplicates=1`（不倒退）。
- **验证**：`tests/specs/` 562 passed / 0 failed；本会话新增 6 tests / 6 passed。

### 剩余
- B7 剩：backup 完整性 (#58 audit.jsonl 未入 backup、#59 raw-copy WAL 未 checkpoint)、capital 审计 (#87)、trades/export docstring 漂移 (#65) —— 分别有独立成本；下一批候选 #87 + #65（都是单点小改）。
- B8/B9/B10 仍留。
- 市场类型数据模型迁移仍暂缓。

### 边界
只改 sync 层三条 finding + 焦点回归；未推 / 未 restart / 未 enable live。旧 `_export_db_tables` 通用接口保留（其它 export 模式仍用，signature 未变）。

---

## 2026-07-07 — B7-broker：跨 broker averaging + trailed-stop 三联（3 findings + #142 核对 / 1 commit）

### 触发
接 B7-trust 后 owner「继续推进」。选 B7 中形状统一的 broker 三联（都是 paper_broker → 三家 live broker 的克隆漂移）+ 顺手核对 #142 是否已被 `iridite/system_proxy_support` 覆盖。

### 做法
1. **#140 averaging-in entry_fee 累加**：paper_broker 在均入时累加 entry_fee（`existing.entry_fee + new_leg_fee`）；bitget/okx/binance 三家 live broker 从没抄这段 —— 均入后 entry_fee 只写新的一腿，之后 close 按 schedule 重推的费用喂给学习环，realized PnL 被高估。三家各加一段 mirror paper_broker 的累加代码。
2. **#141 averaging-in liquidation_price 重算**：均入后加权平均入场变了，jetty liquidation 也应该重算（用同 leverage）。paper_broker 有这段；三家 live broker 没有。三家各加一段 `if existing_lev and existing_lev > 1: update_kwargs['liquidation_price'] = liquidation_price_for(new_entry, direction, existing_lev, symbol)`。展示/LLM 用，非实际风控闸门 —— 交易所自己的强平线是真闸门。
3. **#139 trailed-stop exchange 同步**：`position_manager._maybe_update_trailing_stop` 只在 `execution_mode in (bitget_demo, bitget_live_limited)` 时调 `replace_exchange_stop`；okx/binance 均入的 DB stop 收紧但交易所侧不动，网关掉线时旧的宽 stop 先触发。新增 `execution.stops.replace_exchange_protective_stop(pos_id, new_stop)` 做 broker-agnostic 分派：Bitget 调既有 `replace_exchange_stop`；OKX 加 `replace_okx_exchange_stop`（cancel old algo + place new via `client.place_algo_order` / `cancel_algo_order`，signature 已核）；Binance 目前 client 只有 `place_order` 无 algo/stop 接口，dispatcher log warning + 跳过（DB stop 已收紧，仅交易所侧未同步）。整个特性 gated 在 default-off `cfg.trading.exchange_tpsl_enabled` 后 —— 生产 dormant，用户 opt-in 时三家 broker 行为需一致。
4. **#142 核对**：拉起 `.claude/worktrees/system-proxy-support` worktree（PR #38，Option C），确认该分支已重写 `binance.client.py` 用 `make_ccxt(...)` 工厂 + managed aiohttp session 走 proxy，`_ensure_exchange` 惰性构建。**结论：#142 已在那条 PR 里被覆盖**，不在 bug_fix 分支上独立修（会与 PR merge 冲突且重复工作）。台账记 refuted-in-that-branch。

### 交付
- 3 findings 全处置 + 1 finding 核对为 already-fixed-elsewhere。1 提交（代码 + 测试）。
- **新增回归** `tests/test_broker_averaging_and_trail_fixes.py`（7 tests / 7 passed）：
  - `test_live_broker_averaging_in_accumulates_entry_fee_and_recomputes_liquidation` — parametrized 三家 broker，构造 leg1=100 qty=1 + leg2=110 qty=1，断言 entry_fee = fee(leg1) + fee(leg2)（不是被覆盖），liquidation 重算且不等于 leg1 的值。
  - `test_replace_exchange_protective_stop_dispatches_to_okx_when_okx_demo` — dispatch 到 OKX 分支。
  - `test_replace_exchange_protective_stop_still_dispatches_bitget` — 保住既有 Bitget 路径不回归。
  - `test_replace_exchange_protective_stop_is_noop_when_flag_disabled` — feature-flag 关时全静默（连 Bitget 也不动），生产 default-off 语义正确。
  - `test_replace_exchange_protective_stop_binance_logs_and_skips` — Binance 未实现 log 但不炸。
- **验证**：`tests/specs/` 554 passed / 0 failed（同 baseline）；`tests/test_broker_averaging_and_trail_fixes + test_tpsl + test_okx_broker + test_bitget_broker + test_position_manager + test_position_close + test_position_reconciler + test_paper_broker_stops` = 64 passed。

### 剩余
- B7 剩：backup/import 完整性 (#58/59/62/63/64/162)、capital 审计 (#87)、reconciler 状态、export docstring 诚实性 (#65) —— 都比较机械；下一批候选。
- market_type 数据模型迁移（#51/52/54/55/57/96 等）仍暂缓 —— 与 market_snapshots migration 耦合。
- **B8/B9/B10** 保留 —— B8 需 `npm run build`，规划中。

### 边界
只改本会话四条 finding 直接对应的代码 + 焦点回归；未推 / 未 restart / 未 enable live / 未触 credentials。**#139 的 Binance 侧交易所同步未实现，dispatcher 会 log warning** —— 用户即使 opt-in `exchange_tpsl_enabled` 到 Binance testnet，也只能收紧 DB stop，交易所侧 bracket 保持初始值；这是**诚实的 partial-fix**（Binance client 需补 algo/stop 表面才能补齐，不在本批范围）。

---

## 2026-07-07 — B7-trust：信任边界四联批（4 findings / 1 commit）

### 触发
owner「继续推进 B7-B10」。选 B7 中形状统一的信任边界四联（无跨模块耦合、每处触信任边界、可用一个焦点回归覆盖）。

### 做法
1. **#66 persistence 路径根边界**：`POST /persistence/backups/verify` 和 `POST /persistence/import` 以前接受任意 caller path 直塞进 `verify_backup(Path(path))` / `ImportService.merge(archive)`。现在 `_resolve_archive_path()` 拒绝 `..` 段、resolve() 后要求落在 `data_dir/backups` 或 `data_dir/exports` 内，不满足 → 400。
2. **#67 executor 确认门**：`requires_confirmation` 原本是 metadata-only —— handler 已经跑了，response 才「标注」自己需要确认。现在 `execute_typed_command` + `AgentShellRuntime.handle` 在调用 handler 前检查 `params.confirm`；`requires_confirmation=True` 且 `confirm != true` → 返回 `permitted=True, needs_confirmation=True` 但**不跑 handler**。第二次调用需带 `confirm=true` 才真正执行。锁定测试 `test_operator_command_persists_structured_response_metadata_for_result_cards` 更新为断言首次不产生 action。
3. **#82 runtime_continuity kill switch**：service 有 `self.enabled` 但从来不检查。加 `_require_enabled(op_name)` 守卫，在 `prepare_candidate` / `start_shadow` / `validate_shadow` / `promote` / `drain` / `rollback` / `acquire_lock` / `release_lock` 各首行调用，disabled 时 raise `RuntimeTransitionError("runtime_continuity_disabled:<op>")` → 路由 409。读路径（status / list_events / current_runtime_id）不加守卫，dashboard 保持能看。`server/routes/update.py::update_continuity` 的直用也改为透传 `enabled=cfg.runtime.runtime_continuity_enabled`。
4. **#83 runtime 路由 actor+confirm**：req model 去掉 `actor: str = "human"`（改由 `_ROUTE_ACTOR="human"` 服务端硬编码，攻击者无法伪造 audit actor）；`RuntimeTransitionRequest` 加 `confirm: bool = False`，promote / rollback / forced-drain 强制 `confirm=True`，不然 400。`frontend/src/api/client.ts` 的 promoteRuntime / drainRuntime / rollbackRuntime 都相应加 `confirm: true`。

### 交付
- 4 findings 全处置。1 提交（代码 + 测试 + 前端 client）。
- **新增回归** `tests/test_trust_boundary_confirmation_fixes.py`（10 tests / 10 passed）：验证 #66 out-of-root/`..`/合法路径三档、#67 两阶段 confirm 契约、#82 kill switch 各写方法均 reject 且状态可读、#83 promote/rollback 无 confirm → 400 + candidate actor 服务端硬编码不接受调用方 `actor` 字段。
- **既有测试更新**：`tests/test_runtime_continuity.py` 加 `_mock_shadow_gateway` 桩（打 `server.routes.runtime` 的 `spawn_shadow_gateway` 名称绑定，而不是原始模块 —— 闭包按调用时词法作用域解析）+ setup_env 默认写入 `runtime_continuity_enabled: true` 配置。`tests/test_operator_routes.py` 的 resume 断言从「有 action」改为「无 action + needs_confirmation=True」。
- **验证**：`tests/specs/` 554 passed / 0 failed（+1 vs baseline 553）；`tests/` 非 specs 1870 passed / 3 deselected（3 deselected 皆为 bug_fix 与 master 漂移，已由 handoff §2 记明，非本批引入）。

### 剩余
- B7 内其它类别（PnL/reconciler market_type 键、backup 完整性、persistence 导入冲突、broker 跨市场）留下一批。市场类型数据模型迁移（#54/55/51/52/57/96 涉及）仍暂缓 —— 与已 deferred 的 market_snapshots migration 耦合。
- **#142 Binance 客户端 proxy 绕过**须先对 `iridite/system_proxy_support` 分支核对是否已修（worktree 已建 `.claude/worktrees/system-proxy-support`）。

### 边界
只改本会话四条 finding 直接对应的代码 + 焦点回归；未推 / 未 restart / 未 enable live / 未触 credentials。合并注意仍如上批 —— 本分支基点较老，`origin/master` 已推进多个提交（含 `c945110f` 可能与 B5 重叠），留待 PR 阶段 rebase。

---

## 2026-07-07 — Bug 台账修复推进：B1–B6 + B10a（42 findings / 11 commits）

### 触发
owner「开始完全修复」→「请继续，保证要一次搞完」。从 180 条台账按主题分批修，每批一次提交 + 定向回归。

### 做法
- 专用分支 `fix/bug-review-fixes`（基于 `iridite/bug_fix` HEAD）。每批：读码 → 最小改动 → 定向回归测试（触信任边界/安全/密钥/生命周期必带）→ 窄 + 宽测试 → 逐批提交。
- 显式 `git add` 指定文件，绝不 `-A`；修复期间不 push/restart/enable live（推送在收尾单独一步，owner 指令后执行）。
- 台账 `FULL_BUG_REVIEW_2026-07-06.md` 顶部「Fix Progress」表是权威修复日志（batch → commit → findings → 验证）。

### 交付（11 提交 / 42 findings，9 HIGH 全处置）
- **B1 密钥防泄漏**（`6b3c0e3`）#1,2,4,5,60,61,69,133,148 — GET/webhook/network 响应+审计脱敏、日志/导出/诊断脱敏。
- **B2 风控闸门**（`62054af`，信任边界）#7,24,93,98 — live 失败即拒（只 `live_limited` 放行）、现货+做空拒单、入场几何 stop/entry/tp 校验、lesson 拦截须 category=risk。
- **B3 隧道鉴权**（`756973d`）#137,132 — risk/exchange-mode/permission-mode 纳入公网隧道保护、重启审计发起方诚实。
- **B4a 决策链**（`98e964e`）#12,21,102-部分,151,152 — conviction 解析加固、现货做空归一、serenity 读对 `market_type` 键、归因回退。
- **B5 学习闭环**（`14eef58`/`ed2ca9d`/`45ef293`）#157,159,160,131,16,18,20,99,130 — guard/分析去合成平仓+过 clean_window、memo 半衰期权重、case 按平仓时间开窗、过期 lesson 不进 prompt、scanner-family 证据、applied lesson 复审。
- **B6 自愈/运行时**（`da7c6c3`/`843944a3`/`9483a4d4`）#145,146,135,136,144,88,149,150,138 — 修**中途掉线永久暂停**（HIGH）、回滚诚实、env-strip 补齐前缀、reconciler 状态、onchain/prediction 留存清理接线、capital 迁移守卫、启动同步移出端口绑定路径。
- **B10a SDD/契约**（`c8b798f4`）#34,176,95,105 — 注册漏挂的 spec（矩阵 +100 claims）、删死码前端方法、knowledge/execution 契约去除已删除路径。

### 验证
- 全 SDD spec 套件 **588 passed / 0 failed**（顺带修好 #20 非 hermetic）；每批另加窄回归，共新增 15 个 test 文件。

### 剩余（逐批续做，见台账 Fix Progress）
- **B7** 组合/PnL/对账/broker/持久化 · **B8** 前端诚实性（需 `npm run build`）· **B9** 数据/分析/doctor/cli · **B10** 文档漂移尾。
- **暂缓（耦合/基建，记原因）**：市场类型数据模型迁移（#9,10,11,13,14）、operator 鉴权（#40,41）、#109/#143/#102-pending/#17、运行时性能 #90/#147/#134。

### 边界
本阶段（与前几条审查记录不同）**确有**改业务/风控/执行/配置代码 + 跑测试 + 逐批提交，全部在专用分支 `fix/bug-review-fixes`；推送前未碰 live、未 restart gateway；触信任边界的每处改动都带定向回归测试。合并注意：`origin/master` 自本分支分叉后已推进 5 个提交（并发会话，含 `c945110f` 可能与 B5 guard 改动重叠），留待 PR 时对账。

---

## 2026-07-06 — Bug 台账整理：加导航索引 + 复核 #1–#131 精度

### 触发
owner「先把扫描出来的问题这个文档完整整理一下，确保足够准确、具体细致、方便 debug」。

### 做法
- `#132`–`#180` 是本会话产出、已双重对抗性核验；`#1`–`#131` 来自更早的手工会话、从未独立复核，且代码可能已漂移。
- 只读 workflow 14 lane 逐条把 `#1`–`#131` 对照**当前代码**复核，返回 confirmed/line_drift/overstated/likely_fixed/duplicate + 严重度 + 首打开位置 + 精炼一句话。结果：**126 confirmed / 2 line_drift / 3 overstated / 0 已修 / 0 重复**——旧台账质量很高。
- 用结构化产出 + 我自己整理的 `#132`–`#180` 元数据，在台账顶部插入 **Finding Index**（图例 + HIGH 优先清单 + 180 行可导航表：编号/严重度/首打开 file:line/一句话/状态）。

### 交付
- 台账新增顶部导航索引（180 行，9 HIGH / 99 MED / 72 LOW；另 9 条 refuted）。
- 5 条非 confirmed（#18/#23/#38/#84/#119）各追加 `**Correction (verify pass 2026-07-06)**` 说明并在索引标 `corrected`。要点：**#84 不是 XSS**（`_ERROR_HTML.format()` 先因 CSS 花括号抛 KeyError，真缺陷是错误页 500，降为 low）；#23/#38 impact 被高估（无前端调用者 / UI 下拉不给该值）；#18/#119 是行号漂移已给正确位置。
- 交接文档同步记录。台账现 2827 行，`#1`–`#180` 每条都可从索引一跳到位、且精度可信。

### 边界
只整理审查文档 + 交接；未改任何业务/风控/执行/配置/前端/测试代码；未跑测试；未 git add/commit。用临时脚本生成 180 行表格（生成后已删）以避免手抄错误。

---

## 2026-07-06 — Bug 审查收尾：前端逐页 + 后端零头 12 路并行审（全项目覆盖完成）

### 触发
owner「先不修，先继续收尾」。剩余未扫描 = 前端逐页（最大一块）+ 后端零头（knowledge/runtime_continuity/uta/doctor/cli）。

### 做法
- 只读 workflow 12 lane（前端 8：Settings 4280 / CommandCenter 1548 / Serenity+Learning / Prediction+News+Universe /
  Trades+Charts+Proposals / Risk+Overview+AgentActivity / 未覆盖 components / 共享 App·client·types·lib+charts；
  后端 4：knowledge / runtime_continuity+uta / doctor 逐行 / cli）。前端 lane 额外套「UI 诚实性 / fmtSymbol /
  +00:00-vs-Z 时间戳 / 加载通道 / onError」房规。并发压到 3。
- 每条候选再派独立对抗性核验。33 agent，0 报错。21 候选 → 18 保留（`#163`–`#180`），3 推翻。

### 交付
- 台账 `#163`–`#180` + 3 refuted + **Coverage status 标注「full-project pass complete」**。
- 三轮 fan-out（9+16+12）+ 原 #1–#131 手工，合计覆盖前端端到端 + 全部后端子系统，范围 `#1`–`#180`。
- 关键发现：`#171`（Overview 交易所连接灯永远探 Bitget，OKX/Binance 显示错）；`#174`（顶栏主状态永远读不到风控引擎，
  CAUTION/OPERATIONAL 是死支，健康态误显"未评估"）；`#165`（暂停/恢复吞 409，operator 误以为已恢复）；
  `#176`（knowledge 契约仍写 post_trade_review 为检索类型，照文档回接会重现 1900 行洪泛）；`#178`/`#180` doctor/CLI 诚实性。
- 交接文档全部更新为"覆盖完成 → 下一步是修或按变更再扫"，并给了 #163–#180 修复分诊。

### 边界
只做审查记录 + 交接文档；未改任何业务/风控/执行/配置/前端/测试代码；未重启 gateway；未跑测试（只改文档）；未 git add/commit。

---

## 2026-07-06 — Bug 审查缺口扫描：全项目未扫描子系统 16 路并行审 + 对抗性核验

### 触发
owner「继续对未扫描的部分做完整 code review，要求与之前一致」。先做全 `aletheia/` 覆盖缺口地图，
确认第一轮只覆盖了路由/网关基础设施/broker，剩 ~60k LOC 的信任关键区与决策链未系统审。

### 做法
- 只读 workflow 16 lane（信任关键：risk 引擎 20 关守卫链 / execution 下单咽喉 / permissions+tool_registry /
  config+secrets；运行态：self-heal 调度 job 体+connectivity / storage was_in_tx+审计链+保留；决策链：
  agent_shell decision+operator / agents 生产者；数据：provider+collectors+sources；analytics 28 文件拆 2 lane；
  model_provider；universe/trading/sizing；operator-shell+杂项）。每 lane 对照契约+spec；并发压到 3。
- 每条候选再派独立对抗性核验 agent。40 agent，0 报错，0 rate-limit。24 候选 → 20 保留（`#143`–`#162`），4 推翻。
- 亲手复核了两个矛盾/关键点：coinalyze OI 解析 vs 污染的矛盾（读 coinalyze.py 定论合并进 #156）。

### 交付
- 台账 `FULL_BUG_REVIEW_2026-07-06.md` 新增 `#143`–`#162` + 4 条 refuted + Coverage/Verification 更新。
- 交接文档更新：范围 `#1`–`#162`、最近发现、修复分诊（`#159`/`#145` 列 HIGH）、下一步覆盖。
- 两条 HIGH：`#145`（连接掉线后自动恢复任务挂在临时事件循环上→中途掉线永久暂停不自愈）；
  `#159`（守卫晋升日 cron 默认开、会改活配置，却用含合成/对账平仓的污染数据算胜率→可凭假数据晋升硬拦截守卫）。
- 关键 medium：`#153`（L1/L2 自愈动作是不可达死码 + PI 审批门是空转橡皮章，spec 假绿）；
  `#157`（校准反馈 f-string 崩溃被静默吞→喂 LLM 的校准反馈是死的）；`#161` 双层模型路由 fallback 认错 provider；
  `#160` memo/推理反馈用污染数据喂 PI prompt；`#162` proposal-pack 导出无视 id 全量导出。

### 边界
只做审查记录 + 交接文档；未改任何业务/风控/执行/配置/前端/测试代码；未重启 gateway；未跑测试（只改文档）；未 git add/commit。

---

## 2026-07-06 — Bug 审查续做：后端未覆盖区 9 路并行审 + 对抗性核验

### 触发
接手暂停的全项目 bug 审查，owner 选「继续审查扩覆盖」。目标是覆盖交接文档标注的未覆盖后端区（控制路由、gateway 生命周期/边缘、自更新、server app、持仓退出、交易所同步/broker、底层 client），不重复已记录区域。

### 做法
- 只读 workflow 编排 9 个 reviewer lane，每 lane 拿对应 `docs/design/*.md` 契约 + `tests/specs/spec_*.py` 对照代码；并发压到 3（尊重本 workspace 的 rate-limit 教训）。
- 每条候选 finding 再派一个独立「对抗性核验」agent 复核（确认真实、非预期行为、非死码假绿）。
- 两条最重要的密钥/信任边界发现（#133 日志脱敏、#137 tunnel 鉴权）我另行亲手复读锚点，防「假绿」。
- 22 个 agent，0 报错，0 rate-limit 失败；13 候选 → 11 保留（`#132`–`#142`），2 条被核验推翻。

### 交付
- 审查台账：`docs/_ai/FULL_BUG_REVIEW_2026-07-06.md` 新增 `#132`–`#142` + 「Investigated and refuted」小节 + Coverage/Verification 更新。
- 交接文档 `BUG_REVIEW_HANDOFF_2026-07-06.md`：更新范围、最近发现、修复分诊（`#137` 列为最高优先）、下一步覆盖。
- 关键发现：`#137` tunnel 鉴权漏 `/api/settings/risk`（公网下可无鉴权关熔断/放宽风控，defeat 了 `/api/risk/kill-switch` 的保护）；`#133` 日志脱敏漏 bare `secret`/`ACCESS-KEY`/`ACCESS-SIGN`；`#135` 自更新回滚不检查 `reset_hard` 结果谎报成功；`#140`/`#141`/`#139`/`#142` 三 broker 一致性/代理（`#142` 疑似已在 `iridite/system_proxy_support` 修，须先确认）。

### 边界
只做审查记录 + 交接文档；未改任何业务/风控/执行/配置/前端/测试代码；未重启 gateway；未跑测试（本轮只改文档）；未 git add/commit。

---

## 2026-07-06 — Full bug review ledger + AI handoff

### 触发
Owner 要求从 SDD 文档和 `discussion.md` 出发做完整代码域 bug 审查，并明确要求所有发现落本地文件；随后要求暂停，由 Claude Code 接手。

### 交付
- 新增/更新审查台账：`docs/_ai/FULL_BUG_REVIEW_2026-07-06.md`。
- 当前已记录 findings `#1` 到 `#131`。
- 新增接手说明：`docs/_ai/BUG_REVIEW_HANDOFF_2026-07-06.md`。
- `AI_HANDOFF.md` 和 `CURRENT_TASK.md` 增加接手入口。
- 后续记录格式调整为更短的 Markdown 分条格式。

### 边界
只做代码审查记录和交接文档；不改交易、风控、执行、配置、前端或测试代码；不重启 gateway；不触发交易行为。

---

## 2026-07-07 — Vergex 永续结构信号 observation-only 最小接入

### 触发
当前分支要把 Vergex 报告里可合作的“永续持仓成本结构 / 清算燃料 / 集中度”转成
Aletheia 可消费证据，但不能导入敏感明细，也不能影响 live/risk/execution。

### 交付
- 新增 `perp_structure_snapshots` 表和 `PerpStructureSnapshot` 模型。
- 新增 `insert_perp_structure_snapshot`、`latest_perp_structure`、`prune_perp_structure_snapshots`。
- 新增 `VergexStructureCollector` fixture 入口，白名单接受聚合字段，丢弃地址、prompt、账户、credential 形态字段。
- `render_enriched_context_md(perp_structure=...)` 渲染 POC、Value Area、清算燃料、集中度、data_quality、as_of_time。
- `market_observer` 只消费 60 分钟内的新鲜结构快照，作为 prompt 证据，不改变交易行为。
- `doctor` 增加 `data.perp_structure` 新鲜度探针。
- 数据利用和数据模型文档登记该 observation-only 数据域。

### 验证
- `uv run pytest tests/test_data_collectors.py tests/test_doctor.py tests/test_market_context.py tests/specs/spec_data_utilization.py tests/specs/spec_data_model.py -q`
- `git diff --check`
- `uv run aletheia doctor`（只读诊断）

### 边界
不接真实 Vergex API、不新增交易所、不导入地址库/账户/prompt/credential、不改 risk/execution/live、
不让该信号单独触发开仓或提高杠杆。

---

## 2026-07-05 — Opportunity CLI consolidation

### 触发
`inspect`、`flow`、`alpha`、`funnel`、`sizing`、`thesis` 在机会排查上职责重叠，owner 日常
不知道应该先敲哪个命令。

### 交付
- 新增主入口 `uv run aletheia opportunity audit`，用于分析机会从生成到放弃或执行的完整生命周期。
- 新增 `uv run aletheia opportunity trace --id <opportunity_id>`，用于追踪单个机会。
- `opportunity audit --view funnel|sizing|alpha|thesis` 统一承接四个细分视角。
- `flow` 保留为隐藏兼容别名，执行时提示改用 `opportunity`。
- `alpha`、`funnel`、`sizing` 顶层命令从根帮助菜单隐藏；人类输出提示改用 `opportunity --view ...`。
- `thesis audit` 隐藏为兼容命令；`thesis snapshot/snapshots/records` 保留，用于观察留档和明细导出。

### 验证
- `uv run pytest tests/test_cli.py::test_help -q`
- `uv run pytest tests/test_opportunity_flow_ledger.py::test_opportunity_cli_outputs_audit_and_trace tests/test_opportunity_flow_ledger.py::test_legacy_flow_cli_remains_hidden_compatibility_alias tests/test_opportunity_flow_ledger.py::test_opportunity_cli_outputs_specialized_views -q`

### 边界
只改 CLI 入口和只读分析路由；不改 risk、execution、scheduler、agent 决策、DB schema、配置或 live 行为。

---

## 2026-07-04 — Executable Candidate Supply Floor V1

### 触发
System Operator 在可执行候选池不足时会继续从贫瘠候选池里做判断，容易表现成连续 skip。

### 交付
- `trading_context` 写入 `candidate_supply_snapshot` operator event。
- layer2 增加 `candidate_supply` 摘要：可执行候选数、供给 floor、主排除原因。
- `operator_recommendations` 在连续 2 个 snapshot 的 `executable_count < 3` 时输出
  `candidate_supply_drought`。
- recommendation 指向既有安全命令 `run_market_scan`，由 Command Center / 权限层决定是否执行。
- `flow audit` integrated signals 增加 candidate supply 状态。

### 验证
- `uv run pytest tests/test_candidate_supply_floor.py tests/test_operator_events.py tests/test_anti_hallucination_e2e.py tests/test_agent_shell.py -q`
- `uv run pytest tests/specs/spec_decision_lifecycle.py tests/specs/spec_data_model.py tests/test_architecture_fitness.py -q`

### 边界
只做供给诊断和刷新建议；不调交易阈值、不改 debate/guard/risk/execution、不调用 LLM、
不创建 proposal、不下单。

---

## 2026-07-04 — Opportunity Flow CLI polish

### 触发
Opportunity Flow 已经能作为总账本，但排查 debate/guard/错过机会时还需要更快地缩小范围。

### 交付
- `flow audit` 增加 `--stage`、`--verdict`、`--symbol` 三个过滤参数。
- human output 增加 `Debate signal` 区块，展示 debate 总量、skip 数、trace 完整度、
  missed-alpha 可疑数和下一步需要的证据。
- `inspect debate-impact` / `inspect guard-impact` 的 human output 增加指向 flow 的软提示；
  JSON 输出不变。
- 更新 `docs/_ai/OPPORTUNITY_FLOW_LEDGER_V1.md` 的使用示例和迁移状态。

### 验证
- `uv run pytest tests/test_opportunity_flow_ledger.py tests/test_debate_impact_audit.py tests/test_guard_impact_audit.py tests/specs/spec_analytics_contracts.py tests/test_architecture_fitness.py -q`
- `uv run aletheia flow audit --hours 24 --limit 5 --stage debate_downgraded --verdict missed_alpha_suspect --symbol BTC/USDT`

### 边界
只读输出增强；不改交易决策、不调 debate 参数、不跑 ranker/guard/risk/execution、不新增 LLM。

---

## 2026-07-04 — Opportunity Flow Ledger V1

### 触发
guard / unsupported / debate / risk / thesis 等诊断分散在不同入口，排查“机会为什么没有变成交易”时认知成本过高。

### 交付
- 新增 `uv run aletheia flow audit --hours 24`，输出最近窗口内每个 opportunity 的最终状态、最终阶段、原因、时间线和已有反事实结果。
- 新增 `uv run aletheia flow trace --opportunity-id <id>`，输出单个 opportunity 的完整只读 trace。
- 汇总复用已有只读审计：Trade Funnel、Debate Impact、Thesis Lineage、Sizing Trace。
- 新增 `docs/_ai/OPPORTUNITY_FLOW_LEDGER_V1.md`，记录 flow 定位和旧 audit 命令的兼容路由计划。
- 空数据库 / 缺运行库时返回 `insufficient_data`，不崩溃。

### 验证
- `uv run pytest tests/test_opportunity_flow_ledger.py tests/test_trade_funnel_audit.py tests/test_debate_impact_audit.py tests/test_sizing_trace_audit.py tests/test_thesis_lineage.py -q`
- `uv run pytest tests/specs/spec_analytics_contracts.py tests/test_architecture_fitness.py -q`
- `uv run aletheia flow audit --hours 24 --limit 5`
- `uv run aletheia flow trace --opportunity-id missing-flow-id`

### 边界
只读重建，不新增 LLM 调用，不跑 ranker，不重跑 guard/debate/risk/execution，不建新表，
不改交易行为；缺字段保持 `insufficient_data`。

---

## 2026-07-04 — Guard Impact Audit 只读诊断

### 触发
System Operator 持续 skip 后，下一步需要判断 active blocking guards 是否正在过度压制候选池，
尤其是 momentum long / momentum short guards。

### 交付
- 新增 `uv run aletheia inspect guard-impact --hours 24`。
- 支持 `--json`，输出 active blocking guards 总数、按 scanner_family / direction / symbol 聚合、
  最近窗口内被 blocking guard 命中的候选、命中 guard 明细、guard 作用范围分类，以及已有
  shadow / forward outcome。
- verdict 只用已有 outcome：`protective`、`overbroad_suspect`、`insufficient_data`。
- 诊断会排除本地已知的 pre-guard skip（例如 TTL 过期、execution_eligible=False），但不会调用
  交易所去判断 demo unsupported symbol。

### 验证
- `uv run pytest tests/test_guard_impact_audit.py tests/test_paper_trader_guards.py tests/test_paper_trader_ranker.py tests/test_trade_funnel_audit.py -q`
- `uv run aletheia inspect guard-impact --hours 24`
- `uv run aletheia inspect guard-impact --hours 24 --json`

### 边界
只读 DB + guard markdown/frontmatter + existing shadow/forward outcomes；不禁用/修改 guard，
不改 ranker / System Operator / risk / execution / live，不调用 LLM / HTTP / exchange。
## 2026-07-04 — Thesis Lineage 链路完整性修复

### 触发
Thesis Lineage V1.1 观察发现：不是所有 `opportunity_id` 缺失都是真断链，有一部分是合法的全局
skip/watch；但另一些确实是需要父机会 ID 的判断链路没有带全。

### 交付
- ranker candidates 和 open positions 的 causal IDs 进入 System Operator context，供确定性归因使用。
- `_attach_opportunity_context()` 支持从 ranker candidate 回填 opportunity，从 close/reduce 的目标持仓回填原始机会。
- 方向冲突或多持仓歧义时保持空，不用 symbol/time 猜测。
- System Operator 已生成的 `bear_case`、`key_assumptions`、supporting/opposing signals 进入持久化链路。
- proposal `decision_meta` 同时写新旧 supporting/opposing 键，Thesis Lineage parser 兼容历史键。
- Thesis Lineage read model 可通过 `proposal_id -> trade_proposals.opportunity_id` 只读还原确定性历史链，并标 `decision_opportunity_id_inferred_from`。
- 审计统计拆出真正要盯的链路缺口：`link_required_missing_opportunity_id`、`global_no_target_count`、`stale_missing_pnl_pct`、`overdue_pending` 等。
- 修复计划文档：`docs/plans/thesis_lineage_integrity_repair_plan.md`。

### 验证
- `uv run pytest tests/test_thesis_lineage.py tests/test_anti_hallucination_e2e.py tests/test_proposal_rationale.py tests/specs/spec_data_model.py::test_pi_decision_carries_opportunity_id tests/specs/spec_decision_lifecycle.py tests/specs/spec_serenity_attribution.py -q`
- `uv run pytest tests/test_architecture_fitness.py tests/specs/ -q`
- `uv run pytest tests/test_doctor_trading_diagnostics.py tests/test_doctor.py::test_deep_diagnostics_includes_log_policy_redacted_tails_and_attention -q`
- `.\scripts\test_ai.ps1`
- `cd frontend && npm run build`

### 边界
只修链路归因和只读审计口径；不新增 LLM 调用、不建新表、不改交易决策、不动 risk/execution/live/credentials。

---

## 2026-07-04 — Thesis Lineage V1.1 观察节奏收口

### 触发
V1.1 不只是有命令，还需要明确每天怎么跑、什么时候判断、修 provenance 后怎么看效果。

### 交付
- `docs/plans/thesis_lineage_v1_1_observation_window.md` 增加 Observation Cadence。
- 当前任务和 handoff 明确：部署后先跑 `doctor --deep --json`，每天跑一次
  `thesis snapshot --days 7`。
- 明确如果观察期内修 provenance，只用修复后新写入的行判断效果，不用修复前历史行否定修复。

### 边界
只改文档；不改代码、不改交易行为、不加 LLM、不加 UI、不建表。

---

## 2026-07-04 — Thesis Lineage snapshot 汇总命令

### 触发
V1.1 观察窗口会连续保存多天快照；需要不用手工打开 JSON 就能扫最近几次缺字段趋势。

### 交付
- 新增 `uv run aletheia thesis snapshots`，只读汇总 `data/diagnostics/` 中最近保存的
  thesis audit JSON。
- 支持 `--json`，输出每份快照的 total、缺 decision、缺 shadow/forward、底层表缺口等关键数字。

### 边界
只改 CLI / 测试 / 文档；不写数据库、不改交易行为、不加 LLM、不加 UI、不建表。

---

## 2026-07-04 — Thesis Lineage 接入 doctor deep 观察

### 触发
V1.1 观察窗口的日常入口是 `doctor --deep`；Thesis Lineage 只在单独 CLI 里容易被漏看。

### 交付
- `doctor --deep` 的 trading diagnostics 增加 `thesis` compact summary。
- summary 只包含总数、缺字段计数、raw table 缺口、main_finding / next_action，不带全量 records。
- `thesis_lineage.compact_thesis_lineage_summary()` 供 doctor 复用。

### 边界
只改只读诊断输出和测试；不改交易行为、不加 LLM、不加 UI、不建表、不触 risk/execution/live。

---

## 2026-07-04 — Thesis Lineage V1.1 snapshot 命令

### 触发
V1.1 观察窗口要每天留本机 JSON 快照；只靠手动重定向容易漏路径或漏字段。

### 交付
- 新增 `uv run aletheia thesis snapshot --days 7`。
- 命令只读生成同一份 thesis audit JSON，默认写入 `data/diagnostics/`，并打印文件路径和关键缺口计数。
- 测试覆盖 snapshot 落盘和 JSON 内容。

### 边界
只改 CLI / 测试 / 文档；不写数据库、不改交易行为、不加 LLM、不加 UI、不建表。

---

## 2026-07-04 — Thesis Lineage V1.1 数据完整性统计内建

### 触发
V1.1 观察窗口需要稳定回答“哪些链路字段缺失”，不能每次靠临时脚本查库。

### 交付
- `thesis audit` JSON 增加 `data_completeness`：统计 lineage 记录能串到 decision / proposal /
  shadow / forward / position 的数量，以及缺 decision、缺 shadow outcome、缺 forward return、
  缺 position / actual PnL 等比例。
- `thesis audit` JSON 增加 `raw_table_completeness`：按当前窗口只读统计 `pi_decisions`、
  `trade_proposals`、`shadow_trades`、`universe_forward_outcomes` 的关键缺字段。
- CLI 人类输出增加 `Data completeness` 区块，方便每天观察 V1.1。
- 测试补到 11 个，覆盖缺字段统计和 raw table completeness。

### 边界
只改 Thesis Lineage 读模型/audit 输出和测试；不改交易行为、不调 prompt、不加 LLM、
不加 UI、不建表、不重启 gateway。

---

## 2026-07-04 — Thesis Lineage V1.1 Observation Window 计划

### 触发
owner 明确要求 Thesis Lineage v1 做完后不要马上升 v2。先跑 7-14 天真实数据，确认现有
交易判断链有没有留下足够完整、可审判的数据；如果断链严重，先补 provenance 和缺字段。

### 交付
- 新增 `docs/plans/thesis_lineage_v1_1_observation_window.md`。
- 下一阶段定义为 `Thesis Lineage V1.1 Observation Window`：跑起来、看数据完整性、修断链和缺字段。
- 明确 V1.1 禁止项：不做新 thesis generator、LLM normalizer、prompt 优化、自动调参、新 UI、
  交易行为改动、新 ThesisRecord 表、决策前历史 thesis 检索。
- 明确运行结果属于本机事实，JSON 快照写入 `data/diagnostics/` 这类 gitignored 运行目录，
  不提交到 git 文档。

### 边界
本批只改计划/交接文档，不改代码、不改交易行为、不重启 gateway。

---

## 2026-07-04 — Thesis Lineage 读模型 + 交易详情 UI

### 触发
owner 要求把 thesis lineage 叠到现有交易因果链上，并且前端 Trades & Positions 的每笔交易详情
已经有 causal chain UI，需要同步显示 thesis 的参与情况和实际/反事实对比。

### 交付
- 新增 `aletheia/analytics/thesis_lineage.py`：只读重建 observation thesis → decision →
  proposal → risk → order/fill/position → shadow/forward outcome 的语义链。
- 新增 CLI：`uv run aletheia thesis audit --days 7`、
  `uv run aletheia thesis records --days 7 --json`。
- 新增 API：`GET /api/analytics/thesis-lineage`，支持按 `position_id` / `order_id` /
  `proposal_id` / `opportunity_id` 过滤，供交易详情 drawer 使用。
- 前端 `Trades & Positions` 的 Order / Position detail drawer 在现有 Causal Chain 下方新增
  `Thesis Lineage` 区块，展示 verdict、actual vs shadow vs batch average、观察假说、决策信号、
  assumptions / bear case 和缺失提示。
- `docs/design/api_reference.md` 登记新 analytics endpoint。

### 边界
本批只读，不新建 DB 表，不改交易行为，不调 prompt/ranker/sizing/risk/execution/scheduler/live，
不调用交易所。LLM 边界更精确地说：生成 thesis 的上游可以用 LLM 或规则/模型；Thesis
Lineage 读模型/审计层不新增额外 LLM 调用，只消费 market_observer / System Operator /
Serenity 已经生成并落库的 thesis / reasoning / evidence。缺字段就标 missing/insufficient_data，
不事后补写聪明理由。前端只展示后端真实返回；没有记录时显示不可用，不编造正向状态。

### 验证
- `uv run pytest tests/test_thesis_lineage.py -q -o cache_dir=.pytest_cache_ai`
- `uv run pytest tests/test_thesis_lineage.py tests/test_trade_attribution.py tests/test_alpha_attribution.py tests/test_trade_funnel_audit.py tests/test_sizing_trace_audit.py -q -o cache_dir=.pytest_cache_ai`
- `uv run pytest tests/specs/spec_api_reference.py tests/test_architecture_fitness.py -q -o cache_dir=.pytest_cache_ai`
- `cd frontend && npm run build`
- `.\scripts\test_ai.ps1`

---

## 2026-07-04 — Trade Funnel + Sizing Trace audit 接入 doctor deep

### 触发
owner 需要把 audit 的诊断能力产品化，用一个主入口回答两个生产问题：为什么信号没有变成足够多交易，
以及为什么开仓 notional 经常很小。

### 交付
- 新增 `aletheia/analytics/trade_funnel.py`：只读统计 opportunity → signal_pending →
  System Operator decision → proposal → risk → order → position 的掉量位置。
- 新增 `aletheia/analytics/sizing_trace.py`：只读追踪 proposal/position/fill 的实际 notional，
  并用当前 sizing 配置做近似解释，明确标记 `reconstructed_from_current_config`。
- 新增 CLI：`aletheia funnel audit`、`aletheia sizing trace`，均支持 `--hours`、`--days`、`--json`。
- `doctor --deep` / `doctor --json --deep` 增加 Alpha / Funnel / Sizing compact summary。
- 新增实现说明：`docs/_ai/TRADE_DIAGNOSTICS_AUDIT_2026-07-04.md`。

### 边界
本批不改风控、执行、agent 决策、prompt、配置、persona、Kelly、杠杆或 live 状态。
doctor 只展示诊断，不自动调参；交易表现问题不变成 infrastructure blocker。

### 验证
聚焦测试覆盖 trade funnel、sizing trace、doctor trading diagnostics。完整命令记录见本次完成报告。

---

## 2026-07-03 — 全项目 SDD 对齐第一批

### 触发
Learning SDD 对齐完成后，按 owner 要求开始把同样的文档对齐方法扩展到整个项目。

### 交付
- 修正规则引擎删除后的 SDD 漂移：`agent_contracts.md`、`knowledge_contract.md`、
  `PI_OPERATOR_DESIGN_DECISIONS.md`、`DOC_ATLAS_BY_DOMAIN.md` 不再把 strategy_designer、
  backtest_orchestrator、RuleSet exit、Optuna 当成当前活路径。
- `api_reference.md` 补近期 model OAuth、settings、risk、operator、analytics 端点。
- 当前 spec 注释/claim 文案统一为 System Operator prompt。
- 新增 `docs/_ai/SDD_GLOBAL_ALIGNMENT_AUDIT_2026-07-03.md`，记录全项目剩余 SDD 扫描任务。

### 验证
- 相关 spec 通过：102 passed, 3 skipped。
- `git diff --check` 通过。

### 注意
全项目 SDD 修复尚未完成；本批只处理第一组高风险漂移。下一步继续 route-vs-doc、
config-to-UI 和 missing/xfail spec 审计。

---

## 2026-07-03 — Learning SDD 文档对齐

### 触发
owner 指出 `discussion.md` 是 Learning 金标准，担心 SDD 文档系统是否准确反映当前项目理解；
要求先在 Learning 范围内处理 SDD 文档，再扩展到全项目。

### 交付
- 重写 `docs/design/learning_contract.md`，清除控制字符污染，并按当前实现重述 Learning 链路。
- 修正 `knowledge_contract.md` 中 proposed/accepted、contradiction stub、time-decay defer、
  guard promotion 路径等旧描述。
- 补齐 `api_reference.md` 中 Learning UI 使用的 lesson/guard/funnel/config 端点。
- 同步 `SYSTEM_MAP.md`、`DOC_ATLAS_BY_DOMAIN.md`、`persistence_recovery_contract.md`、
  `learning_mechanism_research_plan.md`、`product_definition_2026-06-30.md`。
- 更新 Learning 相关 spec 注释，避免把已实现能力继续写成 xfail/defer。

### 验证
- 旧状态关键词搜索无命中。
- `git diff --check` 通过。
- Learning/Insight spec 通过；worktree 无运行态 DB 的数据依赖项按现有逻辑跳过。

---

## 2026-07-03 — Learning draft skill producer 与 memo 半衰期

### 触发
discussion 后续复核发现 Learning 闭环虽然已经能从交易案例进入 lesson/policy，但长期
`ps_skills` 候选没有稳定生产者，旧 trading memo 也缺少自然衰减机制。

### 交付
- `lesson_reviewer` 在 lesson 被应用后，生成 draft skill 候选。
- `SkillStore.create_from_lesson()` 按 lesson id 幂等，避免重复 reviewer 运行复制技能。
- draft skill 默认不启用，只允许进入低风险 advisory 层；不触碰 risk/execution/live/credentials/order。
- `memo_half_life_weight()` 使用 7 天半衰期给旧 memo 降权。
- System Operator memo prompt 显示 `Memo time weight`，并把 regime playbook 的胜率边缘做时间折算。
- Learning 状态报告和设计契约同步更新。

### 验证
- Learning 存储链路相关测试通过。
- Learning/Insight spec 通过，原 time-decay xfail 已解除。

---

## 2026-07-03 — verbose 日志与 doctor 深度诊断

### 触发
owner 担心 MVP/Round 1/Round 2 阶段系统日志不够详细、doctor 命令不够适合实时排障；
要求先按 verbose 思路保留更多问题现场，日志按 50MB 分段，暂不急着删除历史日志。

### 交付
- 文本日志轮转改为 50MB 分段、多代数字备份，便于保留更多现场。
- backend/frontdoor/foreground uvicorn 日志级别上调，诊断期优先可观测性。
- doctor 增加 `--verbose` / `--deep`：输出异常检查、相关日志提示、日志清单、近期失败任务、近期审计摘要、脱敏日志尾部。
- `doctor --json --verbose` 增加结构化 `diagnostics`，供 AI 和脚本做交互式诊断。
- `doctor --bundle <file>` 增加脱敏诊断包落盘，方便故障复盘或交给 AI/人排查。
- `gateway logs` 从单看 `gateway.log` 改为多路 operational logs 尾部：gateway/backend/frontdoor/operator shell。
- 日志尾部读取增加敏感字段遮罩，避免诊断输出直接泄露 token/secret/authorization。
- RUNBOOK / DEPLOYMENT 补充 verbose doctor 和日志保留规则。

### 验证
- 日志与 doctor 定向测试通过。
- 安全测试通过。
- `doctor --json --verbose` 可解析且包含深度诊断字段。
- `doctor --bundle` 可写出可解析 JSON 文件。
- `gateway logs -n 2` 可输出多路日志尾部。
- `fast -n 4` 并行快层通过；顺手修复 `paper_trader` 调度门对极简测试配置桩过脆的问题。

### 注意
本批不改交易执行、风控、密钥、实盘开关。工作区里同时存在其它会话的历史数据下载、
交易决策和审计链改动，提交时应只纳入日志/doctor 边界文件。

---

## 2026-07-03 — Runtime 通讯通路拆分与 dashboard 稳定性

### 触发
owner 反馈页面顶部加载条常驻 90%、BTC/ETH/SOL 顶栏显示 stale feed、前端经常报
backend unreachable；要求按事务重要性拆分前后端通路，并参考 OpenAlice 等 agentic
trading 项目的实时/后台通讯经验。

### 交付
- 连接探活改为轻量 `/api/ready`，backend guard 不再用 `/api/status` 作为在线判断。
- backend guard 10s 轮询、连续 3 次失败才通知，降低短暂慢请求/重启窗口造成的噪声。
- 顶部 FetchProgress 忽略后台轮询和长期 refetch 请求，避免常驻 90%。
- ticker 三个 symbol 查询标记为后台请求，仍诚实显示 stale，但不再驱动全局加载条。
- 健康汇总读取缓存化；请求路径不再直接跑交易所外部探测。
- OKX/CCXT `load_markets()` 增加坏 market id 过滤，避免 `id=None` 行导致市场刷新中断。
- recommendations 作为 advisory/background 通路做 15s 短缓存。
- 新增 `docs/_ai/RUNTIME_COMMUNICATION_LANES.md`，固定五类通讯通路和后续扩展规则。

### 验证
- Runtime/frontend 通讯相关 55 个测试通过。
- Operator recommendations 相关 32 个测试通过。
- Frontend build 通过。
- `aletheia doctor` 通过 gateway/frontdoor/backend 基础检查；剩余 warnings 属于既有运营/数据质量项。

---

## 2026-07-03 — pytest 分层与 AI 快反馈链路

### 触发
owner 要求把测试从“全量慢跑”改成更适合 AI 修 bug 的分层反馈：先找慢测，再标
fast / integration / e2e / slow，默认只跑快层和上次失败，最后再上并行。

### 交付
- 新增 `fast` / `integration` / `e2e` / `slow` marker；保留 `tier_*` / `domain_*` 旧标签。
- `scripts/test_ai.ps1` 作为 AI 默认命令：先 `--lf`，再 `-m fast`。
- 引入 `pytest-xdist`，仅作为手动最后阶段，不写入默认 `addopts`。
- 修复 e2e conftest 全量误标 bug；修复 retention 测试对外部配置状态的隐式依赖。
- 更新 `docs/operations/PYTEST_OPTIMIZATION_GUIDE.md`，补慢榜、命令、并行边界和核心规则到测试映射。

### 验证
- 分层自检 9 passed。
- `fast -n 4` 并行通过：1188 passed, 1 skipped。
- 全量非并行通过：2267 passed, 7 skipped, 7 xfailed。

---

## 2026-07-03 — discussion 批次开发经验沉淀

### 触发
owner 要求把本轮 20+ worktree、多分支、多模块修复过程里的经验和教训写成本地开发文档,
让后续 AI 继续开发时能直接看到,避免重复踩坑。

### 交付
- 新增 `docs/_ai/DISCUSSION_DEVELOPMENT_LESSONS_2026-07-03.md`。
- `DEV_PROTOCOL.md` 加大批量修复经验入口。
- `AI_WORKFLOW.md` 的 Context Recovery Protocol 加该文档。
- `AI_HANDOFF.md` 和 `CURRENT_TASK.md` 加短入口。

### 内容要点
- 长文先拆成 Dxx 可验证清单,审计和修复分支分开。
- 性能先量化再修 top offender;本轮 recommendations 重复 artifact scan 6 -> 1。
- 前端源码更新后必须 build 静态包并重启 gateway。
- 合并后要清理 worktree、已合并分支、未登记旧目录。
- owner 决策项不能伪装成 bug fix。

---

## 2026-07-02 — 动态 sizing / persona / learning 系统 P0–P4

owner 方向落地:Operator 在风控硬边界∩persona区间内动态决仓位/杠杆/加仓/止损 + Learning 拟合 +
渐进解冻。全默认 OFF(零行为变化)。设计 `docs/design/dynamic_sizing_operator_persona_design.md`。

- P0 `3e8015f` active_broker_view_complete 视图完整性门(修 #1 权益基准 + #2 thrash 变体)
- P1 `ad580a1` persona 区间 schema + persona.py resolver seam(conservative==扁平配置零变化)
- P2 `9c27dc4` build_sizing_context→risk_appetite;operator_decidable 参数上下文决策
- P3 `3fbdd79` sizing_policy regime 干净窗奖励学习 + doctor loop.sizing_policy
- P4 `884e035` 渐进解冻 ladder(distress refreeze)+ 金字塔 pyramid_plan(硬顶 clamp)
- specs PS-S01..S12;conformance 522;full suite 1817/0
- 未接:金字塔实盘下单(owner-gated,会 place orders)
- 顺带:PR#35 费率漂移 6 用例对齐(d8aedeb+149774d,只改测试)

---

## 2026-07-01 — reconciler 假孤儿 thrash 根因修复 + Scorecard 去污

### 触发
监控抓到做空仓批量 reconciled_gone 平仓 + 相同价 adopted 重开 → thrash 循环。

### 根因
非 Bitget 账户上 classify_positions 在 unified 缓存过期时回退读空的 legacy Bitget 缓存 →
误判全部 OKX 持仓 orphaned → 本地平(不动交易所)→ 重认领 → 无限 thrash。41% 仓位
reconciled_gone、56% adopted、strategy 归因被毁(10/110 真实平仓带 strategy_id)。

### 修复(commit 5d6bb89,SDD)
- P0 classify_positions 回退 broker 感知(非 Bitget+unified 过期 → bail);PL-ADOPT-10/11/12
- P1 go_live_scorecard 排除 synthetic 平仓(新建 analytics/close_reasons.py);AN-S03
- 穷尽排查确认缓存错配只坑 reconciler 一处

### P2(owner 授权已实施,da38fbf)
- P2-1 OKX 交易所保护单(OKXClient.place_algo_order + stops 分发器 + adopt 调用;双门控
  exchange_tpsl_enabled 休眠 + demo-only);PL-ADOPT-13/14
- P2-2 adopt._recover_strategy_link 归因恢复(P0 已解根问题,此为防御性;紧配不误归因);PL-ADOPT-15/16
- P2-3 factor_feedback 过滤 synthetic 平仓;LP-S15
- conformance 505 全绿;full suite 1815/0;gateway restart live

### 验证
conformance 500 全绿;full suite 1815/0;真配置 stale 路径 0 false-orphan;doctor healthy。
报告 docs/research/ENTRY_EXIT_ASYMMETRY_ANALYSIS_2026-07-01.md。

---

## 2026-07-01 — Phase 2A+ 剩余四项收口（2A/2A+ 全 CLOSED）

### 触发
owner「尽量把 2a 完全完成,不要再遗留任何项目」。四项 PARTIAL/PENDING 一次做完。
commit `f093e36`（实现）+ `8cea488`（文档）,push master。

### 关键实现
- **2A+.2**：`_derive_regime_targeting(direction)` 推导 direction_affinity + applicable_regimes。
  **陷阱**：applicable_regimes 须写 **JSON 数组**——ranker 用 `json.loads`+`isinstance(list)` 解析,
  逗号串被静默丢弃使过滤失效。primary + sweep-variant 两调用点都传。SP-S04 升 L2b。
- **2A+.4**：`types.ts` + `StrategyLifecycle.tsx` Direction 列/drawer（`fmtRegimes` 解析 JSON）。
- **2A+.9**：`family_quota_by_regime` + `resolve_family_quota()` + allocator/service 消费 +
  Settings UI 每 regime 逐行 sum=1.0 双校验。UP-16 L2b。
- **2A+.11**：`score_volatility` 接 atr_4h/bb_width_4h 4h 多 TF 确认。UP-15 L2b。

### 不做（有理由）
2A+.10 端到端 short 验证 = 运行观察项（非代码）,需 bear/volatile + bull 真实出现,证据留 `data/observation/`。

### 验证
conformance 493→496 全绿；full suite 1815 passed/0 failed；build 通过；doctor healthy。

---

## 2026-07-01 — Scanner + Observer 审计修复 P0–P3

### 触发
两份审计报告（Universe Scanner + Market Observer）→ plan → 实施。核实 workflow 因 workspace rate-limit 全灭，改手动核实，查出报告 6 处根因偏差。分支 `fix/scanner-observer-audit-fixes`，spec `UP-01~13` GREEN。

### 关键修复
- **US-4（最高杠杆）**：`service.py:256` forward-outcome direction 用 `scanner_direction` 同源 + `_infer_direction` 加 event 分支 → 一处修复 momentum 54% None + event 0 条 + 胜率统计双重失真。
- **US-1**：`compute_total_score` 消费 `scanner_weights`（PI 手调此前无效）。
- **US-3/8**：perp market_type 传递 + feature 复合 key。**US-9**：short 硬禁不被高分绕过。
- **US-5/6**：BB squeeze O(n²)→O(n)；price_precision tick-size 保真。
- **MO-1**：key_observations 进 layer1。**MO-2**：per-symbol guard（闭合断口 F）。
- **MO-3/4/6/7/8/9/11/12/13/14**：F&G 填充 / scanner_family hint / RS rank 单symbol省略 / crowding n/a / fallback DEGRADED / news 48h / prediction 流动性 / load_config 移出循环。

### 不做（有理由）
US-7（读侧已 fallback）/ US-12·US-16（无消费者·增强 → ROADMAP）/ MO-5·10·15（已覆盖 / 收益微）。

---

## 2026-07-01 — 双层模型路由上线 + System Operator 重命名 + earendil-works/pi 研究

### 触发

接续上一会话：完成双层模型路由功能；研究 earendil-works/pi 是否可复用；owner 指示
全面统一"PI Agent"→"System Operator"术语，并完成文档同步。

### 双层模型路由（commit e5a8010）

**问题**：所有 agent 共用单一 `default_provider`，顶层决策与批量提取无法分流到不同模型。

**实现**：
- `ModelsConfig` 新增 `deep_provider` / `fast_provider`（`None` → fallback default）
- `get_provider_for_tier("deep"|"fast")` 翻译层（`model_provider/router.py`）
- `llm_complete` / `llm_structured` / `llm_stream` 加 `provider=` 参数
- deep 层 11 个 call site（trading_decision / strategy_designer / risk_critic / market_observer / dreaming_agent / lesson_reviewer）
- fast 层 5 个 call site（research_worker 全部批量 LLM 调用）
- 前端 Routing Role 下拉 + 路由摘要行
- SDD: 5 个 MR-* claims GREEN

### earendil-works/pi 研究结论

核查 `operator-shell/package.json`（无 `@earendil` 依赖）和 `pi_runtime_adapter.ts`（调
Aletheia Python API，不调外部 npm）确认：earendil-works/pi 是 coding CLI，与 Aletheia
operator 无关。operator-shell 完全自建。决策详见 `DECISIONS.md §2026-07-01`。

### 术语统一

活文档统一"System Operator"；`pi_daemon.py` 文件名保留；历史归档文档加头部标注。

---

## 2026-06-29 — OpenAlice TaG 深度研究 + P0-1 hash 基础设施 + P1-2 reconcile audit 链入

### 触发
owner 让研究 https://www.openalice.ai/blog/trading-as-git-intro，并把"能做、有必要做的"全部生成 plan 并实施。会话末追问 https://www.openalice.ai/blog/traderalice-cursor-for-vibe-trading 是否值得借鉴；最后让把文档全量同步。worktree 分支 `worktree-openalice-tag-research`。

### 研究（13-agent workflow，797k tokens / 8.5min）
三层 ground truth：博客全文 / 本地 `_refs/OpenAlice` 真实 TS 源码（`services/uta/src/domain/trading/git/TradingGit.ts`）/ Aletheia 自身对照。5 镜头分析（概念映射 / 架构机制 / 价值主张可信度 / 批判与边界 / Aletheia 对比借鉴）+ 5 轮对抗核查。

**核心发现**：TaG = "工程上扎实的 AI 交易日志系统 + 版本控制比喻的过度包装"。Git 比喻在 revert / branch / merge / Merkle 链上全面失效（成交无 revert、代码只有线性单链、hash 是 SHA-256 截断非 Merkle 树）。博客"1 月崩盘预判"自标注是 backtest，逻辑自相矛盾，零证据值。**真正值得借鉴的两个工程纪律**：① reject 是一等审计记录、② 执行后账户全量快照绑到决策记录。

对抗核查抓到 6 处脑补（含我自己的几条），全部并入正文。报告落 `docs/research/OPENALICE_TAG_DEEP_DIVE_2026-06-29.md`。

### P0-1（commit `b8ed990`）— TradeProposal hash chain（最小可行起手项）
不触 trust boundary 的 hash 基础设施。`models.py` TradeProposal 加 `commit_hash` + `parent_hash`（均 `Optional[str]`，default `None` 向后兼容）+ `migrations.py` `_ADDITIVE_COLUMNS` 注册两列 + `idx_proposals_commit_hash` 索引 + `repositories.py` 新增 `_compute_proposal_hash()`（SHA-256 of canonical JSON of immutable fields 截断 16-char hex）+ `insert_trade_proposal()` 在事务内自动填且链入（全局单链，按 rowid DESC 选 parent）。SDD：DM-S08/S09（L2a）+ DM-B06（L2b）+ DM-C02（L2c：re-compute 确定性 + 链完整性 + **status 免疫断言**——pending → executed 后 commit_hash 必须不变）。

**关键设计**：hash 输入排除 `status` + `commit_hash` 自身；16 字符（OpenAlice 用 8 / 43 亿空间，Aletheia 多 broker 多 strategy 全局单链加宽到 64-bit）；全局单链非按 broker 分链（简单 + 与 OpenAlice 一致）。

验证：4 个新 spec RED→GREEN；全量 1802 passed / 3 skipped / 0 failed 零回归；conformance 359→360 / 99% 覆盖维持；doctor `trust.audit_chain` 通过。

### Vibe Trading 帖（#4）交叉核查（commit `2359971`）
源码 grep 验证 "auto-learn effect" 命中**零**——纯营销话术。三个具体能力中 News Retrieval 已有等价物（`news_items` 表）、LLM 写 Pandas 踩 §4 红线（agent 生成可执行代码）、ReAct + chat-driven 与 trust boundary 冲突。**结论无新增可落地借鉴项**。研究报告加 §八点五交叉核查节避免未来会话重复研究。

### P1-2（commit `31eb1a9`）— Reconcile audit 链入
4-agent explore workflow 先核查 A=FORBIDDEN_INTENTS hardening（owner 让先做）—— 意外发现 `deactivate_kill_switch + modify_permissions` **早在 2026-06-23 就加进** `registry.py:415-426` 并配 Defense-in-depth 注释，OL-C03 spec 已 `status="implemented"` 无 xfail，BACKEND_ISSUES §15.1 也已标 ✅。**A 是 no-op**，无新代码。MASTER_FIX_LEDGER 没列、所以没"过时标签"要修（typical of memory `aletheia-backend-issues-archive-unreliable`）。

B 走 SDD：在 `spec_pipeline_adopted_position.py` 加 PL-ADOPT-09（L2b）；RED 信号清晰（`audit after_state.commit_hash None must equal proposal.commit_hash '4fe0133763e952bd'`）；在 `position_reconciler.py:372-379` 的 `write_audit` after dict 加 `commit_hash` + `parent_hash` 两键（直接读 insert_trade_proposal 事务内填充的 proposal 对象，无需重算）；强化测试也覆盖 `parent_hash` key 存在性（即使值为 None，区分"genesis 链首"和"忘了写"）。GREEN。

**价值闭合**：P0-1 给提案打了内容指纹，P1-2 让 reconcile audit 第一个用上这个指纹。审计消费者从此单看 `audit.jsonl` 行就能还原 hash 链关联，不用回 DB 查 `trade_proposals`。

验证：9/9 PL-ADOPT passed；邻近 57 spec passed；全量 1802 passed 零回归；conformance 360→361。

### 文档全量同步（本 commit + commit 67ed379）
5-agent parallel workflow 扫 5 层金字塔（L1 VISION / L2 design 契约 / L2.5 SDD / L4 _ai status / L4 issues ledger）+ 1 个 adversarial verifier。verifier 抓到 5 处问题（claim 计数误用 stale baseline、scanner 输出被 schema 截断、L2-data-model + L2-SDD 重复提议、PL-ADOPT-09 anchor 不清、漏掉 spec_pipeline_adopted_position / spec_audit_chain 没进 README spec 表）。亲自核 grep 验证 spec_data_model.py 实际 18 claims（scanner 误用 8），重写截断内容，并去重整合。

L2 同步（commit 67ed379）：data_model.md + audit_chain_contract.md（新增 §6.4 提案级 hash chain + §7.3 doctor 探针 gap + §6.3 表加 position_adopted 行 + 顺便修 stale 行号 :415-419 → :415-426）+ trust_and_change_control.md §1.1 末尾 + design/README.md spec 表纠正与补全。

L4 同步（本 commit）：DEVLOG + AI_HANDOFF + CURRENT_TASK + DECISIONS prepend；docs/references/INDEX.md §3 加新研究报告；docs/references/openalice_prism_review.md 标 P0-1 / P1-2 ✅；新建 docs/issues/TAG_BORROW_BACKLOG.md（4 项剩余借鉴 P0-2/P0-3/P1-1 + Decimal/risk_critic stats/state-driven orchestration 状态卡）。

### 当前状态
worktree `worktree-openalice-tag-research` 待 owner 审核 merge：5 commit（b8ed990 P0-1 + 2359971 Vibe 核查 + 31eb1a9 P1-2 + 67ed379 L2 docs + 本条 L4 docs）。memory `aletheia-tag-borrow-plan` 已同步落地状态。后续 P0-2（state_after 快照）/ P1-1（reject 同构）触 trust boundary，需 owner 显式批准；P0-3（Guard 责任链拆分）触 risk/engine.py，红线最严。

## 2026-06-25 — proposal rationale 双重截断刈断 meta JSON(没搞全 + 弱代码外泄)

### 现象
用户在 proposal detail 看 rationale "没搞全"(thesis 半句断)+ "夹杂弱代码"
(原始 `---{"conviction":64,...` JSON 串直接显示给人)。

### 真因(亲核)
`agent_shell/trading_decision.py` 拼 rationale = `f"{reasoning[:200]}\n---\n{json.dumps(meta)}"`
再 `[:500]`。两层截断:reasoning 砍 200(thesis 断句)+ 整串砍 500,而 meta JSON
~1.1KB → `[:500]` 把 JSON 刈成非法串。实测 5/5 条 agent_rationale 恒=500、JSON 全
`Unterminated string`。前端 parseDecisionMeta 的 JSON.parse 必失败 → catch 把整条
原始串(含碎 JSON)当 text 显示 = 弱代码外泄。两症状同一根因。

### 修复(冻结例外 #5,用户授权,双层防御)
- 后端 trading_decision.py:`_MAX_RATIONALE_TEXT=600` 只截 human thesis(带 `…`),
  **删整串 `[:500]`** → JSON 永远完整。agent_rationale 列是 TEXT 无限制。
- 前端 Proposals.tsx:parseDecisionMeta catch 返回分隔符前干净 text;
  ProposalRationaleDetail/ProposalRationale no-meta 分支渲染 text 非 raw → 坏值
  也永不向人倒 JSON(防御纵深,保证不复发)。
- `tests/test_proposal_rationale.py`(3):JSON 必可解析 / thesis 单独截断非整串 500 /
  短 thesis 不动。

### 判决影响
记 EXPERIMENT_PROTOCOL 例外 #5。**PI 输入信号变化**:agent_rationale 的 meta JSON
被 reasoning_feedback.py 解析出 scanner_family → 喂回 PI prompt。修复前 JSON 全损坏
→ reasoning_feedback 一直 "unknown";修复后首次成功提供真实 scanner_family = PI 多
一个此前缺失的输入维度,阶段评估区分修复前/后。

### 根治理念
根除"human text + machine JSON 拼单串再截断"这个脆弱模式本身(任何 char cap 都会
刈 JSON),而非调大数字。后端保证 JSON 不被切 + 前端保证坏值不外泄 = belt-and-suspenders。
全 risk/agent_shell 冻结链 128 测试通过。

## 2026-06-25 — 回撤刹车误用持仓 notional 当 equity → $0 cap 锁死 demo 开仓 (70a7b64)

### 现象
用户发现 OKX Demo 一整批期货 Short 被拒,拒因 `Position size over cap — $21.72 > $0 cap`。
$0 上限 = 任何仓位都开不了 → 整批无差别拒绝(22h 内 144 笔误拒)。

### 真因(亲核,非推理)
`risk/engine.py:_portfolio_drawdown_scale`(检查 #5 回撤缩放)按 `proposal.execution_mode`
(okx_demo)过滤 `portfolio_snapshots`,拿到**每个 mode 自己的行**,其 `total_value_usdt`
是持仓 **notional**(Σqty×price)而非账户 equity(`pnl.py:373` 写入)。13:53-14:03 五笔
止盈平仓(净赚 +12),持仓 notional 从 ~$272 collapse 到 $16.5 → drawdown 误算
`(277-16.5)/277=94%` → scale=0 → `effective_cap=26.84×0=$0` → 所有提案被拒。
**赚钱平仓反而把自己锁死。** 真实账户(`exchange_account` 行)全程稳定 ~$88,400,无回撤。

### 修复(70a7b64,trust boundary,用户授权,走 SDD)
engine.py:356 交易所模式(okx_demo/*_demo/live)改读 `exchange_account` 行真实 equity;
`paper_internal` 无交易所账户、mark-to-market `total_value_usdt` 即 equity,保持原路径。
- `spec_risk_engine.py`:+RE-B05a 回归(notional 272→16 但 equity 平稳 → scale 必须=1.0);
  RE-B05 分段数学保持绿。
- `risk_engine_contract.md`:关卡 #5 注明回撤数据源 = exchange_account equity。

### 判决影响
记入 `EXPERIMENT_PROTOCOL.md` 例外 #4。**关键**:此 bug(6-24 17:25→6-25 15:18 UTC,~22h)
人为压制了交易样本——Round 1 G6 样本量低(T+2.6 天仅 17 笔)有相当部分归因于此 infra
误拒,非 PI 不愿交易。该窗口"被拒"不计入"PI 决策质量"评估。

### 生效验证
重启后 okx_demo scale=1.0、cap 恢复 $26.84;risk_decisions 15:22/15:24/15:27 连续
approved=1 空 violations(此前 15:18 仍 `...>0.0` 误拒)。全 risk 测试 56 passed。
同属 notional/equity 混淆家族(余额读错=例外#1;回撤口径=本次)。

## 2026-06-24 — 进程级 SelectorEventLoop 根治 Windows IOCP 顽疾 §17/§23/§24 (5bfa4a0, fix 分支)

### 现象
体检发现系统半瘫:Web UI 卡死 3h45min(frontdoor HTTP 不响应但 TCP 仍 listen,
看门狗 TCP-only 检测误判"还活着"不重启)、trading loop 因 LLM provider 间歇性
不可用被 startup_gate 自动暂停。errors.log 持续刷 `ProactorEventLoop IOCP drain
teardown hung 255s`(§17)+ frontdoor `WinError 64` 周期性崩溃(§23/§24)。

### 真因
整个 `aletheia/` 未设 `WindowsSelectorEventLoopPolicy`,所有 `asyncio.run()` 与
uvicorn server loop 默认 ProactorEventLoop(IOCP)。Windows 上 ProactorEventLoop
关闭时 IOCP drain 挂起 + AcceptEx overlapped 操作产生 WinError 64。
`scheduler._run_async_with_timeout` 此前只局部用 SelectorEventLoop,覆盖不到
broker `_run_coro` / `exchange_sync._run_async` 在另一 worker 线程另起的裸
`asyncio.run()` —— 这才是 §17 线程泄漏的真正来源。

### 修复(5bfa4a0)
进程级设 policy(覆盖全部裸 asyncio.run,修 §17/§24)+ uvicorn 子进程加
`--loop none`(loop_factory=None → 回退标准 asyncio.run → 遵循进程 policy 而非
uvicorn 硬编码的 ProactorEventLoop 工厂,修 §23)。
- `async_utils.py`(新):`apply_windows_selector_policy()` 幂等 helper。
- `server/app.py` / `gateway/frontdoor.py` / `main.py`:import 顶部调用(三进程)。
- `_gateway_runner.py`:两处 uvicorn cmd 加 `--loop none`。
- `scheduler.py`:既有 workaround 加注释(逻辑不变)。
- `tests/test_event_loop_policy.py`(新):真实子进程断言 policy 生效 + `--loop none`
  契约回归守卫。

### 关键实证(纠正 agent 推断)
- uvicorn 0.48.0 `LOOP_FACTORIES` 含 `"none": null`;`--loop selector` 不存在
  (传了会崩);`--loop asyncio` 在 win32 仍返回 ProactorEventLoop。亲测
  `Config(loop='none').get_loop_factory()` 返回 None(exit 0)。
- SelectorEventLoop 唯一破坏路径 = claude_cli/codex_cli 的 8 处
  create_subprocess_exec,但二者 config `enabled=false`,当前安全。已在
  async_utils.py 留显著警告:启用前须改这 8 处为线程内同步 subprocess。

### 安全边界
未触碰任何实验冻结路径(AI 决策链);aiohttp/httpx/ccxt 均纯 socket I/O 兼容
Selector。改动隔离在 `fix/windows-selector-event-loop` 分支,未动 master。

### 待验证 / 遗留
- 修复运行时效果由后台监测确认(新进程 errors.log 不再出现 IOCP/WinError 64)。
- 顺带发现但未修(超本轮 scope):(1) backend 冷启动 ccxt load_markets 耗时
  20-30s > launcher 15s readiness 窗口,导致 LLM 故障期 `gateway start` 易报
  "readiness did not pass" 假失败(进程其实在后台继续起);(2) LLM provider =
  Azure Databricks AI Gateway 端点侧间歇性超时(host/网络层正常,401/可达性
  1.9s),非本机可控。

## 2026-06-24 — Equity Curve 误报 + 诊断代理陷阱(dbde229 commit body 勘误)

### 现象
用户报 Dashboard 显示 "Equity Curve / No trades closed yet — curve appears after
the first position closes",但 DB 里有 68 笔已平仓(realized -21.36 USDT)+ 3
笔持仓。空态文案与事实矛盾。

### 真因(两层叠加)
1. **前端 bug**:`PnLChart.tsx` 的 `useQuery` 只解构 `data`/`isLoading`,**未
   处理 `isError`**。请求失败时 `data===undefined → points=[] →
   chartData.length===0`,直接走"还没平仓"的空态分支 —— 违反"onError 必须处理"。
2. **触发外因**(诊断时归因被误判,见下方勘误):用户浏览器实测访问 localhost
   完全直连无碍。最初看到空态最可能是 gateway 处于 churn 期端点偶发失败 +
   PWA 缓存了无 isError 处理的旧 bundle。

### 修复(dbde229)
PnLChart 新增 `isError` 分支:红色 "Failed to load equity curve." + Retry 按钮
(`refetch`),与真零交易空态区分。后端 `/api/analytics/equity-curve` 完好
(days=1/7/30 均返回完整 points)。重建并提交 dashboard_static。

### 勘误 — dbde229 commit body 的代理归因错了
原 commit body 写"浏览器走系统代理被转坏返回 502"、建议用户"在代理软件
绕过列表加入 127.0.0.1/localhost"。**这两句都是错的**,用户当场指出后实测纠正:

- **WinINET 系统代理**(浏览器走的):`HKCU:...\Internet Settings\ProxyEnable=0`
  (关闭),且 `ProxyOverride` 已含 `localhost;127.*;<local>`(DIRECT)。浏览器
  访问 `127.0.0.1` 不经任何代理。
- **环境变量代理**(curl/CLI 走的):`HTTP_PROXY=HTTPS_PROXY=http://127.0.0.1:7897`,
  `NO_PROXY` 空。curl 默认会无脑把 localhost 也丢给代理 → 误返回 502/000。**我**
  诊断时中招后,把 curl 假象错误地外推成"浏览器/后端故障"。

记忆 `aletheia-localhost-probe-proxy-trap` 已写入,以后诊断:
- curl 探活 localhost **一律加 `--noproxy '*'`**;
- 验证浏览器体验用 `Invoke-WebRequest`(走 WinINET);
- 后端死活以 `aletheia gateway status` + `/api/health` 的 `status` 字段为准,
  不以"curl 是否 502"为准。

不 amend dbde229(避免改写历史),勘误以本条 DEVLOG 为准。

---

## 2026-06-23→24 — 最小赚钱循环全链路修复 + SDD 扩张 + 代码审计

### 背景
用户观察"系统不怎么开仓"。逐层排查发现最小赚钱循环(opportunities→paper_trader→
signal_pending→PI Agent→proposal→risk→execution)被**五层叠加阻碍**卡死,20h+ 零开仓。
每修一层暴露下一层——典型的"修复 A 暴露 B"连锁。

### Phase 1 — OKX contractSize 单位混淆 (650f608)
CCXT swap `amount`/`fetch_positions.contracts` 单位是合约张数非币数(TRX=1000/ETH=0.1/
BTC=0.01)。下单放大 contractSize 倍、绕过风控 notional;mark-to-market 算错。
okx/client.py 边界换算 coins↔contracts。清理 3 笔被放大的历史错误仓位。

### Phase 2 — PI 余额幻觉破产 (6c006cb, 冻结例外#1)
Phase 4a 后 PI Agent(LLM)是唯一 proposal 创建者。它被喂 8 个错误事实构建"账户濒临
破产"幻觉 → 100% skip:余额读 bitget 闲置 $167(真实 okx_demo $88k)/ 7d 统计被
units_bug_cleanup 污染(-13.46 vs +1.94)/ cfg.risk.kill_switch_active AttributeError →
risk block 空 / 策略过滤 status-vs-state typo → 0 活跃策略 / win_rate 46.6% 被 LLM
误读为 -45.8% drawdown / long_bias 读如方向否决 / debate 公式丢弃 initial conviction。
trading_context + trading_decision 逐项修(冻结例外,用户授权)。

### Phase 3 — OKX 跨 loop 回归 (d6fc427)
Phase 1 引入的 `_contract_size` 调 `load_markets` 在 `_run_coro` 新线程新 loop 跑,
但 OKXClient 在 __init__(同步上下文)eager 建 aiohttp session → 跨 loop Future 冲突 →
load_markets 失败 → contractSize 回退 1.0 → Phase 1 修复在 broker 路径静默破功。
OKXClient 改 lazy-init:`_ensure_exchange()` 在首次 async 调用所在 loop 建。
回归测试 TestLazyExchangeInit 钉死"__init__ 不建 exchange"。

### Phase 4 — 日损 $1 误熔断 (5831bd1, CR-RE-03, SDD 流程)
engine.py 检查 #8 日损门控对所有模式用 `max_live_limited_cap_usdt`($100)作基数 →
demo 日损限 $1。Phase 1 清账 -23.26 损失触发 demo 日损熔断,阻断当日 PI 全部交易。
SDD 流程:RE-B13 RED → engine.py:154 按 _is_live 分流(demo 用 starting_capital_usdt,
对齐 check #13)→ GREEN。config starting_capital 100→88000(日损限 $1→$880)。

### Phase 5 — signal 重复→"数据损坏"skip (edc15b2 例外#2 + 079bb9d)
paper_trader Path B 写 signal_pending 后只对被跳过的标 reviewed,eligible 漏标 →
同一批 open opportunity 每 5min 重复写入 → PI 读重复(ETH short 12 次)判
"pipeline data integrity compromised"。paper_trader.py:153 加
update_opportunity(reviewed);清理 1676 条历史重复;新增 prune_pi_decisions(7d,
action 白名单保护 trade/close/recommend_promote)防再堆积。PL-PT-05 xfail→pass。

### 结果
五层全清 + gateway 稳定后,PI 02:00+ 产出 5 trade/2 skip,开出 3 笔真实持仓
(ETH/SOL/BTC short),notional 规格正确,integrity 借口消失。最小赚钱循环打通。
doctor loop.profit ✓ healthy。全量 1651 tests passed。

### 附:SDD 扩张 + 审计 + code review
- SDD: spec claims 45→114,新建 4 spec+3 契约,修 12 契约漂移(DOC_CONTROL_ANALYSIS)
- 审计: 16 契约+7 管道+git 挖掘 → 74 findings(CODE_AUDIT_REPORT),已修 trust 项
- code review(11 角度): 修 2 个 get_cached_state 遗漏,否决 6 误报,新发现 OP-WINRATE/APP-HMAC
- 监测固化: doctor loop.profit 探针 + spec_pipeline_profit_loop.py
- 冻结例外 3 次均登记 EXPERIMENT_PROTOCOL;Round 1 起点保留 6/22,修复前数据不计入

---

## 2026-06-21 — 数据质量自检 + PI Agent 自愈闭环 (5e5f4fe)

### 背景
Universe signal 24h win_rate 只有 36.7%（低于40%阈值），但系统对此无感知。
Doctor 只检查进程/网络/配置，不检查数据积累和信号有效性。PI Agent 不了解自身数据环境。

### Phase A — Doctor OHLCV/Signal probes
- `_probe_ohlcv_coverage`: 检查所有 active members 是否满足 min_bars_for_backtest(200)；不足 → warn + hint backfill
- `_probe_signal_effectiveness`: 过去7天 24h horizon forward outcomes win_rate；<40% → warn signal degraded
- `aletheia audit data [--json]`: per-symbol×timeframe bar count 矩阵 + signal effectiveness
- `aletheia data backfill`: 批量下载所有 active members OHLCV

### Phase B — PI Agent 自检+自愈
- `trading_context._get_data_health_summary()`: 新函数，返回 {ohlcv_ready, signal_status, win_rate, coverage}
- Layer 1 自动注入 `data_health` 字段；PI Agent 每轮决策都感知数据环境
- `_build_assessment_prompt()`: signal_status=degraded → 注入 ⚠ SIGNAL DEGRADATION 警告；ohlcv not ready → 保守提示
- `commands.evaluate_trading_opportunity()`: 决策后触发自愈 — degraded → pause_low_conviction；缺数据 → request_backfill（写 system_action knowledge item）

### Phase C（上轮完成）
8 个 OKX active members 全部 ≥200 1h bars；backfill 直接绕过 incremental logic 完成

### 验证
- `aletheia doctor`: `data.ohlcv: ok`, `data.signal: warn(36.7%)`
- `aletheia audit data --json`: 结构化覆盖率 ✓
- `layer1["data_health"]`: `signal_status=degraded` 正确触发


## 2026-06-17 — Self-Evolution Tier 1 实施(worktree-self-evolution-tier1)

> 研究 → 三层设计 → Tier 1 全量实现 + 文档整理，一次会话完成。

### 背景
Universe forward outcomes（4h/15m/1h/24h 收益追踪）数据已有，但从未反哺 scanner 规则。
Tier 1 目标：无 LLM 成本地关闭这个反馈回路，将负面 4h outcome 自动转化为 guard_candidate。

### 实现内容
- `aletheia/universe/outcome_recorder.py`：`settle_due()` 后扫描 4h settled 行，方向感知
  阈值判断（long < -3%，short > +3%），调用 `create_guard_candidate()`，7 天内去重
- `aletheia/storage/repositories.py`：`guard_candidate_exists_recent(symbol, family, days=7)`
- `tests/test_self_evolution_tier1.py`（新建）：10 个单元测试

### 文档
- `docs/plans/self_evolution_plan.md`（从 `docs/_ai/SELF_EVOLUTION_DESIGN.md` git mv）
- `docs/status/ROADMAP.md`：Self-Evolution 横切轨道
- `docs/_ai/CURRENT_TASK.md`：Tier 2（2026-07-01）、Tier 3（2026-08-17）日期

### 验证
1311 passed, 0 failed（master `.venv`，全量 suite）

### 状态
两个提交在 `worktree-self-evolution-tier1`（`1a75dc7`, `89df0be`），待合入 master。
Master 当前有未提交的 equity observer / futures integration 工作，需先处理再 merge。

---

## 2026-06-16 — 项目级 Code-Review 修复(A/B/C/D 设计级,uncommitted)

> 来源:`/code-review max` 全项目审查(契约对应 + 运行态正确性 + 潜在 bug)。
> 15 条确认 findings,7 条机械修复先行,4 条设计级本轮完整实现。

### 实现内容
- **A** `signal_emitter.py`: `_HIGH_CONF_THRESHOLD` 70→60 —— allocator 总分天花板 ≈68.5,
  原阈值令 "high" 永不可达。
- **B** `outcome_recorder.py`: `settle_due`/`_batch_fetch_prices` 改 async,共享 CCXT provider
  给全部 universe symbol 取 live ticker,snapshot 兜底 —— 此前只有静态 3 标的能定价,
  轮动成员全标 `failed`,校准攒不到数据。
- **C** 方向感知校准:`UniverseForwardOutcome` 加 `direction` 列(`models.py` + `migrations.py`
  CREATE TABLE 与 `_ADDITIVE_COLUMNS` 双路);`service.refresh` 经 `_infer_direction` 写方向,
  `register_pending` 落库;`repositories.calibration_stats` 胜率 SQL 改方向感知
  (`positive_count`→`win_count`,short 赢在负收益),`routes/universe.py` 同步。
- **D** `scheduler.py`: 新增 `reschedule_universe_refresh()`(锁内 `reschedule_job`);
  `routes/settings.py` 在 `refresh_interval_min` 变化时调用,使保存即时生效(原须重启)。

### 验证
- `tests/test_universe_calibration.py`(NEW,3 方向用例) + signal_emitter 阈值用例
- ABCD 受影响测试单独跑 36/36 全绿,ABCD 未新增 DB writer / scheduler job
- 顺带修 `test_startup_gate::test_empty_response_not_usable`(测试桩 `_FakeResp` 缺 `.raw`、与
  `startup_gate.py:64` 漂移;加 `raw=None` 对齐真实 `ModelResponse`,零生产改动;BACKEND_ISSUES §6.5 ✅),
  修复后全量基线恢复全绿
- 所有改动均非 trust-boundary 文件(risk/execution/auth/secrets/live 未触碰)

---

## 2026-06-14 — 全局代理配置实现(commit 28eb357)

> 根因:Windows Task Scheduler 启动的 watchdog 进程没有 HTTPS_PROXY env var → CCXT/Binance
> 连接超时 → market_data 写入失败 → market data 永远 stale。

### 实现内容
- `aletheia/net.py`(NEW): `httpx_client()` / `aiohttp_session()` / `apply_proxy_env()` 三个工厂函数
- `aletheia/config/schema.py`: 新增 `NetworkConfig(proxy_url, no_proxy)`
- `aletheia/server/app.py`: lifespan startup 调 `apply_proxy_env()`
- 13 个文件 30+ 处 httpx/aiohttp 调用点统一替换为 net.py 工厂
  (最关键:ccxt_provider.py 的 aiohttp.ClientSession → aiohttp_session())
- `aletheia/server/routes/settings.py`: `POST /api/settings/network` + `/test` 端点
- `frontend/src/pages/Settings.tsx`: NetworkCard 组件 (Infrastructure tab)
- `aletheia/diagnostics/doctor.py`: `_probe_network()` 诊断维度

### 验证
- `npm run build` 通过(187 modules, 803 kB)
- `uv run pytest -q` 通过(同预存失败数,无新增失败)
- 用户可通过 Settings → Infrastructure → Network Proxy 配置代理 URL 并点 Test 验证连通性

---

## 2026-06-14 — Edge Lab 全量落地(commits 9fe5c5f / 8532280 / c90d2eb / ab2ba5c / ab4a522)

> 来源:Clustr100 社区交易群知识提炼 → 用户批准 implementation plan → 两个并发 session 分工落地。

### 实现内容
- **P0**: `Opportunity` 新增 `valid_until / entry_zone / stop_loss_hint / tp_hint`(schema + migration)
- **P0.5**: `paper_broker` 可配置滑点(`slippage_bps=5bps`, 多头高填 / 空头低填) + `execution_fee_pct` 去硬编码
- **P1**: `ShadowTrade` 模型 + `shadow_trades` 表 + `persist_shadow_batch()` + `paper_trader` 每 tick 入库 Top-5
- **P1.5**: `Position` 新增 `btc_entry_price / btc_exit_price`；开仓时从 latest_snapshot 抓 BTC 价写入；平仓时 pnl.py 写 exit
- **P2**: `Position` 新增 `regime_tag`；开仓时从 `latest_macro_snapshot()` 读 `risk_regime`；risk_off 惩罚 -20→-35
- **P2.5**: `compute_family_scorecard()` + `GET /api/analytics/family-scorecard`
- **tests**: `test_edge_lab.py`(11 个新测试全绿) + 回归修复 2 处精确断言(`pytest.approx`)

### 关键修复(本 session 负责)
- `family_scorecard.py` SQL bug：`positions` 无 `scanner_family`，原查询空返回。改为 LEFT JOIN `trade_proposals→opportunities`。
- `models.py` 重复 `ShadowTrade` class：并发 session 各写一份，移除重复定义。
- `test_paper_broker_stops.py` + `test_risk_engine.py`：slippage 导致填价偏移，精确断言失败。改 `pytest.approx(rel=0.01)`。

### 测试状态
`uv run pytest -q`：仅 3 个预存失败留存(test_ac12_risk_blocks, test_self_heal x2)，均与本批次无关。1170+ 通过。

## 2026-06-13 — 执行路径语义诚实文档化(纯文档,不动代码)

> 用户要求学习吸收外部 Agentic Trading 框架经验。核实后发现外部框架研究已做完(三份参考文档齐备),
> 真正净增量是通读当前代码、把"LLM 在决策回路精确位置"的一手事实钉死。用户明确:补文档/认知,不改代码。

### 核实结论(一手,2026-06-13 通读代码)
- **下单机制确定性**:`paper_trader.py:124` 固定 notional/价、`:126-130` 固定止损%、Risk Engine 硬限。
- **但交易方向 = LLM**:`market_observer.py:125-152` LLM 输出 `direction_bias` → `paper_trader.py:104-108` 直接照做。
- **strategy rule 悬空**:`strategy_designer.py:102-113` 产出 entry_rule/exit_rule 文本存 `parameters` JSON,
  全仓无 DSL parser,`paper_trader.py:136-149` 从不读 rule——策略层对交易零影响。
- **验证仅 paper 一层**:`strategy_lifecycle.py:184-273` gates 严格但基于真实 paper 统计,无 backtest/walk-forward/PBO。
- **audit 无 hash 链**:`jsonl.py:25-69` append-only 但每条独立,无 parent_hash。

### Added
- `docs/EXECUTION_PATH_HONESTY.md`:三类拆解(确定性/LLM/悬空,file:line)+ 原始设计 v1 差距对照 +
  Prism"反向"对照 + 验证层/审计链/经验闭环现状 + 三条诚实化路径(命名/验证 gate/真 DSL,仅记录)。

### Changed
- `docs/north_star_references.md`:更正 §0/§2/§6——"market_observer 不读历史"已过时(现注入 memory block);
  经验闭环从"只写不读"更正为"软闭环已通,缺参数化反馈";交叉引用新文档。
- `docs/BACKEND_ISSUES.md`:新增 §4(2026-06-13 结构性发现,未修复待授权)——strategy rule 悬空、
  验证层仅 paper、audit 无 hash 链;header/阅读顺序标注 §4。

### 不做(留痕)
- 不改任何代码;不执行 openalice_prism 蓝图 P0/P1/P2(待授权);不引入 DSL 引擎/回测框架(红线)。

## 2026-06-13 — Gateway 静态文件启动可靠性(commit db2423f)

> 用户访问 WebUI 得到 JSON 404。根因:后台启动时 Vite watch 与 uvicorn 竞态——uvicorn 模块导入时
> 检查 `dashboard_static/index.html` 并 mount,但 Vite 首次构建未完成,文件缺失 → mount 跳过 → 404。

### Changed
- `aletheia/_gateway_runner.py`:禁用内嵌 Vite watch(后台启动依赖预构建静态文件;开发用 dev.ps1)。
- `aletheia/server/app.py`:静态 mount 从模块顶层移到 lifespan startup,新增 `_mount_static_dashboard()`。
- `aletheia/gateway/lifecycle.py`:`do_start` 集成 preflight(fail-fast)+ `_verify_webui_accessible` 启动后验证。
- `aletheia/diagnostics/doctor.py`:新增 `webui.static` 诊断维度。
- `CLAUDE.md`:补 WebUI 静态文件构建规则。

### Added
- `aletheia/gateway/preflight.py`:启动前预检(静态文件/数据库/配置),失败给修复建议并拒绝启动。

## 2026-06-13 — 外部框架综述参考分析(纯文档,不动代码)

> 用户提供外部《Agent Framework Research Report 2025–2026》并要求结合项目更新 roadmap。
> 判断:报告是验证镜非新需求源,其重型栈处方命中红线 → 按参考文件先例只做文档分析。

### Added
- `docs/REFERENCE_agent_framework_research_2025_2026.md`:报告摘要 + 收敛最佳实践 vs Aletheia
  逐条核对 + §7 重型栈(Postgres/向量库/Redis/LangGraph)逐条拒绝附红线 + 净增量 + 治理红线。

### Changed
- `docs/ROADMAP.md`:Sources 引该报告(validation only);Common Failure Modes 补 Lookahead bias +
  Execution/slippage mismatch 两行;File Relationships 加新参考文件。
- `docs/north_star_references.md` §5 末加外部综述独立佐证指针(Hermes ≡ §4、重型栈已拒绝)。

### 不做(留痕)
- 不引入报告推荐的任何依赖/框架(Postgres/vector/Redis/NATS/LangGraph 均违反红线)。
- 不新增 phase/能力,不改 Phase 0 优先级,不触 trust 边界。

## 2026-06-13 — DX 收口 + 能力批次(18 提交)+ 机器交接

> 本日批次紧跟 `1075c8e`(Persistence/Sync/Recovery 子系统,已在 origin/master)。会话结束时
> 在本机停机、准备切换到另一台开发机前推送。逐条根因见 `docs/AI_HANDOFF.md` 同日条目。

### Changed — Dev 体验收口到 single-port :8787
- dev.ps1 不再起独立 Vite dev server(:5173);gateway :8787 同时服务 API + 静态资源。
  `vite build --watch` + backend `uvicorn --reload`(+ `WATCHFILES_FORCE_POLLING` Windows 兼容)
  接管热重载;SSE 推 `frontend-built` 事件,`dev-reload.ts` 自动刷新浏览器。
- 所有 dev-reload 行为门控 `ALETHEIA_DEV_RELOAD=1` + `live_disabled`,生产恢复 supervisor 崩溃保护。
  sentinel 路径统一为 `RELOAD_SENTINEL` 常量。新增 `scripts/watch_backend.py`、`/api/version`。
- "Restart" → "Cold Restart"(明确冷启 vs 热重载)。状态浮层从扁平 table 改为四区 flex 布局。

### Added
- LLM startup gate 重试(默认 3 次,可配 `health.startup_gate_max_retries` /
  `startup_gate_retry_delay_sec`)+ 暂停时 Windows toast,治 Databricks 端点偶发误暂停。
- 原生 Anthropic + OpenAI provider(SSE 流式 + 富元数据 + `llm_stream()` + Settings UI),36 单测。
  本项目默认 LLM 仍是 Databricks AI-Gateway,native 为新增能力非默认切换。
- Universe:`quote_volume_24h` / `spread_bps` 列、enriched detail drawer(features + linked opportunities)。
- `docs/plans/`(5 篇 implementation plan / 设计报告)+ `frontend/public/favicon.svg` 入库。
- Clustr100 社区知识库 10 篇(`docs/research/clustr100_community_kb/`,direction-only 非施工授权)。

### Changed — 风控
- symbol 过滤统一为单个 `symbol_list` + `symbol_filter_mode`(whitelist/blacklist)开关决定语义;
  默认 blacklist + 空列表,移除旧的 BTC/ETH/SOL 硬编码白名单。

### Fixed — design-doc 合规(2026-06-11 评审 4 条)
- paper_trader 记录 skip 原因;prediction_observer 显式 `execution_eligible=False`(+ migration);
  event/catalyst scanner 接进 `run_all_scanners` 参与配额;`depth_10bps_usd` gap 注释澄清。

### Operator
- 模式 `Investigating` → `Working`;`current_intent` 现于浮层 + Command Center strip 显示。

## 2026-06-12 — 账户级仓位发现 + 纳管(Phase 1-2 落地,Phase 3-4 设计在册)

**背景**:所有者立场——Aletheia 启动接管账户后,人难以再与 AI 并行管仓,故全账户仓位
(含系统外开出的)应统一交由 AI 统一管理("接管即纳管")。这是把 ROADMAP Phase 6 的
"live-read reconciliation / position-mismatch"思想**提前、且以 demo 优先**落地。

**修复(顺带,先于一切)**:`total_value_usdt` 之前返回持仓名义市值(口径错,会随每笔
fill 跳变),现在交易所缓存可用时用真实账户权益覆盖,与同一 summary 里其它字段一致
(`pnl.py`)。另修 `client.get_positions()`:Bitget v2 mix all-position 的入场价字段是
`openPriceAvg`,旧代码读的 `averageOpenPrice` 根本不存在 → 一直静默为 0;现已读对,并
补 `markPrice`/`leverage`。无真实入场价就无法合成诚实因果链,这是纳管的前置。

**Phase 1 — 发现(只读,已完成)**:新增 `portfolio/position_reconciler.py`
`classify_positions()`,把交易所缓存仓位与本地 open 仓位按 `(canonical symbol, direction)`
配对(hedge 模式下同标的多空并存,故必须按"对"匹配),分类为 managed / external / orphaned。
新增 `GET /api/positions/reconciliation`;`compute_portfolio` 增 `exchange_external_summary`
(并把旧的、hedge 模式下会算错的 external 计数改为按对精确)。前端 `TradesPositions.tsx`
新增 "Account Reconciliation" 卡片(此前 drift 信号无任何前端消费者)。

**Phase 2 — 纳管(已完成,改动受控)**:新增 `source` 列(`native`/`adopted`)到
positions/orders/trade_proposals(CREATE TABLE + `_ADDITIVE_COLUMNS` 双覆盖)。
`adopt_position()` 在单事务内合成 proposal→order(filled)→position 完整链,全部
`source="adopted"`,入场价/数量取自交易所,零入场价/零数量则拒绝纳管(不伪造)。关键设计:
纳管仓位的 `execution_mode` 按账户模式映射(demo→`bitget_demo`,live→`bitget_live_limited`),
于是 `position_manager` 自动管理 demo、跳过 live——**demo 自动 / live 人工的拆分由
`execution_mode` 一处如实设置后被既有守卫免费强制**,无需特殊判断。

**治理(trust §1.1 修订)**:旧铁律"外部仓位永不入本地表"修订为"可入表,但必须带
`source=adopted` 的诚实合成链"。红线测试 `test_external_positions_not_written_to_local_table`
拆为 `test_sync_alone_never_writes_local_positions`(sync 仍只读)+
`test_adopted_positions_carry_synthetic_chain`(纳管必带诚实链)。决策记录见 DECISIONS.md。

**Phase 3-4(设计在册,未实现)**:P3 = 定时 reconcile 任务(demo 自动纳管 + 孤儿平仓,
**默认开启**,见 DECISIONS.md 行为警告)、operator 命令(`reconcile_positions`/`adopt_position`/
`close_position`,live 需人工确认)、手动触发 API。P4 = live 人工确认平仓路径
(`close_live_position`,过 Execution Gateway)+ Full 模式接缝(默认关闭)。

**测试**:`uv run pytest -q` → **777 passed**。前端 `tsc` 干净,bundle 已重建。

---

## 2026-06-10 — CLI 分层 + 运营命令软弃用（人类交互收口到 Web）

**背景**：评估是否完全去掉 CLI、人类交互全走 Web。结论：不去掉，分三层。

**硬约束（为什么不能全删 CLI）**：Web UI 由 gateway 进程伺服，进程没起就没 UI——无法用"还不存在的 UI"启动那个进程；且 Windows 看门狗任务写死 `aletheia.main gateway start`，`doctor` 最需要时正是 gateway 挂掉、UI 够不着时。

**三层划分**：
- **bootstrap 内核（保留）**：`gateway start/restart/enable-autostart`、`doctor`、`version`。
- **运维/调试工具（保留，无 Web 平价）**：`bitget sync`、`knowledge reindex`、`knowledge search`。
- **运营命令（软弃用，有 `/api/*` 平价）**：`permission`/`model`/`risk`/`strategy`/`update` 整组 + `secrets` + `bitget test`。

**软弃用机制（commit `16efd5b`）**：
- `cli/_deprecated.py`（新）：`warn_deprecated()` 往 **stderr** 打一行指向 Web 的黄色提示；**stdout 不变**。
- 整组弃用用 Typer `@callback`（子命令执行前触发）；`bitget test` 用内联提示，`sync` 保持干净。
- `main.py`：`add_typer` 重排为 bootstrap / kept-tools / `[deprecated]` 三档，help 文本加 `[deprecated → …]` 标记。
- `diagnostics/doctor.py`：kill_switch / demo-creds 的 fix_hint 改为 Web 首选、CLI 备选——避免"删命令后提示成谎话"。

**文档同步（commit `2217510`）**：RUNBOOK / README 标 dashboard 为 primary、弃用 CLI 为 deprecated；`implementation_guide`（冻结规格）与 AI_HANDOFF 历史条目不改。

**决策**：软弃用而非硬删——零回退、零功能损失、保留逃生口；物理删除留待将来 Web 路径用顺手后一次性收尾。架构决策见 `docs/DECISIONS.md`。

**测试**：`uv run pytest -q` → **538 passed**（软弃用零行为变更，无回退）。

---

## 2026-06-08 (later⁷) — Gateway 稳定性修复（ghost-socket 根治）

**问题**：前端状态栏显示 "Gateway Stopped" 但页面正常访问；`gateway stop` 无法释放端口。

**根因**：uvicorn multiprocessing worker 在 parent 被 kill 后变孤儿，继承 listening socket 并用旧代码运行。其 cmdline (`multiprocessing.spawn.spawn_main`) 不含 orphan detector 的标记字符串，逃脱所有清理。

**修复（commit `a90ea84`）**：
- `pidfile.py` — `find_port_holders(port)`：netstat/lsof 直接按端口查 PID（兜底）
- `lifecycle.py` — `do_start`/`do_stop` 三层清理：PID tree-kill → cmdline orphan sweep → port holder kill
- `health.py` — `/api/status` 自报 `gateway_running: True`（能响应 = 在运行）
- `app.py` — lifespan 启动补写 PID + ownership 追踪（只清自己写的）
- `gateway.py` — restart 拒绝无恢复机制时的自杀

**决策**：网关端口清理改为 "one stop all" 策略——最终兜底不看进程身份，只看端口占用。
**测试**：46 gateway/supervisor/pidfile tests 全过。

---

## 2026-06-08 (later⁶) — Markets K线动态加载（scroll-back history + live tail）

**目的**：Markets 页（`/charts`）的 K 线图原本只加载固定一段（最新 N 根），向左滚动看不到更早历史，右沿也不自动更新。改成主流交易所体验：向左滚到边缘 → 自动续加更早历史；右沿保持实时刷新。

**后端**（`aletheia/data/ccxt_provider.py` + `server/routes/data.py`）：
- 新增 `_TF_MS` 映射 + `timeframe_ms(tf)` helper（tf→毫秒的单一真源）
- `get_ohlcv`/`_fetch_ohlcv` 增加 `since: int|None` 参数，透传给 CCXT `fetch_ohlcv(since=)`
- `/api/market/ohlcv` 增加 `before`（unix 秒）参数：`since_ms = before*1000 - limit*tf_ms`，并把 `>= before` 的 candle 过滤掉，保证续加页与左沿不重叠
- 缓存策略收紧：只有默认最新视图（`before` 未设 且 `limit==100`）走 TTL 缓存；历史页与实时尾部一律 fetch fresh

**前端**（`api/client.ts` + `charts/PriceChart.tsx`）：
- `getOhlcv` 增加 `before` 参数（第 4 位）
- `PriceChart` 重写为命令式 candle store（refs 持久化，图表不随数据重建）：
  - 初次加载最新 `INITIAL_LIMIT=300` 根
  - 向左滚到 `SCROLL_TRIGGER_BARS=10` 内触发 `loadOlder()`：`before=<最左根>` 取下一页、去重、前插、`setData` 后用 `setVisibleLogicalRange` 平移逻辑区间保持视窗不跳
  - `LIVE_POLL_MS=10s` 轮询最新 2 根，`series.update()` 替换在成形/追加已收盘的 bar
  - 守卫：`loadingOlderRef`/`exhaustedRef`/`initializedRef`；markers 在独立 effect 重挂不重建图表

**验证**：488 passed（+2 新测试：`before` 过滤 + `since` 窗口数学、unsupported timeframe 干净报错）；后端 urllib E2E 确认续加页与最新视图 gap 恰为 1 个 tf、零重叠；Vite :5173 视觉 E2E 确认三种行为（初次 `limit=300`、续加 `before=...`、实时 `limit=2`）全部触发、零 console error。

---

## 2026-06-08 (later⁵ cont.) — Deep Research + ROADMAP v1

**目的**：博采众长制定按周迈步路线图。

**Deep Research 执行**：102 agent / 20 sources / 25 claims → 19 verified (6 killed)

**核心发现（adversarially verified）**：
- 经验闭环有 4 层：无学习 → 记忆累积 → walk-forward 策略修改 → RL 端到端（我们在 1→2）
- 策略必须结构化（Pydantic schema / signal+confidence 数值对），不能 free-text
- Agent 协作主流：fan-out/fan-in + 硬风控门控；对抗辩论是加分非必须
- walk-forward validation 是防噪音固化的唯一可靠方法（SHARP 论文 2026.05）
- 单 agent RL vs 多 agent 辩论之争未定——我们选多 agent + 可解释性
- TradingAgents 明确说"treat as research scaffold"——这是诚实定位

**产出**：`docs/ROADMAP.md`（12 周路线图 + 4 个观测门 + 红线 + 失败模式表）

**来源（核实度最高的）**：
- TradingAgents (github.com/TauricResearch) — structured debate + Pydantic output
- ai-hedge-fund (github.com/virattt) — fan-out/fan-in + signal/confidence
- SHARP (arXiv 2605.06822) — walk-forward validated policy edits
- TradingGroup (arXiv 2508.17565) — chain-based + 20-day outcome labeling
- AlphaQuanter (ACL Findings 2026) — single RL agent, tool-augmented
- FinRL (github.com/ai4finance-foundation) — ensemble DRL baseline
- AutoGen/Magentic-One — actor model + stall detection

---

## 2026-06-08 (later⁵) — Pipeline gap analysis + 文档统一

**目的**：对照"全市场扫描→分析→策略→开仓→平仓→评估→学习→总结"完整预期，
逐环节代码核实当前实现与预期的差距。

**核心发现**：
- 管道骨架 80% 接通：5 crypto + 4 xStocks + 4 stocks + prediction markets 自动扫描；
  market_observer→paper_trader→position_manager 全链路每 15min 自动运行
- 三大结构性差距明确定位：
  1. **经验闭环只闭 30%**：6 agent 写 knowledge，只 2 agent（observer/designer）读回
  2. **Agent 无协作流**：13 个 scheduler job 各自 cron，无方向性数据传递
  3. **策略文字非规则**：designer 产出 entry_rule 自然语言，trader 不读
- 两个 agent 代码完整但未排入 scheduler：`post_trade_reviewer` / `risk_critic`

**产出文档**：
- `docs/PIPELINE_STATUS.md`（新建）— 逐环节现状表 + 差距分析 + scheduler 全景 + 北极星阶段图
- `docs/AI_HANDOFF.md` — 更新 current state + next steps
- `docs/CURRENT_TASK.md` — 更新状态为 PLANNING（stage 0.5 措施待确认）
- Memory `aletheia-mvp-status.md` — 更新 pipeline completion 表

**无代码变更**。纯分析+文档整理 session。

---

## 2026-06-08 (later⁴) — xStocks 接入 + 管道 bug fix 三项

**本次 session 两阶段工作：**

### 阶段一：Bitget xStocks（ON-series 代币化股票）接入

- `aletheia/config/schema.py` — 新增 `observed_xstocks: list[str]`（默认 AAPLON/TSLAON/NVDAON/SPYON）
- `aletheia/trading/instruments.py` — 新增 `xstocks_instruments_from_config()`，返回 `exchange="bitget"` 的 `InstrumentId` 列表
- `aletheia/agents/market_observer.py` — 新增 xStocks 分析 loop：独立 Bitget provider，`asset_class="equity"`，Opportunity 写入但 paper_trader gate 门控跳过（observation-only）
- `aletheia/trading/market_data.py` — 新增 xStocks ticker 采集 loop，写入 `market_snapshots`（position_manager 定价所需）；提取 `_fetch_and_store()` helper 复用
- `tests/test_xstocks_observer.py` — 4 个新测试覆盖 xStocks 路径

### 阶段二：管道 bug fix（全链路审计发现）

1. **`Opportunity.confidence` 默认值** (`aletheia/storage/models.py:51`)
   - `"medium_confidence"` → `"speculative"`
   - 原因：paper_trader `_CONFIDENCE_RANK = {"speculative":0, "medium":1, "high":2}` 不含 `"medium_confidence"`，任何默认值 opportunity 被 rank KeyError 当 0 静默跳过

2. **`paper_broker.py` datetime 时区不一致**
   - 移除 `from datetime import datetime` 和 `filled_at=datetime.utcnow().isoformat()`（naive）
   - `Order.filled_at` 默认 None；`Fill.filled_at` 用 `_utcnow()`（带 tzinfo）

3. **xStocks ticker 未写入 market_snapshots**（顺带修复）
   - position_manager 调 `_latest_price(symbol)` 时因无 snapshot 返回 None → 持仓永不触发 stop/TP
   - 修复：`market_data.py` xStocks loop 调 `_fetch_and_store()` 同等写入

**测试结果**：474 passed，1 flaky（`test_write_audit_jsonl` 全量并发路径泄漏，单跑通过，既存问题）

**commit**: `0303907 fix(pipeline): confidence default, datetime tz, xStocks ticker snapshots`
**commit**: `4f59d70 feat(xstocks): Bitget tokenized equity observation via market_observer`

## 2026-06-08 (later³) — Chart blackout root-cause: stale gateway process + provider consolidation

**Symptom**: Every lightweight-charts component (equity curve + the 3 markets
K-line charts) rendered blank. `/api/market/ohlcv` returned
`{"error":"market_data_unavailable","candles":[]}` and the frontend's
`Array.isArray(data) ? data : []` guard silently turned that into an empty array.

**Root cause was NOT missing abstraction** — the data layer (`MarketDataProvider`
protocol, `CCXTProvider`, `OHLCVCache`, `symbols.py`) was already implemented and
correct on disk. Two real causes:

1. **Stale process (the actual blackout).** The gateway booted at 11:16; the commit
   that switched `data.py` to `CCXTProvider` landed at 13:26. The running process
   held the *old* `data.py` in memory — which called legacy `BitgetClient.get_ohlcv()`
   with canonical `BTC/USDT`, a symbol Bitget REST v2 rejects (`Parameter BTC/USDT
   does not exist`). **Completely invisible**: `/api/health` still reported healthy.
   Fixed immediately by `aletheia gateway restart`.
2. **Construction drift.** `CCXTProvider(...)` was hand-constructed from the same
   "read config → build" template in 3 places, inviting copy-paste in new components.
   A dead, uncalled singleton `get_default_provider()` also lingered — exactly the
   cross-event-loop singleton trap we want to forbid.

**Hardening landed (so this class of bug surfaces honestly next time):**

- **Version-drift health check.** New `aletheia/agent_runtime/build_info.py` records
  the source commit at boot (`record_boot_commit()` in `lifespan()`, before any
  scheduler thread starts). `code_drift_issue()` compares disk HEAD to the booted
  commit; `compute_health()` now appends `code_updated_pending_restart:<old>..<new>`
  when they diverge → `/api/health` goes degraded, telling us to restart. Reuses
  `update/git_ops.current_commit()`. Non-repo / git-unavailable → stores None → never
  false-alarms. Known limit (accepted): uncommitted working-tree edits don't move
  HEAD so aren't flagged — but the real failure here was "committed, not restarted",
  which is caught.
- **Single provider factory.** Deleted the dead singleton; added
  `get_market_data_provider(exchange=None)` as the one market-data entry point
  (per-call, never cached — documented why: CCXT binds aiohttp to the creating loop,
  scheduler uses a fresh loop per job). Routed 3 sites through it: `routes/data.py`,
  `trading/market_context.py`, `trading/market_data.py`. Left the 2 Bitget-only
  xStocks sites (`market_data.py`, `agents/market_observer.py`) constructing
  `CCXTProvider(exchange="bitget", fallback_exchange="bitget")` directly — both now
  carry a comment marking the deliberate exception (factory would force the config
  fallback `bybit`, which doesn't list ON-series tokens).
- **CLAUDE.md** Development Rules gained a market-data rule pointing at the factory
  and forbidding `BitgetClient` for market data.

Note: the equity curve being flat at 0.0 is *honest*, not this bug — `PnLChart`
reads `/api/market/portfolio-history` (DB), which has snapshots but no positions yet.
Out of scope.

## 2026-06-08 (later²) — Market context polish (thesis fallback + score persisted)

After the market context layer landed, real-run inspection surfaced two
rough edges:

1. **codex_cli's structured output is not 100% reliable.** On ~1 in 3 runs
   it returns a valid JSON shape but with empty `rationale` and empty
   `key_observations` (the JSON parse drops fields, or the model returns a
   minimal stub). Old code then fell back to `thesis = context[:500]`,
   dumping the raw markdown snapshot (`## Verified market data snapshot`,
   table headers etc.) into the dashboard thesis cell — looked broken.
2. **`confidence_score` only lived inside the thesis text** (`score=65/100`
   at the end). The dashboard couldn't surface it as a real column.

Both fixed:

- **`opportunities.confidence_score INTEGER`** column added (additive
  migration; nullable so older rows stay valid). `Opportunity` Pydantic
  model + frontend `Opportunity` type updated.
- **Thesis fallback rewritten**: when rationale + observations are both
  empty, thesis is a one-line stub citing the failure mode
  (`"No structured rationale produced (direction=X, confidence=Y). Likely
  cause: LLM unavailable or structured-output parse failure — see
  agent_runs/errors."`). Raw snapshot never leaks through.
- **Charts page Markets table** — added a `Score` column showing
  `confidence_score/100` with a tooltip explaining what it means and why
  it can be empty. Side-by-side with categorical confidence so the model's
  full calibration is visible at a glance.

Real e2e (3 fresh runs after migration applied):
- `long / medium / 64/100` — full structured shape ✅
- `neutral / speculative / 50/100` — model genuinely on the fence,
  honestly committed a midpoint ✅
- `neutral / speculative / —` + thesis "No structured rationale..." —
  Codex CLI timed out, fallback stub kicked in cleanly ✅

New test `test_observer_thesis_fallback_avoids_raw_snapshot`. Full suite
**423 passed** (the 2 failures in `test_exchange_sync.py` predate this
work — verified by stashing other working-tree mods and re-running).
Zero change-controlled module touched.

## 2026-06-08 (later) — Market context layer (fixes "all neutral / speculative")

The dashboard Markets page had been showing every market_observer opportunity
as `direction=neutral, confidence=speculative` — symptom that read like "AI is
not running". Diagnosis: AI is running fine (codex_cli healthy), but the LLM
was being fed exactly **one line per symbol** ("BTCUSDT: price=X 24h_change=
0.05%"). With a 1-D input the model can only reply at the base rate; the
system prompt's "be conservative — default to neutral" hardened that into
permanence.

Fix is the data-supply layer, not the prompt. Borrowed the **TradingAgents**
pattern (multi-timeframe pre-computed indicators + short close series +
markdown verified-snapshot tables + structured output schema) using stdlib
only — no numpy/pandas/ta-lib. Research memo and design rationale in
`docs/research/market_context_eval.md` (covers OpenAlice tool-as-DSL pattern,
TradingAgents predigest pattern, why VergeX is unavailable as a reference).

What got built:

- **`aletheia/trading/indicators.py`** (new, stdlib-only) — SMA / EMA / RSI
  (Wilder's smoothing) / ATR / Bollinger / momentum / volume z-score / cross-
  asset relative-strength rank. Pure functions, oldest→newest series, None on
  too-short windows.
- **`aletheia/trading/market_context.py`** (new) — `build_market_context()`
  pulls 1h × 60, 4h × 50, 1day × 30 OHLCV per symbol via the existing
  `BitgetClient.get_ohlcv` (no creds, no new endpoints), computes the
  indicator pack per timeframe, ranks the symbols by 24h return, and renders
  a ~2.5kb markdown block: per-symbol indicator table + last-10 1h closes +
  cross-asset RS table + indicator glossary. Falls back to the legacy 1-line
  context on any failure (red line: agents must never crash).
- **`aletheia/agents/market_observer.py`** — feeds the new context block in,
  upgrades `MarketRead` schema to add `confidence_score` (0-100, forces the
  model to commit a number) and `key_observations` (list of specific data
  citations — empty list signals "no concrete evidence"). System prompt
  rewritten: removed the "default to neutral" hard nudge, replaced with
  calibration ladders (high/medium/speculative criteria, when long vs short
  vs neutral is justified). Adds two `record_step()` breadcrumbs
  (`market_read_input` with the full snapshot, `market_read_output` with the
  structured read) so the dashboard step trace shows the model actually saw
  data and reasoned over it.

Real e2e: first run after the change produced
`direction=long, confidence=medium` with the thesis citing
"all three above 1h SMA20 and SMA50, positive 1h+4h Mom10, ETH leads cross-
asset RS" — vs the previous N runs which were unanimously
`neutral / speculative`. The ground truth (BTC/ETH/SOL all up 2–6% intraday
with multi-timeframe agreement) finally reaches the model.

Red lines kept: no funding/OI/orderbook (would extend client.py surface), no
news/sentiment (AGENTS.md ban), no numpy/pandas (dependency discipline), no
change-controlled module touched. New `tests/test_market_context.py` (17
tests covering indicators, builder render, fallback path, observer integration
and structured-output extraction). Full suite **423 passed**.

What this *doesn't* do: deepen the perception layer (funding regime / OI /
orderbook / news) — those remain north-star-deferred until stage-0 evidence
accumulates. This pass just ensures the data we already collect actually
reaches the model. The Learning Ledger's `activation_ratio` now has a real
input signal (vs being permanently 0% because no opportunity ever advanced).

## 2026-06-08 (latest) — Three remaining real gaps closed

After WEBUI_BACKEND_HANDOFF was done, three real gaps remained that I hadn't yet
addressed (everything else was either already fixed, recorded as a future-scope
north-star item, or banned by AGENTS.md). All three closed in one pass — they're
small once the §1.1 causal chain and §6 experience-loop scaffolding are in place:

- **Gap 1 — agent_run_steps now actually populated.** The `/api/agent-runs/{id}/steps`
  endpoint shipped in the prior pass returned an empty array because nothing was
  writing to the table. Hooked the write in `agents/helpers.py::log_tool` (every
  tool call now produces a step) plus a new `record_step()` for non-tool reasoning
  nodes. Per-run step counters live in-process and are released by the orchestrator
  at end-of-run so the dict stays bounded. **Zero changes to the 8 agents** — they
  already use `log_tool` everywhere, so the trace fills in automatically.

- **Gap 2 — Stability monitor + `GET /api/stability`.** Bridges the gap between
  "process alive" (existing `health.py`) and "data internally consistent"
  (north-star §7 stage-0 evidence). New `agent_runtime/stability_monitor.py`
  reports orphan counts (fills with no order, executed proposals with no order,
  etc.), strategy_id drift (paper proposals without a strategy — direct §1.1
  causal-chain signal), 24h agent failure ratio, uptime hours, and a 0-100
  composite score with explicit reason codes. Read-only — inconsistencies are
  REPORTED, never silently repaired. Operator can poll the endpoint through the
  7-14 day stage-0 run and watch the score numerically.

- **Gap 3 — Learning Ledger gets a quality block.** `dreaming_agent` was already
  emitting a Learning Ledger but it only counted volume ("research_note: 12,
  strategy_memory: 3"). Volume can be noise. Added stage-1 quality signals:
  activation ratio (% strategies that reached paper_running or beyond),
  retire/deprecate ratio (noise being filtered — non-zero is healthy),
  post-trade-review coverage (%), and lessons-consulted count over 24h
  (`retrieve_lessons` + `load_playbooks` calls — non-zero means the experience
  loop is actually closing). This is the evidence north-star §7 stage 1 wants.

Wiring is intentionally minimal: no new tables, no schema migration, no agent
behaviour changes. Just three new read paths that surface signals already implied
by data we already record. New `tests/test_three_gaps.py` (10 tests) including a
red-line test that orphan fills get DETECTED, never silently fixed. Full suite
406 passed.

What this leaves on the gap list: none of the *immediate* "real" gaps. Remaining
items are all north-star architectural work (Sophia-class long-term identity,
RL feedback loop, cross-agent debate workflow, deeper market perception) that
the project's stage sequencing explicitly defers until 7-14 days of clean stage-0
evidence accumulates. The stability + ledger numbers above are the receipts that
let "stage 0 done" become a yes/no rather than a vibe.

## 2026-06-08 — Exchange Account Sync (closes WEBUI_BACKEND_HANDOFF #3 / portfolio-sync-gap)

The Portfolio dashboard was off by an entire reality: it only saw locally-managed
positions, so `total_value_usdt = 0` even when the demo account held real balance.
Now closed:

- **`portfolio/exchange_sync.py`** (new) — `sync_exchange_account()` calls the existing
  read-only Bitget client methods (`get_balances`/`get_positions`/`get_open_orders`)
  and caches the result as a JSON blob in the `settings` table (no schema change). Status
  classifier `ok | stale | unconfigured | error`. Never raises; degrades to None and the
  UI shows the existing caveat. **Note (turned out client.py was already exposing
  everything — no change-controlled module touched).**
- **`compute_portfolio()`** — extended with `exchange_equity_usdt`,
  `exchange_state_age_sec`, `exchange_external_positions` (drift signal), and
  `exchange_sync_status`. The local managed `total_value_usdt` field is preserved so the
  UI can show both side-by-side and reconcile drift.
- **`GET /api/exchange/account`** — read-only cached view; HTTP path never blocks on
  Bitget. Scheduler refreshes the cache every 5 min via a new `_run_exchange_sync` job.
- **`aletheia bitget sync`** CLI — manual one-shot for debugging.

Red lines kept: read-only against the exchange; external positions stay out of the
local `positions` table (they have no proposal back-link → would violate trust §1.1);
`exchanges/bitget/client.py` untouched. New `tests/test_exchange_sync.py` (9 tests
including a red-line test that asserts the local positions table stays empty after a
sync). Full suite 396 passed. Real e2e: CLI sync against the live demo account
returned `status: ok` and the cached view flowed into `/api/exchange/account` and
`/api/portfolio` after a gateway restart.

Cross-process visibility caveat: SQLite WAL means a CLI write doesn't immediately
appear to a long-running gateway process holding its own connection. In practice the
scheduler refreshes the cache from inside the gateway process every 5 min, so the API
sees the truth on its own timeline — no extra wiring needed for the production path.

## 2026-06-07 — WebUI backend handoff: position FK + steps endpoint

Implemented the two actionable, in-scope gaps from `WEBUI_BACKEND_HANDOFF.md`:

- **#1 position → proposal/order causal link** (⭐ highest value): `positions` gained
  nullable `proposal_id`/`order_id` FK columns (CREATE TABLE + additive migration).
  Both brokers populate them when a fill opens a position; averaging into an existing
  position preserves the *original* opener. Makes the position→upstream direction of the
  trust §1.1 causal chain real (CausalChain.tsx no longer needs "not recorded"). Touched
  change-controlled `execution/bitget_broker.py` — additive only, focused regression tests.
- **#2 `GET /api/agent-runs/{id}/steps`**: read-only endpoint exposing `agent_run_steps`
  (new `list_run_steps`/`insert_run_step` repo fns). Honest caveat: the table is not yet
  written by any agent, so it returns `[]` today — populating it is a separate, larger
  change (every agent) beyond the handoff's "expose the table" ask.

Not done (by design): **#3** exchange equity touches change-controlled `client.py` → needs
explicit owner authorization; **#4** news ingestion is banned MVP scope (AGENTS.md).

`tests/test_webui_backend_handoff.py` (6 tests); full suite 387 passed.

## 2026-06-07 — Experience loop (north-star stage 2, minimal closed loop)

### Added — knowledge that reads back into decisions (§6 gap closed, minimally)

The knowledge base was write-only (the central north-star gap). It now feeds prior
lessons back into decisions, using frontier-but-constrained patterns (Generative-Agents
retrieval scoring, Hermes-style memory block, RAG provenance) on **SQLite FTS5 — no
vectors, no new deps**:

- `knowledge/retrieval.py` (new): FTS5 index + `retrieve_lessons()` scored by
  BM25(relevance) × recency-decay × importance. **Quality gate: only `high_confidence`
  + `active` items are retrievable** — speculative/archived noise can't become "experience".
  `format_memory_block()` renders results as an **explicitly advisory, provenance-tagged**
  block. Degrades to empty on any failure (FTS absent / no match) so agents never break.
- `migrations.py`: `knowledge_fts` virtual table, created out-of-band so a SQLite build
  without FTS5 can't break other migrations.
- `knowledge/manager.py`: `create_*` index high-confidence items; archive/deprecate
  remove them from the index (archived ⇒ not recalled).
- `trading/playbooks.py`: `load_playbooks()` / `format_playbook_block()` — playbooks are
  now **read** into the designer prompt (were seed-only, 0 readers). Read-only; agents
  never write playbooks.
- `agents/market_observer.py` + `agents/strategy_designer.py`: inject the memory block
  (and playbooks for the designer) into the LLM prompt; retrieval is logged as a
  `retrieve_lessons` tool call (observable). Injection is pure prompt text — it cannot
  touch risk/execution/config.
- `cli/knowledge_cmd.py` (new): `aletheia knowledge reindex` (backfill) + `search` (preview).

Governance held: read-only, quality-gated, advisory-not-instruction, no change-controlled
module touched, no live gate weakened. New `tests/test_experience_loop.py` (8 tests);
full suite 381 passed. End-to-end smoke confirmed a high-confidence lesson reaches the
observer's decision prompt with its provenance tag.

## 2026-06-07 — WebUI structural redesign (frontend-only)

### Changed — 14 dashboard pages → 10 role-organized destinations

Complete structural redesign of the React frontend to fix the owner's pain points
(density / layout / transparency / workflow). Diagnosis: UI was a flat page-per-DB-table;
the framework wants §23.1's 6 roles around the causal chain + a workflow flow. **Zero
backend / API changes** — pure frontend composition over existing endpoints.

5-phase IA (Observe / Decide / Execute / Evolve / Operate). Merges:
- Positions + Paper Trades → **Trades & Positions** (`TradesPositions.tsx`) + new
  **`CausalChain.tsx`** inspector (proposal→risk→order→fill→position; position-anchored
  upstream shown as "not recorded" — honest re trust §1.1, never symbol-matched).
- Agent Runs + Opportunities → **Agent Activity** (`AgentActivity.tsx`, tabs via new
  `Tabs.tsx`); run drawer "Reasoning Steps" block = mount point for the unbuilt
  `GET /api/agent-runs/{id}/steps`.
- Dreaming + Knowledge Base + Reports → **Learning** (`Learning.tsx`, 3 tabs; §4 KB
  taxonomy preserved, no type laundering).
- Proposals → **Approval Desk** (decision cards + inline risk verdict + confirm-to-approve).

New `PageHeader.tsx` (title + role descriptor) on all 10 pages — directly answers the
"transparency insufficient" complaint. News Intel got the §17.3 honesty banner
("manual entry / ingestion pipeline not configured"). RiskControls grid → responsive.

Deleted 7 merged page files. Legacy routes redirect (no dead links). CommandPalette +
cross-links updated. Verified live (preview MCP): nav structure, tab switching, honest
empty states, legacy redirects, zero console errors. tsc + vite build green.

Docs: `WEBUI_PAGE_MAP.md`, `OVERVIEW_REDESIGN.md`, `WEBUI_BACKEND_HANDOFF.md` (parked
backend gaps), `BACKEND_ISSUES.md §12`. Branch: feat/strategy-causal-chain (not merged).

## 2026-06-07 (later)

### Fixed — re-audit of BACKEND_ISSUES §10 + 8 falsely-archived issues

Re-verified the §10 "all fixed 2026-06-06" archive against current code (40+ items):
~75% true, ~25% claimed-fixed-but-not. Eight confirmed false/half-true, now really
fixed (each with a focused regression test; full suite 373 passed):

- §1.2 live-order: insert a `pending` order row BEFORE `place_order` (was place-then-
  insert → exchange order with no local trace if DB write fails). `bitget_broker.py`
- §1.3 health alert: run the Telegram coroutine to completion in a worker thread (was
  `loop.create_task` fire-and-forget → alert silently dropped from a sync caller). `health.py`
- §2.3 paper/demo risk: added `max_demo_position_notional_usdt` + `max_demo_single_trade_risk_pct`
  with distinct violation tokens (was unbounded → promotion evidence built from unbounded
  trades). `risk/engine.py`, `config/schema.py`
- §2.3 clientOid: `place_order` now sends a proposal-derived `clientOid` (idempotency on
  retry). `exchanges/bitget/client.py`
- §2.3 kill-switch TOCTOU: `evaluate_proposal` serialized via `_eval_lock` so a daily-loss
  trip is visible to the next proposal. `risk/engine.py`
- §2.5 paper_trader: autonomous-OFF gate duplicated into the agent body (CLI/API trigger
  bypassed the scheduler gate). `agents/paper_trader.py`
- §3 commit_all: dreaming agent calls `commit_all` after consolidation (Trading-as-Git was
  wired but never invoked). `agents/dreaming_agent.py`
- §3 gateway logs: size-cap rotation (`rotate_if_needed`) + tail-only reads (was unbounded
  growth + whole-file read → OOM). `gateway/logs.py`, `_gateway_runner.py`

Also added `starting_capital_usdt` as a real config field (was read via getattr fallback).
The §10 archive now carries a correction banner; lesson recorded: never trust a "fixed"
claim without grepping current code.

### Fixed — strategy↔trade causal chain (gap #2, north-star stage 0/2 prerequisite)

The `strategy_id` causal chain was broken in **four** places, not one (the
2026-06-06 §1.1 "fix" only wired paper_trader's fallback, leaving the upstream
producer side dead). All four now connected:

- `strategy_lifecycle.create_strategy()` accepts `parameters` → persisted to
  `StrategyRecord.parameters` (was always `"{}"`; trust §4.2 audit snapshot)
- `strategy_designer` produces a structured parameter spec and auto-advances the
  strategy through the **ungated** lifecycle states to `paper_running` (stops there;
  live gates untouched), so a tradable strategy actually exists
- `strategy_designer` registered as a daily scheduler job (was never scheduled →
  no strategies were ever produced autonomously)
- `market_observer` binds new opportunities to an active strategy via
  `linked_strategy_id` (honest None if none active; was never set, zero coverage)

Result: promotion-gate `paper_trades` count is no longer structurally stuck at 0.
New `tests/test_strategy_causal_chain.py` (5 tests) locks the chain end-to-end.
Suite: 357 passed.

### Changed

- Documentation housekeeping: archived all fixed items in BACKEND_ISSUES.md (§10) and FRONTEND_ISSUES.md (§10.1–10.24); removed root-level IMPLEMENTATION_GUIDE.md duplicate (canonical copy in docs/)
- Gateway: replaced all Windows subprocess calls (wmic/tasklist/powershell/taskkill) with pure ctypes Win32 API — fully invisible operation, zero console window flashes

### Decision

- Process introspection on Windows uses kernel32/ntdll ctypes instead of spawning external executables (see DECISIONS.md)

## 2026-06-06

### Added

- CLAUDE.md (project overview for Claude Code)
- .claude/ directory (settings, commands: start-session, end-session, update-handoff, project-audit)
- .codex/config.toml (Codex project config)
- docs/PROJECT_CONTEXT.md, docs/CURRENT_TASK.md, docs/AI_HANDOFF.md, docs/DECISIONS.md, docs/DEVLOG.md, docs/AI_WORKFLOW.md

### Fixed (major pass)

- **Backend (80+ fixes)**: All items in BACKEND_ISSUES.md §1-§3 — trust invariants (strategy_id causal chain, execution gateway isolation, git ff-only, promotion gates), data integrity (PnL fees, KB status filtering, CORS, audit symmetry, risk evaluation, scheduler error handling, telegram hardening), dead code cleanup
- **Frontend (24 fixes)**: All items in FRONTEND_ISSUES.md §1-§3 — shared types/DataTable/DetailDrawer components, risk_decision display, KB markdown reading, autonomous_paper_trading toggle, effective config card, promotion gates UI, chart-trade linking, dreaming 6-section layout, overview sparkline, cross-page navigation, agent run tool calls

### Notes

AI workspace initialization. Existing AGENTS.md preserved as-is (already comprehensive).
