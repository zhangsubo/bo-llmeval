# EVALPLAT

LLM API 服务评测管理平台。平台在统一输入、生成参数、负载和 Tokenizer 口径下，对多个供应商的 OpenAI-compatible API 做性能、质量和成本对比，并生成可离线查看的静态报告。

## 能力范围

- 供应商、连接、模型部署、价格版本和 API Key 加密管理
- Tokenizer、内置/自定义数据集、测试模板及不可变版本
- 任务预检、配置快照、PostgreSQL FIFO 队列、取消、重试和 Run lease/fencing
- 固定并发、固定 RPS、阶梯负载，以及冷启动/预热/稳态阶段
- 非流式/流式 Chat Completions 指标：Latency、TTFT、TPOT、ITL、吞吐和双口径 Token
- native/EvalScope 质量评测与独立 Docker 代码沙箱
- 指标对比、版本化 HTML 报告、CSV/JSON 导出和历史清理
- SSRF/DNS pinning、输出脱敏、审计事件和运行监控

## 技术结构

```
src/evalplat/          FastAPI 应用、SQLAlchemy 模型、服务层和 API 路由
tests/                 后端单元/集成测试
frontend/              React 19 + TypeScript + Vite + Ant Design 管理台
migrations/            Alembic 迁移
Dockerfile             app/worker 镜像
Dockerfile.code-worker 独立代码沙箱 Worker 镜像
```

PostgreSQL 是唯一数据库，也是 Run 队列；常驻 `worker` 执行普通请求，`code-worker` 通过宿主 Docker socket 创建受限沙箱容器。

## 本地开发

```bash
cp .env.example .env
# 编辑 .env：至少替换 DATABASE_URL 密码和 APP_MASTER_KEY
./.venv/bin/pip install -e ".[dev,evalscope]"
./.venv/bin/alembic upgrade head
./.venv/bin/uvicorn evalplat.main:create_app --factory --reload
```

API 默认在 `http://127.0.0.1:8000`，健康检查为 `/healthz` 和 `/readyz`。前端开发：

```bash
cd frontend
npm install
npm run dev
```

## Compose

```bash
docker compose up -d --build migrate app worker code-worker
```

需要前端容器时再启动 `frontend`，访问 `http://localhost:8080`。生产环境应使用随机 `APP_MASTER_KEY`、非开发数据库凭据和固定 digest 的 `SANDBOX_IMAGE`，并设置 `SANDBOX_REQUIRE_IMMUTABLE_IMAGE=true`。

代码沙箱相关配置：

- `DOCKER_GID`：Linux 上设为宿主 `/var/run/docker.sock` 的实际组 ID；Docker Desktop 通常为 `0`
- `SANDBOX_HOST_DIR`：宿主机绝对路径，必须与容器内 `/sandbox` 的 bind mount 对应
- `SANDBOX_WORK_DIR`：Worker 可见的沙箱路径，Compose 默认 `/sandbox`

## 验证

### 手工 API 诊断

`scripts/test-openai-api.py` 支持交互选择 base URL/key 配置、输入 model ID、提示词和选择
`chat/completions`、`responses`、`video`、`seedance` 四种接口。文本默认流式，记录首个非空文本
片段的 TTFT、请求耗时、增量事件、返回文本及 usage；视频默认通过 `/v1/video/generations/{id}` 查询，
记录异步任务耗时、下载地址及 Token 用量，成功后自动下载为本次结果目录下的 `video.mp4`。
这是独立诊断工具，不写入平台评测任务或数据库；单次调用不替代 EvalScope 评测。
`seedance` 使用火山方舟原生 `/api/v3/contents/generations/tasks` 协议，支持创建、查询、
分页列表、取消/删除和首尾帧输入；`--no-wait` 可只创建或单次查询。

```bash
cp -n scripts/openai-api-config.example scripts/openai-api-config
# 在 scripts/openai-api-config 中填写常用配置；模板内不填写真实 key
python3 scripts/test-openai-api.py
# 直接选择接口；追加 --no-stream 可测试非流式文本请求
python3 scripts/test-openai-api.py --api-type responses
# 继续查询已有视频任务（不创建任务，无需 Model ID）
python3 scripts/test-openai-api.py --task-id '你的任务ID'
# 若服务仅支持 videos 状态查询接口，可手动切换
python3 scripts/test-openai-api.py --task-id '你的任务ID' --video-query-api videos
# 方舟原生接口；需选择单独配置的方舟地址和 key
python3 scripts/test-openai-api.py --api-type seedance
python3 scripts/test-openai-api.py --api-type seedance --task-id 'cgt-你的任务ID'
python3 scripts/test-openai-api.py --api-type seedance --action list
```

配置只读，结果保存到 `scripts/test-results/<接口类型>/<UTC 时间戳>/`，两者已加入 Git 忽略。
默认配置和结果目录均相对于脚本所在目录定位，不受运行时工作目录影响。
视频按 Seedance 接入指南发送 JSON，选择 video 后可选 720p（默认）、1080p、4K；
指定 `--resolution` 或 `--size` 会跳过菜单。其余默认 5 秒、16:9、开启音频、关闭水印；
随后可选择参考素材：1 不使用、2 视频、3 图片、4 音频，可用逗号多选；三种均支持 HTTP(S) URL 或 `asset://asset-<素材ID>`，与提示词一起发送。
`--reference-video`、`--reference-image`、`--reference-audio` 可重复或组合指定，`--no-reference` 跳过素材选择；兼容旧参数 `--no-reference-video`，已有任务查询不询问。
支持 `--resolution` 等参数，默认查询路径为 `/v1/video/generations/{id}`。
详见 [手工 API 诊断说明](docs/manual-openai-api.md)。测试：

```bash
./.venv/bin/python -m pytest tests/test_manual_openai*.py -q
```

### 平台验证

```bash
./.venv/bin/pytest -q
./.venv/bin/python -m compileall -q src tests
./.venv/bin/alembic check
cd frontend && npm run lint && npm run build
```

当前代码已通过 472 个后端测试、编译检查、前端构建，并在临时本地 PostgreSQL 实例通过 Alembic upgrade/check。默认开发 DSN 因角色 `evalplat` 不存在不可用；发布前仍需在目标环境完成 PostgreSQL 多 Worker 并发、真实供应商网络与 EvalScope SDK、Docker 沙箱权限/禁网、容量压测和浏览器 E2E 验收；这些不能由 SQLite/fake engine 单测替代。

更多需求、路线图和审查记录见 `.planning/PROJECT.md`、`.planning/REQUIREMENTS.md`、`.planning/ROADMAP.md`、`.planning/CODE-REVIEW.md` 及 `prd/prd_v1.0_final.md`。
