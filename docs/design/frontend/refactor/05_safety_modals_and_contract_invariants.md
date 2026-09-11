# 交易安全确认、语义诚实与存量契约继承规范 (Safety & Invariants Contract)

> **契约目标**：本文件规定 Aletheia 前端重构在追求“高密度、流式响应、四工作台融合”的同时，必须**100% 严格继承的底层交易安全规范、操作员二次确认墙（ConfirmModal）、数据语义诚实准则（DataState）以及系统审计链身份**。

---

## 一、 防灾级操作员确认墙契约（Safety Confirmation Modal Invariant）

Aletheia 面对的是真金白银的真实量化交易系统。无论上层视图如何演进，涉及系统生存状态的破坏性、高风险动作**必须强制经过操作员二次确认**。

### 1. 强制受控的高危动作清单（Protected Action Matrix）

| 业务动作 | 触发位置 | 确认弹窗严重级别 (Variant) | 契约硬性要求与防穿透标准 |
| :--- | :--- | :--- | :--- |
| **Engage Kill Switch**<br>*(触发全局熔断)* | Topbar / Command Palette / Risk 工作台 | `danger` (全局红灯) | 弹窗阻断一切背景点击与快捷键穿透，点击后调用 `setKillSwitch(true)` |
| **Release Kill Switch**<br>*(解除全局熔断)* | Topbar / Command Palette / Risk 工作台 | `warning` (谨慎黄灯) | **必须显式向后端传递 `confirm_release: true`**；若未确认直接调用，后端 422 拒绝 |
| **Switch to Full Mode**<br>*(切换实盘执行权限)* | StatusCluster / Governance 设置 | `danger` (高危确认) | 明确提示“此操作将启用交易所真实下单”，确认后才允许提交 |
| **Emergency Close All**<br>*(一键全量平仓)* | Trades & Positions / Blotter | `danger` (清盘确认) | 严格遵守 `execution_contract.md §10.2`：前端发起前必须支持后端逐仓 Mark 预检与部分失败诚实透传 |
| **Live Unlock**<br>*(解锁实盘交易状态)* | Governance / Live Unlock 面板 | `danger` (防灾级双锁) | 必须满足本地签名/二次密码，生成符合 `live_unlock_contract.md` 的审计事件 |
| **Risk Policy Mutation**<br>*(修改风控上限/杠杆)* | Governance / Risk 策略编辑器 | `warning` | 离开未保存草稿必须触发 `dirtyGuard` 导航拦截警告 |

### 2. 确认弹窗的无障碍与焦点捕获铁律
- 弹窗呼出时，必须使用 `useDialogFocus` 强制将焦点捕获在弹窗内部，ESC 键退出，Tab 键在取消/确认按钮间循环。
- 严禁任何后台 WebSocket 推流引发的页面重新渲染关闭或重置正在打开的确认弹窗。

---

## 二、 数据语义诚实契约（DataState Invariant — 对齐 `dashboard_data_presentation_contract.md`）

必须严格继承现有 L2 契约 `docs/design/contracts/dashboard_data_presentation_contract.md` 中规定的**数据呈现五态模型**：

```typescript
export type DataState =
  | 'loading'      // 正在拉取数据，首次加载呈现骨架或加载中提示
  | 'available'    // 成功获取数据且数据非空
  | 'empty'        // 接口成功返回，但业务上确实没有数据（真实为 0 / 真正无仓位）
  | 'stale'        // 网络异常/推流中断，保留上次成功的历史快照供观察，但明确标明过期
  | 'unavailable'  // 首次拉取即失败或后端服务未就绪，明确标红报错
  | 'partial';     // 部分字段或非关键切片不可用，保持降级可用
```

### 1. 语义表达红线（Zero Lying）
- ❌ **严禁把 `unavailable`（读取失败）渲染成 `empty`（无数据）或数字 `0`**：
  例如：持仓接口报错时，严禁显示“暂无持仓”或“净值 0”，必须诚实展示 `DataStateMessage(state='unavailable')`。
- ❌ **严禁把网络断开时的历史旧值包装成新鲜数据**：
  当 WebSocket 断线或 REST 轮询失败时，已渲染的行和数字可以保留在屏幕上，但必须挂载 `stale` 提示条或标记（`stale: true`），明确告知操作员当前为陈旧快照。

---

## 三、 设置开关与控件持久化契约（Settings Switch Invariant — 对齐 `settings_control_contract.md`）

全站所有复用 `Switch` / `Toggle` 原语的配置控件必须严格遵从 `docs/design/contracts/settings_control_contract.md`：

1. **双模显式声明（Explicit Persistence Mode）**：
   - 每个开关必须显式传入 `persistence="immediate"` 或 `persistence="staged"`。
   - `immediate`：点击开关立即发起 API 请求写入后端（如实时调试开关、排障代理）。
   - `staged`：点击开关仅在前端草稿区暂存，必须等待操作员点击全局“Save”按钮后统一校验并提交。
2. **防漏修改拦截（Dirty Guard）**：
   - 当工作台存在未保存的 `staged` 修改时，操作员尝试切换工作台、刷新页面或点击后退，系统必须弹出未保存拦截确认（`dirtyGuard.ts`），防止配置丢失。
3. **测试契约守门**：
   - 必须保持全站 65 处开关站点的类型检查与单元测试（`settingsSwitchContract.test.ts`）100% 绿灯。

---

## 四、 系统审计血缘与客户端身份契约（Audit Chain Invariant）

根据 `docs/design/contracts/audit_chain_contract.md` 和 `spec_live_unlock.py`：

1. **统一注入审计身份（Audit Context Injection）**：
   - 纯 TS 通信层（`core/api/client.ts`）发出的所有 POST / PUT / DELETE 变动操作，必须在请求头或 Payload 中确保声明：
     - `actor: "human"`
     - `source: "dashboard"`
2. **严禁越权伪造系统身份**：
   - 网页端绝不可伪造 `actor="system"` 或 `source="scheduler"` 发起修改。
   - 每一笔经由前端界面的指令交互（包括 CommandCenter 命令行输入）均必须完整归档至后端的 `audit_events` 数据库表。
