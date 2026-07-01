# 复盘 —— reconciler 假孤儿 thrash：经验与教训（2026-07-01）

> 性质：L?（事件复盘 + **可迁移工程教训**，非一次性状态）。配套根因分析
> [`ENTRY_EXIT_ASYMMETRY_ANALYSIS_2026-07-01.md`](ENTRY_EXIT_ASYMMETRY_ANALYSIS_2026-07-01.md)。
> 目的：把这次从「监控抓到信号 → 追根到代码核心 → 穷尽排查 → 损益评估 → SDD 修复 → P2 收尾」
> 的过程沉淀成**下次遇到同类问题能直接翻的方法论 + 反模式自查表**。相关记忆
> [[aletheia-reconciler-false-orphan-thrash]] / [[aletheia-profit-loop-broker-balance-trap]] /
> [[aletheia-round1-cleanwindow-eval]] / [[aletheia-weak-spec-fake-green-trap]]。

---

## 0. TL;DR

- **Bug**：非 Bitget 账户(OKX demo)上，对账器把交易所上真实存在的持仓误判为"孤儿"并平掉，
  下一周期又把它重新认领——一个自维持的空转循环（thrash）。
- **根因**：`classify_positions` 在多 broker 统一缓存过期时，**回退去读一个只认 Bitget 的老缓存**；
  该缓存对 OKX 账户是"新鲜但空"(0 持仓、状态 ok)，诚实性守卫因"状态 ok"没拦住 → 全部误判孤儿。
- **影响**：206 个仓位里 41% 是假孤儿平、56% 是重认领碎片；PnL 数字没被粉饰（thrash 净≈0），
  但**策略归因被摧毁**(仅 10/110 真实平仓带策略标识)——owner 想攒的"干净窗"基本是空转产物。
- **修复**：P0 回退改 broker 感知（非 Bitget 无新鲜统一缓存就 bail，绝不读错 broker）→ 源头掐断。
  P1 Scorecard 去污。P2 OKX 交易所保护单(休眠+demo-only)/ 归因恢复 / 信号门过滤。全 SDD、
  full suite 1815/0、commit 5d6bb89 + da38fbf。

---

## 1. 可迁移教训（核心）

### A. 发现层 —— 为什么它藏了这么久

**A1. 活体监控能抓到测试和审计都抓不到的一整类 bug。**
515+ 全量测试全绿、SDD conformance 500 claims 全绿，都没发现这个 thrash。它只有在**监控盯着
运行系统的真实事件流、跨时间**才暴露(同一批仓 2 秒内全平、5 分钟后原价重开)。
- **为什么**：单元/spec 测的是"给定输入的确定输出"，测不出"状态随时间演化 + 环境配置交互"这类
  bug（缓存过期窗口 × 5min 对账节拍 × OKX 账户）。
- **怎么用**：验证一条数据管道"能跑"之后，**再挂一个活体监控盯真实事件流一段时间**。会重复
  发生的状态异常（开/平/重开）用 Monitor 盯，一次性完成用后台 Bash。

**A2. "端到端跑通" ≠ "端到端正确"。**
我一开始汇报"做空链路六环全通"——但那个"端到端"大半是 thrash 空转，不是策略行为。
- **为什么**：只看到"有仓位在开、在平、有 PnL"就下结论，没看**平仓的 close_reason 和 source**。
- **怎么用**：验证管道时，**永远追一层到"为什么"**——不是"有平仓"，而是"平仓是 stop_loss/
  take_profit(策略) 还是 reconciled_gone(清扫)？仓是 native(策略开) 还是 adopted(认领)？"。
  出现大比例的行政性 close_reason / adopted source = 你在观察空转，不是策略。

### B. 根因层 —— 同类 bug 的通用形状

**B1. 「同一个根错配的第二个受害者」——修一处必扫所有消费者。**
这个"legacy Bitget-only 缓存 vs 多 broker"错配**之前就坑过一次**([[aletheia-profit-loop-broker-balance-trap]]：
喂 PI 账户余额幻觉)。当时只修了那一个消费者(`get_cached_state` 默认路径)，**没扫其余消费者**，
于是同一个错配换个消费者(positions 分类)又爆了。
- **为什么**：一个共享资源(这里是缓存)的系统性错配，往往有**多个消费者**，每个都是潜在受害者。
- **怎么用**：修好一个共享资源/契约的根错配后，**立刻 grep 出所有消费者逐个判定**是否同病。
  这次穷尽排查(10 个 `get_cached_state`/`get_unified_cached_state` 消费点)才确认"只剩 reconciler
  一处"——但这一步本应在**第一次修 profit-loop 时就做**。

**B2. 「新鲜但错」比「陈旧但对」更危险。**
守卫只检查缓存"新不新鲜"(status ok/stale)，没检查"是不是对的 broker"。空的 Bitget 缓存
status=ok，于是骗过守卫。
- **为什么**：可用性(fresh)和正确性(right source)是**两个维度**。防御只做了前者。
- **怎么用**：任何 fallback / 降级路径，**既要验新鲜度、也要验"这是不是当前场景该用的源"**。
  "拿到了一份不过期的数据"不等于"拿到了正确的数据"。宁可 bail(明确说"看不清")也不要拿错源
  硬算——这次的修复正是"非 Bitget 账户无新鲜统一缓存 → bail 报全 managed，绝不读 Bitget 空缓存"。

**B3. 本地改动不回写权威源 → 循环。**
demo 孤儿平仓只改本地 DB、不动交易所 → 交易所上仓还在 → 下周期被重新认领 → 循环。
- **为什么**：本地态和权威态(交易所)分叉后，只改本地的一侧，下一次同步必然把它拉回来。
- **怎么用**：任何"关闭/删除/撤销"操作，问一句：**它有没有传播到权威源？** 如果只改了镜像/缓存，
  下次同步会撤销它，形成 flip-flop。要么真的回写权威源，要么先确认权威源确实已变。

**B4. 损失不在你以为的维度。**
我第一反应是"thrash 粉饰了 PnL"，数据一算：reconciled_gone 净 PnL≈-0.72(几乎平)，PnL 数字
是真实的；真正被毁的是**策略归因**(64% 真实平仓落在无策略标识的 adopted 碎片上)。
- **为什么**：直觉锚定在最显眼的指标(PnL)，但系统性 bug 的伤害可能在另一个维度(归因/样本纯度)。
- **怎么用**：**先用数据把损失拆到每个维度**(按 close_reason、按 source、按 strategy_id 缺失率)，
  再下"影响有多大"的结论。别用直觉替代 SQL。

### C. 验证 / 流程层

**C1. 配置门控的休眠功能可以安全上线。**
交易所保护单功能挂在 `exchange_tpsl_enabled`(默认 False)后面。给 OKX 补齐这个能力、接进 adopt，
但因为开关是关的 → **对当前生产零影响**，owner 哪天打开才激活。
- **怎么用**：往敏感路径(execution/下单)加能力时，**挂在一个默认关闭的开关后面**——休眠上线、
  零风险、owner 主动 opt-in 时再验。这是"触信任边界但仍能安全推进"的通用手法。

**C2. 动手前先调查，bug 的真实 scope 常被最初的表述低估。**
"adopted 仓不下保护单"实际是"OKX broker 对任何仓都不下交易所保护单"。若照最初表述只补 adopt，
会留下"认领的仓有保护、新开的仓没有"的不一致。
- **怎么用**：实现前先把机制摸清(现有下单路径怎么走、别的 broker 怎么做)，**别照 bug 报告的字面
  scope 就动手**。

**C3. SDD 反向验证：spec 必须先红。**
每条 spec 都先跑出 RED(精确报出 bug：1 个假孤儿 / thrash 平仓 / 胜率被污染到 15.4%)，改完才 GREEN。
- **为什么**：一条从没红过的 spec 证明不了任何事([[aletheia-weak-spec-fake-green-trap]] 的假绿陷阱)。
- **怎么用**：写完 spec **先确认它在未修复代码上失败**，再实现。红→绿的转变才是"这个测试真的守住了
  这个 bug"的证据。

**C4. 趋势/滚动窗指标不会即时归零。**
修好后 doctor `reconciled_ratio` 仍显示 78%——它是最近 50 笔的**滚动窗**，装的全是修复前的历史。
- **怎么用**：汇报修复效果时，区分"**源头已修**"(代码/spec 证明)和"**指标已追上**"(趋势窗滚动)。
  别因为趋势指标还没变就以为没修好，也别因为改了代码就宣称指标已好。

**C5. 环境约束下，"最穷尽"不等于"最大 fan-out"。**
ultracode 要求穷尽，但本 workspace 有每分钟 token 限额，宽 fan-out 并行 agent 会秒级 429 全灭
([[aletheia-workspace-ratelimit-fanout]])。这次改用**自己控制的密集并行工具调用**做穷尽排查。
- **怎么用**：一个 429 挂掉的 workflow 覆盖率更低。有硬环境约束时，自驱的系统性排查 > 盲目 fan-out。

---

## 2. 反模式自查表（下次调试直接过一遍）

- [ ] 这条管道"能跑"，我有没有**追一层看 close_reason / source / 归因标识**，确认不是空转/artifact？
- [ ] 我修的这个根错配(共享缓存/契约/转换)，有没有**grep 出所有消费者**逐个判定同病？
- [ ] 这个 fallback/降级路径，有没有既验**新鲜度**又验**源正确性**？"新鲜但错"会不会骗过守卫？
- [ ] 这个"关闭/撤销"操作，有没有**回写权威源**？只改本地会不会被下次同步撤销成循环？
- [ ] 我说的"影响"，是**用数据按维度拆过**的，还是直觉？损失会不会在另一个维度(归因/样本纯度)？
- [ ] 我的 spec **先红过**吗？还是可能是假绿(只查字符串存在/从没失败过)？
- [ ] 我报的"已修复"，区分了**源头已修** vs **趋势指标已追上**吗？
- [ ] 碰 execution/下单等敏感路径，能不能**挂在默认关闭的开关后面休眠上线**？live 有没有被自动触发？

---

## 3. 这次做对的、可复用的正向流程

1. **监控先行**：抓到明确信号(监控事件)才动手，不凭猜。
2. **追到代码核心 + 亲测**：不停在现象，读 classify/close_orphan/adopt/exchange_sync 全数据流，
   并用真实配置模拟触发条件(stale-unified 路径)确认 0 false-orphan。
3. **穷尽排查 + 损益评估先于修复**：先把"哪些地方受影响"和"损失多大"用数据说清，再动手。
4. **SDD 全程**：spec 先红后绿、反向验证、每步全量回归。
5. **安全边界不破**：触 execution 边界的 P2 挂默认关闭开关 + demo-only + graceful，绝不给 live 自动下单。
6. **意图/事实分离**：根因分析、复盘、契约进 git；运行态证据(regime/持仓/胜率)留 `data/observation/`。
7. **收口纪律**：ROADMAP/HANDOFF/DEVLOG/记忆同步，避免"ledger 只进不出致状态失真"。

---

## 4. 交叉引用

- 根因分析（症状/代码追溯 file:line/损益/修复）：`ENTRY_EXIT_ASYMMETRY_ANALYSIS_2026-07-01.md`
- commits：`5d6bb89`(P0 thrash 根因 + P1 Scorecard 去污)、`da38fbf`(P2 三项)
- 单一真源：`aletheia/analytics/close_reasons.py`(什么算 synthetic/行政性平仓)
- specs：PL-ADOPT-10~16、AN-S03、LP-S15
- 记忆：[[aletheia-reconciler-false-orphan-thrash]]、[[aletheia-profit-loop-broker-balance-trap]]
  (同根错配的第一个受害者)、[[aletheia-round1-cleanwindow-eval]](当时"平仓靠 reconciler 非 AI"
  的真机制就是本 bug)、[[aletheia-weak-spec-fake-green-trap]]、[[aletheia-workspace-ratelimit-fanout]]
