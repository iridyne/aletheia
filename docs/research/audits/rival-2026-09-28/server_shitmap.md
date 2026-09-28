# Server 去屎山专项扫描

本轮建议 **2 张新问题卡、1 张旧修复遗漏的跟进卡**；另有 5 张开放卡在当前源码仍成立。优先处理 WebSocket 鉴权失败放行（已有 #1332），然后处理模型验证身份错绑和首页 Risk 状态误报。positions 的旧 blob 投影仍在本分支，但 #3631 已关闭，应先核对修复合入情况，不能重复建卡或直接称为主线回归。

## 范围、基线与证据口径

源码固定为 `ed9d8bc330cbdc29eb02006337e24424141fbd29`，审计开始时工作树干净；这也是 CLI 报告提交。当前分支为 `iridite/rival_analysis`，**不是实时 origin/master 或 Fedora 部署状态**。2026-09-28 只读查询 GitHub 作查重。原清单的“从未 review”仅指缺少这份专项扫描，不能推出没有历史缺陷卡。

主 Agent 独立完成，未派发 Agent。覆盖 `core/aletheia/server/` 全部 **29 个 Python 文件、17,278 物理行、206 个 AST ExceptHandler，其中 111 个宽捕获**。物理行含空行、注释和 docstring；宽捕获指裸捕获或捕获类型表达式包含 Exception/BaseException。密度只用于定位，不作为缺陷数量。routes 下有 22 个非空模块和一个空 __init__.py，不能把目录项数当成路由数。

方法是全文件结构扫描（函数、异常分支、入口与大函数）加重点调用链复核，**并非 17,278 行逐行人工签署，也不是安全认证或全路径无缺陷证明**。表中的确认项有当前源码和真实入口/消费者证据；本轮未执行故障注入、接口请求、测试套件或性能测量。仓库没有 `.codegraph/`，使用 AST、源码和调用点搜索。没有访问运行数据库、读取凭证、修改业务代码、启动服务或创建 issue。冻结 Risk 实现未重扫；这里只核对 server 的 Risk 读模型。

五类标签沿用 CLI 报告。“无墙钟”归入契约漂移的可用性子类。P1/P2 是建议优先级；D0 表示建议修复恢复已有语义、Contract Delta NONE，不代表本报告授权实施。

## 发现表

下表路径默认相对 `core/aletheia/server/`，行号绑定上述 SHA。

| # | file:line | 屎点类型 | 语义影响 | 建议动作 | D 级 |
|---|---|---|---|---|---|
| S1 · P2 · 新 | `routes/model.py:267–352`；失败分支 `:370–402` | 契约漂移 | 探测开始使用 A 配置，完成后重新读取 B 配置并生成验证身份，把 A 的结果记给 B；失败也可能污染新配置的验证记录 | 新卡：把身份绑定到实际被测 provider/config，在持久化时用原身份作比较；复用现有身份校验 | D0 |
| S2 · P2 · 遗漏 | `routes/ui.py:160–182` | 契约漂移 | 任意一条历史 RiskDecision 就使首页 bootstrap 返回 healthy，连旧拒绝记录也满足；专用 risk/status 已明确禁止这种推断 | 跟进闭卡 [#1814](https://github.com/iridyne/aletheia/issues/1814) 的兄弟读模型遗漏；统一已有状态投影，不动 Risk 评估逻辑 | D0 |
| S3 · P2 · 新 | `startup_policy.py:8`；`app.py:559–576` | 契约漂移 | 健康接口宣称 scheduler 初始化失败后继续运行，真实 lifespan 会清理并抛错终止启动；现有测试还固定了旧声明 | 新卡：修正诊断目录及其断言以匹配实际启动边界，不把启动改成放行 | D0 |
| S4 · P1 · 已存在 | `routes/ws.py:66–79, 341–348` | 权威路径宽异常 | 读取鉴权配置失败返回 False，WebSocket 跳过 ticket 检查并 accept；前提是该入口可达且本应要求远程鉴权 | 复用开放卡 [#1332](https://github.com/iridyne/aletheia/issues/1332)，配置不可用时拒绝连接 | D0 |
| S5 · P2 · 已存在 | `routes/ws.py:243–254` | 契约漂移（无墙钟） | 共享锁内串行 await send_json；慢客户端可以同时拖住常规和告警广播 | 复用开放卡 [#1855](https://github.com/iridyne/aletheia/issues/1855)，给发送有限 deadline 并走统一断开清理 | D0 |
| S6 · P2 · 已存在 | `routes/universe.py:828–849` | blob 列泄露 | planning-history 返回 repository 全行，含逐次规划输入等明细；responses 文档声明不执行响应字段过滤 | 复用开放卡 [#3642](https://github.com/iridyne/aletheia/issues/3642)，先确认明细消费者，再建立明确列表投影 | D0，保留分页与明细语义 |
| S7 · P2 · 已存在 | `routes/prediction.py:29–36, 52–68` | 契约漂移 | 先取最新 N 条再按 status 过滤；状态总数只统计最新 200 条 | 复用开放卡 [#1529](https://github.com/iridyne/aletheia/issues/1529)，过滤先于 LIMIT，总数用聚合 | D0 |
| S8 · P2 · 已存在 | `routes/prediction.py:136–145` | 契约漂移 | events 的 search 在 repository LIMIT 后执行，较旧匹配项被截掉 | 复用开放卡 [#1530](https://github.com/iridyne/aletheia/issues/1530)，search 下推查询 | D0 |
| H1 · 已关闭卡的分支差异 | `routes/data.py:1064–1111, 1242–1256` | blob 列泄露 | PositionView 仍 extra=allow；PnL view 复制整行，列表保留 reasoning/evidence 等明细字段 | [#3631](https://github.com/iridyne/aletheia/issues/3631) 已关闭，先比对其修复与目标分支，不计新卡 | D0 |
| M1 · 低 | `dev_reload.py:42–45` | 死代码候选 | record_reload 无仓内调用或注册，增量计数入口已经悬空 | 随相关维护删除前再次查消费者，不单独立卡 | D0，等价清理 |
| M2 · 低 | `app.py:410–828`；`routes/analytics.py:1876–2187` | 巨型函数 >300 行 | lifespan 419 行、get_operator_decisions 312 行；生命周期/聚合职责集中，长度本身不证明错误 | 仅在具体修复触及时抽取已有职责，禁止为行数单独重写 | D0，等价整理 |

## 关键调用链与后续最小验收

**S1：持久化的 CAS 无法补救错误的证据身份。** `ping_model` 在等待 broker 之前取得 provider，之后才再次 load_config 并调用 provider_configuration_identity。`model_provider/router.py:211–243` 的 persist_provider_verification 会比较“传入身份”和“当前身份”，但两者此时都来自 B，因此不能识别实际请求发给 A。`model_provider/broker.py:695–730` 的配置失效机制明确保留已接纳工作直至排空；不能假定变更自动取消旧探测。即使缓存关闭使旧请求失败，错误分支也会重新绑定当前配置。配置保存与路由缓存失效分步进行，不能依赖网络时序作正确性保证。

最小验收：挂起 A 的探测，在等待期间替换为不同 URL/model 的 B，再完成 A 的成功、空响应和异常三条路径；B 不得接收 A 的成功或失败验证记录。候选探测在 `extensions/model_endpoints/credential_routes.py:194` 预先取 candidate_identity，可作为现有实现参考，但必须保证身份和实际 provider 属于同一配置快照。此问题影响配置证据真值，未证明它绕过交易或 Live 门禁。[Inference Harness Contract](../../../design/contracts/llm_inference_harness_contract.md) 要求验证精确 endpoint/model，并保持身份漂移保护。

**S2：同一个系统已有正确语义，首页复制了旧判定。** `routes/risk.py:89–125` 默认 not_evaluated，并明确说明不可从历史决定推出当前 admission 健康；`ui._risk_snapshot` 则只检查历史列表非空。`shell_bootstrap` 在 `ui.py:315,335` 发布该值，`frontend/src/pages/Overview.tsx:84–95,297–309` 把 healthy 渲染为 active/green。#1814 已关闭但正文覆盖此类根因；#1184 的 bootstrap 缺失回退是另一触发条件。跟进卡应明确“首页重复投影遗漏”，不泛称整个 Risk 修复失效。最小验收：无记录、旧 approved、旧 rejected、preflight/legacy 记录都不能让清空刹车的状态变成当前健康；真实 brake/warning 仍按现有规则展示。

**S3：诊断说明与执行分支相反。** lifespan 仅在 background_services_enabled 时初始化 scheduler；异常后停止 job manager、清理自己拥有的 PID，抛出 scheduler_initialization_failed。`routes/health.py:454` 暴露 startup_policy_snapshot，而 `tests/test_startup_policy.py:9` 仍断言 warn_continue。本次只读测试源码，未运行。最小验收应同时约束目录投影和真实异常分支，不仅修改字符串断言；不改启动策略。

**S4/S5：鉴权和广播分别追踪。** app 将 ws router 挂到 `/api`；WebSocket 不经过普通 HTTP 请求鉴权 middleware。配置异常使 ticket 分支根本不执行，是已有开放卡，不是新发现。广播的共享 send_lock 覆盖所有客户端的串行发送；只限制客户端数量并不能给发送建立墙钟。后续验收分别注入配置读取失败，以及一个永不完成发送的客户端，证明未授权连接被拒绝、其他客户端和告警仍有界推进。没有实际建立 WebSocket 或发送消息。

**S6/H1：类型声明不等于字段收敛。** `storage/repositories.py:4781–4810` 使用 SELECT pg.*；`repositories_universe.py:813` 的 effective attention 投影保留输入字段，路由再原样返回 page。`responses={200: ...}` 只描述 OpenAPI，不是 response_model 的序列化约束。positions 虽有 response_model，但其 extra=allow 且显式含 reasoning；`portfolio/pnl.py:1338–1354` 以 dict(row) 开始，不去掉 blob。#3631/#3642 的 MB 数字是旧 runtime 的历史测量，**本轮没有复测，不能当成当前体积**。limit 已有上限，不能声称单响应行数随数据库无限增长。后续用固定合成大字段检查列表输出、分页、schema 和详情可读性即可先验证投影。

**S7/S8：请求过滤不是对已截断页面作装饰。** `list_opportunities_by_asset_class` 委托 `_select_all`，后者在 SQL 中 LIMIT；`repositories_market_data.list_prediction_events` 也先 LIMIT。最小验收用一条旧匹配记录和 N 条新不匹配记录，验证过滤结果仍包含旧记录，并把真正总数与页面条数区分。

## 覆盖清单

以下每个文件均进入 AST/异常/函数尺度扫描；“重点复核”列说明人工追踪焦点。空包也计入文件数。没有列出缺陷表示本轮未确认，不表示该模块已经全面无风险。

| 文件（相对 server/） | LOC | except / 宽捕获 | 重点复核 |
|---|---:|---:|---|
| `__init__.py` | 0 | 0 / 0 | 包初始化/导出；结构扫描 |
| `app.py` | 1,531 | 70 / 31 | lifespan、路由装配、鉴权、scheduler 失败与清理；S3/M2 |
| `compression.py` | 114 | 1 / 0 | 压缩中间件异常边界 |
| `dev_reload.py` | 54 | 1 / 1 | 状态计数与消费者；M1 |
| `operator_service_auth.py` | 62 | 0 / 0 | 服务身份验证边界 |
| `responsiveness.py` | 67 | 0 / 0 | 响应性采样与启动接线 |
| `routes/__init__.py` | 0 | 0 / 0 | 包初始化/导出；结构扫描 |
| `routes/analytics.py` | 2,495 | 19 / 13 | 聚合/可选扩展降级、operator decisions 大函数；M2 |
| `routes/bitget.py` | 78 | 0 / 0 | 连接状态委托与响应 |
| `routes/capital.py` | 83 | 0 / 0 | 事件写入及审计调用，保留已有问题背景 |
| `routes/command.py` | 18 | 0 / 0 | 命令入口委托 |
| `routes/data.py` | 3,123 | 27 / 15 | positions/trades 投影、缓存、拒绝/close 路径；H1 |
| `routes/dev_reload.py` | 69 | 1 / 0 | SSE 与开发状态，未作并发运行验证 |
| `routes/economics.py` | 286 | 1 / 0 | 成本读模型及不可用响应 |
| `routes/export.py` | 430 | 5 / 4 | 线程池、流式 CSV/临时 ZIP 生命周期；诊断导出不按列表泄露计 |
| `routes/gateway.py` | 103 | 0 / 0 | 控制委托与 actor 来源 |
| `routes/health.py` | 501 | 12 / 11 | 健康投影及 startup policy；S3 |
| `routes/live.py` | 223 | 1 / 0 | 确认、preflight、scorecard、配置重读、审计边界 |
| `routes/model.py` | 506 | 6 / 4 | Ping/broker/验证持久化；S1 |
| `routes/operator.py` | 1,626 | 2 / 0 | 入口/异常、pause 与扩展操作委托，结构扫描为主 |
| `routes/persistence.py` | 454 | 4 / 1 | 路径约束、导入导出响应、archive 处理；关联扩展 sync |
| `routes/prediction.py` | 282 | 8 / 2 | 过滤次序、计数、元数据容错；S7/S8 |
| `routes/risk.py` | 202 | 2 / 0 | 仅 server 读模型及动作入口；未扫描冻结 Risk 实现 |
| `routes/settings.py` | 2,918 | 28 / 15 | runtime/trigger/model/network 等配置入口及异常；未逐行审计全部配置语义 |
| `routes/ui.py` | 416 | 5 / 5 | bootstrap 可用性封装与 Risk 重复投影；S2 |
| `routes/universe.py` | 1,086 | 5 / 3 | planning-history 投影与分页、候选链的不可用值；S6 |
| `routes/update.py` | 162 | 0 / 0 | 更新委托、状态/失败响应 |
| `routes/ws.py` | 370 | 8 / 6 | ticket、广播与 sampler 生命周期；S4/S5 |
| `startup_policy.py` | 19 | 0 / 0 | 诊断目录与执行一致性；S3 |

## 未升级为问题及立卡清单

模型请求已有 broker 墙钟；单个 route 没有 timeout 不等于没有 deadline。可选扩展不存在、非权威诊断不可用、shutdown 尽力清理等宽异常不一律删除。Native Candidate 的 opportunity_id 为空也可能是当前主线设计，本轮没有把 #3644 的假设当作事实。全量诊断导出和单条详情允许携带原始字段，不按列表 blob 泄露处理。

建议立卡仅为 **S1（模型身份错绑）、S3（启动目录漂移）两张新卡，S2 一张明确关联 #1814 的遗漏跟进卡**。S4–S8 使用已有 5 张开放卡；H1 先核对分支是否包含 #3631 修复；M1/M2 不单独立卡。卡的优先顺序为 S4、S1/S2、S6、S5/S7/S8/S3，不因编号决定施工次序。

查重检索包含 all-state 的 ping_model、model_ping/identity、verification/identity、not_evaluated/risk、startup_failure_policy、WebSocket/auth/broadcast、prediction/filter/limit 和 blob；阅读了 #1332、#1855、#1814、#1184、#1961、#3631、#3642、#1529、#1530 等正文。未命中只表示本组检索未发现，不保证不存在其他措辞的重复卡。报告中的建议验收尚未执行，未做 Fedora、网络、性能或交易验证。

文档检查：`dev_check.py --no-tests --no-types` 与 `check_docs.py` 通过；Python/测试未改动，未运行测试套件。
