# LLM 压测与视频 C2PA 检测

## 启动与入口

安装项目依赖后执行 `./start.sh`，在“测试与诊断 → LLM 压测与自检”创建任务。CLI 与 Web 共用 `scripts/test-results/benchmark/`，后台运行不依赖浏览器连接；CLI 任务也可从 Web 查看和停止。

```bash
./.venv/bin/python -m pip install -e '.[dev]'
./.venv/bin/python scripts/benchmark.py --profile 1 --model MODEL_ID --concurrency 10 --requests 100
```

`--profile` 复用现有配置与 1Password；未选配置组时从 `OPENAI_BASE_URL`、`OPENAI_API_KEY` 读取临时配置。不提供 API Key 命令行参数，不修改 `.env`。

## 并发与负载

- 并发为从 1 起的正整数，不固定为 100～500，也不将 500 硬编码为上限。
- 固定并发：`--concurrency 50 --requests 1000`。
- 渐增并发：`--concurrency 500 --ramp-up 1 --requests 1000`，在 1 秒内升至目标；不指定时直接发起。
- 固定 RPS：`--rps 20 --concurrency 100 --requests 1000`。requests 此时是计划槽位上限，饱和或调度落后时跳过槽位，不积压后补发；报告展示已发与跳过数量。
- `--duration 60` 限制当前阶段发压时间；先到请求数或时长上限即停止发新请求。排空仍计入全程吞吐窗口。
- 阶梯：`--stages stages.json`；文件为 JSON 数组，例如下面的配置。每档独立发压并排空，不混算阶段分位数。

```json
[
  {"concurrency": 10, "requests": 100, "ramp_up_s": 1},
  {"concurrency": 100, "requests": 500, "ramp_up_s": 2},
  {"concurrency": 10, "requests": 100}
]
```

`--warmup` 预热样本写入独立文件，不纳入正式统计。`--timeout` 为单请求总超时，`--silence-timeout` 为正文/工具进度静默超时（reasoning 和心跳不重置），`--total-timeout` 为任务总时限。

CLI 第一次 Ctrl+C 停止发新请求并排空，再次 Ctrl+C 取消在途；Web 提供这两种停止方式。没有自动重试，失败不会被重试成功掩盖。

## 模板与指标

支持 `chat/completions`、`responses`，默认流式；`--no-stream` 仅统计可观测指标。模板：`short`、`long_input`、`long_output`、`reasoning`、`tools`；自定义正文用 `--prompt-file`。工具模板仅校验 `lookup_city` 的 city 参数，不执行外部工具。

- TTFT 是首个非空正文片段，不含心跳、角色、推理。
- 首推理、首工具事件单独计时。
- SSE 片段不等于 token，普通接口的精确 ITL 为不可用；另报片段间隔及每请求最长正文静默。
- 可手动指定 `--tokenizer-encoding cl100k_base` 或 `o200k_base` 估算正文 token 数及 TPOT。必须自行确认与模型匹配，不能自动套用于任意供应商；首次加载可能下载 tokenizer 数据。TPOT 估算使用首末正文片段跨度 / (正文 token 数−1)，首片段批量输出会产生偏差，仅一个片段时不估算。
- mean、p50、p95、p99、max 使用 nearest-rank；p99 样本少于 100 时提示不足。
- 主延迟分布仅含成功样本，另列失败耗时与失败、超时、取消、无正文、截断数量。HTTP 200 不代表协议完整或场景成功。
- 输出吞吐按完成请求的上报 usage 聚合，包含推理的 usage 不当作正文 TPOT 分母。缺失不补零，展示覆盖样本量。
- 没有 SLO 不自动判性能合格，不计算 Goodput。比较时展示参数差异、有效样本量及口径兼容性。

结果包含 `result.json`、`requests.jsonl`、`warmup.jsonl`、`requests.csv`、`summary.json`、`report.md`。逐请求证据不保存正文；自定义提示词只保存摘要，复现时须重新提供原样本。事件元数据最多保存每请求 256 条，记录截断；响应总量限制为 8 MiB。密钥及 URL 不进入报告。

结果中心可使用压测专用 AI 解读，汇总与判定由确定性代码计算；AI 不改变验收状态。

## 自检与证据边界

`--suite selfcheck` 或 Web“错误码自检 + 性能观测”会先发起三种受控请求：缺少凭证、无效参数、随机不存在模型。自检请求与后续性能样本分开统计，结果保存预期/实际 HTTP 状态。当前默认兼容规则分别接受 401/403、400/422、400/404。

流式中断、流内错误、超时、工具调用完整性、429/503 与 Retry-After 由实际请求观测。`--strict-errors` 将观察到的 429/503 缺失 Retry-After 判为失败；通用模式不将该要求视为 HTTP 的普遍强制规则。

未能触发的场景显示未验证。不会通过耗尽账户余额触发 402；500/502 等服务端故障、服务端取消后资源释放仍需配合可控环境证据。降载恢复可以配置高低负载阶段观察，但没有恢复阈值时不自动判达标。

## 发压端本地校准

以下命令只调用独立本地模拟进程，不调用模型接口：

```bash
./.venv/bin/python scripts/benchmark_selfcheck.py --concurrency 100 200 500 --requests 1000 --hold 2
```

校准显式使用 1 秒渐增、2 秒模拟请求持有时间；报告保存实际服务端峰值、机器、Python、句柄限制、调度延迟及 `calibration.json`。模拟器与发压器在同机不同进程，仍会竞争机器资源；不可外推真实模型容量。瞬时大量建连可能触发本地监听队列限制，应比较直接突发与渐增的结果，不能悄悄把前者的失败抹去。

## 视频 C2PA

平台默认集成 [官方 c2pa-python SDK](https://github.com/contentauth/c2pa-python)，随 `pip install -e .` 安装，无需单独安装命令行工具。SDK 在隔离子进程中执行，保留超时终止和报告大小限制，已验证版本为 0.38.2。

独立诊断脚本未开启 C2PA 时仍只需标准库。开启时优先使用 SDK；未安装 SDK 时，可使用 PATH 或 Python 可执行文件同目录中的 [c2patool](https://github.com/contentauth/c2patool/releases)（已验证 0.28.4）。显式设置 `C2PATOOL_PATH` 可覆盖 SDK 选择。报告的 `engine`、`tool_version` 记录实际校验器。

```bash
# 在原视频命令上附加选项，下载完成后检测
python3 scripts/test-openai-api.py --api-type video --c2pa-check

# 对已有结果补检，不重新生成
./.venv/bin/python scripts/c2pa_check.py scripts/test-results/openai-video/RESULT_FOLDER
```

Web 工作台提供下载后检测开关，结果页提供检测/重新检测。`c2pa.json` 为最近报告，`c2pa-history/` 保存各次检测。

检测签名、内容绑定、信任三个独立维度。凭证缺失只提示；工具缺失、解析失败、外部凭证无法验证标为未完成。没有明确成功状态码不能根据退出码推断通过。

当前为**离线检测**：关闭远程 Manifest 与在线 OCSP 获取，避免文件内引用触发任意网络访问。可通过 `C2PA_TRUST_ANCHORS` 指定本地信任锚 PEM，报告记录其 SHA-256；不会自动下载或更新信任列表。可信时间戳、签发者及来源历史按校验器报告展示，C2PA 不证明画面事实真实。

## 已知边界

当前不提供分布式发压、任意模型 tokenizer 自动匹配、逐 token 精确 ITL、自动服务端故障注入、运行中任意热修改并发、自动续跑中断任务或收费推算。实际长期容量仍须在目标机器上按业务负载验证。校准工具与执行租约目前面向 macOS/Linux（使用 POSIX 资源和文件锁）。
