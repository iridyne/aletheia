# 前端感知后台升级与热重载提示机制（RFC 评估与实施方案）

> **探讨背景**：针对长周期无人值守或长时间挂机（数天至数周不关浏览器标签页）的交易监视场景，当后台完成 `aletheia update` 升级或提交代码漂移后，前端如何优雅、安全地感知版本升级并提示重载。  
> **核心原则**：第一性原理、奥卡姆剃刀、绝对不破坏操作员正在进行的工作。

---

## 目录
1. [可行性与实现难度评估（能不能做？好不好实现？）](#一-可行性与实现难度评估能不能做好不好实现)
2. [必要性深度思考（有没有必要？解决了什么真实痛点？）](#二-必要性深度思考有没有必要解决了什么真实痛点)
3. [核心设计禁忌：为什么绝对不能“强制静默自动刷新”？](#三-核心设计禁忌为什么绝对不能强制静默自动刷新)
4. [奥卡姆剃刀解法：极简 25 行代码落地规范](#四-奥卡姆剃刀解法极简-25-行代码落地规范)
5. [UTA 架构与多账户议题结论收敛](#五-uta-架构与多账户议题结论收敛)

---

## 一、 可行性与实现难度评估（能不能做？好不好实现？）

### 1. 结论：**100% 可行，而且实现成本极低（极易实现）**
经过排查源码，Aletheia 系统底层**实际上已经完成了 80% 的必要基建**：

1. **后端现有端点天然自备版本指纹**：
   - 每 10 秒轮询一次的 `/api/ui/shell-bootstrap`，其返回载荷中原本就包含：
     ```json
     "runtime": {
       "version": "0.8.4",
       "boot_commit": "403b17e82645",
       "current_commit": "17d97002d99c",
       "mode": "source"
     }
     ```
   - 探活接口 `/api/ready` 也带有 `version` 与启动时间戳。
2. **已有部分实装证据**：
   - 在 `frontend/src/components/PackagedUpdateBanner.tsx:107-115` 中，系统在打包模式（`mode === 'packaged'`）下，已经实现了更新完成后利用 `window.location.reload()` 自动刷新；
   - 在 `Overview.tsx:170` 中，代码已经具备了漂移判定：
     `const codeDrift = !!bootCommit && !!currentCommit && bootCommit !== currentCommit`；
   - 在近期提交 `39a253dc5` 中，团队还修复了 `fix(frontend): reload failed lazy routes on retry (#2858)`，专门用于应对懒加载分包因版本升级产生 404 时的重载。

---

## 二、 必要性深度思考（有没有必要？解决了什么真实痛点？）

### 1. 痛点 1：Vite 动态 Chunk 文件名哈希失效（Fatal 404）
* **物理事实**：Vite 在生产打包时，所有异步分包都有内容哈希（如 `Charts-CFvKRIDa.js`）。
* **故障场景**：
  操作员在周一打开了网页，停留在 `Overview` 页面挂机看盘。
  周二服务端运行 `aletheia update`，重新编译生成了静态文件，旧的哈希文件被清理。
  周三操作员第一次点击侧边栏的 `Charts` 页面，浏览器去请求周一记下的老分包 URL，直接返回 **404 Not Found**！虽然前端做了重试，但如果操作员不知道系统升级了，体验会非常突兀。

### 2. 痛点 2：API 契约漂移与静默掉单
* 后端升级如果调整了某个风控参数的字段名，或者在请求头上增加了新的鉴权签名，停留在旧版内存中的前端 JS 发起请求时会直接遭遇 400/422 报错，导致关键操作被拒绝。

### 3. 结论：**非常有必要，但需要克制的形式**
它属于“**长周期运行系统的刚需防御机制**”，能够彻底终结长线挂机场景下的老版本僵尸前端问题。

---

## 三、 核心设计禁忌：为什么绝对不能“强制静默自动刷新”？

在交易系统中，**任何强制自动重载页面都是灾难性的**：
* 如果操作员正在 `CommandCenter` 输入一段长篇的指令，或者在 `Settings` 中微调了 5 个参数尚未保存；
* 此时如果前端感知到后端升级就粗暴执行 `location.reload()`，操作员所有未提交的输入将瞬间化为乌有！

### 铁律（Ironclad Rule）：
1. **纯粹非侵入式提醒（Advisory Pill）优先**：仅在顶栏浮出一个极其克制的胶囊提示，由操作员自主决定何时点击刷新；
2. **只有在满足“双重安全条件”时才允许自动平滑更新**：
   $$\text{当前处于纯只读页面 (Overview/Charts)} \quad \mathbf{AND} \quad \text{dirtyGuard 确认未保存草稿数为 0}$$

---

## 四、 最大化复用既有组件与逻辑（零新造轮子）

翻查源码，系统内部原本就在 `StatusCluster.tsx:394-403` 实现了 **`sp-drift`（代码漂移）** 模块，并且在 `client.ts` 与 `health-issues.ts` 中已经有了成熟的闭环：

```tsx
// 1. 现存检测事实 (StatusCluster.tsx:111-112)
const driftIssue = (healthQuery.data?.issues ?? data?.health?.issues ?? [])
  .find(issue => issue.startsWith('code_updated_pending_restart:')) || ''
const driftRange = driftIssue.replace('code_updated_pending_restart:', '')

// 2. 现存网关重启接口 (StatusCluster.tsx:198-206)
const restart = useMutation({
  mutationFn: api.restartGateway,
  onSuccess: () => { ... }
})

// 3. 现存浮层底部面板 (StatusCluster.tsx:394-403)
{driftIssue && (
  <div className="sp-drift">
    <div className="sp-section-label sp-drift-label">⚠ Code Drift</div>
    <span className="sp-value">{driftRange}</span>
    <button onClick={() => restart.mutate()}>↻ Cold Restart</button>
  </div>
)}
```

### 现存实现的不足：
1. **藏得太深**：它深埋在 `status-popover` 浮动窗口的最底部。操作员如果不特意 Hover 状态小圆点并向下滚动，根本不知道后台代码已经更新；
2. **没有联动前端自身刷新**：点击 `↻ Cold Restart` 只重启了 Python 网关进程，但在浏览器端没有触发 `window.location.reload()`，导致前端仍在运行老旧的 JS Chunk。

### 最大化复用落地方案：
无需任何新造抽象，直接将这段**已有的 `driftIssue` 状态与 `restartGateway` 动作从弹窗底部“提拔”到顶栏常驻区域**，并串联自动重载：

```tsx
// 顶栏复用已有的 driftIssue 判定与 restartGateway 突变
export function DriftUpdatePill({ driftIssue, driftRange }: { driftIssue: string; driftRange: string }) {
  const restart = useMutation({
    mutationFn: api.restartGateway,
    onSuccess: () => {
      // 复用 PackagedUpdateBanner.tsx 的平滑重载时序 (1200ms)
      window.setTimeout(() => window.location.reload(), 1200)
    }
  })

  if (!driftIssue) return null

  return (
    <button
      type="button"
      className="runtime-reload-pill mono"
      onClick={() => restart.mutate()}
      disabled={restart.isPending}
      title={`Code drift detected (${driftRange}). Click to restart gateway and reload frontend.`}
    >
      <span className="dot dot-yellow" />
      <span>{restart.isPending ? 'Restarting…' : 'Code Updated · Reload'}</span>
    </button>
  )
}
```
  return (
    <button
      type="button"
      className="runtime-reload-pill mono"
      onClick={() => window.location.reload()}
      title="Backend gateway was updated. Click to reload frontend bundle."
    >
      <span className="dot dot-yellow" />
      <span>Update available · Reload</span>
    </button>
  )
}
```

### 2. 样式规范 (`frontend/src/index.css`)
```css
.runtime-reload-pill {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: 3px 8px;
  background: var(--surface-2);
  border: 1px solid var(--yellow);
  border-radius: 999px;
  color: var(--yellow);
  font-size: var(--fs-2xs);
  cursor: pointer;
  transition: background 80ms;
}
.runtime-reload-pill:hover {
  background: var(--surface-overlay);
}
```

### 3. 极简呈现形态：
在顶栏中，只有当后端发生实质升级重启时，才会在顶栏安静亮起一个琥珀色胶囊：
```
[行情条] ... [ 🟡 Update available · Reload ]  [ 🔍 ⌘K ]  [ 🔔 2 ]  [ ● OPERATIONAL ]
```
操作员工作完毕后轻点一下，1.2 秒平滑重载最新静态 Chunk，既安全又优雅。

---

## 五、 UTA 架构与多账户议题结论收敛

你指出的 **“多账户多资金池隔离完全不需要做”** 是极其透彻的业务定夺：

1. **统一交易账户（UTA - Unified Trading Account）是系统的基石**：
   Aletheia 本身就是将全币种、跨市场（现货、U本位永续合约）的抵押品、保证金与浮动盈亏全部抽象为单一资金池模型，并以此赋予 AI 统筹计算全仓风险与杠杆预算的能力。
2. **坚守奥卡姆剃刀**：
   在前端搞多账户切换只会人为打破这种统一性，增加不必要的上下文切换层级与心智负担。
3. **议题正式归档关闭**：
   在后续所有的设计文档与演进计划中，**彻底废除“多账户/多资金池隔离”课题，保持全站单一 UTA 视角的绝对纯粹**。

---

*（本方案已完整保存在本地：`docs/design/frontend/rfcs/frontend_runtime_reload_notification_rfc.md`，彻底厘清了热重载的实施边界与多账户议题的结论）*
