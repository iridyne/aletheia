# Event: Demo↔Live 现货平价研究（Spot Parity）

> 2026-06-28 | 类型：研究 event（owner 触发）| 状态：**实测已坐实 OKX demo 支持现货 → 进入 SDD-first 实现**
> 触发源：owner 提出「Demo 推演应无限向 Live 环境靠近——Live 有现货，Demo 也要有」
> 关联：`docs/archive/legacy/plans/market-data/futures_integration_plan.md`（永续侧已完成）、`aletheia-symbol-display-convention`、`aletheia-okx-contract-size-trap`

---

## 0. 这份文档是什么

owner 给出四条指导原则后，要求开 event 研究「各交易所 demo 是否支持现货、如何让 demo 向 live 收敛」。本文是**研究产出 + 在四原则约束下的架构决策 + SDD-先行实现清单**。

**本文不写代码、不改 schema、不改 spec。** 任何实现都要在此文拍板后，按 SDD 流程先补/改 spec，再动代码。

### Owner 四原则（本文的约束）

1. **Demo↗Live 收敛**：Demo 是 Live 的逼近，能力集向 Live 看齐；Live 有现货，Demo 就该有现货可推演。
2. **核心逻辑交易所无关**：核心目标恒定，差异只在 **adapter 边界**适配，**绝不为某交易所改核心逻辑**。
3. **AI 偏向随情况定**：现货/永续无优劣，不写死默认偏好，按实际行情/资金费率/流动性等情况决策。
4. **SDD 先行**：spec 先于代码定义，绝不让代码跑在文档前面，否则脱离管控。

---

## 1. 核心问题与结论

**问题**：当前传统加密实际只能开永续（见 §2 根因）。要让现货也能按情况开关仓，前提是「demo 环境支不支持现货」——这决定了是**纯改代码**（demo 可推演现货）还是**必须动 live 实盘**（demo 不支持现货 → 触信任边界，需 owner 显式授权）。

**结论（按交易所，置信度分级）**：

| 交易所 | demo 现货支持 | 置信度 | 证据 |
|---|---|---|---|
| **OKX** | **已坐实支持** | ✅ 实测（2026-06-28） | demo 沙盒列出 522 个现货标的（BTC/ETH/SOL/USDT 均 active spot）；用 demo 凭证 `set_sandbox_mode(True)`+`defaultType=spot` 真下单：现货市价买入成交（BTC free 0→0.00033）+ 卖回平仓全程通过。**纯属我们代码自我设限（§2）**。 |
| **Bitget** | **很可能只支持永续** | 中高 | 官方 demo trading 文档挂在 **Futures/contract** 目录下；代码注释 `paper_trader.py:11`「Bitget demo only supports USDT futures」（历史实证）。 |
| **Binance** | 现货/永续 demo 分属两套 testnet | 中（未接入运行） | Binance spot-testnet 与 futures-testnet 是两个独立环境/域名，不在当前执行路径。 |

**当前活跃执行所 = OKX demo**（`data/config.yaml`：`paper_execution_mode: okx_demo` / `ccxt_exchange: okx` / `allowed_exchange: okx`）。

→ **乐观判断**：OKX demo 大概率能推演现货，那么"让 demo 有现货"主要是**改 adapter + 解除自我设限**，不必动 live。但需 §5 的一次实测坐实，再进入实现。

---

## 2. 根因：现货被卡在哪（实际只走永续）

决策链**核心逻辑层**其实已支持现货（Round 2 解除了 observer 的 spot-only guard）：

- 标的池同一币有现货+永续两条目（`universe/service.py`、`registry.py`）。
- `market_observer.py:456` 对现货+永续都发机会，market_type 从标的池继承。
- 风控对现货/永续分别检查 `enabled_markets`，当前两者皆 `true`，**不拦现货**（`risk/engine.py:97`）。

被卡死成永续，全在**两处自我设限**（都不是核心逻辑，是 venue/adapter 层泄漏与提示偏向）：

### 卡点 A —— OKX adapter 写死永续（违反原则 2：venue 限制泄进执行核心）
- `exchanges/okx/client.py:93` — `defaultType: "swap"`（含 `load_demo_markets:276`）。
- `exchanges/okx/client.py:141` — `place_order` 无条件 `to_ccxt_perp(order.symbol)`，**没有 market_type 分支**，现货提案也会被转成永续格式。
- `exchanges/okx/client.py:286` — `load_demo_markets` `if info.get("type") != "swap": continue`，现货标的（无 `:USDT` 后缀）被排除出 demo 白名单 → `okx_broker.py` demo 白名单检查会拒掉现货提案。

### 卡点 B —— AI 默认偏向永续（违反原则 3：写死了偏好）
- `agent_shell/trading_decision.py:281` — `market_type = decision.market_type or "usdt_futures"`。
- `agent_shell/trading_decision.py:770` — 提示词「Default to 'usdt_futures' for directional crypto trades」。
- 即使能开现货，AI 也极少主动选。

---

## 3. 在四原则下的目标架构（决策记录）

### 原则 2 落地：现货/永续的「类型」是核心概念，「能不能下单」是 adapter 能力

- **核心层**（universe / opportunity / trading_decision / risk）：只认 canonical 类型 `spot` / `usdt_futures`，**不关心交易所**。已基本符合。
- **adapter 边界**（各 `exchanges/*/client.py` + `execution/*_broker.py`）：每个交易所声明自己**支持哪些 market_type**（capability），并按 `order.market_type` 正确路由（现货用 spot 符号 + spot defaultType；永续用 perp 符号 + swap defaultType + 杠杆）。
- 若某交易所 demo 不支持现货（如 Bitget），那是**该 adapter 的能力缺失**，应在 capability 层显式声明并由上层优雅降级（该 venue 下现货机会标记 unsupported），**而非在核心层禁掉现货**。

### 原则 1 落地：demo 能力矩阵显式化，向 live 看齐

- 引入「venue 能力矩阵」：`{venue, mode(demo/live)} → 支持的 market_type 集合`。
- doctor/启动时校验：若 live 支持现货而 demo 不支持，**显式 warn**「demo 与 live 能力不对齐」，让收敛缺口可见（呼应 doctor 诚实模式偏好）。

### 原则 3 落地：AI 不写死默认

- 去掉「无脑默认 usdt_futures」：market_type 优先**继承 opportunity 的 market**（标的池本就区分现货/永续成员）。
- 提示词从「默认永续」改为「按情况权衡」：列出权衡因子（资金费率方向、是否要杠杆/做空、流动性、持有周期），让 AI 按情况选，不预设优劣。

---

## 4. SDD-先行清单（原则 4：先 spec 后代码）

**实现前必须先做的 spec 工作**（顺序：先声明 `SpecClaim(status="missing")` → 写 L2a RED → 实现 → GREEN）：

1. **新增 venue capability spec**（建议 `tests/specs/spec_exchange_capability.py`）：断言每个 adapter 声明的 `supported_market_types`，以及「核心层不得因单一 venue 限制而全局禁某 market_type」。
2. **OKX adapter 现货路由 spec**：断言 `place_order(market_type="spot")` 走 spot 符号/ spot defaultType；`load_demo_markets` 收现货标的。
3. **trading_decision 去默认偏向 spec**：断言 market_type 继承 opportunity.market，无 opportunity 时不再硬默认 perp（FL-S12/S13 邻域，需同步更新）。
4. **demo↔live 能力对齐校验 spec**：断言 doctor/startup 在 demo 与 live market_type 集合不一致时产出可见信号。
5. **契约文档同步**：`docs/design/` 相应契约（execution/adapter 边界、risk `enabled_markets` 语义）+ `data_model.md`（若 OrderRequest/Position 需 market_type 透传字段）。
6. 把新增 spec 文件加入 `test_spec_conformance.py` 硬编码 import（避免 SDD 收集陷阱）。

---

## 5. 仍需的实测步骤（坐实 OKX demo 现货能力 —— owner 运营步骤）

文档无法 100% 确认 OKX demo 现货下单，需一次**最小往返实测**（系统当前 paused，且涉及执行边界，属 owner 授权的运营动作）：

1. 用 OKX demo key，CCXT `set_sandbox_mode(True)` + `defaultType="spot"`，`load_markets()` 看是否返回现货标的（只读，安全）。
2. 对某现货标的（如 `BTC/USDT` 无后缀）下一笔极小额 demo 市价单，确认 demo 接受现货下单并返回成交。
3. 结果回填本文 §1 矩阵，把 OKX 现货置信度从「高/待坐实」升级为「已坐实」或证伪。

**若坐实支持** → 走 §4 SDD 清单纯改 adapter，不动 live。
**若证伪（demo 不支持现货）** → 现货只能在 live 验证，触信任边界，需 owner 显式授权 live-spot 路径（另开 plan）。

---

## 6. 本 event 不做什么（护栏）

- ❌ 不动 live 路径 / 不解锁 live / 不改风控硬限额。
- ❌ 不在核心层为任何单一交易所写死/禁用 market_type。
- ❌ 不在本文阶段改代码——先拍板 §1 矩阵实测结果 + 过 §4 SDD。
- ❌ 不扩展新交易所（原则：核心不变，仅 adapter 适配已接入的 OKX/Bitget）。

---

## 7. 实现进度

### ✅ 已完成（2026-06-28，分支 `worktree-demo-live-spot-parity-event`）

SDD-先行全程：先 spec（RED）→ 实现 → GREEN。

- **spec**：`tests/specs/spec_spot_parity.py`（SP-S01~S06），已加进 `test_spec_conformance` import。conformance 290 claims / 99% coverage 全绿。
- **OKX adapter**（`exchanges/okx/client.py`）：加 `SUPPORTED_MARKET_TYPES` / `is_leveraged_market` / `okx_ccxt_symbol`；`place_order` 按 market_type 分支（spot：canonical 符号、无杠杆/无 contractSize、市价买 `tgtCcy=base_ccy`、无 reduceOnly；perp 维持原逻辑）；`load_demo_markets` 纳入 spot 标的。
- **决策链**：`trading_context.py` opportunity 透出 `market` 字段；`trading_decision.py` `_attach_opportunity_context` 继承 opportunity.market；submit 兜底改为"空头必永续/其余现货"（去掉无条件 `or usdt_futures`）；prompt 改为按情况权衡、无固定偏向。
- **契约文档**：`execution_contract.md` §3.1 新增 adapter market_type 能力矩阵。
- **回归**：定向 67 passed / 0 failed；架构适配 11 passed；全量在后台跑。

### 待办（owner / 部署）

1. **merge 到 master + gateway restart + `aletheia doctor` 验证**（worktree 空 data，doctor 须在主仓真实 runtime 跑；系统当前 paused）。
2. **live demo 端到端验收**：系统恢复后，观察 PI 是否对 spot opportunity 真开现货仓 + 平仓（CausalChain 全节点）。属 owner 运营步骤。
3. 余项（非阻塞）：Bitget adapter 现货能力（demo 不支持，需 live 才有现货）按需另议；其余交易所能力矩阵补全。
