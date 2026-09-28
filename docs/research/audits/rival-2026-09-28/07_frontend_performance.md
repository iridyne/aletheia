# 07 · 前端响应速度与 K 线加载研究报告

> 研究日期：2026-09-28
> 我方：`http://10.2.2.66:8787`（Fedora gateway，只读 GET）· `master@6525cee5a`
> 对照：R20/AstraQuant `https://www.r20.cn/`（真实 Chromium 渲染实测）· 仓库 `642701c4`
> **性质：分析与汇报，不含代码改动。** 修复设计在第 6 节。
> 关联 issue：#3624（OHLCV 缓存失效）· #3625（positions payload）· #3626（重复请求）· **#3628（SSE 扇出，第二轮新增）** · 汇总 #3627
> **第二轮补测（2026-09-28 晚）**：已补齐首轮"未验证"清单中的 R20 交互延迟、R20 请求计数、我方其余路由、`shell-bootstrap` 成因四项。结论有实质变化，见第 5.1、9 节。

---

## 1. 一句话结论

**慢的不是首屏加载，也不是图表库——是三个互相叠加的数据层缺陷。**

首屏我们其实不输（冷缓存下 FCP 356ms vs R20 364ms，JS 体积我们更小）。真正的差距在交互与数据路径：

| 缺陷 | 实测 | 后果 |
|---|---|---|
| OHLCV 缓存在 `limit=100` 才生效，而图表固定要 300 | **2.4s vs 0.03s，约 80 倍** | 每次开图 / 每次向左翻页都付一次上游往返 |
| `/api/positions` 返回全部 200 行 × 87 列 | 解压后 **3,006 KB**，只为渲染 **4 个** 未平仓 | 主线程反复 parse 3MB |
| 单页重复请求 | 一次 `/charts` 访问 **200 次 API 调用**，`positions` **13 次**、`shell-bootstrap` **70 次** | 累计约 5MB 传输 / 约 39MB JSON 解析 |

**"UI 反馈慢"的物理机制就是最后一条**：主线程被反复用来解析视图早已拥有的数据，于是输入响应和动画掉帧。

---

## 2. 首屏对照：我们并不落后（先排除错误假设）

冷缓存（`Network.clearBrowserCache` + `setCacheDisabled`）真实 Chromium 实测：

| 指标 | 我们 `/` | R20 `/history` |
|---|---:|---:|
| TTFB | 11 ms | 113 ms |
| **FCP** | **356 ms** | **364 ms** |
| 总传输 | 266 KB | 618 KB |
| JS 传输 | **196 KB** | 267 KB |
| 请求数 | 20 | 34 |

**首屏我们略优，JS 体积比它小 27%。** 所以"它们前端快"不是打包体积或框架问题。

技术栈对照也排除了图表库因素——**两边用的是同一套库**：

| | 我们 | R20 |
|---|---|---|
| K 线 | `klinecharts ^10.0.3` | `klinecharts ^10.0.3` |
| 轻量图 | `lightweight-charts ^4.2.3` | `lightweight-charts ^5.2.1` |
| 框架 | React 19 | Vue 3.5 |
| 生产依赖数 | 10 | 8 |

**同一个 klinecharts 大版本。** 所以 K 线渲染慢不可能是库的问题，只能是**喂给库的数据来得慢**——正是第 3 节。

> 一个必须标注的口径问题：R20 首次测量显示 `totalKB: 6`、`jsKB: 0`，那是缓存命中的结果，不能用；上表是清缓存后的数字。另外 `https://www.r20.cn/dashboard` 返回 `{"detail":"Not Found"}`，它的真实应用路由是 `/history` 等，早期若按 `/dashboard` 测会得到 2 个请求 / 1KB 的假数据。

---

## 3. 缺陷 A：OHLCV 缓存对图表完全无效（#3624）

服务端 `core/aletheia/server/routes/data.py:1673`：

```python
use_cache = since_ms is None and limit == 100
```

客户端 `frontend/src/charts/PriceChart.tsx:29-30`：

```ts
const INITIAL_LIMIT = 300
const PAGE_LIMIT = 300
```

**唯一的消费者永远请求 300，而缓存只认 100 —— 于是缓存对图表路径是死代码。**

shell 实测，各三次：

```
limit=300 : 2.395s / 2.348s / 2.006s    X-Aletheia-Data-Source: provider_fetch  （每次）
limit=100 : 0.032s / 0.023s / 0.028s    X-Aletheia-Data-Source: memory_cache    （首次之后）
```

**同一端点、同一 symbol、同一 timeframe，2.4s vs 0.03s，差异约 80 倍，只由 `limit` 取值决定。** 响应体仅约 8KB，所以这不是带宽问题，是每次都同步向交易所取数。

浏览器侧 `/charts` 冷加载，最慢的两个请求都是它：

```
/api/market/ohlcv?...limit=300              2448 ms   8 KB
/api/market/ohlcv?...limit=300&before=...   2135 ms   8 KB
```

**合计约 4.6 秒，换来 16KB。**

`data.py:1630-1636` 的 docstring 说明了设计意图（最新视图不能给冻结的蜡烛、历史分页各自是独立窗口）——**意图是对的，但它是按 `limit=100` 写的**，而唯一消费者用 300。这是典型的"两个文件靠一个未写下来的魔数达成一致，然后悄悄漂移"。

---

## 4. 缺陷 B：为渲染 4 个持仓下载 3MB（#3625）

```
rows                   : 200
  status=open          : 4
  status=closed        : 196        ← 98%
execution_mode         : paper_internal 170 / bitget_demo 30
columns per row        : 87
wire (gzip)            : 381.5 KB
decoded JSON           : 3,006.5 KB   ← 浏览器要 parse 3MB
只含 open 行的话        : 10.9 KB
```

**99.6% 是视图不用的历史。** gzip 掩盖了传输成本（7.9 倍压缩比），但掩盖不了 parse/GC 成本——那笔开销落在主线程上。

后端其实已有"哪些持仓算数"的概念，`core/aletheia/risk/engine.py:332`：

```python
return [row for row in list_all_open_positions() if row.get("execution_mode") != "paper_internal"]
```

**风控层知道要排除 `paper_internal`，HTTP 层没应用这个概念。** 170/200 行是 2026-08-10 → 09-05 的归档模拟器数据。

> 附带一个正确性警示（不只是性能）：这个端点名字暗示"当前持仓"却返回全量历史，**我自己在本轮分析中就因此误判过一次**——按行数读成了"200 个未平仓、仓位无限堆叠"，实际只有 4 个 open。对任何消费者都是同类陷阱。

---

## 5. 缺陷 C：一次访问 200 个 API 调用（#3626）

真实 Chromium，冷缓存，进入 `/charts` 静置 12s：

| endpoint | 次数 | 传输 |
|---|---:|---:|
| `/api/positions` | **13** | **4,966 KB** |
| `/api/market/snapshots` | 14 | 261 KB |
| `/api/opportunities` | 7 | 154 KB |
| `/api/ui/shell-bootstrap` | **70** | 140 KB |
| `/api/trades` | 12 | 96 KB |
| `/api/market/microstructure` | 23 | 23 KB |
| `/api/market/ohlcv` | 35 | 16 KB |
| **合计** | **200** | — |

`/api/positions` 13 次 × 约 382KB ≈ **5MB 传输**；因每个响应解压后约 3MB，**单页访问约 39MB JSON 解析**。这就是"UI 反馈慢"的直接物理原因。

`/api/ui/shell-bootstrap` **70 次**最异常。它的配置（`frontend/src/App.tsx:77-83`）是 `staleTime: 5_000` + `refetchInterval: 10_000`，在约 16s 窗口内应该约 2 次。**成因已在第二轮定位（见 5.1）：不是重复挂载，是 invalidation 扇出。**

**全局默认值是合理的**（`frontend/src/main.tsx:38-44`：`staleTime: 30_000`、`refetchOnMount: true`、`refetchOnWindowFocus: false`，并且注释解释了为什么）。**所以问题不在默认层，而在 per-query 覆盖或组件挂载行为击穿了缓存。**

35 次 OHLCV 与缺陷 A 叠加：每次都是 `limit=300` 的未缓存 2.4s 上游取数。

## 5.1 第二轮补测：成因定位 + 更严重的路由（#3628）

首轮把 `shell-bootstrap` ×70 标成了 `[推断]`。第二轮定位到确切机制，并发现**比 `/charts` 更糟的路由**。

### 逐路由请求计数（同一方法：冷缓存 + 静置 11s）

| route | API 调用 | API KB | FCP | 最重端点 |
|---|---:|---:|---:|---|
| `/trades` | 9 | 405 | 288 ms | `/api/positions` ×1 = 382KB |
| `/universe` | 13 | 126 | 248 ms | `/api/universe/workbench` ×4 = 111KB |
| **`/command-center`** | **241** | **1062** | 240 ms | `operator/tasks` **×57** = 399KB · `operator/sessions` **×57** = 171KB · `operator/status` **×58** = 58KB |
| `/charts`（首轮） | 200 | — | 244 ms | `/api/positions` ×13 = 4966KB |

**`/command-center` 是全 App 最差的路由：约 11 秒内 241 次调用。**

好消息：`/trades` 与 `/universe` 只有 9 / 13 次，**重复请求不是全站性问题**，集中在 `/charts` 与 `/command-center`。但 `/trades` 仍然为一个表格拉了 382KB 的 `positions`（缺陷 B 确实跨路由存在，符合首轮推测）。

### 成因：一个 SSE 事件失效十个 query key

`frontend/src/pages/CommandCenter.tsx:490-511`：

```ts
const handleOperatorEvent = (event: MessageEvent<string>) => {
  ...
  startTransition(() => {
    qc.setQueryData(['operator-events'], current => mergeEvents(current || [], parsed))  // ← 正确范例
  })
  void qc.invalidateQueries({ queryKey: ['operator-status'] })
  void qc.invalidateQueries({ queryKey: ['operator-extensions'] })
  void qc.invalidateQueries({ queryKey: ['operator-sessions'] })
  void qc.invalidateQueries({ queryKey: ['operator-tasks'] })
  void qc.invalidateQueries({ queryKey: ['operator-recommendations'] })
  void qc.invalidateQueries({ queryKey: ['operator-commands'] })
  void qc.invalidateQueries({ queryKey: ['operator-session'] })
  void qc.invalidateQueries({ queryKey: ['operator-events', 'session'] })
  void qc.invalidateQueries({ queryKey: ['operator-events', 'task'] })
  void qc.invalidateQueries({ queryKey: ['operator-context'] })
}
```

**关键点：`operator-sessions`（`:407-410`）与 `operator-tasks`（`:411-414`）根本没有声明 `refetchInterval`。** 所以 57 次不可能来自轮询——**N 个事件 × 10 个 key = 10N 次重取**。`operator-status` 虽有 `refetchInterval: 10000`（`:403`）却跑了 58 次，即约 57 次来自扇出。

**这意味着请求量随 operator 活动量线性增长**——UI 恰好在操作员真正在工作时最卡。

同一机制解释了 `shell-bootstrap` ×70：该 key 除自身 10s interval 外，至少还有 5 个失效/重取站点——`components/StatusCluster.tsx:190,199,209`、`components/CommandPalette.tsx:38,136`、`lib/wakeupGate.ts:19`、`lib/useRealtimeUpdates.ts:32`，外加 `components/MarketTicker.tsx:85-86` 用同一 key 开了第二个 `useQuery`。

注意 `:495-498` 的 `startTransition` + `setQueryData` 是**正确做法**（从事件负载直接更新缓存，不发请求）；紧随其后的十个 `invalidateQueries` 把这个好处对其余所有 key 抵消掉了。

---
## 5.2 第二轮补测：R20 正面对照（首轮缺失的关键一环）

首轮只测了 R20 的加载指标，没测它的交互与请求纪律，所以"它更快"只能算主观观察。第二轮从 landing 点进它的真实终端（`https://www.r20.cn/trading`，标题「实盘矩阵」）实测：

### 请求纪律：29 vs 200

| | R20 `/trading`（图表页） | 我们 `/charts` |
|---|---:|---:|
| API 调用 | **29** | **200** |
| API 传输 | 757 KB | — |
| 重复项 | `candles` ×19 = 57KB · `/api/all` ×8 = 688KB | `positions` ×13 = 4966KB · `shell-bootstrap` ×70 |

**它也有重复请求问题**（`/api/all` ×8，单次解码 309KB、耗时 4.5s，是它最慢的端点），**但总调用量只有我们的 1/7。**

### K 线端点：65ms vs 2400ms（决定性对照）

在它页面内直接连打它自己的蜡烛端点，`limit=300`、`cache: "no-store"`：

```
/api/v1/market/SUI-USDT-SWAP/candles?bar=15m&limit=300
  155ms / 65ms / 69ms / 69ms      每次 27KB
```

对照我们同形状请求（`limit=300`）：**2395ms / 2348ms / 2006ms，每次 8KB。**

**同样是 300 根蜡烛，它 65–69ms，我们 2.4 秒——约 35 倍差距，而且我们的响应体还更小。** 这是对"K 线加载慢"最直接的量化证据，且它与缺陷 A 完全吻合：我们每次都在向交易所同步取数，它在本地命中。

**结论修正**：首轮说"它交互更快仍是主观观察"。现在可以确定地说：**在 K 线数据获取这一项上，它客观快约 35 倍，根因是我们的缓存键写错了（#3624），不是它前端更优。** 它的前端工程反而有我们没有的问题（`/api/all` 单端点 309KB / 4.5s）。

### 附带确认：它的终端自述 Demo

其终端顶部指标卡标注 **"Demo — Aggregated equity across venues"**，与此前 `/api/v1/public/plaza/profile` 返回 `is_live: false` 一致。**其公开展示的业绩来自模拟盘**，此点与 06 域的结论相互印证。

## 6. 修复设计（供未来实施）

### 6.1 顺序与依赖

| 序 | 动作 | issue | 依赖 | 预期效果 |
|---|---|---|---|---|
| 1 | OHLCV 缓存键纳入 `limit` | #3624 | 无 | 开图/翻页 2.4s → 数十 ms（R20 实测同形状 65ms，是可达基线） |
| 2 | `/api/positions` 默认只返回 open + 收窄列 | #3625 | 无 | 单次解码 3006 KB → 约 11 KB；同时修好 `/trades`（该路由也拉 382KB） |
| 3 | **SSE 扇出改 `setQueryData` + 按 key coalesce** | #3628 | 无 | `/command-center` 241 次 → O(1)/key；顺带解决 `shell-bootstrap` ×70 |
| 4 | 收尾其余重复请求 | #3626 | 建议在 1–3 之后 | `/charts` 200 次降一个数量级 |

**为什么 1、2 在前**：先把单次成本降下来，剩余的重复就便宜了；反过来先压重复，仍然每次付 3MB / 2.4s。
**为什么 3 提前到 4 之前**：第二轮定位到 #3628 的扇出是 #3626 的主因之一（`shell-bootstrap` ×70 同源），修 3 会自动带走 4 的一部分。
**范围提示**：`/trades` 9 次、`/universe` 13 次 —— 重复请求**不是全站性问题**，只集中在 `/charts` 与 `/command-center`，不需要全局改造。

### 6.2 各条要点

**#3624**：把 `limit` 纳入缓存键而非 `limit == 100` 相等判断（`data.py:1673`）；缓存本身已接受 `limit` 参数（`:1683-1689`）。保留 `before > 0` 不缓存（这部分设计正确）。可考虑"先给缓存尾部、后台再验证"，让秒级过时的蜡烛不阻塞首绘。**补一个断言：图表实际用的 `INITIAL_LIMIT` 必须是可缓存值**，防止两个文件再次悄悄漂移。

**#3625**：默认只返回 open，历史走 `?status=` 或独立分页端点；复用已有的 `execution_mode != "paper_internal"` 概念（`risk/engine.py:332`）而不是另造一个过滤器；列投影按视图所需，宽行保留在明细端点；**同一次改动里迁移唯一消费者 `frontend/src/App.tsx`，不留兼容别名**。

**#3628（第二轮新增，优先级高于 #3626）**：优先 `setQueryData` 从事件负载更新——`CommandCenter.tsx:495-498` 已有正确范例；必须重取时按 key 做 debounce/coalesce，使 N 个事件塌缩成 O(1) 次；按解析出的事件类型**收窄扇出**，而不是每个事件都失效十个 key；同样处理 `['ui-shell-bootstrap']`（≥5 个失效站点 + 10s interval + `MarketTicker.tsx:85-86` 的第二个同 key query）。

**#3626**：`shell-bootstrap` 的成因已由 #3628 定位（invalidation 扇出，非重复挂载），**修成因而不是抬高 `staleTime` 掩盖**；确保同一资源共享同一 query key 以便 N 个消费者去重（React Query 默认会去重，是 per-call 选项分叉破坏了它）；把轮询频率与 payload 重量解耦——重端点应低频或事件驱动（已观测到 `/api/ws/ticket`）；加一个"单路由挂载请求预算"的 dev 断言，否则页面长大后这个数字会重新失控。

### 6.3 度量方式（改动前后同一口径）

```bash
# OHLCV 缓存
curl -s -o /dev/null -D - ".../api/market/ohlcv?symbol=BTC/USDT&timeframe=1h&limit=300" | grep -i x-aletheia-data-source

# positions payload
curl -s --compressed ".../api/positions" | python3 -c "import sys,json;d=json.load(sys.stdin);print(len(d),'rows',sum(1 for p in d if p['status']=='open'),'open')"
```

```js
// 单页请求计数（浏览器 console）
const rs = performance.getEntriesByType("resource").filter(r => /\/api\//.test(r.name));
const c = {}; for (const r of rs) { const k = r.name.replace(/^https?:\/\/[^/]+/,'').split('?')[0]; c[k]=(c[k]||0)+1; }
console.log(rs.length, Object.entries(c).filter(([,n])=>n>1).sort((a,b)=>b[1]-a[1]));
```

---

## 7. 首轮"未验证"清单的处置（全部已补测）

| 首轮未验证项 | 第二轮结果 |
|---|---|
| `shell-bootstrap` ×70 的成因 | **已定位**：invalidation 扇出，非重复挂载。该 key 除 10s interval 外有 ≥5 个失效/重取站点，另有 `MarketTicker.tsx:85-86` 第二个同 key query。同机制在 `/command-center` 更严重 → #3628 |
| R20 真实交互延迟 | **已实测**：其蜡烛端点 `limit=300` 连打四次为 155/65/69/69 ms（27KB）。我们同形状为 2395/2348/2006 ms（8KB）。**约 35 倍差距，根因是 #3624** |
| R20 是否也有重复请求 | **有**：`/api/all` ×8（单次解码 309KB、4.5s，是其最慢端点）、`candles` ×19。但总调用 **29 次 vs 我们 200 次** |
| 我方其余路由 | **已测**：`/trades` 9 次（仍拉 382KB positions）、`/universe` 13 次、**`/command-center` 241 次（全站最差）**。重复请求不是全站性问题 |
| `positions` payload 是否跨路由 | **确认跨路由**：`/trades` 与 `/command-center` 都拉 382KB。首轮推测成立 |

## 8. 仍然未验证（诚实边界）

- **修复后的真实收益**。第 6 节数字是按当前实测推算的上界；R20 的 65ms 说明数量级可达，但我们的后端形态不同，不保证等同。
- **我方 WebSocket 的实际使用程度**。只观测到 `/api/ws/ticket` 存在，未追踪其推送了哪些数据、能替代哪些轮询。
- **R20 的图表拖拽/缩放帧率**。测了数据获取延迟，未测渲染帧率（两边同为 klinecharts，渲染差异预期不大，但未证）。
- **`/command-center` 的 241 次是否随事件量线性增长**。机制上应当如此（10 invalidations/event），但未做不同事件速率下的对照实验。
- **其余未测路由**：`/learning`、`/news`、`/settings`、`/risk`、`/agent-activity` 等。按已测样本推断，重端点 + SSE 扇出是主要风险面，但未逐一验证。
- **`/api/all` 式聚合端点的取舍**。R20 用一个 309KB 的聚合端点换取更少的请求数；我们是多个细端点。两种取舍各有代价，本报告未评估哪种更适合我们。

---

## 9. 一句话给未来的实施者

**不要动图表库，也不要优化打包体积——首屏和库都不是瓶颈**（冷缓存 FCP 我们 356ms vs R20 364ms，JS 体积我们小 27%，且两边同为 `klinecharts ^10.0.3`）。

按 **#3624 → #3625 → #3628 → #3626** 修数据层：一个缓存键、一个默认 scope、一处 SSE 扇出收窄、最后收尾剩余重复。**四条都不涉及 UI 重写。**

最强的单点证据：**R20 同样 300 根蜡烛只要 65ms，我们要 2400ms，而我们的响应体更小。** 这不是它前端更优，是我们的缓存键写错了一个数字。
