# 07 · 前端响应速度与 K 线加载研究报告

> 研究日期：2026-09-28
> 我方：`http://10.2.2.66:8787`（Fedora gateway，只读 GET）· `master@6525cee5a`
> 对照：R20/AstraQuant `https://www.r20.cn/`（真实 Chromium 渲染实测）· 仓库 `642701c4`
> **性质：分析与汇报，不含代码改动。** 修复设计在第 6 节。
> 关联 issue：#3624（OHLCV 缓存失效）· #3625（positions payload）· #3626（重复请求）· 汇总见文末

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

`/api/ui/shell-bootstrap` **70 次**最异常。它的配置（`frontend/src/App.tsx:77-83`）是 `staleTime: 5_000` + `refetchInterval: 10_000`，在约 16s 窗口内应该约 2 次。`[推断]` 多出来的来自重复挂载/多 observer，而非 interval 本身——本轮未定位到具体成因。

**全局默认值是合理的**（`frontend/src/main.tsx:38-44`：`staleTime: 30_000`、`refetchOnMount: true`、`refetchOnWindowFocus: false`，并且注释解释了为什么）。**所以问题不在默认层，而在 per-query 覆盖或组件挂载行为击穿了缓存。**

35 次 OHLCV 与缺陷 A 叠加：每次都是 `limit=300` 的未缓存 2.4s 上游取数。

---

## 6. 修复设计（供未来实施）

### 6.1 顺序与依赖

| 序 | 动作 | issue | 依赖 | 预期效果 |
|---|---|---|---|---|
| 1 | OHLCV 缓存键纳入 `limit` | #3624 | 无 | 开图/翻页从 2.4s → 数十 ms |
| 2 | `/api/positions` 默认只返回 open + 收窄列 | #3625 | 无 | 单次解码 3006 KB → 约 11 KB |
| 3 | 消除重复请求（先查 `shell-bootstrap` 70 次的成因） | #3626 | 建议在 1、2 之后 | 200 次调用降一个数量级 |

**为什么 3 放最后**：先把单次成本降下来，剩余的重复就便宜了；反过来先压重复，仍然每次付 3MB/2.4s。

### 6.2 各条要点

**#3624**：把 `limit` 纳入缓存键而非 `limit == 100` 相等判断（`data.py:1673`）；缓存本身已接受 `limit` 参数（`:1683-1689`）。保留 `before > 0` 不缓存（这部分设计正确）。可考虑"先给缓存尾部、后台再验证"，让秒级过时的蜡烛不阻塞首绘。**补一个断言：图表实际用的 `INITIAL_LIMIT` 必须是可缓存值**，防止两个文件再次悄悄漂移。

**#3625**：默认只返回 open，历史走 `?status=` 或独立分页端点；复用已有的 `execution_mode != "paper_internal"` 概念（`risk/engine.py:332`）而不是另造一个过滤器；列投影按视图所需，宽行保留在明细端点；**同一次改动里迁移唯一消费者 `frontend/src/App.tsx`，不留兼容别名**。

**#3626**：先定位 `shell-bootstrap` 为何倍增（重复挂载 / 多 observer / interval 叠加），**修成因而不是抬高 `staleTime` 掩盖**；确保同一资源共享同一 query key 以便 N 个消费者去重（React Query 默认会去重，是 per-call 选项分叉破坏了它）；把轮询频率与 payload 重量解耦——重端点应低频或事件驱动（已观测到 `/api/ws/ticket`，大快照可能根本不需要轮询）；加一个"单路由挂载请求预算"的 dev 断言，否则页面长大后这个数字会重新失控。

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

## 7. 本次未验证

- **`shell-bootstrap` 70 次的具体成因**。只确认了配置值与观测次数矛盾，未定位到重复挂载点。
- **R20 前端的真实交互延迟**。只测了加载指标；未测它的图表翻页/切 symbol 的响应时间，因此"它交互更快"仍是你的主观观察，我只证明了**我们这侧存在三个可解释该观察的确定缺陷**。
- **R20 是否也有同类重复请求**。未对其做请求计数对照。
- **我方 WebSocket 路径的实际使用程度**。只观测到 `/api/ws/ticket` 存在。
- **修复后的真实收益**。第 6 节的数字是按当前实测值推算的上界。
- **其他路由**。只系统测了 `/` 与 `/charts`；`/trades`、`/universe` 等未测，`positions` 的 payload 问题很可能在那些页面同样存在。

---

## 8. 一句话给未来的实施者

**不要动图表库，也不要优化打包体积——首屏和库都不是瓶颈。** 按 #3624 → #3625 → #3626 的顺序修数据层：一个缓存键、一个默认 scope、一次请求去重，三者都不涉及 UI 重写。
