# Server 与 Extensions 完整修复方案

本方案把两份扫描中的已确认问题转为实施步骤和完成标准，覆盖 server S1–S8、extensions E1–E5、positions 分支差异 H1，以及全部 M 类清理候选。问题证据分别见 [Server 扫描](server_shitmap.md) 和 [Extensions 扫描](extensions_shitmap.md)。已有 GitHub issue 仅提供历史背景；新建 issue 不是本方案的前置条件或交付目标。

**当前状态：方案已完成，业务修复尚未实施。** 方案编制基线为 `e15064b766766e88dabdf79b4e6b1e7b850bd461`，代码证据仍对应扫描基线 `ed9d8bc330cbdc29eb02006337e24424141fbd29`；两者之间仅有审计文档提交。本次授权是完善解决 Plan。后续实施仍由主 Agent 承担；实施顺序是技术依赖，不是逐阶段人工审批流程。

## 目标、范围与实施顺序

目标是让鉴权、验证、观察任务和导出准确表达成功/失败，让列表返回明确投影，并消除已经证明无消费者的代码。已确认缺陷按 D0 修复，Contract Delta NONE。依据当前 [Inference Harness Contract](../../../design/contracts/llm_inference_harness_contract.md)、[Settings Control Contract](../../../design/contracts/settings_control_contract.md)、[Persistence Recovery Contract](../../../design/contracts/persistence_recovery_contract.md) 及扫描中已追踪的正确兄弟实现。保持 Risk 评估规则、交易主线、实验阈值、历史数据和凭证权限不变。没有确认的怀疑项不作为既成缺陷实施。

| 建议顺序 | 修复工作 | 对应发现 | 依赖与交付 |
|---|---|---|---|
| 开工前 | 核对实施分支与历史修复 | H1 及所有发现 | 对目标 SHA 重读命中代码；已有修复通过验收后直接记为已满足，不重复移植 |
| 优先 | WebSocket 鉴权与广播生命周期 | S4、S5 | 先拒绝未知鉴权，再修发送/清理；两项可以独立提交 |
| 优先 | 导出完整性与隔离 | E4、E5 | 共享 exporter；先调用独立目录，再保证失败不发布，作为一个连贯提交 |
| 接续 | 模型验证真值 | S1、E1 | 先绑定被测身份，再统一设置探测完成判定；覆盖 Ping/Profile/模型路由 |
| 接续 | 观察任务失败传播 | E2 | 修改已有 pass result；CLI、scheduler 消费验证同时完成 |
| 接续 | 首页 Risk 投影 | S2 | 只修 server 展示投影，必要时同步其前端状态文案 |
| 接续 | 校准 GET 只读 | E3 | 复用已有缓存读取和 scheduler builder，取消 GET 持久化回退 |
| 接续 | 列表投影和详情 | S6、H1 | 先确定详情读取面，再收窄列表及生成类型 |
| 接续 | Prediction 过滤和计数 | S7、S8 | repository 查询修复先于 route；兄弟入口一起验收 |
| 收尾 | 启动诊断一致性与无调用代码清理 | S3、全部 M | 启动目录独立修复；仅删除已证明无消费者部分 |

每个独立根因完成实现、局部验证和自审后提交，不把互不相关的修复攒成大 diff。出现一条路径的待决事项时继续其他独立路径。上述顺序不要求一次性改完才能交付。

## WebSocket：未知鉴权拒绝，慢客户端有界清理

**目标与改动位置。** `core/aletheia/server/routes/ws.py` 的 `_ws_remote_auth_required` 不得在配置失败时返回“无需鉴权”。保留“确定无需鉴权”和“确定需要 ticket”两个正常分支，配置读取异常由路由明确拒绝（沿用握手拒绝路径），不得 accept 或启动采样。日志只含安全原因/异常类型。正常 ticket 的单次消费和过期行为不改。

广播保留现有最多 50 个客户端、共享采样器和发送顺序控制，不新增消息队列系统。使用标准 asyncio 超时和 gather，使一个广播对客户端快照并发发送；单客户端发送 deadline 初始取 1 秒，作为模块常量，不新增配置界面。共享 send_lock 防止两个广播同时写同一 socket；它只被一个有界发送批次占用。失败连接先从活跃集摘除，在锁外做有界 close/清理；close 也使用有限 deadline。

统一 `remove`、广播失败和 sampler_failed 的生命周期清理。必须处理“广播由 sampler 自身调用”的情况：清理最后一个客户端时，不能 cancel 后 await 当前 task，也不能持有 lifecycle_lock 等待需要该锁才能退出的兄弟 task。锁内只更新集合/任务引用，锁外取消并等待非当前任务；当前 sampler 在本次清理后正常结束。Cancellation 保持传播，不能被当作普通发送失败吞掉。

**验收。** 扩展 `tests/test_ws_realtime.py`：配置读取抛错且无 ticket 时不 accept；无需远程鉴权正常接入；有效/无效/过期 ticket 行为不变。一个永不完成发送的替身与一个正常客户端同时在线时，正常客户端收到本轮消息，超时客户端被摘除，下一轮告警仍推进；最后客户端被清理后两个 sampler 终止，无自等待、悬挂任务或重复 close。用事件控制时序，不用任意 sleep 猜竞态。完成标准是有限 deadline 与生命周期行为成立，不是只在源码中出现 wait_for。

## 导出：一个调用一个身份，失败不发布产物

**目标与改动位置。** `extensions/sync/exporter.py` 的 export_bundle 在调用入口生成一次 UUID，供 staging、默认 archive 名、manifest export_id 共用；时间戳只用于可读性。`_write_manifest` 接收该身份，不再自行用秒时间生成。config_status 使用本次调用的局部结果，避免同一 ExportService 实例被并发复用时串值。参考已有 BackupService 的唯一命名惯例，不加全局锁。

对 mode 明确列出的表按必需表处理。`_export_db_tables` 的 execute、首行读取、后续迭代和写文件失败均向上传播；带安全表名/阶段说明，保留原异常链，不把缺表/锁失败解释为空表。可读空表写空 JSONL 文件，纳入 checksum，使必需表集合可逐项核对。当前计划不引入“可选表”兜底；若当前契约确有可选表，只按已声明集合处理，不从异常倒推可选性。

所有文件生成完毕后，先在最终目标同目录写唯一临时 archive，关闭成功后原子替换目标。失败只删除本调用的 staging/临时文件，不删除此前存在的 output。默认 output 因 UUID 独立；显式指定同一 output 的覆盖约定保持原样，但只能看到完整旧归档或完整新归档，不能看到半成品。复查 `server/routes/persistence.py` 和 CLI 导出入口：异常返回失败，不能返回 status ok 或成功退出；不泄露路径中的凭证信息。

**验收。** 复用 `tests/test_io_honesty_fixes.py`、`tests/test_persistence_route_concurrency.py`、`tests/specs/spec_persistence.py`：在临时根和 SQLite fixture 上分别让查询首行、迭代中段、artifact copy、归档写入失败，断言无新成功产物、旧显式目标字节不变、API/CLI 不成功。固定同一秒，同一实例连续及并发导出，断言 staging、默认文件与 manifest 身份独立、内容/checksum 互不混用。空表成功且有对应空文件；config-only 等不含 DB 的 mode 不被新增 DB 要求阻断。保留 secrets redaction、symlink 和 import 预检回归。

## 模型验证：结果属于实际被测对象

**身份修复（S1）。** 改 `server/routes/model.py` 和 `model_provider/router.py` 的探测获取边界：从实际 provider 的构造快照取得身份，并把 provider 与身份一起交给 Ping。不能只把现在的 load_config 提前一行：router 缓存按名字索引，配置 B 和仍缓存的 provider A 仍可能错配。

沿已有 provider factory/identity reader 处理 HTTP 配置；在同一配置与凭证快照内完成 provider 构造及身份计算，缓存命中时返回该实例原有的构造身份。若现有 factory 分别读取凭证导致无法保证一致，则在现有 HTTP 构造函数内一次解析供构造/摘要共用，而非在 route 复制凭证逻辑。Core 只接收摘要；配置锁不跨 await/网络，不把凭证放入响应、日志或缓存键。实例无可信身份时不得持久化验证结果，不能用当前配置身份补填。

成功、空响应、异常三个出口统一携带开始时取得的身份。`persist_provider_verification` 继续在当前配置下比较身份；配置变更时不写入新配置，结果明确区分“旧探测结果”和“本次是否持久化”。旧请求仍可正常完成，不靠取消请求修复身份归属。涉及缓存 clone/model selection 时保留精确 model 的身份，不把 preferred model 验证复用于另一个 model。

**完成判定修复（E1）。** 在已有 ModelResponse 所属模块提供一个小的纯判定函数，供 Ping 与 candidate probe 复用：非空白文本或 completion_confirmed=True 即成功。candidate 在记录 inference success 和 verified=True 之前判定；失败复用既有错误响应/验证失败记录，Verify & Save 不提交配置、secret 或路由分配。保留候选 identity 在 await 前冻结以及保存时 CAS。startup/structured 消费策略继续独立，不做全局“空文本失败”改造。

**验收。** 扩展 `tests/test_model_profiles.py`、`tests/test_model_provider.py` 与 `tests/test_llm_inference_harness.py` 的现有替身：A 开始后保存 B、构造前缓存 A 而配置已是 B、等待期间撤销 provider、请求失败、空响应，均不更新 B 验证；B 自己的成功探测可持久化。空白且无 completion 拒绝，reasoning-only 有 completion 接受，普通文本接受。Profile 创建/更新、精确模型路由分配的失败都保持原配置和临时 secret 不落入正式状态。所有网络、keyring 和配置目录隔离；检查响应和日志不出现测试 secret。完成标准是构造身份与证据一致，不能仅断言 CAS 被调用。

## 观察任务：每个失败阶段进入最终结果

改 `extensions/analytics/filter_controller.py:run_controller_observe`，在已有 summary 中记录阶段错误，覆盖 replay 构建、注册和整个 runner 调用；保留真实完成数量。推荐持久化或整个 runner 失败为 failed；已执行部分工作但注册失败/单窗口失败为 partial；只有全部要求阶段完成（包括合法零工作）才为 success。每个错误包含稳定阶段名和安全原因，warnings 不再是唯一出口。

注册失败后仍允许现有独立窗口执行，最终保留 partial；不为修复汇总而改实验状态、阈值或 checkpoint 权威。重复尝试复用当前幂等注册机制。`cli/observe_cmd.py` 和 scheduler 已消费 pass_status，优先保留这套接口，仅补齐确有遗漏的消费者。

**验收。** 复用 `tests/filter_eval/test_filter_controller_eval.py` 和 `tests/test_observe_cli.py`，分别注入构建、注册、runner 异常，证明 summary 非 success、文本/JSON CLI 同为非零、scheduler 记录失败。合法空轮为 success；已有推荐持久化失败、window error、UFC mismatch 仍失败/部分失败。Worker lock 拒绝保持已有专用退出语义。不得用默认空字典覆盖异常结果，也不得回写失败为可信 checkpoint。

## 首页 Risk：收敛两个状态投影

在 server 层提取一个纯配置到状态的共享函数，供 `routes/risk.py` 与 `routes/ui.py` 使用；函数只接受所需 config/brake 字段，不读历史表、不调用 Risk 评估器。默认 not_evaluated；现有 kill/demo/live brake 和 live warning 的优先级保持不变。删除首页“存在历史记录即 healthy”的分支；最新 RiskDecision 只保留为独立历史信息。

若 `Overview.tsx` 仍把 not_evaluated 与空 violations 渲染为 all-clear，改为明确“当前未评估”及中性色；未知/读取失败显示不可用，真正 blocked/warning 保留。此改动仅是相同状态语义的前后端闭环，不扩大为 RiskControls 或风控算法重写。

**验收。** `tests/test_ui_bootstrap.py` 加配置与历史记录矩阵：无历史/旧通过/旧拒绝/preflight/legacy 均不产生当前 healthy，两个 API 对同配置的共有状态一致，刹车优先级不变。相应 Overview 组件用已有前端测试设施验证 unknown/not_evaluated 不显示绿色健康；历史决定仍可读取。冻结 `core/aletheia/risk/` 不修改。

## 校准 GET：只读缓存，刷新保留原职责

`extensions/analytics/routes.py:get_confidence_calibration` 只读取当前可用持久化 report。缓存缺失、过期或语义不合格时返回已有 no_data 形状，附明确 report 不可用原因；GET 不调用 build_calibration_report，也不触发新后台任务。保留 execution_mode 与 cohort 元数据；模式未解析仍返回 unavailable。

`confidence_calibration.build_calibration_report` 继续供现有 scheduler 的显式计算/持久化入口使用，本修复无需拆出通用计算框架或新增 refresh API。检查该 GET 的所有间接读取，使用已有只读配置/数据库入口，确保迁移或缓存读取不能偷偷写回。若未来要求点击刷新持久化证据，那是另一个明确产品动作，不伪装在 GET 中。

**验收。** `tests/test_confidence_calibration.py` 及 route 测试准备足量合格样本但无当前 report，连续/并发 GET 后 DB 与 artifact/config 字节不变；返回 no_data 而非伪造新报告。缓存命中仍返回原报告；显式调用 scheduler 所用 builder 仍按原规则持久化。此前 GET 即时生成报告的 UI 预期同步改成等待计划刷新。

## 列表投影：轻量摘要、准确 schema、可追溯详情

**前置核对。** H1 的历史 #3631 已关闭；实施前对选定目标分支检查 PositionSummaryView、路由输出和回归。已有正确实现直接复用，当前审计分支缺修复不代表主线回归。源码核对不改变 checkout 或自动合并任何分支。

**Planning History。** `server/routes/universe.py` 定义专用列表摘要，与 CandidatePlanningFields 明细解耦；排除 planner_input_json、requalification_source_json、result_json、validated_hypothesis_json 等逐次完整材料。先列出 `Universe.tsx` 表格及其他实际消费者所需字段，保留 id、instrument、状态、时间、关联 plan/order、cursor 与追踪摘要。repository 为列表使用显式列投影，避免 SELECT pg.* 把重字段从 DB 搬到 Python 后才丢掉；内部 generation 详情读取继续完整。

route 使用真实 response_model 约束，不能只声明 responses 文档。详情必须按 generation_id 精确定位旧 generation，不能用“当前 candidate”替代历史实体；先复用已有精确读取面，如目标分支确实没有 HTTP 详情，则补一个只读 generation detail 入口并复用 repository.get_candidate_planning_generation，同样鉴权/404。该接口只暴露已获授权的现有证据，不新增写入权。positions 采用已有 #3631 的摘要范式，保留现有单仓详情，列表 status 默认值与 LIMIT 语义不改。

同步导出 OpenAPI 并生成前端类型，调整真正依赖被移除字段的详情消费者；不手改生成类型、不让缺失字段被误填为 null。仓库原有列表分页/排序规则原样保留。

**验收。** 扩展 `tests/test_universe_routes.py`、`tests/test_positions_route_pagination.py`、`tests/test_openapi_export.py`：固定多条记录及大 blob，列表不含这些字段，增大 blob 长度不增大摘要体积；详情返回原始材料；同时间戳翻页无重复/遗漏，空页、边界 limit、非法 cursor、详情 404 正确。前端类型检查和相关构建通过，历史/当前详情身份一致。体积比较使用同一合成数据集，不沿用旧运行态的 MB 数字作验收。

## Prediction：先过滤，再截页；总数独立聚合

在 `storage/repositories.py:list_opportunities_by_asset_class` 增加 keyword-only status 条件，进入 SQL WHERE 后再排序/LIMIT。新增窄的 prediction count 聚合（或复用已有同语义聚合），按 asset_class 精确统计 total/open，不复用最新 200 条页面。原无过滤调用保持行为；route 去掉 Python status 过滤。

在 `storage/repositories_market_data.py:list_prediction_events` 增加 search；保留已有 category/status 条件，将 title/category 的搜索条件以括号 OR 组合并参数化后置于 LIMIT 前。保持原大小写不敏感的字面子串语义：不得让 `%`、`_` 变成用户未请求的 SQL 通配符，也不得把 Python lower 的 Unicode 行为悄悄换成 SQLite 的 ASCII-only lower。当前支持域若含 Unicode，用连接内确定性 casefold/lower 比较函数或等价表达实现，并保持只在本查询使用；不扩展通用查询框架。

**验收。** 使用现有 `tests/test_prediction_history.py` 及 repository/route fixture：旧匹配项前有 N 条新不匹配项，limit=1 仍得到匹配项；总数超过 200 仍正确；category/status/search 组合、引号、百分号、下划线、大小写与非 ASCII 文本遵循原匹配语义，查询参数不可注入。无 search/status 的旧调用和分页顺序不变。

## 启动目录与清理候选

**启动目录（S3）。** `server/startup_policy.py` 的 scheduler_initialization 改成与现行 lifespan 一致的 fail_startup/interface_not_ready；`health.py` 继续读这一个目录。更新 `tests/test_startup_policy.py`，同时在现有 lifespan 测试用替身注入 init_scheduler 异常，验证 job manager/PID 清理、RuntimeError 和未到 ready。background services 禁用模式仍跳过 scheduler。只改诊断声明，不把启动失败改成宽松继续。

**无消费者代码（server M1、extensions M1/M2）。** 对 record_reload、detect_policy_conflicts、filters_by_stage、render_feedback_for_pi、known_bitget_profiles 重查导出、动态注册、测试、脚本和操作文档；证明无入口后删除函数/纯死模块及仅服务于它的 import。留下实际消费者的部分，不能用名称相似替代调用链证明。不删除历史数据库或历史 fixture 所需生成器。

**长函数（server M2、extensions M3）。** 明确处理决定为“本轮不以行数为由重构”。修 S3 或其他具体问题时只抽取必要职责；其余长函数保留，有已验证的错误或重复职责时再动。该决定属于计划处置完成，不称为函数已经重构。knowledge 的历史操作入口和其余未证实怀疑项同样保留，记录所缺证据，不做猜测式删除。

## 验证、提交与完成标准

上述用例是后续实施时应执行的验收，**本次仅编制方案，未新增或运行这些测试**。后续先用最小隔离 fixture 证明原行为失败，再修复并验证同一条件通过；优先扩展列出的现有测试文件，具体 selector 以实际 collection 为准，不把未收集到测试算通过。生产 config、凭证、网络、交易和共享数据库均不用于回归。

每个实施提交记录 D0、变化边界、Contract Delta NONE，附确切测试命令和结果；以当前 `dev_check.py` 做首轮反馈，再运行与实际改动相符的 focused regression 和 PR 级本地 gate。涉及前端生成类型/状态显示时执行对应前端检查。远程 CI 已退役，不把等待远程 CI 写为完成步骤；全套测试只有明确需要时按当前隔离流程安排。

| 完成层次 | 必须有的证据 |
|---|---|
| 方案完成（当前交付） | 全部 S/E/H/M 均有具体处理、依赖和验收；审计证据可追溯；文档检查通过并提交 |
| 某项代码修复完成 | 目标 SHA 上缺陷复核、局部代码 diff、能失败的回归及修后结果、自审、独立提交 SHA |
| 本轮实现完成 | 全部确认缺陷已修或在目标分支已有实现且验收通过；候选清理有删除/保留理由；无模糊的“以后再查”替代确认缺陷 |
| 已合并/已部署 | 分别提供实际合并状态、部署精确 SHA 和 Fedora 运行证据；不能由本地通过推断 |

失败时优先修复当前局部提交；必要回退只撤销该提交，不还原整个工作树、不覆盖其他人的改动。导出变更不重写既有归档，查询/投影变更不改历史行。若部署后发现问题，先遵循既有部署回退授权和流程；本计划不自动授权部署、重启、Live 或重置观察窗口。鉴权修复不能以“回退方便”为由重新允许未知鉴权连接。
