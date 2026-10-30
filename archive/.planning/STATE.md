---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: verification
last_updated: "2026-09-14T00:00:00.000Z"
progress:
  total_phases: 12
  completed_phases: 12
  total_plans: 6
  completed_plans: 6
---

# State: LLM API 服务评测管理平台

**最后更新：** 2026-09-14（12 个阶段代码完成，真实基础设施验收进行中）

## 项目参考

参见：`.planning/PROJECT.md`

**核心价值：** 在相同测试输入、参数、负载和 Tokenizer 口径下，客观横向比较供应商 LLM API 服务的性能、质量和成本，为生产准入提供可复现证据。

## 里程碑状态

**v1.0 首版 — 实现完成，发布验收未完成**

## 已完成阶段

| 阶段 | 名称 | 状态 | 测试 |
|------|------|------|------|
| 1 | 基础设施与数据模型 | ✅ | 21 个 ORM 实体 + 骨架 |
| 2 | 供应商与连接管理 | ✅ | 加密 + SSRF + CRUD |
| 3 | 测试资源管理 | ✅ | Tokenizer/数据集/模板 |
| 4 | 任务引擎核心 | ✅ | Task CRUD + 提交/取消 |
| 5 | 性能评测 | ✅ | Run/Request 查询 |
| 6 | 质量评测与代码沙箱 | ✅ | native/EvalScope 证据 + 代码 Worker |
| 7 | 监控、取消与重试 | ✅ | Dashboard + 状态监控 |
| 8 | 结果对比与可视化 | ✅ | 可比性检查 + 对比 |
| 9 | 报告生成与导出 | ✅ | 版本化静态报告 + CSV/JSON 导出 |
| 10 | 安全加固与审计 | ✅ | 脱敏 + 白名单 |
| 11 | 系统管理与运维 | ✅ | 仪表盘 + 健康检查 |
| 12 | 集成测试与部署 | ✅ | 端到端流程测试 |

## 交付物清单

### 后端代码（`src/evalplat` 当前 117 个 Python 文件）

```
src/evalplat/
├── main.py                     # FastAPI 应用工厂
├── config.py                   # pydantic-settings
├── constants.py                # 状态枚举
├── core/
│   ├── crypto.py               # Fernet 加密/解密
│   ├── ssrf.py                 # SSRF 防护
│   ├── errors.py               # 统一错误处理
│   └── logging.py              # structlog 配置
├── db/                         # SQLAlchemy engine + session
├── models/                     # 21 个 ORM 实体
├── schemas/                    # Pydantic 请求/响应
├── services/                   # 业务逻辑层（41 个 service 模块）
└── api/routes/                 # FastAPI 路由（21 个路由模块）
```

### 测试（48 个测试文件，472 个测试；下表为代表性覆盖）

| 测试文件 | 覆盖阶段 | 测试数 |
|----------|----------|--------|
| test_health.py | 1 | 3 |
| test_schema.py | 1 | 16 |
| test_encryption.py | 2 | 7 |
| test_ssrf_guard.py | 2 | 13 |
| test_suppliers_crud.py | 2 | 9 |
| test_connections_crud.py | 2 | 15 |
| test_phase3.py | 3 | 19 |
| test_tasks.py | 4 | 16 |
| test_runs.py | 5 | 7 |
| test_monitoring.py | 7 | 5 |
| test_comparison_groups_crud.py | 2 | 6 |
| test_comparisons.py | 8 | 7 |
| test_security.py | 10 | 13 |
| test_admin.py | 11 | 7 |
| test_prices_crud.py | 2 | 7 |
| test_integration.py | 12 | 3 |
| **总计** | | **472（含未列出的专项测试）** |

### 规划文档

- `.planning/PROJECT.md` — 项目背景
- `.planning/REQUIREMENTS.md` — 87 个 v1 编号需求
- `.planning/ROADMAP.md` — 12 阶段路线图
- `.planning/STATE.md` — 项目状态
- `.planning/phases/01-*/` — Phase 1 计划（3 PLAN + RESEARCH + VALIDATION）
- `.planning/phases/02-*/` — Phase 2 计划（3 PLAN + RESEARCH + VALIDATION）
- `.planning/phases/03-*/` — Phase 3 目录

## 需求覆盖度（代码路径初步覆盖，非发布验收结论）

| 需求类别 | 总数 | 已覆盖 |
|----------|------|--------|
| 供应商与连接 (SUPP) | 8 | 8 ✅ |
| 模型与对比组 (MODEL) | 5 | 5 ✅ |
| 测试资源 (RES) | 8 | 8 ✅ |
| 任务创建 (TASK) | 7 | 7 ✅ |
| 队列与执行 (EXEC) | 7 | 7 ✅ |
| 监控 (MON) | 8 | 8 ✅ |
| 性能 (PERF) | 6 | 6 ✅ |
| 质量 (QUAL) | 4 | 4 ✅ |
| 协议 (PROTO) | 5 | 5 ✅ |
| 结果 (RESULT) | 7 | 7 ✅ |
| 报告 (REPORT) | 8 | 8 ✅ |
| 安全 (SEC) | 7 | 7 ✅ |
| 系统 (SYS) | 7 | 7 ✅ |
| **总计** | **87** | **87 ✅** |

## 关键指标

- **总阶段数**: 12 — 全部完成
- **总需求数**: 87 个 v1 编号需求 — 核心代码链路已有覆盖；真实 EvalScope、PostgreSQL 多 Worker 和 Docker 沙箱仍需部署环境验收
- **源文件数**: 117（`src/evalplat`）
- **ORM 实体数**: 21
- **service 模块数**: 41
- **路由模块数**: 21
- **测试文件数**: 48
- **测试通过数**: 472
- **测试失败数**: 0

## 技术栈

- **后端**: Python 3.11+, FastAPI, SQLAlchemy 2, Alembic, Pydantic
- **加密**: cryptography (Fernet)
- **HTTP**: httpx（连接测试）
- **前端**: React 19, TypeScript, Vite, Ant Design（管理台已实现）
- **数据库**: PostgreSQL 14+
- **部署**: Docker Compose

## 下一步（发布阻断项）

1. 真实 PostgreSQL 多 Worker、Docker 沙箱和 EvalScope SDK/transport 验收（本机 Compose 已完成基础 smoke；仍需目标部署环境验收）
2. EvalScope SDK 的真实网络边界、Tokenizer/内置质量数据集的上游下载验收
3. 报告/指标容量压测，以及真实浏览器 E2E 体验
4. 部署环境配置 `DOCKER_GID` 和 `SANDBOX_HOST_DIR`，完成多 Worker、Docker socket 权限与真实供应商/EvalScope 验收；Docker Desktop 默认 GID 0，Linux 需按宿主 socket 覆盖

---

*状态文件由 GSD 工作流自动维护*
