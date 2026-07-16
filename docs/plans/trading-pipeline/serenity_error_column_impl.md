# 实施计划：给 serenity_research_runs 加 error 列（owner 已批准，探查完成待落地）

> 2026-06-30。建议 B 的实施。探查已完成，因上下文耗尽未落代码。环境工具(grep/Grep)持续损坏输出，
> 用「独立脚本落文件读回」最稳（见 [[aletheia-env-flaky-uv-run-transient]]）。

## 已查明的事实（落点 + 模板）

- migration 机制：`aletheia/storage/migrations.py` 的 `MIGRATIONS = [...]` 列表（文件 1847 行）。
- **加列已有先例**（直接照抄）：`"ALTER TABLE serenity_research_runs ADD COLUMN theme_summaries TEXT"`。
- `CREATE TABLE IF NOT EXISTS serenity_research_runs ( id TEXT PRIMARY KEY, trigger_type TEXT, ... )` 也在同文件。
- 失败路径：`research_worker.py:222-236` 的 `_worker` except 块：
  ```python
  except Exception as e:
      logger.error("research run %s failed: %s", run_id, e)
      run = repo.get_serenity_research_run(run_id)
      if run:
          run.status = "failed"
          run.completed_at = _now_iso()
          repo.update_serenity_research_run(run)
  ```
- `update_serenity_research_run` 现写：status, started_at, completed_at, summary, opportunities_found,
  themes_researched, theme_summaries（**不含 error**）。
- model `SerenityResearchRun` dataclass **已有 `error` 字段**（只是 DB/repo 没接）。

## 待办（按序）

1. **migrations.py**：
   - `MIGRATIONS` 列表末尾追加一条 migration：`"ALTER TABLE serenity_research_runs ADD COLUMN error TEXT"`。
   - `CREATE TABLE ... serenity_research_runs` 定义里加 `error TEXT`（新库直接有列，幂等）。
2. **repo `update_serenity_research_run`**：SET 加 `error=?`，参数元组加 `run.error`。
3. **research_worker.py except 块**：`run.status="failed"` 后加 `run.error = str(e)[:1000]`（在 update 前）。
4. **probes.py `_check_serenity_pipeline`** failed 分支（可选增强）：把 `row['error']` 拼进 message，
   让 doctor 直接显示失败原因。注意 SELECT 已是 `*` 还是显式列——当前是显式列，需把 error 加进 SELECT。
   （查：probes.py 该方法的 SELECT 现为 `status, created_at, started_at, completed_at`，要加 `error`。）
5. **docs/design/data/data_model.md**：serenity_research_runs 加 error 字段（SDD 规则：改表同步契约）。
6. **验证**：`uv run pytest tests/test_doctor_probes.py -q`（落文件读回）；restart + doctor。
7. **清理**：`_mig_probe.py` / `_mig_out.txt`（若残留）。

## 待查的次要矛盾（记一笔，非阻塞）
3 条 failed run 的 `completed_at=None`，但上述 except 块会设 completed_at。说明它们可能**不是**被这个
except 标记的，而是被别处（疑似 startup 清理 orphan queued→failed）。grep 其他 `status="failed"` 设置点确认。
不影响本次加列（任何失败路径都该能写 error）。

## 边界
- migration + repo + worker(agents/，非 trust boundary) + 探针 + 文档。**不碰** risk/execution/trust。
- 未 commit。连同既有 34 个未推送 commit 待 owner 处置。
