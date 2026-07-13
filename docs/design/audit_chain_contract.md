# Audit Chain Contract

> **范围**: 两条独立防篡改链——(1) `data/logs/audit.jsonl` 及其轮转存档的**文件级哈希链**（系统事件记忆），(2) `trade_proposals` 表的**行级哈希链**（提案不可伪造链，TaG P0-1，见 §6.4）。  
> **层级**: L2 信任边界契约（Trust Boundary — change control required）  
> **权威实现**: `aletheia/files/jsonl.py`（文件链）、`aletheia/storage/repositories.py:_compute_proposal_hash`（行链）  
> **最后更新**: 2026-06-29

---

## §1 文件位置

活跃文件路径（运行时）:

```
data/logs/audit.jsonl
```

轮转存档命名规则: `audit.jsonl.1`, `audit.jsonl.2`, … (数字递增，数字越小越旧)

目录由 `_log_dir()` 在首次写入时自动创建 (`jsonl.py:21-24`):

```python
def _log_dir() -> Path:
    d = get_data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d
```

**轮转策略**: 基于文件大小，阈值来自 `config.retention.jsonl_max_mb`（默认 50 MiB，`schema.py:299`）。不按时间/日期轮转。每次调用 `write_audit()` 前 `_rotate_jsonl_if_needed()` 检查当前文件大小；若 `>= threshold` 则立即轮转 (`jsonl.py:102-104`):

```python
def _rotate_jsonl_if_needed(path: Path) -> None:
    if path.exists() and path.stat().st_size >= _max_bytes():
        path.rename(_next_rotation_path(path))
```

轮转是 best-effort：若 `OSError` 则跳过轮转，继续写入 (`jsonl.py:56-58`)。轮转后旧文件**不删除**，永久保留作为历史存档。

---

## §2 链结构

每一行是一个独立 JSON 对象（JSONL 格式），`_prev_hash` 字段将全部记录串成哈希链。

### 必填字段 (`trust_and_change_control.md §1.4`)

| 字段 | 类型 | 说明 |
|------|------|------|
| `timestamp` | ISO-8601 字符串 | UTC 时间；`write_audit()` 若未提供则自动注入 (`jsonl.py:190-191`) |
| `actor` | 枚举字符串 | `human` / `agent` / `system` / `telegram` / `cli` |
| `source` | 枚举字符串 | `dashboard` / `api` / `cli` / `scheduler` / `agent_runtime` / `telegram` / `execution_gateway` |
| `event_type` | 字符串 | 事件类型，如 `live_unlocked`, `kill_switch_activated`, `secret_rotated` |
| `entity_type` | 字符串 | 被操作对象类型，如 `live_state`, `kill_switch`, `secret`, `command` |
| `entity_id` | 字符串 | 被操作对象的 ID（指向真实行/对象，不伪造） |
| `message` | 字符串 | 人类可读的事件描述 |
| `_prev_hash` | 64 位十六进制字符串 | 前一行序列化内容的 SHA-256（链接字段，由 `write_audit()` 注入） |

可选字段（有则包含）: `before`/`before_state`（变更前状态）、`after`/`after_state`（变更后状态）。

序列化格式: `json.dumps(event, default=str, ensure_ascii=False)` (`jsonl.py:198`)，注意 `_prev_hash` 在序列化前已注入到 `event` 中，因此 `_prev_hash` **包含在**哈希计算的字节里（详见 §3）。

---

## §3 链算法

实现位于 `jsonl.py:182-200`：

```
prev_hash = _get_last_audit_hash(use_cache=False)
event["_prev_hash"] = prev_hash
line = json.dumps(event, default=str, ensure_ascii=False)
_append_jsonl_line(...)
_last_audit_hash = _hash_record(line)
```

**哈希函数** (`jsonl.py:122-123`):

```python
def _hash_record(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()
```

- 输入: 写入磁盘的**完整行字节**（含 `_prev_hash` 字段，不排除任何字段）
- 算法: SHA-256，输出 64 位小写十六进制字符串

**链接规则**:

1. 第一条记录（整个 audit.jsonl 生命周期首条）: `_prev_hash = "0" * 64`（创世哈希，`jsonl.py:16`）
2. 后续每条: `_prev_hash = SHA-256(上一条完整行的 UTF-8 字节)`

**验证方式**: 重新对每行计算 SHA-256，确认与下一行的 `_prev_hash` 字段一致。任何行内容被修改都会导致从该行起的所有后续 `_prev_hash` 校验失败。

**并发安全** (`jsonl.py:153-179`): 每次写入持有两把锁:
- `_audit_hash_lock`（进程内线程锁）
- `_exclusive_file_lock`（跨进程文件锁，Windows 用 `msvcrt.LK_LOCK`，Unix 用 `fcntl.LOCK_EX`）

两把锁确保在读取 `prev_hash → 构造记录 → 写入` 的全过程是原子性的，不会有两条记录共用同一个 `_prev_hash`。

**SQLite 镜像规则**：`write_audit()` 默认在文件追加成功后把同一事件镜像到
`audit_events`，供结构化查询。若调用方已经在业务状态事务中提交了同一个
`AuditEvent`，必须显式传 `mirror_db=False`；该参数只抑制重复的 SQLite 镜像，
不得跳过 JSONL 追加、`_prev_hash` 注入或两把锁。这样既保留事务内权威审计，
也避免事务后的文件写入再次制造一条同义数据库记录。

---

## §4 跨轮转链接

当活跃文件被轮转（重命名为 `audit.jsonl.1` 等）后，下一条记录写入新的 `audit.jsonl` 时，`_get_last_audit_hash(use_cache=False)` 从**所有**历史文件按时间顺序读取最后一行 (`jsonl.py:139-150`, `jsonl.py:85-89`):

```python
def _jsonl_paths_chronological(path: Path) -> list[Path]:
    paths = _rotated_paths(path)   # audit.jsonl.1, .2, ...（按编号升序）
    if path.exists():
        paths.append(path)         # 最后加上活跃文件
    return paths
```

```python
def _last_non_empty_line(paths: list[Path]) -> str:
    last_line = ""
    for path in paths:
        ...  # 读取每个文件的最后非空行
    return last_line
```

因此新文件首条记录的 `_prev_hash` 指向轮转前最后一条记录的哈希——**链在文件轮转边界保持连续**。

`verify_audit_chain()` 对此有专项容错逻辑 (`jsonl.py:303-310`): 文件首行（`is_first_in_file=True`）的 `_prev_hash` 不匹配时标记为 benign `reanchors` 而非 error，以兼容轮转边界的历史竞态记录。并发写入者共享同一 `_prev_hash` 的情况同样被容忍（Pattern 2）。

---

## §5 验证工具

### aletheia doctor 集成

`doctor.py:536-552` 定义 `_probe_audit_chain()` 检查项，挂入 `build_report()` 的探针列表 (`doctor.py:721`):

```python
def _probe_audit_chain(report: Report) -> None:
    from aletheia.files.jsonl import verify_audit_chain
    result = verify_audit_chain()
    records = result.get("records", 0)
    if records == 0:
        report.add("trust.audit_chain", "info", "no audit records yet")
        return
    if result["valid"]:
        reanchors = result.get("reanchors", 0)
        extra = f", {reanchors} rotation re-anchors" if reanchors else ""
        report.add("trust.audit_chain", "ok", f"hash chain valid ({records} records{extra})")
    else:
        errors = result.get("errors", [])
        detail = f"chain BROKEN — {len(errors)} error(s) in {records} records: {errors[:3]}"
        report.add("trust.audit_chain", "warn", detail,
                   "audit log may have been tampered with or corrupted")
```

**check 名称**: `trust.audit_chain`

**报告内容**:

| 状态 | severity | detail 示例 |
|------|----------|-------------|
| 无记录 | `info` | `"no audit records yet"` |
| 链完整 | `ok` | `"hash chain valid (3897 records, 2 rotation re-anchors)"` |
| 链中断 | `warn` | `"chain BROKEN — 8 error(s) in 3897 records: ['audit.jsonl:42: _prev_hash mismatch ...', ...]"` |

detail 中包含: 总记录数、错误数、前 3 条错误的 `{文件名}:{行号}: _prev_hash mismatch (expected <16hex>..., got <16hex>...)` 定位信息。

### 独立验证函数

`verify_audit_chain()` (`jsonl.py:249-324`) 可独立调用，返回:

```python
{"valid": bool, "records": int, "errors": list[str], "reanchors": int}
```

遍历所有历史文件 + 活跃文件（按 `_jsonl_paths_chronological` 顺序），对每行校验 `_prev_hash`。

---

## §6 信任不变量

### §6.1 仅追加（Append-Only）

`data/logs/audit.jsonl` 及其轮转存档**只允许追加写入**，禁止任何路径对文件执行 `truncate`、`overwrite`、或删除行。

- 实现保证: `write_audit()` 经 `_append_jsonl_line()` → `path.open("a", encoding="utf-8")` (`jsonl.py:59`)，以 `"a"` 模式打开，操作系统级别追加。
- ⚠️ **无 truncate 防护**: 代码层面没有显式拒绝其他路径写入 audit 文件的机制；信任依赖进程隔离和 change-control 纪律（见 §8）。

### §6.2 链中断必须报警

链中断时 `aletheia doctor` 必须以 `warn` 级别上报（`_probe_audit_chain` 实现了此逻辑，`doctor.py:549-552`）。

⚠️ **当前状态（已知缺陷）**: 报告级别为 `warn`（非 `blocker`）。doctor 在链断时不阻塞网关启动，仅文字告警。见 §7。

### §6.3 信任边界操作必须写审计记录

以下操作类型必须写入 `audit.jsonl` 记录，且必须在操作完成后持久化：

| 操作 | event_type | 实现位置 |
|------|-----------|----------|
| live 解锁 | `live_unlocked` | `server/routes/live.py:35-43` |
| live 加锁 | `live_locked` | `server/routes/live.py:56-65` |
| kill switch 激活 | `kill_switch_activated` | `risk/kill_switch.py:27-36` |
| kill switch 解除 | `kill_switch_deactivated` | `risk/kill_switch.py:45-54` |
| 凭证变更 | `secret_rotated` | `cli/secrets_cmd.py:46-50`；前端 Settings 路由另有审计 |
| Agent Shell 命令执行（含拒绝） | `agent_shell_command` | `agent_shell/audit.py:37-44` |
| 外部持仓认领（reconciler） | `position_adopted` | `portfolio/position_reconciler.py:361-382`；`after_state` 含 `commit_hash` + `parent_hash`（链入 §6.4 提案 hash chain，TaG P1-2） |

`FORBIDDEN_INTENTS` 集合中的意图（`agent_shell/registry.py:415-426`）在 `agent_shell/commands.py:167` 被拦截；被拦截时同路径写 `agent_shell_command` 审计记录，`message` 前缀为 `"DENIED"`。

### §6.4 提案级 hash chain（TaG P0-1，2026-06-29）

`trade_proposals` 表自 P0-1 起携带行级 hash chain，与 §6.1–6.3 的 `audit.jsonl` 文件链**并存且独立**——前者保护 SQLite 提案记录不可伪造，后者保护审计日志事件流不可篡改。

**两链对照表**

| 维度 | `audit.jsonl` 文件链（§6.1–6.3） | `trade_proposals` 行链（§6.4） |
|------|---------------------------------|-------------------------------|
| 保护对象 | 审计事件日志（系统级事件流） | 交易提案记录（决策级实体） |
| 存储 | `data/logs/audit.jsonl` 及轮转 | SQLite `trade_proposals` 表 |
| 链节点 | 每条 JSONL 行 | 每行 `trade_proposals` |
| 前向引用字段 | `_prev_hash` | `parent_hash` |
| 当前节点哈希字段 | 由下一行的 `_prev_hash` 间接反推 | 显式 `commit_hash` 列 |
| 哈希算法 | SHA-256(整行 UTF-8 字节) 完整 64-char hex | SHA-256(canonical JSON of immutable fields) 截断 16-char hex（64-bit 空间） |
| 写入路径 | `files/jsonl.py:write_audit()`，跨进程文件锁 | `storage/repositories.py:insert_trade_proposal()`，事务内自动填 |
| 校验工具 | `verify_audit_chain()` + doctor `trust.audit_chain` 探针 | 无专用 doctor 探针（gap，见 §7.3） |

**§6.4 不变量**

1. **自动填充**：`insert_trade_proposal()` 在 `p.commit_hash is None` 时强制计算并原地回写；调用方不传也不会破链。
2. **哈希输入排除可变字段**：`status` 与 `commit_hash` 自身被显式 `model_dump(exclude=...)` 排除。`pending → executed/rejected` 是正常状态流转，**不得**因之失效。
3. **全局单链**：跨 broker / strategy / execution_mode 共享一条链，按 `rowid DESC` + `commit_hash NOT NULL` 选 parent。`parent_hash = NULL` 仅合法出现在 ① 链首（表内首条已 hash 行）或 ② P0-1 上线前写入的 legacy 行。
4. **审计事件链入**：所有 `position_adopted` 事件（§6.3 表新增行）必须在 `after_state` 携带 `commit_hash` + `parent_hash`，与 §6.4 同一节点同步；P1-2 在 `position_reconciler.py:372-379` 实施。
5. **架构等价**：未来其他合成提交路径（execution gateway / reject path）若需链入，沿用相同字段约定（不要发明新键名）。

**SDD 锚点**

- `tests/specs/spec_data_model.py` 的 DM-S08（pydantic 模型字段）/ DM-S09（migration 注册）/ DM-B06（insert 自动填 + 链入）/ DM-C02（re-compute 确定性 + 链完整性 + status 免疫）
- `tests/specs/spec_pipeline_adopted_position.py` 的 PL-ADOPT-09（audit event payload 携带 commit_hash + parent_hash）

**手动校验命令**

```
uv run pytest tests/specs/spec_data_model.py -k "hash" -q
uv run pytest tests/specs/spec_pipeline_adopted_position.py -k "commit_hash" -q
```

**设计参考**: `docs/research/OPENALICE_TAG_DEEP_DIVE_2026-06-29.md §五 P0-1 / P1-2`。

---

## §7 当前已知缺陷

### §7.1 哈希链中断（已知，未修复）

**症状**: `aletheia doctor` 报告 `trust.audit_chain: warn — chain BROKEN — 8 error(s) in 3897 records`（2026-06-22 读数）。

**严重程度**: 🟠 中等——chain 中断削弱审计的防篡改保证，但不影响新记录的写入和系统运行。错误数量（8/3897 ≈ 0.2%）极低，大概率来源于 file-lock 引入前（early 并发竞态）写入的历史记录，而非恶意篡改。

**可能根因**: `_exclusive_file_lock` 于后期引入；更早写入的若干记录在无锁并发条件下产生了 `_prev_hash` 冲突，其中少数不满足 Pattern 2（consecutive duplicate）的容忍条件。

**前向链接**: → `docs/issues/BACKEND_ISSUES.md §10.3`（audit hash chain 实施历史）

**缓解状态**: 新记录自 file-lock 引入后不再产生竞态中断；历史断点为静态、已固定。未做 chain repair 工具。

### §7.2 warn 级别不阻塞启动

`trust.audit_chain` 中断时 doctor 报告 `warn`（非 `blocker`），网关启动不受阻断。若需要强制要求链完整性，需要将 severity 升级为 `blocker`。

### §7.3 提案级 hash chain 无 doctor 探针（TaG P0-1 范围外）

`trade_proposals.commit_hash` / `parent_hash` 链当前**仅由 SDD spec 测试**（DM-C02 单事务内重计算 + 链完整性）保证，**没有运行时 doctor 探针**——链中断不会触发 `aletheia doctor` 告警。

如需补齐，需新增独立扫描函数（类比 `verify_audit_chain()`），按 `rowid` 顺序遍历 `trade_proposals`，重算每行 `_compute_proposal_hash(p)` 对比存储值 + 验证 `parent_hash` 连续性，并接入 `diagnostics/doctor.py` 作为 `trust.proposal_chain` 探针。该工作不在 P0-1 范围内，列为后续 hardening 候选。

---

## §8 改动控制

`aletheia/files/jsonl.py` 已列入信任边界受控模块（`trust_and_change_control.md §6.1`）：

> `aletheia/files/jsonl.py`: the append-only JSONL writer backing `data/logs/audit.jsonl`. Any change to write semantics, append guarantees, or file-path routing directly affects the §1.4 audit chain invariant.

**变更前必须声明**（按 `trust_and_change_control.md §6.2`）:

- 哪条不变量受影响或改变
- live 行为是否变化
- 机密处理是否变化
- 持久化状态语义是否变化
- 哪些测试证明行为正确

**最小验证命令**:

```
uv run pytest tests/test_audit_hashchain.py -q
uv run pytest tests/specs/ -q
```

**禁止的改动类型**（无需询问即可判定）:

- 将 `_append_jsonl_line` 的 `"a"` 模式改为 `"w"` 或 truncate
- 移除 `_audit_hash_lock` 或 `_exclusive_file_lock`
- 从 `write_audit()` 调用链中移除 `_prev_hash` 注入
- 更改 `_hash_record()` 的输入范围（如排除某些字段）
- 绕过 `write_audit()` 直接调用 `append_jsonl()` 写审计记录
