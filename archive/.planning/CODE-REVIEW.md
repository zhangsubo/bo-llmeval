# 当前代码审查基线

审查范围：当前 `HEAD` 与工作区改动，结合 `prd/prd_v1.0_final.md` 和实际测试结果。

## 高内聚/低耦合评估

- **边界清晰**：API route 负责输入/响应适配，service 负责业务事务，queue/worker 负责调度，`run_executor`/`evaluation_engine` 负责执行协议，`report_service` 负责离线发布；SSRF、脱敏和限流通过独立服务复用。
- **状态机集中**：Run lease/fencing、Task 聚合、代码作业收敛分别集中在 `run_queue_service`、`task_lifecycle_service`、`code_execution_service`，终态写入有单一入口，便于审查并发一致性。
- **仍有耦合热点**：`run_worker` 负责快照解释、调度、质量/性能/代码分流，`report_service` 同时承担指标装配和多页面渲染；后续扩展应优先通过策略/端口抽离，避免继续增加分支。
- **演进约束**：`EvaluationEngine` Protocol、不可变 Run snapshot、受控文件名和数据库唯一约束是跨模块契约；新增功能必须补充契约测试、失败终态和并发/脱敏回归，不应绕过 service 直接写 ORM。

## 当前剩余风险

1. **真实基础设施验收尚未完成**
   PostgreSQL 多 Worker FIFO/advisory lock、Docker socket 权限、沙箱禁网/资源限制和真实 EvalScope SDK 仍需在部署环境中验收；当前测试主要使用 SQLite 与注入的 fake engine。
2. **前端仍是管理台级别实现**
   任务配置、监控、逐请求详情、结果对比、报告版本和 CSV/JSON 导出工作流已可操作；资源页已覆盖主要创建/上传动作，报告已包含质量/性能/成本/错误图表，选择器已支持服务端搜索，页面已按路由拆包。真实浏览器 E2E 仍需验收。
3. **内置质量数据集的真实上游验收未完成**
   IFEval、C-Eval、MMLU、GSM8K、MGSM、HumanEval、MBPP 的目录、缓存和逐样本适配已实现，但仍需在部署环境验证 EvalScope/Hub 网络、license 和真实数据格式。
4. **Compose 镜像构建与沙箱路径配置**
   `app` 和 `code-worker` 镜像已在本地成功构建，并实际安装 `evalscope==1.11.1`。代码 Worker 需要 `docker-cli`、宿主 Docker socket 的匹配 GID，以及 `SANDBOX_HOST_DIR` 与 `/sandbox` 的 host/container 路径映射；Compose 已提供默认映射，Linux 部署仍需按宿主 socket 设置 `DOCKER_GID`。
   Docker Desktop/macOS 的 socket 常见为 `root:root`，Compose 默认 `DOCKER_GID=0`；Linux 仍应按宿主实际 GID 覆盖该值。
   app 的宿主端口已收紧为 `127.0.0.1:8000`，外部访问应统一经过 frontend/nginx。当前已有旧容器仍显示 `0.0.0.0:8000`，目标部署需按新 Compose 重建后再验收，不能把现有容器状态当作新配置已生效。

## 本轮复审新增

18. **资源下载与双口径统计边界已收紧**
    Tokenizer 状态变更现在遵循有限状态流转，只有 builtin 或已发布并带 checksum 的资源可以进入 `ready`；新鲜的 `downloading/processing` claim 会拒绝重复工作，超过 1 小时的 stale claim 会收敛为 `failed` 并允许重试，残留临时目录/文件也会回收。Tokenizer revision 不再直接拼接为路径，ZIP 解压同时拒绝 Windows 路径和符号链接。指标聚合对同一 Request 的 vendor/local Token 分别累计，整体吞吐和成本只采用 vendor 优先的单一主口径，避免双算。
19. **前端首屏按路由拆包**
    管理页面使用 `React.lazy` + `Suspense` 按路由加载，Vite 构建产物已拆分为页面和组件 chunks，消除约 1.3MB 单一主 bundle 的发布警告。
20. **限流 reservation 释放具备一次补偿重试**
    Request 证据提交后，连接限流事件释放若遇到瞬时数据库提交异常，会回滚并在行锁下幂等重试一次，避免短时间幽灵占用；彻底不可用时仍由 lease 过期回收。

21. **质量评分和代码汇总避免无谓的 N+1/全量物化**
    native quality 评分使用一次 `row_number()` 窗口查询取得每个 Request 的最新 Attempt；代码沙箱状态汇总改为数据库分组计数，不再为渲染状态数字加载全部 CodeExecution 行。新增多 Attempt 取最新结果回归测试。

22. **CSV 导出改为流式发布**
    CSV 使用 Attempt 计数子查询和 `yield_per` 逐行写入临时文件，避免在导出大 Run 时同时持有全量 Request ID 与计数映射；仍采用原子替换，失败不会覆盖旧文件。

23. **报告生成失败可复用幂等键重试**
    失败版本继续保留为历史记录，但释放其 `Idempotency-Key`；重复点击可生成新的不可变版本，不会被旧失败记录永久短路。

16. **本轮已修复的安全与指标边界**
    SSRF 现在除永久禁止网段外，默认拒绝所有 `ipaddress.is_global=False` 的特殊地址（CGNAT、TEST-NET、基准测试和保留网段等），显式 CIDR 白名单仍可放行非永久禁止地址。供应商 usage 现在统一规范化并在信息充分时补齐分项/总量，只有 `total_tokens` 时也会保留总 Token 证据并将不可拆分成本标记为估算；`schedule_dropped` 不再计入供应商成功率/失败率分母。新增草稿编辑、终态复测和代码质量模式联动，Compose 沙箱 bind source 与 `SANDBOX_HOST_DIR` 保持一致，生产配置强制绝对 host path。
17. **前端报告筛选与任务工作流已补齐**
    Reports 支持供应商、生成时间起止筛选，并在手工输入 Task UUID 时退出“全部报告”模式；Tasks 支持从列表重新打开 draft、保存 PATCH、终态创建复测草稿并展示差异。任务、报告和监控选择器已改为有界首屏加载与防抖服务端搜索；任务页数据集/模板版本改用全局分页接口，消除了按资源逐个请求版本的 N+1。

11. **评测引擎真实运行时仍需部署验收**
    任务配置校验已将 EvalScope 固定为性能/文本质量的运行时引擎，native 仅保留给独立 Docker 代码评分器；当前缺口是目标部署环境中的 EvalScope SDK、真实性能压测 API 和网络 transport 验收，而不是任务配置边界。
12. **列表筛选与监控字段已补齐代码路径**
    连接、模型、任务和报告现在通过 service/route 提供数据库端筛选；任务监控返回目标/实际 RPS、错误分类计数及最近错误时间。仍需在基准容量 PostgreSQL 上验证查询计划和分页 P95。
13. **Task 评测配置更新边界已统一**
    创建、PATCH 和提交均复用评测类型/引擎/质量模式组合校验；流式 Tool Calling 在增量重建后重新校验最终结构；沙箱执行使用统一的配置镜像策略。
14. **Request 证据写入边界已统一脱敏**
    `run_executor.execute_chat_request` 在创建或复用 Request 时，使用当前连接 API Key 递归脱敏 `input_messages`；质量执行器在预创建证据和异常收敛前也执行正则兜底，调度丢弃分支同样不再直接保存原始消息。
15. **标准日志与 structlog 均有凭据过滤**
    `core.logging` 安装一次性的 `LogRecordFactory`、handler filter 和 structlog event processor，覆盖 `Authorization`/Bearer、已知凭据字段及常见 API Key 模式；异常 traceback 的渲染文本也会脱敏，避免普通异常参数绕过脱敏。

## 高优先级问题

5. **任务执行仍需真实 PostgreSQL/Docker 验收**
   Worker 已用独立 Session + `ThreadPoolExecutor` 支持 fixed-concurrency 的真实 in-flight 并发，固定 RPS 已支持最大 in-flight 和调度丢弃，并已区分冷启动/预热/稳态；连接级 concurrency/RPS/RPM/TPM 已通过数据库事件预留、advisory lock 和执行器接线完成，但尚未在真实 PostgreSQL 多 Worker 环境压测。
6. **SSRF 仍需真实网络验收**
   连接测试、模型同步和 Worker 实际请求已在发送前解析并校验，且 HTTP 客户端不跟随重定向；固定解析传输已实现，但 IPv6、多地址 fallback 和 TLS 场景仍需部署环境验证。
7. **报告与指标的容量优化**
   报告生成已使用临时目录发布，指标和报告查询仍有进一步批量化/避免 N+1 的空间。
8. **生产密钥必须覆盖开发占位值**
    `.env.example` 现已提供合法但公开的开发 Fernet key，生产环境仍必须替换为随机密钥并通过安全配置注入。
9. **远程资源依赖需按部署 profile 安装**
   Tokenizer 下载 API 已支持 builtin、Hugging Face、ModelScope 和 ZIP 上传；后两类依赖位于 `evalplat[resources]`，默认镜像未安装时会记录明确失败状态。下载 claim 支持 stale recovery，状态接口不会把不完整远程资源直接标记为 `ready`。
10. **EvalScope 的真实 SDK 与网络边界仍需验收**
   适配层已隔离 SDK 并持久化聚合/逐样本证据；缺少逐样本记录的聚合结果现在会标记为失败，真实 EvalScope 1.11.1、其 OpenAI transport、重定向和 DNS rebinding 仍需部署环境验证。

## 本轮补充记录

24. **历史清理预览与实际删除口径一致**
    `CodeExecution` 预览统一按 `run_id` 统计，兼容缺少 `request_id` 的旧记录；新增回归测试。
25. **报告文件名跨平台安全**
    报告路径序列化统一处理 POSIX `/` 和 Windows `\\`，只暴露受控 basename，避免在 POSIX 上泄露整段 Windows 路径。
26. **双口径成本估算标记准确**
    同一 Request 同时存在 vendor usage 与 local tokenizer 时，成本按 vendor usage 计算并标记 `estimated=false`；仅缺少 vendor usage 时才标记估算。
27. **任务预检失败反馈可操作**
    前端提交失败后重新读取最新 Task 的 `precheck_result`，展示检查项和限制值，并提供编辑入口。
28. **质量失败返回值不会遗留孤立 Request**
    质量适配器即使只返回 `failed/cancelled` payload，执行器也会补齐 Attempt 并关闭 Request；已有 Attempt 时只补 Request 终态，避免重复证据。
29. **报告 schema 跨平台 basename 一致**
    API `ReportRead` 与报告服务使用相同的 `/`、`\\` 归一化规则，旧 Windows 路径只序列化文件名。
30. **历史清理兼容旧 Windows 路径和导出文件**
    清理路径解析支持 Windows drive/UNC basename；已生成的 `run-<uuid>.csv/json` 也进入清理 manifest，预览字节数与实际删除一致。
31. **代码沙箱样本索引与 Request 对齐**
    `CodeExecution.sample_index` 直接使用质量 Request 的规范索引，避免自定义样本未提供索引造成关联错乱。
32. **清理期间导出下载受保护**
    `run-<uuid>.csv/json` 文件在对应任务的清理批次进入 `running` 后与静态报告一样拒绝下载，避免读取正在回收的敏感导出。

33. **清理批次增加崩溃恢复心跳**
    CleanupBatch 在每个任务后持久化删除计数、失败项和 `updated_at`；超时的 `running` 批次不再永久阻断报告/导出下载，并可由 retry 端点转换为 `failed` 后继续执行。超时阈值由 `HISTORY_CLEANUP_STALE_SECONDS` 配置，默认 900 秒。

34. **EvalScope 数据集路径再次收敛到边界**
    `build_evalscope_perf_config` 对不可变快照中的 `storage_path`/`dataset_path` 执行 `resolve()` + `relative_to(dataset_dir)`，生产调用显式传入数据集根目录；越界路径在进入 EvalScope 前失败。

35. **native executor 兼容旧 endpoint 快照**
    单请求执行统一处理 base URL、`/v1`、`/chat/completions` 和 `/completions` 四种形态，避免历史快照重复拼接 endpoint。

36. **调度丢弃不再污染性能统计与异常失败边界**
    `schedule_dropped` 只计入计划请求数，延迟、ITL、速度、Token 和吞吐时间窗均只消费实际发送请求；EvalScope 性能适配器在异常配置中包含解密 API Key 时也会按当前连接凭据脱敏后再落库。

37. **模板评测类型物化与 API 默认值解耦**
    创建任务时，模板版本的 `test_type` 会覆盖请求 schema 的默认评测类型/引擎/质量模式，保证直接调用 API 使用代码模板时得到 `quality + native + code` 的一致快照。

38. **长时间代码沙箱任务保持 Worker 心跳**
    code-worker 使用独立短事务心跳线程，每 20 秒刷新状态，并在优雅退出时停止线程和清理记录；最长 300 秒的沙箱执行不会被预检误判为 stale。
39. **管理台历史资源完整分页**
    Tasks、Reports、Monitoring 的任务/资源选择器使用 `fetchAllPages` 获取完整分页数据，避免超过 100 条记录后无法选择历史对象。
40. **用户可见异常统一收敛**
    EvalScope、沙箱、历史清理、资源下载和报告渲染异常在持久化/API/离线报告边界使用稳定摘要；详细堆栈仅保留日志，路径和凭据不会随证据返回。

## 已验证

- `./.venv/bin/python -m pytest -q`：472 passed, 1 warning（含协议边界、状态值域、服务层字段白名单、固定 RPS 调度、EvalScope 结果映射、EvalScope 数据集路径边界、沙箱镜像策略、列表筛选、监控字段、数据集可加载性、内置长度预设、Request/日志脱敏、非公网特殊地址、双 Token 口径、资源下载 stale recovery、ZIP 路径/符号链接边界、限流释放补偿、部分 usage 和调度丢弃统计、质量最新 Attempt、报告幂等失败重试、历史清理预览、清理批次崩溃恢复、跨平台报告 basename、预检失败反馈、质量失败证据、native endpoint 兼容、导出清理、样本索引和清理期间下载保护、调度丢弃统计排除、EvalScope 性能异常脱敏、报告失败脱敏和代码模板类型物化回归；仍不代替真实基础设施验收）。
- 现有 PostgreSQL 容器执行双事务 `FOR UPDATE SKIP LOCKED` smoke：两个并发事务分别认领不同队列项，测试专用表已清理；仍需真实多 Worker、advisory lock、限流和容量压测。
- 现有 `code-worker` 容器真实执行沙箱 smoke：编译/测试通过，外网连接被 `--network none` 阻断，`/workspace` 写入被只读根文件系统拒绝，且无残留 `evalplat-sandbox-*` 容器；目标部署仍需重新验证 socket 权限、镜像 digest 和宿主路径。
- `python -m compileall -q src tests`：通过。
- `frontend`: `npm run build`：通过，页面和组件已按路由拆包，最大静态 chunk 约 450KB；`npm run lint` 退出码 0，但有既有 React effect/dependency warnings。
- `ALEMBIC_DATABASE_URL=postgresql+psycopg://evalplat@127.0.0.1:55432/evalplat ./.venv/bin/alembic upgrade head` 与 `alembic check`：在临时本地 PostgreSQL 实例通过，当前 head 为 `20260915_model_deployment_uq`，无新 upgrade operations。默认开发 DSN 仍因角色 `evalplat` 不存在而不可用。
- 临时本地 PostgreSQL 双 Session FIFO smoke：两个并发 Worker 分别认领不同队列项并正确递增 fencing token；测试行已清理。
- Docker daemon 当前不可用（`~/.orbstack/run/docker.sock` 不存在），因此本轮未重复 Compose/沙箱 smoke。
- DELETE 连接接口已按软删除契约更新测试：返回 204，并将状态置为 `archived`。
- 任务提交已支持目标模型预检、Run 创建和基础不可变快照。
- `run_queue_service` 已支持 FIFO 认领、lease 续租和 fencing token 保护的终态更新。
- `run_executor` 已支持单次非流式 OpenAI-compatible Chat Completions 调用，并落库 Request/Attempt、延迟、HTTP 状态和 vendor usage。
- `run_executor` 已严格校验非流式响应和 SSE 结束标记/JSON/chunk，供应商缺失流式 usage 时使用本地 Tokenizer 指标回退，并区分连接、首 Token、chunk idle 和总请求超时类别。
- Run 快照现已保存执行所需的 `base_url`（不保存 API Key），实际请求优先使用快照地址，避免提交任务后连接 URL 变更破坏历史 Run 的可复现性。
- 报告服务已生成脱敏摘要 HTML 和 Request CSV，文件目录由 `REPORT_DIR`/`report_dir` 配置。
- Run 查询已区分轻量分页列表和按需完整 Request/Attempt 详情；报告支持版本列表和单版本查询。
- 质量服务已提供五种确定性文本/数值评分器和逐请求证据，不执行任意用户脚本。
- `sandbox_service` 已提供 Docker 禁网、只读根文件系统和资源限制适配器；独立代码 Worker、编译/运行证据、代码执行摘要和导出已接入，仍需验证 Docker socket 权限及真实容器端到端执行。
- `metrics_service` 已提供 Run 汇总和按 phase 的成功率、错误率、延迟分位数、Token 吞吐、ITL 和快照价格成本估算；静态报告已将这些指标渲染为离线分位数、时间序列、质量、成本和错误分类图表。
- 前端对比组和监控页面已替换为可操作的列表/创建/状态刷新页面；Reports 支持任务选择器、报告下载和按 Run 的 CSV/JSON 导出，Models 支持从启用连接下拉选择、价格版本维护，Connections 支持编辑与 API Key 替换；资源选择器已完成有界分页和远程搜索，页面已按路由拆分 chunks。
- 任务状态接口现在返回队列位置、计划/完成请求数、进度、当前并发和预计剩余时间；对比接口附带统一 Run 指标表数据。
- 任务页已支持选择多个模型部署，终态任务可触发报告生成；连接列表已覆盖全部供应商。
- Docker Compose 已包含独立常驻 Worker；报告生成返回可下载 URL，前端可直接打开产物。
- `load_profile_service` 已提供可复现的多负载计划编译和参数校验。
- Worker 已消费 `request_count/load_profile` 计划并在每个请求前检查取消状态。
- `run_worker.process_one` 已串联认领、单 Run 执行和 fencing 保护的终态更新。
- Worker 异常会把 Run 收敛到失败，过期 lease 会标记为 `interrupted_failed`。
- 运行中 Task 支持进入 `cancelling`，排队 Run 立即取消，Worker 在发送下一请求前检查取消状态。
- 父 Task 会根据全部子 Run 状态聚合为 running/completed/failed/partially_completed/cancelled。
- 修正 `Request.run_id` 的 ORM UUID 类型，使其与迁移和 PostgreSQL schema 一致。
- 新增 `services/evaluation_engine.py`：集中定义 `EvaluationEngine` Protocol、EvalScope 1.11.1 adapter、Run 快照到 `TaskConfig` 的映射，以及缺失依赖时的 `unavailable` 结果；新增 `tests/test_evaluation_engine.py` 契约测试。
- EvalScope 已由 `run_worker` 按快照分流，创建质量 Request/Attempt，成功、unavailable 和配置异常都会收敛 Run 终态；结果和异常会过滤 API Key。
- 连接测试、模型同步和 Worker 实际请求在发送前重新执行 `SSRFGuard` 校验，新增请求边界回归测试；`pyproject.toml` 增加 `evalscope==1.11.1` optional extra。
- 质量任务现在支持 `evaluation_kind`、`quality_samples`、`request_count`、`stream` 和 `retry_policy` 快照；质量执行器按一条 Request 多个 Attempt 执行有限重试，并在样本/Attempt 边界响应取消。
- 流式执行器现在记录 Tokenizer 对齐的增量时间序列并计算 ITL、TPOT、平均生成速度、连续 10 Token 峰值速度和 1 秒峰值 TPS（无 Tokenizer 时保留兼容的 chunk 证据）。
- 新任务快照支持 `warmup_count`；Worker 对显式新快照按 `cold_start`、`warmup`、`steady` 分阶段执行，旧快照保持原请求数量和语义。
- 常驻 Worker 将 `SessionLocal` 传入调度器；fixed-concurrency 请求使用独立 Session 和线程池并发执行，避免跨线程共享 SQLAlchemy Session。
- 固定 RPS 在达到 `max_inflight` 时记录 `schedule_dropped` Request/Attempt，不因慢请求拖移 open-loop 计划。
- Run 快照现在物化模型/连接的上下文、流式能力、TLS、timeout 和 limits，HTTP 执行优先使用快照配置；嵌套配置使用深拷贝避免别名污染。
- 新增 `Report` ORM 和 `20260904_reports` Alembic 迁移；报告生成记录版本、摘要/明细路径和任务快照，CSV 现在记录真实 Attempt 数量。
- OpenAI-compatible 响应现校验最小 `tool_calls` 结构并保留未知扩展字段；CSV 导出对公式前缀执行安全转义，报告明细包含输入、提取字段和原始响应（均 HTML escape）。
- 对比服务已兼容旧快照，同时按当前 `dataset_version_id`、`generation_params`、`load_profile`、`stream` 和 `evaluation_kind` 判定严格可比性，修复字段名漂移导致的误判。
- 数据集服务新增 JSONL/CSV 上传：100MB 限制、UTF-8/行结构/列数全文件校验、SHA-256 checksum、UUID 文件名原子落盘和 ready 版本记录；新增上传 API。
- ready 数据集版本现在可由 Worker 安全读取并按 `field_mapping` 映射为质量样本；路径越界、缺失文件、checksum/样本数不一致会收敛 Run 为失败而不会遗留运行中状态。
- `template_service.ensure_builtin_templates` 幂等创建七套首版内置模板及版本；应用启动时自动确保模板存在，自定义同名模板不会阻止内置模板种子。
- 任务创建现会加载 `template_version_id` 并把模板的评测类型、数据集、Tokenizer、生成参数、流式模式、预热、负载、请求数和重试策略物化到草稿快照；显式请求字段优先，路由保留 `exclude_unset` 语义。
- 新增 `audit_service.record_event`，供应商/连接、任务创建/提交/取消、报告生成、白名单和系统设置修改均写入审计事件；任务创建和设置更新的审计写入与业务变更保持同一事务。
- `.env.example` 的开发主密钥占位值改为合法 Fernet 格式，并明确生产替换要求。
- TLS `skip_verify` 开关默认关闭，创建或更新时必须显式提供风险确认；空 API Key 会被拒绝。
- 连接级 runtime rate limiter 已接入 `run_executor`；限额拒绝写入 `connection_limit` Request/Attempt 证据，释放时回写实际 Token，长运行 active lease 不受滚动窗口误删。
- 代码 sandbox runner 先加载 `main.py` 再执行 `tests.py`，支持函数定义型 HumanEval/MBPP 样本；代码执行摘要进入 metrics、HTML quality 页和 Run JSON 导出。
- 历史清理已支持预览、不可变 manifest、报告文件目录约束、按依赖顺序删除、部分失败记录、幂等补偿重试、运行中报告保护和审计事件保留；执行 API 要求确认文本 `CONFIRM`。
- 报告、CSV 和 JSON API 只返回受控文件名；历史 Report 响应对旧绝对路径也只序列化 basename，清理模块仍兼容旧记录。
- 连接兼容性测试结果（models、流式/非流式能力、耗时、request ID、脱敏错误）持久化在 Connection 最近测试快照中。
- Worker Run lease 在长执行期间由独立 Session 定期续租；取消状态统一阻止后续请求，清理/报告/重试共享 Task 行锁协议。
- EvalScope 结果已持久化到 `Request.quality_result`，支持常见逐样本列表键和聚合证据；无法映射逐样本结果的调用会保留聚合诊断但将 Request/Run 标记失败，native 评分结果同样逐请求持久化。
- 前端新增结果对比、报告版本查询/生成/下载入口，并扩展任务创建的质量/负载字段。
- 草稿任务 PATCH 现在可编辑目标、数据集、Tokenizer、生成参数、负载和质量配置；监控页可直接取消或重试单个 Run。
- Run 重试通过 `retry_of_run_id` 唯一约束实现幂等；Dataset/Template 版本通过父行锁和 `(parent_id, version)` 唯一约束避免并发重复。
- Run 快照包含 Dataset/Tokenizer 校验和、版本、来源、路径和价格币种/汇率；报告快照包含 Task/Run 备注和 TLS 不安全标记，数据库提交失败会删除已发布文件。
- 指标与报告已将最新 Attempt、Request 和 Attempt 明细批量加载，减少逐请求 N+1 查询；全局并发上限可由 `global_max_parallel_runs` 系统设置覆盖环境默认值。
- 运行时依赖 `httpx`、`python-multipart` 已归入主依赖，Docker 的 `.[evalscope]` 安装可覆盖生产 import；沙箱默认使用 digest 固定镜像，并可通过 `SANDBOX_IMAGE` + `SANDBOX_REQUIRE_IMMUTABLE_IMAGE` 继续约束生产镜像来源。
- 本地 PostgreSQL 双 Session 并发 FIFO smoke 通过；真实 Docker digest 沙箱 smoke 通过禁网、只读根文件系统和代码测试执行。
- 本地 Compose 镜像已完成 `evalscope==1.11.1` 安装；使用旧 PostgreSQL volume 的迁移在旧账号下成功，应用和两个 Worker 的真实容器 smoke 通过。健康探针为 `/healthz`、`/readyz`（版本化路径 `/api/v1/healthz`、`/api/v1/readyz` 也保留），均返回 200；代码 Worker 在 Docker Desktop 的 `root:root` socket 下通过 `DOCKER_GID=0` 成功执行禁网、只读沙箱。
- 内置输入长度预设已贯通模板版本、Task/Run 快照、Tokenizer-backed Worker 消息生成和前端选择器；短/中/长/超长分别为 256/128、2K/512、8K/1K、32K/2K Token，缺少 ready Tokenizer 时预检和 Worker 均明确阻断。

## 推荐实施顺序

1. 任务输入模型、强制预检、快照和 Run 创建。
2. PostgreSQL FIFO worker、lease/fencing、取消和重试状态机。
3. OpenAI-compatible 请求执行与双口径 Token/性能指标。
4. 在真实 EvalScope/生产 PostgreSQL 环境验收逐样本结果、队列和限流边界。
5. 静态多页报告、CSV/JSON 导出和脱敏验证。
6. 完成浏览器端 E2E 验收。
7. 集成测试、Docker 部署验证和最终安全/性能审查。
