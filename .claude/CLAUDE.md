# LLM API 服务评测管理平台 - 开发指导

本项目使用 GSD (Get Stuff Done) 工作流进行开发。

## 项目概览

**产品名称：** LLM API 服务评测管理平台  
**项目代码：** EVALPLAT  
**核心价值：** 在相同测试输入、参数、负载和 Tokenizer 口径下，客观横向比较供应商 LLM API 服务的性能、质量和成本

## 当前状态

- **里程碑：** v1.0 首版发布
- **当前阶段：** Phase 1 - 基础设施与数据模型
- **总阶段数：** 12 个阶段
- **总需求数：** 87 个 v1 需求

## 技术栈

- **后端：** Python 3.11+, FastAPI, SQLAlchemy 2, Alembic, Pydantic, EvalScope
- **前端：** React 18, TypeScript, Vite
- **数据库：** PostgreSQL 14+（唯一数据库，兼作队列）
- **部署：** Docker Compose
- **代码沙箱：** Docker 容器隔离

## 核心设计原则

1. **PostgreSQL 是唯一数据库和队列** - 不使用 Redis、Celery、Elasticsearch
2. **EvalScope 是唯一评测引擎** - 通过 Python API 调用，不拼接 shell 命令
3. **双口径 Token 统计** - 供应商 usage 用于成本，本地 Tokenizer 用于性能对齐
4. **任务配置快照不可变** - 保证历史结果可复现
5. **性能请求不自动重试** - 保留真实失败率和延迟分布
6. **静态多页 HTML 报告** - 可离线查看，可删除敏感明细页
7. **API Key 加密存储** - 页面/日志/报告统一脱敏

## 下一步操作

### 开始 Phase 1

```bash
/gsd-plan-phase 1
```

这将创建 Phase 1 的详细执行计划。

### 查看项目状态

```bash
/gsd-progress
```

### 查看完整路线图

参见：`.planning/ROADMAP.md`

## 重要文档

- `.planning/PROJECT.md` - 项目背景和核心价值
- `.planning/REQUIREMENTS.md` - 完整需求列表（87 个 v1 需求）
- `.planning/ROADMAP.md` - 12 个阶段的路线图
- `.planning/STATE.md` - 当前项目状态
- `prd/prd_v1.0_final.md` - 完整产品需求文档（权威参考）

## 开发规范

### 数据库迁移

```bash
# 创建迁移
alembic revision --autogenerate -m "description"

# 执行迁移
alembic upgrade head
```

### API 开发

- 使用 FastAPI 路由
- Pydantic schema 做请求/响应验证
- SQLAlchemy 2 ORM 访问数据库
- 所有时间使用 UTC 存储

### 前端开发

- React + TypeScript
- 仅保证最新稳定版桌面 Chrome
- 中文界面
- 不做移动端适配

### 安全要求

- API Key 使用应用层加密存储
- 所有输出路径统一脱敏（页面、日志、CSV、JSON、HTML）
- 用户生成内容按纯文本转义
- 域名/CIDR 白名单阻止 SSRF
- 防止 DNS rebinding

## GSD 工作流

本项目使用 GSD 工作流管理开发过程：

1. **计划阶段** - `/gsd-plan-phase N` 创建详细计划
2. **执行阶段** - `/gsd-execute-phase N` 执行计划
3. **验证阶段** - `/gsd-verify-work N` 验证交付
4. **转换阶段** - 完成后进入下一阶段

每个阶段的计划、执行和验证结果都会保存在 `.planning/phases/phase-N/` 目录下。

## 参考资料

- [EvalScope 官方文档](https://evalscope.readthedocs.io/)
- [FastAPI 文档](https://fastapi.tiangolo.com/)
- [SQLAlchemy 2 文档](https://docs.sqlalchemy.org/)
- [React 文档](https://react.dev/)

---

本文件由 GSD 工作流自动生成。如需修改项目设置，请编辑 `.planning/config.json`。
