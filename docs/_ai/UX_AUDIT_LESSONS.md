# 全应用 UX 审计经验总结

> 2026-06-20 | Phase 2 Settings + Phase 3 全应用 (85+30 项) 修复后整理  
> 目标读者：后续 AI Agent 接手前端任务时参考

---

## 一、设计第一性原理

### 1. 操作摩擦 ∝ 不可逆程度

项目唯一安全原则：**确认成本应匹配操作后果**。

| 代价级别 | 典型操作 | 设计模式 |
|---|---|---|
| 立刻恢复 | toggle、展开面板 | 即时，无确认 |
| Session 级 | Scheduler pause | `confirm()` |
| 安全边界 | Permission Full / Exchange live / Kill Switch release | `confirm()` + 明确后果文案 |
| 破坏性+资金 | Live Unlock / Strategy retire | `confirm()` + 不可撤回说明 |

**常见错误**：把所有 toggle 做成即时生效 → 误触即可升级到 Full 模式。

### 2. UI 是数据边界，不是壳

`onChange` 入口 → `parseInt("")` → NaN → 穿透到 API。  
**规则**：在 state setter 里拦截非法值（`if (!Number.isFinite(n)) return`），不依赖后端/save。

### 3. 视觉语言要诚实（颜色 = 语义）

- 红 = 破坏性/不可撤销/紧急停止
- 黄 = 降级/注意/警告
- 绿 = 正常/active
- muted = 未知/不适用/disabled

**错误**：kill-switch 用黄色（应红）；live mode indicator 用 muted（应绿+脉冲）。

### 4. 模式必须 Ambient 展示

用户做决策时（Proposals / Trades），当前的 paper/live/demo 模式必须始终可见，不能要求用户"去 Settings 查"。
- 使用 `.mode-context-bar` + `.mode-badge-live/demo` CSS 类
- 复用已有 `['status']` query（零额外网络请求）

---

## 二、React/TanStack 模式规范

### useMutation 必须有 onError

```tsx
const mut = useMutation({
  mutationFn: () => api.doThing(),
  onSuccess: () => { setErr(null); qc.invalidateQueries({...}) },
  onError: (e: any) => setErr(e.message || 'Operation failed'),
})
// 渲染：
{err && <div className="settings-save-msg err mt-2">{err}</div>}
```

**绝对禁止**：`useMutation` 不带 `onError` → 静默失败，用户不知道操作是否成功。

### Error 展示位置规则

- 错误信息显示在**触发按钮下方**（block），不与按钮同行右侧并排
- 使用 `.settings-save-msg err` + `.mt-2` 间距类
- **禁止** `style={{}}` 内联样式（pytest 断言会拦截）

### Loading / Skeleton 规则

- `isLoading` 期间渲染 skeleton，不渲染"假空状态"（如 "No items" 实际只是还没加载完）
- 骨架屏类：`.skeleton` 容器 + `.skeleton-bar` / `.skeleton-bar-inline`
- Kill Switch 等关键状态未加载时不展示 INACTIVE（会误导），用 skeleton 占位

### Query 不需要单独加 refetchInterval

全局已配置 `defaultOptions.queries.refetchInterval: 10_000`（`main.tsx`）。
不要每个 query 重复写 `refetchInterval: 10000` 除非有特殊频率要求。

### Drawer 关闭时清理临时状态

```tsx
<DetailDrawer onClose={() => { setSelected(null); setAnalyzeErr(null) }}>
```

不清理会导致：打开 item A → 报错 → 关闭 → 打开 item B → 仍显示 A 的旧错误。

---

## 三、前端代码约束

### 禁用内联样式

`style={{...}}` 被 `test_p3_pages_use_shared_primitives` 测试断言拦截。
所有视觉控制走 CSS 类。常用工具类：
- `.mt-2` / `.mb-2` / `.mb-3` — 间距
- `.text-muted` / `.text-sm` / `.mono` — 文字
- `.flex` / `.gap-2` / `.items-center` — 布局
- `.cal-table-wrap` / `.cal-table` / `.cal-sub` — 表格
- `.clickable` — 可交互行/卡片

### humanize() 用于面向用户的 snake_case 值

```tsx
import { humanize } from '../lib/format'
// humanize('strategy_designer') → 'Strategy Designer'
```

不直接暴露 API 返回的 snake_case 字符串到 UI。

### confirm() 是合法的最小方案

项目没有 modal 组件库。`window.confirm()` 是已建立的模式（20+ 处使用）。
不为加一个确认对话框引入 UI 库。

### 路由必须正确

死链是 P0 bug。跨页链接用精确路由名：
- `/prediction-markets`（不是 `/prediction`）
- `/serenity-lens`（不是 `/serenity`）
- `/risk-controls`（不是 `/risk`）

---

## 四、测试体系规则

### pytest 静态断言结构

`tests/test_universe_settings_ui.py` 用 `Path(...).read_text()` 读取前端源码做字符串断言。

**添加新断言的模式**：
```python
def test_xxx():
    src = Path("frontend/src/pages/Foo.tsx").read_text(encoding="utf-8")
    assert "expected_pattern" in src, "Why this matters"
```

**当前断言覆盖**：
- 安全 P0（confirm 存在性）
- onError 覆盖度（计数 >= N）
- 内联样式禁止（`style={{` not in pages）
- Loading 状态存在性
- 标签/文案正确性

### 修改 UI 后的验证流程

1. `npm run build` — zero TS errors
2. `uv run pytest tests/test_universe_settings_ui.py -q` — all pass
3. 检查是否引入 `style={{`

---

## 五、Code Review 高频 Bug 模式

本次审计后 Code Review 发现的 5 类问题，后续 Agent 应主动检查：

### Bug 1: Drawer 状态泄漏

跨 item 复用同一状态变量（如 `analyzeErr`），drawer 关闭时未清理 → 打开新 item 看到旧错误。  
**修复**：`onClose` 中清理所有临时状态。

### Bug 2: indexOf 对未知值返回 -1

`array.indexOf(val)` 对不在数组中的值返回 -1，后续 `(idx + 1) % length` 计算出错误结果。  
**修复**：`if (idx < 0) return` 守卫。

### Bug 3: 删除条件守卫导致 "0" 显示

`counts[key]! > 0` 去掉 `> 0` 后，count=0 时仍渲染 badge `<span>0</span>`。  
**修复**：恢复 `> 0` 守卫，零值不显示 badge。

### Bug 4: 多 mutation 共享单一 error state

9 个 mutation 共享 `operatorErr`，任何一个 `onSuccess` 都清除其他的 error。  
**状态**：已知问题，单用户 dashboard 下 "最后一个错误胜出" 可接受。若需修复 → 每 mutation 独立 state 或 error 堆栈。

### Bug 5: 递归格式化时 key 透传

`fmtBoundary(val, key)` 递归调用子 object 的 value 时，仍传了父级 key → 子值被错误格式化为百分比。  
**修复**：递归调用不传 key：`fmtBoundary(v)` 无第二参数。

---

## 六、大规模 UI 修复的工程策略

### 批次分解原则

85 项分 8 批，优先级：
1. **安全 P0** — 误操作可导致资金风险
2. **危险操作确认** — 不可逆操作缺少摩擦
3. **Loading/Empty** — 用户被误导（假空状态）
4. **模式透明度** — 决策盲点
5. **onError 全系统** — 静默失败
6. **文字/标签** — 降低认知负担
7. **代码质量** — 可维护性
8. **测试固化** — 防回退

### 每批验证，不等全部完成

每批改完立即 `npm run build` 检查 TS 编译。不要攒到最后。

### 文件操作纪律

- **Edit 前必须 Read** — 项目有 linter 自动改写文件，上次读的内容已不是最新
- 大文件（1000+ 行如 CommandCenter.tsx）每次只改一个局部
- CSS 追加到 `index.css` 末尾，不删已有 class
- 新 CSS class 加了要同步写 pytest 断言

### 第二轮扫描的价值

首轮 85 项修完后做第二轮扫描，又发现 30 项新问题（多数是首轮修复引入的"半修复"或首轮遗漏的低优先级问题）。  
**建议**：大批量修复后总是做一轮 review scan。

---

## 七、避坑清单（快速参考）

| 坑 | 后果 | 正确做法 |
|---|---|---|
| `useMutation` 无 `onError` | 用户看不到失败 | 每个 mutation 必须有 onError |
| `style={{}}` | pytest 断言失败 | 用 CSS class |
| drawer onClose 不清 state | 旧错误泄漏到新 item | onClose 清理所有临时 state |
| kill switch 加载中显示 INACTIVE | 误导用户（以为安全） | 加载中用 skeleton |
| `indexOf` 不检查 -1 | 计算出错误索引 | `if (idx < 0) return` |
| 递归函数传递父级 context | 子值被错误格式化 | 递归时不传 context 参数 |
| 死链 `/prediction` | 404 白屏 | 用完整路由如 `/prediction-markets` |
| 首次渲染空 → "No items" | 用户以为真的没数据 | `isLoading ? skeleton : data.length ? list : emptyMsg` |
| `"(stub)"` 在 nav 标签里 | 不专业 | 若 stub，在 PageHeader role 里说明 |
| percentage 值用 `%` 标注变化量 | 语义错误 | 变化量用 `pp`（percentage points） |
