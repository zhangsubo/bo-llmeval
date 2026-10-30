---
phase: 1
slug: infrastructure-data-model
# status lifecycle: draft (seeded by plan-phase) → validated (set by validate-phase §6)
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-08-18
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> 来源：`01-RESEARCH.md` 的 "Validation Architecture" 一节。

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.1.1 + httpx 0.28.1（`httpx.ASGITransport` 直接驱动 app） |
| **Config file** | `pyproject.toml` `[tool.pytest.ini_options]` — Wave 0 创建 |
| **Quick run command** | `pytest tests/test_health.py tests/test_suppliers_api.py -x -q` |
| **Full suite command** | `pytest -q`（含迁移往返测试，需要 Docker db 服务运行） |
| **Estimated runtime** | quick < 10s / full < 60s |

测试数据库策略：`docker compose --profile test up -d db-test`（独立 postgres 容器、独立库名 `evalplat_test`）；`conftest.py` 每个测试会话执行 `alembic upgrade head`，测试间用 TRUNCATE/事务回滚隔离。

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_health.py tests/test_suppliers_api.py -x -q`（< 10 秒，需 db 容器运行）
- **After every plan wave:** Run `pytest -q`（含迁移往返，< 60 秒）
- **Before `/gsd-verify-work`:** Full suite must be green + `alembic check` 零 diff + `docker compose up` 冷启动到 `/readyz` 200 人工验证一次
- **Max feedback latency:** 60 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 01-02-T2-migration | 01-02 | 2 | 成功标准1（迁移可执行，空库 upgrade/downgrade/再 upgrade 幂等） | — | N/A | integration | `pytest tests/test_migrations.py -x` | ❌ W0 | ⬜ pending |
| 01-01-T3-health | 01-01 | 1 | 成功标准2（`/healthz` 200；`/readyz` DB 可达时 200、不可达时 503） | T-1-log | 健康检查不触 DB、不泄漏内部信息 | unit/integration | `pytest tests/test_health.py -x` | ❌ W0 | ⬜ pending |
| 01-02-T1-schema | 01-02 | 2 | 成功标准3 + SCOPE-1（ORM↔schema 一致；15 张核心表约束正确） | — | N/A | integration | `pytest tests/test_migrations.py::test_alembic_check tests/test_schema.py -x` | ❌ W0 | ⬜ pending |
| 01-03-T1-crud | 01-03 | 2 | 成功标准4（suppliers CRUD：POST 201 / GET 200 / PATCH 200 / DELETE 405） | T-1-input | Pydantic 全量校验；统一错误结构 | integration | `pytest tests/test_suppliers_api.py -x` | ❌ W0 | ⬜ pending |
| 01-01-T3-queue | 01-01 | 1 | EXEC-01（`runs` 表 lease/fencing 列与部分索引；SKIP LOCKED 认领冒烟） | — | N/A | integration | `pytest tests/test_queue_schema.py -x` | ❌ W0 | ⬜ pending |
| 01-03-T2-errors | 01-03 | 2 | SCOPE-6（统一错误结构 `{error:{code,message,details}}`） | T-1-err | 错误响应无堆栈、不回显内部细节 | unit | `pytest tests/test_errors.py -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

**边界用例（必须覆盖）：**
- 空数据库：`GET /api/v1/suppliers` 返回 `[]` + 200
- 迁移往返：`upgrade head → downgrade base → upgrade head` 连续执行无错误
- 并发健康检查：50 并发请求 `/healthz` 全部 200 且无连接池泄漏
- DB 不可达时 `/readyz` 503 而 `/healthz` 仍 200
- 幂等键冲突：同键重复写入触发 UNIQUE 约束
- UTC 校验：`created_at` 带 `+00:00` 时区

---

## Wave 0 Requirements

- [ ] `pyproject.toml` + 依赖锁定 + venv 创建
- [ ] `docker-compose.yml`（db / migrate / app 服务）
- [ ] `migrations/env.py` + `0001_initial_schema.py`
- [ ] `tests/conftest.py` — 测试 DB fixture（alembic upgrade + 会话隔离）
- [ ] `tests/test_health.py` `test_migrations.py` `test_suppliers_api.py` `test_schema.py` `test_queue_schema.py` `test_errors.py` — stubs
- [ ] 框架安装：`pip install fastapi sqlalchemy alembic pydantic pydantic-settings "psycopg[binary]" uvicorn structlog pytest httpx`

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| `docker compose up` 冷启动全流程 | 成功标准2 | 需要真实容器编排与迁移-on-start 时序 | 删卷后 `docker compose up --build`，观察 migrate 完成、app 起来、`curl localhost:8000/readyz` 返回 200 |
| curl/Postman 调用 CRUD | 成功标准4 | 成功标准要求"可通过 Postman/curl 调用"的人工确认 | 对运行中的服务执行 POST/GET/PATCH 各一次，确认响应结构与状态码 |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 60s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
