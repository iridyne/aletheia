# Discussion Action Plan — Historical Decomposition
> 来源：2026-07-03 owner 口述，已于 2026-07-12 整理为
> [Product Direction](../../../product/definition/DIRECTION.md) 及其链接的 L2 契约、issue ledger 和运行手册。
> 本文保留当时的拆解、优先级与判断过程，**不是**当前计划或当前系统事实；新工作从
> `workflow bootstrap`、对应 L2 契约和 issue ledger 开始。

---

## P1 — 核心功能断点（直接影响交易质量）

### 1. ⬜ Learning 子模块全链路修复与重设计

**背景**：口述核心诉求。现有 learning 闭环多处断口（见 `aletheia-learning-loop-state-2026-07-01` 记忆），导致 AI 无法真正从历史交易中学习进化。

**目标**：
- 对每一笔完成的交易做完整 ETL：入场/出场价、止盈止损点位、预期 vs 实际盈亏比、推理依据
- 清洗后写入 Knowledge Base（可被 System Operator 检索）
- 新规则可修补/推翻旧规则（self-evolving，不是只增不改）
- 连通 Universe scanner 的反馈：哪个 pattern 发掘的标的、这次表现如何

**当前断口**（按记忆汇总）：
- 断口 A/D/E/G 已修（3e042d0）；断口 F 未修（market_observer guard_context 缺字段）
- reflection 写入 SyntaxError 静默吞（5be7826）
- lesson → policy 整条链未建
- ranker 惩罚仅 16 条可见

**下一步**：读 `docs/issues/audits/LEARNING_LOOP_AUDIT_2026-06-26.md`，对照断口 F 和 lesson→policy 链，写 spec claim 后补实现。

---

### 2. ⬜ Universe 选币池方向性偏斜修复

**背景**：owner 观察到选出的标的理由几乎清一色 momentum，mean reversion / short bias / volatility 极少出现，说明 scanner pattern 注册或权重存在偏斜，自学习没有纠正这个问题。

**检查点**：
- `run_all_scanners` 注册点，各 family 的实际调用比例（参考记忆 `aletheia-weak-spec-fake-green-trap`：short_bias 分支曾是死码）
- regime 评分是否真正影响方向权重（`aletheia-ranker-regime-direction-fix`：bear 市 short 候选应 +15 非 -20）
- Learning 惩罚是否反馈到 scanner 选择（目前疑似没有）

**下一步**：跑 `doctor` + 统计最近 200 条 opp 的 scanner family 分布，确认偏斜存在后补 spec claim 再改代码。

---

## P2 — UI/UX 可用性（影响日常操作效率）

### 3. ⬜ 顶栏实时价格显示不稳定

**背景**：BTC/ETH/SOL 实时价格栏有时变成横线，问题已存在多轮，修了两次没根治。

**诊断方向**：
- 前端 SSE/WebSocket 连接是否在断连后正确重连
- 后端 price feed 数据源（market observer 扫描节奏）与顶栏推送的解耦度
- 是否和 `aletheia-frontdoor-disconnect-真根因` 记忆中的 frontdoor ReadError 有关

**下一步**：打开前端 DevTools → Network，复现断线，看 SSE 请求状态码和重连时序。

---

### 4. ⬜ 前后端数据延迟 / 页面切换无加载态

**背景**：主页面间切换等待久，skeleton 占位但没有进度条，体感"卡"。

**可能根因**：
- 某些 API handler 仍走冷路径（未命中缓存），见 `ASYNC_PERFORMANCE_LESSONS.md`
- 页面组件 mount 时串行发多个请求，而非并行
- 无 loading spinner / progress bar，用户无反馈

**下一步**：用 DevTools Performance 录制一次页面切换，找出最慢的请求，直连 `:8788` 冷测对应 endpoint（`Measure-Command { Invoke-WebRequest ... }`）。

---

### 5. ⬜ Trades & Positions 页面布局优化

**背景**：Order Fields 卡片横占全宽，Closed Positions 被推到页面最底部，看盈亏需要滚动。

**改动**：
- Order Fields + Closed Positions 改为左右并排（各占约 50% 宽，或 40/60）
- Analytics + Insights 两个 subtab 合并为一个（两者本质都是"历史交易分析"）

**注意**：改布局前先确认这两个子页面的数据来源是否有差异，合并后保留更完整的那套数据。

---

### 6. ⬜ 开仓使用限价单而非市价单

**背景**：owner 明确要求：入场挂限价单（maker），避免 taker 费率损耗，同时强制在下单前确认入场价/止盈/止损/盈亏比。

**当前状态**：需确认 paper_trader 和 execution adapter 的下单类型默认值。

**改动方向**：
- System Operator 生成 TradeProposal 时必须包含 entry_price（限价）、stop_loss、take_profit、risk_ratio 四个字段，缺任何一个拒绝下单
- 执行 adapter 默认 `order_type=LIMIT`，市价单需显式 override
- 前端 TradeProposal 审批卡片展示上述四字段

---

## P3 — 架构/命名一致性（技术债，不阻塞功能）

### 7. ⬜ "PI Agent" → "System Operator" 全项目改名

**背景**：owner 在 discussion 中强调"希望这个改名可以贯彻得更彻底一点，整个项目中所有有关这个的表达都要一次性改过来"。

**范围**（需 grep 核实）：
- Python 代码变量名、注释、日志字符串中的 `pi_agent` / `PI Agent` / `pi agent`
- 文档（`docs/`）中的称呼
- 前端组件文字、tooltip
- 数据库字段名（**谨慎**：字段改名需迁移脚本）

**注意**：`pi_daemon.py`、`pi_decisions` 表名等技术实现名称改动成本高，可先改 display 层（UI 文字、日志描述），技术名留到下一次大重构。

---

### 8. ⬜ 侧边栏结构大改

**背景**：owner 认为当前左侧侧边栏模块归类已不合时宜，需整体重组。

**已知变化**：
- Strategy 子模块已删除（Phase C 已合 master）
- Serenity S 不应归在 Evolve/Learning 下，更像独立分析路径
- Evolve 模块下实际有用的只剩 Learning 子模块

**下一步**：owner 需要给出新的侧边栏结构草图，再由 AI 实现。当前先不动，等 owner 确认新 IA（信息架构）。

---

## P4 — 战略性/高成本决策（需谨慎评估）

### 9. ❌ 框架语言迁移到 TypeScript

**owner 动机**：前后端同语言，减少数据传输摩擦。

**评估结论（建议维持 Python）**：
- 现有 Python 后端 ~8000 行，含 515+ 测试、完整 SQLite/async 基础设施，迁移成本极高
- TypeScript 在 async 并发、科学计算（numpy/pandas 类操作）上没有优势
- 前后端数据摩擦根因是 API 设计和缓存问题，不是语言问题（见 P2 #4）
- operator-shell 已经是 TypeScript，前端也是，前后端协议通过 OpenAPI/类型生成可对齐

**替代方案**：生成 TypeScript 类型定义（从 Pydantic schema 自动导出），前端用强类型调用 API，而非迁移整个后端。

**结论**：此项延后，不纳入近期计划。

---

### 10. ⬜ pytest 测试速度优化

**背景**：全量跑约 5 分钟，影响开发迭代节奏。

**当前策略**（已在 CLAUDE.md 中记录）：日常只跑针对性子集（`-k "xxx"`），全量后台异步跑。

**可改进方向**（如果确实要提速）：
- 找出最慢的前 20 个测试（`pytest --durations=20`）
- SQLite setup/teardown 是主要瓶颈：考虑用 in-memory DB 替代部分 fixture
- 标记 `@pytest.mark.slow` 分离慢测试，日常跳过

**优先级**：低，当前 workflow 已有绕过方法，不阻塞开发。

---

## 附：System Operator 职能扩展（跨条目设计说明）

owner 描述的 System Operator 三大职能：
1. **交易决策**：结合 Knowledge Base + Market Observer 信号，决定开/平仓（现有核心功能）
2. **知识消费**：主动检索 Learning 子模块产出的规则/教训，用于下一次决策
3. **运行时自诊断**：调用 `/_infra/doctor` JSON 接口，发现 runtime issue 后自主处理（不涉及代码修改）—— 这部分已有骨架（`aletheia-operator-self-supervision`），Phase 4 自动修仍 defer

职能 2 依赖 P1 #1（Learning 链路修复）完成后才有数据可消费。

---

## 执行顺序建议

```
P1-#1 Learning 修复  ←  最核心，其他模块都依赖它有数据
P1-#2 选币池偏斜     ←  可与 #1 并行，独立诊断
P2-#3 顶栏价格       ←  快速 bug fix，独立
P2-#4 页面加载态     ←  独立，DevTools 诊断优先
P2-#5 布局优化       ←  纯前端，独立
P2-#6 限价单机制     ←  改 paper_trader + execution adapter
P3-#7 改名           ←  批量 sed，风险低，可单独一个 commit
P3-#8 侧边栏结构     ←  等 owner 给 IA 草图
P4-#10 测试提速      ←  低优先级，随时可做
P4-#9  TS 迁移       ←  延后
```
