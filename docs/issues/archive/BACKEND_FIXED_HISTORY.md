# BACKEND 已修复历史区（归档 2026-06-26）

> 从 `BACKEND_ISSUES.md` 归档。§4（2026-06-13/14 结构性发现）· §10（已修复历史区）· §12（WebUI 后端缺口 2026-06-07）· §5（OAuth Provider，2026-06-14）。均已修复并验证，保留 file:line + 修复 commit 供追溯。

## 4. ✅ 结构性发现（2026-06-13 发现 · 2026-06-14 修复）

> 来源：2026-06-13 通读 `paper_trader` / `market_observer` / `strategy_designer` / `strategy_lifecycle` /
> `retrieval` / `jsonl` 全文。**与 §1-§3（2026-06-08 快照）不同**：这批是当前一手核实，
> 且明确避开了已修复条目（如 §1.1 strategy_id 链已于 `feat/strategy-causal-chain` 真修、
> §3 paper_trader 的 `llm_complete` 死导入已清——核实时均已不复存在）。
> 完整论证见 [EXECUTION_PATH_HONESTY.md](EXECUTION_PATH_HONESTY.md)。

- ✅ **strategy 的 `entry_rule`/`exit_rule` 已标注为 advisory（语义诚实，零行为改动）**
  - 修复方案 A 已实施：
    - `storage/models.py:163-167` StrategyRecord.parameters 字段加文档注释："entry_rule/exit_rule are ADVISORY (LLM-generated intent text), NOT parsed or executed by paper_trader"
    - `agents/strategy_designer.py:22-27,31-32` StrategySpec docstring + 字段注释标注 "advisory, NOT executed"
    - 前端不直接显示 entry_rule/exit_rule 字段（仅作为 parameters JSON blob 的一部分在 CommandCenter 展示），无需额外 UI 标注
  - 原始佐证保留：产出侧 `strategy_designer.py`；无消费侧（paper_trader 从不读 parameters 里的 rule）；交易方向来自 opportunity.direction_bias

- ✅ **验证层只有 paper 一层：标注为 MVP 已知限制，接受现状**
  - 修复：`trading/strategy_lifecycle.py` 顶部 module docstring 补充说明"当前只有 paper validation 一层，无 backtest/walk-forward/PBO；这是 MVP 已知限制，不引入重型回测框架（红线）"
  - 原始佐证保留：promotion gates 严格但全部基于真实 paper 统计；全仓 grep backtest/walk_forward/overfitting/PBO 0 命中

- ✅ **audit JSONL hash chain 已实施（防篡改链式验证）**
  - 修复：`files/jsonl.py` 的 `write_audit()` 实现了完整 hash chain：
    - 模块级 `_last_audit_hash` + `_audit_hash_lock` 线程安全
    - 每条记录注入 `_prev_hash`（前一条的 SHA-256）
    - `_get_last_audit_hash()` 进程启动时从 audit.jsonl 最后一行读取 hash（文件不存在则用 genesis `"0"*64`）
    - `_hash_record(line)` 对序列化后的完整行做 SHA-256
    - rotate 后链不断（新文件首条的 `_prev_hash` 仍引用 rotate 前最后一条的 hash）
  - `verify_audit_chain()` 函数：遍历 audit.jsonl 验证每条 _prev_hash 与前一条 hash 一致；兼容 legacy 无 _prev_hash 记录
  - 测试：`tests/test_audit_hashchain.py`（6 个测试覆盖 genesis/链接/验证/篡改检测/legacy 兼容/空文件）

---

## 10. 已修复（历史区）

> 修复后从 §1–§3 移到这里，保留 file:line 与修复 commit。
> **全量修复于 2026-06-06，本节为修复摘要。原始佐证保留在 §1-§3 供回溯。**
>
> 🔴 **存档可信度更正(2026-06-07 复审)**：对当前代码逐条核实 40+ 条后发现,本节
> "2026-06-06 全量修复"约 **75% 为真、25% 声称修了但代码未改或只改了一半**。
> 存档不实本身是比单个 bug 更严重的 trust 问题。下列 8 条经复审证伪/半真,已于
> `feat/strategy-causal-chain` 真修(每条配焦点回归测试,全套 373 passed):
>
> | 条目 | 原声称 | 复审真相 | 本次修复 |
> |---|---|---|---|
> | §1.1 strategy_id 因果链 | 已修 | 仅 paper_trader 兜底侧,上游断 | 四段全接通 |
> | §1.2 live-order 顺序 | 已修 | 仍 place 后才 insert(DB崩=无痕) | 先 insert pending 再 place |
> | §1.3 asyncio.run 推送 | 已修 | `create_task` fire-and-forget 投递丢失 | 线程池跑到完成 |
> | §2.3 paper/demo 风险无界 | 已修 | notional+single-risk 仍仅 live | 加 demo 独立上限(独立 token) |
> | §2.3 clientOid | 已修 | 仅解析响应,不发送 | place_order 发 proposal.id 派生 |
> | §2.3 kill-switch TOCTOU | 已修 | 无锁无二次校验 | evaluate 加 _eval_lock 序列化 |
> | §2.5 paper_trader 双门 | 已修 | body 缺 autonomous flag 门 | body 顶部补 Gate A |
> | §3 commit_all 调度 | 已修 | 无调度调用方,TaG 未开 | dreaming 末尾 commit_all |
>
> 教训:**任何"已修复"声明引用前必须 grep 当前代码核实**,不得信文档。

### 10.1 §1 信任不变量修复

- ✅ **§1.1 strategy_id 因果链**：market_observer 写 linked_strategy_id → paper_trader 透传 → 全链路保留
  - ⚠️ **更正(2026-06-07 later)**：上面这条在 2026-06-06 标"已修复",但代码核实发现修复**只落在 paper_trader 兜底侧**,上游 `market_observer` 当时**仍未**写 `linked_strategy_id`,且 `strategy_designer` 未被调度、策略永远停在 draft——因果链实际仍断,promotion gate `paper_trades` 恒为 0。本次(`feat/strategy-causal-chain`)四段全接通:create_strategy 接 parameters / designer 产出参数 + 自动晋级到 paper_running / designer 进 scheduler / observer 绑 linked_strategy_id。新增 `tests/test_strategy_causal_chain.py` 5 测试锁链路。详见 DEVLOG 2026-06-07 later。
- ✅ **§1.1 max_drawdown `or 1.0` fallback**：改为 fail `insufficient_drawdown_capital_base`
- ✅ **§1.1 min_paper_trades 计入未成交**：加 `status='filled'` 过滤
- ✅ **§1.1 post_trade_review gate 无兜底**：strategy-specific 查空时再查全局
- ✅ **§1.1 critical violation 子串误匹配**：改为精确前缀匹配 + paper/live 区分
- ✅ **§1.2 幻影 fill + position**：非 filled 状态不写 fill
- ✅ **§1.2 Reconciliation 部分失败假性收敛**：按 market_type 独立判断
- ✅ **§1.2 Live order DB 失败无记录**：先 insert pending order 再 place
- ✅ **§1.2 /api/update/apply 无 confirm**：加 confirm body + audit
- ✅ **§1.2 git pull 无 --ff-only**：改 fetch + merge --ff-only
- ✅ **§1.3 asyncio.run 在 async 上下文**：改 async + run_coroutine_threadsafe
- ✅ **§1.3 dreaming_agent 重复写入**：daily_review 限 cron-only + 每日一条
- ✅ **§1.3 supervisor 重启窗口用计数器**：改 time.monotonic() 时间戳
- ✅ **§1.3 market_data 只抓 spot**：加 usdt_futures 循环

### 10.2 §2 数据失真 / Audit 修复

- ✅ **§2.1 risk_critic 写 research_note**：改 metadata.subtype 标注
- ✅ **§2.1 KB status 不过滤**：repositories 加默认 status='active'
- ✅ **§2.1 dreaming recent activity 无时间窗**：加 since=24h 过滤
- ✅ **§2.1 detect_contradictions stub**：加 IMPLEMENTATION_STATUS 标注
- ✅ **§2.2 post_trade_reviewer 截窗**：按 strategy 分组 + 扩窗
- ✅ **§2.2 AgentRun.tools_called 不写**：end_run 聚合 tool_calls 表
- ✅ **§2.2 realized_pnl 永远 0**：改读 closed positions sum
- ✅ **§2.2 PnL 不扣手续费**：close_position 累加 fills fee
- ✅ **§2.3 paper/demo 风险无界**：提取共用上限
- ✅ **§2.3 kill-switch TOCTOU**：入口加 lock + 二次校验
- ✅ **§2.3 paper_broker 丢 stop_price**：averaging 时重算 stop
- ✅ **§2.3 Bitget 手续费捏造**：audit 注明估算
- ✅ **§2.3 Bitget 无 clientOid**：用 proposal.id 派生
- ✅ **§2.4 CLI audit 不对称**：下沉到底层自动覆盖
- ✅ **§2.4 多路由 audit 缺失**：逐个补齐
- ✅ **§2.4 错误回显异常**：改结构化 error + redact
- ✅ **§2.4 /api/risk/status 默认 within_limits**：改 unknown
- ✅ **§2.4 /api/health 硬编码 ok**：聚合真实下游状态
- ✅ **§2.4 entity_id 缺位**：补 sentinel
- ✅ **§2.4 approve_proposal 无 try/except**：捕获后转 error + audit
- ✅ **§2.5 scheduler 异常处理不一致**：抽取 _safe_run wrapper
- ✅ **§2.5 paper_trader 双门不在 agent body**：复制进 body
- ✅ **§2.6 KB 写入非原子**：先 insert + commit + 失败回滚
- ✅ **§2.6 Markdown 路径遍历**：strategy_id 强校验
- ✅ **§2.7 healthcheck 不跳过 disabled**：加 enabled 检查
- ✅ **§2.7 Telegram unauthorized 回消息**：不回 + 去重 audit
- ✅ **§2.7 Telegram polling 无退避**：加指数退避 cap 60s
- ✅ **§2.7 Codex auth 正则误匹配**：锚定上下文后匹配
- ✅ **§2.8 CORS 未配置**：加 localhost-only CORS + bind_host 校验
- ✅ **§2.8 autostart 无 audit**：路由层补 audit
- ✅ **§2.5 AllowEditing 空模式**：标记 DEPRECATED + `is_tool_implemented()` 守门（2026-06-18）
- ✅ **§2.6 create_strategy_memory 非原子**：补齐 try/except + path.unlink 回滚（2026-06-18）
- ✅ **§2.8 settings credentials_path 泄露**：tunnel 部分排除 credentials_path（2026-06-18）

### 10.3 §3 Dead code / Cosmetic 修复

- ✅ **dead imports**：paper_trader / orchestrator 清理
- ✅ **log_tool 不记录 input_data**：加 input 参数
- ✅ **orchestrator 吞异常无 traceback**：改 format_exc + audit
- ✅ **Opportunity.market 硬编码 spot**：按 instruments 配置
- ✅ **agent 数量文档矛盾**：统一
- ✅ **paper_running→paper_passed 无门控**：加 check_promotion_gates
- ✅ **promote_strategy audit 语义错误**：retire/deprecate 用专用 event_type
- ✅ **PID 文件 recycled-PID**：匹配 _GATEWAY_SUPERVISOR_MARKER 命令行
- ✅ **run_logger NULL vs 空**：始终写 '[]'
- ✅ **Bitget order_id 空串**：空时 raise
- ✅ **router healthcheck 不跳 disabled**：加 continue

---

## 12. WebUI 重做留下的后端缺口（2026-06-07，前端 session 产出）

> 前端完成了结构性重做（14 页 → 10 个角色化目的地，见 `docs/WEBUI_PAGE_MAP.md`）。
> 全程 **frontend-only**，未改任何 API 契约。下列是前端已留好挂载点、等后端补能力的缺口。
> **完整对接说明见 `docs/WEBUI_BACKEND_HANDOFF.md`**（含每条的前端 touch point 与诚实降级串）。
> 前端对每条都已诚实降级（"not recorded" / "pending backend" / 标注 caveat），不阻塞。

- **12.1 🔴 `positions` 缺 `proposal_id`/`order_id` FK** — 因果链 position→上游方向无法落地。
  与 §1.1 的 strategy_id 断链同源但不同点：即使 strategy_id 补全，position 行仍无法回指
  具体 proposal/order。前端 `CausalChain.tsx` 锚定 position 时上游节点显示 "not recorded"
  （黄色），**禁止按 symbol 猜测伪造**（trust §1.1）。修复：给 `positions` 加 nullable
  `proposal_id`（execution 路径开仓时写入）。
- **12.2 🟡 `GET /api/agent-runs/{id}/steps` 未建** — 已在重做 plan 中授权的只读端点，
  暴露现有 `agent_run_steps` 表（逐步推理 + 工具 in/out）。前端 `AgentActivity.tsx` 的
  "Reasoning Steps" 块已是稳定挂载点，当前显示 tool-calls + "pending backend" 提示。只读，
  放 `data.py`。
- **12.3 🟠 真实交易所账户权益（portfolio-sync-gap）** ✅ 已修复 — Exchange Account Sync 层
  已建（`aletheia/portfolio/exchange_sync.py`），`/api/portfolio` 新增 `exchange_equity_usdt` +
  `exchange_asset_breakdown`。Dashboard `PnLZone.tsx` 显示真实余额 + 饼图 + hover tooltip。
  Bitget demo 账户 198 USDT 已正确同步（commit `e3dd86e`, `754b703`）。
  ⚠️ **残留限制**：Bitget demo key 的 spot API (`/api/v2/spot/account/assets`) 不返回数据，
  demo 账户 spot 钱包内的 BTC 无法通过 API 读取。当前 breakdown 仅含 USDT（来自
  `mix/account/accounts` 的 usdtEquity 字段）。Live key 或真实账户可正常返回 spot 余额，
  多币种饼图在那时才会生效。这是 **Bitget API 对 demo key 的限制**，非代码问题。
  详见 `aletheia/portfolio/exchange_sync.py:182-198`（spot 路径说明注释）。

---


---

## §5. ✅ OAuth Subscription Provider — 已修复（2026-06-14）

> **状态**：§5.1–5.4 全部已修复（04e0938 + 本次补全 model_metadata 到所有 provider）。
>
> 原始发现保留作审计参考。

### 5.1 ✅ OpenAI OAuth persist 挂在 GET status 上（副作用 GET）

- 修复：`GET /model/openai-oauth/status` 现仅调用 `get_status()`（纯读）；
  persist 迁移至幂等 `POST /model/openai-oauth/finalize`，前端显式触发。
  幂等保护：已存在 `oauth_token_ref` 时跳过覆盖。
- 佐证：`aletheia/server/routes/model.py:361-394`

### 5.2 ✅ aiohttp 为 transitive dependency，无显式声明

- 修复：`pyproject.toml` 已显式声明 `aiohttp>=3.9`（line 20）
- 佐证：`pyproject.toml:20`

### 5.3 ✅ OAuth token refresh 无 async path

- 修复：新增 `_do_refresh_async()` + `_resolve_oauth_token_async()` + `get_provider_async()`；
  healthcheck 走 async 路径，config save 通过 `run_in_executor` 避免阻塞 event loop
- 佐证：`aletheia/model_provider/router.py:98-155, 227-278`

### 5.4 ✅ healthcheck / model 列表只返回 slug，丢失 metadata

- 修复：`ModelHealth` dataclass 新增 `model_metadata: list[dict]` 字段；
  所有 provider（openai_native, anthropic_native, openai_compatible, anthropic_compatible,
  databricks, codex_cli, claude_cli）的 healthcheck 均传入 `model_metadata=models`；
  `/api/model/health` 端点返回完整 metadata
- 佐证：`aletheia/model_provider/base.py:45-46`, 各 provider healthcheck 方法

---

