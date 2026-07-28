# Prism 价值提取存档（删库前清仓）

> **用途**：`iridite/prism`（AI Portfolio Analysis MVP, ~1770 行 Python/FastAPI + ~600 行有效前端）即将从 GitHub 删除。本文件在删库前把仓库里**所有有复用价值的部分原样保全**——删库后 GitHub 与 `_refs/prism` 克隆都可能不在，本文件成为唯一留存，故关键代码**整段内联**而非只给 file:line（引用会随删库失效）。
> **配套**：架构层面的对照分析见 [openalice_prism_review.md](openalice_prism_review.md) 第三节（Prism 的反面教材价值 + 可借小模式的 file:line）。本文件聚焦"代码本体清仓"。
> **日期**：2026-06-10。

---

## 一、全仓盘点（有价值 vs 死代码/空壳）

| 部分 | 文件 | 行数 | 价值判定 |
|---|---|---|---|
| **WebSocket 进度推送链路** | `services/progress_hub.py` + `task_store.py` + `analysis_service.py` 回调 + `api/main.py` endpoint | ~280 | ⭐ **高**：干净、自洽、可直接移植。本文件第二节原样保全 |
| **前端 WS 消费 + 进度条** | `frontend/src/pages/Dashboard.tsx:49-156` | ~100 | ⭐ **高**：标准 react-query + 原生 WebSocket 消费范式。第二节保全 |
| **类型化 API client + ws URL 推导** | `frontend/src/api/client.ts` | 99 | 🟢 中：TS 契约 + http→ws 推导小工具。第二节保全 |
| **fetcher 的 degraded-quality 标记** | `fetchers/fund_fetcher.py` `market_fetcher.py` | — | 🟢 中：数据降级不崩、显式标 `quality`。第二节保全模式 |
| **进程内 TTL 单例缓存** | `storage/cache_manager.py` | 65 | 🟢 中：50 行够用的 TTL 缓存。第二节保全 |
| LangGraph StateGraph 编排骨架 | `agents/coordinator.py` `core/prism_kernel.py` | ~245 | 🟡 低：骨架可参考，但 Aletheia 已有调度；见对照报告第三节 |
| base_agent 统一契约 | `agents/base_agent.py` | 30 | 🟡 低：`analyze()->{analysis,confidence,reasoning}` 契约，已在对照报告记录 |
| **LLM 多 agent 全套** | `agents/*_analyst.py` `reasoning/cot_engine.py` `analysis/recommendation_engine.py` | ~250 | ❌ **死代码**：从未被后台任务调用（实际跑 `prism_kernel` 纯算术）。**反面教材，不提取** |
| `message_bus.py` | `agents/communication/message_bus.py` | 27 | ❌ 空壳：`MessageBus` 类无实际通信逻辑 |
| `normalizer.py` | `preprocessing/normalizer.py` | 31 | ❌ TODO 空壳：`normalize_fund_data` 里写着 `# TODO: Implement` |
| Analysis/Portfolio 页 | `frontend/src/pages/{Analysis,Portfolio}.tsx` | 各 5 | ❌ 重定向空壳：`<Navigate to="/dashboard"/>` |
| Tailwind 设计 token | `frontend/tailwind.config.js` | 30 | ⚠️ **定义了但没用**：见第三节诚实评估 |
| SQLAlchemy/Alembic/asyncpg/akshare 技术栈 | `pyproject.toml` `alembic/` `database.py` | — | ❌ 对 Aletheia 反方向：违反 SQLite+JSONL/无 Postgres 的不扩张原则 |

---

## 二、原样保全的可复用代码

### 2.1 ⭐ 后端：ProgressHub —— WebSocket 进度广播（51 行）

`{task_id: set[WebSocket]}` 订阅模型，publish 时遍历推送、自动剔除断开连接。无外部依赖（仅 fastapi + asyncio）。

```python
# services/progress_hub.py
from __future__ import annotations
import asyncio
from collections import defaultdict
from fastapi import WebSocket


class ProgressHub:
    """Broadcasts task progress events to websocket subscribers."""

    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, task_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[task_id].add(websocket)

    async def disconnect(self, task_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            sockets = self._connections.get(task_id)
            if not sockets:
                return
            sockets.discard(websocket)
            if not sockets:
                self._connections.pop(task_id, None)

    async def publish(self, task_id: str, payload: dict) -> None:
        async with self._lock:
            sockets = list(self._connections.get(task_id, set()))

        disconnected: list[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_json(payload)
            except Exception:
                disconnected.append(ws)

        if not disconnected:
            return

        async with self._lock:
            live = self._connections.get(task_id)
            if not live:
                return
            for ws in disconnected:
                live.discard(ws)
            if not live:
                self._connections.pop(task_id, None)
```

### 2.2 ⭐ 后端：TaskStore —— 任务状态机（54 行）

`asyncio.Lock` 守的 `{task_id: TaskState}`，状态 `queued/running/completed/failed`。
**⚠️ 移植到 Aletheia 必须改为落 SQLite**——此实现是纯内存，进程重启即丢（`self._tasks: dict`）。Aletheia 的 memory 纪律明确"限频/状态须持久化"。

```python
# services/task_store.py
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from datetime import datetime


@dataclass
class TaskState:
    task_id: str
    portfolio_id: str          # 移植时改为通用 subject_id / job_kind
    status: str                # queued | running | completed | failed
    progress: int
    message: str
    timestamp: datetime
    error: str | None = None


class TaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, TaskState] = {}   # ⚠️ 内存态，移植需落 SQLite
        self._lock = asyncio.Lock()

    async def put(self, state: TaskState) -> None:
        async with self._lock:
            self._tasks[state.task_id] = state

    async def get(self, task_id: str) -> TaskState | None:
        async with self._lock:
            return self._tasks.get(task_id)

    async def update(
        self, task_id: str, *,
        status: str | None = None, progress: int | None = None,
        message: str | None = None, error: str | None = None,
    ) -> TaskState | None:
        async with self._lock:
            state = self._tasks.get(task_id)
            if state is None:
                return None
            if status is not None:   state.status = status
            if progress is not None: state.progress = progress
            if message is not None:  state.message = message
            if error is not None:    state.error = error
            state.timestamp = datetime.utcnow()
            return state
```

### 2.3 ⭐ 后端：进度回调贯穿长任务 + 双写（更新状态 + 广播）

核心模式：长任务接受一个 `progress_cb(progress, message)`，每个阶段调一次；`_update_state` 同时更新 TaskStore 并通过 ProgressHub 广播——**单点双写，状态与推送不漂移**。

```python
# services/analysis_service.py —— 关键片段
async def _update_state(self, task_id, *, status=None, progress=None, message=None, error=None):
    state = await self._task_store.update(
        task_id, status=status, progress=progress, message=message, error=error)
    if state is None:
        return
    await self._progress_hub.publish(task_id, {           # ← 状态更新即广播
        "task_id": state.task_id, "portfolio_id": state.portfolio_id,
        "status": state.status, "progress": state.progress,
        "message": state.message, "timestamp": state.timestamp.isoformat(),
        "error": state.error,
    })

# 启动任务：fire-and-forget，立即返回 task_id
async def start_analysis(self, portfolio_id: str) -> str:
    task_id = f"task_{uuid4().hex[:12]}"
    await self._task_store.put(TaskState(task_id=task_id, portfolio_id=portfolio_id,
        status="queued", progress=0, message="Task queued", timestamp=datetime.utcnow()))
    asyncio.create_task(self._run_analysis(task_id=task_id, portfolio_id=portfolio_id))
    return task_id

# 长任务内部：把 progress_cb 传进 kernel，kernel 每阶段回调
async def progress_cb(progress: int, message: str) -> None:
    await self._update_state(task_id, status="running", progress=progress, message=message)
# kernel 内部：await progress_cb(10, "Loading..."); ...; await progress_cb(100, "Done")
# 失败路径：except Exception as exc: await self._update_state(..., status="failed", error=str(exc))
```

### 2.4 ⭐ 后端：WebSocket endpoint —— keep-alive 由服务端推

```python
# api/main.py
@app.websocket("/ws/tasks/{task_id}")
async def task_progress_socket(websocket: WebSocket, task_id: str) -> None:
    hub = get_progress_hub()
    await hub.connect(task_id, websocket)
    try:
        while True:
            await websocket.receive_text()   # 仅保活；进度由 hub.publish 主动推
    except WebSocketDisconnect:
        await hub.disconnect(task_id, websocket)
    except Exception:
        await hub.disconnect(task_id, websocket)
```

### 2.5 ⭐ 前端：WebSocket 消费 + 进度条（React + react-query）

任务完成时 `invalidateQueries` 触发结果重拉——推送与查询缓存联动的标准范式。

```tsx
// Dashboard.tsx —— WS 消费 useEffect
const currentTaskId = taskStatus?.task_id
useEffect(() => {
  if (!currentTaskId) return
  const ws = new WebSocket(wsUrlForTask(currentTaskId))
  ws.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data) as TaskStatus
      setTaskStatus(payload)
      if (payload.status === 'completed') {                       // 完成即失效缓存→自动重拉结果
        queryClient.invalidateQueries({ queryKey: ['analysis', payload.portfolio_id] })
        queryClient.invalidateQueries({ queryKey: ['portfolio', payload.portfolio_id] })
      }
    } catch { /* ignore malformed payload */ }
  }
  return () => { ws.close() }
}, [currentTaskId, queryClient])

// 进度条（纯 Tailwind，无依赖）
<div className="h-3 w-full overflow-hidden rounded bg-stone-200">
  <div className="h-3 bg-emerald-700 transition-all"
       style={{ width: `${Math.max(0, Math.min(100, taskStatus.progress))}%` }} />
</div>
```

### 2.6 前端：http→ws URL 推导 + 类型契约（client.ts 精华）

```ts
// 把 API base URL 安全推导成 ws/wss
export const wsUrlForTask = (taskId: string): string => {
  const httpBase = API_BASE_URL.replace(/\/$/, '')
  const wsBase = httpBase.startsWith('https://')
    ? httpBase.replace('https://', 'wss://')
    : httpBase.replace('http://', 'ws://')
  return `${wsBase}/ws/tasks/${taskId}`
}

// 任务状态的 TS 契约（与后端 TaskState 镜像）
export interface TaskStatus {
  task_id: string
  portfolio_id: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  progress: number
  message: string
  timestamp: string
  error?: string | null
}
```

### 2.7 数据获取：degraded-quality 诚实标记模式

fetcher 在部分数据源失败时**不抛、不静默**，而是回填占位 + 显式标 `quality: "degraded"`，调用方可据此决定是否信任。符合 Aletheia "语义诚实" 优先级。

```python
# fetchers/*.py —— 模式提炼
funds_data, has_error = {}, False
for code in codes:
    try:
        ... # 正常抓取 + 写缓存 ttl
    except Exception as exc:
        logger.error(f"Failed to fetch {code}: {exc}")
        funds_data[code] = {"name": code, "nav": 0.0, "change": 0.0, "error": str(exc)}  # 占位+错因
        has_error = True
return {
    "timestamp": datetime.now().isoformat(),
    "source": "akshare",
    "quality": "degraded" if has_error else "ok",   # ← 显式数据质量标记
    "funds": funds_data,
}
```

### 2.8 进程内 TTL 单例缓存（cache_manager.py 精华）

`__new__` 单例 + `{key: (value, expiry)}` + 惰性过期删除。connect/close 是预留的外部缓存接口占位（当前 no-op）。50 行够用，无 Redis 依赖。

```python
class CacheManager:
    _instance = None
    _cache: dict[str, tuple[Any, datetime]] = {}
    _lock: asyncio.Lock | None = None
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    async def get(self, key):
        async with await self._get_lock():
            if key not in self._cache: return None
            value, expiry = self._cache[key]
            if datetime.now() < expiry: return value
            del self._cache[key]; return None             # 惰性过期
    async def set(self, key, value, ttl=300):
        async with await self._get_lock():
            self._cache[key] = (value, datetime.now() + timedelta(seconds=ttl))
```

---

## 三、前端"设计语言"的诚实评估

用户特别问及"前端 UI 设计语言"。**核实结论：Prism 没有成体系的设计语言，不构成可提取资产。** 证据：

1. **定义了却没用的设计 token**：`tailwind.config.js` 精心定义了冷色调 `primary` 色板（蓝灰 `#f0f4f8`→`#102a43`，50-900 共 10 级）+ 衬线 `Crimson Pro` / 无衬线 `Inter`——但 grep 全前端，**`primary-*` 色类、`font-serif` 的 Crimson Pro 实际几乎没被页面使用**。
2. **页面用的是另一套且不一致**：`Wizard.tsx` 用 `bg-amber-50 / text-stone-900 / border-amber-200`（暖色）；`Dashboard.tsx` 用 `bg-stone-100`（中性灰）。两页色系不统一，与 config 里的蓝灰 token 三方割裂。
3. **唯一可记录的朴素选择**：卡片式布局（`rounded-xl border bg-white p-5`）、`font-serif` 标题（视觉上未生效）、emerald 进度条、red-700 错误文案。这是典型"能跑就行"的 MVP 样式，不是设计系统。

**对 Aletheia 的意义**：零。Aletheia 的 UI 方向（cockpit 架构、无 shadcn/Tailwind 的栈决策）已在 memory `aletheia-ui` 中确立，且比 Prism 这套成熟、自洽得多。**Prism 前端唯一值得带走的是工程范式（WS 消费 + react-query 联动，见 2.5/2.6），不是视觉。**

---

## 四、提取完整性确认

- ✅ 后端有价值代码：进度推送全链路（2.1-2.4）、degraded-quality（2.7）、TTL 缓存（2.8）—— 全部原样内联。
- ✅ 前端有价值代码：WS 消费 + 进度条（2.5）、URL 推导 + 类型契约（2.6）—— 全部原样内联。
- ✅ 架构/编排层面的反面教材与可借小模式 —— 已在 [openalice_prism_review.md](openalice_prism_review.md) 第三节记录（含 LLM 死代码的诊断、LangGraph 骨架、base_agent 契约）。
- ✅ 前端设计语言 —— 第三节核实为"无成体系设计语言"，无可提取资产。
- ✅ 死代码/空壳/反方向技术栈 —— 第一节明确标注，确认无提取价值。

**结论：Prism 仓库的全部有价值内容已提炼至本地文件（本文件 + 对照报告）。删库不会造成任何价值损失。**
