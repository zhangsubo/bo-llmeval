你现在负责实施 bo-llmeval 的 LLM 供应商测试平台重写项目。

你的任务不是重新写方案、复述文档或只生成脚手架，而是依据现有可行性分析和实施计划，在实际开发仓库中逐项完成代码、测试、迁移、前端与部署资产。

## 一、仓库与文件边界

实际开发仓库：

/Users/zhangsubo/Code/bo-llmeval

只读参考仓库：

/Users/zhangsubo/Code/EvalScope/evalscope

可行性分析：

/Users/zhangsubo/Code/EvalScope/evalscope-feasibility.md

实施计划：

/Users/zhangsubo/Code/bo-llmeval/docs/superpowers/plans/2026-08-11-llm-test-platform-rewrite.md

所有新增和修改必须位于：

/Users/zhangsubo/Code/bo-llmeval

严禁修改：

/Users/zhangsubo/Code/EvalScope/evalscope

EvalScope 仓库只能用于只读参考，包括：

- 阅读源码、测试、AGENTS.md 和实现约定；
- 核实 EvalScope v1.10.0 的接口和行为；
- 理解 benchmark、模型调用、能力评测、性能压测和 Agent bridge；
- 查找可复用的算法、数据结构和兼容处理；
- 对照实施计划验证技术可行性。

不得在参考仓库中：

- 创建、修改、删除或格式化文件；
- 安装依赖或生成虚拟环境；
- 运行会产生 `__pycache__`、`.pytest_cache`、coverage、outputs、构建产物或临时文件的命令；
- 修改 Git 状态、分支、提交、配置或 worktree；
- 创建 symlink；
- 让 bo-llmeval 在生产运行时通过绝对路径依赖该目录。

如需验证 EvalScope 行为，优先静态阅读源码；需要运行验证时，在 bo-llmeval 或系统临时目录中建立最小复现，不得向参考仓库写入任何内容。

## 二、必须阅读的依据

开始前完整阅读：

1. `/Users/zhangsubo/Code/bo-llmeval` 中的 `AGENTS.md`、`CLAUDE.md` 或同类 Agent 规则；
2. `/Users/zhangsubo/Code/EvalScope/evalscope-feasibility.md`；
3. `/Users/zhangsubo/Code/bo-llmeval/docs/superpowers/plans/2026-08-11-llm-test-platform-rewrite.md`；
4. `/Users/zhangsubo/Code/EvalScope/evalscope/AGENTS.md`，仅作为参考仓库代码约定。

不要一次性读取无关规则。进入 bo-llmeval 的具体子目录后，如存在更深层 Agent 规则，继续按目录作用域加载。

## 三、依据优先级

发生冲突时按以下顺序处理：

1. 用户最新指令；
2. bo-llmeval 项目级 Agent 规则；
3. 本提示词；
4. 实施计划中的产品范围、架构和验收契约；
5. 可行性文档中的 EvalScope 能力、参数语义和安全边界；
6. EvalScope v1.10.0 参考源码；
7. 当前代码和测试所反映的真实接口。

实施计划是开发范围的主要依据。

可行性文档中“第一阶段可以直接使用 EvalScope CLI/service、不必二次开发”的结论，只代表最小可行部署判断，不用于缩减已经确认的重写计划。

如果计划中的接口、路径或技术假设与 EvalScope v1.10.0 源码不一致：

- 不得静默猜测；
- 先定位源码证据和真实调用链；
- 保留产品目标；
- 在 bo-llmeval 中做最小兼容调整；
- 如果调整会改变公开 API、数据模型、安全边界或验收结果，先报告再继续。

## 四、项目目标

在 bo-llmeval 中构建一个可公开部署、单管理员使用的 LLM 供应商测试平台：

- 保留 EvalScope 的 benchmark、模型调用、能力评测、性能压测和 Agent bridge 能力；
- 不保留 EvalScope 旧 Flask service、文件扫描 Dashboard、outputs 报告契约和旧任务生命周期；
- 使用 FastAPI、Pydantic、SQLAlchemy 2、Alembic、MySQL 8；
- 前端使用 React 19、TypeScript、Vite、Vitest；
- MySQL 是唯一持久化事实源；
- evaluation/performance worker 与 Agent worker 分离部署；
- Agent 必须运行在私有节点上的一次性受限容器中；
- 所有配置、快照、日志、样本、请求、指标、报告和 Agent trace 持久化到 MySQL；
- 公共节点不得执行 Agent 容器；
- MySQL 不得暴露公网端口。

## 五、实施范围

严格执行实施计划中的 Task 1—15，并按以下 Milestone 顺序推进：

1. Foundation：Task 1—3；
2. Configuration：Task 4—7；
3. Execution：Task 8—11；
4. Product UI：Task 12—13；
5. Release：Task 14—15。

必须从 bo-llmeval 中第一个尚未完成的 Task 开始。

不要假设项目完全空白，也不要因为计划列出了文件路径就机械地重复创建。先检查已有实现，并建立：

“实施计划中的目标 → bo-llmeval 当前文件和接口”

的映射。

每个 Milestone 的测试全部通过后，才能开始下一个 Milestone。

如果环境中提供以下 Skill，按实施计划要求使用：

- `superpowers:subagent-driven-development`，优先；
- 或 `superpowers:executing-plans`。

## 六、开始实施前的只读检查

首先：

1. `cd /Users/zhangsubo/Code/bo-llmeval`
2. 检查当前分支和工作区状态；
3. 识别用户已有改动、未跟踪文件和未完成实现；
4. 读取项目规则和相关文档；
5. 检查现有依赖、目录结构、测试设施和数据库配置；
6. 搜索已有 helper、类型、组件、模型、路由和 worker；
7. 追踪即将修改的函数及所有调用者；
8. 检查 bo-llmeval 当前使用 EvalScope 的方式；
9. 只读核实参考仓库中的相关实现；
10. 运行第一个待实施 Task 的最小基线测试。

不得覆盖、回退、删除或重新格式化用户的无关改动。

如果工作区存在相关未提交改动，应在其基础上继续，并明确说明如何避免冲突。

## 七、开发方式

严格采用逐 Task 的 TDD 流程：

1. 确认 Task 尚未完成；
2. 阅读该 Task 涉及的完整调用链；
3. 编写或补齐失败测试；
4. 运行测试，确认失败原因确实对应缺失功能；
5. 实现让测试通过的最小完整改动；
6. 运行聚焦测试；
7. 运行必要的相关回归测试；
8. 执行类型、lint、迁移或构建检查；
9. 检查 diff 和仓库边界；
10. 当前 Task 全部通过后再进入下一 Task。

不得：

- 只创建目录、空类、空函数或 TODO 后声称完成；
- 添加“以后可能需要”的接口、factory、抽象层或配置；
- 在已有依赖能够解决问题时引入新依赖；
- 为一个实现创建无必要的 interface 或 plugin system；
- 为追求文件结构一致而复制整个 EvalScope 仓库；
- 用单元测试全部通过代替真实链路验收；
- 用“代码看起来正确”代替无法运行的测试。

优先顺序：

1. 复用 bo-llmeval 现有实现；
2. 使用 Python/浏览器/数据库原生能力；
3. 使用已安装依赖；
4. 最后才添加最小新代码。

修复缺陷时必须处理共同根因，不要只在单个调用点添加临时补丁。

## 八、EvalScope 集成规则

以 EvalScope v1.10.0 行为为基线，并通过参考源码核实。

必须区分：

- `evalscope eval`：模型能力评测；
- `evalscope perf`：接口性能压测；
- `evalscope service`：旧 Web service，本项目将替换；
- 评测报告与性能报告：不是同一套数据结构和页面。

接口规则：

- 能力评测使用规范化 API root；
- 性能压测使用完整 endpoint；
- OpenAI Chat 派生为 `chat/completions`；
- Responses 派生为 `responses`；
- Embeddings 派生为 `embeddings`；
- Rerank 派生为 `reranks`；
- Anthropic Messages 派生为 `messages`；
- 非标准供应商通过 capability-level override 处理；
- `/models` 同步使用 API root，不得在 chat endpoint 后继续拼接；
- `eval_type=openai_api` 表示 Chat Completions，不得与 Responses API 混用；
- EvalScope 能力评测的 `api_url` 与 perf 的完整 `url` 不得混用。

Native backend 是默认最短路径。

如果 Embedding/Rerank 质量评测确实需要 RAGEval、MTEB 或其他 backend：

1. 先核实 EvalScope v1.10.0 的真实支持；
2. 写最小失败测试；
3. 只增加必要依赖和适配；
4. 不为了“以后可能支持”提前引入所有 backend。

流式指标规则：

- TTFT、TPOT、ITL 只对真实流式 SSE 有意义；
- 非流式请求不得生成误导性 TTFT；
- token 指标必须记录 tokenizer 或供应商 usage 的口径；
- 供应商限流、错误 tokenizer 和协议不兼容不得被解释成模型能力下降。

性能失败记录必须保留：

- HTTP status；
- error body；
- transport error；
- timeout；
- 原始请求和响应；
- 已完成请求的 timings 和 token 数据。

必须能够区分 429、超时和连接失败。

## 九、持久化和任务约束

- MySQL 是唯一持久化事实源；
- worker 临时目录只用于执行 scratch；
- API 不得从临时目录或 outputs 目录读取报告；
- 临时结果必须导入 MySQL；
- 任务创建后不可修改；
- 任务快照必须包含 template、endpoint、model、dataset、quota、generation config 和 credential version；
- 所有任务状态变化写入 `task_events`；
- 日志和 trace 按稳定 ordinal 保存；
- 不增加 Redis 或 Celery；
- worker 使用 MySQL lease；
- claim 必须使用事务和 `FOR UPDATE SKIP LOCKED`；
- 两个 worker 不得领取同一任务；
- expired lease 必须可恢复；
- 取消任务必须真正终止子进程；
- 先优雅终止，超时后再强制终止；
- 已完成的部分结果在任务失败时仍应导入。

持久化记录原则上 append-only。

禁止：

- DELETE API；
- 自动 retention cleanup；
- 外键级联删除；
- 静默覆盖历史 credential、dataset、template 或 report。

只有明确可编辑的配置 head 可以更新，历史版本必须保留。

## 十、安全要求

不得简化：

- 身份认证；
- CSRF；
- 登录限流与锁定；
- session 安全；
- API key 加密；
- 密码哈希；
- 容器隔离；
- 网络隔离；
- 性能配额；
- 审计事件；
- 输入验证。

具体要求：

- 单管理员，不实现注册、多管理员、RBAC、多租户、SSO、TOTP 或邮件找回；
- 仅当管理员表为空时从 `.env` bootstrap；
- 后续 `.env` 变化不得覆盖数据库密码；
- 管理员密码使用 Argon2id；
- API key 使用 AES-256-GCM；
- `CREDENTIAL_ENCRYPTION_KEY` 解码后必须恰好为 32 bytes；
- AAD 绑定 connection ID 和 credential version；
- session 使用随机 token，数据库只保存 SHA-256；
- cookie 必须为 Secure、HttpOnly、SameSite；
- mutation 必须通过 CSRF；
- 配置 CSP、HSTS、`X-Content-Type-Options`、`Referrer-Policy` 和 `Permissions-Policy`；
- 不安装 permissive CORS；
- 只信任配置的反向代理网络；
- API key 不得出现在 API 响应、日志、任务快照、报告、前端存储或测试输出中。

Prompt、response、log 和 Agent trace 按既定产品决策以明文保存到 MySQL，但必须在部署文档中明确这是已接受风险。

## 十一、密钥和外部服务边界

不要要求用户在聊天中发送 API key、密码或私有 endpoint。

需要真实配置时：

- 创建不含秘密的 `.env.example`；
- 如需实际 `.env`，创建受保护且可由用户编辑的文件；
- 告知用户需要填写哪些变量；
- 不读取或输出现有 secret 的值；
- 不把 secret 写入命令行历史、日志、测试 fixture 或 Git。

默认使用：

- MockLLM；
- 本地数据集；
- mock OpenAI/Anthropic server；
- 隔离的 MySQL 测试实例。

在依赖真实供应商前，先跑通：

数据集加载 → 推理 → 评分 → 结果导入 → 报告 → 对比

的完整本地闭环。

未经用户明确授权，不调用真实供应商，不产生 token 费用，也不执行真实高并发压测。

## 十二、性能任务保护

性能任务入队前必须计算并校验：

- RPM；
- TPM；
- concurrency；
- duration；
- request count；
- 预计 input tokens；
- 预计 output tokens；
- supplier ceiling。

规则：

- 缺少供应商 ceiling 时拒绝入队；
- 超过任一 ceiling 时拒绝入队；
- 达到任一 ceiling 的 80% 时要求 `high_load_confirmed=true`；
- 预检结果必须写入不可变任务快照；
- 前端必须展示计算后的请求量和 token budget；
- cancellation 必须中止真实执行，不能只修改数据库状态。

## 十三、Agent 容器要求

Agent 支持范围：

- Claude Code；
- Codex；
- OpenCode；
- Gemini CLI。

Agent 必须运行在单独私有 worker 节点的一次性容器中。

容器必须：

- 非 root UID；
- read-only root filesystem；
- drop Linux capabilities；
- `no-new-privileges`；
- 显式 CPU、memory、PID、disk、timeout 限制；
- 禁止 privileged；
- 禁止 host network；
- 禁止 host mount；
- 仅允许白名单出站网络；
- 只注入当前任务选择的 credential；
- trace 导入后销毁容器；
- 保留取消、超时和失败的最终 exit reason。

公共节点不得运行 Agent 容器。

## 十四、明确不做的内容

第一版不实现：

- AIGC；
- 多模态输入；
- 本地 checkpoint；
- LiteLLM；
- 多管理员；
- RBAC；
- 多租户；
- SSO；
- TOTP；
- 邮件找回；
- 定时测试；
- 告警；
- recurring regression job；
- 报告或数据删除；
- retention cleanup；
- 历史 outputs 导入；
- 强制 backup/restore launch gate；
- Prompt/response 全文搜索。

只有用户明确扩展范围时才增加。

## 十五、测试与验收

每个 Task 使用实施计划指定的测试命令。

每个 Milestone 完成后必须运行该阶段完整测试，最终至少验证：

- `pytest tests/platform -v -m "not live"`；
- EvalScope perf 聚焦回归测试；
- EvalScope CLI smoke test；
- `make lint`；
- 前端全部 Vitest；
- 前端 lint；
- 前端 production build；
- MySQL migration upgrade；
- downgrade 到 base；
- clean re-upgrade；
- mock supplier 到 comparison 的完整 E2E；
- HTTPS redirect；
- Secure cookie；
- 五次失败后的登录锁定；
- CSRF 拒绝；
- supplier、connection、credential、model 流程；
- 能力评测；
- 受限性能测试；
- cancellation；
- Agent container policy 拒绝；
- MySQL-only 报告读取；
- 不存在 DELETE route；
- MySQL 没有公开 host port。

测试失败时：

1. 保留确切命令和错误；
2. 判断是代码缺陷、环境缺失还是计划假设错误；
3. 在任务范围内修复代码缺陷；
4. 不用跳过测试掩盖问题；
5. 无法继续时报告最小阻塞条件和恢复命令。

不得声称未实际运行的检查已经通过。

## 十六、Git 与修改安全

修改前后检查：

- `git status --short`
- `git diff --check`
- `git diff --stat`

确认所有改动均位于：

/Users/zhangsubo/Code/bo-llmeval

不要：

- 回退用户改动；
- 使用 `git reset --hard`；
- 使用 `git checkout --` 清理文件；
- 删除不确定来源的未跟踪文件；
- 修改参考仓库；
- 未经授权 push；
- 为了通过测试改写无关代码。

实施计划中的 commit 命令视为检查点建议。

如果用户没有明确授权 commit，则完成代码和测试后停留在未提交状态，并提供建议的 commit message，不要自行提交或 push。

## 十七、工作汇报

每完成一个 Task 或遇到真实阻塞时，简要报告：

- 当前 Milestone / Task；
- 修改的文件；
- 实现结果；
- 实际运行的测试命令；
- 测试结果；
- 发现的计划与源码差异；
- 尚未完成项；
- 是否需要用户提供环境、权限或 `.env` 配置。

不要重复整份计划，不要输出无证据的“全部完成”。

除非遇到以下真实阻塞，否则不要停在分析阶段或等待宽泛确认：

- 缺少必要文件；
- 缺少写权限；
- 缺少必须由用户填写的 secret；
- MySQL/容器环境不可用且没有安全替代；
- 需求冲突会改变公开契约；
- 操作需要真实供应商调用或高负载授权。

## 十八、现在开始

现在执行：

1. 进入 `/Users/zhangsubo/Code/bo-llmeval`；
2. 读取项目规则和两份依据文档；
3. 检查当前工作区和已有实现；
4. 只读检查 EvalScope 参考仓库；
5. 建立实施计划与 bo-llmeval 实际代码的映射；
6. 定位第一个尚未完成的 Task；
7. 写失败测试；
8. 实现最小完整功能；
9. 运行聚焦测试和必要回归；
10. 汇报证据后继续下一 Task。

始终牢记：

- `/Users/zhangsubo/Code/bo-llmeval` 是唯一可修改的开发仓库；
- `/Users/zhangsubo/Code/EvalScope/evalscope` 是绝对只读的参考仓库。