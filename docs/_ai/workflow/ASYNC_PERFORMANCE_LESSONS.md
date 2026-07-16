# 异步 API 性能血泪总结

> 写于 2026-07-02，根因：GET /api/settings 冷启延迟 30 秒被 ultracode 调查确认。
> 修复提交：`0c46da3`（loader.py 缓存 + jsonl.py 减少磁盘读）。

---

## 1. 核心根因：这是"异步函数里做同步 I/O"的结构性盲区

Python 的 `async def` 不是免费的并发许可证——它只是告诉 event loop"我会主动让出控制权"。
但如果函数体里调用了同步阻塞操作（磁盘读写、threading.Lock、SQLite），它**整个卡死 event loop**，
和 `time.sleep()` 在 async 函数里一样糟糕。

本次问题链：

```
GET /api/settings  (async def)
  └─ load_config()          ← 同步：yaml.safe_load + Pydantic model_validate (磁盘读 + CPU)
       └─ 零缓存              ← 每次请求都重走一遍
  └─ 22× model_dump()       ← 同步：完整 Pydantic 序列化 × 22 section

POST /api/settings/*  (async def)
  └─ load_config()           ← 同步磁盘读
  └─ save_config()           ← 同步：35 mkdir + yaml.dump + tempfile + os.replace
       └─ ensure_data_dirs() ← 每次 35 个 mkdir 系统调用
  └─ write_audit()           ← 同步：OS 文件锁 + 读所有 audit.jsonl 历史文件 + SHA-256 + SQLite INSERT
       └─ _max_bytes()       ← 再调一次 load_config()（第三次磁盘读！）
```

FastAPI 只有在 `sync def` 函数时才自动把它扔进 threadpool（不堵 event loop）。
`async def` 里的同步调用是开发者的责任——框架无法自动识别并保护。

---

## 2. 为什么会持续写出这样的代码？

### 2a. "同步写法更简单"是默认选择

在功能开发阶段，`cfg = load_config()` 比 `cfg = await loop.run_in_executor(None, load_config)` 直觉上更清晰。
没有显式的性能压力时，开发者（包括 AI agent）会选择最短路径。

### 2b. 本地开发不暴露真实延迟

开发时 config.yaml 小、audit.jsonl 不存在、DB 几乎空。
`load_config()` 在测试机上可能只花 1–2ms，根本察觉不到问题。
生产环境：config.yaml 膨胀到 25+ section（personas、family_quota_by_regime 等）、
audit.jsonl 有几千条旋转文件、app.db 421MB——性能差了 3–4 个数量级。

### 2c. frontdoor 代理层掩盖了后端延迟

frontdoor 的 httpx keepalive 和连接复用让热路径显示 7–93ms，
用浏览器打开 Settings 感觉"可以用"——但其实是 frontdoor 复用了之前的连接，
真正 30 秒延迟只在第一个冷连接暴露（通常是刚重启后）。

"偶尔慢一次"比"每次都慢"更难被当 bug 报告。

### 2d. 没有 API 延迟监控

系统有 doctor 但没有 HTTP 请求耗时日志（gateway logs 是空的）。
没有 p95/p99 延迟指标，"慢"是主观感受而不是可追踪的数字。
问题可能存在了很长时间，只是没人拿秒表测过后端直连。

### 2e. 技术债是累积的

`write_audit` 里 `use_cache=False` 最初是为了保证 cross-process hash chain 正确性——
是合理的保守决策。但随着 audit 日志增长、settings 端点增多，
这个保守选择变成了每次保存都扫描几十个历史文件的性能陷阱。
单点决策对，整体组合错。

---

## 3. 如何在未来的开发中避免

### 规则 1：async def 里禁止直接做同步 I/O（除非能证明<1ms）

```python
# ❌ 错误
async def get_settings():
    cfg = load_config()          # 同步磁盘读，堵 event loop

# ✅ 正确（短期）：加缓存，使热路径 <1ms
cfg = load_config()              # 命中缓存 = 纯内存，无阻塞

# ✅ 正确（长期）：真正的 IO 移到 threadpool
cfg = await asyncio.get_event_loop().run_in_executor(None, load_config)
```

**判断标准**：如果这个函数在生产数据量下会做磁盘/网络/锁操作，它就不能直接在 `async def` 里调用。

### 规则 2：热路径读操作必须有缓存

凡是：
- 每次 HTTP 请求都要读的数据
- 内容变化频率 << 请求频率（config、retention 阈值等）

→ 必须加 TTL 缓存。5–60 秒对 UI 来说完全透明，对吞吐量影响是 100–1000x。

**缓存三要素**：`_cache` 变量 + `time.monotonic()` TTL + 写入时主动失效。

### 规则 3：新增 API 端点前，问三个问题

1. 这个 handler 里有没有同步 I/O（文件/DB/锁）？
2. 这个 handler 会被高频调用吗（轮询/自动刷新）？
3. 被调用的函数内部是否隐式做了昂贵操作（如 `load_config` 内藏于 `_max_bytes()`）？

### 规则 4：用实测数据代替直觉

每次加新的 API 端点，在开发时跑一次：
```powershell
Measure-Command { Invoke-WebRequest -Uri 'http://127.0.0.1:8788/api/<endpoint>' -UseBasicParsing }
```
**直连后端（8788）**，不经 frontdoor（8787），消除代理缓存掩盖。
冷路径 >100ms 就需要解释原因。

### 规则 5：轮询 UI 组件必须设置合理间隔

前端有 ~14 个并发轮询（StatusBar×6 @10s、exchange tests×6 @12-15s、model health @5s、
market ticker @15s）。在 Settings 页打开期间，这些持续占用后端 worker。

原则：
- 健康探针（exchange 连接测试）：最小间隔 60s，且只在用户看到相关 tab 时才 enable
- 状态轮询（status、kill-switch）：可以 10s，但 handler 必须是纯 DB 读（<5ms）
- LLM 结果类（model health）：最小 30s，因为每次都可能触发网络调用

---

## 4. 快速检查清单（新功能上线前）

```
□ async def handler 里没有直接的同步磁盘/DB/网络调用
□ 高频读操作有缓存（TTL + 写失效）
□ 直连后端冷路径 < 100ms（用 Measure-Command 实测）
□ 新轮询间隔 >= 30s（除非 handler 是纯内存操作）
□ ensure_data_dirs / 类似"初始化"操作只跑一次，不在热路径上
□ audit/log 写入在 POST handler 里是否可接受（考虑 async queue）
□ 同步测试替身收到已创建的 coroutine 时，是否执行、await 或显式 close
```

---

## 5. 已知遗留慢点（待优化，非紧急）

| 位置 | 问题 | 影响 | 建议 |
|---|---|---|---|
| `health.py:compute_health()` | async def 里同步 load_config + 两次 SQLite | GET /api/status 偶慢 | 加缓存或移 threadpool |
| `pnl.py:compute_portfolio()` | SELECT * FROM positions WHERE status='closed' LIMIT 10000 | GET /api/portfolio 在 421MB DB 上慢 | 加分页或只返回 summary |
| `data.py:start_agent_run()` | inline await run_agent() = 全 LLM 调用在 HTTP 请求内 | POST /api/agent-runs/start 可能 60s+ | 改为 fire-and-forget + job_id 轮询 |
| `frontdoor_route.py` | 每次代理请求读 frontdoor-route.json | 每 /api/* 一次磁盘读 | 加 10s TTL 缓存 |

---

## 6. 异步测试替身必须拥有传入 coroutine 的生命周期

Broker 的同步包装器接收的是**已经创建好的 coroutine 对象**。测试若把包装器替换成
`MagicMock(return_value=...)` 或直接 `side_effect=Exception(...)`，调用结果虽然符合断言，
但传入的 coroutine 既没有运行、也没有关闭。它通常要等到后续测试触发垃圾回收才报警，
所以警告可能错误地显示在 OpenAPI、表现统计或其他完全无关的测试名下。

测试规则：

- 需要真实执行时，使用真正的事件循环 runner。
- 只想模拟返回值或错误时，使用 `tests.async_test_utils.closing_coro_runner(...)`，
  先关闭传入 coroutine，再返回或抛错。
- 不得把 coroutine 存进列表只为统计调用次数；关闭后记录普通标记或检查 mock 调用。
- `pyproject.toml` 将未等待 coroutine 和 pytest 的 unraisable exception 设为错误，
  不得用过滤器隐藏。

这条门禁保护的是测试可信度：如果协程泄漏只作为 warning，测试可以全绿，但警告归因、
资源生命周期和后续测试隔离都是假的。

---

## 7. TestClient 与运行时 HTTP 客户端是两条依赖边界

Starlette 1.2 起，`starlette.testclient.TestClient` 官方使用 `httpx2`；旧 `httpx` 仅是
带弃用提示的兼容路径。项目因此把 `httpx2>=2.5,<3` 放在 UV 的 dev dependency group，
只服务同步 ASGI 测试。生产网络调用仍使用项目原有的 `httpx[socks]`，两者不能互相替换。

`StarletteDeprecationWarning` 已升级为测试失败：测试依赖缺失、回退到旧兼容层或未来再次
发生不兼容时，必须显式处理，不能靠忽略 warning 保持“假绿”。
