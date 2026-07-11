# AI Handoff

> **本文件是 append-only 变更史**(记录"做了什么改动 + 根因",机器无关)。
> 其中任何"当时机器状态"(gateway 在跑 / 观察期第 N 小时 / 凭证已存 / demo 单已落)
> 都是**写入时刻的快照,不代表当前**。要知道当前真实状态,运行 **`aletheia doctor`**。
> 双机意图/事实分离的金标准见 `docs/_ai/DEV_PROTOCOL.md`。

## Last Updated

2026-07-11 (Cross-platform service runtime honesty)

---

## Recent Change (Cross-platform service runtime honesty — 2026-07-11)

macOS 的 launchd 不会继承 fnm 交互终端的临时 PATH，导致主网关虽然在线，Node
operator shell 却持续启动失败。现在 Node 启动保留 Windows/PATH 发现，并依次支持
显式覆盖、fnm default、Volta、asdf、mise、Homebrew 和 `/usr/local` 稳定路径；不把
一次终端会话的 multishell 路径写死。

doctor 新增独立 `operator.shell` 状态，不再用四小时一次的 Python operator assessment
冒充 Node 命令层健康。网关重启历史也区分“当前仍失败”和“历史失败后已经恢复”：审计
证据仍保留，高频重启仍告警，但当前 gateway/backend 健康时不再被旧 spawn failure
持续判为不健康。

没有修改 operator 权限、命令内容、交易、风险、执行、live 或凭证。跨平台原则与验证
清单见 `docs/_ai/CROSS_PLATFORM_SERVICE_ENVIRONMENT.md`。

---

## Recent Change (Market-context instrument identity — 2026-07-11)

市场观察器构造永续上下文时，原先先把标的改成交易所格式、再作为普通字符串交给行情提供器。字符串没有市场类型，提供器会把它恢复为现货，导致永续品种请求不存在的现货 K 线；缓存也只按币种和周期，现货与永续可能互用。

现在上下文从调用方接收场所，并把 `venue + canonical symbol + market type` 作为完整身份传入提供器和缓存。降级到最近快照时也指定同一市场类型。没有改筛选阈值、风险、执行、仓位、实盘或凭证。

验证：市场上下文 23 项、相关数据/Universe 46 项、完整规则 633 项通过（7 skipped，6 xfailed）。

---

## Recent Change (Filter settlement cadence diagnostic honesty — 2026-07-11)

doctor 原先只要 filter outcome 到了 `due_at` 就立即计入 overdue，超过 50 条便告警；但 outcome settler 每 15 分钟运行，所以正常等待下一轮的样本会让健康状态周期性变黄。现在统一采用 20 分钟宽限：宽限内单列 `awaiting_cadence`，超过宽限才进入 `stale_overdue` 和告警阈值。

没有修改结算频率、取价、结果计算、过滤、实验、风险、执行或 live。doctor 回归 46 项、完整规则 634 项通过；单进程连续全量 `2809 passed, 13 skipped, 6 xfailed`。

---

## Recent Change (Prospective filter shadow experiments — 2026-07-11)

历史回放达到门槛后不再停在一句“建议设计实验”，也不会直接放宽生产过滤。控制器会登记一个固定版本、固定人群、固定对照值/候选值和固定停止门的前瞻 shadow 实验；只使用登记时刻之后的新首次候选，历史选参样本不能再次充当验证样本。

对照组和 shadow 增量组都要各自达到至少 20 个完整净结果和 70% 覆盖。达到绝对与相对 5bp 门槛只会进入人工复核；不支持则结束，生产 filter 版本变化则自动作废。实验表通过 CHECK 约束固定 `production_mutation_allowed=0`，整个模块不写配置、不改变放行结果。

同一个固定版本、阶段、家族、方向、市场和周期由数据库唯一索引限制为一个预登记 treatment；后续历史回放波动不会再为同一未来样本并行追逐其他阈值。

没有修改 TTL、ranker、方向刹车、Kelly/sizing、风险、执行、live 或凭证。

验证：聚焦过滤回归 48 项、架构检查 11 项、完整规则 633 项通过；重放到最新主线后单进程连续全量 `2806 passed, 13 skipped, 6 xfailed`。

---

## Recent Change (Serenity worker failure classification — 2026-07-11)

Serenity 的后台研究线程原先在最外层捕获异常后，一律把失败写成 `unknown_error`。因此即使通用 LLM 调用已经识别空消息 timeout，线程边界仍会丢掉类型。现在正常管线和线程兜底都使用同一“异常类名 + 消息”分类；空消息 `TimeoutError` 会留下 `provider_timeout` 和可读原因。

没有改研究 prompt、provider 选择、重试节奏、评分、交易、风险、执行、live 或凭证。Serenity 工作台 61 项、相关 LLM/健康/调度 75 项、完整规则 631 项通过。

---

## Recent Change (OHLCV issue-ledger correction — 2026-07-11)

市场数据问题台账中的 MDS-05 曾保留旧结论，称不同交易所的同一根 K 线会互相覆盖。当前代码早已使用“交易所 + 标的 + 现货/合约 + 周期 + 时间”作为唯一身份，旧库升级也会重建为同一结构；来源无法可靠判断的旧行只放入 `legacy_unknown`，不假装属于真实交易所。

这次只纠正台账，不改采集、行情、评分、交易、风险、执行、live 或凭证。聚焦 OHLCV 身份回归 3 项通过。

---

## Recent Change (SQLite capacity diagnostic honesty — 2026-07-11)

doctor 旧逻辑只要 app.db 超过 500MB 就 warn，并推荐一个不存在的维护脚本。现在同时读取文件大小、SQLite freelist 和磁盘余量：低磁盘才 warn；大而紧凑的有效历史库只作 info；可回收页同时超过 100MB 和 20% 才提示安排离线 compact。

没有删除数据，没有自动执行 VACUUM，也没有修改 retention 或 SQLite 写入语义。

验证：doctor 回归 44 项、完整规则 632 项通过；单进程连续全量 `2799 passed, 13 skipped, 6 xfailed`。

---

## Recent Change (Optional feed diagnostic honesty — 2026-07-11)

Coinalyze 开关默认开启但凭证是可选的，旧 doctor 只看开关，导致未配置时同时产生 OI 和清算两个 warn。现在 doctor 复用 collector 的真实 secret→environment 凭证存在性判断，但只接收布尔值：disabled/unconfigured 都是 info；已配置却没有 OI 或 OI 过期才 warn；清算是事件流，没有事件始终显示 idle/info。

没有启用数据源、没有输出密钥，也没有修改采集、评分、交易、风险、执行或 live。

验证：聚焦 collector/doctor 回归 70 项、完整规则 632 项通过；单进程连续全量 `2796 passed, 13 skipped, 6 xfailed`。

---

## Recent Change (LLM timeout error observability — 2026-07-11)

通用 LLM helper 以前只记录 `str(exception)`；provider 抛空消息 `TimeoutError` 时，日志和研究 run 只剩 `llm_unavailable:`，Serenity 最终误报 `unknown_error`。现在 complete/structured/stream 共用异常格式器，消息为空也保留类名，下游可准确归为 `provider_timeout`。

methodology 研究仍使用原 ICT/SMC 专用 prompt 和单 lens 路径，只把单次输出预算从 16384 对齐到常规 lens 已验证的 8192，降低撞 60 秒 provider 边界的概率。没有修改 provider 选择、fallback 顺序、研究评分、晋升、交易、风险、执行、live 或凭证。

验证：聚焦 LLM/health/Serenity 回归 88 项、完整规则 632 项通过；单进程连续全量 `2794 passed, 13 skipped, 6 xfailed`。

---

## Recent Change (Operator scheduler continuity — 2026-07-11)

System Operator 原本只有每天 6 个 `:15` CST 内存 cron，网关错过窗口后不会补评估。现在保留原时刻，并在启动 2 分钟后触发同一个 job；scheduler wrapper 先读取持久 `operator_assessment`，最近结果不足 3 小时 45 分钟就跳过，既防止反复重启重复动作，也允许 agents 串行队列约 15 分钟的正常延迟。

无历史、坏时间或查询失败时允许评估继续，因为 Operator 原有 permission level、禁止动作、提案门和每日动作限频仍是最终边界。wrapper 对空 summary 改为安全 no-op。没有修改任何评估规则、参数动作、权限等级、风险或执行语义。

验证：聚焦 Operator/scheduler/doctor 回归 120 项、完整规则 631 项通过；单进程连续全量 `2792 passed, 13 skipped, 6 xfailed`。

---

## Recent Change (Serenity daily scheduler continuity — 2026-07-11)

Serenity 原本只随 `dreaming_daily` 在每天 02:00 CST 入队，网关错过该时刻就要再等一天。现在保留原 cron，同时在网关启动 4 分钟后做一次补跑判断：按上海时区计算最近应执行批次，只有该批次没有成功结果、没有活动任务且没有已尝试记录时才创建；`serenity:scheduled:YYYY-MM-DD` 持久键防止反复重启重复研究。

研究 worker 是进程内 daemon thread，无法跨网关重启存活。启动时会把前一进程留下的全部 queued/running/waiting_retry 行标为 `gateway_restart_interrupted` 失败并记录事件，不再让幽灵 running 永久堵塞。doctor 改用最近成功完成时间判断新鲜度；近期失败只作为 latest attempt 展示，不再伪装成功，修复提示也改为真实的 `dreaming_daily` 与 `serenity_startup_catchup`。

没有修改研究评分、自动晋升阈值、PI 决策、filter、风险、执行、live、凭证或每日 lesson extraction 行为。

验证：聚焦 scheduler/Serenity/doctor 回归 129 项、完整规则 630 项通过；单进程连续全量 `2784 passed, 13 skipped, 6 xfailed`。

---

## Recent Change (Deterministic application shutdown — 2026-07-11)

网关关闭现在先阻止调度器继续生产任务，再取消并等待 LLM 启动检查、交易所启动检查和账户启动同步，随后停止它们可能派生出的 LLM/交易所自动恢复循环，最后回收 Telegram 与 PI daemon。整段清理位于 `finally`，正常停止、重启和异常离开走同一路径。

账户启动同步不再占用事件循环默认线程池，而使用可独立释放的执行器；即使经纪商同步卡住，也不会让事件循环关闭无限等待。真实 FastAPI 接口测试使用明确的 api-only runtime，不启动调度器、账户同步或外部启动探针。

没有修改后台任务的启动条件、频率、broker 请求语义、交易、风险、执行、live 或凭证。聚焦生命周期/连接/runtime 回归 42 项、完整规则 629 项通过；单进程连续全量 `2775 passed, 13 skipped, 6 xfailed`，原先在 doctor TestClient 退出处的 TST-A01 已关闭。

---

## Recent Change (Execution/data exchange divergence guard — 2026-07-11)

外部 demo/live 的真实执行场所现在按 `paper_execution_mode` 解析，不再拿可能过期的 `exchange.name` 代替。统一 alignment 同时供设置后端、状态 API、doctor、Settings 和 Overview 使用；本地模拟明确为 local_paper，不产生分叉告警。

修改主行情源或 Paper execution mode 只要会新产生 `X execution / Y data` 分叉，后端先返回 409，前端必须勾选同时写明两个场所的确认框才能保存。Market Data 更新先全部标准化/校验，再确认、应用、保存；400/409 不会留下半修改缓存。现有有意分叉不阻塞无关字段编辑。首页常驻显示 `X exec · Y data`，连接探针改测真实执行场所。

没有自动改配置，没有修改凭证、live 解锁、风险、broker 或订单路由。

验证：聚焦配置/状态/doctor 回归 119 项、完整规则 629 项和前端 production build 通过。单进程全量在既有 `test_infra_doctor_endpoint_matches_contract` 的 TestClient 退出泄漏处等待；中断前 1199 项通过，该节点隔离 1 项通过，节点后分段 1570 项通过、1 项 Settings 原生 checkbox 规范失败已修并回归 75 项通过。合计覆盖 2771 项，0 个剩余真实失败，但不是一次连续全量绿；遗留记为 TST-A01。

---

## Recent Change (Market-data observability and OHLCV provenance — 2026-07-11)

采集任务最近成功不再被当成“库里一定有可用数据”。新增只读覆盖报告，分别显示盘口、主场所 funding/OI、LSR、清算、宏观、链上和 OHLCV 的保留行数、标的数、来源数、最新时间与新鲜状态；首页 System 同时显示 Collectors 和 Data coverage。清算是事件流，平静期显示 idle，不伪报采集故障。

OHLCV API 保持原数组响应，响应头明确区分内存缓存、外部抓取、数据库降级和不可用。外部失败时只读取 exact venue+symbol+spot/perp+timeframe+before 的本地历史，SQLite 读取走线程池。CLI microstructure 修正 imbalance 字段，并按 exact venue 显示 funding/OI 来源、时间和 freshness。

没有修改采集频率、provider、行情值、scanner、filter、风险、仓位、执行、live 或凭证。

验证：真实 567MB 数据库冷覆盖报告约 150ms、30 秒缓存热读低于 1ms；聚焦回归 78 项、完整规则 628 项、全量 2763 项与前端 production build 通过，0 项真实失败。

---

## Recent Change (Enriched decision-context freshness — 2026-07-11)

增强行情不再因为“仍是数据库最新一行”就无限期参与决策。盘口、主场所 funding/OI/LSR、宏观、链上、跨场所 funding 和永续持仓结构统一使用 fail-closed 时效策略；缺失、坏时间、过期或异常未来时间都会让整条快照退出决策，不再出现主字段已失效但年化值、变化率或 LSR 仍残留的半失效状态。

Market Observer 复用通用数据库上下文构建器，Universe 的数据库 fallback 使用同一策略。已知交易场所时衍生品按 exact venue 查询；Coinalyze 聚合 OI 保留给显式 research/doctor 消费，但从默认主场所读取中排除。通用 DB 路径补回 LSR，真实 render 可见且进入 crowding。

没有修改行情值、scanner 权重、filter 阈值、风险、仓位、执行、live 或凭证。

验证：聚焦回归 73 项、完整规则 627 项、全量 2757 项通过，0 项真实失败。

---

## Recent Change (First-exposure filter feedback and bounded replay — 2026-07-11)

过滤证据现在按 filter+stage+version+outcome candidate 的首次持久化决定唯一归组。同一机会先新鲜放行、后续重试时过期拒绝，后者仍保留用于排障，但不会再把同一个净结果同时放进 pass/reject。报告时间窗在全保留历史完成首次暴露排序后再截断，并显示排除的重试行和发生决定迁移的候选数。

新增通用的固定版本单调阈值回放框架，首个 adapter 是 family TTL。每个回放精确锁定 stage/version/family/direction/market/horizon，最多 4 个候选阈值、每组 5000 候选、每报告 8 个嫌疑；显示新增放行、净结果覆盖、原始/收缩净期望和下游未重放限制。只有至少 20 个完整结果、70% 覆盖、收缩净收益绝对值和相对改善都达到 5bp，才可标记为“可设计独立 shadow 实验”；永不写配置。

没有修改 TTL、ranker、sizing、Kelly、风险、执行或订单。

验证：聚焦机会/过滤/回放测试 82 项、完整规则 626 项、全量 2751 项通过，0 项真实失败。

---

## Recent Change (Opportunity diagnostic evidence honesty — 2026-07-11)

机会审计不再把重建上限冒充窗口总样本：overview 分开报告窗口数据库总数、实际重建数和过滤后显示数。controller view 现在真正使用 `--hours`，按过滤决策发生时间截断近期证据；scheduler 的长期 shadow 观察仍使用全历史。

sizing trace 的历史计划仓位改用提案自身 quantity×price，当前配置反算值单独标为 current-policy projection；实际成交优先按该提案 fills 汇总，只有没有 fill 的旧链路才回退 position。这样后续仓位变化不会倒灌到某一笔历史提案。没有修改 sizing 计算、Kelly、过滤参数、风险或订单。

验证：机会/仓位/过滤证据聚焦回归 100 项通过，完整规则 625 项、全量 2746 项通过，0 项真实失败。

---

## Recent Change (Market-data collector coverage visibility — 2026-07-11)

`data_collectors` 仍严格受 40 秒总预算和 45 秒任务上限保护，但每个 domain 现在会区分成功、部分结果、空结果、失败、超时、因总预算跳过、未配置和已禁用。SQLite 按 domain 只保留一行最新状态和累计计数，不生成无限历史；连续三次失败/超时/预算跳过才产生去重基础设施提醒。

`GET /api/market/collector-health`、doctor `data.collectors` 和 Overview System / Collectors 读取同一状态。交易暂停不再误停 layered-pause 中的公共数据采集；没有 observed instruments 时，依赖标的的盘口/衍生品明确标为未配置，CoinGecko、宏观和 DefiLlama 继续运行。没有增加预算或并发，也没有修改行情值、筛选、风险、执行、live 或凭证。

验证：真实数据库在线副本迁移后完整性为 ok；聚焦回归 101 项、完整规则 625 项、全量 2741 项通过，0 项真实失败；前端 production build 通过。

---

## Recent Change (macOS launchd gateway watchdog — 2026-07-11)

macOS 不再把 autostart 报为不支持。项目会安装用户级 `com.aletheia.trading-os.gateway` LaunchAgent：登录运行并每 60 秒执行同一个幂等 `gateway start --no-browser`。它只恢复现有 supervisor，不分别托管 frontdoor/backend，不使用会造成短命令重启风暴的 KeepAlive。

plist 使用绝对 venv Python 和项目目录，原子写入、无环境变量/凭证；enable/disable 通过 launchctl bootstrap/kickstart/bootout，status 实时区分未安装、文件存在但未加载、已加载未成功运行、路径漂移和未知状态。Windows Task Scheduler 行为保持不变。Settings、CLI 和 doctor 使用统一跨平台语义。没有修改 live、风险、执行、交易暂停或凭证。

验证：生成 plist 通过 macOS `plutil`，绝对 Python/工作目录存在且 CLI 可导入；跨平台 autostart/gateway 聚焦回归 112 项、完整规则 623 项、全量 2731 项通过，0 项真实失败；前端 production build 通过。

---

## Recent Change (Active-Universe OHLCV catch-up — 2026-07-11)

动态 Universe 新成员不再只能等待夜间历史回填。独立 scheduler 任务按交易所、symbol、spot/perp 和周期检查精确 1h/4h 覆盖，每轮只处理配置上限内的缺口。partial/failed/ready、连续失败次数和下次重试时间保存在 SQLite；失败按 15 分钟起步指数退避，三次后进入基础设施告警。Universe 刷新本身不做网络下载，夜间回填继续作为批量兜底。

补齐范围包含暂时不可执行的活动成员，因为数据覆盖不能依赖交易资格，否则 doctor 能看到但规划器永远跳过。任务不写 `execution_eligible`，不会因此授予交易权限。

Settings 暴露开关、间隔和单轮上限；doctor 显示活动缺口对应的 catch-up 状态。没有修改选币分数、过滤器、风险、仓位、执行、live 或凭证。

验证：真实库副本迁移完整性为 ok，关键行情/交易因果表行数前后一致；专项回归 18 项、完整规则 617 项、与最新 master 合并后的全量 2719 项通过，0 项真实失败；前端 production build 通过。

---

## Recent Change (Orderbook full instrument identity — 2026-07-11)

盘口现在从采集到消费都携带交易所、symbol 和 spot/perp。旧的 384 行没有可靠类型，统一标为 `unknown`；新采集写真实类型。Market observer 和 Universe 只读目标市场的盘口，inspect/doctor 显示完整身份。真实库副本迁移 384/384 行全部保留。

没有修改盘口计算公式、筛选阈值、风险或执行。

验证：真实库副本迁移 384/384 行保留；相关测试 156 项、完整规则 615 项、全量 2692 项通过，0 项真实失败。

---

## Recent Change (New-listing OHLCV bootstrap — 2026-07-11)

本地完全没有历史的新币，如果从一个月前起查为空，现在会按 500/100/24/6 根窗口逐步靠近上市时间；找到数据后先向左补、再向右追到当前。修复 CAP 永续 4h 有数据但 1h 错误停在 0 的通用根因，不写标的特例。

验证：相关测试 25 项、全量 2689 项通过，0 项真实失败。

---

## Recent Change (OHLCV full instrument identity — 2026-07-11)

历史 K 线不再只按 symbol 存储：主键和所有活动池回填/诊断都使用交易所、symbol、现货/永续、周期和时间。永续现在可以落库；旧数据里被二次写成 `ccxt` 而无法恢复真实交易所的行标为 `legacy_unknown`，不会冒充当前覆盖。真实库副本迁移演练 `89,806/89,806` 行全部保留。

没有修改行情数值、选币阈值、风险或执行。

验证：真实库副本迁移无丢行；相关测试 163 项、完整规则 614 项、全量 2688 项通过，0 项真实失败。

---

## Recent Change (Trading memo startup catch-up — 2026-07-11)

交易备忘录仍按每天北京时间 02:30 正常合成，但 scheduler 启动 3 分钟后会补跑一次；已有备忘录不足 6 小时时沿用原有限流直接跳过。这样服务错过夜间窗口后能自行恢复，不会等到第二天。模块中未实现的“新增 3 个案例事件触发”说明已删除。没有修改备忘录内容规则、排序加减分、风险或执行。

验证：学习/调度与架构聚焦测试 51 项、完整规则 613 项、全量 2684 项通过，0 项真实失败。

---

## Recent Change (Data-model claim ID uniqueness — 2026-07-11)

Local paper account 的结构检查从重复的 `DM-S08` 改为未占用的 `DM-S23`；原 `DM-S08` 继续唯一指向交易提案哈希链。只修复规则索引，不改运行时代码或数据。

验证：数据模型检查 24 项、规则索引结构 6 项、全量 2683 项通过；14 项跳过、6 项预期失败、0 项真实失败。

---

## Recent Change (Reconciliation execution-account scope — 2026-07-11)

重复开仓的根因不是方向刹车：paper_internal 本地仓被拿去和 unified 外部账户空仓视图比较，几分钟后被 `reconciled_gone` 关闭，AI 又因无持仓重新进入。Reconciler 现在按 active execution_mode 隔离本地仓与快照仓；本地模拟固定 local_only，外部账户只能处理本账户的本地仓和带同一 broker_id 的快照仓。缺少账户标签时安全降级，Demo 聚合快照不解释 Live 仓位。

这是一条通用账户边界，不针对 VIRTUAL 写特例。没有修改 broker 下单、风险、平仓价格、live 或凭证。

验证：对账聚焦测试 17 项、持仓接管规则 24 项、全部规则 612 项、执行/风控/仓位组合回归 87 项通过。全量 2682 项通过；唯一失败是 master 已有的 `DM-S08` 规则编号重复，需在独立提交中更名。

---

## Recent Change (Local No-Exchange paper account — 2026-07-11)

Local `paper_internal` now owns an append-only simulated-cash ledger. First use
records 200 USDT once; a deliberate operator balance of zero stays zero. Available
cash is cash less margin reserved by open local positions, and risk tightens the
existing local per-order ceiling by that available amount. A local close records its
realized PnL once without rewriting trade history.

Settings exposes human-only cash credit/debit/set actions and close-all / close-spot
actions only while Paper (internal) is active. Each is audit logged. The System
Operator receives the local balance and recent human account changes as read-only
context. External demo/live accounts, credentials, exchange execution, and live
unlock behavior were not changed.

Validation: focused account/risk/execution/settings tests, full specs, architecture
fitness, and frontend production build passed.

---

## Recent Change (Restored knowledge path relocation — 2026-07-11)

从旧机器恢复的知识记录仍保存 Windows 绝对路径，正文文件虽然已经位于本机 data 目录，FTS 重建却把它们当缺失。检索层现在只按 `data/` 后缀安全重定位到当前 data 根目录，并拒绝任何越界路径；doctor 的重建命令提示也已改为真实存在的 `knowledge reindex`。

---

## Recent Change (Orderbook parser recovery — 2026-07-11)

订单簿采集长期 0 行的直接原因是 CCXT/OKX 每档返回价格、数量、订单数三列，而解析器只接受两列，导致每个标的都失败。现在只读取前两列，并对永续合约按 contractSize 换算真实美元深度；没有改变交易决策或执行边界。

仍有一个已登记后续项：orderbook 存储缺少 market_type。当前 Universe 的同 symbol 市场去重只能降低混用概率，不能替代完整现货/永续身份；需要独立迁移和逐消费者改造。

---

## Recent Change (Filter feedback stage identity — 2026-07-11)

负反馈证据现在把“发生在哪一道关卡”作为身份的一部分。同一候选即使在两个阶段经过同名、同参数、同结果的检查，也会保留两条独立事实；控制器按真实阶段分组，并展示当时实际参数，不再用注册表的概括标签覆盖历史事实。

这修复了两道方向刹车在报告中都显示成 operator skip 的问题，也防止未来参数相同后证据因幂等键碰撞丢失。没有合并或放宽方向刹车，没有修改风险、执行、仓位、live 或凭证边界。

---

## Recent Change (Instrument-aware market collector routing — 2026-07-11)

主交易所行情任务不再把永续成员当现货字符串。Price feed、orderbook 和 volume ranking 现在携带完整的交易所与市场类型；批量成交量扫描会先确认每个精确市场，单个不支持标的不再拖垮整批。结构化 ranking 回到 Universe 时也保留原 market type，不再把字典误当 symbol。

OKX category 3 股票/ETF 合约在资产分类、成本和执行能力正式接入前继续保持观察态，不进入 crypto 可执行池。本批没有新增交易权限，也没有修改风控、仓位、订单路径或 live 开关。

---

## Recent Change (Opportunity submission evidence honesty — 2026-07-11)

System Operator 的 `trade/scale_in/flip` 现在明确表示“模型想交易”，不再被误解为“提案已经创建”。如果提案前的方向集中度检查或目标市场取价拒绝了这次尝试，系统会保存稳定原因；无价格路径不再静默返回空值。

机会漏斗直接读取已经落库的 ranker 和 proposal submission 证据，展示历史候选数、进入选择阶段的数量和已知前置拒绝原因。审计不会重新跑当前 ranker，也没有改变方向刹车参数、风控、仓位、执行或 live 权限。

---

## Recent Change (Opportunity filter evidence foundation — 2026-07-10)

机会链上的每道主要筛选现在会在判断当时留下事实，不再从最终状态反推。ticker 是否存在、下载前成交额、稳定币/黑名单/成交额/点差/新鲜度等高频无方向检查按扫描批次完整保存；Observer、Ranker、冷却/持仓/批次上限、System Operator、Universe 配额和 Risk 保存逐候选轨迹。

只有在事前已有 long/short、目标交易所/市场和有效入场价时，才创建 15m/1h/4h/24h 净结果；无方向候选只报覆盖，不事后选赚钱方向。新 shadow/control 复用同一份 exact-instrument 成本后结果，旧 gross-only 行不进入 alpha 学习。filter controller 只比较同版本、同策略族/方向/市场/周期的 pass/reject，未达样本与覆盖门时保持“证据不足”，不写配置。

Top-N 选择改为先排除已持仓/冷却，再应用批次上限，低排名候选可补位。`XAUT/PAXG` 不再被误当美元稳定币，但仍必须通过普通交易所、流动性、风控和执行检查。分析证据按 30/90 天有界保留；交易、权威风控和审计原件不清理。

仍保持不动：方向刹车阈值、ranker 权重/经济门、生产校准、Kelly/sizing、live、凭证和订单路径。

验证：完整设计+机会决策链组合 `1165 passed, 9 skipped, 6 xfailed`；最终全量测试为 `2649 passed, 14 skipped, 6 xfailed`，仅 3 个已在干净主分支独立确认的旧失败（macOS/Windows autostart 路径断言 + 2 个 knowledge maintenance 索引测试）。

---

## Recent Change (Opportunity cost-aware fixed-horizon outcomes — 2026-07-10)

机会被选中后的结果不再用“稍后查一次当前价”代替固定持有期表现。新结算会锁定原交易所、现货/合约、做多/做空和 15m/1h/4h/24h 目标时点，优先用目标时点 K 线，拒绝用过晚的当前价伪造可学习结果。

结果分别保存原始价格涨跌、按方向换算的收益、手续费、价差/滑点、资金费、净收益和期间最好/最差波动。只有净收益超过最低可交易优势才算胜出；旧数据只保留价格变动的历史解释价值，不补写虚构成本，也不进入新校准。现货与永续合约的后备行情不再互相代用。

分配、风控读取、诊断、规则候选、机会审计和 Universe 页面已统一到这个净收益口径，并按策略族+方向+市场类型分组。逐筛选器对照已在随后的 Stage E 实施；自动放宽/收紧仍关闭。

---

## Recent Change (Opportunity profitability review and repair — 2026-07-10)

从顶层盈利目标到 `Opportunity -> filter -> System Operator -> proposal -> risk -> execution -> outcome -> feedback` 完成一轮当前源码和历史数据核实。权威入口：

- `docs/research/OPPORTUNITY_LIFECYCLE_PROFITABILITY_REVIEW_2026-07-10.md`
- `docs/plans/opportunity_lifecycle_profitability_repair_plan_2026-07-10.md`

本轮已修：OKX 永续成交额为空导致合约全部在预筛消失；`short_bias` 资金费率方向写反；现货/永续在分配后才去重导致占坑；PI `bear_case` 只有字段未接 schema/parser；提案按 symbol-only 取价；pending shadow 伪装 resolved。Opportunity 漏斗现在把事件吞吐和同批机会转化分开，转化率只用 Opportunity ID 因果 cohort；动态金额风控理由会聚类。doctor 新增 spot/perp feature 覆盖，并明确 `loop.profit=ok` 只代表提案流水，不代表盈利。

未直接改：方向刹车、ranker 权重、family calibration、Kelly/sizing、净收益结算、多资产执行。这些会改变选择或仓位，先按计划补 market-aware 净收益和 filter 决策/对照记录。

验证：聚焦测试、相关 Universe/决策/漏斗/doctor 回归、完整 specs 和 architecture fitness 均通过；最终全量为 2588 passed（排除 3 个已在未修改 master 独立复现的既有失败），细节见同日 DEVLOG。

---

## Recent Change (Local-paper exchange status honesty — 2026-07-10)

The dashboard previously treated the configured exchange preference (for example,
OKX demo) as evidence that an external account should be connected. In local paper
mode this caused a failing connection probe and a red exchange warning even though
no exchange was meant to be used. Restored exchange-account cache data could also
appear as the local paper account's balance.

The status API now states whether the current execution path actually uses an
external exchange. Local paper displays "no exchange", does not probe an external
connection, and excludes external account caches from its account summary. External
demo/live paths retain their existing connection status and cached-balance behavior.
Gateway status and Telegram `/status` use the same distinction.

---

## Recent Change (Risk status history separation — 2026-07-10)

The dashboard had treated the newest rejected RiskDecision as a currently active
violation indefinitely. A resolved $0-cap rejection therefore kept the operator UI in
warning state even after the effective cap returned to $200.

The risk-status endpoint now reports only live configuration and kill-switch
conditions as active violations. The newest immutable decision remains attached as
history, and the Overview labels a rejected latest check with its timestamp instead
of calling it current risk. No decision records are deleted or altered.

---

## Recent Change (Paper simulation $0-cap repair — 2026-07-10)

The local paper simulator was rejecting normal proposals even with the configured
$200 cap because its drawdown multiplier read an unrelated exchange-account history.
That history included an old high-water mark and a current small balance, producing
a false large drawdown and an effective $0 cap.

paper_internal now skips drawdown scaling until the system has a genuine simulated
equity ledger. It still uses the configured position cap, daily-loss guard, open
position limit, and all other risk checks. Exchange-backed demo/live modes retain
their real account-equity drawdown protection. Focused risk tests and the full
executable contract suite passed.

---

## Recent Change (Opportunity lifecycle integrity repair — 2026-07-10)

修复机会从生成、筛选到收益复盘这条链里几处会把“猜测”写成“事实”的问题。

- 现货与永续合约虽然共用 `BTC/USDT` 这样的内部符号，但资格检查现在必须匹配同一种市场的快照；
  现货新报价不能再让永续合约被标成可执行。
- `opportunity trace` 只把数据库里确实留下的决策算作拒绝。没有 ranker 记录时，
  `execution_eligible=False` 等字段只显示为“推断的资格状态”，不再冒充 ranker 已拒绝。
- debate 只有确实把原本可交易的信心降到门槛以下时，才记为 `debate_downgraded`；普通 skip
  仍保持为 operator skip。
- forward outcome 现在按机会所属的 universe snapshot 绑定，避免重复使用 member 时把后来收益
  套到较早机会身上。
- filter feedback controller 不再把某个 scanner family 的整体表现误说成某个具体 filter 应该放松或
  收紧。在没有每个 filter 自己的放行/拒绝记录前，报告诚实显示证据不足。
- `doctor` 会把 demo/paper 单笔额度为 `0` 的状态报为 blocker，因为这会让每一笔 proposal 都被风险门拒绝；
  不自动改额度。

验证：机会审计、universe、doctor、filter 相关测试及全部 specs 均已通过；前端 production build 通过。

---

## Recent Change (Available-balance position cap — 2026-07-10)

Risk check #5 now treats the existing position-notional values as hard ceilings and
uses the lower of that ceiling and the matching exchange account's cached available
USDT. Demo and live Bitget accounts are read separately; other exchange-backed modes
use their own unified-account entry. paper_internal has no external balance, so it
continues to use the configured ceiling. The risk engine never makes an exchange call
while evaluating a proposal, and a missing cached balance retains the configured cap.

The default demo ceiling remains $200. Live remains separately capped and still needs
the existing human live-unlock path plus every other risk check. Focused risk tests,
the full executable contract suite, and the frontend production build passed.

---

## Recent Change (Market-type data-model sub-batch — B12, 2026-07-09)

最后一块成批的遗留 bug（同一个币在现货和合约上被搞混）已修完。这批之前被推迟，理由是
"要先给行情快照表加一列再动"——但核查发现那一列**本来就有**，所以根本不用改表结构，
真正要做的只是把"这是现货还是合约"这个信息一路传下去。

- **#13**（风控用错快照）：合约决策不再拿同名现货的快照来判断数据新鲜度/入场价。查快照时
  优先按市场类型精确匹配，找不到再退回按符号查（旧库里合约快照被存成了现货，退回保证不会
  凭空多拒单）。触风控信任边界，配 PL-MD-06/07 两条回归。
- **#14**：行情采集不再把合约标的当现货抓，改成读标的/持仓自己的市场类型。
- **#9**：候选生成把市场类型带上，决策层不再拿到 None。
- **#102**：Serenity 促成的待决信号从源机会里补回市场类型（不用给 pi_decisions 加列）。
- **#10 判为"不修"**：设计上（spec OD-S01 / 提交 `db3c3f8`）方向分析与合约/现货无关，
  流水线本就每个符号只出一个标的，按符号去重是**符合设计**的；加市场类型去重反而会把当初
  故意去掉的"同币重复观察"放回来。已在代码里加注释说明。
- **#11/#68/#106 确认早已修好**（`034d4c8` 的渐进匹配平仓）。

提交：`df2fa5c`→`02421c0`（5 个：repo 过滤参数 / market_data / risk+specs / 候选+决策链 /
风控网关测试桩）。验证：spec 全套 + risk + ranker + runtime → 633 passed / 0 failed。
未 push、未 restart gateway（按交接约定，由 owner 触发）。台账权威记录见
`FULL_BUG_REVIEW_2026-07-06.md` 的 B12 行。

---

## Recent Handoff (Full Bug Review — fix phase, 2026-07-07)

Full-project bug/SDD review is **complete** (`#1`–`#180`, coverage marked "full-project pass complete"). The **fix phase is underway** on branch `fix/bug-review-fixes` (off `iridite/bug_fix` HEAD, pushed to origin).

- Detailed finding ledger + authoritative Fix Progress table: `docs/_ai/FULL_BUG_REVIEW_2026-07-06.md`
- Short takeover guide: `docs/_ai/BUG_REVIEW_HANDOFF_2026-07-06.md`
- Findings recorded: `#1` through `#180` (9 HIGH / 99 MED / 72 LOW; + 9 refuted, do not re-report).
- **Fixed so far: 42 findings across 11 commits** (batches B1–B6 + B10a). All 9 HIGH findings addressed.
- Verification: full SDD spec suite **588 passed / 0 failed** + 15 new focused regression test files. Trust-boundary changes (`risk`/`execution`/secrets/live/gateway lifecycle) each carry a focused regression per AGENTS.md.
- Remaining (batched, ledger Fix Progress is source of truth): **B7** portfolio/PnL/reconciliation/broker/persistence · **B8** frontend honesty (needs `npm run build`) · **B9** data/analytics/doctor/cli · **B10** doc-drift tail. Plus ~15 deferred coupled/infra items (market-type data-model migration, operator auth #40/#41, #109/#143/#102-pending/#17, runtime-perf #90/#147/#134) — each recorded with its reason in the ledger.
- Merge note: `origin/master` has advanced 5 commits since this branch forked (concurrent session; incl. `c945110f "Scope auto guards to their source symbol"` which may overlap B5 guard changes) — reconcile at PR time, not on this branch.

Owner preference for future records: use short, structured Markdown rather than long prose. Suggested fields: domain, impact, document expectation, code evidence, reproduction/verification, expected fix direction.

Next AI should read the ledger's Fix Progress table first, then continue the next batch instead of re-checking already-fixed or already-recorded findings.

---

## Recent Change (Vergex 永续结构信号 observation-only 最小接入 — 2026-07-07)

完成 Vergex/Hyperliquid 永续持仓结构信号的最小安全接入。目标是让系统能消费聚合后的
结构证据，不导入地址明细、他人 prompt、账户/资产/credential，也不让该信号直接影响下单。

- 新增 `perp_structure_snapshots` 表，存 POC、Value Area、上下方清算燃料、集中度、
  data_quality、as_of_time 等聚合字段。
- 新增 `PerpStructureSnapshot` 模型、repository 写入/最新读取/30 天清理函数。
- 新增 `VergexStructureCollector` fixture 写入入口：只接受白名单聚合字段；拒收/丢弃
  钱包地址、prompt、账户、credential 形态字段；当前不调用真实 Vergex API。
- `market_observer` 只读取 60 分钟内的新鲜结构快照，拼进 `Perp structure` 证据块；
  该块只进 prompt，不改 proposal 阈值、不改排序、不碰风控/执行。
- `doctor` 新增 `data.perp_structure` 新鲜度探针；无数据/旧数据只做可观测提示。
- `docs/design/data_utilization.md` 和 `docs/design/data_model.md` 已登记该 observation-only 数据域。

边界：不改 live/risk/execution/credentials，不接真实 Vergex endpoint，不建地址库，不接 marketplace
prompt，不复刻 Vergex composite score，不让结构信号单独触发开仓。

验证：
- `uv run pytest tests/test_data_collectors.py tests/test_doctor.py tests/test_market_context.py tests/specs/spec_data_utilization.py tests/specs/spec_data_model.py -q`
- `git diff --check`
- `uv run aletheia doctor`（只读诊断；运行态结果以执行机器为准，本文不记录为共享事实）

后续如果要从 fixture 进入真实 partner feed，先补配置 + Settings 控件 + collector fixture 测试，
继续保持默认关闭和 observation-only。

---

## Recent Change (Opportunity CLI consolidation — 2026-07-05)

机会/假说排查入口已收口到 `opportunity`，避免 `flow`、`alpha`、`funnel`、`sizing`、
`thesis audit` 平级抢入口。

新的 owner 日常入口：

```powershell
uv run aletheia opportunity audit --hours 24
uv run aletheia opportunity trace --id <opportunity_id>
uv run aletheia opportunity audit --view funnel
uv run aletheia opportunity audit --view sizing
uv run aletheia opportunity audit --view alpha
uv run aletheia opportunity audit --view thesis
```

语义边界：
- `inspect opportunities`：只列出机会原始记录。
- `opportunity audit`：分析机会整体生命周期。
- `opportunity trace`：追踪单个机会的一生。

兼容策略：
- `flow` 是隐藏兼容别名，继续可执行但提示改用 `opportunity`。
- `alpha`、`funnel`、`sizing` 顶层命令从根帮助菜单隐藏，human 输出提示改用 `opportunity --view ...`。
- `thesis audit` 隐藏为兼容入口；`thesis snapshot/snapshots/records` 保留用于观察留档和明细导出。

边界：只改 CLI 入口和只读分析路由；不改 risk、execution、scheduler、agent 决策、DB schema、
配置或 live 行为。

验证：
- `uv run pytest tests/test_cli.py::test_help -q`
- `uv run pytest tests/test_opportunity_flow_ledger.py::test_opportunity_cli_outputs_audit_and_trace tests/test_opportunity_flow_ledger.py::test_legacy_flow_cli_remains_hidden_compatibility_alias tests/test_opportunity_flow_ledger.py::test_opportunity_cli_outputs_specialized_views -q`

---

## Recent Change (Executable Candidate Supply Floor V1 — 2026-07-04)

新增候选供给枯竭诊断，用来在 System Operator 连续面对贫瘠可执行候选池时提示刷新机会池，而不是继续反复 skip。

- `trading_context` 在构建 `executable_candidates` / `excluded_candidates_summary` 后写入
  `candidate_supply_snapshot` operator event，并把 `candidate_supply` 摘要放入 layer2。
- 连续 2 个 snapshot 的 `executable_count < 3` 时，`/api/operator/recommendations` 输出
  `candidate_supply_drought`。
- recommendation metadata 包含 `main_reason`（例如 `ttl_expired`）、`executable_count`、
  `floor`、`consecutive_cycles`、`task_type=run_market_scan`。
- `flow audit` 的 integrated signals 增加 candidate supply 摘要，方便 OFL 也能看到当前供给是否枯竭。
- 暂停状态下只给 recommendation，不带可执行 `taskType`。

边界：不改 trading threshold、不改 debate、不改 guard、不改 risk、不改 execution、
不调用 LLM、不创建 proposal、不下单；只是候选供给 telemetry + recommendation。

验证：
- `uv run pytest tests/test_candidate_supply_floor.py tests/test_operator_events.py tests/test_anti_hallucination_e2e.py tests/test_agent_shell.py -q`
- `uv run pytest tests/specs/spec_decision_lifecycle.py tests/specs/spec_data_model.py tests/test_architecture_fitness.py -q`

---

## Recent Change (Opportunity Flow CLI polish — 2026-07-04)

`aletheia flow audit` 增加三个只读过滤参数，方便直接看可疑机会：

```powershell
uv run aletheia flow audit --hours 24 --stage debate_downgraded
uv run aletheia flow audit --hours 24 --verdict missed_alpha_suspect
uv run aletheia flow audit --hours 24 --symbol TRX/USDT
```

Flow human output 新增 `Debate signal` 区块，直接展示 debated decisions、debated skips、
trade-like decisions、trace_complete / trace_missing、missed_alpha_suspect 和下一步需要的证据。

`inspect debate-impact` 和 `inspect guard-impact` 的人工输出末尾增加软提示，指向
`uv run aletheia flow audit --hours <n>`；JSON 输出保持不变。

边界：只读 CLI/read-model 输出增强；不改 debate 公式、ranker、guard、risk、execution，
不新增 LLM 调用，不建表。

验证：
- `uv run pytest tests/test_opportunity_flow_ledger.py tests/test_debate_impact_audit.py tests/test_guard_impact_audit.py tests/specs/spec_analytics_contracts.py tests/test_architecture_fitness.py -q`
- `uv run aletheia flow audit --hours 24 --limit 5 --stage debate_downgraded --verdict missed_alpha_suspect --symbol BTC/USDT`

---

## Recent Change (Opportunity Flow Ledger V1 — 2026-07-04)

新增统一的只读机会流账本入口：

```powershell
uv run aletheia flow audit --hours 24
uv run aletheia flow trace --opportunity-id <id>
```

目标：把一个 opportunity 从出现到最终状态的生命周期串起来，回答“为什么这个机会没有变成交易”。

- 每条记录输出 root opportunity、`final_state`、`final_stage`、`final_reason`、timeline、
  shadow/forward counterfactual outcome。
- 覆盖成功和失败路径：created、ranker_excluded、guard_blocked、venue_unsupported、
  ttl_expired、signal_pending、operator_skip、debate_downgraded、proposal_created、
  risk_rejected、order_executed、position_opened、position_closed、shadow_resolved、
  forward_outcome_resolved。
- 复用已有只读审计摘要：Trade Funnel、Debate Impact、Thesis Lineage、Sizing Trace。
- Guard Impact V1 保持专项入口；flow 只在已有持久化 reasoning/skip reason 明确写出
  guard_blocked 时标记该 stage，不重新跑 guard 规则。
- 旧 audit 命令暂不硬路由，兼容计划见 `docs/_ai/OPPORTUNITY_FLOW_LEDGER_V1.md`。

边界：不调用 LLM，不跑 ranker，不重跑 guard/debate/risk/execution，不建新表，不改交易行为。
旧数据缺字段时保持 `insufficient_data`，不事后补写理由。

验证：
- `uv run pytest tests/test_opportunity_flow_ledger.py tests/test_trade_funnel_audit.py tests/test_debate_impact_audit.py tests/test_sizing_trace_audit.py tests/test_thesis_lineage.py -q`
- `uv run pytest tests/specs/spec_analytics_contracts.py tests/test_architecture_fitness.py -q`
- `uv run aletheia flow audit --hours 24 --limit 5`
- `uv run aletheia flow trace --opportunity-id missing-flow-id`

---

## Recent Change (Guard Impact Audit — 2026-07-04)

新增一个窄范围、只读的 guard impact 诊断入口，用来判断 active blocking guards 是否正在压制
候选流：

```powershell
uv run aletheia inspect guard-impact --hours 24
uv run aletheia inspect guard-impact --hours 24 --json
```

- 读取 active blocking guard markdown/frontmatter、最近窗口内本地可评估的 open opportunities、
  existing shadow / forward outcomes。
- 输出 active blocking guards 总数、按 scanner_family / direction / symbol 的聚合、
  被 guard 命中的候选、每个候选命中的 guard、guard 作用范围分类、已有 outcome 和 verdict。
- verdict 只来自已有 outcome：`protective`、`overbroad_suspect`、`insufficient_data`。
- 本地已知的 pre-guard skip（如 TTL 过期、execution_eligible=False）不计入 guard impact；
  demo unsupported symbol 不在这里过滤，因为该诊断禁止调用 exchange/HTTP。

边界：不禁用/修改 guard，不改 ranker，不改 System Operator，不动 risk/execution/live，
不调用 LLM/HTTP/exchange，不写 DB，不修改 guard 文件。

验证：
- `uv run pytest tests/test_guard_impact_audit.py tests/test_paper_trader_guards.py tests/test_paper_trader_ranker.py tests/test_trade_funnel_audit.py -q`
- `uv run aletheia inspect guard-impact --hours 24`
- `uv run aletheia inspect guard-impact --hours 24 --json`
## Recent Change (Thesis Lineage 链路完整性修复 — 2026-07-04)

完成一轮 Thesis Lineage V1.1 数据链路完整性修复（commit `7df8889f`）。
目标不是生成新 thesis，而是让已有判断留下更完整的父子关系，并让审计统计区分真实断链和
正常的全局判断。

- `trading_context` 现在把 ranker candidates 和 open positions 的 causal IDs 带入上下文：
  `opportunity_id`、`strategy_id`、`proposal_id`、`trade_school` 等。
- `_attach_opportunity_context()` 增加两类确定性回填：
  ranker candidate → opportunity；close/reduce decision → open position。方向冲突或多持仓歧义时不猜。
- System Operator 已生成的 `bear_case`、`key_assumptions`、supporting/opposing signals 会从
  `commands.evaluate_trading_opportunity()` 传到 persistence path。
- proposal rationale 的 `decision_meta` 同时写新键 `supporting_signals` / `opposing_signals`
  和旧键 `supporting` / `opposing`，历史/前端/反馈读取保持兼容。
- Thesis Lineage read model 可以只读地通过 `proposal_id -> trade_proposals.opportunity_id`
  还原一部分历史决策链，并用 `decision_opportunity_id_inferred_from="proposal_id"` 标明是推断。
- 审计口径拆分：`global_no_target_count`、`link_required_missing_opportunity_id`、
  `proposal_backfillable_missing_opportunity_id`、`stale_missing_pnl_pct`、`overdue_pending`、
  mature-only shadow/forward missing。不要再把所有 `opportunity_id=NULL` 都当作断链。
- 修复计划文档：`docs/plans/thesis_lineage_integrity_repair_plan.md`。

后续看效果时，优先看修复后新写入数据：
`signal_pending` 应继续保持 opportunity_id 缺失为 0；`link_required_missing_opportunity_id`
才是真正需要盯的 PI 决策断链；`global_no_target_count` 是全局 skip/watch，不应作为断链修。

验证：
- `uv run pytest tests/test_thesis_lineage.py tests/test_anti_hallucination_e2e.py tests/test_proposal_rationale.py tests/specs/spec_data_model.py::test_pi_decision_carries_opportunity_id tests/specs/spec_decision_lifecycle.py tests/specs/spec_serenity_attribution.py -q`
- `uv run pytest tests/test_architecture_fitness.py tests/specs/ -q`
- `uv run pytest tests/test_doctor_trading_diagnostics.py tests/test_doctor.py::test_deep_diagnostics_includes_log_policy_redacted_tails_and_attention -q`
- `.\scripts\test_ai.ps1`
- `cd frontend && npm run build`

---

## Recent Change (Thesis Lineage — 2026-07-04)

新增一层只读 thesis lineage：不替代交易因果链，而是在现有事实链上显示“这条交易假说怎么参与、
最后实际结果和反事实结果怎么对比”。

- 后端新增 `aletheia/analytics/thesis_lineage.py`，从 opportunities / serenity_opportunities /
  pi_decisions / trade_proposals / risk_decisions / orders / fills / positions / shadow_trades /
  universe_members / universe_forward_outcomes 只读重建记录。
- CLI 新增 `aletheia thesis audit`、`aletheia thesis records` 和只读落盘用的
  `aletheia thesis snapshot`。
- API 新增 `GET /api/analytics/thesis-lineage`，支持按 position/order/proposal/opportunity 过滤。
- 前端 Trades & Positions 的 Order / Position detail drawer 在现有 Causal Chain 下方新增
  Thesis Lineage 区块，显示 verdict、actual/shadow/batch 对比、观察假说、决策信号、assumptions、
  bear case 和 parse warning。
- API reference 已登记新 endpoint。

边界：不新建表、不改交易行为、不调 prompt/ranker/sizing/risk/execution/scheduler/live、
不调用交易所。LLM 边界：生成 thesis 的上游可以用 LLM 或规则/模型；Thesis Lineage
读模型/审计层不新增额外 LLM 调用，只消费 market_observer / System Operator / Serenity
已经生成并落库的 thesis / reasoning / evidence。缺字段就标 missing/insufficient_data，
不事后补写聪明理由；前端无数据时显示不可用，不伪造状态。

下一步不要直接升 v2。先按
`docs/plans/thesis_lineage_v1_1_observation_window.md` 跑 7-14 天观察窗口，只看三件事：
跑起来、数据是否完整、断链/缺字段在哪里。V1.1 禁止新增 thesis generator、LLM normalizer、
prompt 优化、自动调参、新 UI、交易行为改动、新 `ThesisRecord` 表或决策前历史 thesis 检索。
如果大量记录缺 `opportunity_id`、`signal_source`、`decision_meta`、shadow outcome 或 forward
return，先修 provenance，不做 v2。

`aletheia thesis audit` 已内建 V1.1 统计：JSON 里看 `data_completeness` 和
`raw_table_completeness`，CLI 人类输出里看 `Data completeness`。这些统计只读现有记录，
不调用 LLM、不写表、不改交易。`aletheia thesis snapshot --days 7` 会把同一份 audit
JSON 写到 `data/diagnostics/`，用于观察窗口留本机快照；`aletheia thesis snapshots`
可汇总最近保存的快照；不要提交这些运行快照。
`doctor --deep` 的 trading diagnostics 也会带 `thesis` compact summary，方便日常入口直接
看到断链/缺字段概况。

观察节奏：部署后先跑 `uv run aletheia doctor --deep --json`，然后每天跑一次
`uv run aletheia thesis snapshot --days 7`。如果观察期内修 provenance，只用修复后新写入的行
判断效果，不要用修复前历史行否定修复。

验证：
- thesis lineage tests：11 passed。
- 相关审计/归因 tests：48 passed。
- API reference + architecture tests：21 passed。
- `frontend npm run build` 通过。
- `scripts/test_ai.ps1` 通过。
- 空 DB CLI 不崩；真实库 7 天窗口可生成 summary。

---

## Recent Change (Trade diagnostics audit — 2026-07-04)

新增两组只读交易诊断，并把 doctor deep 作为主入口：

- `Trade Funnel Audit`：看机会、pending signal、System Operator 决策、proposal、risk、order、
  position 哪一步掉量。
- `Sizing Trace Audit`：看最近 proposal/position 的实际 notional，并用当前配置近似解释小仓位来源。
- `uv run aletheia doctor --deep` / `--json --deep` 现在包含 Alpha / Funnel / Sizing compact summary。
- 新增 standalone 命令：`aletheia funnel audit`、`aletheia sizing trace`，支持 `--hours`、`--days`、`--json`。
- 实现说明：`docs/_ai/TRADE_DIAGNOSTICS_AUDIT_2026-07-04.md`。

边界：本批只读，不改交易行为、不调风控/仓位/杠杆/persona、不创建 proposal、不下单、
不调用交易所、不触发 agent/scheduler。这些诊断/审计层不新增 LLM 调用，只读已有系统记录；
doctor 的交易诊断不制造 blocker，`--fix` 不根据诊断自动调参。

---

## Recent Change (文档全量收口 — 2026-07-04)

`frozen_optimization_backlog.md` 和 `round2_week1_decision_chain_fixes.md` 自创建以来从未加过完成标记，
状态严重失真。本次一次性收口：

- `frozen_optimization_backlog.md`：17 条已完成项加 `[完成 hash]` 前缀（A1-A6/A8-A13/B1/B2/B4/D14/D21）
- `round2_week1_decision_chain_fixes.md`：6 个核心 Fix 全标完成（L-Bug-A/A'/B/C/D + S-Bug-C）
- `REMAINING_WORK_2026-07-01.md`：§3 标已收口，§5.3 标已完成
- 环境修复：hermes-agent 移除 + PATH 清理；`AGENTS.md` + `DEV_SETUP.md` 加 `uv run` 铁律防重复踩坑

未完成确认仍 OPEN：A7/A14/C1/C2/D 系列大部分/E 系列。

---

## Recent Change (全项目 SDD 对齐第一批 — 2026-07-03)

在 Learning SDD 对齐完成后，启动全项目 SDD 漂移扫描并完成第一批高风险修正：

- 修正 `agent_contracts.md`：job 数量 31；paper_trader / position_manager 不再声称
  RuleSet exit 可直接平仓。
- 修正 `knowledge_contract.md`：`strategy_designer` 生产/消费路径标为 legacy，不再当当前活路径。
- 修正 `PI_OPERATOR_DESIGN_DECISIONS.md`：加入 Position C 边界说明，当前 System Operator
  不再触发 strategy_designer / backtest / Optuna。
- 修正 `DOC_ATLAS_BY_DOMAIN.md`：策略域变为 legacy strategy/promotion，不再把规则引擎列为当前范围。
- 补充 `api_reference.md`：model OAuth、settings、risk、operator、analytics 等近期端点。
- 更新多份 spec 注释/claim 文案，将当前 prompt 术语改为 System Operator prompt。
- 新增 `docs/_ai/SDD_GLOBAL_ALIGNMENT_AUDIT_2026-07-03.md`，记录已修项和全项目剩余扫描清单。

验证：
- `uv run pytest tests/specs/spec_learning_pipeline.py tests/specs/spec_insight_feedback_loop.py tests/specs/spec_data_utilization.py tests/specs/spec_serenity_evidence.py tests/specs/spec_serenity_attribution.py tests/specs/spec_universe_pipeline.py tests/specs/spec_strategy_pool.py tests/specs/spec_pipeline_position_manager.py -q`
  → 102 passed, 3 skipped。
- `git diff --check` 通过。

状态：全项目 SDD 修复尚未完成。下一步继续做 route-vs-api_reference 生成式比对、
System Operator 术语边界、config-to-UI、spec missing/xfail 审计。

---

## Recent Change (Learning SDD 文档对齐 — 2026-07-03)

针对 root `discussion.md` 中 owner 对 Learning 的金标准描述，完成一轮 Learning 范围内
SDD/契约/API 文档对齐：

- `learning_contract.md` 重写为干净版，去除控制字符损坏，明确当前链路：
  trade case / memo / feedback → lesson → policy → draft skill。
- `knowledge_contract.md` 修正旧漂移：`ps_lessons` 稳定消费态是 `applied`，
  contradiction 已有保守检测，time-decay 已有基础 half-life，guard candidate 既有人审
  也有配置开启后的自动晋升路径。
- `api_reference.md` 补齐 Learning UI 实际使用的 lesson action、guard candidate promote、
  learning-funnel 和 guard config 接口。
- `SYSTEM_MAP.md`、`DOC_ATLAS_BY_DOMAIN.md`、`persistence_recovery_contract.md`、
  `learning_mechanism_research_plan.md`、`product_definition_2026-06-30.md` 同步用
  System Operator 术语和当前 Learning 状态。
- spec 注释同步：`spec_learning_pipeline.py` / `spec_insight_feedback_loop.py` 不再把
  time-decay 或 memo effectiveness 描述成未实现/xfailed 缺口。

验证：
- 旧状态关键词搜索无命中：contradiction stub、time-decay xfail/missing、accepted risk path、
  guard 无自动晋升、trading_memo 唯一路径等。
- `git diff --check` 通过。
- `uv run pytest tests/specs/spec_learning_pipeline.py tests/specs/spec_insight_feedback_loop.py -q`
  在文档 worktree 通过；无主仓运行态 DB 的条目按现有测试逻辑跳过。

后续：Learning 范围已先收口，下一步按同样方法做全项目 SDD 文档漂移扫描。

---

## Recent Change (Learning 长期技能候选与 memo 时间衰减 — 2026-07-03)

针对 discussion 后续审计里最后一段 Learning 缺口，补齐 lesson → draft skill 和旧 memo 降权：

- `lesson_reviewer` 在 lesson 被应用到 policy version 后，会通过 `SkillStore.create_from_lesson()`
  生成 `status="draft"` 的长期技能候选。
- draft skill 只进入 `knowledge` / `prompt_context` / `ranking_advisory` 低风险层，不会自动
  activate，不会触碰 risk / execution / live / credentials / order submission。
- skill 创建按 lesson id 幂等，重复 reviewer 运行不会复制多条技能。
- `trading_memo` 增加 7 天半衰期权重；System Operator prompt 会显示 `Memo time weight`，
  并把 regime playbook 的胜率边缘做时间折算，旧 memo 不再无限期等权影响判断。
- `docs/_ai/LEARNING_LOOP_STATUS_REPORT_2026-07-03.md` 已更新为当前 Learning 闭环报告。

验证：
- `uv run pytest tests/test_learning_stores.py tests/specs/spec_learning_pipeline.py::test_lesson_reviewer_promotes_and_applies tests/specs/spec_learning_pipeline.py::test_time_decay_overturn_path_exists tests/specs/spec_learning_pipeline.py::test_skill_store_producer_is_draft_only tests/specs/spec_insight_feedback_loop.py::test_memo_half_life_weight_rendered_into_prompt -q`
- `uv run pytest tests/specs/spec_learning_pipeline.py tests/specs/spec_insight_feedback_loop.py -q`

边界：本批不改交易执行、风控限额、凭证或 live 开关；skill 启用仍保留人工审核。

---

## Recent Change (verbose 日志与 doctor 深度诊断 — 2026-07-03)

针对 MVP/Round 1/Round 2 阶段“先尽量保留问题现场、诊断输出要更具体”的要求，完成一轮
日志和 doctor 命令增强：

- gateway/error/frontdoor 文本日志从 10MB 单备份改为 50MB 分段、数字多代保留，适合先保留更多现场。
- gateway/backend uvicorn 日志提升到更详细级别：后端 debug、frontdoor info、前台运行 debug。
- 新增日志安全裁剪与脱敏读取，`doctor --verbose` 展示日志尾部时会遮掉 token、secret、authorization 等敏感字段。
- `aletheia doctor --verbose` / `--deep` 增加“下一步看哪里”的诊断包：异常检查、相关日志提示、日志清单、近期失败 agent run、近期审计事件摘要、红acted 日志尾部。
- `aletheia doctor --json --verbose` 增加 `diagnostics` 字段，方便 AI/脚本在故障时直接读取结构化诊断。
- `aletheia doctor --bundle <file>` 可生成脱敏 JSON 诊断包，包含检查结果、系统摘要、日志清单和脱敏日志尾部。
- `aletheia gateway logs` 现在默认展示 gateway/backend/frontdoor/operator 多路日志尾部，并带文件名和脱敏处理。
- 运维文档补上 verbose doctor、50MB 日志分段、保留策略和敏感信息规则。

验证：
- `uv run pytest tests/test_gateway_logs.py tests/test_doctor.py tests/test_doctor_json.py tests/test_supervisor.py -q -o cache_dir=.pytest_cache_ai`
- `uv run pytest tests/test_security.py -q -o cache_dir=.pytest_cache_ai`
- `uv run aletheia doctor --json --verbose` 结构化输出包含 `diagnostics.logs` / `diagnostics.attention`。
- `uv run aletheia doctor --bundle <tmp-file>` 可写出可解析 JSON bundle。
- `uv run aletheia gateway logs -n 2` 可输出多路日志尾部。
- `uv run pytest -m fast -n 4 -q -o cache_dir=.pytest_cache_ai` 通过；并行快层曾暴露
  `paper_trader` 调度门对极简测试配置桩过脆，已补安全默认值和回归测试。

注意：当前工作区另有历史数据下载/交易决策/审计链相关未提交改动，属于其它边界；本批次不要混入这些文件。

---

## Recent Change (runtime 通讯通路拆分与前后端稳定性 — 2026-07-03)

针对 dashboard 加载条卡 90%、ticker stale、backend unreachable 误报/频报这一组问题，
完成一轮前后端通讯通路收口：

- 连接探活改走轻量 `/api/ready`，不再用重型 `/api/status` 判断“后端是否在线”。
- 前端 backend guard 改为 10s 轮询、连续 3 次失败才通知，减少短暂重启/慢请求造成的误报。
- 顶部 FetchProgress 只统计用户可见请求，忽略后台轮询和带 `refetchInterval` 的长期请求。
- 市场 ticker 查询标记为后台请求，避免 ticker 轮询把全局加载条常驻在 90%。
- `/api/health` / `/api/status` 复用 5s 健康缓存；健康汇总不再在请求路径内直接打交易所外部探测。
- OKX/CCXT `load_markets()` 增加坏 market id 防护：丢弃 `id=None` 的异常行，避免一个坏行打断市场刷新。
- `/api/operator/recommendations` 归入 advisory/background 通路：15s 短缓存，pytest 下自动旁路缓存保证测试诚实。
- 新增 `docs/_ai/RUNTIME_COMMUNICATION_LANES.md`，记录 liveness / control-status / market-display / advisory / future-push 五类通路。

验证：
- `uv run pytest tests/test_ccxt_provider_factory.py tests/test_health_fast_path.py tests/test_frontend_runtime_comm.py tests/test_status_routes.py tests/test_server.py tests/test_operator_events.py -q`
- `cd frontend && npm run build`
- `uv run aletheia doctor`

注意：当前真实运行态仍以 `aletheia doctor` 为准；本文不记录 gateway PID、快照时间等本机事实。

---

## Recent Change (pytest 分层与 AI 快反馈链路 — 2026-07-03)

测试体系改为面向 AI 修 bug 的快反馈：

- 新增直观 marker：`fast` / `integration` / `e2e` / `slow`，保留原 `tier_*` 和 `domain_*` 兼容标签。
- `fast` 只覆盖便宜的本地单测；当前计数 `fast=1189`。
- `slow/e2e` 自动覆盖端到端、在线/真实网络、训练/Optuna/远程联调、doctor/Serenity/Operator 等慢榜热点；当前计数 `slow or e2e=338`。
- 新增 `scripts/test_ai.ps1`：AI 默认先跑上次失败，再跑 `fast`；使用独立 `.pytest_cache_ai/`，绕开本机坏掉的 `.pytest_cache` 权限残留。
- 引入 `pytest-xdist`，但不写进默认 `addopts`；只作为最后阶段可选并行。
- 修复 `tests/e2e/conftest.py` 原来全量收集时给所有测试加 `e2e` 的分层 bug。
- 修复 `tests/test_retention.py` 对 scheduler 配置状态的隐式依赖，使 `fast -n 4` 并行可过。
- `docs/operations/PYTEST_OPTIMIZATION_GUIDE.md` 已更新：慢榜、默认命令、xdist 使用边界、文档规则到测试的映射表。

验证：
- `uv run pytest tests/test_pytest_tiering.py -q -o cache_dir=.pytest_cache_ai` → 9 passed。
- `uv run pytest -m fast -n 4 -q` → 1188 passed, 1 skipped。
- `uv run pytest tests/ -q --tb=line -o cache_dir=.pytest_cache_ai` → 2267 passed, 7 skipped, 7 xfailed。

---

## Recent Change (discussion 批次开发经验沉淀 - 2026-07-03)

新增 `docs/_ai/DISCUSSION_DEVELOPMENT_LESSONS_2026-07-03.md`,总结本轮 20+ worktree
批量修复的可复用经验:

- 长文先拆成 Dxx 可验证清单,审计和修复分支分开。
- 性能先量化再修 top offender;本轮 `/api/operator/recommendations` 重复 artifact scan
  6 -> 1,减少 83.3%。
- 前端源码更新后必须 `npm run build` + `uv run aletheia gateway restart --no-browser`,
  否则浏览器仍可能看到旧 `dashboard_static`。
- 合并后清理 worktree、已合并本地分支、未登记旧目录。
- owner 决策项不能伪装成 bug fix: `reduce_size`、DB retention、历史 PI 标识符迁移、
  live/风控语义都要单独确认。

`DEV_PROTOCOL.md` 和 `AI_WORKFLOW.md` 已加入口,后续 AI 接大批量任务前应先读该文档。

---

## Recent Change (discussion.md 口述 7 项全量落地 — 2026-07-03)

> 7 项已 FF-merge master(commits `1c889a6`→`659057d`,64 文件,线性)。详见记忆
> `aletheia-discussion-round-2026-07-03` + 计划 `docs/_ai/DISCUSSION_ACTION_PLAN.md`。
> **前置再基线**:owner 以为学习闭环大面积断裂——两份审计已过时,断口 A-G/R0/ranker
> 幻觉/lesson→policy 全已修(引用审计前先核 HEAD)。

- **W1 顶栏价格横线真根因**:MarketTicker 取全市场最近20条快照,标的>20 时 BTC/ETH/SOL
  掉出窗口→横线。修=按 symbol 各查最新1条 + snapshot_at 过期指示。+ 全局 FetchProgress。
- **W2**:Trades Orders&Fills|Closed 并排;Analytics+Insights 合并(tab id 保 'analytics');
  侧边栏 Serenity Evolve→Observe。
- **W3** PI Agent→System Operator **仅显示层**(UI/注释/prompt/活文档);技术内核
  (pi_daemon.py/pi_decisions表/PiDaemon类/PI-S*/pi_agent_*)保留。
- **W5 选币偏斜**(数据实证 momentum 84%/long 85%):根因=explore 绕过配额回流 momentum
  + derivatives/event 死 family 占 40% 静态配额 + regime 75% None。修=allocator explore 只取
  under-quota(UP-17)+ 静态 family_quota 重平衡。合成池 84%→39%。诊断 docs/research/
  UNIVERSE_SKEW_DIAGNOSIS_2026-07-03.md。
- **W6** learning_contract.md 订正到 HEAD(accepted→applied L-Bug-E;生产者已接);登记 3 残留。
  发现文档预存 ESC 控制字符损坏(独立清理)。
- **W4 限价单+四要素**(信任边界,owner授权):TradeProposal +take_profit/risk_reward(DB列);
  决策端总填齐四要素;create_paper_proposal 默认 limit;风控 check 2b 对 limit+native 缺一拒单
  (incomplete_entry_plan,market/adopt 豁免)。spec PI-S05。
- **W7** pytest 已优化(110s/1699,最慢2.4s);注册 slow marker + 文档,不硬凑。

**待办(需 owner 决策/信任边界,已登记未擅改)**:reduce_size 升级硬约束?;历史
direction=None 污染(family_direction_edge COALESCE 当 long,被风控 RISK-14 消费);
derivatives/event 数据源死 + regime 75% None(W5 根治的深层);learning_contract ESC 清理。

**验证**:spec 540/530/98%;全量 1686 passed / 10 **预存**失败(基线同样红,测试自身过时);
frontend build+assets 200;后端冷探 3ms;doctor 15 warn 全预存/暂停态无新增。gateway
Paused=True(未擅解除)。⚠️ spot-parity 分支仍未 merge(历史遗留,与本轮无关)。

---

## Recent Change (Position C Phase C 完成 — 文档/spec 同步 → Position C 全部收官 — 2026-07-02)

> **Position C(规则引擎删除)A+B+C 全部完成。** 分支 `refactor/strategy-position-b-p0p1`,
> 全量绿,未 merge master。上线路径见文末。

### Phase C 交付
- **SDD spec 诚实性**(`commit`):spec_strategy_pool 删 SP-S04(测已删 strategy_designer 码);
  spec_model_routing MR-S04 六→五 deep-tier(去 strategy_designer);spec_promotion_gates 不改
  (Option B 保留 check_promotion_gates)。conformance 537 绿。
- **8 契约文档**:scheduler/agent_contracts/SYSTEM_MAP job 表(去 designer+backtest 加 guard_promotion +
  agent 段/timeout/数据流图);api_reference(删 strategies gates/promote/retire + backtest + strategy-evidence
  端点);data_model(4 表标 legacy read-only);promotion_gates_contract + README(LEGACY 横幅);
  execution_contract §9(死字符串诚实性缺口 → 标记已由删除解决);trust_and_change_control §6.1 +
  decision_lifecycle 无需改(引擎非受控信任边界/无引用)。
- **整洁项**:删孤儿 backtest_paper_drift.py + 其单测(路由已删)。
  遗留(无害 optional,已记):ranker always-None strategy_evidence 死参、operator_recommendations
  喂已删分支的死计算(2 处 list_artifacts,操作控制面敏感故未动)、router.py + knowledge_contract 的
  strategy_designer 知识路由(dead-but-consistent 配置,Phase A 有意保留)。

### Position C 全景(A+B+C)
- **A**:`aletheia/strategy/` 整包 + strategy_designer/strategy_evidence/ruleset_scanner/lifecycle_automation
  物理删;ranker/paper_trader/scheduler 解耦;guard_promotion 重挂保命。
- **B**:风控 #14 → scanner-family 证据门(信任边界,SDD-first);决策链/analytics landmine 清;前端页删 +
  #14 Settings UI;doctor strategy_health 改 legacy-only;operator 命令面(6 命令 + 推荐 + 路由 + 动作)移除。
- **C**:spec + 契约文档同步(本条)。
- **保留(Option B)**:create_strategy/promote_strategy/check_promotion_gates 内部 legacy 函数;
  strategy_records/backtest_* 表只读 legacy;strategy_id 可空归因列;GET /strategies + venue 端点。

### 上线路径
Position C 已闭环,可整体 FF-merge 回主仓 + 主仓 restart + HTTP 实测(worktree data/ 空不代表运行态)。

---

## Recent Change (Position C Phase B 全部完成 — 2026-07-02)

> 承接下方「Phase A 后半」。同一 worktree/分支。**Phase A + B 全部完成,12 commit 领先 master,全量 1702 绿。**
> owner 已定 **Option B**。**唯一剩余 = Phase C(文档/spec 同步)**。

### 已交付(全绿)
- **`49c580f` 风控 #14 闸改写(信任边界,owner 批「家族证据门·沿用旧量级」)**:strategy FSM 准入 →
  scanner_family 干净窗证据门(经 opportunity 解析 family → `family_direction_edge` 要 n≥
  `risk.live_family_min_samples`(30) 且 win_rate≥`risk.live_family_min_win_rate_pct`(50));fail-closed;
  删死码 `_LIVE_ELIGIBLE_STATES`;token `family_not_live_eligible:*`。SDD-first:RE-S06 + RE-S04/S04b 前缀
  加 "family_" + risk_engine_contract §2 #14 改写 + 3 行为测试。
- **`49b9cf3` 决策链 + analytics 路由 landmine 清理**:trading_decision 去 ruleset_signals;
  commands.evaluate_trading_opportunity 去 scan_active_rulesets;routes/analytics 删 strategy-evidence +
  backtest 端点。**至此 aletheia/ 内无任何对已删模块的 import**。
- **`0df5016` 前端 + #14 Config↔UI 闭合**:App.tsx 去 StrategyLifecycle import/route/nav;删
  `pages/StrategyLifecycle.tsx`;client.ts 删死方法(getStrategyEvidence/getBacktestPaperDrift/
  getBacktestRuns/Detail/Compare);Learning.tsx 去 "Strategy promotion" 板+PromoCard;**#14 两 config 字段
  Settings UI 补齐**(settings.py RiskConfigUpdate+_RISK_BOUNDS / client.ts 类型 / Settings.tsx
  RISK_FAMILY_GATE 段)。`npm run build` 通过(tsc 无错 + 780 模块)。
- **`6541502` doctor content.strategy_health 改 legacy-only**:引擎删后 strategy_records 冻结,旧 zombie WARN
  会误报;改为报 legacy 计数 info(probe-honesty);test_operator_loop content_health 捕获测试的 WARN 源
  换成 content.proposal_bias。
- **`0df5016` 前端 + #14 Config↔UI 闭合**(见上)。**`6541502` doctor legacy-only**(见上)。**`ac8a7d6` handoff**。
- **`8b8b7ba` operator 命令面移除(#7,Phase B 收官)**:6 个 strategy-FSM 命令(get_strategy_lifecycle/
  create_strategy_draft/create_paper_experiment/mark_strategy_under_review/pause_paper_strategy/
  propose_strategy_promotion)+ dispatch + registry CommandDef + intent + keyword;operator_recommendations
  5 条指向已删命令/agent 的推荐分支(保留 live_limited_enablement_proposal_due);operator_loop
  trigger_strategy_designer OperatorAction(+ 自主发射 + _action + MAX 常量);routes/data promote/retire/gates
  端点;client.ts promoteStrategy/retireStrategy/getStrategyGates。retire/demote 动作保留(已收敛 TS)。
  底层 create_strategy 等按 Option B 保留。命令级测试 ~19 删/改(6 文件),全量 1702 绿。

### ✅ owner 已定 = Option B
`create_strategy`/`promote_strategy`/`check_promotion_gates` **作内部 legacy 函数保留**(写保留的 legacy 表,
~15 测试 fixture 零改);strategy_lifecycle.py 不收缩 FSM 函数(promote_strategy 仍用 _has_pi_approval)。已只删 operator/UI 面。

### ⏳ 唯一剩余 = Phase C(文档/spec 同步,纯文档为主,无 runtime/测试影响)
- **spec 诚实性**:`spec_strategy_pool` SP-S04(strategy_designer regime targeting,已删)标废 + 迁 SP-S05/S06/S07
  到 scanner spec;`spec_model_routing` 去 strategy_designer 引用。(注:spec 测试体不被 pytest 收集,故不报错,
  但 conformance matrix 计数偏高——诚实性问题。)
- **8 契约文档**:trust_and_change_control §6.1 受控模块清单(去 strategy 引擎)、decision_lifecycle、
  agent_contracts(去 strategy_designer job + backtest_orchestrator,加 guard_promotion)、promotion_gates 标废、
  api_reference(去 strategies/backtest 端点)、data_model(strategy_records/backtest_* 标 legacy-only)、
  SYSTEM_MAP(scheduler job 表:去 strategy_designer_daily/eu/us + backtest_orchestrator,加 guard_promotion)、
  design/README。risk_engine_contract §2 #14 已在 49c580f 改。
- **整洁项(无害,可选)**:backtest_paper_drift.py 孤儿死码(路由已删,只剩单测)可删;paper_trader_ranker
  always-None 的 strategy_evidence 死参可清;operator_recommendations 留了喂已删分支的死计算(未用变量 +
  2 处 list_artifacts(500))可清。

### 上线路径
Phase C 完成后一次性把这些 commit FF-merge 回主仓 + 主仓 restart + HTTP 实测(worktree data/ 空,不代表运行态)。

---

## Recent Change (Position C Phase A 后半 — 引擎包物理删除 + guard_promotion re-home — 2026-07-02)

> 承接下方「Position C Phase A 前半」。本次在 worktree `.claude/worktrees/strategy-deletion`
> (基于分支 `refactor/strategy-position-b-p0p1`)完成 Phase A 后半。用 worktree 隔离而非在

> 承接下方「Position C Phase A 前半」。本次在 worktree `.claude/worktrees/strategy-deletion`
> (基于分支 `refactor/strategy-position-b-p0p1`)完成 Phase A 后半。用 worktree 隔离而非在
> 主 checkout 切分支 —— gateway 全程跑主仓 master 稳定完整引擎,auto-restart git hook 有主工作树
> 守卫不会从 worktree 触发,把计划 §3「半删状态被重启崩溃」的风险从"缓解"变成"消除"。

### 已交付(本次 commit,Phase A 后半)
- **scheduler 解耦**:删 3 个 strategy_designer cron job + backtest_orchestrator job +
  `_run_strategy_designer` / `trigger_strategy_designer_on_demand` / `_run_backtest_orchestrator` /
  `_backtest_orchestrator_body` + 两个 `_ASYNC_JOB_TIMEOUT_SEC` 条目。
- **3 个热路径调用点解耦**:signal_emitter(event-driven design 块 + `_high_conf_emitted` 死变量)、
  price_feed(significant-move designer 触发块)、post_trade_reviewer(P3 review→design 事件链 +
  `_trigger_strategy_adaptation` 函数)全部移除。
- **dreaming_agent**:去 performance_ledger 的 factory_block(Strategy Factory metrics 段)。
- **cli/strategy**:删 backtest 命令(留 list/status 读 legacy 表)。
- **物理删除**:`aletheia/strategy/` 整包(14 文件)+ `agents/strategy_designer.py` +
  `analytics/strategy_evidence.py` + `trading/ruleset_scanner.py` + `analytics/lifecycle_automation.py`。
- **测试**:删 9 个纯引擎 test_*(dsl/backtest_gate/param_sweep*/walk_forward/orchestrator/evidence/
  causal_chain/event_driven_design)+ spec_evaluator_contracts(去 test_spec_conformance 导入);
  调整 test_runtime(job 集 + 删 designer 测试)、test_event_triggers(删 2 designer 测试)、
  test_experience_loop(删 designer 测试)、test_learning_loop_integration(删 TestRegimeAwareFiltering/
  TestRuleSetBonus/TestPostTradeEventChain 三类,留 TestLessonAwareScoring —— 前两类本是 b9f733f 就该清
  的漏网,上会话只跑 ranker 子集没发现)。

### ⚠️ 计划外的关键 ripple(计划 §2 未预料 —— 已妥善闭合)
删 backtest_orchestrator 会**静默掐断 `guard_promotion`(活跃学习子系统,doctor 13 active guards)**:
它唯一触发链是 backtest_orchestrator → `run_lifecycle_automation` → `run_promotion_check`,无其它 scheduler
入口。**处置**:`analytics/lifecycle_automation.py` 三函数中 `apply_strategy_evidence_actions`(用被删的
strategy_evidence)、`apply_exit_policy_feedback`(写 suggested_exit_params,消费者是被删的 strategy_designer,
前端不读)均已死 → 整文件删;`run_promotion_check` **重新挂成独立 scheduler job `guard_promotion`**
(daily 04:00 CST,executor=io,gated by runtime.paused —— 精确保留原节拍与门控,零行为变化)。
test_runtime required job 集加 `guard_promotion` 锁死防回归。

### 验证(证据)
- 20 个入口模块(app/scheduler/operator_loop/commands/routes/doctor/agents/cli 等)全部 import OK ——
  **物理删包不破坏系统 import**(所有外部引擎引用都是函数内 lazy)。
- 受影响 8 个测试文件 112 passed;spec conformance 537 claims / 99%。
- 全量套件:**1721 passed / 3 skipped / 0 failed**(182s;总数较 8f55261 的 1842 减少 = 删除 ~100+ 引擎测试)。

### 仍留待 Phase B/C(未合并 master 前必须清完 —— 分支合并前这些不会 live)
- **Phase B 运行时地雷**(lazy import,import-安全,被调时降级,非 hot path):
  `commands.py:1880` ruleset_scanner;`routes/analytics.py:12/164/176/191/219` strategy_evidence +
  strategy.repositories(backtest 端点)。连同 doctor `content.strategy_health` / `_has_pi_approval`、
  **风控 #14 闸(SDD-first,owner 已放行)**、前端 StrategyLifecycle 页 + Learning gates 一并 Phase B 删。
- **Phase C spec 诚实性**(claim 注册表,不被 pytest 执行故不报错但 matrix 略虚高):
  `spec_strategy_pool` SP-S04(strategy_designer regime targeting,已删)标废 + 迁 SP-S05/S06/S07 到 scanner
  spec;`spec_model_routing` 去 strategy_designer 引用;+ 8 份契约文档同步 + agent_contracts/SYSTEM_MAP
  的 scheduler job 表(去 strategy_designer_daily/backtest_orchestrator,加 guard_promotion)。
- **附带遗留**(harmless dead,Phase C 清):`knowledge/router.py` AgentRole Literal 含 "strategy_designer"
  路由项;`config/schema.py`/`trading/instruments.py` 注释;`config.strategy_factory.*` / event_triggers
  designer 字段(schema 保留不影响 import)。
- **保留不删**:`strategy_records`/`backtest_runs`/`backtest_trades` 表(只读 legacy,不 DROP);
  `strategy_id`/`linked_strategy_id` 列。

### 上线注意
本分支仍**未 merge master**;主仓 gateway 跑的是含完整引擎的 master。合并须在 Phase B/C 完成后一次性
FF-merge 到主仓(彼时运行时地雷已清),再主仓 restart + HTTP 实测(worktree 的 data/ 为空不能代表)。

---

## Recent Change (Strategy 模块存废 → Position B 交付 + Position C 删除进行中 — 2026-07-02)

### 背景 / 判决
多 agent 存废分析（6-lens 证据 + 3 立场 + 红队 + 综合）审视 Strategy 模块（规则引擎 vs
Agentic 决策的范式冲突）。**owner 拍板：选 C —— 整套规则引擎删掉；现在做；放行 P3（风控 #14 闸）；
代码与文档必须全程同步。** 分析报告与推理链见两份计划：
- `docs/plans/strategy_module_position_b_refactor_2026-07-02.md`（B 的 P0/P1，已交付）
- `docs/plans/strategy_engine_deletion_position_c_2026-07-02.md`（C 的删除计划 + 真实 ripple + 分阶段）

关键认知：RuleSet 在代码里是**真·因果件**（硬入场闸 + 绕过 PI 的自动平仓 + 每日 backtest 退役），
但活体效果为零（208 笔平仓 ruleset_exit=0，84 策略 0 曾到 paper_running）= "范式冲突代码上真实、
行为上休眠"的已布线未引爆层。完整性核查发现 `universe_forward_outcomes`+`calibration_stats`+Tier-1
guard **已是** scanner-family thesis 存储，别重建。

### 已交付（commit，全绿）
- **Position B P0/P1**（`8f55261`，全量 1842 passed）：P0 拆引信（移除 RuleSet 绕过 PI 的自动平仓，
  spec PL-PM-08）；P1a per-family thesis 证据喂 PI（`family_direction_edge`，DU-S10/S11）；
  P1b regime 归位 scanner-family（`cfg.universe.scanner_regime_scoring` no-op 抽取，SP-S06/S07）。
- **Position C Phase A 前半**（`b9f733f`，−457 行）：ranker + paper_trader **完全从引擎解耦**
  （去 strategy 解析/regime-fit/ruleset-entry/evidence 门/Path A；所有 `aletheia.strategy` import 移除）。
  引擎包**暂留未删** = 系统仍可完整 import = 安全中间态。ranker 10 / guards+ranker 24 绿。

### ⏳ 未完成（owner 选"A：新会话专注续做" —— 下一会话从这里接）
分支 `refactor/strategy-position-b-p0p1`（**注意：主 checkout 当前 HEAD 就在此分支**，非 master；
含 B+C 两批 commit，未 merge master）。剩余按 C 计划分阶段：
- **Phase A 后半**：scheduler（3 designer job + backtest job + `_run_strategy_designer` /
  `trigger_strategy_designer_on_demand` / `_run_backtest_orchestrator` / `_backtest_orchestrator_body`）；
  3 个 `trigger_strategy_designer_on_demand` 调用点（signal_emitter:182 / price_feed:196 /
  post_trade_reviewer:171，均 lazy import = 运行时地雷）；dreaming_agent（performance_ledger）；
  lifecycle_automation（strategy_evidence）；cli/strategy。**然后物理删** `aletheia/strategy/` 整包 +
  strategy_designer.py + analytics/strategy_evidence.py + trading/ruleset_scanner.py + ~10 测试文件。
- **Phase B**：commands.py（operator create/promote 命令面）、routes/data.py+analytics.py（strategies/
  backtest 端点）、doctor 探针（content.strategy_health / `_has_pi_approval`）、**风控 #14 闸（信任边界，
  SDD-first，owner 已放行）**、前端 StrategyLifecycle 页 + Learning 页 gates。
- **Phase C**：8 份契约文档同步（trust_and_change_control §6.1 / decision_lifecycle / agent_contracts /
  promotion_gates 废止 / risk_engine #14 / api_reference / data_model / SYSTEM_MAP / design README）
  + SDD spec 删改（spec_strategy_pool / spec_promotion_gates / spec_evaluator_contracts / backtest*）
  + test_spec_conformance 导入更新。
- **保留不删**：`strategy_records`/`backtest_runs`/`backtest_trades` 表（只读 legacy，历史可查，不 DROP）；
  `strategy_id`/`linked_strategy_id` 列（可空归因标签）。

### 不变量
交易状态一致性 > 一切；每阶段结束 import-clean + 测试绿 + commit（gateway 重启落绿 commit）；
风控其余 19 关不动只改 #14。

---

## Recent Change (动态 sizing / persona / learning 系统 P0–P4 — 2026-07-02)

### 背景
owner 定方向:仓位/杠杆/加仓/止损在风控硬边界∩persona区间内由 Operator 动态决 + Learning 拟合 +
渐进解冻。设计 `docs/design/dynamic_sizing_operator_persona_design.md`(IMPLEMENTED P0-P4)。

### 三层决策盒 + 五期(全默认 OFF = 零行为变化)
- **P0**(`3e8015f`)`active_broker_view_complete()` 视图完整性门:修 #1(sizing 读错权益)+
  #2(reconciler thrash 的 unified-新鲜但缺 broker 变体)。PL-ADOPT-19/20/21。
- **P1**(`ad580a1`)persona 区间 schema + `trading/persona.py` resolver seam(conservative==扁平配置)。
- **P2**(`9c27dc4`)`build_sizing_context()`→risk_appetite;上下文决策(仅 operator_decidable 参数)。
- **P3**(`3fbdd79`)`trading/sizing_policy.py` regime 干净窗奖励学习 appetite;doctor loop.sizing_policy。
- **P4**(`884e035`)progressive unlock(ladder+distress refreeze)+ pyramid_plan(硬顶 clamp)。
- specs PS-S01..S12(spec_persona_sizing.py);conformance 510→522;full suite 1817/0。

### 未接(owner-gated)
金字塔**实盘 scale-in 下单**(policy 已实现,下单触 execution 边界,需 owner 授权+给 max 数值)。

### 顺带
合并 PR#35(demo/live 费率等价性)后 6 个费率漂移用例失败,已对齐测试期望 0.1%→0.06%
(`d8aedeb`+`149774d`,只改测试不改 PR#35 代码)。

### 边界/开启
风控硬上限 immutable / live 人工 / Learning 只吃干净窗 / 渐进解冻+refreeze。owner 分步:
persona_sizing→跑干净窗→unlock→learning→(授权)金字塔;每步 demo+doctor 观察。

---

## Recent Change (reconciler 假孤儿 thrash 修复 — 2026-07-01)

### 背景
观察 Phase 2A+.10 做空链路时监控抓到:6 空仓 2s 内全平(reconciled_gone,均未碰 stop/tp),
5min 后以相同入场价全部 source=adopted 重开。追到代码核心 → 自维持 thrash 循环。根因分析
报告 `docs/research/ENTRY_EXIT_ASYMMETRY_ANALYSIS_2026-07-01.md`(commit 3ea0005/5d6bb89)。

### 根因
非 Bitget 账户(okx_demo)上 `position_reconciler.classify_positions` 在 unified 多 broker
缓存过期(≥600s)时回退读 legacy Bitget-only 缓存 → 该缓存新鲜但空(0 持仓,status=ok)→
诚实性守卫因 status=ok 不触发 → 所有 OKX 持仓误判 orphaned → reconcile 平仓(仅本地记账不动
交易所)→ 持仓仍在 OKX → 下周期 auto-adopt 重认领 → 无限 thrash。触发窗口=exchange_sync
连续 2 次 OKX 同步超时使 unified age>600s,撞 5min reconcile 节拍。

### 损益评估
41% 仓位 reconciled_gone、56% source=adopted;PnL 数字未被粉饰(reconciled_gone 净 -0.72,
≈平),但**strategy 归因被毁**:仅 10/110 真实平仓带 strategy_id,0/116 adopted 带 strategy_id,
64% 真实平仓在 adopted 碎片上(默认 2%/4% 价位非策略价位)。owner 干净窗基本是 thrash 产物。

### 修复(SDD,commit 5d6bb89)
- **P0** classify_positions 回退改 broker 感知:仅 Bitget/live 才回退 legacy 缓存;非 Bitget +
  unified 过期 → bail 全 managed。spec PL-ADOPT-10/11/12。实测真配置 stale 路径 0 false-orphan。
- **P1** go_live_scorecard 四项交易指标排除 synthetic 平仓(新建 analytics/close_reasons.py 单一
  真源;trade_case_builder 复用)。spec AN-S03。
- 穷尽排查:缓存错配只坑 reconciler 一处(其它消费者 mode=None 默认路径已被 profit-loop 保护)。

### P2(owner 授权已实施,commit da38fbf)
- P2-1 adopted 仓交易所保护单:核实更大——OKX broker 从不下交易所 TP/SL(只 Bitget),
  `exchange_tpsl_enabled` 默认 False(休眠)。新增 OKXClient.place_algo_order + okx_broker
  place_okx_protective_orders + stops.place_exchange_protective_orders 分发 + adopt 调用。
  双门控 flag(休眠零生产影响)+ DEMO-only(不给 live 自动下单)。PL-ADOPT-13/14。
- P2-2 归因:P0 已解根问题;防御性补 adopt._recover_strategy_link(紧配近 15min reconciled_gone
  本地平仓继承 strategy_id,否则 NULL 不误归因)。PL-ADOPT-15/16。
- P2-3 factor_feedback 过滤 synthetic 平仓(is_synthetic_close),信号门不再被误教。LP-S15。
- 验证:conformance 505 全绿;full suite 1815/0;gateway restart P2 live。

### 验证
conformance 496→500 全绿;full suite 1815 passed/0 failed;gateway restart 后真配置模拟 stale 路径
0 false-orphan;doctor health.rollup healthy(reconciled_ratio 是 trailing 窗,会自愈)。

---

## Recent Change (Phase 2A+ 剩余四项收口 — 2026-07-01)

### 背景
owner 指令「尽量把 2a 完全完成,不要再遗留任何项目」。承接 Phase 2A CLOSED（`7506a04`）
与 2A+.8/.12 已修（`04ef582`/`8d3bb93`），本次把 2A+ 剩余四项 PARTIAL/PENDING 一次做完，
Phase 2A + 2A+ 代码层面全部 CLOSED（唯一剩 2A+.10 = 纯运行观察，非代码）。
commit：`f093e36`（实现）+ `8cea488`（文档收口），已 push master。

### 关键修复
- **2A+.2 designer 写 applicable_regimes**：新增 `_derive_regime_targeting(direction)`
  同时推导 direction_affinity + applicable_regimes（long→排除 bear / short→排除 bull /
  neutral→all，与 affinity gate 一致而非双重限制）。**关键陷阱**：applicable_regimes 必须写成
  **JSON 数组**——`paper_trader_ranker._check_strategy_regime_fit` 用 `json.loads`+`isinstance(list)`
  解析，逗号串会 json.loads 失败被静默丢弃使过滤失效。primary + sweep-variant 两个 create_strategy
  调用点都传。SP-S04 升级 L2a→L2b（含 ranker 真过滤断言）。
- **2A+.4 Dashboard 方向标签**：`types.ts` 加两字段；`StrategyLifecycle.tsx` 加 Direction 列
  （affinity badge）+ drawer（`fmtRegimes()` 解析 JSON 数组，malformed 不抛）。后端 GET /strategies
  已返全列。
- **2A+.9 family_quota 按 regime**：`config/schema.py` 加 `family_quota_by_regime`（4 regime 默认：
  bear 提 short_bias/derivatives、starve momentum）；`allocator.resolve_family_quota(cfg, regime_state)`
  命中覆盖、None 回退静态（向后兼容）；`service.py` 传 `current_regime.state`；settings 路由 +
  Settings UI 每 regime 一行 + 逐行 sum=1.0 前后端双校验。UP-16 L2b。
- **2A+.11 atr_4h/bb_width_4h 接 scorer**：`score_volatility` 加 4h 多时间框架确认（4h ATR>4%
  持续放量 +12 / 4h BB<3% 挤压蓄能 +12），区分 1h 噪声与真实波动 regime。UP-15 L2b（反向验证防死码）。

### 未做（有理由,非遗漏）
- **2A+.10 端到端 short 验证**：非代码——需 bear/volatile regime 真实出现观察全链路；且「bull regime
  下 short 被 gate 过滤」需 bull 出现才能验。运行观察证据留在 `data/observation/`（gitignored）。

### 验证
conformance 493→496 claims 全绿；full suite 1815 passed / 0 failed（确定序两遍）。前端 build 通过。
`aletheia doctor` health.rollup healthy。**运行态状态一律以 `aletheia doctor` 为准。**

---

## Recent Change (Scanner + Observer 审计修复 P0–P3 — 2026-07-01)

### 背景
基于两份审计报告（`docs/research/{UNIVERSE_SCANNER,MARKET_OBSERVER}_AUDIT_2026-07-01.md`）制定并实施 `docs/plans/scanner_observer_audit_fix_plan_2026-07-01.md`。宽 fan-out 核实 workflow 因 workspace rate-limit 全灭，改手动逐条 grep+读源码核实，查出报告 **6 处根因偏差**（见报告 §0）。分支 `fix/scanner-observer-audit-fixes`，spec `UP-01~13` GREEN。

### 关键修复
- **US-4（最高杠杆，一处解三处）**：`service.py:256` forward-outcome direction 改用 `scanner_direction`（与 signal_emitter 同源）+ `_infer_direction` 加 event 分支 → 消解 momentum 54% direction=None + event 0 条 forward outcome + 胜率统计双重失真。
- **US-1**：`compute_total_score` 消费 `scanner_weights`（PI 手调权重此前完全不生效）。
- **US-3/US-8**：inserted_members 补 market_type + feature 按 (symbol, market_type) 复合 key（perp 不再错标 spot）。
- **US-9**：short_enabled=False 硬拦 short（不再被 score≥66 绕过）。**US-5/US-6**：BB squeeze O(n²)→O(n)；price_precision tick-size 保真。
- **MO-1**：key_observations 进 System Operator layer1（+渲染）。**MO-2**：guard advisory 改 per-symbol（闭合学习回路断口 F）。
- **MO-3/4/6/7/8/9/11/12/13/14**：F&G 填充、scanner_family hint、RS rank 单symbol省略、crowding n/a、fallback DEGRADED 标注、news 48h 过滤、prediction 流动性过滤、load_config 移出循环。

### 未做（有理由，非遗漏）
- **US-7**（sync 失败）：`get_current_universe` 读侧已 fallback 静态池 + `run.status=failed` 已记录，无需重建。
- **US-12（all_scores 入库）/US-16（4h 特征接入 scorer）**：无消费者/属增强 → defer `ROADMAP.md`。
- **MO-5/MO-10/MO-15**：MO-1 已补信息 / FTS MATCH 已保证相关性 / 每 cycle 一次开销可忽略。

### 验证
`tests/specs/spec_universe_pipeline.py`（13 claims GREEN）+ 全量回归。**运行态状态一律以 `aletheia doctor` 为准。**

---

## Recent Change (System Operator 重命名 + earendil-works/pi 结论 + 双层模型路由 — 2026-07-01)

### 背景

本次会话完成三项工作：(1) 双层模型路由上线；(2) earendil-works/pi 研究与结论；(3) 术语统一。

### 1. 双层模型路由（commit e5a8010）

**问题**：所有 agent 共用单一 `default_provider`，顶层决策 agent 和批量数据提取 agent
用同一个模型（成本/质量都难优化）。

**实现**：
- `aletheia/config/schema.py`：`ModelsConfig` 加 `deep_provider` / `fast_provider` 两个可选字段
- `aletheia/model_provider/router.py`：新增 `get_provider_for_tier(tier)` 翻译函数
- `aletheia/agents/helpers.py`：`llm_complete` / `llm_structured` / `llm_stream` 加 `provider=` 参数
- deep 层（顶层决策）：`trading_decision.py`、`strategy_designer.py`、`risk_critic.py`、
  `market_observer.py`、`dreaming_agent.py`、`lesson_reviewer.py` 共 11 个 call site
- fast 层（批量提取）：`research_worker.py` 5 个 call site
- 前端 Settings：Routing Role 下拉（Default / Deep tier / Fast tier）
- `tests/specs/spec_model_routing.py`：5 个 MR-* SDD claims 全 GREEN

**向后兼容**：`deep_provider=None` / `fast_provider=None` → 自动降级 `default_provider`，
现有配置零迁移。

### 2. earendil-works/pi 研究结论

**结论**：earendil-works/pi（`@earendil/pi` npm 包）是**终端编程助手 CLI**（RPC/SDK/Skills
框架，让 AI 辅助编码），与 Aletheia System Operator（交易决策 + 系统监管）**完全不同职责**。

**关键核查**：
- `operator-shell/package.json`：无 `@earendil` 依赖
- `operator-shell/src/pi_runtime_adapter.ts`：调用的是 Aletheia Python `/api/operator/internal/llm-session`，
  不调 earendil npm API
- 历史"crosswalk 文档"对照的是内部计划文档 vs 双进程设计规格，不是 earendil 代码映射

**决定**：不引入 earendil-works/pi，operator-shell 完全自建（~6k TS + ~6k Python）。

### 3. 术语统一：PI Agent → System Operator

所有活文档统一用"System Operator"；`pi_daemon.py` 文件名保留（改名触 trust boundary）；
历史归档文档（`docs/design/specs/2026-06-10-pi-*.md` 等）只加文件头标注不全量改写。
决策详见 `DECISIONS.md` § 2026-07-01。

---

## Previous Change (P3d 债务核实 — 2026-06-30)

### 核实结果

grep 逐条核实 P3d MASTER_FIX_LEDGER 中标记为"未核实"的条目：

| 条目 | 结论 |
|------|------|
| §1.3 supervisor restart window | ✅ **ALREADY FIXED** — `_gateway_runner.py:383` 已用 `time.monotonic()`，原 ledger 指向旧代码 |
| BIN-XLOOP BinanceClient eager-init | ✅ **ALREADY FIXED** — `_ensure_exchange()` lazy-init 已在 `binance/client.py` |
| F6 symbol_map KeyError | ✅ **ALREADY FIXED** — `to_coingecko_id()` 已改 `.get()` 返回 None |
| dreaming_log 孤儿 | ✅ **MYTH** — 实际 0 孤儿（top 20 active check） |
| F2 POST /policy 缺失 | 低优先 — 前端不调用，走 `POST /settings/universe`；dead repo fn，无影响 |
| E5 retire 无超时 | **设计态** — recommend_retire → pi_decisions 等 PI 确认，是有意流程 |
| E3 volume_scanner 内存态 | 仍 OPEN — 重启重建，低危 |
| CR-PI-01 scanner weight 不持久化 | 仍 OPEN — 需 schema + 前端 + 调用改动，中优先 |

### 文档更新

- `docs/issues/BACKEND_ISSUES.md`: §1.3/BIN-XLOOP/F6 改为 ALREADY FIXED
- `docs/issues/_RAW_SWEEP_LEDGER.md`: BIN-XLOOP/F6 状态 → `fixed:2026-06-30-verified`
- `docs/issues/MASTER_FIX_LEDGER_2026-06-27.md`: P3d 一般 bug 列表标注三项 ALREADY FIXED

### SDD spec 状态

`spec_learning_pipeline.py` 20 claims 全 GREEN（19 passed，1 xfailed OL-2 optuna）

---

## Previous Change (Evolve 模块数据清理 + 根因修复 — 2026-06-30)

### 背景

检查 Evolve 模块（post_trade_reviewer / dreaming_agent / lesson pipeline）的数据库写入质量，
发现两个独立 bug 导致大量幻觉/无效内容写入 knowledge_items 和 ps_lessons。

### Bug 1: post_trade_reviewer flood (LP-IDEM)

**根因**: 每 4h 运行一次，每次 `list_fills(limit=200)` 取相同静态 fill 池，无幂等保护。
`knowledge_hash_exists` 拦不住（LLM 输出文本随机略变），导致 2012 条冗余 post_trade_review。

**修复** (`aletheia/agents/post_trade_reviewer.py` + `aletheia/knowledge/manager.py`):
- `_fills_window_key(strategy_id, fills)` — SHA-256 of sorted fill IDs → 16-char hex
- `_review_already_written(strategy_id, window_key)` — `tags LIKE '%window:KEY%'`
- 写入时 `tags=["window:{window_key}"]`，相同 fill 集合跳过 LLM

### Bug 2: dreaming_agent evidence void (LP-EVIDENCE)

**根因**: `_extract_lessons` 调 `store.create(evidence={})` 写空 evidence_json，
`lesson_reviewer` 拿不到数值证据 → 每次 defer → 246 条 lessons 永卡 proposed。

**修复** (`aletheia/agents/dreaming_agent.py`):
- 从 trade_case_study frontmatter 构建确定性聚合证据：
  `outcome_counts`, `avg_pnl_pct`, `factor_keys`, `scanner_families`, `case_count`
- 确定性证据与 LLM 输出 merge，确定性优先

### 数据清理（历史存量）

- 433 条重复 `trade_case_study` → archived（保留最早一条，dedup 按 content_hash）
- 246 条空 evidence `ps_lessons` (proposed, 2026-06-28 前) → archived

### SDD 更新

- `tests/specs/spec_learning_pipeline.py`: 新增 LP-IDEM + LP-EVIDENCE（共 20 claims，全 GREEN）
- `docs/issues/BACKEND_ISSUES.md`: §2.2 末尾追加两条 ✅ FIXED 记录

### 验证

```
uv run pytest tests/specs/spec_learning_pipeline.py -q
→ 19 passed, 1 xfailed
```

---

## Previous Change (issue triage + event scanner — 2026-06-30)

### Issue triage 结论

三文件全量审计结论：
- **RUNTIME_BEHAVIOR_ISSUES.md**: 基本干净，22 条全闭，残余仅 1 条 aiohttp unclosed session（监测即可）
- **BACKEND_ISSUES.md**: APP-HMAC（hmac.compare_digest）、kill-switch confirm_release、OP-WINRATE（fills→positions）均已在较早 commit 实现；CUM-ADD 被 risk engine check #6b `symbol_already_open` 覆盖，无需额外修复
- **F5 event scanner**: 唯一本会话修复（见下）

### F5 `score_event()` 实现

**位置**: `aletheia/universe/scanners.py:328`

**根因**: `score_event()` 是永远返回 `(0.0, [], None)` 的空壳，整个 event 家族的机会评分静默为 0。

**修复**: 实现基于可用 feat 字段的冲击事件检测：
- 核心信号：1h 价格跳空（+4% / +8% 两档，direction-intrinsic）
- 量能确认：`vol_ratio >= 4x/2x/1.5x` 三档加分，低于 1.5x 折半
- Taker 方向确认：`taker_buy_ratio` > 1.5 或 < 0.65
- OI 爆升协助（> 15% delta）
- 延伸 RSI 折价（> 80 / < 20 打 0.7）
- 4h 趋同小幅加分

**验证**: 强冲击（ret_1h=12%，vol×6，taker=2.0）→ event 家族 90 分成为 best_family

---

## Previous Change (xfail 全量闭合 — 2026-06-30, adc5fd7)

### 10 个 xfail claims 升级为 implemented

**代码实现（6 处）**:
- `operator_loop.py`: `_request_pi_approval_for_selfheal()` — L1 自愈前写 recommend_l1_selfheal 审批记录 (OL-C08/SH-C02)
- `go_live_scorecard.py`: `count_consecutive_zero_fill_days()` (D8-S04)、`check_kill_condition_consecutive_rounds()` (D8-S02)、`compute_pnl_by_trade_school()` (D2-S03)、`_check_profit_factor()` kill-floor guard PF<0.5→fail (D8-B01)
- `server/routes/live.py`: live_unlock 加 scorecard gate，composite_score<70 返回 HTTP 403 (D8-B02/D8-C01)
- `portfolio/position_manager.py`: `adjust_stop(position_id, new_stop_price)` — PI stop 调整路径 (RE-S05)
- `risk/engine.py`: check #6 加 trade_school 共池决策注释 (D2-C02)

**Spec 层**: 三个文件 xfail 全移除，status missing→implemented
**验证**: 63 PASS / 3 XFAIL（正确 defer: D8-B03/D2-S02/D2-B02）/ 0 FAIL
**Conformance**: 440 claims | 432 implemented | 98%

---

## Previous Change (产品定义对齐 + SDD 代码校准 — 2026-06-30)

### 八域产品定义对话
与 owner 完成 8 个功能域深度确认（结果落 `docs/plans/product_definition_2026-06-30.md`）：

- **PI Operator**：A路单 LLM 主环路（ReAct 风格），B路 council 模式 Phase 2 延后；六参考实现均确认 A路为生产标准
- **交易品种**：perp + spot 同一 PI；Serenity Lens 为独立第二赛道（xStocks + dreaming_agent）
- **数据层**：免费替代 CoinGlass/Glassnode → Coinalyze；技术指标尽量多采集；新源须在 data_utilization.md 预登记
- **学习/进化**：Ranker 权重调整 + trading_memo 两路低/中风险学习；三阶段研究计划（`docs/plans/learning_mechanism_research_plan.md`）
- **风控执行**：risk engine 保持独立最终审核；动态调整 defer Phase 2
- **自愈层**：L0/L1/L2 三档；L1 可并行交易但 PI 批准前置；L2 须人工确认
- **人机界面**：Command Center 心跳三宫格设计（`docs/plans/command_center_heartbeat_design.md`）
- **失败退出**：当前 MVP 全 demo 阶段，kill 机制等 demo 达标再讨论

### SDD 契约文档更新（5 份）
- `live_unlock_contract.md §4b`：PI 全权 + UI 警示墙三要素不变式
- `PI_OPERATOR_DESIGN_DECISIONS.md`：产品基线基准段
- `knowledge_contract.md §8`：学习安全边界（Ranker / trading_memo / 风控不可修改）
- `operator_layer.md`：自愈合同（L0/L1/L2 + PI 批准前置）
- `data_utilization.md`：Coinalyze 规划 + DU-C01/C02 登记

### GAP 修复（代码，commit 9c9862a）
- `live.py`：live_unlock → operator.permission_mode=Auto；live_lock → ReadOnly（对称；GAP-LU-01）
- `derivatives.py`：删除 `_fetch_liquidations_ccxt` + `_CCXTLiquidationsUnsupported` 死代码 87 行（GAP-DU-C02）
- `coinalyze.py`（新建）：CoinalyzeCollector，清算+OI，key-guard 模式（GAP-DU-C01）
- `operator_loop.py`：L1 自愈三动作 stub + OL-C08 xfail 守卫 PI 批准门（GAP-C08）
- `Settings.tsx`：LiveUnlockWarningWall 三要素警示墙替换单行 confirm()（GAP-LU-03）
- `registry.py`：Auto 权限模式补全

### 新增 spec claims（5 个 spec 文件，~150+ claims）
- `spec_live_unlock.py`：LU-B07/C04/C05/C06/C01b（§4b 全权 + 对称）
- `spec_operator_loop.py`：OL-S11/S12/C07/C08
- `spec_data_utilization.py`：DU-C01/C02/S04/S05
- `spec_learning_pipeline.py`：LP-S12/S13 + KLS-G02 修正
- `spec_insight_feedback_loop.py`：IFL-* 三处审计缺口

### 验证
408 claims / 99% 通过；1700 业务测试通过（Charts.tsx UI 规范违规为预先存在，非本次引入）

### 剩余（defer）
- CoinalyzeCollector scheduler 接入（config + `_async_data_collectors`）
- OL-C08 PI 批准门接通（L1 自愈动作当前为 stub）
- Command Center 心跳三宫格实现
- 学习机制三阶段研究（Phase 1 学术论文）
- B路 council 模式（Phase 2）

---

## Last Updated (prev)

---

## Recent Change (OpenAlice TaG 借鉴 P0-1 + P1-2 + 文档全量同步 — 2026-06-29)

分支 `worktree-openalice-tag-research`,5 commit:`b8ed990`(P0-1)+ `2359971`(Vibe Trading 交叉核查)+ `31eb1a9`(P1-2)+ `67ed379`(L2 design 契约同步)+ `cb4a303`(L4 status 同步)。

**研究**(13-agent workflow,797k tokens):三层 ground truth(博客原文 / 本地 `_refs/OpenAlice` 真实 TS 源码 / Aletheia 自身对照)+ 5 镜头分析 + 5 轮对抗核查。报告 `docs/research/OPENALICE_TAG_DEEP_DIVE_2026-06-29.md`(含 Vibe Trading 帖交叉核查 §八点五)。核心发现:**TaG 真正值得借鉴的是工程纪律(reject 一等审计 + 执行后 state_after 快照),不是 git 比喻包装本身**;博客 "1 月崩盘预判" 自标 backtest 逻辑自相矛盾零证据值;"Trade Primitive auto-learn effect" 经源码 grep 命中零是纯营销话术。

**P0-1**(`b8ed990`):TradeProposal 加 `commit_hash` + `parent_hash`(16-char SHA-256 prefix,排除 `status` 与 `commit_hash` 自身后哈希所有不可变字段)+ migrations 加列 + 索引 + `insert_trade_proposal()` 事务内自动填且全局单链(按 `rowid DESC` 选 parent)。SDD:DM-S08/S09 L2a + DM-B06 L2b + DM-C02 L2c(re-compute 确定性 + 链完整性 + **status 免疫**)。不触 trust boundary,改 4 文件 +241 行。

**P1-2**(`31eb1a9`):`position_reconciler.adopt_position()` 写 `position_adopted` audit event 时,在 `after_state` 加 `commit_hash` + `parent_hash` 两字段(直接读 insert_trade_proposal 事务内填充的 proposal 对象,无需重算)。SDD:PL-ADOPT-09 L2b。改 2 文件 +99 行。**价值闭合**:P0-1 给提案打了内容指纹,P1-2 让 reconcile audit 第一个用上这个指纹——审计消费者从此单看 `audit.jsonl` 行就能还原 hash 链关联。

**A FORBIDDEN_INTENTS hardening 核查发现是 no-op**:4-agent explore workflow 发现 `deactivate_kill_switch` + `modify_permissions` **早在 2026-06-23 就加进** `agent_shell/registry.py:415-426` 并配 "Defense-in-depth double-lock" 注释,OL-C03 spec status=implemented 无 xfail,BACKEND_ISSUES §15.1 已标 ✅。`MASTER_FIX_LEDGER_2026-06-27.md` 本就没列(verifier 误指控);典型 memory `aletheia-backend-issues-archive-unreliable` 应用场景——文档标"未修"≠代码真未修,grep 优先。

**文档全量同步**(5-agent parallel workflow + 1 个 adversarial verifier):L2 design 契约(`data_model.md` 加 trade_proposals.commit_hash/parent_hash blockquote + `audit_chain_contract.md` §1 范围扩展 + §6.3 表加 position_adopted 行 + §6.4 两链对照表 + §7.3 doctor 探针 gap + `trust_and_change_control.md §1.1` 末尾加 hash chain 锁定 + `design/README.md` spec 表纠正 spec_data_model.py 8→18 + 补齐 spec_audit_chain.py / spec_pipeline_adopted_position.py 漏列);L4 status(本会话 4 文档 prepend);docs/references 同步(INDEX §3 加新研究报告 + openalice_prism_review.md 标 ✅);新建 `docs/issues/TAG_BORROW_BACKLOG.md` 列剩余借鉴清单状态。

**验证**:DM-S08/S09/B06/C02 + PL-ADOPT-09 全 GREEN;邻近 57 specs passed;全量 1802 passed / 3 skipped / 0 failed(2:08,与 baseline 一致零回归);conformance 360→361 / 99% coverage 维持;doctor `trust.audit_chain` 通过。Merge 进 master 时与 master 同日的 lesson guard fields 加列 (`activated_at`/`dry_run_until`/`expires_at` 进 ps_lessons,见下一章) 在 `_ADDITIVE_COLUMNS` 列表末尾共存,无逻辑冲突。

**剩余 TaG 借鉴清单**(`docs/issues/TAG_BORROW_BACKLOG.md`):
- P0-2 执行后 state_after 账户全量快照(`gateway.py` 已有 audit 写入点加字段;触 trust boundary,需 owner 显式批准)
- P0-3 Guard 责任链拆分(`risk/engine.py:77-332` 20 个 check 拆 `risk/guards/`;trust boundary 红线最严,需 SDD 全套行为零变化)
- P1-1 reject 与 push 同结构(三路拒绝 audit 补 hash 字段;触 trust boundary)

memory:`aletheia-tag-borrow-plan` 已同步落地状态。

---

## Recent Change (Evolve 模块 — Learning 修复 + Guards 子标签 — 2026-06-29)

**背景**：全量 Debug 批次完成后，审计 Evolve 模块下所有子功能的健康状况。

**d85f7b3 — fix(learning): 5-bug Learning module fix — settler/guard/spec/docs**：
1. Signal Settler 挂起 — `settle_due()` 缺独立 scheduler job；新增 `outcome_settler` 15min 定时任务（unconditional）。
2. Guard UI 缺失 — 新增 `POST /settings/guard` 端点 + Settings 页面 "Guard Promotion" 区域含 auto_promote_enabled 开关。
3. `spec_lesson_guards.py` — master 已有完整 4 claims（LG-S01~S04）。
4. LP spec 状态标注 — LP-S01~S06 加 explicit `status="implemented"`。
5. `docs/design/knowledge_contract.md §6.A1` — 更新为 L-Bug-B 已修复后的真实状态。

**7c21813 — fix(ui): post-trade lessons — click-to-expand drawer + trim ellipsis**：
Lessons 列表行可点击展开 DetailDrawer，正文超 120 字符显示 "…"。

**6df3999 — feat(evolve): Guards sub-tab + LessonRow TTL fields + stale comment cleanup**：
1. `client.ts:199` 陈旧注释（称 L-Bug-B 未接通）已移除。
2. `LessonRow` 类型补齐 `activated_at/dry_run_until/expires_at`；lesson 抽屉渲染 TTL 字段。
3. Knowledge Base 新增 "Active Guards" 子标签 — 接通后端 `GET /api/knowledge/guards`（之前零前端消费者）。

**Ledger 核实**（大量"待修"项实际早已修复，ledger 未同步）：
- show_errors yfinance bug、B1 ranker post_trade_review、A9 operator win_rate、L-Bug-D B2/B3 — 均已修复（代码注释明确标注）

---

## Recent Change (负反馈闭环：高维 + 可推翻 self-evolve — 2026-06-28)

分支 `worktree-neg-feedback-loop-fix`。审计发现"用交易结果调整未来交易"的 4 条通路 3 条坏；owner 定调（记忆 `aletheia-negfeedback-high-dimensional-evolve`）：**要高维模式归因 + 可被后续证据推翻的 self-evolve + 全自动，摒弃低维 symbol/direction/scanner 硬拦截**。设计见 `docs/plans/round2_negative_feedback_loop_closure.md`。

**根因 + 修复（5 项核心）**：
- **Path 6 反思**：`reflection.py:62/65` 重复 `opportunity_id=` kwarg → 整模块 `SyntaxError`，被调用方 `try/except` 静默吞 → record_decision/get_last_reflection 双失效、doctor 不报警。提交 5be7826（A13）引入。修=删重复行 + `test_agent_shell::test_reflection_module_imports_cleanly` import smoke 锁回归。
- **缺陷②脏数据带偏**：三学习模块用滚动 30/90 天全量、含 Round1 脏数据 → 学出"全 contra"。修=新增 `analytics/learning_window.clean_window_cutoff`（floor=2026-06-25T15:22Z），insight_synthesizer/confidence_calibration/reasoning_feedback 三处取数接入。
- **缺陷③不会自我纠错（owner 最在意）**：高维 memo 系统已全自动注入决策链，但 **Module J 缺失**——无回溯评估、memo 仅 72h 时间衰减、`MAX_DIRECTION_BIAS_STRENGTH` 死常量。修=新增 `analytics/memo_effectiveness.evaluate_prior_memo`（回溯评估上一 memo regime_playbook 预测 vs 实际→accuracy 写回 tags→overturned 注入新 memo `prior_corrections`→render 进 observer/PI prompt）+ enforce bias cap（单边占比>0.75 force balanced）。
- **缺陷①高维退化**：`current_regime`/`_current_regime` 读 macro `risk_regime` 而非 `market_regimes.state` → positions.regime_tag 全 neutral/unknown，regime_playbook 名存实亡。修=两函数优先读 market_regimes.state（fallback macro→unknown），所有 broker 经 enrichment 受益；reconciler adopt 旁路（38 NULL）留后续。
- **不做**：低维硬拦截（guard predicate / risk engine block_*），按 owner 定调。

**SDD**：新增 `tests/specs/spec_insight_feedback_loop.py`（IFL-S01/B01/B03/S02/B04/B05/S03，7 claims 全绿）+ 接入 test_spec_conformance；ROADMAP Self-Evolution Tier2/2.5 标记 DONE（修文档-代码背离）。

**附带修复（owner 追加，4 个 pre-existing 全量失败，与上面无关）**：
- `settings.py` GET /settings 漏 `serenity` section（schema+POST+前端控件已就绪，仅 GET 漏）→ 加一行。
- `repositories.py` 缺 `prune_agent_shell_{heartbeats,events,messages,artifacts}`（scheduler 调用但 ba80a62 不完整重构未实现 → cleanup AttributeError 被吞、连带 data_pruned audit 没写）→ 加 4 函数（was_in_tx 模式）。
- `test_serenity_workbench::test_promote_theme` 过时（断言旧 `draft`/`proposalCandidateId`，代码已是 Phase3 `submitted_to_pi`）→ 改测试匹配契约（代码对、测试漂移）。

**验证**：全量 1762 passed / 0 failed / 3 skipped（修复前 1758+4 failed）；architecture_fitness 11、spec_conformance 全绿；零回归。
**已合并主线**：3 commit（feat(learning) 负反馈 / fix 4 测试 / fix(operator,pi) PI 审计）+ merge master。待主仓 `aletheia gateway restart` + `doctor` 生效（worktree data/ 为空）。

---

## Recent Change (AI Berkshire 整合 — 2026-06-28)

**目标**：把 GitHub xbtlin/ai-berkshire 的价值投研框架模式吸收进 Serenity Lens。

**5 个子功能（SDD first，全部 GREEN）**：

- **S1 多透镜研究** (`087ec9d`)：4 个视角(technical/flow/sentiment/macro)顺序 LLM 调用
  替换原单次 Stage 1 调用；每条 evidence 带 `agent_lens` 字段标记来源视角。
  顺序设计因 Databricks rate limit（并发 → 429 全灭）。

- **S2 可研究性评级** (`cdad0c3`)：`assess_researchability_grade()` 确定性算法产出 A/B/C
  评级；A=≥2 high_confidence+URL，B=≥3 likely，C=其余；C 级上限封顶 score≤40，
  防止低质证据撑出高分机会。

- **S3 来源可靠性天花板** (`372fda3`)：`SOURCE_RELIABILITY_TIER` 软天花板；news/social→
  likely，market_data→high_confidence，official→disclosed；阻止来源本身可信度不足
  的证据被评为 disclosed（e.g. "看了新闻就说已公开披露"）。

- **S4 Forced Verdict** (`cdad0c3`)：每个 opportunity 必须带 `verdict_json`（含
  recommendation/bull_case/bear_case/key_assumption/invalidation）；防止 AI 空谈
  不下判断。

- **P1 key_assumptions** (`ddad0cb`)：`TradingDecision` 新增 `key_assumptions: list[str]`
  字段；PI 被要求写出≤3 条核心前提假设；落库至 `pi_decisions.key_assumptions`（JSON
  数组）；reflection.py 同步持久化。

- **U1/U2 前端显示** (`c14469e`)：SerenityLens 机会表格新增 Verdict(彩色徽章)/Grade(A/B/C)
  /可展开 bear_case；证据表格新增 Lens 列（agent_lens 标签）。

**spec 覆盖**：303 claims GREEN（ML-S01~S03/B01~B02 + SE-S06/I06 + SV-S03/S04/B02 + 迁移修正）。

**待合并主仓**：分支 `worktree-lazy-drifting-clarke`。合并后须在主仓 `gateway restart`
才能生效（worktree data/ 为空，见记忆 `aletheia-worktree-gateway-empty-data-trap`）。

**剩余 P3 项（数据积累后再推）**：
- D1: xStock 可研究性数据质量管理（等 researchability_grade 数据积累后再做）
- D2: evidence URL 提取增强（LLM prompt 改善，等运行数据后迭代）

---

## Recent Change (Equity Curve 误报 + 诊断代理陷阱勘误 — 2026-06-24)

**前端 bug**:`PnLChart.tsx` useQuery 未处理 `isError`,请求失败时 data 为空 →
chartData.length===0 → 走"还没平仓"空态文案,与 DB 里 68 笔平仓事实矛盾。
违反"onError 必须处理"。修复 dbde229:isError 分支显示 "Failed to load equity
curve." + Retry 按钮。

**勘误**:dbde229 commit body 把诊断时 curl 探活的 502 错误地外推为"浏览器
走系统代理被转坏",并建议用户"代理软件绕过列表加 127.0.0.1"。用户当场指出
后实测纠正:WinINET 系统代理 `ProxyEnable=0` 且 127.* 已 DIRECT,浏览器直连
无碍;只是**环境变量代理** `HTTP_PROXY=127.0.0.1:7897`+NO_PROXY 空,导致 curl
误把 localhost 丢给代理产生 502 假象。记忆 `aletheia-localhost-probe-proxy-trap`
已写入。以后诊断:curl 探活 localhost 必须 `--noproxy '*'`;后端死活以 gateway
status + /api/health 的 status 字段为准。

不 amend dbde229 历史,以 DEVLOG 2026-06-24 条勘误为准。

---

## Recent Change (最小赚钱循环修复 — 2026-06-23→24)

> **背景**：用户观察到"系统不怎么开仓"。逐层排查发现 PI Agent(Phase 4a 后唯一
> proposal 创建者)被**五层叠加阻碍**卡死,20h+ 零开仓。每修一层暴露下一层。
> 最终全部清除后 PI 恢复正常多空交易(02:00+ 5 trade/2 skip,3 笔持仓)。

**五层阻碍 + 修复(按发现顺序)**：
1. **OKX contractSize 单位混淆** (`650f608`)：CCXT swap `amount`/`contracts` 单位是
   合约张数非币数,放大 contractSize 倍(TRX 1000×)且绕过风控 notional。
   okx/client.py 边界换算 coins↔contracts。
2. **PI 余额幻觉破产** (`6c006cb` 冻结例外#1)：8 个 context-injection bug 喂 LLM
   错误事实(余额读 bitget 闲置$167 vs 真实 okx_demo $88k / 统计被 cleanup 污染 /
   kill_switch AttributeError / 策略 status-vs-state typo / win_rate 误读为 drawdown /
   bias 标签 / debate 公式丢 initial)。trading_context + trading_decision 修。
3. **OKX 跨 loop 回归** (`d6fc427`)：`_contract_size` 的 `load_markets` 在 `_run_coro`
   新 loop 跑,但 exchange 在 __init__ 旧 loop 建 → 跨 loop Future → contractSize
   回退 1.0 → contractSize 修复破功。OKXClient 改 lazy-init(`_ensure_exchange`)。
4. **日损 $1 误熔断** (`5831bd1` CR-RE-03)：demo 日损门控误用 `max_live_limited_cap`
   ($100×1%=$1)作基数,清账 -23.26 损失触发熔断阻断当日全部交易。engine.py:154
   按 `_is_live()` 分流,demo 用 `starting_capital_usdt`。config starting_capital→88000。
5. **signal 重复→"数据损坏"skip** (`edc15b2` 冻结例外#2 + `079bb9d`)：paper_trader 写
   signal_pending 后不标 opportunity=reviewed → 每 5min 重复入选 → PI 读重复判
   "data integrity compromised"。paper_trader.py:153 加 update_opportunity(reviewed);
   清理 1676 条历史重复;新增 prune_pi_decisions(7d,保护决策行)防堆积。

**修改文件(生产代码)**：okx/client.py / okx_broker.py / exchange_sync.py / pnl.py /
risk/engine.py(+models/migrations proposal_execution_mode 列) / strategy_lifecycle.py /
reconciliation.py(OKX/Binance live 订单告警) / paper_trader.py / trading_context.py /
trading_decision.py / instruments.py(tradable_symbols canonical) / fallback.py(_MODEL_HEALTH 锁) /
manager.py(daily_review 质量门) / market_data.py(xStock infra_event) / jsonl.py(tz 后缀+guard) /
doctor.py(_probe_profit_loop 探针) / repositories.py(prune serenity/shadow/pi_decisions) /
settings.py(HealthCard 422 修) / analytics.py(equity-curve uPnL) / 前端 Settings.tsx+client.ts

**冻结例外(3 次,用户授权,登记 EXPERIMENT_PROTOCOL)**：#1 profit-loop 8 bug /
#2 PT-1 signal 去重 / #3 CR-RE-03 日损基数。Round 1 起点保留 6/22,**所有修复
生效前的数据不计入策略评估**,6/24 起为干净观察窗口。

**监测固化**：doctor `loop.profit` 探针(opportunities 流动但 proposal 停滞 6h+ 即告警) +
`spec_pipeline_profit_loop.py` E2E。这类静默断流再不会无声发生。

**验证**：全量 1651 tests passed;生产路径 E2E(DOGE demo 单 notional $11.84 正确);
loop.profit ✓ healthy;PI 恢复 trade 决策(SOL/ETH/BTC short,notional 规格正确)。

---

## Recent Change (SDD 体系扩张 + 全面代码审计 — 2026-06-23)

> 在修 bug 之外,本会话还做了 SDD 文档体系的大规模加固 + 一次全面代码审计。

**SDD 扩张**：spec claims 45→114;新建 4 份 spec(api_reference/data_layer/operator_loop/
subsystem_observers) + 3 份契约(knowledge/scheduler/audit_chain);修 12 份契约漂移;
扩 6 份现有 spec 到 L2b/L2c。详见 `docs/research/DOC_CONTROL_ANALYSIS_2026-06-23.md`。

**代码审计**：16 契约 spec↔code 对账 + 7 管道 E2E + git log 挖掘 → 74 findings,
Top 25 修复优先级。详见 `docs/research/CODE_AUDIT_REPORT_2026-06-23.md`。已修 trust 项
(OL-C03 双锁/CR-RE-01 proposal_execution_mode/CR-EX-01 reconcile 告警/CR-DM-01 ID 决议)。
未决项分类登记 BACKEND_ISSUES §15-18(trust 待授权/frozen 待 Round2/卫生下 sprint)。

**Code review(11 角度)**：修 2 个 get_cached_state 改动遗漏(exchange_sync None 防御 +
inspect_cmd 兜底);否决 6 个 agent 误报(混淆历史 diff)。新发现 OP-WINRATE
(operator_loop fills 无 realized_pnl → win_rate 恒 0,pre-existing,Round 2) + APP-HMAC
(tunnel auth timing side-channel,待修)。

---

## Recent Change (Exchange 统一选择 — 2026-06-20)

> **目标**：消除三轴分裂（market_data.ccxt_exchange / exchange.paper_execution_mode / exchange.name 各指不同交易所）。
> 新增 `POST /api/settings/primary-exchange`，一次调用原子同步全部 4 个字段。

**根因**：系统原本允许用户在不同地方单独配置数据源和执行交易所，导致产生三轴不一致的状态（如 name=bitget，ccxt=okx，paper_mode=okx_demo）。

**修改文件**：
- `aletheia/server/routes/settings.py`：新增 `_EXCHANGE_UNIFICATION_MAP`（okx/bitget/binance 三个映射）+ `PrimaryExchangeRequest` + `POST /primary-exchange` endpoint；paper_internal 选项只改 paper_execution_mode，不动 name/ccxt；直接写 `cfg.risk.allowed_exchange`（绕过 `_RISK_IMMUTABLE`，这是设计）
- `aletheia/diagnostics/config_consistency.py`：扩展 `_DEMO_CRED_MAP` 覆盖 okx_demo + binance_testnet；新增 `_check_exchange_consistency` 检查 name≠ccxt 时输出 info warning
- `frontend/src/api/client.ts`：新增 `setPrimaryExchange()` API 方法
- `frontend/src/pages/Settings.tsx`：新增 `PrimaryExchangeCard`（4-tile 选择器，放在 ExchangeConnectionCard 前）；ExchangeModeRow hint 改为动态；Trading Mode → "Trading Mode — Advanced"
- `frontend/src/index.css`：新增 `.exchange-tiles` / `.exchange-tile` / `.exchange-tile-active` 等 CSS

**验证**：
- `npm run build` 0 error（777 modules）
- `pytest -k "config or settings"` 79/79 pass
- API smoke test：POST `{"exchange_id":"okx"}` → 4 字段全部变为 okx；切回 bitget → 全部变为 bitget

**xStocks 例外**：3 处 `CCXTProvider(exchange="bitget")` 硬编码（ON-series 代币仅在 Bitget 上市），未动。

---

## Recent Change (P1 竞品 Gap 三功能 — 2026-06-20, c1e67a5)

> **目标**：竞品差距分析 P1 — 加速策略迭代：(A) 本地历史数据避免每次回测拉 API；
> (B) Optuna Bayesian 搜索替换暴力 param_sweep；(C) 事件驱动 30s 价格/量监测 + 触发。

**Feature A: 历史数据管理**
- `aletheia/data/history_downloader.py` (新建)：`download_symbol_history()` 增量下载 + `check_coverage()`；分页 500 bars，0.35s 限速，检测 `get_ohlcv_newest_ts()` 决定增量起点
- `aletheia/storage/repositories.py`：`upsert_ohlcv_candles` 新增 `window=None` 动态裁剪；新增 `get_ohlcv_coverage()` + `get_ohlcv_newest_ts()`
- `aletheia/strategy/backtest.py`：`load_bars_from_db(limit=None)` 读 config `max_bars_per_pair`
- `aletheia/agent_runtime/scheduler.py`：`_run_ohlcv_nightly_backfill()` + cron 注册（CST 03:30）+ price_feed timeout 25s→45s
- `aletheia/cli/data_cmd.py` (新建)：`aletheia data download SYMBOL` + `aletheia data coverage SYMBOL`
- `aletheia/main.py`：挂载 `data_app` typer

**Feature B: Optuna 智能参数优化**
- `pyproject.toml`：新增 `optuna>=3.6`
- `aletheia/config/schema.py`：`StrategyFactoryConfig` 新增 `sweep_use_optuna=False`、`sweep_n_trials=60`、`sweep_optuna_timeout_sec=120.0`、`sweep_top_k=10`
- `aletheia/strategy/param_sweep.py`：`_run_optuna_sweep()` 用 `TPESampler(seed=42)` + `MedianPruner`；`run_parameter_sweep()` 按 flag 派发；`_run_brute_force_sweep()` 提取原逻辑
- Settings 前端 + route：4 个新字段复用 `POST /strategy_factory`

**Feature C: 事件驱动触发**
- `aletheia/config/schema.py`：新增 `EventTriggerConfig`（enabled / price_move_pct_threshold / volume_surge_multiplier 等）
- `aletheia/data/price_feed.py`：缓存结构扩展 `prev_price` + `volume_24h`；tick-gating（universe 每 3 tick/90s）；`_check_event_triggers()` 检测价格/量突破；`_notify()` 封装
- 新增 `POST /settings/event_triggers` route

**测试**：17 new tests 全绿（test_history_downloader.py × 5、test_param_sweep_optuna.py × 3、test_event_triggers.py × 9）

---

---

## Recent Change (Exchange-native TPSL bracket — 2026-06-20, f9e8dfb)

> **目标**：竞品 Gap 分析后，实施最高 ROI 改进：在 Bitget 交易所层面挂 SL + TP trigger
> orders，让止损/止盈在价格触及时毫秒级响应，不再依赖 scheduler 5-min 轮询。

**设计决策**：
- 两个独立 trigger（`pos_loss` + `pos_profit`）而非 combined `profit_loss` planType，
  因为 Bitget OCO 联动语义文档不明确，独立挂单行为确定。
- 本地 position_manager 轮询保留为 fallback（belt-and-suspenders）。
- 失败不阻塞开仓：TPSL 挂单失败仅 log warning，position 仍正常创建。
- 功能开关 `exchange_tpsl_enabled: bool = False`，需用户在 Settings 前端显式开启。
- Exchange-triggered close 检测：`_sync_exchange_closed()` 在每次 evaluate 周期开头
  调用 `get_positions()` 对比，发现本地持仓已不在交易所 → 以 `exchange_bracket_triggered` 关仓。

**文件变动**：
- `aletheia/exchanges/bitget/client.py`：新增 place_tpsl_order / cancel_plan_order / get_pending_plan_orders
- `aletheia/execution/bitget_broker.py`：_try_place_tpsl / _try_cancel_tpsl / replace_exchange_stop；
  execute_live_order 新增 stop_price + take_profit_price 参数；平仓前取消 bracket
- `aletheia/execution/gateway.py`：demo + live bitget 分支均计算 TP 并传 broker
- `aletheia/portfolio/position_manager.py`：_sync_exchange_closed；trailing stop 联动 replace_exchange_stop；
  close_position 改为 top-level import（修复 patch 路径以支持测试）
- `aletheia/storage/migrations.py + models.py`：exchange_tpsl_order_id TEXT 列（additive）
- `aletheia/config/schema.py`：exchange_tpsl_enabled: bool = False
- `aletheia/server/routes/settings.py`：TradingConfigUpdate 新增字段
- `frontend/src/pages/Settings.tsx`：Exchange-native TPSL Toggle UI
- `tests/test_tpsl.py`：11 tests，全部通过

**也修复了 4 个 pre-existing test failures**（commit e069d56）：
- Universe.tsx Scanner Calibration 表：6 个 inline style → CSS class
- Settings.tsx UniverseConfigCard：去除多余 onDirty prop
- index.css：新增 button:active { transform: none; }
- docs/_ai/PROJECT_CONTEXT.md：补充 :8787 端口

---

## Recent Change (Trade Analytics 面板 + Batch 2 bug 修复 — 2026-06-19, 0b3b1a6)

> **目标**：在 Trades & Positions 页面增加 Analytics sub-tab，可视化展示
> 交易模式差异（RuleSet vs Opportunity）、scanner family 表现、持仓时间分布。

**Batch 2 Code-Review 修复（4 个 bugs，优先于 analytics 实施）**：
- **CRITICAL** `aletheia/storage/migrations.py`：`_ADDITIVE_COLUMNS` 补入 `("atr_pct", "universe_members", "REAL")`；
  缺失导致现有 DB 升级后 ATR 仓位定规模链路静默失败（`_atr_adjusted_notional()` 读到 None）。
- **HIGH** `aletheia/agents/strategy_designer.py`：`_covered` 集合改为仅含 active 状态策略；
  原代码把 retired/failed 策略也排除，导致 ranker 优先给已失败币种生成新策略。
  同时用 `to_bitget()` 标准化符号，消除 `BTC/USDT` vs `BTCUSDT` 比较总为 False 的 bug。
- **MEDIUM** symbol 格式：`strategy_designer.py` 原用 `.replace("/","")` 手动转换，
  违反 CLAUDE.md canonical symbol 规则，改为 `to_bitget()` 统一入口。
- **INFO** `aletheia/universe/allocator.py`：`get_family_multipliers()` 文档字符串更正
  （"momentum/derivatives → 4h" → 实际 "momentum → 4h, derivatives/mean_reversion/volatility → 1h"）。

**Trade Analytics 面板**：
- `aletheia/analytics/trade_stats.py`（新建）：`build_trade_stats()` 聚合已平仓
  持仓，按 source type / family / hold-time 分桶。RuleSet 判断用
  `json_extract(s.parameters, '$.ruleset') IS NOT NULL`（`strategy_evidence.py` 同标准）。
  family 数据复用 `compute_family_scorecard()`。
- `aletheia/server/routes/analytics.py`：追加 `GET /api/analytics/trade-stats`。
- `frontend/src/charts/TradeAnalyticsPanel.tsx`（新建）：4 区块 CSS bar chart 面板，
  `useQuery(staleTime=60s)`，零新依赖。
- `frontend/src/api/client.ts`：追加 `getTradeStats()` 及 TypeScript 接口。
- `frontend/src/pages/TradesPositions.tsx`：用 `<Tabs>` 拆出 Blotter / Analytics 两个 tab。
- `frontend/src/index.css`：追加 `.analytics-*` CSS 类。
- `aletheia/dashboard_static/`：重新构建（203 modules）。

**验证**：`GET /api/analytics/trade-stats` 返回正确 JSON（59 trades，by_source/by_family/
hold_distribution/totals 字段完整）；前端构建 0 error；gateway 已重启。

---

## Last Updated (前次)

2026-06-17 (Self-Evolution Tier 1 实施完毕；worktree 待合入 master)

---

## Recent Change (Self-Evolution Tier 1 — 2026-06-17, worktree-self-evolution-tier1)

> **目标**：关闭 `universe_forward_outcomes` → knowledge base 反馈回路。
> 此前坏结果有数据却从未反哺规则；Tier 1 是零 LLM 成本的最低风险起点。

**核心逻辑**（`aletheia/universe/outcome_recorder.py`）：
- `settle_due()` 结算后调用 `_maybe_generate_guard_candidates(settled_rows)`
- 只处理 `horizon=4h`；方向感知阈值：long `return < -3%`，short `return > +3%`
- 7 天内同 `symbol+scanner_family` 去重（`repositories.guard_candidate_exists_recent()`）
- 生成 `medium_confidence` guard_candidate（不进 FTS 检索，需人工确认升级）
- 失败只 `logger.warning`，不影响 `settle_due()` 返回值

**修改文件**：
- `aletheia/universe/outcome_recorder.py`：`_GUARD_HORIZON` / `_LOSS_THRESHOLD_*` 常量 + `_maybe_generate_guard_candidates()` + `_build_guard_candidate_content()`
- `aletheia/storage/repositories.py`：`guard_candidate_exists_recent()` dedup 查询
- `tests/test_self_evolution_tier1.py`（新建）：10 个单元测试，全通过

**文档整理**（第二个提交）：
- `docs/_ai/SELF_EVOLUTION_DESIGN.md` → `docs/plans/self_evolution_plan.md`（移入 plans/，加明确日期）
- `docs/status/ROADMAP.md`：新增 Self-Evolution 横切轨道（Tier 1/2/3 状态表）
- `docs/_ai/CURRENT_TASK.md`：待办第 0 项写入 Tier 2（2026-07-01）、Tier 3（2026-08-17）

**验证**：用 master `.venv` 跑全量 suite，**1311 passed, 0 failed**。

**待操作**：worktree 分支 `worktree-self-evolution-tier1` 尚未合入 master，
因 master 有未提交的 in-progress 工作（equity observer、futures integration 等）。
合并命令：`git merge worktree-self-evolution-tier1 --no-ff`（处理完 master 未提交变更后执行）。

---

## Last Updated (前次)

2026-06-16 (项目级 code-review:契约漂移 + 运行态正确性,15 findings → 修复)

---

## Recent Change (项目级 Code-Review 修复 — 2026-06-16, uncommitted)

> 全项目 code-review(契约对应 + 运行态正确性 + 潜在 bug)。15 条确认 findings:
> 7 条机械修复已先行落地,4 条设计级(A/B/C/D)在本轮完整实现。

**A — universe high-confidence 标签不可达**
- 根因:`signal_emitter._HIGH_CONF_THRESHOLD=70`,但 allocator 总分天花板 ≈68.5
  (opportunity 0.35×100 + tradability 0.25×100 + liquidity 0.15×90 − min cost 5.0),
  所以每个 deterministic opportunity 都被强制 "medium","high" 永不出现。
- 修复:阈值降到 60,保留有意义的 medium=[45,60) / high=[60,ceiling] 区分。

**B — forward outcome 只能给 BTC/ETH/SOL 定价**
- 根因:`settle_due` 只读存量 market snapshot,而 snapshot 仅覆盖静态观测的
  3 个标的;轮动 universe 成员全部 None → 标 `failed`,校准永远攒不到数据。
- 修复:`settle_due`/`_batch_fetch_prices` 改 async,用共享 CCXT provider 给全部
  symbol 取 live ticker(provider 关闭一次),snapshot 仅作兜底。

**C — calibration 胜率对 short 方向算反了**
- 根因:`calibration_stats` 把 `forward_return_pct > 0` 一律算赢,但 short 仓在
  价格下跌时才赢 → derivatives/mean_reversion 的 short 选择被系统性误判。
- 修复:`UniverseForwardOutcome` 加 `direction` 列(model + 迁移:CREATE TABLE
  与 `_ADDITIVE_COLUMNS` 双路,startup-only,幂等);`service.refresh` 用
  `_infer_direction` 写入方向;`register_pending` 落库;胜率 SQL 改方向感知
  (long/NULL: return>0 赢;short: return<0 赢),`positive_count`→`win_count`
  (语义诚实,short 赢在负收益),`routes/universe.py` 同步改名。

**D — 设置保存后刷新间隔不生效**
- 根因:APScheduler IntervalTrigger 添加后不可变,保存的 `refresh_interval_min`
  要等下次重启才生效。
- 修复:新增 `scheduler.reschedule_universe_refresh(interval_min)`(锁内
  `reschedule_job`,job 不存在/异常返回 False 不阻断保存);`settings.py` 在
  `refresh_interval_min` 变化时调用,返回 `rescheduled` 标志。

**Tests**: 新增 `tests/test_universe_calibration.py`(3 方向感知用例);改 signal_emitter
阈值用例。ABCD 受影响测试单独跑 36/36 全绿,且未新增 DB writer / scheduler job。

**顺带修复(同批)**:`test_startup_gate::test_empty_response_not_usable` 原失败——测试桩
`_FakeResp` 缺 `.raw`、与生产 `startup_gate.py:64`(`resp.raw` 兜底判定)漂移。给
`_FakeResp.__init__` 加 `raw=None` 参数对齐真实 `ModelResponse`,零生产代码改动
(详见 `docs/issues/BACKEND_ISSUES.md` §6.5,已标 ✅)。修复后 `test_startup_gate` 14/14 绿,
全量基线恢复全绿。

## Recent Change (Phase 0 节奏调整 + 文档统一 — 2026-06-16)

> **节奏调整**:取消专门隔离 72h 不 commit 的观察窗口,改为边开发边持续观察闭环健康。
> `code_drift` 自愈重启是预期行为;Phase 间不再硬性串行;Phase 6 Live 仍需显式授权。

- `docs/_ai/CURRENT_TASK.md`:重写 Phase 0 段(binary gates → continuous trends),Pi Agent 段压缩为摘要
- `docs/status/ROADMAP.md`:Phase 0 / Track B / 红线调整,去掉"Phase 0 门全绿"前置
- `docs/_ai/PROJECT_CONTEXT.md`:全面重写(777→1295 tests, 新增 operator/edge lab/universe 等里程碑)
- `docs/_ai/DEV_PROTOCOL.md` §4:删除"战略冻结"段,替换为"边开发边观察"
- `docs/_ai/AI_HANDOFF.md`:压缩 2447→<500 行(50+ Operator 增量条目合并为一段摘要)
- `docs/_ai/REPAIR_PLAN.md`:标记归档(全部 Wave 完成)

> **Model provider thinking-mode fix** (uncommitted):
> `openai_compatible.py:145` + `routes/model.py:259`: `max_tokens` 5/10→200,
> thinking-mode 模型把 budget 花在推理上产出空 text → 误判 unavailable。

## Recent Change (P1 FTS5 + Universe + UI — 2026-06-16, commits cb9692e..5f2433f)

- **FTS5 experience-loop fix** (cb9692e): startup `reindex_all()` backfill + doctor `fts.coverage` probe + 5 tests
- **Universe forward outcomes**: 15m/1h/4h/24h 收益追踪 + `GET /api/universe/outcomes`
- **Deterministic opportunity path** (a0c7cfa): `signal_emitter.py` — universe members → Opportunity (无 LLM); 32 tests
- **Test fixture refactor** (ed1015d, f113230): LLM-skip behavior alignment + startup_gate sleep skip (21min→9min)
- **Universe snapshot TTL** (d0dc48d, 8f69a44, 5b61fe5): 24h TTL + stale markers + Settings TTL control
- **Model provider**: thinking-mode content extraction + inference probe fallback (14321f1, 540c0ab)
- **Settings UI**: real values instead of placeholder hints (5f2433f)
- Tests: 1295/1295 pass

## Recent Change (决策层重构 — 2026-06-15, commits ef3e765, 73703ef)

- **RuleSet-driven trading**: `ruleset_scanner.py` 从 OHLCV 直接生成 entry/exit 信号
- **PI Agent conviction sizing**: structured LLM 评估 → adversarial debate → fractional Kelly
- **paper_trader v3**: Path A (RuleSet multi-strategy) + Path B (opportunity fallback)
- **Strategy lifecycle automation**: auto-promote / auto-pause

## Recent Change (Edge Lab — 2026-06-14, commits 9fe5c5f..ab4a522)

- Shadow trades (Top-5 反事实), configurable slippage (5bps), BTC benchmark, regime tags
- Family scorecard API (`GET /api/analytics/family-scorecard`)
- 11 tests; SQL fix (`scanner_family` JOIN path)

## Recent Change (全局代理配置 — 2026-06-14, commit 28eb357)

- `aletheia/net.py`: httpx/aiohttp 统一代理工厂
- 13 文件 30+ 处调用点替换; Settings UI NetworkCard; doctor `network.proxy` probe

## Recent Change (9域数据收集层 — 2026-06-14, commit 84a66fd)

- microstructure/derivatives/macro collectors + normalizer + context_builder
- 5 DB tables, scheduler 2min tick, 16 tests
- market_observer 自动追加 enriched context block

---

## Operator Layer Track B — 累积实现摘要 (2026-06-11, 50+ 增量)

> 以下是 2026-06-11 期间在 `operator-shell/` + `aletheia/agent_shell/` + `aletheia/server/routes/operator.py`
> 上完成的大批量增量工作的压缩摘要。逐条原始记录已归档;详见 git log 该日期范围。

**Python truth layer**:
- `/api/operator/internal/context/query` (分级安全 context)
- `/api/operator/internal/commands/execute` (typed command loopback)
- `/api/operator/internal/decisions` + `/repair-decisions` + `/command-intents`
- `/api/operator/extensions` (inspect/enable)
- `/api/risk/summary` (boundary change detection)
- `/api/strategies/summary` (promotion/evaluation detection)
- `/api/reviews/summary` + `/api/dreaming/summary`
- Session-kind child sessions (diagnostic/dreaming/strategy/review/command)
- Command audit persistence + inspectable working-memory files (JSONL/Markdown)

**Node Operator Shell**:
- `OperatorLoop` with 3-tier cadence (10s/30s/5min)
- `RecommendationEngine`: 15+ recommendation types
- `TaskRuntime`: priority queue, in-flight task re-entry, permission-aware materialization
- `RepairEngine`: proposal-only decisions (9 specialist paths)
- `SessionRuntime`: budgeted recovery folding, active branch persistence, parent trace inheritance
- Heartbeat mode state machine (Observing/Trading/Reviewing/Dreaming/Repairing/Degraded)
- Extension sandbox: draft → test → propose → enable → slash dispatch

**Command Center frontend**:
- SSE-first event feed
- Authoritative command preview (Ready/Blocked/Query/Forbidden)
- Structured trace/result cards
- Context inspector
- Session tree with nested branch create/select
- Cross-session command search
- Enabled extensions panel

**Tests**: 154 Python operator tests + 80 Node tests; builds pass.

---

## Historical Changes (2026-06-13 and earlier)

### 2026-06-13 — Gateway 静态文件启动可靠性 (db2423f)
- preflight.py 启动预检 + lifespan mount + doctor webui.static 诊断

### 2026-06-13 — DX 收口 (18 commits)
- Single-port :8787 dev (vite build --watch + uvicorn --reload)
- LLM startup gate 3次重试 + Windows toast
- Native Anthropic + OpenAI provider (SSE 流式)
- symbol_list 双模式过滤
- Clustr100 社区知识库

### 2026-06-13 — Persistence/Sync/Recovery (1075c8e)
- 18 新模块: EventStore, StateStore, ArtifactStore, GatewayFSM, Reconciliation, Recovery, Checkpoints, Export/Import, Backup/Restore, Learning/Policies
- 15 张 `ps_*` 前缀表(additive overlay)

### 2026-06-12 — 账户级仓位发现 + 纳管 (P1-P2)
- position_reconciler.classify_positions() + adopt_position()
- source 列(native/adopted) + 诚实合成链
- trust §1.1 修订

### 2026-06-21 — PI Agent 信号增强 + Liquidation 修复 + Operator ICT 研究 (ce0360d)
- Task A: UniverseFeatureSnapshot 新增 4 列 (taker_buy_ratio/vol_ratio/relative_strength_24h/oi_price_divergence)
  features.py 写入; trading_context.py Layer 1 读取+crowding_signal; trading_decision.py prompt 增强+_SIGNAL_RULES
- Task B: derivatives.py 日志提升 debug→warning; 时间窗口 1h→4h; "0 in window" 诊断; scheduler persist 计数日志
- Task C: operator_loop.py trigger_strategy_research action (每日限频1次); research_worker.py _ICT_METHODOLOGY_SYSTEM +
  mode="methodology" 分支; trading_context.py Layer 3 注入 serenity_opportunities; trading_decision.py 渲染 Research-sourced 小节

### 2026-06-10 — CLI 分层 + 运营命令软弃用
- bootstrap 内核(gateway/doctor/version)保留
- 运营命令(permission/model/risk/strategy)指向 Web UI

### 2026-06-08 — 多轮基础设施修复
- Gateway ghost-socket 根治 (a90ea84)
- Markets K线动态加载 (scroll-back + live tail)
- Deep Research → ROADMAP v1
- Market context layer (multi-TF indicators → LLM)
- Exchange account sync
- Experience loop (FTS5 retrieval → agent prompts)

### 2026-06-07 — WebUI 结构性重设计
- 14→10 pages (5-phase IA)
- CausalChain inspector
- Strategy↔trade causal chain fix (4 places)
- §10 re-audit (8 false-positive fixes)

### 2026-06-06 — 项目初始化
- CLAUDE.md + docs/_ai/ 全套
- Backend 80+ fixes + Frontend 24 fixes (§1-§3)

---

## Commands to Run

```bash
uv sync
uv run pytest -q                              # 1295+ tests
uv run aletheia gateway start --no-browser    # 启动闭环
uv run aletheia doctor                        # 取真实事实
```

## Warnings

- Live trading is disabled by default. Never enable without explicit user confirmation.
- Trust boundary modules (risk/, execution/, auth.py, secrets.py) require user authorization.
- Frontend must be rebuilt after source changes: `cd frontend && npm run build`
- After backend .py changes: `uv run aletheia gateway restart --no-browser`

## 2026-07-11 — Agent 工作交接机制加固

- 完成记录不再由工具直接写“测试通过”；必须显式执行登记过的命令，且失败不留成功证据。
- 任务只能用当前实现提交上的真实验证完成；交接文件必须包含固定栏目，完成后的下一步统一回到冷启动入口。
- 冷启动会区分当前证据、历史证据、缺失证据和不可达证据，不再把已完成任务的旧开发指令交给新 Agent。
- CI 和版本化提交钩子会拦截“代码/文档变了但任务包或交接没更新”。
- 验证：专项与架构 40 项通过；项目规范 636 项通过。全量 2837 项通过，唯一失败是 `origin/master` 已存在的 Gateway 规范编号重复，本次未触碰 Gateway。

## 2026-07-11 — Observer 运行状态与 LLM 连接恢复

- Market Observer 的正常前置拒绝不再进入 AgentRun 错误通道；拒绝事实继续由 filter evidence 独立保存，真实 provider/异常/预算错误仍保留。
- LLM `ConnectError`、网络读写、远端协议和代理故障进入已有备用模型链；60 秒 timeout 不扩成多模型串行重试，避免突破有界后台任务。
- 全量测试发现并纠正 Gateway stability 中两个同名 `GS-S03` 登记；data collector budget 使用 `GS-S04`，只改规则身份，不改运行行为。
- 后续仍按 `docs/_ai/CURRENT_TASK.md` 的前瞻样本门推进，不因本次可靠性修复自动调整任何筛选参数。
