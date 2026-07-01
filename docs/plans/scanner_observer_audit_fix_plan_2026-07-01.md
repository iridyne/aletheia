# Scanner + Observer 审计修复实施计划

> 制定日期：2026-07-01  
> 分支：`fix/scanner-observer-audit-fixes`（基于 master e5f9255）  
> 来源报告：`docs/research/UNIVERSE_SCANNER_AUDIT_2026-07-01.md` + `MARKET_OBSERVER_AUDIT_2026-07-01.md`  
> 方法：报告 35 个问题**逐条 grep + 读源码亲核**（宽 fan-out workflow 因 workspace rate-limit 全灭，改手动核实——反而查出报告 6 处根因错误）  
> 原则：SDD spec-first、最小改动、不过度工程化。真 bug 才修；设计选择/增强项明确标注不改的理由。

---

## 0. 核实结论：报告 6 处根因订正（诚实纪律）

报告是审计初稿，file:line 与根因存在漂移。亲核后必须订正的 6 处：

| # | 报告原判定 | 核实后真相 | 处置 |
|---|---|---|---|
| US-4.1 | `scanner_weights` 是「无人读的死字段」 | **写入链完整**（PI Operator `_action_adjust_scanner_weight` 写 + Settings.tsx 只读展示 + OL-C09 spec 锁定持久化），**消费链缺失**（`compute_total_score` 只用自动 calibration，从不读 PI 手调的 scanner_weights） | 修消费链，关联 OL-C09 |
| US-4.10 | event scanner 门槛低（25分）「永远被竞争压制」 | **错**：`ret_1h>8%→+45`、`>4%→+25`，配 vol×4(+30)+taker(+15) 可达 **90 分**，真触发能赢。0 条 forward outcome 真因 = 事件稀有 + `_infer_direction` **无 event 分支**导致 direction=None | 归入 US-4 修复 |
| US-4.15 | all_scores「被丢弃」 | run_all_scanners **返回** all_scores，存在于内存 scored dict，只是没写入 UniverseMember 表 | 降级 P2 可观测性 |
| MO-3.4 | `crowding=(lsr-1)/0.5 if lsr else 0` 逻辑误导 LLM | **该逻辑不存在**：LSR 已处理 `None→"n/a"`(:116)，crowding 用的是 `lv.crowding_score` 独立字段（normalizer 计算） | 重定位为 MO-8 |
| MO-3.5 | FTS token 不匹配（query 用 "BTCUSDT" 单 token） | **不成立**：`_fts_query`(:198) 把 `/` 拆成空格 → `"BTC" OR "USDT"`，召回正常 | **撤销** |
| MO-3.9/3.10 | 列为 P1-High | FTS 有 confidence 门 + BM25 + recency 排序，仅缺最低分阈值 / snippet 偏短 | 降级 P2-Low |

**最高杠杆发现**：`US-4`（`service.py:256`）是一处修复解决三处症状的根因链——见下。

---

## 1. 根因分层

35 个问题归为 5 类系统性根因：

**A. 方向统计污染（最高优先）** — `service.py:256` 构造 `inserted_members["direction"]` 时用 `_infer_direction(family, item)` **重算**方向，丢弃了 item 里已有的 `scanner_direction`；而 `signal_emitter` 用 `scanner_direction or _infer_direction`。二者不一致 →
- forward outcome 记录的方向 ≠ opportunity 实际方向（胜率统计双重失真）
- momentum 的 `_infer_direction` 要求 ret_4h≠None 且与 RSI 同向，大量候选落空 → **54% direction=None**
- `_infer_direction` 无 event 分支 → event 赢竞争后 direction=None → forward outcome 永远 0 条

**B. 信息流「最后一公里」断裂** — market_observer 的 LLM 把最具体的数字引用写入 `opportunity.key_observations`（annotation+create 两路都写），但 PI layer1(`trading_context.py:152-172`) 只读 9 个字段、且 `thesis[:150]` 截断，`key_observations` 根本不在 SELECT 里 → PI 决策看不到 LLM 最有价值的输出。

**C. 配置消费缺失** — `scanner_weights`（PI 写了 allocator 不读）、`family_quota` 缺 volatility（30% 无约束 fallback）——写入/校验/UI 齐全，唯独打分环节不消费。

**D. 数据降级 / 缺失静默** — market_type 丢 key 全 fallback spot、sync_registry 失败无降级、fallback context 无「数据已降级」提示、F&G runtime 路径漏填、RS rank 单 symbol 恒 1/1——都是「静默给出看似正常的错值」，比报错更危险。

**E. CPU/IO 效率热路** — BB squeeze O(n²)、per-symbol `load_config()`、memo 每 cycle 查库。

---

## 2. 分级修复清单

> 每项：核实位置 → 根因 → 改动 → spec → 测试 → 影响面。  
> 决策链模块（signal_emitter/service/scanners/trading_context/market_observer）均走 SDD spec-first，改后 `aletheia doctor` 验证。**无一项触碰 trust boundary**（risk limits / execution unlock / credentials / live 均不改）。

### P0（决策链核心，5 项）

#### US-4 【方向统计污染】`service.py:256` + `_infer_direction` 无 event 分支
- **根因**：见 §1.A。
- **改动**：
  1. `aletheia/universe/service.py:256` — `"direction": item.get("scanner_direction") or _infer_direction(item.get("scanner_family"), item)`（与 signal_emitter 对齐）。
  2. `aletheia/universe/signal_emitter.py:_infer_direction` — 新增 `event` 分支（用 ret_1h 方向，与 score_event 一致）。
- **spec**：新建 `spec_universe_pipeline.py` 或扩 `spec_factor_feedback.py` — claim「forward outcome 记录方向必须与 opportunity direction 同源」。
- **测试**：`tests/test_universe_signal_emitter.py` — 构造 momentum(ret_4h=None) + event 候选，断言 inserted_members direction 与 emit direction 一致、event 有方向。
- **影响面**：决策链（forward outcome / 胜率统计 / calibration）。**修此一处同时消解 US-4.4 momentum 54% None + US-4.10 event 0 条**。

#### US-3 【perp 错标 spot】`inserted_members` 缺 market_type key
- **根因**：`service.py:251-260` append 的 dict 无 `market_type` key（UniverseMember 表 :230 有），signal_emitter.py:70 全 fallback "spot"。
- **改动**：`service.py:251` inserted_members dict 补 `"market_type": item.get("market_type", "spot")`。
- **spec**：扩 `spec_spot_parity.py` — claim「universe→opportunity market_type 传递不丢失」。
- **测试**：构造 perp member，断言 emit 的 opportunity.market 为 usdt_futures 而非 spot。
- **影响面**：决策链 + 执行路径（perp opportunity 走对适配器）。

#### MO-1 【key_observations PI 不读】`trading_context.py:152-172`
- **根因**：见 §1.B。layer1 SELECT 无 `key_observations`。
- **改动**：`_build_layer1_snapshot` 的 `_select_all` 结果字典补 `key_observations`（解析 JSON，取前 2 条拼进 snapshot 行文本，控制长度）。thesis 截断从 150 提到合理值（如 220）或改为 thesis 摘要 + obs。
- **spec**：扩 `spec_pi_decision_chain.py` — claim「PI layer1 快照必须暴露 key_observations」。
- **测试**：构造带 key_observations 的 open opportunity，断言 layer1 snapshot 文本含 observation 内容。
- **影响面**：决策链（PI 输入信息量）。

#### MO-2 【guard advisory 架构错误】`market_observer.py:531`
- **根因**：见报告 MO-3.2。guard_ctx 用 `"symbols"`(list) 循环外全局构建、缺 scanner_family/direction，`_applies_to_context` 恒 False（学习回路断口 F）。
- **改动**：
  1. 保留循环外的 lessons/memo 全局 memory_block（这部分无 symbol 依赖，合理）。
  2. 在 `_analyze_symbol` LLM 返回结果后、写 opportunity 前，用 `{symbol(单数), scanner_family, direction, confidence}` 单独调 `build_advisory_context` 取 guard advisory，附加到该 opportunity 的注释/日志。
- **spec**：扩 `spec_lesson_guards.py` — claim「market_observer guard 上下文含 symbol+scanner_family+direction」（关联断口 F）。
- **测试**：mock 一条 active guard(applies_to scanner_family)，断言匹配到当前 symbol 的 advisory 非空。
- **影响面**：决策链 + 学习回路（断口 F 闭合）。

#### US-1 【scanner_weights 消费缺失】`allocator.py:135-138`
- **根因**：见 §0/§1.C。`compute_total_score` 只乘 calibration，不读 `cfg.scanner_weights`。
- **改动**：`compute_total_score` 增参数或读 `cfg`（UniverseConfig 已传入）：`opportunity *= calibration.get(family,1.0) * cfg.scanner_weights.get(family, 1.0)`。空 dict → 全 1.0 不改变现状。
- **spec**：扩 `spec_operator_loop.py`（OL-C09 已锁写入）新增 claim「scanner_weights 被 compute_total_score 消费」，或新建 allocator claim。
- **测试**：`tests/test_universe_allocator.py` — 设 scanner_weights={momentum:2.0}，断言 momentum 候选 total_score 相对提升。
- **影响面**：决策链（PI 手调权重终于生效）。

### P1（正确性 + 性能，9 项）

#### US-7 【sync_registry 失败无降级】`service.py:289`
- **根因**：refresh except 直接 status=failed/return None，已有的 `fallback_static_universe`/`_fallback_as_members` 未被调用 → 本 cycle 零 opportunity。
- **改动**：except 分支中，若 sync/collect 失败，用 `_fallback_as_members(cfg)` 生成 core 成员并走 emit，记 warn；snapshot 标记 degraded。
- **spec**：新建 claim「universe refresh 失败时降级到静态池而非空转」。
- **测试**：monkeypatch sync_registry 抛异常，断言仍产出 fallback 成员。

#### US-8 【feat_by_symbol spot/perp 覆盖】`signal_emitter.py:61`
- **根因**：`{f["symbol"]: f}` 同 symbol 覆盖。
- **改动**：改复合 key `(symbol, market_type)`；member 侧同步用 market_type（依赖 US-3）。
- **spec**：关联 spec_spot_parity。
- **测试**：同 symbol spot+perp 两条 feature，断言各自 emit 不串。

#### US-9 【short_enabled=False 被高分绕过】`signal_emitter.py:199`
- **根因**：`_passes_regime_gate` 对 score≥66 无条件豁免，short_enabled=False（short_bias=0）时高分 short 仍通过，违背管理员硬意图。
- **改动**：`_passes_regime_gate` 传入 short_enabled（或 regime 标记），short 全局禁用时 short 方向**硬拦截不豁免**；long 保持原豁免逻辑。
- **spec**：扩 regime/short 相关 claim（`spec_pi_decision_chain` 或新建），断言 short_enabled=False 时任意分数 short 均被拦。
- **测试**：short_enabled=False + score=90 short，断言被 gate 拦。
- **影响面**：决策链 + regime（非风控硬限制，属信号门）。

#### MO-3 【F&G/top_movers runtime 漏填】`context_builder.py:246-256`
- **根因**：`build_market_context_from_db` 组装 MacroSnapshot 未传 fng/top_movers（表有列、collector 在写、渲染层会渲染）。
- **改动**：:246-256 补 `fng_value=macro_row.get("fng_value")`、`fng_classification=macro_row.get(<核准列名>)`、`top_movers_summary=macro_row.get(<核准列名>)`。实施时先核 `macro_snapshots` 表确切列名（migrations.py）。
- **spec**：扩 `spec_macro_signals.py` — claim「runtime context 组装暴露 F&G / top_movers」。
- **测试**：`tests/test_data_collectors.py` — 塞含 fng 的 macro_row，断言 snapshot 文本含 "Fear & Greed"。
- **影响面**：enrichment / paper_broker / PI 上下文（非 market_observer 主路径，需在报告注明影响面）。

#### MO-4 【scanner_family 不进 LLM prompt】`market_observer.py:357`
- **根因**：market_observer 已知 scanner_family(:618) 却不传 LLM，做通用分析而非针对性验证。
- **改动**：user prompt 拼接 scanner_family hint（有值时）。
- **spec**：关联 `spec_agent_contracts.py`（market_observer 契约）。
- **测试**：断言有 scanner_family 时 prompt 含该 family 字样（不 mock-call-verify，检查拼出的字符串内容）。

#### US-5 【BB squeeze O(n²)】`features.py:167-175`
- **根因**：`for i in range(20,min(100,len)): bollinger(closes_1h[:i])` 每资产最多 80 次全量重算。
- **改动**：滑动窗口一次 O(n) 计算 width 序列，取历史均值比较。
- **spec**：无（性能，不改行为语义）。
- **测试**：单测断言新旧 bb_squeeze 判定结果一致（等价性）+ 大数组性能基准。

#### US-6 【price/amount_precision int() 截断】`registry.py:141-142`
- **根因**：CCXT precision 可能是 tick-size 浮点(0.001)，`int()` 截为 0，订单精度丢失。
- **改动**：检测 `precisionMode`（或值 <1 的浮点视为 tick-size），保留原始值/换算为小数位。
- **spec**：无（数据保真），加单测。
- **测试**：price_prec=0.001 与 =3 两种，断言不被截为 0。
- **影响面**：执行边界数据（不改执行逻辑本身）。

### P2（可观测性 / 一致性 / 数据质量，约 10 项）

| ID | 位置 | 修复 | 备注 |
|---|---|---|---|
| US-12 | `service.py` UniverseMember | 加 `all_scores` 列持久化 | 需 migration + model 字段；可观测性 |
| US-13 | `allocator.py:243` | explore pool 过滤 `total_score>阈值` 再 random.sample | 减少 0 分标的进 universe |
| US-14 | `registry.py:120` | demo_whitelist 不匹配时记 debug 日志 | okx_demo 专属，加可观测 |
| US-15 | `registry.py:32` | `"_" in symbol_id` 收窄为更精确判断 | 换交易所前置，加注释 |
| MO-5 | `market_observer.py:420` | annotation 保留原 scanner thesis（追加而非覆写）或只写 key_observations | 保信息 |
| MO-6 | `market_context.py:137` | symbols<2 时不渲染 RS block（避免 Rank 1/1 误导）| 确认单 symbol 调用 |
| MO-7 | `market_context.py:248` | fallback 文本加「⚠️ 数据降级」标注 | 诚实标注 |
| MO-8 | `context_builder.py:118` + normalizer | crowding_score 无数据时渲染 "n/a" 而非 "0/100" | 订正 MO-3.4 |
| MO-9 | `context_builder.py:106` | 表头 crowding 加 glossary（LSR 含义） | 降 LLM 歧义 |
| MO-12 | `market_observer.py:321` | news 加 `created_at > now-24h` 过滤 | 旧新闻不当新信号 |
| MO-13 | `market_observer.py:296` | prediction signals 加 volume 阈值 | 滤零流动性噪音 |
| MO-10 | `retrieval.py:252` | 加最低 score 阈值过滤低相关 lesson | P2-Low |
| MO-11 | `retrieval.py:221` | snippet token 24→48 或多列 | P2-Low |

### P3（效率，2 项）
- **MO-14** `market_observer.py:343` — `load_config()` 移出 per-symbol 循环（用外层 cfg 或传参）。
- **MO-15** memo/`get_latest_memo()` 每 cycle 查库 → 进程内 TTL 缓存（1h）。

---

## 3. 明确「不改」的项（非 bug / 超范围，记录理由）

| 项 | 判定 | 理由 |
|---|---|---|
| US-10 derivatives proxy 弱信号 | 设计选择 | 已有 `is_proxy` 检测 + medium cap（signal_emitter:125），行为正确 |
| US-11 event 稀有 | 非 bug | event 真触发能赢竞争；0 条 forward outcome 由 US-4 修复（direction）解决 |
| US-16 真 4h 特征未被 scorer 消费 | 增强 | rsi_4h/atr_4h/bb_width_4h 已收集，接入 scorer 是能力扩展，非 bug → 记入 `docs/status/ROADMAP.md` |
| MO-3.5 FTS token | 撤销 | 核实不成立（`/` 已正确拆分） |

---

## 4. SDD spec 变更清单

新增/修改的 spec claim（改代码前先写 RED）：

1. `spec_pi_decision_chain.py` — PI layer1 暴露 key_observations（MO-1）；short_enabled=False 硬拦 short（US-9）
2. `spec_spot_parity.py` — universe→opportunity market_type 不丢失（US-3、US-8）
3. `spec_lesson_guards.py` — market_observer guard 上下文含 symbol/family/direction（MO-2，断口 F）
4. `spec_operator_loop.py` — scanner_weights 被 compute_total_score 消费（US-1，关联 OL-C09）
5. `spec_macro_signals.py` — runtime context 暴露 F&G/top_movers（MO-3）
6. `spec_factor_feedback.py` 或新建 `spec_universe_pipeline.py` — forward outcome 方向与 opportunity 同源 + event 有方向（US-4）
7. `spec_agent_contracts.py` — market_observer prompt 含 scanner_family（MO-4）

**收集陷阱**（记忆 aletheia-sdd-spec-collection-trap）：新建 spec 文件必须加进 `tests/test_spec_conformance.py` 的硬编码导入清单，否则 `pytest tests/specs/` 收集 0 个。

---

## 5. 测试覆盖策略

- **单元**：每个 P0/P1 配定向单测（验证数据结果，非 mock-call-verify；DB/audit 真实跑，仅 mock 网络/LLM）。
- **spec**：`uv run pytest tests/specs/ -q` 全绿；`test_spec_conformance` 打印矩阵。
- **回归**：`uv run pytest tests/test_universe_*.py tests/test_market_context.py tests/test_agent_runtime.py -q`。
- **全量**：后台 `uv run pytest tests/ -q --tb=line`（~5min），有失败回头修。
- **doctor**：`uv run aletheia doctor` 验证闭环健康，WARN 不新增。

---

## 6. 文档同步清单（收口纪律：ledger 只进不出会失真）

- `docs/design/agent_contracts.md` — market_observer prompt/guard 上下文契约
- `docs/design/data_utilization.md` — F&G/top_movers 消费路径登记（如状态变化）
- `docs/design/SYSTEM_MAP.md` — universe pipeline 方向统计修复注记
- `docs/design/data_model.md` — 若 US-12 加 UniverseMember.all_scores 列
- `docs/design/api_reference.md` — 无端点变更（预期不动）
- 两份审计报告（`docs/research/*_AUDIT_2026-07-01.md`）— 回填 6 处核实订正 + 标注「已修复」状态
- `docs/issues/LEARNING_LOOP_CHAIN_AUDIT.md` — 断口 F 状态更新（MO-2 修复）
- `docs/status/ROADMAP.md` — US-16（4h 特征接入）记为增强项
- `docs/_ai/AI_HANDOFF.md` / `DEVLOG.md` / `CURRENT_TASK.md` — 会话结束更新（仅意图，不写运行态）
- `docs/_ai/DECISIONS.md` — 记录「方向统计单一数据源」架构决策

---

## 7. 实施批次与顺序

| 批次 | 内容 | 依赖 |
|---|---|---|
| B1 | P0 决策链：US-4 → US-3 → US-8（US-3 前置）→ MO-1 → MO-2 → US-1 | spec-first |
| B2 | P1 正确性：US-7、US-9、MO-3、MO-4 | — |
| B3 | P1 性能：US-5、US-6 | — |
| B4 | P2：US-12/13/14/15、MO-5/6/7/8/9/10/11/12/13 | — |
| B5 | P3：MO-14、MO-15 | — |
| B6 | 全量测试 + doctor + 文档同步 + 报告订正 | 全部完成后 |

每批：先写/更新 spec（RED）→ 实现（GREEN）→ 定向单测 → 下一批。B6 收尾统一验证。

---

## 8. 验证 Gate（完成定义）

- [ ] `uv run pytest tests/specs/ -q` 全绿，新 claim 均 GREEN
- [ ] `uv run pytest tests/test_universe_*.py tests/test_market_context.py tests/test_agent_runtime.py -q` 全绿
- [ ] 全量 `uv run pytest tests/ -q` 无新增失败
- [ ] `uv run aletheia doctor` 无新增 WARN
- [ ] 6 处报告订正回填、两份报告标注修复状态
- [ ] SDD 契约文档同步、`test_spec_conformance` 矩阵含新 claim
