# Aletheia 后端内存持续增长:诊断报告

- 采集时间:2026-10-10 09:57–10:08(CST),Fedora 主机 `10.2.2.66`,进程 `uvicorn aletheia.server.app:app`(Python 3.14.7,PID 2313213)
- 采集方式:`sys.remote_exec`(PEP 768)向运行中的进程注入只读脚本,**没有改 aletheia 代码、没有重启**(分析前一次重启除外,见"背景")。tracemalloc 在采集窗口内开启约 6 分钟后已关闭。
- 原始数据与脚本:本目录 `raw/`、`scripts/`(Fedora 上同样保留一份在 `/home/admin/aletheia-leak-20261010/`)

## 一句话结论

**不是单纯的 Python 对象泄漏,而是"每次扫描瞬间加载大量数据 + glibc 堆碎片不归还"叠加**:
1. 进程 `RssAnon` ≈ 1.8–2.5 GB,但 glibc 堆里**真正在用的只有 0.23–0.89 GB,空闲却没还给系统的有 1.4–1.6 GB**。
2. 对运行中的进程调用一次 `malloc_trim(0)`(0.1 秒),RSS 从 **2285 MB 掉到 1181 MB**,但 6 分钟后又涨回 ~2500 MB。
3. 每次涨的"脉冲"来自两处**一次性把整表读进内存**的代码(见下),数据量随表增长,所以会越用越大。

## 证据

### 1. glibc 堆几乎全是空闲碎片(`mallinfo2`)

| 时间 | RssAnon | arena(堆总量) | in_use(真正在用) | free_held(空闲未归还) |
|---|---|---|---|---|
| 09:58:08(平稳期) | 1842 MB | 1767 MB | **227 MB** | **1540 MB** |
| 10:04:15(扫描脉冲中) | 1824 MB | 2259 MB | 887 MB | 1372 MB |

- gc 追踪的容器对象只有 110 万~132 万个,浅层大小合计 < 250 MB(dict 占 136 MB),**解释不了 1.8 GB**。
- 已加载的重型库只有 numpy、pandas、aiohttp、httpx(没有 torch/onnx 之类的大块原生内存)。
- 线程 31~58 个,glibc 会给每个线程组分配独立 arena,这是碎片放大的典型条件。

### 2. `malloc_trim(0)` 对照实验(`raw/trim_result.json`)

```
before: RssAnon 2155 MB, in_use 428 MB, free_held 1577 MB
after : RssAnon 1050 MB, in_use 431 MB, free_held 1528 MB   (耗时 0.108 s, 服务无中断)
```
RSS 掉了约 1.1 GB,`in_use` 不变 → 释放的都是碎片里的空闲页。

### 3. 之后的增长曲线(`raw/timeline.csv`,10 秒一采)

```
09:58:34 anon=1847   09:59:34 anon=2106   → 09:59:55 注入 malloc_trim
10:00:34 anon=1364   10:02:34 anon=1552   10:04:34 anon=1858
10:05:34 anon=2434   ← 30 秒内 +576 MB(一次扫描脉冲)
10:06:34 anon=2509   10:07:34 anon=2350
```
同一时段线程数 46→73,fd 86→215,都是脉冲型而不是线性泄漏。

### 4. tracemalloc:存活内存的热点(`raw/tracemalloc_top.json`,10:04–10:08 采样,总计 ~518 MB)

| 存活大小 | 位置 | 说明 |
|---|---|---|
| **232 MB** / 21.9 万个对象 | `core/aletheia/universe/candidate_selection_shadow.py:490` | `db.execute(...).fetchall()`,在 `ThreadPoolExecutor` 工作线程里,调用链 `universe/service.py:209 → build_candidate_selection_shadow_bundle → load_candidate_selection_outcomes` |
| **64 MB** / 22.5 万个 | `extensions/observation_lab/checkpoint.py:328` | `rows = db.execute(f'SELECT * FROM "{name}"').fetchall()`,调用链 `runner.py:171 → checkpoint.py:557 → :434 → :328` |
| 66 + 22 + 19 MB | `ccxt/base/exchange.py:693 / 1081 / 1112` | ccxt 解析行情/`load_markets` 的 JSON(bitget),约 10 万~96 万个小对象 |
| 8 MB | `portfolio/pnl.py:1214` | |

gc 对象计数的变化(09:58 → 10:04):`dict` 55.2 万 → 69.5 万,新增 `sqlite3.Row` 5.6 万个 —— 与上面两处 `fetchall()` 吻合。

## 根因线索:两处无界 `fetchall()`

### A. `candidate_selection_shadow.py::load_candidate_selection_outcomes`(最大)

```python
cutoff_clause = " AND b.captured_at < ?" if cutoff else ""
limit_clause = "" if cutoff else " LIMIT ?"        # ← 有 cutoff 时没有 LIMIT
...
records = db.execute(
    "SELECT r.scanner_run_id, r.compact_output_json FROM universe_twin_shadow_records r "
    "JOIN universe_twin_shadow_batches b ON b.scanner_run_id=r.scanner_run_id "
    f"WHERE b.record_version LIKE ?{cutoff_clause} "
    f"ORDER BY b.captured_at DESC, r.candidate_index{limit_clause}", params,
).fetchall()
```
- `universe/service.py:203-209` 调用时传了 `settled_through=training_cutoff`(= `captured_at`),所以**走的是没有 LIMIT 的分支**,`limit_batches=500` 在这条路径上不生效。
- 线上主库 `~/.aletheia/app.db`(3.2 GB)实测:`universe_twin_shadow_records` **115,190 行,仅 `compact_output_json` 一列就 455 MB**,`universe_twin_shadow_batches` 1,658 行。
- 每次扫描都把这 455 MB JSON 文本读成 Python `str`,再 `json.loads` 成 dict(上面 232 MB 存活 + 瞬时峰值),**内存与表大小成正比,表只增不减**。
- 峰值后 glibc 不把空闲块还给系统,RSS 只升不降。

### B. `observation_lab/checkpoint.py:328`

对每张表 `SELECT *` + `fetchall()` + 在内存里 `sorted(..., key=canonical_json_safe)`,同样与表大小成正比。

## 建议(按收益排序,**均未在线上验证**,除第 4 条的 trim 实验已验证)

1. **A 处改为流式/分批**:对 cursor 迭代(`for row in cursor:` 或 `fetchmany`),边读边 `json.loads` 边聚合,不要先 `fetchall()` 整表;或者恢复 `LIMIT`(有 cutoff 时也应限制 `limit_batches`);只取需要的字段而不是整个 `compact_output_json`。预期把每次扫描的瞬时峰值从数百 MB 降到几十 MB。
2. **B 处同理**:大表分块读取,避免 `SELECT *` 整表 + 内存排序。
3. **降低碎片**(不改业务代码,成本最低):给 `aletheia-gateway.service` 加 `Environment=MALLOC_ARENA_MAX=2`(必要时再加 `MALLOC_TRIM_THRESHOLD_=131072`),或换 jemalloc。supervisor 里 `env = os.environ.copy()` 会把它传给 uvicorn 子进程。**需要 A/B 验证效果**,我没有在线上改。
4. **临时止血**:大扫描结束后在后台线程里 `ctypes.CDLL("libc.so.6").malloc_trim(0)`(已验证 0.1 秒、可释放 ~1.1 GB、无副作用观察到)。
5. 之后建议用 `memray` 或开发环境跑 `tracemalloc` 对比 `fetchall()` 修复前后的峰值。

## 采集的局限

- 只采了一个约 10 分钟的窗口、一个进程;tracemalloc 开启期间会放大 `in_use` 读数(第二次快照的 887 MB 含扫描瞬时峰值),**不应当当作稳态值**。
- `compact_output_json` 平均约 4 KB/行是由 455 MB / 115,190 行推算的。
- 没有看到长期不降的 Python 对象增长(`gc` 计数除扫描脉冲外基本稳定),因此我把主因定为"瞬时峰值 + 碎片",而不是引用泄漏;若修复 A/B 后仍有单调增长,需要再抓一次。

## 背景(为什么采这个)

- 该主机总内存 14 GB,今天 03:24 内核 OOM 杀掉过 aletheia 的 python(anon-rss 3.4 GB)。
- 为保护主机,我临时给 `aletheia-gateway.service` 加了 drop-in:`MemoryHigh=3000M / MemoryMax=3600M`(`~/.config/systemd/user/aletheia-gateway.service.d/50-memcap.conf`),并把 `_gateway_runner.py:239` 的 `--log-level debug` 改成 `info`(未提交,工作区 1 行 diff)。采集期间曾临时放宽到 4500M/5200M,**已恢复**。
- 请 aletheia 开发者决定:上述 `info` 改动与 drop-in 是否保留、是否合入。

## 复现/复用

`scripts/snap.py`(gc 普查 + mallinfo + 开 tracemalloc)、`scripts/trim.py`(trim 对照)、`scripts/tm.py`(tracemalloc top + 关闭)。用法:

```bash
/path/to/venv/bin/python -c "import sys; sys.remote_exec(<PID>, '/abs/path/snap.py')"
```
需要 Python ≥ 3.14、同用户、`ptrace_scope` 允许。脚本把结果写到各自 `OUT` 目录常量(需先改路径)。
