---
name: aletheia-silent-except-pass-sop
description: "批量治理 Aletheia 仓库里 except ...: pass 静默失败站点（盘点、判定、批量改写、重建 baseline、跑门禁）时使用"
---

## 适用场景

Aletheia 仓库要求“不能有无日志的静默失败”，门禁是 `tests/audit/test_no_silent_except_pass.py` + `tests/audit/silent_except_pass_baseline.json`（耻辱榜，条目必须带 reason）。需要批量清理或新增/删除条目时按本 SOP。

## 1. 盘点（AST，不要 grep）

站点约定：`ast.ExceptHandler.lineno`（即 `except` 行）+ 文件相对路径；条件是 `len(node.body) == 1 and isinstance(node.body[0], ast.Pass)`。扫描根 `core/aletheia` 与 `extensions`，跳过 `__pycache__`。

每处附上下文再判定：except 子句文本、所属函数、前后各 ~8 行（注释/文档字符串决定它是“文档化降级”还是“被掩盖的缺陷”）。

## 2. 判定三类

- `log_warning`：可能掩盖真实缺陷或 operator 可见损失——审计/证据写入、DB/IO 写入、risk/kill-switch/scheduler 控制、进程 kill、交易所读取失败后被回退数值顶替、通知投递。
- `log_debug`：有文档的 enrichment/显示回退/teardown/best-effort，调用方有可用回退。
- `keep`：必须写**具体** reason，不能写“not a defect”。合法理由：`signal.signal()` 在非主线程按设计抛错、被自己主动 cancel 的 `CancelledError`、幂等清空（FileNotFound/unlink）、`FileExistsError` 的 O_EXCL 竞争、`sqlite3.ProgrammingError`（已关闭）、锁竞争重试循环、`logging_config.py` 的 handler 内部（打日志会递归）。

`except (CancelledError, X)`：拆成两个 handler——CancelledError 保持静默，X 落日志。

消息格式沿用既有约定：`"<component>: <what failed>[; <fallback>]"`，英文、单行、`exc_info=True`。长消息可换行成括号形式。

## 3. 批量改写（脚本，不要手改）

按 (file, line) 从下往上替换 `pass` 行，保持缩进。要点：

- 模块已有模块级 logger 时**复用其变量名**（`logger`/`log`/`LOGGER`/`_log`），否则插入 `import logging` 与 `logger = logging.getLogger("aletheia.<dotted>")`（core 去掉 `core/`，extensions 去掉 `extensions/`）。
- `import logging` 必须排在 `from __future__` 之后（用 AST 找 top-level Import，别用 `line.strip() == 'import logging'`，否则函数内的缩进 import 会误判“已存在”）。
- Logger 定义插到 import 头部之后，前后留空行。
- 校验用 `compile(src, path, 'exec')`，`ast.parse` 不会报 `from __future__` 位置错误。
- 行内 `pass  # 注释` 的内容要吸收进 message。
- 消息里别出现 `API_KEY`/`SECRET_KEY`/`PRIVATE_KEY` 字面量（pre-commit 密钥扫描会拦，改写措辞，不要 `--no-verify`）。

## 4. 重建 baseline

按当前 AST 重新生成站点列表；保留项按**文件 + 出现顺序 + except 子句文本**与判定匹配（插入 import 会让行号漂移，不能按行号匹配）；拆分类站点会新增一条 CancelledError 静默条目；每条都要有 reason；同步更新 `count` 与测试模块 docstring 里的数字。

## 5. 验证

```sh
uv run pytest tests/audit/test_no_silent_except_pass.py -q
uv run ruff check --no-cache core/aletheia extensions
uv run python scripts/dev_check.py --no-tests --no-types
uv run python -c "import importlib;[importlib.import_module(m) for m in CHANGED_MODULES]"  # 抓 NameError/import 期错误
```

再补跑受影响模块的 focused pytest。

## 6. 提交

一次提交一个批次（文件分组），消息写清 number 变化与“哪些保留 + 为什么”。提交前 `git reset`，因为上一次失败的提交可能还压在暂存区里，会把不相关文件带进本次提交。
