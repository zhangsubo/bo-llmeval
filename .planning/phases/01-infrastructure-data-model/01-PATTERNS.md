# Phase 1: 基础设施与数据模型 - Pattern Map

**Mapped:** 2026-08-18
**Files analyzed:** 28（全部为新建文件）
**Analogs found:** 0 / 28（greenfield，代码库中无任何源码可作为 analog）

## ⚠️ Greenfield 结论

经验证，本仓库为 **greenfield（绿地）项目**：

- 无任何 `.py` / `.ts` 源码文件（glob 结果为空）
- 无 `pyproject.toml` / `package.json` / `go.mod` / `Cargo.toml`
- 仓库仅含文档：`AGENTS.md`、`prd/`、`research/`、`.planning/`、`annex/`、`flowcharts/`、`prototype/`、`templates/`、`review/`、`versions/`
- 本项目无 `.claude/skills/` 或 `.agents/skills/` 目录

**因此不存在代码库内部 analog。本文件不虚构 analog**；planner 应以下列两个来源作为 pattern 权威来源：

1. **`01-RESEARCH.md` 的 Architecture Patterns（Pattern 1–7）与 Code Examples 节** — 全部摘自 SQLAlchemy / Alembic / FastAPI / PostgreSQL 官方文档，可视为本阶段的"外部 pattern 库"
2. **`AGENTS.md` 的开发规范** — 锁定技术栈与硬性纪律（UTC 时间、无物理删除、无 Redis/Celery 等）

## File Classification

下表从 `01-RESEARCH.md` 的「Recommended Project Structure」（第 164–201 行）与「Wave 0 Gaps」提取。**所有文件均为新建**，"Pattern 来源" 列指向 RESEARCH.md 的具体章节。

| New File | Role | Data Flow | Pattern 来源（RESEARCH.md） | 备注 |
|----------|------|-----------|------------------------------|------|
| `docker-compose.yml` | config | infrastructure | Code Examples → docker-compose.yml | db / migrate / app 三服务 |
| `Dockerfile` | config | infrastructure | Code Examples + Security Domain | 非 root USER（安全要求） |
| `.env.example` | config | configuration | Project Structure | 只含键名，不含真实秘密 |
| `pyproject.toml` | config | configuration | Standard Stack（版本锁定清单） | 含 pytest 配置 `[tool.pytest.ini_options]` |
| `alembic.ini` | config | migration | Pattern 3 | **不硬编码 DSN** |
| `migrations/env.py` | config | migration | Pattern 3 | `target_metadata`、`compare_type=True`、从 Settings 读 URL |
| `migrations/versions/0001_initial_schema.py` | migration | DDL/batch | 数据模型设计（15 张表）+ 索引策略 | autogenerate 后**人工审阅** |
| `src/evalplat/__init__.py` | config | — | Project Structure | 空包标记 |
| `src/evalplat/main.py` | provider | request-response | Pattern 5（app factory + lifespan） | `create_app()` 工厂，启动校验 DB 可达 |
| `src/evalplat/config.py` | config | configuration | Don't Hand-Roll（pydantic-settings） | `Settings` 类，`.env` 加载 |
| `src/evalplat/db/base.py` | model | — | Pattern 1 + Pattern 2 | **命名约定 Base 必须先于一切模型落笔**（Pitfall 1） |
| `src/evalplat/db/session.py` | service | CRUD | Pattern 5 + Pitfall 5 | 同步 engine、`pool_pre_ping=True`、显式 pool 参数 |
| `src/evalplat/models/*.py`（15 个实体） | model | CRUD | Pattern 2 + 数据模型设计表 | 每实体一文件，与表一一对应 |
| `src/evalplat/schemas/*.py` | model | request-response | Code Examples → 三层分离 | Pydantic v2，与 ORM 分离 |
| `src/evalplat/api/router.py` | route | request-response | Pattern 5 | 聚合 v1 路由 |
| `src/evalplat/api/deps.py` | middleware | request-response | Project Structure | `get_db`、分页参数依赖 |
| `src/evalplat/api/routes/health.py` | route | request-response | Code Examples → 健康检查端点 | `/healthz`（不触 DB）与 `/readyz`（`SELECT 1`）分层（Pitfall 4） |
| `src/evalplat/api/routes/suppliers.py` | controller | CRUD | Code Examples → 供应商 CRUD 样板 | 本阶段唯一完整 CRUD；DELETE 返回 405 |
| `src/evalplat/core/errors.py` | utility | request-response | Pattern 6 | 统一 `{error:{code,message,details}}` 结构 |
| `src/evalplat/core/logging.py` | utility | event-driven | Pattern 7 | structlog JSON；**不记录 Authorization header** |
| `src/evalplat/constants.py` | utility | — | 数据模型设计 → 状态枚举 | `String + CHECK`，不用 PostgreSQL ENUM |
| `tests/conftest.py` | test | CRUD | Validation Architecture | 测试 DB fixture：alembic upgrade + 会话隔离 |
| `tests/test_health.py` | test | request-response | Phase Requirements → Test Map | 成功标准 2 |
| `tests/test_migrations.py` | test | DDL/batch | Test Map + `alembic check` | 成功标准 1/3（升级/回滚往返） |
| `tests/test_suppliers_api.py` | test | request-response | Test Map + 边界用例 | 成功标准 4 |
| `tests/test_schema.py` | test | DDL/batch | Test Map | SCOPE-1（SQLAlchemy inspect 断言约束） |
| `tests/test_queue_schema.py` | test | CRUD（并发） | Test Map + Pattern 4 | EXEC-01（SKIP LOCKED 并发认领冒烟） |
| `tests/test_errors.py` | test | request-response | Test Map | SCOPE-6（404/422 统一错误结构） |

## Pattern Assignments（外部 pattern 来源映射）

由于没有代码库 analog，以下为 planner 编写各 plan action 时应**直接引用 RESEARCH.md 的对应 pattern 章节**，按文件分组：

### 数据库基础层（先于所有模型落笔）

- **`db/base.py`** → 引用 **Pattern 1**（RESEARCH.md 第 203–224 行）：`MetaData` 命名约定 + `DeclarativeBase`。同时建 `TimestampMixin`（Pattern 2，第 239–246 行）
- **`db/session.py`** → 引用 **Pattern 5** 中的 engine 配置：`pool_pre_ping=True`；并遵守 **Pitfall 5**：显式写 `pool_size`/`max_overflow`（如 5+5）
- **`migrations/env.py`** → 引用 **Pattern 3**（第 265–273 行）：`compare_type=True`、`compare_server_default=True`、DSN 从 `Settings` 读取

### 模型层（15 张表）

- **所有 `models/*.py`** → 引用 **Pattern 2**（第 226–261 行）：`Mapped[]` + `mapped_column` 全类型注解、UUID 主键 `server_default=text("gen_random_uuid()")`、`DateTime(timezone=True)` + `server_default=func.now()`
- 表结构逐表定义 → 引用 **数据模型设计** 表（第 354–370 行）
- 索引 → 引用 **索引策略** 表（第 380–393 行）
- 关键纪律：
  - `runs` 表必须含队列预埋列（`lease_owner` / `lease_expires_at` / `fencing_token` / `heartbeat_at`）与部分索引（**Pattern 4**，第 275–302 行）
  - 状态列用 `String + CHECK`，禁用 PostgreSQL ENUM（第 376 行）
  - `attempts.raw_response` 用 `Text` 存字节级原文 + `extracted_fields JSONB` 另存结构化提取（**Pitfall 6**，第 449–451 行）
  - JSONB 列不挂 `MutableDict`——快照字段语义不可变（**Pitfall 3**，第 434–436 行）
  - `tasks`/`runs` 需 `idempotency_key` UNIQUE 约束（约束表第 35 行）
  - 无物理删除：`suppliers`/`connections`/`model_deployments` 只有 `status` 流转，不提供 `deleted_at`

### 应用骨架层

- **`main.py`** → 引用 **Pattern 5**（第 304–330 行）：`create_app()` 工厂 + `lifespan`（启动 `SELECT 1` 校验 DB 可达，**不自动跑迁移**——Anti-Pattern 1）
- **`api/routes/health.py`** → 引用 **Code Examples → 健康检查端点**（第 501–517 行）：`/healthz` 不触 DB、`/readyz` 报 DB 延迟
- **`api/routes/suppliers.py`** → 引用 **Code Examples → 供应商 CRUD 样板**（第 530–539 行）：路由 → Pydantic schema → ORM 三层分离
- **`core/errors.py`** → 引用 **Pattern 6**（第 334–343 行）：统一错误 JSON 结构；兜底 handler 不回显 `str(exc)`
- **`core/logging.py`** → 引用 **Pattern 7**（第 345–348 行）：structlog JSON + request_id 绑定；预留脱敏过滤器位置
- **`config.py`** → 引用 **Don't Hand-Roll** 表（第 407–416 行）：pydantic-settings，禁止散落 `os.environ.get`

### 基础设施层

- **`docker-compose.yml`** → 引用 **Code Examples → docker-compose.yml**（第 462–497 行）：`postgres:16` + `TZ=UTC` + healthcheck；`migrate` 服务容器化执行 `alembic upgrade head`
- **`Dockerfile`** → 非 root USER（Security Domain，第 682 行）

### 测试层

- **所有 `tests/*.py`** → 引用 **Validation Architecture**（第 611–655 行）：pytest + httpx `ASGITransport`；`conftest.py` 每个测试会话跑 `alembic upgrade head`，测试间 TRUNCATE/回滚隔离
- 边界用例清单 → 引用第 636–642 行（空库列表、迁移往返、并发健康检查、DB 不可达时 healthz/readyz 分层行为、UTC 时区校验）

## Shared Patterns（适用于所有新文件的横切约定）

### 代码与注释语言

- **代码标识符（变量/函数/类/表名/字段名）：一律英文**，遵循 RESEARCH.md 数据模型设计的推荐命名（`suppliers`、`connections`、`model_deployments` 等；英文命名为 [ASSUMED A4]，计划阶段需用户确认一次）
- **docstring 与行内注释：使用中文** — 项目文档（AGENTS.md、PRD、RESEARCH.md）全部为中文，前端为中文界面（AGENTS.md 第 87 行）；面向用户的错误消息（如错误结构中的 `message` 字段）用中文（Pattern 6 示例：`"供应商不存在"`）
- 状态/类型枚举的**存储取值用英文**（`active/disabled/archived`、`draft/queued/running/...`，[ASSUMED A5]），语义对应 PRD 中文状态机

### 时间处理（硬性规范，AGENTS.md 开发规范）

- 所有列：`DateTime(timezone=True)`（`timestamptz`）+ `server_default=func.now()`
- 应用代码禁止 `datetime.utcnow()`（Python 3.12 已弃用）与裸 `datetime.now()`；需要时用 `datetime.now(timezone.utc)`（Pitfall 2）
- DB 容器 `TZ=UTC`

### 同步 SQLAlchemy 架构决策（[ASSUMED A1]）

- Phase 1 使用**同步 engine + `Session`**，不用 async；FastAPI 同步路由由线程池执行
- planner 可采纳或推翻，但一旦定下，session 层与所有 route 签名需一致

### 安全基线（本阶段"立规矩"）

- 日志不得记录 `Authorization` header；错误响应不得回显内部细节（ASVS V7）
- `.env` 必须入 `.gitignore`，仓库只提供 `.env.example`
- `connections.api_key_ciphertext` 列本阶段建成 **nullable**，加密逻辑属 Phase 2/10，禁止提前实现半成品（Pitfall 7）
- `base_url` 字段本阶段加格式校验（http/https、无 userinfo），完整 SSRF 白名单属 Phase 2/10

### 迁移纪律

- 每次模型变更必须 autogenerate + **人工审阅**；表/列重命名、CHECK 语义变化必须手写（Pattern 3）
- 迁移永远由独立命令/容器执行，严禁埋进 lifespan（Anti-Pattern 1）
- 验证标准：`alembic upgrade head` → `alembic check` 零 diff → `downgrade base && upgrade head` 往返成功（Code Examples 第 519–526 行）

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| 全部 28 个文件 | — | — | Greenfield 项目：仓库无任何源码。Planner 应使用 RESEARCH.md Pattern 1–7 / Code Examples（官方文档来源）作为 pattern 权威，并遵循上文 Shared Patterns 约定 |

## Metadata

**Analog search scope:** 仓库根目录全量 glob（`**/*.py`、`**/*.ts`、`pyproject.toml`、`package.json`、`go.mod`、`Cargo.toml`）+ 顶层目录列表
**Files scanned:** 0 个源码文件（确认 greenfield）
**Pattern extraction date:** 2026-08-18
**Pattern 权威来源:** `01-RESEARCH.md`（Pattern 1–7、数据模型设计、索引策略、Code Examples、Pitfalls、Don't Hand-Roll）+ `AGENTS.md`（开发规范、核心设计原则）
