# 仓位过小根因分析 + 交易 Persona 计划（2026-07-01）

> 性质：L?（分析报告 + 实施计划，**未实施**——owner 要求先分析出报告与计划）。
> 触发：owner 观察到 demo 下杠杆仓位总是开得极小（"基本上只有 1%"），希望以"小仓位高杠杆
> 博超额收益 / 滚仓"为蓝本，做成设置里可切换的不同交易策略(persona)。
> 相关记忆：[[aletheia-drawdown-notional-vs-equity-trap]] / [[aletheia-reconciler-false-orphan-thrash]] /
> [[aletheia-profit-loop-broker-balance-trap]] / [[aletheia-okx-contract-size-trap]]。

---

## 0. TL;DR —— 为什么仓位这么小

**不是一个原因,是三个约束叠加把仓位钉死在 ~$25–27 notional**(在 ~$89k demo 权益上 ≈ **0.03%**):

1. **基准 notional 只有 $25**(`trading.default_notional_usdt=25`)。vol-target 的 Kelly 上限公式是
   `base × (effective_kelly×4) × 2.5`,base 就 $25 → 天花板只有 **$25–$62.5**,再大的 raw_size 也被削到这。
2. **Kelly 卡在最低档 0.10**。sizing 对"< 10 笔成交的策略"强制 effective_kelly=0.10(新策略从小做起)。
   **但 thrash 把策略归因毁了**(绝大多数仓位无 strategy_id / 是 adopted 碎片)→ **没有任何策略能攒够
   10 笔** → Kelly 永远停在 0.10 → 仓位永远最小档。这是 reconciler thrash 的又一处下游伤害。
3. **System Operator 自己把上限收到了 $26.84**。风控硬上限 `risk.max_demo_position_notional_usdt`
   默认 $200,但操作员在连亏(WR 40%、thrash 污染)下**自动收紧了 ~9 次**(每次 ×0.8):
   audit 实证链 `52.43→41.94→33.55→26.84`,已逼近 $20 地板。

外加 **杠杆上限**(demo:主流币 5x / 中盘 3x / 山寨 2x)与 **drawdown_scale**(回撤时再打折)。

> ⚠️ 当前还叠加一个**瞬时假象**:OKX demo API 正在报 `50001 服务不可用`,导致统一账户视图暂时
> 只剩 bitget($167)。若 sizing 此刻读到 $167 而非真实 OKX 权益,仓位比例会更失真。这是交易所侧
> 临时故障,不是"总是很小"的根因,但**暴露了一个真实脆弱点**(见 §4)。

---

## 1. 完整 sizing 链路(按"谁把仓位削小"排序)

| 层 | 代码 | 作用 | 当前值 | 对仓位的影响 |
|---|---|---|---|---|
| 基准 notional | `sizing.py:94` `default_notional_usdt` | 一切的锚 | **$25** | 天花板的根 |
| vol-target 原始 | `sizing.py:120-121` `vol_target_risk_usdt/ATR%` | 让 1 ATR 波动=风险预算 | risk=$25 | 想要 ~$1250(2% ATR) |
| **Kelly 上限** | `sizing.py:124` `base×(eff_kelly×4)×2.5` | 削 raw_size | eff_kelly **0.10** | **削到 ~$25**(<10笔) |
| 样本门 | `sizing.py:98-104` `paper_trade_count<10→0.10` | 新策略从小做起 | 大多 <10 | 归因被毁→永远触发 |
| conviction×drawdown | `sizing.py:127` `compute_position_size` | 信念/回撤调整 | — | 通常再打折 |
| **风控硬上限(demo)** | `risk/engine.py:126` `max_demo_position_notional_usdt×drawdown_scale` | 硬顶 | **$26.84**(操作员收紧) | **最终硬顶** |
| 单标的上限 | `risk/engine.py:311` `max_per_symbol_notional_usdt` | 单标的总敞口 | $400 | 一般不触及 |
| 杠杆上限 | `risk` `max_futures_leverage_demo=5` 等 | 杠杆倍数 | 主5/中3/山寨2 | 限制名义/保证金比 |

**结论**:三个约束(base $25 / Kelly 0.10 / 操作员上限 $26.84)几乎重合在 ~$25–27,所以仓位"总是很小"。
要放大,**这三个都得动**——只改一个,另外两个仍会把它压回去。

---

## 2. 根因归属(证据)

1. **操作员自保收紧(主因之一)**:`operator_loop._action_reduce_sizing`(:819)每次 ×0.8 收紧,
   audit_events 实证 `52.43→41.94→33.55→26.84`。ParamSpec:neutral=200 / floor=20 / ceiling=500。
   这是**设计行为**(连亏时自动降险),不是 bug——但它把"想放大仓位"的意图直接反向抵消。
2. **Kelly 卡 0.10(主因之一)**:归因被 thrash 毁 → 策略攒不够 10 笔 → 永远最小 Kelly。
   **修 thrash(已做)是解开这一环的前提**,但历史 adopted 碎片仍拉低样本。
3. **base $25 偏小**:`default_notional_usdt=25` 是刻意保守的 MVP 起点。

---

## 3. 能不能开大?可调的旋钮(仅列清单,未改)

| 旋钮 | 现值 | 放大方向 | 风险提示 |
|---|---|---|---|
| `default_notional_usdt` | $25 | ↑(如 $200–$1000) | 直接抬天花板 |
| `vol_target_risk_usdt` | $25 | ↑(每笔风险预算) | 直接放大波动敞口 |
| `vol_kelly_cap_fraction` | 0.25 | ↑ / 抬 <10笔 的 0.10 地板 | Kelly 越高越接近全下注,方差飙升 |
| `max_demo_position_notional_usdt` | $26.84(操作员) | ↑ 且**限制操作员收紧到此** | 否则操作员又收回去 |
| `max_futures_leverage_*` | 5/3/2 | ↑(滚仓要更高) | 触信任边界,杠杆=爆仓风险源 |
| drawdown_scale | 动态 | 放宽/关闭(persona) | 回撤时不再自动降险 |

---

## 4. 顺带发现:sizing 的权益基准脆弱点(与本次 thrash 同源)

OKX API 一挂,统一账户视图就只剩 bitget($167)。**任何按"权益 ×%"算仓位/上限的路径,此刻都会
用错基准**。这跟 [[aletheia-profit-loop-broker-balance-trap]]、本次 reconciler thrash 是**同一个根**:
"活跃 broker 视图不完整时,系统不该拿一个残缺/错误的账户状态继续算"。**建议(独立于 persona)**:
sizing / 风控上限读权益时,若活跃 broker(OKX)未成功纳入统一视图 → 拒绝放大、退回保守基准或暂停开新仓,
而不是拿 bitget 的 $167 硬算。这是 §滚仓的前置安全条件(滚仓对基准错误零容忍)。

---

## 5. 滚仓 / roll 策略是什么 + 风险画像(先讲清,再设计)

**滚仓**:用**小本金 + 高杠杆**开仓,盈利后**把浮盈加到仓位上(金字塔加仓)**,让盈利笔滚雪球,
博单笔超额收益;亏损笔用**极紧的失效点**快速止损,把单笔最大亏损锁死在小本金。数学上是
**高方差、正偏度**的打法:大多数小亏 + 偶尔大赚。

**诚实风险**:高杠杆 + 加仓是**账户爆掉的经典组合**。滚仓能赢的前提是**铁一样的单笔止损纪律 +
总敞口硬上限 + 自动去杠杆**。没有这些护栏,它不是"博超额",是"加速归零"。所以 persona 设计的
重心不是"放大",而是"放大的同时把下行锁死"。

---

## 6. 计划:交易 Persona(设置里可切换的策略档位)——**未实施**

### 6.1 概念
`TradingPersona` = 一组命名的 sizing/杠杆/风控/加仓参数打包。设置里选一个"当前 persona",
sizing 与风控读**当前 persona 的参数**而非全局散配置。默认 persona = 现有保守配置(零行为变化)。

### 6.2 初版三档(建议)
| Persona | 定位 | base_notional | risk/trade | kelly | 杠杆上限 | 加仓 | 单笔止损 | 操作员可收紧? |
|---|---|---|---|---|---|---|---|---|
| **conservative**(默认) | 现状 | $25 | $25 | 0.25 | 5/3/2 | 无 | 2% | 是(现状) |
| **balanced** | 温和放大 | $200 | $100 | 0.35 | 5/4/3 | 无 | 2% | 是 |
| **roll(滚仓)** | 小本高杠杆博超额 | $50(小本) | 按本金%固定 | n/a(固定) | **10–20x**(需授权) | **金字塔加浮盈,≤N 次** | **0.5–1%(极紧)** | **否(固定激进,但硬护栏不可越)** |

### 6.3 每个 persona 必带的**硬护栏**(不可被 persona 覆盖到关闭)
- **单笔最大亏损上限**(USDT 绝对值)——滚仓的命门。
- **总敞口/总保证金上限**——加仓不得突破。
- **自动去杠杆 / kill**:回撤达阈值强制平仓 + 暂停该 persona。
- **权益基准完整性门**(§4):活跃 broker 视图不完整时禁止放大/加仓。
- live 永远走人工解锁(persona 不改这条)。

### 6.4 接入点(实现时)
- `config/schema.py`:`TradingPersona` model + `personas: dict` + `active_persona: str`。
- `trading/sizing.py` + `risk/engine.py`:读 active persona 的参数(而非全局字段);无 persona→回退现状。
- `operator_loop.py`:操作员自动收紧**必须尊重 persona 边界**(roll persona 的 floor 不能被收到 $20;
   或对 roll persona 关闭自动收紧,改为只在硬护栏触发时 kill)。
- 加仓(金字塔):新增"盈利加仓"规则(仅 roll persona),走 Execution Gateway,受总敞口硬上限约束。
- 前端 Settings:persona 选择器 + 每档参数只读展示 + 护栏值(Config↔UI 规则)。
- SDD:先写 spec(persona 参数被 sizing/风控真实消费 / 硬护栏不可关 / 操作员尊重边界 / 加仓受限)。

### 6.5 分期
- **P1**:persona schema + sizing/风控读 active persona(只加 `conservative`=现状,零行为变化)。
- **P2**:`roll` persona 参数 + 硬护栏(单笔止损/总敞口/自动去杠杆/权益完整性门)。
- **P3**:金字塔加仓规则(仅 roll,受硬上限)。
- **P4**:Settings UI + 操作员按 persona 边界收紧。
- 每期 SDD、全量回归、demo 观察后再进下一期。

---

## 7. 需 owner 拍板的开放问题

1. **目标仓位**:每笔想开到权益的百分之几?(现 0.03%,想要 1%?5%?)
2. **杠杆红线**:滚仓愿意跑到多少倍?(demo 现上限 5x;滚仓通常 10–20x——触信任边界,需明确授权)
3. **加仓规则**:盈利后加几次?每次加多少?浮盈的多少滚进去?
4. **操作员权限**:roll persona 是否允许操作员自动收紧,还是"固定激进 + 只在硬护栏触发时 kill"?
5. **单笔最大亏损**:滚仓单笔最多亏多少 USDT 可接受?(这是护栏的核心数字)
6. **先在哪跑**:demo 先验证(OKX API 恢复后),live 永远人工解锁。

---

## 8. 交叉引用
- 代码:`trading/sizing.py`(sizing 公式)、`risk/engine.py:122-128`(notional 硬上限)、
  `agent_shell/operator_loop.py:819-874`(操作员收紧/放松)、`config/schema.py:436/279`(默认值)。
- 相关根因:reconciler thrash(毁归因→Kelly 卡 0.10)见 `ENTRY_EXIT_ASYMMETRY_ANALYSIS_2026-07-01.md`
  + `POSTMORTEM_reconciler_thrash_2026-07-01.md`;权益基准错配见 [[aletheia-profit-loop-broker-balance-trap]]。
