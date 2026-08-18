# Phase 1: 基础设施与数据模型 - Research

**Researched:** 2026-08-18
**Domain:** PostgreSQL schema 设计 / SQLAlchemy 2.0 ORM / Alembic 迁移 / FastAPI 应用骨架 / Docker Compose 开发环境
**Confidence:** HIGH（技术栈已锁定，核心机制均有官方文档支撑；具体表结构设计为基于 PRD 的推荐方案，需在 plan 阶段确认）

## Summary

本阶段是绿地（greenfield）项目的第一步：仓库中目前只有 PRD、研究文档和 `.planning/`，没有任何源码。技术栈已由 AGENTS.md 和 PRD §12.1 锁定（Python 3.11+、FastAPI、SQLAlchemy 2、Alembic、Pydantic、PostgreSQL 14+、Docker Compose），本研究不做技术选型，只回答"如何按标准做法把它搭对"。

三个最关键的发现：

1. **队列能力必须在建表时预埋，而不是 Phase 4 再加列。** PRD §8.8 要求 PostgreSQL 持久化 FIFO 队列 + Worker lease + 递增 fencing token（PRD 原文："lease 必须包含递增 fencing token；失去 lease 的旧 Worker 后续上报不得覆盖新 Worker 或终态结果"）。PostgreSQL 官方文档确认 `SELECT ... FOR UPDATE SKIP LOCKED` 是 queue-like table 的标准并发认领机制，`percentile_cont` 是内置 ordered-set aggregate，两者都无需额外组件 [CITED: research/official_source_research_v0.1.md §6.1，来源为 postgresql.org 官方文档]。因此 `runs` 表在 Phase 1 就必须带 `status / queued_at / lease_owner / lease_expires_at / fencing_token` 等列，否则 Phase 4 要做风险更高的在线加列迁移。
2. **不可变快照用 JSONB 列承载，版本化实体用"只增不改"的版本表承载。** PRD §6.2 列出任务入队前必须冻结的完整快照清单（连接标识、Base URL、模型 ID、`credential_version`、数据集 checksum、Tokenizer、生成参数、负载、价格与汇率、引擎版本等），属于宽而嵌套的结构，用 `tasks.config_snapshot JSONB` + `runs.run_snapshot JSONB` 存储最贴合；数据集/模板/价格则按 PRD §8.4–8.6 的"已被引用不得覆盖，更新产生新版本"语义建模为版本表。
3. **索引策略现在定基调、不做过度设计。** PRD §13.1 明确基准容量为 10,000,000 条请求/Attempt，且"达到上述规模前不引入分区表"。因此 `requests`/`attempts` 用 `(run_id, id)` 复合 btree 支持 keyset 分页即可，不建分区、不建 BRIN；`runs` 的 FIFO 认领用 `status = 'queued'` 部分索引。

**Primary recommendation:** 一次性在初始 Alembic 迁移中建立全部核心表（含队列/审计/备注/报告等后续阶段使用的表），ORM 采用 SQLAlchemy 2.0 `Mapped[]` 全类型注解 + 显式命名约定 + `timestamptz` UTC 时间戳；FastAPI 用 app factory + lifespan 模式；Phase 1 只实现 health 端点和供应商（suppliers）一组完整 CRUD 作为骨架模板，其余实体留到各自阶段。

## Project Constraints (from AGENTS.md / PRD)

以下约束具有与锁定决策同等的效力，研究和计划不得与之冲突：

| 来源 | 约束 |
|------|------|
| AGENTS.md 核心设计原则 1 / PRD §8.8.1 | PostgreSQL 是唯一数据库和持久化任务队列，禁止 Redis、Celery、Elasticsearch |
| AGENTS.md 核心设计原则 2 / PRD §12.1 | EvalScope 是唯一评测引擎，通过 Python API 调用，禁止拼接 shell 命令（本阶段不集成，但依赖结构要预留） |
| AGENTS.md 核心设计原则 3 / PRD §9.3 | 双口径 Token 统计：供应商 usage + 本地 Tokenizer 两套数值，互不覆盖 → `requests`/`attempts` 表必须各有两套 Token 列 |
| AGENTS.md 核心设计原则 4 / PRD §6.2 | 任务配置快照不可变，历史结果不因后续配置/价格/密钥变化而改变 |
| AGENTS.md 核心设计原则 5 / PRD §10.3 | 性能请求不自动重试；质量请求有限重试 → Request/Attempt 分离建模 |
| AGENTS.md 开发规范 | 所有时间使用 UTC 存储 |
| PRD §13.1 | 容量基准：≤30 供应商、500 连接、2,000 模型部署、10,000 任务、10,000,000 请求/Attempt；达到前不建分区表 |
| PRD §8.2.2 | API Key 应用层加密存 PostgreSQL，主密钥来自受保护 `.env` → `connections` 表存密文 + `credential_version`，不存明文 |
| PRD §8.2.1 / §8.3.1 | 供应商、模型部署不提供物理删除，只有状态流转（启用/停用/归档） |
| PRD §8.6 / §8.5.4 | 模板与数据集版本化：被任务引用后不可改，修改产生新版本 |
| PRD §13.3 | 入队、取消、重试、报告生成、清理必须使用幂等键或状态条件更新 → `tasks`/`runs` 需要 `idempotency_key` 唯一约束 |

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| EXEC-01 | PostgreSQL 作为唯一数据库和持久化 FIFO 队列 | 见"Architecture Patterns → Pattern 4：队列预埋设计"与"Standard Stack"；SKIP LOCKED 认领 SQL 已给出 [CITED: postgresql.org/sql-select] |
| SCOPE-1 | 数据库 schema 设计（供应商、连接、模型、数据集、Tokenizer、任务、Run、请求、Attempt） | 见"Architecture Patterns → 数据模型设计"完整 15 张表定义 |
| SCOPE-2 | 核心领域模型类（SQLAlchemy ORM） | 见 Pattern 1（命名约定 Base）与 Pattern 2（Typed Model 示例） |
| SCOPE-3 | FastAPI 应用骨架和路由结构 | 见 Pattern 5（app factory + lifespan + 路由注册）与项目结构 |
| SCOPE-4 | Alembic 数据库迁移配置 | 见 Pattern 3（Alembic 配置要点） |
| SCOPE-5 | 开发环境 Docker Compose 配置 | 见 Code Examples → docker-compose.yml |
| SCOPE-6 | 基础错误处理和日志配置 | 见 Pattern 6（全局异常处理）与 Pattern 7（结构化日志） |
| SCOPE-7 | 健康检查端点 | 见 Code Examples → /healthz 与 /readyz 模式 |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| 表结构/约束/索引 | Database (PostgreSQL) | — | 唯一数据库，约束下推到 DB 层（唯一、外键、CHECK）保证任何写路径都受保护 |
| 领域模型与事务边界 | API / Backend (SQLAlchemy ORM) | — | 业务一致性（状态机、快照冻结）在 Python 事务中表达 |
| Schema 演进 | Database (Alembic) | — | Alembic autogenerate 比对 ORM metadata 与真实 schema |
| 配置管理 | API / Backend (pydantic-settings) | — | `.env` 注入 DB 连接串与主密钥（PRD §11.2） |
| 请求/响应校验 | API / Backend (Pydantic) | — | ASVS V5 输入校验落在 API 边界 |
| 健康检查 | API / Backend | Database | `/healthz` 进程内活；`/readyz` 执行 `SELECT 1` 验证 DB 可达 |
| 队列认领（Phase 4） | Database (SKIP LOCKED) | API / Backend | 认领语义必须由 DB 行锁保证，不能在应用层模拟 |
| 依赖供给（PostgreSQL） | CDN / Static 之外的基础设施 | Docker Compose | 开发环境用 Compose 起 `postgres:16`；生产用外部 PostgreSQL（PRD §12.2） |

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `fastapi` | 0.141.1 | Web 框架 | 锁定技术栈；自动生成 OpenAPI 文档便于 Postman/curl 验证 [VERIFIED: PyPI registry 2026-08-18 + AGENTS.md 锁定栈] |
| `sqlalchemy` | 2.0.52 | ORM | 锁定技术栈；2.0 全类型注解 `Mapped[]` 是当前官方推荐写法 [VERIFIED: PyPI registry + docs.sqlalchemy.org] |
| `alembic` | 1.19.1 | 数据库迁移 | 锁定技术栈；SQLAlchemy 官方配套迁移工具 [VERIFIED: PyPI registry + github.com/sqlalchemy/alembic] |
| `pydantic` | 2.13.4 | 请求/响应校验与 settings 基础 | 锁定技术栈；FastAPI 0.1xx 基于 Pydantic v2 [VERIFIED: PyPI registry] |
| `psycopg` | 3.3.4 | PostgreSQL 驱动（psycopg 3） | SQLAlchemy 2.0 的现代驱动，连接串方言 `postgresql+psycopg://`；替代 legacy psycopg2 [VERIFIED: PyPI registry；CITED: docs.sqlalchemy.org dialects] |
| `uvicorn` | 0.52.3 | ASGI 服务器 | FastAPI 官方推荐运行方式 [VERIFIED: PyPI registry] |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `pydantic-settings` | 2.15.0 | `.env` / 环境变量配置加载 | Settings 管理（DB DSN、主密钥、日志级别）[VERIFIED: PyPI registry；CITED: fastapi.tiangolo.com settings 文档推荐] |
| `structlog` | 26.1.0 | 结构化 JSON 日志 | 业务日志与审计上下文绑定；若团队偏好零依赖可用 stdlib `logging` + JSON formatter 替代 [VERIFIED: PyPI registry 2026-08-18] |
| `pytest` | 9.1.1 | 测试框架 | Nyquist 验证层 [VERIFIED: PyPI registry] |
| `pytest-asyncio` | latest | asyncio 测试支持 | FastAPI/SQLAlchemy async 测试 [ASSUMED — 本阶段若走同步 engine 可不需要] |
| `httpx` | 0.28.1 | 测试客户端 | `httpx.ASGITransport` 直接打 FastAPI app，无需起真实端口 [VERIFIED: PyPI registry] |

**架构决策（建议）**：Phase 1 使用 **同步 SQLAlchemy engine + `Session`**，不使用 async engine。理由：FastAPI 会把同步 route 放进线程池执行；评测平台是内部低频管理面 API + 后续 Worker 轮询写库，同步模型更简单、Alembic 集成更直接、避免 async session 的 lazy-load 陷阱。此建议为 Claude's discretion（AGENTS.md 未规定同步/异步），planner 可采纳或推翻。[ASSUMED]

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `psycopg` (v3) | `psycopg2-binary` | psycopg2 是上一代驱动；SQLAlchemy 2.x 官方把 psycopg3 列为推荐；无理由选旧的 |
| 同步 engine | `asyncpg` + async engine | async 对高并发 I/O 有优势，但增加复杂度（Alembic 需要 async 桥接、lazy-load 报错难排查）；内部平台不需要 |
| `structlog` | stdlib `logging` | structlog 上下文绑定（`bind(task_id=...)`）对后续审计日志更顺手；stdlib 零依赖但要手写 JSON formatter |
| UUID 主键 | bigint identity | 见"数据模型设计"：业务实体用 UUID，高频流水表用 bigint |

**Installation:**

```bash
pip install "fastapi==0.141.1" "sqlalchemy==2.0.52" "alembic==1.19.1" \
  "pydantic==2.13.4" "pydantic-settings==2.15.0" "psycopg[binary]==3.3.4" \
  "uvicorn[standard]==0.52.3" "structlog==26.1.0"
pip install --dev "pytest==9.1.1" "httpx==0.28.1"
```

**Version verification:** 以上版本均于 2026-08-18 通过 `https://pypi.org/pypi/<pkg>/json` 实时查询确认存在；`psycopg[binary]` extra 提供预编译 libpq，避免开发机编译依赖。

## Package Legitimacy Audit

> 已执行 `gsd-tools query package-legitimacy check --ecosystem pypi`。注意：该 seam 对 PyPI 生态没有下载量数据源（`unknown-downloads`），且把"最近发过新版"一律记为 `too-new`——这两个信号对 fastapi/sqlalchemy 这类活跃维护的规范包必然触发，属于**启发式误报**，不是 slop 信号。所有包均为 AGENTS.md/PRD 锁定技术栈或其官方配套，registry 存在性 + 官方文档来源已于本 session 确认。

| Package | Registry | Age | Downloads | Source Repo | Verdict | Disposition |
|---------|----------|-----|-----------|-------------|---------|-------------|
| fastapi | PyPI | ~7 年（首版 2018） | seam 无数据 | github.com/fastapi/fastapi | SUS（误报：too-new/unknown-downloads） | 批准 — 锁定栈，官方文档来源确认 |
| sqlalchemy | PyPI | ~20 年 | seam 无数据 | sqlalchemy.org / github.com/sqlalchemy | SUS（误报） | 批准 — 锁定栈 |
| alembic | PyPI | ~14 年 | seam 无数据 | github.com/sqlalchemy/alembic | SUS（误报） | 批准 — 锁定栈 |
| pydantic | PyPI | ~8 年 | seam 无数据 | github.com/pydantic/pydantic | SUS（误报） | 批准 — 锁定栈 |
| pydantic-settings | PyPI | ~7 年 | seam 无数据 | github.com/pydantic/pydantic-settings | SUS（误报） | 批准 — FastAPI 官方 settings 文档推荐 |
| uvicorn | PyPI | ~8 年 | seam 无数据 | github.com/encode/uvicorn | SUS（误报） | 批准 — FastAPI 官方推荐 ASGI server |
| psycopg | PyPI | psycopg3 ~4 年（项目 ~15 年） | seam 无数据 | github.com/psycopg/psycopg | SUS（误报） | 批准 — SQLAlchemy 官方方言 |
| structlog | PyPI | ~12 年 | seam 无数据 | github.com/hynek/structlog（seam 报 no-repository，实为遥测缺失） | SUS（误报 + no-repository） | 批准但有兜底：可用 stdlib logging 替代，见 Alternatives |
| pytest | PyPI | ~20 年 | seam 无数据 | github.com/pytest-dev/pytest | SUS（误报） | 批准 |
| httpx | PyPI | ~6 年 | seam 无数据 | github.com/encode/httpx | SUS（误报） | 批准 |

**Packages removed due to [SLOP] verdict:** 无
**Packages flagged as suspicious [SUS]:** 全部 10 个包被 seam 标记 SUS，但逐个人工核对后确认均为规范包（锁定技术栈 + 官方文档/官方仓库来源）。**建议 planner 不为此插入逐包 `checkpoint:human-verify`**，改为在 Wave 0 用一个批量确认（或视锁定栈豁免）；若遵循保守策略，首个 `pip install` 任务前加一个 checkpoint 即可。

*补充说明：Python 包不存在 npm 式 postinstall 脚本风险面；`psycopg[binary]` 的 wheel 来自官方 psycopg 组织。*

## Architecture Patterns

### System Architecture Diagram

```
                     ┌────────────────────────────────────────────┐
                     │           Docker Compose (dev)             │
                     │                                            │
  curl / Postman ──► │  ┌──────────────┐      ┌───────────────┐  │
  浏览器(后续阶段) ──► │  │  FastAPI app │─────►│  postgres:16  │  │
                     │  │  (uvicorn)   │      │  (唯一数据库) │  │
                     │  └──────┬───────┘      └───────┬───────┘  │
                     │         │                      │          │
                     │    lifespan 启动时              ▲          │
                     │    校验 DB 可达                  │          │
                     │         │                      │          │
                     │  ┌──────▼───────────────────┐  │          │
                     │  │ 路由层 /healthz /readyz   │  │          │
                     │  │ /api/v1/suppliers (CRUD) │──┘          │
                     │  └──────┬───────────────────┘             │
                     │         ▼                                 │
                     │  Pydantic schema 校验 → Service/ORM       │
                     │  → SQLAlchemy Session → PostgreSQL        │
                     └────────────────────────────────────────────┘

  迁移路径（独立进程，不进请求路径）:
    alembic upgrade head ──► PostgreSQL（建表/改表）
    docker compose run --rm migrate（容器化执行，见 docker-compose.yml）
```

数据流：HTTP 请求 → FastAPI 路由 → Pydantic 校验 → SQLAlchemy 事务 → PostgreSQL；响应原路返回。Alembic 迁移是独立的带外通道，应用启动只校验 DB 可达、不自动跑迁移（见 Pitfall 4）。

### Recommended Project Structure

```
bo-llmeval/
├── docker-compose.yml          # 开发环境：db + app + migrate
├── .env.example                # DATABASE_URL / APP_MASTER_KEY / LOG_LEVEL（不含真实秘密）
├── pyproject.toml              # 依赖与工具配置（pytest、ruff）
├── alembic.ini
├── migrations/
│   ├── env.py                  # 注入 naming-convention metadata 与 DATABASE_URL
│   └── versions/
│       └── 0001_initial_schema.py
├── src/evalplat/
│   ├── __init__.py
│   ├── main.py                 # create_app() factory + lifespan
│   ├── config.py               # pydantic-settings Settings
│   ├── db/
│   │   ├── base.py             # Base + naming_convention + TimestampMixin
│   │   └── session.py          # engine、SessionLocal、get_db dependency
│   ├── models/                 # 每个实体一个文件，与表一一对应
│   │   ├── supplier.py  connection.py  model_deployment.py ...
│   ├── schemas/                # Pydantic 请求/响应模型（与 ORM 分离）
│   ├── api/
│   │   ├── router.py           # 聚合 v1 路由
│   │   ├── deps.py             # 公共依赖（分页参数、DB session）
│   │   └── routes/
│   │       ├── health.py       # /healthz /readyz
│   │       └── suppliers.py    # 本阶段唯一完整 CRUD 样板
│   ├── core/
│   │   ├── errors.py           # 统一异常类型 + handler
│   │   └── logging.py          # structlog/stdlog JSON 配置
│   └── constants.py            # 状态枚举（TaskStatus/RunStatus/...）
└── tests/
    ├── conftest.py             # 测试 DB、app fixture
    ├── test_health.py
    ├── test_migrations.py      # upgrade head / downgrade base 往返
    └── test_suppliers_api.py
```

### Pattern 1: 命名约定 Base（必须在第一个模型之前建立）

**What:** 在 `MetaData` 上显式声明所有约束/索引的命名约定。
**When to use:** 永远。没有命名约定时 PostgreSQL 自动生成的约束名不可预测，Alembic autogenerate 在后续改约束时会产生无法对齐的迁移 diff，这是最典型的 Alembic 事故源 [CITED: docs.sqlalchemy.org/en/latest/core/constraints.html "Configuring a Naming Convention for a MetaData Collection"]。
**Example:**

```python
# Source: docs.sqlalchemy.org/en/latest/core/constraints.html (naming convention 官方示例)
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
```

### Pattern 2: Typed Declarative Model + UTC 时间戳

**What:** SQLAlchemy 2.0 全类型注解模型，时间一律 `timestamptz` + DB 端默认值。
**Example:**

```python
# Source: docs.sqlalchemy.org/en/latest/orm/declarative_tables.html (Mapped/mapped_column 官方写法)
import uuid
from datetime import datetime
from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False,
    )

class Supplier(TimestampMixin, Base):
    __tablename__ = "suppliers"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    short_name: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)      # 原厂/云厂商/其他
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'active'"))
    remark: Mapped[str | None] = mapped_column(Text)
    # 无物理删除（PRD §8.2.1）——不提供 deleted_at，只流转 status
```

要点：`gen_random_uuid()` 自 PostgreSQL 13 起内置，无需 `pgcrypto` 扩展 [CITED: postgresql.org/functions-uuid]；`server_default` 放数据库端，保证绕过 ORM 的写入（迁移脚本、运维手工 SQL）也有值；时间戳必须 `DateTime(timezone=True)` 对应 `timestamptz`，存 UTC（AGENTS.md 硬性规范）。

### Pattern 3: Alembic 配置要点

**What:** autogenerate 对齐 ORM metadata；迁移与运行时分离。
**关键配置（`migrations/env.py`）:**
- `target_metadata = Base.metadata`（继承 Pattern 1 的命名约定）
- `compare_type=True`、`compare_server_default=True`——否则列类型/默认值变更不会被 autogenerate 发现
- 从 `Settings` 读 `DATABASE_URL`，不在 `alembic.ini` 里硬编码
- 事务内迁移（PostgreSQL 支持 DDL 事务，默认 `transaction_per_migration=False` 即可让整个 revision 原子提交）
**纪律（写入团队规范）：**
- 每次模型变更必须生成并**人工审阅** autogenerate 结果；autogenerate 不检测：枚举值增删、CHECK 约束语义变化、表/列重命名（会识别成 drop+add）——这些必须手写 [CITED: alembic.sqlalchemy.org autogenerate "What does Autogenerate NOT detect?"]
- `alembic check`（1.19 提供）可在 CI/验证阶段断言"ORM metadata 与 DB schema 无 diff"，直接支撑成功标准 3 [CITED: alembic.sqlalchemy.org]

### Pattern 4: 队列预埋设计（Phase 1 建表，Phase 4 启用）

**What:** `runs` 表同时是业务记录和队列表；Phase 1 把列和部分索引建好。
**PRD 依据（逐字引用，[VERIFIED: prd/prd_v1.0_final.md:417-421]）：**
> "Worker 使用数据库 lease 和 heartbeat 认领 Run。"
> "lease 必须包含递增 fencing token；失去 lease 的旧 Worker 后续上报不得覆盖新 Worker 或终态结果。"

**认领 SQL（Phase 4 将使用，Phase 1 在注释/文档中固化）:**

```sql
-- Source: postgresql.org/docs/current/sql-select.html (FOR UPDATE / SKIP LOCKED 官方语义)
UPDATE runs
SET status = 'running',
    lease_owner = :worker_id,
    lease_expires_at = now() + interval '60 seconds',
    fencing_token = fencing_token + 1,
    heartbeat_at = now()
WHERE id = (
    SELECT id FROM runs
    WHERE status = 'queued'
    ORDER BY queued_at ASC          -- FIFO：严格按入队时间（PRD §8.8.1）
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING *;
```

全局并发上限（默认 4，PRD §8.8.1/EXEC-02）由认领前的 `SELECT count(*) FROM runs WHERE status='running'` 门控，配合系统配置表读取上限值。fencing token 校验模式：Worker 每次写结果携带自己认领时的 token，`UPDATE ... WHERE id=:id AND fencing_token=:token`，影响行数为 0 即已失去 lease，上报被拒绝（验收用例 A-29）。

### Pattern 5: FastAPI App Factory + Lifespan

**What:** `create_app()` 工厂函数 + `lifespan` 上下文管理器；启动时校验 DB 可达，不自动迁移。
**Example:**

```python
# Source: fastapi.tiangolo.com/advanced/events (lifespan 官方模式)
from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlalchemy import text

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))   # 启动即失败，好过第一个请求才失败
    app.state.engine = engine
    yield
    engine.dispose()

def create_app() -> FastAPI:
    app = FastAPI(title="LLM API 服务评测管理平台", lifespan=lifespan)
    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")
    return app
```

`on_event("startup")` 已废弃，一律用 lifespan [CITED: fastapi.tiangolo.com]。`pool_pre_ping=True` 防止连接池拿到被服务端断开的连接（内部平台长时间空闲后首请求必踩）。

### Pattern 6: 统一错误处理

**What:** 业务异常 → 统一 JSON 错误结构；Pydantic `RequestValidationError` → 422 统一格式；兜底 `Exception` handler 记日志返回 500，**不得回显内部细节**。
**Example 错误结构:**

```json
{ "error": { "code": "SUPPLIER_NOT_FOUND", "message": "供应商不存在", "details": null } }
```

纪律：任何 handler 不得把 `str(exc)`（可能含 DB 连接串、SQL 片段）写进响应 [CITED: ASVS V7 错误处理与日志]。

### Pattern 7: 结构化日志

**What:** structlog JSON 输出 + `contextvars` 绑定 request_id；日志配置在 `core/logging.py`，应用入口单次调用。
**纪律（PRD §11.2 安全要求，本阶段就要立规矩）:** 日志处理器链中预留脱敏过滤器位置；Phase 2 起 API Key 密文/明文都不得进日志，本阶段先确保请求日志中间件不记录 headers 中的 `Authorization`。

### 数据模型设计（核心交付物）

实体清单以 PRD §6.1 为权威来源 [VERIFIED: prd/prd_v1.0_final.md:156-172]。下表字段名为推荐英文命名（PRD 未规定英文字段名 → 命名本身为 [ASSUMED]，语义逐条来自 PRD）：

| # | 表 | 说明与关键字段 | 来源 |
|---|----|----------------|------|
| 1 | `suppliers` | 供应商：name, short_name, type, status(`active/disabled/archived`), remark。无物理删除 | PRD §8.2.1 [VERIFIED: prd/prd_v1.0_final.md:241-243] |
| 2 | `connections` | 服务连接：supplier_id FK, name, region, service_tier, base_url, `api_key_ciphertext`(bytea/text), `credential_version`(int), tls_skip_verify(bool, default false), timeouts/limits **JSONB**, status, remark | PRD §8.2.2 [VERIFIED: prd/prd_v1.0_final.md:247-254]；快照只引用 credential_version、不复制密钥 [VERIFIED: prd/prd_v1.0_final.md:179] |
| 3 | `model_deployments` | 模型部署：connection_id FK, model_id(实际调用 ID), display_name, comparison_group_id FK(可空), context_window, max_output_tokens, supports_streaming, supports_non_streaming, tokenizer_id FK(可空), model_version, source(`synced/manual`), status, remark | PRD §8.3.1 [VERIFIED: prd/prd_v1.0_final.md:279-281]；一个部署首版最多属一个对比组 → 用列而非关联表 [VERIFIED: prd/prd_v1.0_final.md:286] |
| 4 | `comparison_groups` | 对比组：name, default_tokenizer_id FK, remark | PRD §8.3.2 [VERIFIED: prd/prd_v1.0_final.md:285-288] |
| 5 | `model_prices` | 价格版本：model_deployment_id FK, input/output/cached_token_price(numeric), unit, currency, exchange_rate(numeric>0), effective_from, price_version, source(`manual/external`), external_* 预留字段。追加式，不改历史行（汇率变化只影响未来任务） | PRD §8.3.3 [VERIFIED: prd/prd_v1.0_final.md:292-298] |
| 6 | `tokenizers` | Tokenizer 版本：name, source(`modelscope/huggingface/upload/builtin`), resource_id, revision, checksum, size_bytes, status(`pending/downloading/ready/failed/archived`), error, local_path。被引用版本不可覆盖 | PRD §8.4 [VERIFIED: prd/prd_v1.0_final.md:302-308] |
| 7 | `datasets` + `dataset_versions` | 数据集主表（name, kind(`performance/quality`), remark）+ 版本表（dataset_id FK, version, source, license, checksum, sample_count, language, capability_tags JSONB, default_prompt, scoring_method, field_mapping JSONB, token_length_stats JSONB, status, storage_path）。版本不可变 | PRD §8.5 [VERIFIED: prd/prd_v1.0_final.md:322-346]；性能/质量分开管理 [VERIFIED: prd/prd_v1.0_final.md:319] |
| 8 | `test_templates` + `test_template_versions` | 模板主表（name, is_builtin, status）+ 版本表（template_id FK, version, test_type, scenario, dataset_version_id FK, tokenizer_id FK, generation_params JSONB, stream_mode, warmup_count, load_profile JSONB, request_count, retry_policy JSONB, timeouts JSONB, limits JSONB, remark）。内置模板不可改；被使用后修改产生新版本 | PRD §8.6 [VERIFIED: prd/prd_v1.0_final.md:351-357] |
| 9 | `tasks` | 评测任务：name, template_version_id FK, run_mode(`serial/parallel`), status, `config_snapshot` **JSONB**, precheck_result JSONB, idempotency_key **UNIQUE**, queued_at, started_at, finished_at, remark | PRD §8.7/§8.8 [VERIFIED: prd/prd_v1.0_final.md:362-413]；幂等键要求 [VERIFIED: prd/prd_v1.0_final.md:400] |
| 10 | `runs` | 评测 Run（兼队列表）：task_id FK, model_deployment_id FK, seq, status, `run_snapshot` **JSONB**, source_run_id FK(重试来源), queued_at, started_at, finished_at, `lease_owner`, `lease_expires_at`, `fencing_token`(bigint default 0), `heartbeat_at`, error_summary | PRD §6.1/§8.8 [VERIFIED: prd/prd_v1.0_final.md:166,394-421] |
| 11 | `requests` | 请求（逻辑记录）：run_id FK, sample_index, session_id(多轮可空), turn_index, kind(`perf/quality`), phase(`cold_start/warmup/steady`), stream_mode, input_messages JSONB, status, scheduled_at, started_at, finished_at；双口径 Token 列：`vendor_prompt_tokens/vendor_completion_tokens/vendor_total_tokens` + `local_prompt_tokens/local_completion_tokens/local_total_tokens`（全部 nullable） | PRD §8.13.1/§9.3 [VERIFIED: prd/prd_v1.0_final.md:562-566,711-716] |
| 12 | `attempts` | 请求尝试（实际 HTTP 记录）：request_id FK, attempt_index, started_at, finished_at, latency_ms, ttft_ms, tpot_ms, itl_series JSONB, avg_speed/peak_speed/peak_tps(numeric), vendor_usage JSONB, raw_response JSONB, error_category, error_body, http_status, vendor_request_id, response_headers JSONB | PRD §6.1/§9.1/§10.1 [VERIFIED: prd/prd_v1.0_final.md:168,687-699,722-736] |
| 13 | `notes` | 人工备注：target_type(`task/run`), target_id, body, 独立存储不改原始数据 | PRD §8.15.1 [VERIFIED: prd/prd_v1.0_final.md:609-612] |
| 14 | `audit_events` | 审计：occurred_at, actor_type, actor_id（预留）, source_ip, action, target_type, target_id, result, diff JSONB（脱敏）。**不含**密钥与业务正文 | PRD §6.1/§8.16.2 [VERIFIED: prd/prd_v1.0_final.md:171,659-661] |
| 15 | `system_settings` | 系统配置单行/键值表：global_max_parallel_runs(默认 4), base_currency(默认 CNY), 白名单等（白名单后续可拆表，Phase 1 建键值表即可） | PRD §14 [VERIFIED: prd/prd_v1.0_final.md:869] |

**状态枚举（英文取值映射为 Claude's discretion [ASSUMED]；中文状态语义逐字引用 PRD §8.8.2 [VERIFIED: prd/prd_v1.0_final.md:404-406]）：**
> "`草稿 → 排队中 → 运行中 → 已完成/部分完成/失败`"
> "取消分支：`排队中/运行中 → 取消中 → 已取消`"

建议映射：Task/Run status ∈ `draft, queued, running, completed, partially_completed, failed, cancelling, cancelled`；Run 另加 `interrupted_failed`（PRD 原文："执行中的 Worker 失联后，Run 标记为'中断失败'" [VERIFIED: prd/prd_v1.0_final.md:419]）。用 `String + CHECK 约束` 而非 PostgreSQL ENUM——ENUM 加值需要迁移且不能事务内回滚旧版本兼容，CHECK 由 Alembic 正常管理 [ASSUMED，团队惯用实践]。

**JSONB vs JSON:** 一律 `JSONB`（除 `raw_response` 也建议 JSONB——PRD 要求"未识别扩展字段原样保留"（[VERIFIED: prd/prd_v1.0_final.md:490]），JSONB 保留全部数据但会规范化键序/去重键；若严格要求字节级原文保真，`raw_response` 用 `Text` 存原始 JSON 字符串 + 另设 `extracted_fields JSONB` 存结构化提取。**推荐后者**，理由见 Pitfall 6）。其余配置/快照/usage 字段用 JSONB，支持后续 GIN 索引与 `->>` 查询 [CITED: postgresql.org/datatype-json]。

**索引策略（容量基线 [VERIFIED: prd/prd_v1.0_final.md:814-821]：10M requests/attempts，达标前不建分区表）：**

| 表 | 索引 | 理由 |
|----|------|------|
| `runs` | 部分索引 `CREATE INDEX ix_runs_queue ON runs (queued_at) WHERE status = 'queued'` | FIFO 认领查询只扫排队行，部分索引保持极小 |
| `runs` | `(status)` 普通 btree | 全局并发计数 `WHERE status='running'`、监控筛选 |
| `runs` | `(task_id, seq)` | 任务详情列出全部 Run |
| `requests` | `(run_id, id)` 复合 btree | keyset 分页 `WHERE run_id=:r AND id > :cursor ORDER BY id LIMIT :n`，PRD 强制分页（[VERIFIED: prd/prd_v1.0_final.md:575]）；10M 行下 OFFSET 分页会退化 |
| `attempts` | `(request_id, attempt_index)` | Attempt 明细按请求展开 |
| `attempts` | `(run_id, started_at)`（BRIN，可选） | 时间序列图表全表扫 run 内数据；BRIN 对单调时间列体积极小。**可选，Phase 1 可不建** |
| `connections` | `(supplier_id)`；`base_url` 不建唯一（同 URL 多账号合法） | 列表筛选 |
| `model_deployments` | `(connection_id)`、`(comparison_group_id)` | 列表筛选 |
| `audit_events` | `(occurred_at)`、`(target_type, target_id)` | 审计查询 |
| `tasks` | `(status, queued_at)`、`idempotency_key UNIQUE` | 任务列表筛选 + 幂等约束 |

**percentile_cont 可行性验证（Phase 5 依赖，现在确认）：** PostgreSQL 内置 ordered-set aggregate `percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms)`，无需扩展 [CITED: postgresql.org/functions-aggregate，经 official_source_research_v0.1.md §6.1 确认]。10M 行单表按 `run_id` 分组聚合时，每个 Run 的请求数（标准基线约千级）很小，聚合开销可控；瓶颈在按 run_id 过滤的索引扫描，已由 `(run_id, id)` 索引覆盖。注意 `percentile_cont` 不能直接并行/部分聚合，若未来跨 Run 大聚合变慢，可用 `timescaledb` 或预计算汇总表——PRD 明确不提前引入，记录为未来演进（PRD §18"高级存储"）[VERIFIED: prd/prd_v1.0_final.md:949]。

### Anti-Patterns to Avoid

- **应用启动时自动跑迁移：** `alembic upgrade head` 埋进 lifespan 会让多副本/Worker 启动产生迁移竞争，且失败模式诡异。迁移永远由独立命令/容器执行（docker-compose 的 `migrate` service），应用只校验可达性。
- **为"队列"另建一张 queue 表：** PRD 明确 Run 即执行单元，队列表与业务表双写会造成状态撕裂。`runs` 表加状态列 + SKIP LOCKED 就是队列（EXEC-01）。
- **在 ORM 模型里写业务状态机流转外的默认值（如 Python 端 `default=datetime.utcnow`）：** UTC 时间用 `server_default=func.now()`，双写源会导致时钟漂移不一致。
- **Phase 1 就实现全部实体的 CRUD API：** 成功标准只要求"基础 CRUD 端点可调用"。只做 suppliers 一组完整 CRUD 作为样板 + health，其余实体在各自阶段随业务逻辑一起实现，避免无业务语义的空壳端点返工。
- **使用 PostgreSQL ENUM 存状态：** 见上文，用 String + CHECK。

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Schema 迁移 | 手写 `CREATE TABLE` SQL 脚本 + 版本号表 | Alembic autogenerate + 人工审阅 | 约束命名、类型 diff、downgrade 全部要手工对齐，必然漂移 |
| 配置解析 | `os.environ.get` 散落各处 | pydantic-settings `Settings` | 类型校验、必填检查、`.env` 加载一次到位 |
| 输入校验 | 手写 dict key 检查 | Pydantic models（FastAPI 内建） | 422 格式统一、OpenAPI 文档自动正确 |
| 队列锁/认领 | 应用层"先 SELECT 再 UPDATE"两阶段认领 | `FOR UPDATE SKIP LOCKED` 单语句 | 两阶段在并发下必重复认领或死锁 [CITED: postgresql.org/sql-select] |
| 分位数统计 | Python 端拉全量数据算 P95 | `percentile_cont() WITHIN GROUP` | 10M 行数据传输本身就超时；DB 内置函数 |
| UTC 时间 | Python `datetime.utcnow()` 赋值 | `timestamptz` + `server_default=func.now()` | 应用/DB 双时钟源漂移；naive datetime 丢时区 |
| UUID 生成 | Python `uuid4()` 客户端生成 | `gen_random_uuid()` server_default | 绕过 ORM 的写入也有主键；减少一次往返 |
| 日志格式 | 手写 f-string 拼 JSON | structlog（或 stdlib + python-json-logger） | 转义、上下文字段、request_id 绑定都是边角案例 |

**Key insight:** 本阶段所有"看起来简单"的基础设施（迁移、队列、时间戳、配置）都有已被官方工具完整覆盖的边角案例；本项目 12 个阶段的复杂度在业务侧（评测引擎集成、沙箱、报告），基础设施每自造一分，后面每个阶段都要为它还债。

## Common Pitfalls

### Pitfall 1: Alembic autogenerate 产生无名约束 diff
**What goes wrong:** 首次迁移后任何模型改动都会生成一批"重建约束"的噪音迁移，甚至 downgrade 失败。
**Why it happens:** 没配 `MetaData.naming_convention`，PostgreSQL 自动起名与 ORM 端不一致。
**How to avoid:** Pattern 1 的命名约定必须在**第一个模型落笔前**建好；中途再加会导致已有约束全量重命名迁移。
**Warning signs:** autogenerate 输出里出现 `op.drop_constraint(None, ...)` 或 `NoneType` 约束名。

### Pitfall 2: naive datetime 混入 timestamptz 列
**What goes wrong:** `psycopg` 对 naive datetime 写 `timestamptz` 时按 session 时区解释，测试机/容器时区不同导致数据漂移。
**How to avoid:** 模型层一律 `DateTime(timezone=True)`；应用代码禁止 `datetime.now()`，用 `datetime.now(timezone.utc)`；DB 容器设 `TZ=UTC`。
**Warning signs:** 单元测试在 CI 通过、本地差 8 小时。

### Pitfall 3: JSONB 列的原地修改不被 ORM 追踪
**What goes wrong:** `task.config_snapshot["x"] = y` 后 commit，变更没落库。
**Why it happens:** SQLAlchemy 默认按引用比较 JSON 列，原地 mutation 检测不到。
**How to avoid:** 赋值整个新 dict（不可变风格）；或对确需原地改的列用 `MutableDict.as_mutable(JSONB)`。快照字段语义上不可变，**应禁止原地改**——不挂 MutableDict 反而是特性。
**Warning signs:** 测试里"改了但没生效"。

### Pitfall 4: 健康检查把 DB 检查塞进 /healthz
**What goes wrong:** DB 短暂抖动 → `/healthz` 挂 → Compose/K8s 重启应用 → 雪崩循环。
**How to avoid:** 分层：`/healthz`（liveness，进程内直接 200）与 `/readyz`（readiness，执行 `SELECT 1` 并报 DB 延迟）。PRD §8.1.2 的组件健康面板（PostgreSQL、Worker、目录磁盘）是 Phase 11 的只读聚合端点，本阶段只做这两个。
**Warning signs:** 重启风暴与 DB 连接数尖峰同时出现。

### Pitfall 5: 连接池默认参数在长事务+SKIP LOCKED 下耗尽
**What goes wrong:** Phase 4 Worker 认领 Run 后长事务持锁，池连接被占满，API 无连接可用。
**How to avoid:** 本阶段把 `pool_size`/`max_overflow`/`pool_pre_ping` 显式写进 engine 配置（如 5+5）；Phase 4 的认领事务必须短（认领即提交，执行过程只用心跳 UPDATE 续 lease，不持有行锁跑评测）。
**Warning signs:** `QueuePool limit overflow` 日志。

### Pitfall 6: raw_response 用 JSONB 存导致"原始响应"不再原始
**What goes wrong:** JSONB 会重排键序、去重键、规范化数字，与 PRD"保留完整原始响应 JSON"（[VERIFIED: prd/prd_v1.0_final.md:488]）产生口径争议。
**How to avoid:** `raw_response` 用 `Text` 存字节级原文；结构化提取结果另存 `extracted_fields JSONB`（content、reasoning_content、reasoning_tokens、缓存 Token、service tier，[VERIFIED: prd/prd_v1.0_final.md:488-490]）。两列各司其职。
**Warning signs:** 排障时无法回放供应商确切响应字节。

### Pitfall 7: 把 Phase 2–11 的业务逻辑提前塞进模型层
**What goes wrong:** 模型类里写满加密、白名单、预检的半成品方法，后续阶段返工。
**How to avoid:** Phase 1 模型只承载结构（字段、关系、约束）+ 状态枚举常量；业务规则一律在后续阶段的 service 层实现。`api_key_ciphertext` 列本阶段建成 nullable，加密逻辑属 Phase 2/10。

## Code Examples

Verified patterns from official sources:

### docker-compose.yml（开发环境）

```yaml
# Source: 标准 Compose 模式；postgres 官方镜像文档 hub.docker.com/_/postgres
services:
  db:
    image: postgres:16          # PRD 要求 14+；16 为当前稳定主版本 [ASSUMED: 版本选择]
    environment:
      POSTGRES_USER: evalplat
      POSTGRES_PASSWORD: evalplat
      POSTGRES_DB: evalplat
      TZ: UTC
    ports: ["5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U evalplat"]
      interval: 5s
      timeout: 3s
      retries: 10
  migrate:
    build: .
    command: alembic upgrade head
    env_file: .env
    depends_on:
      db: { condition: service_healthy }
  app:
    build: .
    command: uvicorn evalplat.main:create_app --factory --host 0.0.0.0 --port 8000 --reload
    env_file: .env
    volumes: ["./src:/app/src"]
    ports: ["8000:8000"]
    depends_on:
      migrate: { condition: service_completed_successfully }
volumes:
  pgdata:
```

### 健康检查端点

```python
# Source: fastapi.tiangolo.com + Pitfall 4 分层设计
from fastapi import APIRouter
from sqlalchemy import text

router = APIRouter(tags=["health"])

@router.get("/healthz")
def healthz():
    return {"status": "ok"}

@router.get("/readyz")
def readyz(db: Session = Depends(get_db)):
    start = time.monotonic()
    db.execute(text("SELECT 1"))
    return {"status": "ready", "db_latency_ms": round((time.monotonic() - start) * 1000, 1)}
```

### 迁移一致性验证（对应成功标准 3）

```bash
# Source: alembic.sqlalchemy.org (alembic check 命令)
alembic upgrade head
alembic check        # ORM metadata 与 DB schema 有 diff 时非零退出
alembic downgrade base && alembic upgrade head   # 往返可执行
```

### 供应商 CRUD 样板（路由 → schema → ORM 三层分离）

```python
# Source: fastapi.tiangolo.com/tutorial/sql-databases 官方结构
@router.post("/suppliers", response_model=SupplierRead, status_code=201)
def create_supplier(payload: SupplierCreate, db: Session = Depends(get_db)):
    supplier = Supplier(**payload.model_dump())
    db.add(supplier)
    db.commit()
    db.refresh(supplier)
    return supplier
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `on_event("startup")` | `lifespan` async context manager | FastAPI 0.93+ 引入，现已标记废弃 | 启动/清理逻辑配对，测试可直接驱动 |
| SQLAlchemy 1.4 `Column()` 声明 | 2.0 `Mapped[]` + `mapped_column()` | SQLAlchemy 2.0 (2023) | mypy 全量类型检查；IDE 补全；本阶段必须全用 2.0 风格 |
| psycopg2 | psycopg 3 (`postgresql+psycopg://`) | SQLAlchemy 2.0 官方方言 | 更好的类型与 pipeline 支持；新项目无理由用 psycopg2 |
| Pydantic v1 `.dict()`/`Config` | v2 `model_dump()`/`model_config` | Pydantic v2 (2023) | FastAPI 当前版本已完全 v2 化；示例代码注意不要抄 v1 博客 |
| Redis/Celery 任务队列 | PostgreSQL `SKIP LOCKED` + lease + fencing token | 官方文档长期支持，社区模式成熟 | EXEC-01 明确排除 Redis；单库简化运维 [CITED: research/official_source_research_v0.1.md §6.1] |

**Deprecated/outdated:**
- `datetime.utcnow()`：Python 3.12 标记弃用，用 `datetime.now(timezone.utc)`；本项目全部时间走 DB `server_default` 更佳。
- `psycopg2-binary` 用于生产：官方长期警告；新项目直接 psycopg 3。
- Alembic 1.13 之前的 `op.execute(f"...")` 字符串拼接风格迁移：用参数化或完整 DDL 字符串常量。

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | 使用同步 SQLAlchemy engine 而非 async | Standard Stack 架构决策 | 中：后续改 async 需动 session 层与所有 route 签名，但不影响 schema |
| A2 | 业务实体主键用 UUID、requests/attempts 用 bigint identity | 数据模型设计（隐含） | 低：初始迁移前可改；上线后改主键成本高 |
| A3 | 状态枚举用 String+CHECK 而非 PostgreSQL ENUM | 数据模型设计 | 低：纯实现偏好，语义等价 |
| A4 | 英文表名/字段名命名（suppliers/connections/...） | 数据模型设计 | 低：PRD 只有中文实体名，命名需团队确认一次 |
| A5 | 状态枚举英文取值映射（draft/queued/running/...） | 数据模型设计 | 低：中文语义已由 PRD 锁定，英文取值仅为代码标识 |
| A6 | docker-compose 使用 postgres:16 而非 14 | Code Examples | 低：PRD 要求 14+，选当前稳定版；如生产外部 PG 为 14 应改为对齐版本 |
| A7 | structlog 而非 stdlib logging | Standard Stack | 低：可无痛替换 |
| A8 | `runs` 表直接兼作队列表（不建独立 queue 表） | Pattern 4 / Anti-Patterns | 低-中：符合 EXEC-01 与 PRD §8.8 语义；若 Phase 4 发现需要独立队列表再迁移 |
| A9 | Phase 1 只实现 suppliers 一组完整 CRUD | Anti-Patterns / 成功标准 4 | 低：成功标准只说"基础 CRUD API 端点"，范围可讨论 |

**A4/A5/A6 建议在 plan 阶段或 discuss 中一次性向用户确认。**

## Open Questions (RESOLVED)

> 以下 4 条已全部在 plan 阶段实质解决：方案假设固化于 01-01-PLAN「计划假设」节，并经 01-01 Task 1（checkpoint:decision，one-way 门控）统一确认。executor 不得推翻。

1. **生产/外部 PostgreSQL 的实际版本是多少？** — RESOLVED
   - What we know: PRD 要求 14+，本机 psql client 为 18.6，compose 建议 postgres:16
   - What's unclear: 生产外部 PostgreSQL 的版本（影响 `gen_random_uuid()`（13+ 内置）、`percentile_cont` 并行行为等假设）
   - Recommendation: 以 14 为最低兼容线写迁移（本方案所有特性 14 均支持），compose 用 16；部署文档注明对齐
   - **Resolution:** 采纳 Recommendation，固化为 01-01-PLAN 计划假设「A-PG 版本」（迁移按 PG 14 最低兼容线编写，docker-compose 用 postgres:16），由 01-01 Task 1 checkpoint 决策项 (4) 门控确认。

2. **requests/attempts 的保留粒度与 ITL 序列存储** — RESOLVED
   - What we know: PRD 要求请求级保存 ITL 序列（§9.1），10M 请求容量基线
   - What's unclear: `itl_series` 放 `attempts` 行内 JSONB 还是独立子表；长输出（8K token）ITL 序列约数千浮点，行内 JSONB 会让行变宽
   - Recommendation: Phase 1 按行内 JSONB 建列（查询模式永远是按 attempt 取整列）；Phase 5 实测体积后如需拆分再迁移
   - **Resolution:** 采纳 Recommendation，固化为 01-01-PLAN 计划假设「A-ITL」（attempts.itl_series 行内 JSONB，01-02 Task 1 第 12 步建列），Phase 5 实测后再评估。

3. **Phase 1 CRUD 范围确认** — RESOLVED
   - What we know: 成功标准 4 要求"基础 CRUD API 端点可通过 Postman/curl 调用"
   - What's unclear: 一组样板（suppliers）是否足够，还是要覆盖 connections/model_deployments
   - Recommendation: 默认 suppliers 一组；若计划阶段认为标准 4 隐含多组，connections 可作为第二组（含 base_url 规范化，但不含加密——加密属 Phase 2）
   - **Resolution:** 采纳默认方案（suppliers 一组），固化为 01-01-PLAN 计划假设「A-CRUD 范围」，由 01-03-PLAN Task 1 实现；connections 等其余实体 CRUD 随各自阶段实现。

4. **审计/系统设置等"后续阶段表"是否进初始迁移** — RESOLVED
   - What we know: 一次性建全（本研究推荐）vs 每阶段增量迁移
   - What's unclear: 团队对初始迁移体积的接受度
   - Recommendation: 建全。15 张表的 DDL 在一个 revision 中可审阅；避免 Phase 10 才发现 audit_events 表结构与早期写入路径冲突
   - **Resolution:** 采纳 Recommendation，固化为 01-02-PLAN 计划假设（0001 初始迁移一次建全 15 类实体共 17 张表，01-02 Task 2 定稿）。

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | 后端运行时 | ✓ | 3.12.4 (≥3.11 ✓) | — |
| Docker | Compose 开发环境、Phase 6 沙箱 | ✓ | 29.4.0 | — |
| Docker Compose | 开发环境编排 | ✓ | v5.1.2 | — |
| PostgreSQL server | 唯一数据库 | 经 Docker 提供（postgres:16 镜像） | — | 本机 Homebrew PG 18.6 client 可直连验证 |
| psql client | 人工排障/验证 | ✓ | 18.6 | — |
| pip | 依赖安装 | ✓ | 24.0 | — |
| git | 版本控制 | ✓ | — | — |

**Missing dependencies with no fallback:** 无
**Missing dependencies with fallback:** 无

注：本机 Python 在 anaconda 环境，建议在 Wave 0 用 `python3 -m venv .venv` 建独立虚拟环境，避免污染 conda base。

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.1.1 + httpx 0.28.1（`httpx.ASGITransport` 直接驱动 app） |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` — Wave 0 创建 |
| Quick run command | `pytest tests/test_health.py tests/test_suppliers_api.py -x -q` |
| Full suite command | `pytest -q`（含迁移往返测试，需要 Docker db 服务运行） |

测试数据库策略：`docker compose --profile test up -d db-test`（独立 postgres 容器、独立库名 `evalplat_test`，或复用 db 服务上的第二个 database）；`conftest.py` 每个测试会话执行 `alembic upgrade head`，测试间用 TRUNCATE/事务回滚隔离。[ASSUMED：常规 FastAPI 测试实践]

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| 成功标准1 | 迁移可执行：空库 upgrade head 建全部表；downgrade base 干净回滚；再 upgrade 幂等 | integration | `pytest tests/test_migrations.py -x` | ❌ Wave 0 |
| 成功标准2 | 应用启动且 `/healthz` 返回 200；`/readyz` 在 DB 可达时 200 | unit/integration | `pytest tests/test_health.py -x` | ❌ Wave 0 |
| 成功标准3 | ORM metadata 与 DB schema 一致 | integration | `alembic check`（封装为 `pytest tests/test_migrations.py::test_alembic_check`） | ❌ Wave 0 |
| 成功标准4 | suppliers CRUD：POST 201、GET 列表/详情 200、PATCH 200、DELETE 405（无物理删除） | integration | `pytest tests/test_suppliers_api.py -x` | ❌ Wave 0 |
| EXEC-01 | 队列表结构就绪：`runs` 表存在 lease/fencing 列与部分索引；SKIP LOCKED 认领 SQL 冒烟（两个并发事务各认到不同行） | integration | `pytest tests/test_queue_schema.py -x` | ❌ Wave 0 |
| SCOPE-1 | 15 张核心表全部存在且约束正确（命名约定、FK、CHECK、UNIQUE） | integration | `pytest tests/test_schema.py -x`（用 SQLAlchemy inspect 断言） | ❌ Wave 0 |
| SCOPE-6 | 统一错误结构：404 业务异常与 422 校验错误均返回 `{error:{code,message,details}}` | unit | `pytest tests/test_errors.py -x` | ❌ Wave 0 |

**边界用例（必须覆盖）：**
- 空数据库：`/api/v1/suppliers` GET 返回 `[]` + 200（空状态不报错）
- 迁移往返：`upgrade head → downgrade base → upgrade head` 三次连续执行无错误（验证 downgrade 不丢约束）
- 并发健康检查：50 个并发请求打 `/healthz`，全部 200 且无连接池泄漏（`/healthz` 不触 DB）
- DB 不可达时 `/readyz` 返回 503 而 `/healthz` 仍 200（停掉 db 容器验证，可用 monkeypatch engine 模拟）
- 幂等键冲突：同 `idempotency_key` 重复 POST tasks（若 Phase 1 建 tasks 端点）或直接在 DB 层验证 UNIQUE 约束生效
- UTC 校验：插入记录后 `created_at` 带 `+00:00` 时区

### Sampling Rate
- **Per task commit:** `pytest tests/test_health.py tests/test_suppliers_api.py -x -q`（< 10 秒，需 db 容器运行）
- **Per wave merge:** `pytest -q`（含迁移往返，< 60 秒）
- **Phase gate:** 全套 green + `alembic check` 零 diff + `docker compose up` 从冷启动到 `/readyz` 200 全流程人工验证一次

### Wave 0 Gaps
- [ ] `pyproject.toml` + 依赖锁定 + venv 创建
- [ ] `docker-compose.yml`（db / migrate / app 服务）
- [ ] `migrations/env.py` + `0001_initial_schema.py`
- [ ] `tests/conftest.py` — 测试 DB fixture（alembic upgrade + 会话隔离）
- [ ] `tests/test_health.py` `test_migrations.py` `test_suppliers_api.py` `test_schema.py` `test_queue_schema.py` `test_errors.py`
- [ ] 框架安装：`pip install fastapi sqlalchemy alembic pydantic pydantic-settings "psycopg[binary]" uvicorn structlog pytest httpx`

## Security Domain

> `security_enforcement` 未在 config 中显式关闭 → 启用。本阶段是基础设施期，安全控制以"立规矩、留位置"为主，实际加密与白名单逻辑在 Phase 2/10。

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no（首版无登录，PRD §11.1 可信网络边界） | — |
| V3 Session Management | no（无会话） | — |
| V4 Access Control | no（首版无权限体系） | — |
| V5 Input Validation | yes | Pydantic models 全量校验请求体；`base_url` 字段本阶段即加格式校验（http/https、无 userinfo），完整 SSRF 校验在 Phase 2/10 |
| V6 Cryptography | 部分（结构预留） | `connections.api_key_ciphertext` 列本阶段建好（nullable）；加密算法选型（建议 `cryptography` Fernet/AES-GCM，主密钥 `.env` 注入，PRD §11.2）属 Phase 2/10，**禁止自造加密** |
| V7 Error Handling & Logging | yes | 统一异常 handler 不回显内部细节；日志不含 Authorization header；错误结构无堆栈 |
| V8 Data Protection | 部分 | 审计表结构不含密钥/业务正文字段（PRD §8.16.2 已在 schema 层落实）；`.env` 不入库（`.gitignore` 确认） |

### Known Threat Patterns for FastAPI + PostgreSQL + SQLAlchemy

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| SQL 注入 | Tampering | SQLAlchemy ORM/参数化；`text()` 仅用于 `SELECT 1` 等无输入常量 |
| 错误信息泄露（连接串/堆栈进响应） | Information Disclosure | 兜底 exception handler 返回固定 500 结构，细节只进日志 |
| 密钥入库明文 | Information Disclosure | 本阶段不实现写入路径；列定义为 ciphertext，Phase 2 加密写入 |
| 迁移脚本硬编码 DSN | Information Disclosure | env.py 从 `Settings` 读 `DATABASE_URL`，`alembic.ini` 不含密码 |
| `.env` 提交进仓库 | Information Disclosure | `.gitignore` 含 `.env`；仓库只提供 `.env.example` |
| 容器以 root 运行应用 | Elevation of Privilege | Dockerfile 用非 root USER（Wave 0 Dockerfile 中落实） |

## Sources

### Primary (HIGH confidence)
- `prd/prd_v1.0_final.md` — 数据模型、状态机、快照清单、容量基线、安全约束（逐节行号已内联标注 [VERIFIED]）
- `AGENTS.md` — 锁定技术栈与 7 条核心设计原则
- `research/official_source_research_v0.1.md` §6.1 — PostgreSQL SKIP LOCKED 与 percentile_cont 官方文档结论（复用，不重复检索）
- docs.sqlalchemy.org/en/latest/core/constraints.html — MetaData naming_convention 官方写法
- docs.sqlalchemy.org/en/latest/orm/declarative_tables.html — Mapped/mapped_column 官方写法
- PyPI registry JSON API（本 session 实时查询）— 全部 10 个包的当前版本号

### Secondary (MEDIUM confidence)
- fastapi.tiangolo.com（lifespan、settings、sql-databases 结构）— 官方文档，经 WebSearch 结果确认页面存在
- alembic.sqlalchemy.org — autogenerate 检测范围、`alembic check`
- Prisma/Netdata 工程博客（SKIP LOCKED 队列模式交叉验证）— 与官方文档语义一致的二手资料

### Tertiary (LOW confidence)
- 同步 vs async engine 的取舍论证 — 基于训练知识与本项目容量特征推断（A1）
- 状态枚举 String+CHECK vs ENUM 的取舍 — 团队惯用实践，非官方强制（A3）

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — 版本号全部经 PyPI 实时验证；栈本身已锁定
- Architecture: HIGH（模式层）/ MEDIUM（具体表结构设计 — 语义均逐字来自 PRD，但英文命名与若干取舍为推荐方案，见 Assumptions Log）
- Pitfalls: HIGH — 均为 SQLAlchemy/Alembic/FastAPI 官方文档明确记载的行为

**Research date:** 2026-08-18
**Valid until:** 2026-09-17（稳定基础设施领域，30 天有效期）
