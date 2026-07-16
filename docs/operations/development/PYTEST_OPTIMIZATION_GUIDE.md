# pytest 优化与重构指南

> 本文档记录在 aletheia-trading-os 测试套件上积累的实战经验。
> 目标读者：下一个接手测试维护或提速任务的 AI/开发者。

---

## 当前基线（2026-06-17）

| 指标 | 优化前 | 优化后 |
|---|---|---|
| 全量测试数 | 1321 | **1340**（+19 属性测试） |
| 全量耗时 | ~47s | **~49s**（测试数 +1.4%，耗时基本持平） |
| 通过率 | 100% | **100%**（1338 passed，另 2 预存在前端 UI 失败） |
| SQLite 模式 | 文件 DB（disk I/O） | **in-memory `:memory:` DB**（RAM，per-test） |
| 主要瓶颈 | migrations 重建 (~38ms/用例) | blueprint + backup() 克隆 (~0.4ms)，已最优 |

> **注**：2 个预存在失败（`test_frontend_build_config`、`test_universe_settings_ui`）是文档/CSS 断言，与本次改动无关，跑前就存在。

---

## 一、本项目的根本慢点

### 1. SQLite setup/teardown（已大幅优化）

原来每个测试用例都要：

1. 创建 `tmp_path` 临时目录
2. `reset_db()` 清空连接
3. `sqlite3.connect(str(tmp_path / "test.db"))` — 磁盘 I/O
4. `run_migrations()`（执行 ~30 张表的 DDL）

现在通过 `conftest.py` 的 `_inmemory_db` autouse fixture，所有不带 `setup_env` 的测试（unit tests、route tests 等）自动得到一个 `:memory:` 内存 DB，消除磁盘 I/O。

已有的加速措施：`_blueprint + backup()` 克隆（`storage/migrations.py`），把 migration 从 ~38ms 压到 ~0.4ms，已是当前最优解，不要改动。**不要试图用 pytest-xdist 并行**——Windows 下多进程共享 SQLite 文件锁会引发竞争，得不偿失。

### 2. 真实网络调用（可消除）

任何建立真实 TCP 连接的测试，在无服务器环境下会等 timeout 才失败。**这是最容易消除的慢点。**

已修复案例：`test_openai_healthcheck_no_server`（7.61s → 0.51s），把真实连接换成 mock `ConnectError`。

---

## 二、质量门（quality gate）——已踩坑，必须了解

`aletheia/knowledge/manager.py` 中的 `_is_quality_pass()` 会静默拒绝低质量内容：

```python
_MIN_CONTENT_LENGTH = 80          # 内容必须 ≥ 80 字符
_PLACEHOLDER_PATTERNS = [
    "Content here",
    "(LLM unavailable",            # 注意：只是子串匹配，不是完整字符串
    "Synthesized 3 research notes",
    "LLM unavailable for synthesis",
]
```

**踩坑模式**：测试里用短字符串（如 `"evidence A"`、`"Reviewed paper evidence."`）调用 `create_research_note` / `create_post_trade_review` / `create_dreaming_log`，返回 `None`，后续 `item.id` 直接崩溃，或者断言的数据库记录数为 0。

**修复规则**：
- 所有测试中传给知识创建函数的内容字符串必须 ≥ 80 字符
- 内容不得包含上述 placeholder 子串
- `commands.py` 中每个调用 `create_*` 的地方，拿到返回值后必须检查 `if item is None` 再用 `item.id`

---

## 三、Mock 模式速查

### 3.1 网络/HTTP 调用 → 用 FailClient 模式

```python
import httpx
from unittest.mock import patch

class FailClient:
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def get(self, url, **k): raise httpx.ConnectError("refused")
    async def post(self, url, **k): raise httpx.ConnectError("refused")

with patch("aletheia.model_provider.openai_compatible.httpx_client", FailClient):
    health = await provider.healthcheck()
```

注意：patch 路径是**被测模块自己 import 的名字**（`aletheia.xxx.httpx_client`），不是 `httpx.AsyncClient`。

### 3.2 LLM 调用 → monkeypatch llm_complete

```python
async def _fake_llm(system, user, max_tokens=800):
    return (
        "BTC 4h shows momentum divergence with RSI overbought while price consolidates. "
        "ETH funding rates suggest crowded longs. Recommend reducing spot exposure.",  # ≥80 chars
        None,  # 无 error
    )
monkeypatch.setattr("aletheia.agents.research_agent.llm_complete", _fake_llm)
```

**何时必须 mock LLM**：测试断言「知识条目被写入」时，必须 mock 返回 ≥80 字符的内容。不 mock 时 LLM 离线，agent 写入的 fallback 内容已经被刻意设计为通过质量门（见 `research_agent.py`），但 dreaming_agent 等其他 agent 可能仍会跳过写入。

### 3.3 LLM 离线降级测试 → 不要 mock，直接跑

当测试目的是验证「LLM 不可用时 agent 不崩溃」时，**不要 mock**——让 llm_complete 真实返回 `(None, error)`，然后断言：

```python
result = await run_agent("research_agent", trigger_type="manual")
assert result["status"] in ("completed", "completed_with_errors")
```

---

## 四、APScheduler 相关注意事项

### 4.1 teardown 噪音

pytest 退出时 APScheduler 的 `RuntimeError: cannot schedule new futures after interpreter shutdown` 是 atexit 回调的噪音，**不计入 pytest 报告的耗时**，不影响测试结果，忽略即可。

### 4.2 scheduler 测试必须显式 stop

```python
def test_scheduler_starts_when_enabled(tmp_path, monkeypatch):
    ...
    s = sched.init_scheduler()
    # 做断言
    sched.stop_scheduler()   # ← 必须，否则线程池挂到 teardown
    reset_db()
```

### 4.3 teardown 耗时 ~0.9s 的用例

`test_acceptance.py::test_ac07` 等 ASGI 测试 teardown 慢，原因是 FastAPI lifespan + httpx 连接池关闭，这是框架正常开销，**不可优化**。

---

## 五、--durations 用法

找慢测试的标准命令：

```bash
uv run pytest tests/ --tb=no --durations=20 -q 2>&1 | Select-String "^\d+\.\d+s"
```

输出示例解读：
- `7.61s call` → 测试主体耗时，可优化
- `0.92s teardown` → 清理耗时，通常不可优化
- `0.4ms call` → 极快，blueprint 克隆生效

---

## 六、pytest-xdist 并行——最后阶段才用

2026-07-03 已引入 `pytest-xdist`，但**不设为默认**。

原因：
1. AI 修 bug 的默认目标是快反馈，不是把全量测试并行打满。
2. Windows 下多进程容易暴露隐藏的文件锁、端口、全局单例问题。
3. 本项目有 gateway 全局单例和 SQLite 运行态约束，不能把 `-n auto` 写进默认 `addopts`。

可用命令：

```powershell
# 只在 fast 层稳定后使用
uv run pytest -m fast -n auto -q

# 提交前后台全量可尝试并行；失败时先用非并行复现
uv run pytest tests/ -n auto --tb=line -q
```

判断原则：如果并行失败、非并行通过，先把它当成测试隔离问题处理，不要直接改生产逻辑。

---

## 七、已修复的典型失败根因

| 根因 | 影响范围 | 修法 |
|---|---|---|
| 测试内容 < 80 字符 | 数十个用例 `create_*` 返回 None | 把测试种子内容扩充到 ≥ 80 字符 |
| `item.id` 无 None 检查 | `commands.py` 多处 AttributeError | `if item is None: return {"error": ...}` |
| fallback 内容含 placeholder 子串 | `research_agent.py` 降级路径 | 重写 fallback 内容，不含 `(LLM unavailable` |
| `repr()` 双倍转义反斜杠 | `test_autostart.py` 路径断言 | `r"\.venv\Lib\site-packages"` → `r"\\.venv\\Lib\\site-packages"` |
| `kill_switch_enabled_demo = False` 默认值 | `test_daily_loss_breaker.py` | 测试内显式 `cfg.risk.kill_switch_enabled_demo = True` |
| 真实 TCP 超时等待 | `test_openai_healthcheck_no_server` 耗 7.6s | mock `ConnectError`，同其他 healthcheck 测试 |
| 断言拼写错 | `test_xstocks_observer.py` `AAPL/USD` | 改为 `AAPL/USDT`（代码注释已明确 /USDT 格式） |

---

## 八、In-memory SQLite 实现细节

### 8.1 架构

`tests/conftest.py` 的 `_inmemory_db` autouse fixture：

```python
@pytest.fixture(autouse=True)
def _inmemory_db(request, tmp_path, monkeypatch):
    if request.node.get_closest_marker("real_db"):
        yield; return
    monkeypatch.setattr(_loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(_loader, "CONFIG_PATH", tmp_path / "config.yaml")
    reset_db()
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _sq._db_conn = conn   # ← 注入 threading.local 里的连接
    run_migrations()
    yield
    reset_db()
```

### 8.2 三类测试的行为

| 测试类型 | 行为 |
|---|---|
| 普通单测/路由测试 | autouse 注入 `:memory:` DB，DATA_DIR 指向 tmp_path |
| 带 `setup_env` 的集成测试 | `setup_env` 自己 `sq._db_conn = conn`，覆盖 autouse 的连接 |
| 标 `@pytest.mark.real_db` 的测试 | autouse 提前退出，测试自己管理真实文件 DB |

### 8.3 real_db 标记适用场景

三个测试需要 `@pytest.mark.real_db`：
- `test_storage.py::test_insert_commits_and_releases_lock`
- `test_storage.py::test_update_commits_and_releases_lock`
- `test_runtime.py::test_scheduler_guard_rolls_back_leaked_sqlite_transaction`

原因：这些测试验证跨连接 WAL 锁行为，必须用**真实文件 DB**（两个独立 `sqlite3.connect` 指向同一路径）。`:memory:` DB 无 WAL，跨连接不共享数据。

---

## 九、Hypothesis 属性测试

### 9.1 Profile 配置

`conftest.py` 顶部：

```python
from hypothesis import settings as _h_settings, HealthCheck
_h_settings.register_profile("ci", max_examples=50, suppress_health_check=[HealthCheck.too_slow])
_h_settings.register_profile("dev", max_examples=20, suppress_health_check=[HealthCheck.too_slow])
_h_settings.load_profile("dev")  # 日常用 dev；CI 环境 load "ci"
```

### 9.2 已有属性测试（tests/test_hypothesis_properties.py）

| 类 | 覆盖 | 关键不变量 |
|---|---|---|
| `TestQualityGate` | `_is_quality_pass` | 短内容必拒、placeholder 必拒、干净长内容必通 |
| `TestSymbolConversions` | `to_canonical/to_bitget/to_ccxt_perp/to_yfinance` | 不崩溃、幂等、往返一致、格式规范 |
| `TestContentHash` | `_content_hash` | 确定性、空白无关、16 位 hex、不同内容不同 hash |

### 9.3 扩展属性测试的原则

只给**有明确不变量的纯函数**写属性测试：
- 格式转换（输入→输出有明确规则）
- 数学/哈希/编解码
- 边界条件（长度门、类型门）

**不适合**：有副作用（DB 写入、网络）、依赖外部状态的函数——用 example-based 测试。

---

## 十、日常工作流建议

```bash
# 改动单模块 → 只跑相关测试（秒级）
uv run pytest tests/test_knowledge_manager.py -q

# 跨模块改动 → 关键词过滤
uv run pytest -q -k "knowledge or dreaming or quality"

# 提交前 → 后台全量，不阻塞
# （在 PowerShell 后台跑或用 run_in_background）
uv run pytest tests/ --tb=no -q

# 看慢测试分布
uv run pytest tests/ --tb=no --durations=20 -q 2>&1 | Select-String "^\d+\.\d+s"
```

**原则：不等全量完成再继续工作**——后台跑，有红灯再回头修。

---

## 十一、分层 marker 与慢测试二分类（2026-07-03）

`pyproject.toml` 和 `tests/conftest.py` 现在把 pytest collection 自动分成四层：

| Marker | 用途 |
|---|---|
| `fast` | AI 默认快反馈层：便宜、本地、单测为主 |
| `integration` | 跨 route / DB / scheduler / runtime / broker 边界 |
| `e2e` | 端到端 pipeline 和 `_e2e` 文件 |
| `slow` | 不进默认 AI 快反馈：慢榜热点、训练/远程/真实联调/端到端 |
| `tier_unit` | 日常最快回归，纯函数/局部模块为主 |
| `tier_integration` | 跨 route / DB / scheduler / runtime / broker 边界 |
| `tier_e2e` | 端到端 pipeline 和 `_e2e` 文件 |
| `tier_spec` | `tests/specs/spec_*.py` 与 spec conformance matrix |

领域 marker 也会自动添加：`domain_operator` / `domain_universe` /
`domain_serenity` / `domain_risk` / `domain_execution` / `domain_market_data` /
`domain_learning` / `domain_frontend` / `domain_runtime` / `domain_storage` /
`domain_security` / `domain_config` / `domain_misc`。

常用命令：

```powershell
# AI 默认：先跑上次失败；再跑 fast
.\scripts\test_ai.ps1

# 手工只跑日常最快层
uv run pytest -m fast -q

# 只跑一个领域 / 层
uv run pytest -m "tier_integration and domain_operator" -q

# 只跑上次失败；cacheprovider 已启用，.pytest_cache/ 被 .gitignore 排除
uv run pytest --lf -q
uv run pytest --ff -q

# 慢任务挖掘 + 二分类
uv run pytest tests/ -q --tb=no --durations=20 2>&1 |
  uv run python scripts/pytest_slow_classifier.py --threshold 1.0
```

慢测试二分类脚本只按 pytest phase 做可解释分类：

| Tag | 含义 |
|---|---|
| `slow_optimizable` | `call` 阶段慢，优先检查 mock、fixture、provider retry、DB fan-out |
| `slow_structural` | `setup` / `teardown` 慢，多数是框架、lifespan、fixture 成本 |

2026-07-03 实测：

- 全量：`2267 passed, 7 skipped, 7 xfailed`，pytest 时间 `132.55s`。
- 分层计数：`fast=1189`，`slow or e2e=338`，`not slow and not e2e=1943`。
- `--durations=20` 最慢项全部是 `call` 阶段，说明慢点集中在测试主体，不是 autouse fixture 清理。
- 当前 top 20 热点：Serenity pipeline/workbench、doctor 报告、operator/session、agent runtime、少量 specs。
- 这些热点已通过路径规则自动打 `slow`，不会进入 `.\scripts\test_ai.ps1` 的默认 fast 回归。

注意：`spec_*.py` 现在被 `python_files` 默认收集。这样会更准确地暴露 SDD
契约红灯；当前 spec 层仍有红灯，不应通过禁收集或静默 xfail 掩盖。

---

## 十二、文档规则到测试的映射

核心规则必须落到可执行检查，而不是只写在 Markdown 里：

| 规则 | 可执行检查 |
|---|---|
| 订单必须经过后端执行网关 | `tests/specs/spec_execution.py`、`tests/specs/spec_pipeline_execution_chain.py` |
| 默认禁止 live trading | `tests/specs/spec_live_unlock.py`、`tests/specs/spec_serenity_live_readiness.py` |
| GET 接口不能返回密钥值 | `tests/specs/spec_api_reference.py`、`tests/test_secrets.py` |
| 市场数据不能直接用 Bitget execution client | `tests/specs/spec_data_layer.py` |
| 前端 symbol 展示必须走 `fmtSymbol` | `tests/specs/spec_equity_observer.py`、`tests/test_universe_settings_ui.py` |
| SQLite 并发三根柱子不能破 | `tests/specs/spec_data_model.py`、`tests/test_storage.py` |
| AI 默认只跑快反馈 + 上次失败 | `tests/test_pytest_tiering.py`、`scripts/test_ai.ps1` |

新增文档规则时，同步补一条测试或在这里登记已有测试；否则这条规则会在下一次重构中失效。
