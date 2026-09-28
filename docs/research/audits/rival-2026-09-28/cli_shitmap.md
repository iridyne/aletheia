# CLI 去屎山专项扫描 · 2026-09-28

本轮完整阅读 `core/aletheia/cli/` 的 33 个 Python 文件，并沿发现项追踪入口、配置、存储和返回值消费者。结论是 **8 个行为问题（5 个建议新立卡、3 个已有卡）及 2 个低优先级整理项**。最高优先级是 `dev seed-markets` 的数据目录隔离失效：终端打印 Preview 路径，实际数据库目标仍是进程启动时选定的主数据目录，后续代码会清表。

这是冻结的审计证据，不是实施计划或运行态健康声明。源码基线为 `4a6a0f6f2304a3996a26eed0ffa38e11269eb36f`，审计开始时工作树干净。由本会话主 Agent 独立完成，未派发其他 Agent；只写本报告和目录索引，未改业务代码、创建 issue、操作 Gateway 或读取真实运行数据和凭证。server/ 与 extensions/ 的完整扫描不属于本轮；仅追踪与 CLI 发现直接相关的下游。

## 口径与发现表

实测为 **10,145 物理行、106 个 AST `ExceptHandler`、45 个宽捕获**。宽捕获指裸捕获或直接捕获 `Exception` / `BaseException`，不是缺陷数量；物理行包含空行、注释和 docstring。`ato`、`aletheia` 在 `pyproject.toml:31–32` 注册到同一个 `aletheia.main:_entry_point`，命令挂载集中在 `main.py`。原清单的“约 10 个命令组”和“108 except”不作为本轮事实。

“从未专项 review”只保留为本次启动背景，不能理解为“没有任何历史审查”。GitHub 已有多个 CLI 缺陷及修复记录，本轮对命中项做了查重。仓库根目录不存在 `.codegraph/`，因此用直接源码、AST 和调用点搜索；没有启动索引。只使用用户约定的五类标签，不为每类凑数量。D 级指建议修复的语义分类，**不因列入报告而授权实施**。

下表路径均相对 `core/aletheia/cli/`，行号绑定上述基线。P1/P2 是本报告建议的处理优先级；“已存在”指 2026-09-28 查询 GitHub 时的状态。

| # | file:line | 屎点类型 | 语义影响 | 建议动作 | D 级 |
|---|---|---|---|---|---|
| F1 · P1 | `dev_seed.py:292–303`；导入 `:22` | 契约漂移 | Preview 环境变量设置晚于 `loader.DATA_DIR` 固定；seed 首次 DB 访问仍指向原数据根，后续会清 `market_snapshots`，并清 Universe/Opportunity 表 | 建议新卡：先修数据根初始化次序并在任何写入前校验有效目标；复用现有 Preview 子进程隔离方式 | D0，恢复 Preview 隔离，Contract Delta NONE |
| F2 · P2 | `inspect_cmd.py:767–773` | 契约漂移 | 声称不修改配置的 `inspect config` 调用默认可持久化迁移的 `load_config()`，旧配置可被查询命令写回 | 建议新卡：让只读 CLI 的配置读取使用现有只读边界，包含真实入口前置调用检查 | D0，恢复只读约定，NONE |
| F3 · P2 | `inspect_cmd.py:603–606`、`:630–632`、`:1622–1624` | 契约漂移 | opportunities / strategies / agent-runs 先全局 LIMIT 再按用户条件筛选；旧匹配记录被新无关记录挤掉，假报空列表 | 建议新卡：在现有 repository 查询内先筛选再 LIMIT；三个命令一起覆盖 | D0，恢复筛选语义，NONE |
| F4 · P2 | `observe_cmd.py:706–711` | 契约漂移 | `migrate-learning-ledger` 得到 `available=False` 时，文本 exit 1，`--json` 提前 return 导致 exit 0 | 建议新卡：输出格式不改变失败退出码，复用同一结果判定 | D0，恢复文本/JSON 一致性，NONE |
| F5 · P2 | `observe_cmd.py:330–370` | 契约漂移 | `checkpoint --verify-only` 校验失败仍 exit 0；上层仅检查进程状态时识别不到失败 | 建议新卡：保留错误结果输出，失败统一非零退出，不保存失败 checkpoint | D0，恢复校验失败信号，NONE |
| F6 · P2 · 已存在 | `data_cmd.py:319–321`、`:373–386` | 契约漂移 | 下载器已经把抓取异常编码为 `DownloadResult.error`，单次下载和批量回填仅打印错误，仍 exit 0 | 复用开放卡 [#1395](https://github.com/iridyne/aletheia/issues/1395)，不重复立卡 | D0，NONE |
| F7 · P1 · 已存在 | `control_cmd.py:496–501`、`:1259–1261` | 权威路径宽异常 / 契约漂移 | runtime 读取异常正确返回 unavailable，但文本渲染把缺失 blockers 转成空列表，显示 `Resume: permitted` | 复用开放卡 [#1393](https://github.com/iridyne/aletheia/issues/1393)；以显式 authority.allowed 渲染，不能从空值推授权 | D0，NONE；不改真实恢复门禁 |
| F8 · P2 · 已存在 | `audit_cmd.py:60–75`、`:117–130` | 契约漂移 | OHLCV 覆盖只按 symbol/timeframe 汇总，跨交易所、spot/perp 数据混计，可能给无历史的目标 instrument 显示 ready | 复用开放卡 [#1686](https://github.com/iridyne/aletheia/issues/1686)，使用已有 exact-instrument coverage 读取 | D0，NONE |
| M1 · 低 | `observe_cmd.py:808–845`、`:1520–1538` | 死代码 | `_latest_lab_evaluation`、`_find_observation` 无仓内调用或命令注册；后者仍保存旧 ledger 查询逻辑 | 随后续该文件修复删除，先保留行为测试；不单独建卡 | D0，等价清理，NONE |
| M2 · 低 | `observe_cmd.py:1181–1517` | 巨型函数 >300 行 | `_collect_overview` 337 行，混合 DB 读取、系统状态聚合和健康判定；本身长度不证明错误 | 触及具体问题时按已有职责拆小纯投影；不为行数单独重构或立卡 | D0，等价整理，NONE |

**本轮没有确认需要立卡的 blob 列泄露。** CLI 明确的 `query`、记录导出、详情输出需要原始字段，不能把所有 JSON/全文输出都认定为泄露。`control_cmd._open_policy_epochs` 读取但最终丢弃 `effective_values_json` 是可删的冗余投影，尚无体积/延迟证据支持把它升级为性能缺陷。

## 关键证据与最小验收

**F1：打印的路径与真正写入的路径不同。** `dev_seed.py:22` 顶层导入 `config.loader._PROJECT_ROOT`；loader 在 `:69–70` 计算一次 `DATA_DIR/CONFIG_PATH`，`:102–103` 的 `get_data_dir()` 直接返回这个固定值。进入 `seed_markets()` 后才改环境变量，并不会重算这两个常量。`storage/sqlite.py:732–733` 用 `get_data_dir()/app.db` 定位数据库；seed 后续的 `_clear()` 在 `dev_seed.py:116–120` 执行 DELETE，`:191–192` 与 `:253` 还有 Universe 和 Opportunity 清理。已有主库且 schema 满足打开条件时，这条路径可能清除主库记录；本轮没有执行 DELETE，也没有声称生产环境已发生数据丢失。

隔离验证在导入 loader 前把 `ALETHEIA_DATA_DIR` 指向临时目录，移除 Preview 环境变量，然后调用原始 seed 函数，以替身 `get_db()` 记录有效根并立即抛出停止异常。结果：`db_target_remains_original=True`、`printed_preview_differs_from_db_root=True`、`stopped_before_db_open=True`。前一次探针断言因 macOS 临时目录 `/var` 与 `/private/var` 的同路径表示失败；改为 `.resolve()` 比较后通过，未触及数据库。修复时不能只把 `setdefault` 改为赋值，因为 loader 已完成导入。最小回归必须覆盖真实 CLI 导入顺序，证明主目录哨兵不变、所有写入指向 Preview，并拒绝越界 Preview 根。权威依据：[Preview 操作契约](../../../operations/development/DEVELOPMENT_PREVIEW.md#instance-identity-and-data-isolation)。

**F2：只读命令触发了配置写回调用。** `inspect_cmd.py` 模块 docstring 承诺所有子命令不改 DB/config；`config()` 却使用默认 `persist_migrations=True`。`config/loader.py:379–381` 删除历史 `strategy_factory`，`:436–460` 把迁移标为 changed 并调用 `save_config(cfg)`。在临时目录放置 `strategy_factory: {}`，将 save_config 替换为记录调用的 spy，执行 `inspect_app` 的 `config --json`，结果 exit 0 且 `save_config` 被调用一次。未让该调用真实落盘。最小修复可复用 `read_only_config()` 或 `persist_migrations=False`，同时检查 `main._configure_runtime` 的前置读取，避免只修展示函数而遗漏真实入口。回归以旧配置字节不变为断言，不能预先 monkeypatch 掉迁移读取后宣称只读。

**F3：已修过同类问题，但这三个兄弟命令仍遗漏。** `repositories.py:691–692`、`:1938–1939` 和 `repositories_research.py:217–221` 都走 `_select_all`，后者在 `repositories.py:362–377` 按 rowid 倒序后 LIMIT。CLI 才过滤 status/state/agent。用内存 SQLite 为每张表插入一条旧匹配、一条新不匹配记录，调用原始 repository 函数体及 CLI，`--limit 1` 在三个命令都返回空数组。这里没有改写被测 SQL，仅用 AST 从源码取出相关函数并注入内存连接。最小回归应同时验证过滤结果、条数、默认无过滤行为。闭卡 [#1514](https://github.com/iridyne/aletheia/issues/1514) 的正文只覆盖 proposals/risk/orders/fills；那些路径当前已下推过滤，不能把整张旧卡判为回归。新卡应明确剩余三个消费者。

**F4：同一 unavailable payload，格式开关改变成败。** 下游 `extensions/observation_lab/migration.py:167–179` 在缺 app DB 时合法返回 `available=False, reason=app_db_unavailable`。CLI 的 JSON 分支在检查 available 之前返回。以该返回值替换 planner，调用原始 `observe_app`：文本 exit 1，JSON exit 0。没有调用 migration writer。最小回归覆盖 unavailable 和 available 两种返回值在两种输出格式下的一致退出码。

**F5：checkpoint verdict 与进程状态不一致。** `extensions/observation_lab/checkpoint.py:543–576` 明确返回 `ok=False` 表示不可继续的校验失败；CLI 虽然只在 verdict.ok 时保存，但输出失败后没有非零退出。注入 `ok=False, reason=lab_unavailable`，`checkpoint --verify-only` 在文本/JSON 两种模式都 exit 0。此结论只指 CLI 失败信号丢失，**不声称它保存了失败 checkpoint、绕过了 runner 或改变了交易权限**。最小回归检查失败 exit 非零、save 未调用，成功路径保持可用。

**F6–F8：当前源码仍符合旧卡描述。** F6 用异步下载替身返回 `error=synthetic_fetch_failure`，单次 download 与显式单 symbol/timeframe 的 backfill 都 exit 0，错误文本仍在。F7 向原始 `control status` 渲染器注入 unavailable runtime snapshot，结果 exit 0 且输出 `Resume: permitted`；JSON 的 unavailable 没被改写，问题是文本映射。F8 经源码追踪确认 SQL、输出字段和文本聚合都没有 venue/market_type；[数据模型](../../../design/data/data_model.md) 明确 OHLCV 按完整 instrument identity 隔离。本轮 F8 是静态证据，没有运行真实覆盖查询。

**M1/M2：是维护成本，不是故障数量。** 全仓符号搜索仅命中两个 helper 的定义，无 Typer decorator、回调注册或调用点，分别约 38、19 行；外部非仓内 Python 消费方不在该结论范围。`_collect_overview` 的 337 行是从 `def` 到 `end_lineno`、含内嵌空行和注释的 AST 跨度。没有因文件 1,538 行就把其中所有函数判成巨型函数。

## 全文件覆盖记录

所有文件均阅读源码；发现项额外追踪下游和上述行为探针。“未确认新问题”表示在本次五类检查范围内未达到立卡证据门槛，不等于全功能或生产验证通过。异常数是 AST 统计，表内“宽”采用前述定义。

| 文件 | 物理行 | except / 宽 | 扫描结论 |
|---|---:|---:|---|
| `__init__.py` | 0 | 0 / 0 | 空文件 |
| `alpha.py` | 25 | 0 / 0 | 委托只读分析；有活跃注册 |
| `audit_cmd.py` | 422 | 1 / 0 | F8；audit verify 有非零失败退出 |
| `bitget_cmd.py` | 44 | 0 / 0 | 失败显式 exit 1，委托 UTA |
| `control_cmd.py` | 1414 | 28 / 21 | F7；其余宽异常保留 unavailable |
| `data_cmd.py` | 406 | 7 / 0 | F6；迁移/压缩等错误有非零退出 |
| `dev.py` | 68 | 0 / 0 | 子进程启动前传 Preview 环境 |
| `dev_seed.py` | 313 | 0 / 0 | F1 |
| `doctor_cmd.py` | 298 | 1 / 0 | 失败、修复失败和 fetch 失败统一退出判定 |
| `evidence_cmd.py` | 220 | 4 / 0 | 注册缺 required path 时两种格式均失败 |
| `exchange_cmd.py` | 120 | 0 / 0 | 配置变更与审计入口；未执行切换 |
| `flow.py` | 45 | 0 / 0 | 已注册的兼容入口，非死代码 |
| `funnel.py` | 26 | 0 / 0 | 已注册的历史查询入口 |
| `gateway.py` | 339 | 1 / 1 | 服务失败退出；防火墙审计失败显式警告 |
| `guard_cmd.py` | 67 | 0 / 0 | 委托现有 owner；apply 与 dry-run 分开 |
| `inspect_cmd.py` | 2415 | 21 / 6 | F2/F3；其余候选见排除项 |
| `install.py` | 249 | 4 / 1 | shim 验证与异常回收；部分成功语义见排除项 |
| `knowledge_cmd.py` | 172 | 1 / 0 | 委托维护/查询；未确认五类中的新增问题 |
| `learning_cmd.py` | 124 | 0 / 0 | 不可用知识资格显式输出 unavailable |
| `migration_progress.py` | 139 | 0 / 0 | 进度线程停止/等待；未确认新问题 |
| `model.py` | 70 | 0 / 0 | test/probe 无可用 provider 时失败 |
| `network_cmd.py` | 203 | 1 / 0 | 显式结果输出；退出语义候选见排除项 |
| `observe_cmd.py` | 1538 | 33 / 15 | F4/F5/M1/M2；截断候选见排除项 |
| `operator.py` | 203 | 1 / 0 | resume 委托现有门禁，拒绝时 exit 1 |
| `opportunity.py` | 230 | 0 / 0 | 按 view 验证选项，复用分析入口 |
| `permission.py` | 36 | 0 / 0 | 值域检查与审计；未执行权限变更 |
| `risk_cmd.py` | 42 | 0 / 0 | 只检查 CLI 委托，Risk 实现冻结 |
| `runtime_cmd.py` | 66 | 2 / 0 | 迁移拒绝非零；退休操作含锁和进程检查 |
| `secrets_cmd.py` | 86 | 0 / 0 | 仅阅读代码；未调用凭证存取 |
| `sizing.py` | 26 | 0 / 0 | 已注册的历史查询入口 |
| `thesis.py` | 175 | 1 / 1 | 快照读错误显式返回 error；导出为显式动作 |
| `tunnel.py` | 95 | 0 / 0 | start/setup 失败非零；未控制服务 |
| `update_cmd.py` | 469 | 0 / 0 | 区分 failed/unproven/pending_restart；未执行更新 |
| **合计** | **10145** | **106 / 45** | 33 个文件 |

## 排除项与证据边界

- `control_cmd` 与 `observe_cmd` 多个宽异常会保留 unavailable/error/unknown，满足部分诊断继续展示的需要；不能批量删除 catch。F7 的根因在下游文本映射，收窄上游异常不能修复它。
- `doctor` 的失败退出、`model test/probe`、`observe run/sync/drift`、`evidence epoch register` 已有失败信号；本轮不把已有修复再列成缺陷。`update --no-restart` 的 pending_restart 是显式设计，不能一概强制失败。
- `network test` 遇到 warn、`install` 的 update-shell 返回非零后仍可返回 0，是候选退出语义不一致；其“诊断报告/部分安装成功”的既有约定尚不足以证明违反当前契约，未计入 5 张建议新卡。SOCKS 未探测也不等于网络断开。
- `observe list` 在最多 1,000 条窗口上再做 status 筛选，是静态可见的截断风险；未完成 Lab 投影与大量窗口的针对性复现，未并入已确认的 F3 或立卡数量。
- `inspect profit-gradient-evidence` 的普通 `write_text` 值得后续结合 artifact 保存契约复核；查到的 [#1680](https://github.com/iridyne/aletheia/issues/1680) 指向旧 `profit-loop-qualification` 命令，不能仅因都写 JSON 就认定同根或已复现。未计入确认问题。
- alpha/flow/funnel/sizing 的命令别名有实际注册和消费者，不能叫死代码；是否删除整个兼容命令面不是本轮擅自决定的范围。risk CLI 仅检查入口和委托方式，没有重扫或修改冻结的 Risk 实现。

本轮运行的是局部隔离探针：临时配置、内存 SQLite、注入式下游返回值和数据库访问阻断。没有执行完整 `main._configure_runtime`，因此不把这些探针称为两个 console script 的端到端测试。两个入口等价由注册源码证明。未启动网络探测、LLM 推理、数据库迁移、seed 写入、资格窗口或共享服务；未跑全套 pytest、远程 CI 或 Fedora 验证。GitHub 只作只读查重，结果是当日快照，不保证未来 issue 状态不变。

查重检索覆盖 all-state 的 CLI、seed/preview、checkpoint/exit、migration、control permitted、coverage audit、opportunities/limit 等组合，并阅读 #1395、#1393、#1686、#1514 和 #1680 正文。未找到同项只表示这些查询未命中，不承诺全库不存在其他措辞的重复卡。

## 建议立卡清单

建议 **新立 5 张**，本报告未执行创建。先处理 F1，再处理剩余行为问题；M1/M2 不单独占卡。

| 建议标题 | 范围与完成证据 |
|---|---|
| Preview seed 必须在配置导入前固定隔离数据根 | F1；真实入口导入顺序下主根不变，任何 DELETE 前验证 Preview 根，临时目录完成回归 |
| 只读 inspect 配置查询不得持久化配置迁移 | F2；旧配置读取前后字节不变，覆盖实际入口前置调用，不改变显式迁移能力 |
| inspect 剩余三个列表命令必须在 LIMIT 前过滤 | F3；旧匹配记录不会被新无关记录挤掉，覆盖三条路径及默认顺序 |
| Lab migration 的 JSON 与文本必须保留相同失败退出码 | F4；available/unavailable × JSON/text 四个组合一致，保留结构化失败输出 |
| Lab checkpoint 校验失败必须返回非零退出码 | F5；失败有可读结果且不保存 checkpoint，成功路径不受影响 |

已有 #1395/#1393/#1686 各自承载 F6/F7/F8，不新建重复卡。本轮成功门槛是 CLI 审查报告可复核；修复、issue 创建、server/extensions 后续扫描、PR 和运行态验收均未在本报告中冒充完成。
