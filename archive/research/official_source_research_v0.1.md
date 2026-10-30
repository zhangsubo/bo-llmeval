# LLM API 评测管理平台官方资料研究报告

> 版本：v0.1  
> 检索日期：2026-08-18  
> 检索方式：当前环境未提供 Tavily，经用户授权后使用通用网页搜索；仅采用官方文档、官方仓库及标准组织资料。

## 1. 研究目标

本研究用于回答三个问题：

1. EvalScope、OpenCompass、token-speed-tester 分别适合承担什么职责。
2. 首版平台应复用哪些能力，哪些业务能力需要自行建设。
3. 性能指标、数据集、任务队列和网络安全设计需要满足哪些客观约束。

本研究不替代已经确认的产品决策，也不把三个开源项目的现有功能直接等同于本平台需求。

## 2. 结论摘要

- **EvalScope 作为首版唯一评测执行引擎。** 官方项目同时覆盖模型能力评测、API 服务压测、结果可视化，并直接支持 OpenAI-compatible API；其性能报告已经包含 TTFT、TPOT、ITL、Latency、请求吞吐和 Token 吞吐等指标，适合承接性能与客观质量评测。
- **OpenCompass 不单独部署。** 它面向广泛的学术评测、数据集配置和排行榜复现，具备大量数据集、分布式评测和实验配置能力；首版只通过 EvalScope 已有后端或适配能力使用确有需要的数据集，并参考 CompassHub 的数据集发现方式。
- **token-speed-tester 不作为运行依赖。** 它是聚焦单次或多次流式 API 测速的 CLI 工具，适合借鉴 TTFT、平均速度、10 Token 滑动窗口峰值速度、单秒峰值 TPS、分位数和 HTML 图表口径，但不承担供应商管理、任务生命周期、队列、复测和跨任务对比。
- **平台层必须自行建设。** 三个项目都不能直接覆盖已确认的供应商—连接—模型部署管理、API Key 安全存储、持久化任务队列、限流保护、取消与复测、不可变快照、人工备注、历史清理和离线多页报告等完整业务闭环。
- **测试可比性必须由平台显式控制。** EvalScope 官方对比文档指出，流式模式、停止条件、Tokenizer、Chat 模板和解码参数不一致都会使 TTFT、TPOT、ITL 或输出长度出现偏差；因此任务必须固定并快照这些条件。
- **供应商 usage 不能作为唯一 Token 来源。** OpenAI 官方 Chat Completions 文档将流式 usage 定义为可选信息，并明确说明流被中断时可能收不到最终 usage chunk；平台保留“供应商 usage + 本地 Tokenizer”两套口径是必要设计。

## 3. 三个项目对比

| 项目 | 官方定位与现有能力 | 适合复用/借鉴 | 不适合直接承担 | 首版决策 |
|---|---|---|---|---|
| EvalScope | 一站式大模型评测框架，支持能力评测、推理服务压测、交互式报告和 OpenAI-compatible API | 性能执行、质量数据集适配、客观评分、代码沙箱能力、原始指标采集 | 内部供应商台账、连接密钥、审批式预检、持久化业务队列、历史复测、静态外发报告版本管理 | 作为唯一执行引擎，由平台通过 Python API 调用 |
| OpenCompass | 面向大范围模型和数据集评测，支持 API 模型、任务切分、分布式评测、实验配置、数据集浏览和排行榜 | 数据集选择、评测方法、数据集元信息与可复现性设计 | 首版内部供应商 API 性能运营平台；其完整部署会带来重复执行栈和额外运维成本 | 不单独部署；只参考数据集与方法，必要能力通过 EvalScope 获取 |
| token-speed-tester | OpenAI/Anthropic API 流式测速 CLI，提供 TTFT、速度、峰值、分位数和 HTML 报告 | 指标命名、峰值速度算法、TPS 曲线、轻量报告视觉表达 | 并发/RPS/阶梯压测、质量评测、供应商管理、任务队列和历史治理 | 不作为依赖，只借鉴指标及报告表现 |

### 3.1 EvalScope 事实依据

- 官方仓库将其定义为支持模型能力评测、推理性能压测和结果可视化的一站式框架，并列出 OpenAI-compatible API 使用方式。
- 官方压测快速开始文档展示了并发数、请求数、请求 URL、模型、数据集和 `tokenizer-path` 等参数；随机数据集需要 Tokenizer 来计算 Token 数。
- 官方输出包括 Performance Overview、Per-Request Metrics、Per-Trace Metrics 和 Workload Throughput；指标覆盖 RPS、成功率、Latency、TTFT、TPOT、ITL、输入/输出 Token 数和吞吐量，并提供多分位统计。
- 官方项目当前也提供 OpenCompass backend，因此首版没有同时运行两套独立评测框架的必要。

来源：

- [EvalScope 官方仓库](https://github.com/modelscope/evalscope)
- [EvalScope 压测快速开始](https://evalscope.readthedocs.io/zh-cn/latest/user_guides/stress_test/quick_start.html)
- [EvalScope LLM 评测集清单](https://evalscope.readthedocs.io/zh-cn/latest/get_started/supported_dataset/llm.html)
- [EvalScope 与 vLLM Bench 对比说明](https://evalscope.readthedocs.io/zh-cn/latest/user_guides/stress_test/vs_vllm_bench.html)

### 3.2 OpenCompass 事实依据

- 官方仓库定位为开放、可复现的大模型评测平台，覆盖大量 Hugging Face 模型、API 模型、数据集与多种评测范式。
- 官方文档支持离线下载、OpenCompass 存储服务自动下载以及通过 ModelScope 按需获取数据集。
- 官方列出的可用数据集包括 HumanEval、MBPP、C-Eval、GSM8K、MMLU 等；这与首版选定的客观质量基准存在交集。
- OpenCompass 2.0 的 CompassHub 用于数据集浏览和发现，CompassRank 更偏向排行榜场景；后者不属于本平台首版目标。

来源：

- [OpenCompass 官方仓库](https://github.com/open-compass/opencompass)
- [CompassHub](https://hub.opencompass.org.cn/home?locale=zh-CN)

### 3.3 token-speed-tester 事实依据

- 官方 README 将其定义为 LLM API Token 流式输出测速 CLI，支持 OpenAI Chat Completions 和 Anthropic Messages。
- 指标包括 TTFT、总耗时、总 Token 数、平均速度、10 Token 滑动窗口峰值速度、单秒峰值 TPS、TPS 曲线以及均值、P50、P95、P99、最小值和最大值。
- 工具可以生成包含 SVG 图表的单份 HTML 报告，但其核心交互仍是命令行参数和单机文件输出。

来源：

- [token-speed-tester 官方仓库](https://github.com/Cansiny0320/token-speed-tester)

## 4. 数据集研究结论

### 4.1 性能与质量数据集必须分开

性能测试关注输入/输出长度、请求到达模式、并发、RPS 和缓存条件的可控性；质量测试关注题目、参考答案、评分器、子集和版本的稳定性。两者混用会同时削弱性能可比性和质量结果解释性。

建议首版质量基准保持已确认的小集合：

| 能力 | 基准 | 首版范围 |
|---|---|---|
| 指令遵循 | IFEval | 客观规则评分 |
| 中文知识 | C-Eval | 精选子集 |
| 通识知识 | MMLU | 精选子集 |
| 数学推理 | GSM8K、MGSM 中文子集 | 答案抽取与客观匹配 |
| 代码能力 | HumanEval、MBPP | 隔离沙箱运行测试用例 |

EvalScope 的当前官方数据集清单包含上述基准。每个实际导入版本仍须单独登记来源、版本、license、checksum、样本数、默认 Prompt 模板和评分方式，不能把框架支持等同于自动取得再分发权。

### 4.2 资源准备应与任务执行解耦

OpenCompass 和 EvalScope 都存在首次下载或指定本地 Tokenizer/数据集路径的流程。为避免外网、镜像或上游版本变化污染正式测试，数据集和 Tokenizer 应在“测试资源”页面提前人工下载、校验并缓存；任务启动时只引用已经就绪的不可变版本。

## 5. 性能指标与可比性约束

### 5.1 标准指标

首版应保留 EvalScope 常用指标和用户明确提出的指标：

- 端到端 Latency / 总耗时
- TTFT
- TPOT
- ITL
- 请求吞吐量、输出 Token 吞吐量、总 Token 吞吐量
- 输入、输出、总 Token 数
- 平均生成速度、端到端平均输出速度
- 10 Token 滑动窗口峰值速度
- 单秒峰值 TPS
- 成功率、失败率与错误分类
- mean、min、max、P50、P90、P95、P99

非流式请求不生成伪造的 TTFT、TPOT 或 ITL；流式和非流式场景分别统计。

### 5.2 必须固定的测试条件

根据 EvalScope 官方误差说明，至少固定并快照：

- messages、Prompt 模板和数据集版本
- Tokenizer、Chat 模板与版本 checksum
- temperature、top_p、max_tokens、stop、seed 等生成参数
- stream 模式和 usage 获取方式
- 并发/RPS、请求数、到达模式、预热次数
- 输入/输出长度目标与是否 `ignore_eos`
- 连接、模型部署、价格、超时和限额配置

不同条件仍可放在同一页面查看，但必须标注“非严格可比”，不得合并统计或生成排名。

### 5.3 Token 计数双口径

- **供应商 usage：** 用于实际账单成本；缺失时明确显示“未提供”。
- **平台 Tokenizer：** 用于性能归一化和估算；保存 Tokenizer 来源、版本和 checksum。
- 两者同时存在时展示差值，不相互覆盖。
- 流式请求中即使设置 `stream_options.include_usage`，中断时仍可能缺失最终 usage，因此缺失 usage 不应直接判定请求失败。

来源：

- [OpenAI Chat Completions API Reference](https://developers.openai.com/api/reference/resources/chat)
- [EvalScope 压测快速开始](https://evalscope.readthedocs.io/zh-cn/latest/user_guides/stress_test/quick_start.html)
- [token-speed-tester 指标说明](https://github.com/Cansiny0320/token-speed-tester#指标说明)

## 6. 平台技术约束

### 6.1 PostgreSQL 作为唯一数据库与任务队列

PostgreSQL 官方文档明确说明，`FOR UPDATE ... SKIP LOCKED` 可用于减少多个消费者处理 queue-like table 时的锁竞争；其 ordered-set aggregates 原生提供 `percentile_cont` 和 `percentile_disc`。这支持首版在单一数据库中实现持久化 FIFO 队列和常用分位数汇总，无需额外引入 Redis、Celery 或分析数据库。

来源：

- [PostgreSQL SELECT 与 SKIP LOCKED](https://www.postgresql.org/docs/current/sql-select.html)
- [PostgreSQL Ordered-Set Aggregate Functions](https://www.postgresql.org/docs/current/functions-aggregate.html)
- [PostgreSQL JSON Types](https://www.postgresql.org/docs/current/datatype-json.html)

### 6.2 Base URL 必须按 SSRF 风险处理

供应商 Base URL 是由后台服务发起请求的用户配置输入，属于 SSRF 信任边界。OWASP 指出只检查 hostname 或首次 DNS 解析不足以阻止 DNS rebinding、重定向、IPv6 和替代 IP 表示绕过，并建议限制协议、解析并分类目标 IP、阻止 loopback/link-local/cloud metadata、控制重定向、设置超时和关闭自动重试。

这直接支持已确认的设计：后台维护域名/CIDR 白名单、永久禁止云元数据和本地特殊地址、执行前重新解析、阻止 DNS rebinding、不跟随 3xx、设置独立超时；“忽略 TLS 错误”不能绕过这些网络边界。

来源：

- [OWASP SSRF Prevention](https://owasp.org/www-community/pages/controls/SSRF_Prevention_in_Nodejs.html)

## 7. 建议写入 PRD 的研究结论

以下条目均与需求访谈结论一致，建议正式写入：

1. EvalScope 是唯一执行引擎；OpenCompass 与 token-speed-tester 不作为独立运行依赖。
2. 性能数据集和质量数据集分开管理，资源提前下载并形成不可变版本。
3. 首版质量集限定为 IFEval、C-Eval、MMLU 精选子集、GSM8K、MGSM 中文子集、HumanEval、MBPP。
4. 流式与非流式分开统计；TTFT、TPOT、ITL 只在有意义时计算。
5. Token 计数同时保留供应商 usage 与平台 Tokenizer 两套口径。
6. 对比前检查数据集、Tokenizer、参数、负载和预热条件，区分严格可比与非严格可比。
7. PostgreSQL 同时承担业务存储和持久化 FIFO 队列，首版不引入 Redis/Celery。
8. Base URL 作为 SSRF 信任边界实施白名单、DNS/IP 复核、特殊地址永久阻断和禁止重定向。

## 8. 建议明确排除

1. 不复刻或二次开发 EvalScope 现有 dashboard。
2. 不单独部署完整 OpenCompass，也不全量搬运其数据集。
3. 不将 token-speed-tester 纳入运行依赖。
4. 不因 OpenAI 当前官方协议包含更多内容类型而扩大首版范围；首版仍只接受文本生成输入输出。
5. 不将上游框架的“支持某数据集”视为已经完成 license 审核或取得再分发权限。
6. 不使用未经核实的社区评价、Stars 数量或排行榜结果决定产品架构。

## 9. 待确认

若上述“建议写入 PRD”和“建议明确排除”均无异议，下一阶段只生成需求文档，不制作原型、流程图、演示站点或部署配置。
