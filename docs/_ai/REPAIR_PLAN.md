# 修复计划 — Backend & Frontend Issues 综合排期

## 2026-06-15 P0/P1 status amendment

- This file remains a historical planning record; current priorities were re-ranked around P0/P1 trust hardening.
- Implemented P0/P1 scope: audit JSONL concurrency/rotation verification, gateway-startup stale agent_runs reconciliation, operator shell pidfile single-instance protection, README test wording, and package version fact sync.
- This round did not add trading capability, backtest execution, DSL execution, risk-limit changes, live unlock, exchange expansion, or market expansion.
- The W4.2 note about a missing hash chain is now historical background; use aletheia/files/jsonl.py and tests/test_audit_hashchain.py as the current source of truth.


> **生成日期**：2026-06-14
> **基于**：`docs/issues/BACKEND_ISSUES.md`（§4, §5, §12 未修复区）+ `docs/issues/FRONTEND_ISSUES.md`（§4, §5, §6.7 未修复区）
> **原则**：§1-§3 + §10 历史区已于 2026-06-06/07 修复（75% 真修、25% 经二次修正补齐于 `feat/strategy-causal-chain`）。本计划只针对**当前确认未修复**的条目。
> **不构成自动施工授权**——涉及 change-controlled 模块的条目需用户逐项批准。

---

## 总览：未修复条目清点

| 来源 | 等级 | 条目数 | 备注 |
|------|------|--------|------|
| Backend §4 (2026-06-13) | 🟠 | 3 | 结构性发现：entry_rule 死字符串、缺 backtest、audit 无 hash 链 |
| Backend §5 (2026-06-14) | 🟠🟡 | 4 | OAuth provider 残留 |
| Backend §12 (WebUI 后端缺口) | 🔴🟡 | 2 | positions FK + agent-runs/steps 端点 |
| Frontend §4 (2026-06-14) | 🟡 | 2 | OAuth UI polish |
| Frontend §5 (2026-06-08) | 🟠🟡 | 4 | Markets 页数据展示 |
| Frontend §6.7 (2026-06-14) | 🟡 | 3 | 设计系统 polish 存量 |
| **合计** | | **18** | |

---

## Wave 1：信任链补全（🔴 优先级最高）

> 目标：消除因果链断点 + 补齐必要 API 端点。
> 预计：1-2 session。

### W1.1 — `positions` 表缺 `proposal_id`/`order_id` FK

- **来源**：Backend §12.1 🔴
- **影响**：因果链 position→上游方向无法落地；前端 CausalChain 组件永远显示 "not recorded"
- **修复**：
  1. `aletheia/storage/migrations.py` — ALTER TABLE positions ADD `proposal_id` TEXT NULLABLE
  2. `aletheia/execution/gateway.py` — 开仓时写入 `proposal_id`
  3. `aletheia/execution/paper_broker.py` — 同步写入
  4. 回归测试：断言 position 行能回指 proposal
- **约束**：触及 `execution/` change-controlled，需授权

### W1.2 — `GET /api/agent-runs/{id}/steps` 端点

- **来源**：Backend §12.2 🟡
- **影响**：前端 AgentActivity "Reasoning Steps" 块为占位态
- **修复**：
  1. `aletheia/server/routes/data.py` — 新增只读路由，查 `agent_run_steps` 表
  2. 前端已有挂载点，无需前端改动
- **约束**：只读端点，风险低

---

## Wave 2：OAuth Provider 完善（🟠 中优先级）

> 目标：让 OAuth subscription 路径从"能用"到"可靠"。
> 预计：1 session。

### W2.1 — OpenAI OAuth persist 从 GET 移到主动路径

- **来源**：Backend §5.1 🟠
- **修复方案**：新增 `POST /model/openai-oauth/finalize`，前端 login 完成后显式调用；
  加幂等保护（检查 `oauth_token_ref` 已存在则跳过）
- **文件**：`routes/model.py`, `frontend/src/pages/Settings.tsx`

### W2.2 — aiohttp 显式声明

- **来源**：Backend §5.2 🟡
- **修复**：`pyproject.toml` 加 `aiohttp>=3.9` → `uv lock`
- **风险**：极低，一行配置

### W2.3 — OAuth token refresh async path

- **来源**：Backend §5.3 🟡
- **修复**：router.py 新增 `async _do_refresh_async()`；healthcheck 和 get_provider 的
  async 路径 await 它；config save 用 `run_in_executor` 避免阻塞 loop
- **文件**：`model_provider/router.py`

### W2.4 — healthcheck 返回 model_metadata

- **来源**：Backend §5.4 🟡 + Frontend §4.1 🟡
- **修复**：
  - 后端 `GET /api/model/health` 增加 `model_metadata: list[dict]` 字段
  - 或新增 `GET /api/model/{provider}/models` 返回完整 metadata
  - 前端 OAuth panel 复用 HTTPProviderForm 的 model combobox（带 tier badge）
- **文件**：`routes/model.py`, `Settings.tsx`

### W2.5 — OAuth disconnect 按钮

- **来源**：Frontend §4.2 🟡
- **修复**：
  - 后端新增 `POST /model/openai-oauth/disconnect` + `POST /model/claude-oauth/disconnect`
    （清 oauth_token_ref/refresh_ref + auth_type 回 api_key + audit）
  - 前端 connected 态增加 "Disconnect" 按钮
- **文件**：`routes/model.py`, `Settings.tsx`

---

## Wave 3：Markets 页数据展示（🟠🟡）

> 目标：让 Charts/Markets 页的信息密度和数据新鲜度达到可用标准。
> 预计：1 session。

### W3.1 — 数据自动刷新

- **来源**：Frontend §5.3 🟡
- **修复**：`Charts.tsx` useQuery 加 `refetchInterval: 30_000`；watchlist card 加 "last updated" 指示
- **投入**：5 分钟

### W3.2 — symbol 格式统一到 canonical

- **来源**：Frontend §5.4 🟡
- **修复**：`Charts.tsx` SYMBOLS 改 `['BTC/USDT', 'ETH/USDT', 'SOL/USDT']`
- **投入**：10 分钟

### W3.3 — 数据来源标注

- **来源**：Frontend §5.1 🟠
- **修复**：watchlist card 加 `via {exchange} (CCXT)` chip；需后端 `Ticker` 增加 exchange 字段或新增 `/api/market/status`
- **文件**：`Charts.tsx`, 可能需 `aletheia/data/ccxt_provider.py`

### W3.4 — Watchlist H/L 范围

- **来源**：Frontend §5.2 🟡
- **修复**：`Ticker` dataclass 加 `high_24h`/`low_24h`；CCXT provider 填充；前端 card 显示
- **文件**：`providers.py`, `ccxt_provider.py`, `Charts.tsx`

---

## Wave 4：结构性设计（🟠 需讨论后决策）

> 目标：处理代码诚实性 + 长期可审计性问题。
> 预计：需先做 DECISIONS.md 记录设计决策，再择机实施。

### W4.1 — entry_rule/exit_rule 诚实性标注

- **来源**：Backend §4.1 🟠
- **选项**：
  - (A) 标注 "advisory, NOT executed"（零行为改动，最快）
  - (B) 下单前加确定性 gate 验证方向（中等）
  - (C) 让 entry_rule 结构化被 paper_trader 解析执行（远期 Phase 2A）
- **建议**：先做 (A) 立即消除语义欺骗，(B)/(C) 待策略框架 v2

### W4.2 — audit JSONL hash 链

- **来源**：Backend §4.3 🟠
- **影响**：无链式防篡改 → "Trading-as-Git" 名实不符
- **修复**：每条 audit 记录增加 `prev_hash` 字段（SHA-256 of prev record）；验证命令 `aletheia audit verify`
- **约束**：需设计好迁移（现有记录补 null hash 基线）

### W4.3 — 缺 backtest 层（记录但推迟）

- **来源**：Backend §4.2 🟠
- **决策**：当前 paper 路径 + Bitget demo 真实模拟盘是唯一验证源；不引入重型回测框架（红线）。
  远期可考虑轻量回测原语。**标记为 deferred**。

---

## Wave 5：前端 Polish 存量（🟡 低优先级）

> 目标：设计系统一致性收尾。
> 预计：散布在日常迭代中，不单独排期。

### W5.1 — off-grid 间距存量迁移

- **来源**：Frontend §6.7
- **范围**：CommandCenter（~1380行）+ Lens 块仍有 ad-hoc rem
- **做法**：新代码强制用 `var(--space-N)`；存量在触碰相关文件时逐步收敛

### W5.2 — CommandCenter / Charts 内联样式

- **来源**：Frontend §6.7
- **做法**：同上，触碰时改

### W5.3 — 响应式断点统一

- **来源**：Frontend §6.7
- **范围**：23 个 `@media` 散在 700/768/900/1100/1180/1200
- **做法**：定义 `--bp-sm/md/lg/xl` 四档，逐步收敛

---

## 排期建议

| Wave | 优先级 | 依赖 | 预计时间 | 状态 |
|------|--------|------|----------|------|
| W1 | P0 | 需授权(execution/) | 1-2 session | ✅ 完成 |
| W2 | P1 | 无 | 1 session | ✅ 完成 |
| W3 | P1 | 无 | 1 session | ✅ 完成 |
| W4 | P2 | 需设计决策 | TBD | ✅ 完成 (W4.3 deferred by design) |
| W5 | P3 | 无 | 持续 | ✅ 完成 |

---

## 跟踪规则

1. 每个条目修复后，在对应 issues 文件中标注 ✅ + commit hash 并移入 §10
2. 本文件更新"状态"列
3. 涉及 change-controlled 模块的修复，先在 `docs/_ai/DECISIONS.md` 记录设计决策
4. 每个 Wave 完成后跑 `uv run pytest -q` 全量回归
