# Testing Guide

> Aletheia 的测试体系分两层：常规 pytest 测试 + SDD executable claims。
> Trust-boundary branch coverage: 参见 [`TRUST_BOUNDARY_COVERAGE.md`](TRUST_BOUNDARY_COVERAGE.md)。

---

## Local proof receipts

The existing ci_plan/ci_local pair owns required proof lanes. Python unit,
runtime, integration, specification and E2E checks use tier_unit,
domain_runtime, tier_integration, tier_spec and tier_e2e respectively; runtime
is the existing runtime domain, not a new classification. Each executed check
records its lane, command, exit code and duration alongside the exact base/head
and plan fingerprint. A failed required check stops execution.

A required lane without an executor is required_but_unavailable and exits
nonzero, never pass. Full-suite proof requires the credential-isolated Fedora
identity/container described in DEVELOPMENT_WORKFLOW.md; the local executor
currently reports that lane unavailable rather than pretending a printed note
is proof. --skip-execute returns planned commands only, not a pass receipt.

## 快速命令

```powershell
# 改动文件快反馈门（每次 edit 后的第一道，秒级）
uv run python scripts/dev_check.py

# 单文件（秒级，日常用）
uv run pytest tests/test_xxx.py -q

# 单元层；串行计时用于跟踪同集合反馈成本
uv run pytest tests/ -m tier_unit -q --timeout=120

# 按关键字过滤
uv run pytest tests/ -k "knowledge or dreaming" -q

# SDD spec claims
uv run pytest tests/specs/ -q

# 架构依赖检查
uv run pytest tests/test_architecture_fitness.py -q

# Conformance Matrix（打印 claim 状态矩阵）
uv run pytest tests/test_spec_conformance.py -v -s

# 全量：仅限已授权、凭据隔离的 Fedora 测试环境（不是稳定 Runtime checkout）
uv run pytest tests/ -q --tb=line
```

全量验证只在凭据隔离且与稳定 Runtime 分离的 Fedora 测试身份/容器中执行，
按 DEVELOPMENT_WORKFLOW.md 的授权与隔离要求操作；不能用本地分层跑完全部测试
来绕过该边界。普通改动先跑 changed-file gate 和相关 focused tests。

---

## 测试分层

| 层级 | 路径 | 用途 |
|------|------|------|
| L0: 架构 | `tests/test_architecture_fitness.py` | import 依赖方向，禁止循环依赖 |
| L1: 单元 | `tests/test_*.py` | 模块内逻辑，mock 边界 = 网络/子进程 |
| L2a: SDD claims | `tests/specs/spec_*.py` | 契约断言，RED→GREEN 开发流 |
| L2b: 集成 | `tests/test_*.py`（含 DB）| 真实 SQLite，不 mock |
| L2c: E2E spec | `tests/specs/spec_*.py` | 关键路径端到端验证 |

---

## SDD 开发流程

新功能**先写 spec，再写代码**：

```python
# 1. 在对应 spec_*.py 里声明（RED）
SpecClaim(id="XX-S01", status="missing", description="...")

# 2. 实现代码 → GREEN

# 3. 更新 status="implemented"

# 4. 运行 Conformance Matrix；它会自动发现 tests/specs/spec_*.py
```

修改 risk/execution/trust 模块前必须先 `uv run pytest tests/specs/ -q` 确认现有 claims 全绿。

Conformance Matrix 会自动导入全部 `tests/specs/spec_*.py`。新 spec 文件如果没有
任何 `@spec_claim`，结构检查会直接失败；不再维护手工导入列表。

---

## Mock 边界规则

```
允许 mock：网络 I/O（HTTP、WebSocket）/ 子进程 / 外部 API
禁止 mock：模块间接口 / 数据库层 / 内部函数调用链
```

伪造子进程时同时隔离创建和终止；不得把虚拟 PID 交给真实 OS。

- **禁止** mock 一切只 assert_called 的测试（只验证数据结果，不验证调用次数）
- 离线交易测试同时隔离私有下单和公共 listing I/O；保留真实能力刷新、Gateway 校验与记账断言。
- DB 测试用真实隔离 SQLite，不 mock。批量持久化示例见
  [test_performance_storage_batches.py](../../../tests/test_performance_storage_batches.py)：
  断言实际落库值，并让后续 SQL 写入失败，验证前序写入全部回滚；不验证 facade 转发或 mock 调用。
- 平台分支测试显式声明目标平台；development updater 的测试不能依赖运行主机恰好不是 Linux。

---

## 慢测试标记

计时超过阈值的测试需打 `@pytest.mark.slow` 标记：

```python
import pytest
@pytest.mark.slow
def test_long_running():
    ...
```

日常跑时用 `-m "not slow"` 跳过。

---

## 测试文件命名

| 文件 | 对应模块 |
|------|---------|
| `tests/test_<module>.py` | `aletheia/<module>/` 的单元/集成测试 |
| `tests/specs/spec_<contract>.py` | `docs/design/<contract>_contract.md` 的 executable claims |

---

## 验证命令（提交前 checklist）

```powershell
# 1. 相关 spec claims 全绿
uv run pytest tests/specs/spec_<affected>.py -q

# 2. 架构依赖未破坏
uv run pytest tests/test_architecture_fitness.py -q

# 3. 后台跑全量（不阻塞）
Start-Job { uv run pytest tests/ -q --tb=line }

# 4. 部署验证
uv run aletheia doctor
```
