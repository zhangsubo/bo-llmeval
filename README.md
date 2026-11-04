# OpenAI 兼容接口测试与 AI 诊断可视化平台

本项目基于 [scripts/test-openai-api.py](scripts/test-openai-api.py) 与 [scripts/analyzer.py](scripts/analyzer.py)，提供 **CLI 命令行** 与 **Web 可视化工作台** 双端互通的 OpenAI 兼容接口（`chat/completions`、`responses`、`video`、`seedance`、`images/generations`、`bailian-video`）诊断测试及 `test-results` 大模型智能解读能力。

> 原 EVALPLAT 平台代码已完整归档至 `archive/` 目录。

## 核心特性

1. **六种协议诊断测试**：
   - `chat/completions` 与 `responses`：支持 SSE 流式（精确记录客户端 TTFT、增量事件时间序列）与非流式。
   - `video`（OpenAI 兼容视频接口）：支持分辨率（`720p`/`1080p`/`4k` 或 `size`）、时长、比例、音频、水印、多模态参考素材（视频/图片/音频 `http(s)` 或 `asset://`）、已有任务 `--task-id` 查询及 MP4 视频自动流式下载与校验。
   - `seedance`（火山方舟原生接口）：支持 `create`、`query`、`list`（分页与状态/模型/多任务 ID 筛选）、`delete`（取消/删除）及首尾帧输入。
   - `images/generations`：支持 Qwen Image 3.0 及通用 Images（wanx-v2 / cogview-4 / cogview-3-plus），文生图、URL/Base64 参考图、多图下载与预览。
   - `bailian-video`：支持 Wan 2.7 文生视频与 Wan 2.6 i2v-flash 图生视频、原生任务轮询与下载。参数与示例见 [测试手册](docs/manual-openai-api.md#openai-images-与百炼原生视频)。
2. **`test-results` 大模型智能诊断解读**：
   - 支持配置独立的 **AI 结果解读专用模型**（`ANALYZER_NAME`、`ANALYZER_BASE_URL`、`ANALYZER_API_KEY`、`ANALYZER_MODEL`，兼容任意 OpenAI `chat/completions` 接口）。
   - 自动解包多层网关嵌套 JSON 错误（如 `body.message` 二次序列化错误），计算 TTFT、流式间隔分布、轮询耗时抖动、缓存命中率、思维链占比及视频下载速率，生成结构化中文诊断报告（原子保存为结果目录下的 `analysis.md` 与 `analysis.json`）。
   - 解读按实际操作区分提交、查询、生成与下载，保留请求阶段和文本截断证据；超时/中断不等同于服务端生成失败，图片部分保存失败单独说明。
   - 判断遵循证据边界：不按固定延迟阈值评级，不从单次耗时断言网关或推理瓶颈，不对第三方网关强制套用官方路径；未上报用量不补零、不推算收费，也不评价未查看的图片/视频质量。已有报告需点击「重新 AI 解读」才会使用新规则。
3. **可视化操作界面（FastAPI + React 19 + TypeScript）**：
   - **测试工作台**：可视化配置所有接口参数、实时监控流式输出与视频轮询日志、随时安全中断测试。
   - **结果与 AI 解读中心**：多维筛选历史结果、内嵌播放本地 `video.mp4`、查看请求耗时瀑布图、一键流式生成 AI 诊断解读、一键复用 `task_id` 继续查询下载。
   - **配置管理中心**：可视化管理 `scripts/openai-api-config` 中的多组被测凭证与 AI 解读专用模型配置（API Key 掩码脱敏保护，与 CLI 实时互通）。

## 快速开始

### 1. 启动可视化 Web 平台

```bash
chmod +x start.sh
./start.sh
```

启动后将自动在浏览器打开 `http://127.0.0.1:18088` 即可使用完整可视化界面。
如需前后端热更新开发模式：

```bash
./start.sh --dev
```

### 2. 命令行 (CLI) 使用方式

```bash
cp -n scripts/openai-api-config.example scripts/openai-api-config
# 运行交互式测试
python3 scripts/test-openai-api.py

# 测试完成后自动调用 AI 解读模型生成 analysis.md
python3 scripts/test-openai-api.py --api-type chat/completions --prompt "你好" --analyze

# 对已有的历史 test-results 目录单独执行 AI 智能解读
python3 scripts/test-openai-api.py --analyze-dir scripts/test-results/openai-video/20261014T133128Z-c2a804e4
# 或直接调用解读模块
python3 scripts/analyzer.py scripts/test-results/openai-video/20261014T133128Z-c2a804e4 --fallback-rules
```

## 移动云素材库（Web）

视频模型测试菜单新增「【移动云】虚拟人像（aigc）」与「【移动云】真人人像」，支持独立 OpenAPI 签名、素材与素材组管理、入库轮询及真人认证的默认/自定义回调流程。配置沿用 1Password 素材库连接组，结果独立保存。使用方式与联调边界见 [移动云素材库测试手册](docs/manual-ecloud-assets.md)。

## 火山引擎素材库（CLI / Web）

新增独立工具 `python3 scripts/test-volcengine-assets.py`，支持素材/素材组的创建、查询、列表、更新、删除，以及素材入库状态轮询；真人人像支持生成 H5 认证链接、查询认证后的 GroupId 并上传素材。通过 `--endpoint` 或 `VOLC_ASSET_ENDPOINT` 指定根地址，不自动追加 `/api/v3`、`/v1` 或 `/business-api`。官方 AK/SK 签名与可选的兼容网关 Bearer 鉴权、配置和使用示例见 [素材库测试手册](docs/manual-volcengine-assets.md)。也可在 Web 左侧“素材库”分组中的“虚拟人像（AIGC）”或“真人人像”页面操作，支持真人认证链接、会话自动填入、入库轮询和历史报告。可在配置管理新增多个具名素材库配置组，分别保存根地址和 AK/SK，并在素材库页面选择使用（各组 AK/SK 独立存于 1Password）；也可临时填写凭证，仅用于当前页面请求。

## LLM 压测与视频 C2PA

新增“LLM 压测与自检”页面与 `scripts/benchmark.py`：并发数可设置，支持固定并发、固定 RPS、阶梯加压、历史对比、JSON/JSONL/CSV/Markdown 导出。统计正文 TTFT、片段间隔、最长静默、吞吐量和 p95/p99；可选择 tokenizer 估算 TPOT，普通 SSE 无逐 token 时间时不伪造 ITL。没有 SLO 不自动判性能合格。

视频测试可选下载后 C2PA 检测，也可在结果页对历史视频补检；签名、内容绑定和信任分别展示，凭证缺失仅提示。平台默认集成 `c2pa-python` SDK，无需单独安装 `c2patool`；独立诊断脚本仍可只用 Python 标准库运行，启用 C2PA 时可使用 SDK 或外部校验器。

CLI 示例、指标口径、自检边界和本地发压校准见 [压测与 C2PA 手册](docs/manual-benchmark.md)。

## 自动化测试

```bash
# 运行全部单元测试（含 CLI 协议回归、AI 解读与后端 API 集成测试）
./.venv/bin/python -m pytest -q

# 前端代码检查与构建验证
cd frontend && npm run lint && npm run build
```


## 1Password 密钥存储

常用接口 API Key、AI 解读 API Key 与素材库 AK/SK 统一保存到 1Password。先安装 `op` CLI，在 1Password 桌面客户端启用 CLI 集成，再在配置管理填写账号与保险库。保存配置时会迁移现有明文/钥匙串凭证，读回核验成功后才原子替换本地配置。`scripts/openai-api-config` 只保存 `op://` 引用和非敏感元数据，配置页面不会读取密钥；CLI/Web 执行时从 1Password 获取。

更新密钥创建新条目，旧版本保留；删除配置或清除密钥只解除本地引用，保险库条目保留供恢复。账号/保险库变更会将引用的密钥复制到新目标并核验。保存失败不切换配置，已创建的新条目会尝试归档。桌面客户端锁定、权限不足或 `op` 不可用时会明确报错，不回退到明文存储。已有环境变量和临时输入继续支持，`.env` 不修改。

字段：`ONEPASSWORD_ACCOUNT`、`ONEPASSWORD_VAULT` 指定账号/保险库；`API_KEY1`、`ANALYZER_API_KEY` 为各自的密钥引用，`ASSET_CREDENTIAL_STORE1` 引用包含 AK/SK 的隐藏字段。共享配置时需确保使用者具有相应保险库权限。CLI 使用标准库封装 `op`，不需要额外 Python 依赖；请保留同目录的 `secret_store.py`。
