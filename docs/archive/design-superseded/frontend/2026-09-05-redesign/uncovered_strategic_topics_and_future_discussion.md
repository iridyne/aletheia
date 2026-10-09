# Aletheia 前端未覆盖关键议题与未来演进讨论清单

> **文档定位**：在完成全站代码审计、设计规范制定、表格优化与 Market 页面重构方案之后，站在**系统可靠性、实盘业务安全与产品完整生命周期**全局高度，深度排查出的 **7 个尚未被充分讨论或存在隐性盲区的深水区课题**，供团队决策层后续研讨。

> 状态：待评审讨论稿。下列界面建议不是已实现能力或发布门槛；当前执行边界以
> [执行契约](../../../../design/contracts/execution_contract.md)和[退役提案](../../../../design/proposals/retire_local_simulated_exchange.md)为准。

---

## 目录
1. [议题 1：站内持久化通知中心与告警审计追溯（Alerting & Toast History）](#议题-1站内持久化通知中心与告警审计追溯alerting--toast-history)
2. [议题 2：实盘解锁（Live Trading Unlock）的“防灾级确认墙”与全局红灯模式](#议题-2实盘解锁live-trading-unlock的防灾级确认墙与全局红灯模式)
3. [议题 3：单 Agent 粒度精细化制动与干预（Per-Agent Granular Governance）](#议题-3单-agent-粒度精细化制动与干预per-agent-granular-governance)
4. [议题 4：历史行情与 AI 决策沙盒重放（Historical Scrubber & Replay Mode）](#议题-4历史行情与-ai-决策沙盒重放historical-scrubber--replay-mode)
5. [议题 5：多账户与多资金池隔离切换（Multi-Account / Sub-Account Context）](#议题-5多账户与多资金池隔离切换multi-account--sub-account-context)
6. [议题 6：PWA 移除后的打包更新与热重载心智（Packaged Desktop Shell Lifecycle）](#议题-6pwa-移除后的打包更新与热重载心智packaged-desktop-shell-lifecycle)
7. [议题 7：一键隐私打码模式（Privacy & Redact Mode for Public Sharing）](#议题-7一键隐私打码模式privacy--redact-mode-for-public-sharing)

---

## 议题 1：站内持久化通知中心与告警审计追溯（Alerting & Toast History）

### 1. 现状痛点
* **瞬态 Toast 容易遗失**：目前全站使用 `useToast()` 弹出临时提示（如平仓成功、网络闪断、分析报错），3~5 秒后自动淡出消失。
* **人不在场盲区**：操作员去倒杯咖啡或移开视线 10 秒，回来后 Toast 已经灭掉。操作员无法确认刚刚界面是否弹出过重大警告或拒单提示。
* **没有集中告警收件箱**：虽然 Settings 中配置了 Telegram 和 Webhook 推送，但**前端站内本身没有一个“通知中心抽屉（Notification Center）”**。

### 💡 探讨方向：
* 在顶栏右侧新增一个带未读计数圆点的铃铛图标 `<NotificationBell count={unreadCount} />`；
* 点击滑出轻量抽屉，倒序展示近 50 条站内事件（风控拒单、成交回执、网络恢复、模型错误），支持“标记已读”与“一键跳转到对应交易”。

---

## 议题 2：实盘解锁（Live Trading Unlock）的“防灾级确认墙”与全局红灯模式

### 1. 现状痛点
* **执行环境与解锁应区分**：Settings 的交易所环境选择接受 `demo` / `live`；选择 Live 环境本身不授予实盘执行权限，Live 解锁是独立边界。下列确认交互仅为讨论建议。
* **缺乏“全站醒目警戒态”**：实盘一旦开启，涉及真实真金白银。目前界面上仅有顶栏微弱的状态小字，缺乏像核潜艇发射那样的“全局警戒状态（Global Live Hazard State）”。如果操作员忘了自己开的是实盘，可能把实盘误当模拟盘放任不管。

### 💡 探讨方向：
* **输入型防灾验证墙**：解锁实盘时，严禁普通点击确定，必须手动敲入指定确认短语：
  `Type "I CONFIRM REAL CAPITAL RISK" to unlock Live Execution`。
* **全局红斑警戒顶条（Live Hazard Banner）**：当系统处于 Live Trading 激活态时，顶栏最上方亮起一条 4px 高度的呼吸红线，或者顶栏常驻显眼的红色闪烁标识 `[● LIVE TRADING ACTIVE - REAL CAPITAL AT RISK]`。

---

## 议题 3：单 Agent 粒度精细化制动与干预（Per-Agent Granular Governance）

### 1. 现状痛点
* **制动粒度过粗（一刀切）**：目前顶栏和 CommandCenter 提供的制动手段是二元的：要么**全局暂停整个 Gateway**，要么正常运行。
* **实际痛点**：在震荡行情下，可能只是 `momentum_breakout` 策略在连续止损，而 `funding_arbitrage`（资金费率套利）和 `market_observer` 运行完全健康。操作员只想叫停冲动追涨的动量 Agent，而不想把整个网关全盘关停。

### 💡 探讨方向：
* 在 `CommandCenter.tsx` 或 `Universe.tsx` 中建立“策略/智能体能力面板”：
  支持对单个 Agent 进行 `Pause Agent` / `Resume Agent`，让干预手段由“拉闸停电”进化为“微创手术”。

---

## 议题 4：历史行情与 AI 决策沙盒重放（Historical Scrubber & Replay Mode）

### 1. 现状痛点
* **只能看“现在”与“过去的结果”，无法看“决策当时的动态切片”**：
  在复盘一笔亏损交易时，操作员在 K 线图上看到的是完整的历史柱线。但交易员真正需要回答的问题是：“在 8 月 27 日 14:05 分那一秒，Agent 做出决策时，当时的盘口点差、当时的指标、当时还没走出来的未来 K 线到底长什么样？”

### 💡 探讨方向：
* 在图表页引入**时间指针滑块（Historical Scrubber）**：
  将图表时间锚定回某笔交易的 `opened_at`，系统自动隐藏该时刻之后的未来 K 线（Bar-by-Bar Replay），让操作员以完全一致的第一视角检验 AI 当时的决策环境。

---

## 议题 5：多账户与多资金池隔离切换（Multi-Account / Sub-Account Context）

### 1. 现状痛点
* **账户边界**：当前账户身份与余额来自明确配置的外部交易所账户；缺失配置不回退到本地模拟账户。未来多账户界面应遵循[统一交易账户提案](../../../../design/proposals/unified_trading_account.md)的身份边界。
* **业务演进瓶颈**：随着策略演进，自营交易室通常会设立“低风险稳健账户（Alpha A）”与“高波动进取账户（Alpha B）”，甚至区分多子账户以隔离不同做市仓位。当前界面没有账户切换的顶层上下文槽位（Account Switcher）。

### 💡 探讨方向：
* 预留轻量的顶部账户选择器架构规范，当后端未来支持多账户时，前端无需破坏现有布局即可平滑接入。

---

## 议题 6：PWA 移除后的打包更新与热重载心智（Packaged Desktop Shell Lifecycle）

### 1. 现状事实
* `index.html` 中明确记录：
  ```javascript
  // PWA was removed. Keep the migration cleanup one-shot; never scan registrations
  // on every dashboard boot and never cache trading data offline.
  ```
  为了防止 Service Worker 缓存危险的过时交易数据，PWA 已被彻底拔除。
* 既然没有 Service Worker，当后端执行 `aletheia update` 重启完成后，前端操作员如果几天不主动刷新网页，内存里加载的始终是旧版 JS Chunk。

### 💡 探讨方向：
* 当网关更新版本后，`/api/ready` 会返回新的 `version` 或 `current_commit`；
* 前端比对当前运行的 commit 与网关返回的 commit，一旦发现后端已升级，在底部弹出一个轻量非阻塞通知：`"New Aletheia version available. [Click to Reload]"`，避免旧版前端调用已变更的 API。

---

## 议题 7：一键隐私打码模式（Privacy & Redact Mode for Public Sharing）

### 1. 现实痛点
* 量化研究员或操作员经常需要在内部团队群、GitHub Issue 或公开讨论中分享界面截图（例如探讨一个复杂的因果链 Bug，或者求证一个图表形态）。
* 但目前界面上所有持仓金额、总资产净值、未实现盈亏、钱包/API Key 后四位都是赤裸展示的，截图前不得不手动在绘图软件中打马赛克，费时费力且极易漏打导致隐私泄露。

### 💡 探讨方向：
* 在快捷键中支持 `Cmd + Shift + P`（或顶栏一个小眼镜按钮）：**一键切换隐私打码模式（Privacy / Redact Mode）**。
* 激活后，全站所有涉及具体金额、Token 数量、盈亏绝对值的文本，统一替换为带有微弱模糊滤镜的占位符（如 `••••••` 或 `filter: blur(4px)`），方便一秒安全截图。

---

## 总结：未覆盖议题研讨矩阵

| 议题 | 核心影响领域 | 紧迫度 | 推荐研讨时机 |
|---|---|---|---|
| **1. 站内通知中心** | 运维监控与防漏警报 | 🟡 中 | 当接入真实 Telegram/Webhook 实盘推送时 |
| **2. 实盘防灾确认墙** | 核心资金安全与防误触 | 🔴 高 | **在开放任何实盘交易（Live Mode）前必须先就绪** |
| **3. 单 Agent 精细制动** | 策略自愈与精细干预 | 🟡 中 | 随着多智能体数量扩展至 5+ 以上时 |
| **4. 沙盒时空切片复盘** | 量化研判与策略归因 | 🟢 低 | 属于高阶分析功能，可在二期探索 |
| **5. 多账户隔离上下文** | 账户结构伸缩性 | 🟢 低 | 后端支持多子账户前仅需保留心智契约 |
| **6. 后台升级热重载提示** | 前后端版本一致性 | 🟡 中 | 防止长时间挂机网页调用过期接口 |
| **7. 一键隐私打码模式** | 团队协作与公开汇报 | 🟢 低 | 开发成本极低（约 10 行 CSS 滤镜切换） |

---

*（本文档已保存至：`docs/design/frontend/uncovered_strategic_topics_and_future_discussion.md`，可作为后续产品需求评审与技术委员会研讨的输入提案）*
