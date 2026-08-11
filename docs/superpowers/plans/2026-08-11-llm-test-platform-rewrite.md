# EvalScope LLM Test Platform Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a public-facing, single-administrator LLM supplier test platform that reuses EvalScope's evaluation engines while replacing its Flask service, file-scanning dashboard, task lifecycle, persistence, authentication, and reporting layers.

**Architecture:** A FastAPI API and React application use MySQL as the only persistent source of truth. Trusted evaluation/performance workers and a separately deployed Agent worker claim immutable tasks from MySQL; EvalScope runs inside worker adapters with ephemeral scratch directories, and every durable configuration, log, sample, request, metric, report, and Agent trace is imported into MySQL. The public node never executes Agent containers and MySQL is reachable only on the private network.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLAlchemy 2, Alembic, MySQL 8.0, Argon2id, AES-256-GCM, React 19, TypeScript, Vite, Vitest, Docker/OCI, Caddy or Nginx.

## Global Constraints

- Preserve EvalScope's benchmark, model-call, performance-metric, and Agent bridge engines; do not preserve the current Flask API or `outputs/`-scanning contracts.
- Single administrator only; no registration, roles, tenants, SSO, email recovery, or TOTP.
- Bootstrap the administrator from `.env` only when the administrator table is empty; subsequent `.env` changes must not overwrite the database password.
- Public access requires HTTPS, secure cookies, CSRF protection, security headers, login throttling, lockout, and audit events.
- Use MySQL as the only durable store. Runtime scratch files are allowed only inside a worker temporary directory and must never be exposed as a report source.
- Persist all reports, per-request details, logs, Agent traces, task snapshots, custom dataset sources, and normalized dataset items in MySQL.
- Do not expose delete endpoints or automatic retention cleanup. All persisted records are append-only except explicitly editable configuration heads.
- Encrypt API keys with AES-256-GCM using `CREDENTIAL_ENCRYPTION_KEY`; hash administrator passwords with Argon2id. Prompt, response, log, and trace bodies remain plaintext by explicit product decision.
- Support text-only Chat Completions, Responses, Embeddings, Rerank, Anthropic Messages, and Agent bridges for Claude Code, Codex, OpenCode, and Gemini CLI.
- Exclude AIGC, multimodal input, local checkpoints, LiteLLM, scheduled tests, alerts, historical `outputs/` import, and data deletion.
- Support multiple suppliers, multiple connection profiles per supplier, multiple credential versions, endpoint overrides, model synchronization, and manual model entries.
- Evaluation uses the normalized API root. Performance endpoints are derived from the root: `chat/completions`, `responses`, `embeddings`, `reranks`, or `messages`; non-standard vendors use a capability-level override.
- Tasks are asynchronous, immutable after creation, and claimed through MySQL leases. Do not add Redis or Celery.
- Agent runs are mandatory disposable containers on a separate private worker node with CPU, memory, disk, timeout, and network controls.
- Performance tests must enforce configured RPM, TPM, concurrency, duration, request, and token ceilings before enqueueing.
- Automated database backup and restore testing is recommended but is not an initial launch gate.
- Follow the repository contract: Python 3.10-3.12 compatibility, 120-column width, typed signatures, English comments/docstrings, minimal changes to EvalScope core, and runnable tests.

---

## Delivery Milestones

1. **Foundation:** Tasks 1-3 produce a public-safe authenticated API backed by an empty MySQL schema.
2. **Configuration:** Tasks 4-7 produce suppliers, credentials, models, datasets, templates, and immutable snapshots.
3. **Execution:** Tasks 8-11 produce recoverable workers and durable evaluation, performance, and Agent results.
4. **Product UI:** Tasks 12-13 produce authenticated administration, task, report, and comparison workflows.
5. **Release:** Tasks 14-15 deploy separate public/worker nodes and remove the old service path.

Each milestone must pass its own tests before work starts on the next milestone.

## Target File Structure

```text
evalscope_platform/
  api/
    main.py                 FastAPI application factory and middleware
    dependencies.py         Auth, DB session, CSRF, and request-context dependencies
    routers/                auth, vendors, datasets, templates, tasks, reports, comparisons
  auth/
    passwords.py            Argon2id hashing and verification
    sessions.py             Opaque signed session cookies and DB session lifecycle
    throttle.py             MySQL-backed login attempts and lockout
  db/
    base.py                 SQLAlchemy declarative base and shared types
    session.py              Engine/session factories and transaction helper
    models/                 One focused model module per bounded context
  domain/
    enums.py                Protocol, task, run, and Agent framework enums
    endpoints.py            API-root normalization and endpoint derivation
  schemas/                  Pydantic request/response contracts matching routers
  services/
    credentials.py          AES-GCM credential version service
    model_sync.py           `/models` synchronization with manual fallback
    datasets.py             Built-in materialization and custom text ingestion
    templates.py            Versioned template and task-snapshot construction
    quota.py                Performance preflight and budget enforcement
    comparisons.py          Baseline and multi-target comparison projection
  workers/
    main.py                 Worker loop and graceful shutdown
    lease.py                MySQL claim, heartbeat, cancellation, and recovery
    executors/              Evaluation, performance, and Agent executors
    importers/              EvalScope output-to-MySQL importers
  cli.py                    Serve, worker, bootstrap, and password-reset commands
migrations/
  env.py
  versions/0001_initial_platform.py
tests/platform/             Unit, API, MySQL integration, worker, and import tests
deploy/
  app/                      Public proxy/API/MySQL deployment examples
  worker/                   Private worker systemd and rootless container runtime files
evalscope/web/src/
  auth/                     Auth context and protected route
  api/platform/             New typed API clients and schemas
  pages/platform/           Login, suppliers, datasets, templates, tasks, reports, compare
```

The existing `evalscope/web/src/components/ui`, chart components, metric formatting, locale infrastructure, and reusable report presentation components remain available. Existing file-path APIs and report scanners are not reused.

## MySQL Data Model

All timestamps are UTC `DATETIME(6)`, identifiers are UUIDv7 stored as `BINARY(16)`, text uses `utf8mb4`, and JSON payloads use MySQL `JSON`. Foreign keys use `RESTRICT`; there are no cascading deletes.

| Bounded context | Tables | Required durable fields |
|---|---|---|
| Authentication | `admin_users`, `auth_sessions`, `login_attempts`, `audit_events` | password hash, session hash/expiry, IP/user-agent, action, before/after JSON |
| Suppliers | `vendors`, `connections`, `credential_versions`, `protocol_capabilities`, `models` | environment, normalized root, encrypted key/nonce/version, protocol, endpoint override, model ID/status |
| Datasets | `datasets`, `dataset_versions`, `dataset_items` | kind, source revision, raw text blob, schema JSON, ordinal, normalized item JSON |
| Templates | `test_templates`, `template_versions` | task type, name, immutable configuration JSON, revision |
| Tasks | `tasks`, `task_targets`, `task_events`, `task_logs` | immutable snapshot JSON, status, lease owner/expiry, heartbeat, cancellation, ordered events/logs |
| Evaluation | `eval_runs`, `eval_samples`, `eval_metrics`, `report_snapshots` | prompt, response, extracted answer, score, latency, tokens, report JSON |
| Performance | `perf_runs`, `perf_requests`, `perf_percentiles` | HTTP status/error, timings, token counts, success, raw request/response, percentile metrics |
| Agent | `agent_runs`, `agent_trace_events` | framework, container limits, ordered trace event JSON, exit status |
| Comparison | `comparison_reports`, `comparison_items` | baseline target, metric key, value, delta, regression status |

Critical indexes:

```sql
CREATE INDEX ix_tasks_claim ON tasks (status, available_at, priority, created_at);
CREATE INDEX ix_tasks_lease ON tasks (status, lease_expires_at);
CREATE INDEX ix_perf_requests_run_time ON perf_requests (perf_run_id, completed_at);
CREATE INDEX ix_eval_samples_run_dataset ON eval_samples (eval_run_id, dataset_version_id, ordinal);
CREATE UNIQUE INDEX uq_connection_capability ON protocol_capabilities (connection_id, protocol);
CREATE UNIQUE INDEX uq_model_external_id ON models (capability_id, external_model_id);
```

## API Surface

```text
POST   /api/auth/login
POST   /api/auth/logout
GET    /api/auth/me
POST   /api/auth/change-password

GET    /api/vendors
POST   /api/vendors
PATCH  /api/vendors/{vendor_id}
POST   /api/vendors/{vendor_id}/connections
PATCH  /api/connections/{connection_id}
POST   /api/connections/{connection_id}/credentials
POST   /api/connections/{connection_id}/test
POST   /api/connections/{connection_id}/models/sync
POST   /api/connections/{connection_id}/models

GET    /api/datasets/benchmarks
POST   /api/datasets/custom
POST   /api/datasets/{dataset_id}/versions
GET    /api/datasets/{dataset_id}/versions/{version_id}

GET    /api/templates
POST   /api/templates
POST   /api/templates/{template_id}/versions

POST   /api/tasks
GET    /api/tasks
GET    /api/tasks/{task_id}
POST   /api/tasks/{task_id}/cancel
GET    /api/tasks/{task_id}/logs
GET    /api/tasks/{task_id}/report
GET    /api/tasks/{task_id}/requests

POST   /api/comparisons
GET    /api/comparisons/{comparison_id}
```

No router defines `DELETE`.

---

### Task 1: Create the platform package and typed settings

**Files:**
- Create: `evalscope_platform/__init__.py`
- Create: `evalscope_platform/config.py`
- Create: `evalscope_platform/api/main.py`
- Create: `tests/platform/test_config.py`
- Create: `tests/platform/test_health.py`
- Modify: `requirements/service.txt`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `PlatformSettings`, `get_settings() -> PlatformSettings`, `create_app(settings: PlatformSettings | None = None) -> FastAPI`.

- [ ] **Step 1: Write failing settings and health tests**

```python
def test_settings_reject_missing_database_url(monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    with pytest.raises(ValidationError):
        PlatformSettings(_env_file=None)


def test_health_is_public(client):
    response = client.get('/health')
    assert response.status_code == 200
    assert response.json() == {'status': 'ok', 'service': 'evalscope-platform'}
```

- [ ] **Step 2: Run the tests and verify that imports fail**

Run: `pytest tests/platform/test_config.py tests/platform/test_health.py -v`

Expected: FAIL because `evalscope_platform` does not exist.

- [ ] **Step 3: Add exact configuration fields and the app factory**

```python
class PlatformSettings(BaseSettings):
    database_url: MySQLDsn
    session_secret: SecretStr
    credential_encryption_key: SecretStr
    admin_username: str
    admin_password: SecretStr
    session_ttl_seconds: int = 43_200
    login_max_failures: int = 5
    login_lock_seconds: int = 900
    max_dataset_upload_mb: int = 50
    public_origin: AnyHttpUrl

    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
```

Add FastAPI, Uvicorn, SQLAlchemy, Alembic, MySQL driver, Pydantic Settings, Argon2, cryptography, and multipart dependencies with compatible minimum versions in `requirements/service.txt`.

- [ ] **Step 4: Run focused tests and repository style checks**

Run: `pytest tests/platform/test_config.py tests/platform/test_health.py -v`

Expected: PASS.

Run: `python -m compileall evalscope_platform`

Expected: exit 0.

- [ ] **Step 5: Commit**

```bash
git add evalscope_platform requirements/service.txt pyproject.toml tests/platform
git commit -m "feat(platform): add FastAPI application foundation"
```

### Task 2: Create the initial MySQL schema and migrations

**Files:**
- Create: `evalscope_platform/db/base.py`
- Create: `evalscope_platform/db/session.py`
- Create: `evalscope_platform/db/models/*.py`
- Create: `evalscope_platform/domain/enums.py`
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/versions/0001_initial_platform.py`
- Create: `tests/platform/db/test_schema.py`

**Interfaces:**
- Produces: `Base`, `create_engine_from_settings()`, `session_scope()`, and all tables listed in **MySQL Data Model**.

- [ ] **Step 1: Write a MySQL integration test for tables, constraints, and indexes**

```python
def test_initial_schema(mysql_engine):
    tables = set(inspect(mysql_engine).get_table_names())
    assert {'admin_users', 'connections', 'credential_versions', 'tasks', 'perf_requests'} <= tables
    with mysql_engine.connect() as connection:
        delete_rules = connection.execute(text("""
            SELECT DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS
            WHERE CONSTRAINT_SCHEMA = DATABASE()
        """)).scalars().all()
    assert set(delete_rules) <= {'RESTRICT', 'NO ACTION'}
```

- [ ] **Step 2: Run migration test against MySQL 8 and verify failure**

Run: `pytest tests/platform/db/test_schema.py -v -m mysql`

Expected: FAIL because the migration and models do not exist.

- [ ] **Step 3: Implement the models and one initial Alembic revision**

Use string-valued enums so database values remain readable:

```python
class Protocol(str, Enum):
    OPENAI_CHAT = 'openai_chat'
    OPENAI_RESPONSES = 'openai_responses'
    OPENAI_EMBEDDINGS = 'openai_embeddings'
    OPENAI_RERANK = 'openai_rerank'
    ANTHROPIC_MESSAGES = 'anthropic_messages'


class TaskStatus(str, Enum):
    QUEUED = 'queued'
    CLAIMED = 'claimed'
    RUNNING = 'running'
    CANCEL_REQUESTED = 'cancel_requested'
    CANCELLED = 'cancelled'
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'
```

Implement every table and index named above. Store raw custom dataset sources in `MEDIUMBLOB`, large text bodies in `LONGTEXT`, and report snapshots in `JSON`.

- [ ] **Step 4: Verify upgrade, downgrade, and clean re-upgrade**

Run: `alembic upgrade head && alembic downgrade base && alembic upgrade head`

Expected: all commands exit 0 on an empty MySQL 8 database.

Run: `pytest tests/platform/db/test_schema.py -v -m mysql`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add evalscope_platform/db evalscope_platform/domain migrations alembic.ini tests/platform/db
git commit -m "feat(platform): add MySQL platform schema"
```

### Task 3: Implement single-administrator authentication and public-edge security

**Files:**
- Create: `evalscope_platform/auth/passwords.py`
- Create: `evalscope_platform/auth/sessions.py`
- Create: `evalscope_platform/auth/throttle.py`
- Create: `evalscope_platform/api/dependencies.py`
- Create: `evalscope_platform/api/routers/auth.py`
- Create: `evalscope_platform/cli.py`
- Test: `tests/platform/auth/test_auth_api.py`
- Test: `tests/platform/auth/test_password_reset.py`

**Interfaces:**
- Produces: `bootstrap_admin(session, settings)`, `require_admin()`, `require_csrf()`, `create_session()`, `revoke_all_sessions()`, and CLI command `evalscope-platform admin reset-password`.

- [ ] **Step 1: Write failing tests for bootstrap, login, lockout, CSRF, cookie flags, logout, and reset**

```python
def test_login_sets_secure_server_session(client, admin):
    response = client.post('/api/auth/login', json={'username': 'admin', 'password': 'correct-password'})
    assert response.status_code == 200
    cookie = response.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=lax' in cookie


def test_mutation_requires_csrf(authenticated_client):
    response = authenticated_client.post('/api/vendors', json={'name': 'Vendor'})
    assert response.status_code == 403
```

- [ ] **Step 2: Run tests and verify failure**

Run: `pytest tests/platform/auth -v`

Expected: FAIL because auth services and routes do not exist.

- [ ] **Step 3: Implement password, session, throttling, and CSRF rules**

Use Argon2id for passwords. Generate 32 random session bytes, store only `SHA-256(token)` in MySQL, sign the cookie value with `SESSION_SECRET`, and return the CSRF token from `/api/auth/me`. Exempt only `/health`, static assets, and `/api/auth/login` from authentication. Record bootstrap, login success/failure, lockout, logout, password change, and password reset in `audit_events`.

- [ ] **Step 4: Add security middleware**

Set CSP, HSTS, `X-Content-Type-Options`, `Referrer-Policy`, and `Permissions-Policy`. Do not install permissive CORS middleware. Trust proxy headers only from configured proxy networks.

- [ ] **Step 5: Run auth tests**

Run: `pytest tests/platform/auth -v`

Expected: PASS with lockout after five failures and all sessions invalidated after password reset.

- [ ] **Step 6: Commit**

```bash
git add evalscope_platform/auth evalscope_platform/api evalscope_platform/cli.py tests/platform/auth
git commit -m "feat(platform): add single-admin authentication"
```

### Task 4: Implement supplier connections, credential versions, and endpoint derivation

**Files:**
- Create: `evalscope_platform/domain/endpoints.py`
- Create: `evalscope_platform/services/credentials.py`
- Create: `evalscope_platform/schemas/vendors.py`
- Create: `evalscope_platform/api/routers/vendors.py`
- Test: `tests/platform/vendors/test_endpoints.py`
- Test: `tests/platform/vendors/test_credentials.py`
- Test: `tests/platform/vendors/test_vendor_api.py`

**Interfaces:**
- Produces: `normalize_api_root(value: str) -> str`, `resolve_endpoint(root: str, protocol: Protocol, override: str | None) -> str`, and `CredentialService.encrypt/decrypt`.

- [ ] **Step 1: Write the endpoint contract tests**

```python
@pytest.mark.parametrize(('protocol', 'expected'), [
    (Protocol.OPENAI_CHAT, 'https://vendor.example/v1/chat/completions'),
    (Protocol.OPENAI_RESPONSES, 'https://vendor.example/v1/responses'),
    (Protocol.OPENAI_EMBEDDINGS, 'https://vendor.example/v1/embeddings'),
    (Protocol.OPENAI_RERANK, 'https://vendor.example/v1/reranks'),
])
def test_endpoint_derivation(protocol, expected):
    assert resolve_endpoint('https://vendor.example/v1/', protocol, None) == expected


def test_anthropic_root_derives_messages():
    assert resolve_endpoint('https://api.anthropic.com', Protocol.ANTHROPIC_MESSAGES, None) == \
        'https://api.anthropic.com/v1/messages'
```

- [ ] **Step 2: Run endpoint and credential tests and verify failure**

Run: `pytest tests/platform/vendors/test_endpoints.py tests/platform/vendors/test_credentials.py -v`

Expected: FAIL because the services do not exist.

- [ ] **Step 3: Implement strict roots and AES-256-GCM credential versions**

Reject roots containing query strings or fragments. Allow only HTTPS except `http://127.0.0.1` and `http://localhost` in development. Decode `CREDENTIAL_ENCRYPTION_KEY` as exactly 32 bytes. Bind ciphertext additional authenticated data to `connection_id:credential_version`.

- [ ] **Step 4: Implement create/update/test APIs without secret disclosure**

Responses expose `key_mask`, `credential_version`, and `last_test_status`; they never expose ciphertext, nonce, or plaintext. Creating a new key appends `credential_versions` and moves `connections.current_credential_version_id` in one transaction.

- [ ] **Step 5: Run vendor tests**

Run: `pytest tests/platform/vendors -v`

Expected: PASS, including an assertion that serialized responses contain no submitted API key substring.

- [ ] **Step 6: Commit**

```bash
git add evalscope_platform/domain evalscope_platform/services/credentials.py evalscope_platform/schemas/vendors.py evalscope_platform/api/routers/vendors.py tests/platform/vendors
git commit -m "feat(platform): add versioned supplier connections"
```

### Task 5: Add model synchronization and manual model management

**Files:**
- Create: `evalscope_platform/services/model_sync.py`
- Modify: `evalscope_platform/api/routers/vendors.py`
- Test: `tests/platform/vendors/test_model_sync.py`

**Interfaces:**
- Produces: `sync_models(session, connection_id, http_client) -> ModelSyncResult` and `add_manual_model(...) -> Model`.

- [ ] **Step 1: Write tests for successful sync, missing `/models`, stale marking, and manual models**

```python
def test_sync_marks_missing_model_inactive_without_deleting(session, connection, respx_mock):
    existing = add_model(session, connection, external_model_id='old-model')
    respx_mock.get('https://vendor.example/v1/models').respond(200, json={'data': [{'id': 'new-model'}]})
    result = sync_models(session, connection.id, httpx.Client())
    assert result.added == ['new-model']
    assert session.get(Model, existing.id).status == 'inactive'
```

- [ ] **Step 2: Run the test and verify failure**

Run: `pytest tests/platform/vendors/test_model_sync.py -v`

Expected: FAIL because model synchronization does not exist.

- [ ] **Step 3: Implement OpenAI `/models` sync with manual fallback**

Use the connection root plus `/models`; never append a chat endpoint. A failed or unsupported sync updates `last_sync_error` but does not alter current model status. Anthropic and non-standard suppliers use manual models.

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/platform/vendors/test_model_sync.py -v`

Expected: PASS.

```bash
git add evalscope_platform/services/model_sync.py evalscope_platform/api/routers/vendors.py tests/platform/vendors/test_model_sync.py
git commit -m "feat(platform): add supplier model catalog sync"
```

### Task 6: Materialize built-in and custom text datasets in MySQL

**Files:**
- Create: `evalscope_platform/services/datasets.py`
- Create: `evalscope_platform/schemas/datasets.py`
- Create: `evalscope_platform/api/routers/datasets.py`
- Test: `tests/platform/datasets/test_custom_ingest.py`
- Test: `tests/platform/datasets/test_builtin_materialize.py`

**Interfaces:**
- Produces: `ingest_custom_dataset(...) -> DatasetVersion`, `materialize_benchmark(...) -> DatasetVersion`, and `export_version_to_jsonl(version_id, path) -> int` for worker scratch use.

- [ ] **Step 1: Write tests for JSONL, CSV, malformed rows, size limits, and immutable versions**

```python
def test_custom_jsonl_creates_raw_and_normalized_version(client, csrf_headers):
    payload = b'{"question":"2+2?","answer":"4"}\n'
    response = client.post(
        '/api/datasets/custom',
        files={'file': ('math.jsonl', payload, 'application/jsonl')},
        data={'name': 'math'},
        headers=csrf_headers,
    )
    assert response.status_code == 201
    assert response.json()['item_count'] == 1
```

- [ ] **Step 2: Run tests and verify failure**

Run: `pytest tests/platform/datasets -v`

Expected: FAIL because dataset services do not exist.

- [ ] **Step 3: Implement validation and materialization**

Accept UTF-8 JSONL and CSV up to `MAX_DATASET_UPLOAD_MB`. Store the exact source bytes and SHA-256, infer a stable field schema, store each normalized row with its ordinal, and reject an entire version transaction when any row is invalid. Built-in benchmarks record benchmark name, subset, source hub, source revision, and normalized items.

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/platform/datasets -v`

Expected: PASS.

```bash
git add evalscope_platform/services/datasets.py evalscope_platform/schemas/datasets.py evalscope_platform/api/routers/datasets.py tests/platform/datasets
git commit -m "feat(platform): add versioned text datasets"
```

### Task 7: Add versioned test templates and immutable task snapshots

**Files:**
- Create: `evalscope_platform/services/templates.py`
- Create: `evalscope_platform/schemas/templates.py`
- Create: `evalscope_platform/api/routers/templates.py`
- Test: `tests/platform/templates/test_versions.py`
- Test: `tests/platform/templates/test_snapshot.py`

**Interfaces:**
- Produces: `create_template_version(...)`, `build_task_snapshot(template_version_id, target_ids) -> TaskSnapshot`, and versioned references to credentials, models, and datasets.

- [ ] **Step 1: Write immutability tests**

```python
def test_task_snapshot_does_not_change_after_connection_edit(session, template, connection):
    snapshot = build_task_snapshot(session, template.current_version_id, [connection.id])
    connection.api_root = 'https://changed.example/v1'
    session.commit()
    assert snapshot.targets[0].api_root == 'https://vendor.example/v1'
```

- [ ] **Step 2: Run tests and verify failure**

Run: `pytest tests/platform/templates -v`

Expected: FAIL because template services do not exist.

- [ ] **Step 3: Implement template schemas per task type**

Define Pydantic discriminated unions for `evaluation`, `performance`, and `agent`. Store canonical JSON in `template_versions`; when creating a task, resolve and copy the exact credential version, endpoint, model ID, dataset version, generation parameters, quota limits, Agent image, and container limits.

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/platform/templates -v`

Expected: PASS.

```bash
git add evalscope_platform/services/templates.py evalscope_platform/schemas/templates.py evalscope_platform/api/routers/templates.py tests/platform/templates
git commit -m "feat(platform): add immutable test snapshots"
```

### Task 8: Implement quota preflight and MySQL task leasing

**Files:**
- Create: `evalscope_platform/services/quota.py`
- Create: `evalscope_platform/workers/lease.py`
- Create: `evalscope_platform/workers/main.py`
- Create: `evalscope_platform/api/routers/tasks.py`
- Test: `tests/platform/tasks/test_quota.py`
- Test: `tests/platform/tasks/test_leases.py`
- Test: `tests/platform/tasks/test_cancel.py`

**Interfaces:**
- Produces: `validate_perf_budget(snapshot) -> PerfBudget`, `claim_next_task(worker_id, lease_seconds) -> Task | None`, `heartbeat(task_id, worker_id)`, and `request_cancel(task_id)`.

- [ ] **Step 1: Write tests for budget rejection, concurrent claims, expired leases, and cancellation**

```python
def test_claim_is_exclusive(mysql_session_factory, queued_task):
    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(lambda worker: claim_next_task(mysql_session_factory, worker, 30), ['a', 'b']))
    assert sum(task is not None for task in claimed) == 1


def test_perf_budget_rejects_rpm_over_connection_limit():
    with pytest.raises(QuotaExceeded, match='RPM'):
        validate_perf_budget(make_snapshot(rate=2.0, rpm_limit=60))
```

- [ ] **Step 2: Run tests against MySQL 8 and verify failure**

Run: `pytest tests/platform/tasks -v -m mysql`

Expected: FAIL because quota and lease services do not exist.

- [ ] **Step 3: Implement transaction-safe claiming**

Use `SELECT ... FOR UPDATE SKIP LOCKED`, then update `status`, `lease_owner`, `lease_expires_at`, and `heartbeat_at` inside the same transaction. A recovery pass changes expired `claimed/running` tasks to `queued` only when no live executor process is registered; append every transition to `task_events`.

- [ ] **Step 4: Implement mandatory performance guardrails**

Reject missing supplier ceilings. Compute planned request count, estimated input/output tokens, duration, rate, concurrency, RPM, and TPM from the immutable snapshot. Require `high_load_confirmed=true` when a plan exceeds 80% of any configured ceiling. Cancellation changes the database state immediately and the worker terminates the subprocess gracefully, then forcefully after ten seconds.

- [ ] **Step 5: Run tests and commit**

Run: `pytest tests/platform/tasks -v -m mysql`

Expected: PASS.

```bash
git add evalscope_platform/services/quota.py evalscope_platform/workers evalscope_platform/api/routers/tasks.py tests/platform/tasks
git commit -m "feat(platform): add safe MySQL task execution"
```

### Task 9: Run capability evaluations and persist complete results

**Files:**
- Create: `evalscope_platform/workers/executors/evaluation.py`
- Create: `evalscope_platform/workers/importers/evaluation.py`
- Create: `tests/platform/execution/test_evaluation_import.py`
- Create: `tests/platform/execution/test_evaluation_failure.py`
- Create: `tests/platform/execution/test_embedding_rerank_evaluation.py`

**Interfaces:**
- Consumes: immutable task snapshot, decrypted credential, dataset JSONL scratch export.
- Produces: `execute_evaluation(task_id) -> EvalRun` with `eval_samples`, `eval_metrics`, logs, and a `report_snapshots` row.

- [ ] **Step 1: Write a mock OpenAI evaluation integration test**

```python
def test_evaluation_persists_report_and_samples(worker, evaluation_task, mock_openai_server, session):
    worker.execute(evaluation_task.id)
    run = session.scalar(select(EvalRun).where(EvalRun.task_id == evaluation_task.id))
    assert run.status == 'succeeded'
    assert session.scalar(select(func.count()).select_from(EvalSample).where(EvalSample.eval_run_id == run.id)) == 1
    assert session.scalar(select(func.count()).select_from(ReportSnapshot).where(ReportSnapshot.task_id == evaluation_task.id)) == 1
```

- [ ] **Step 2: Run tests and verify failure**

Run: `pytest tests/platform/execution/test_evaluation_*.py -v`

Expected: FAIL because the executor and importer do not exist.

- [ ] **Step 3: Implement the EvalScope adapter**

Create a private temporary directory, export the selected dataset version, construct `TaskConfig` from the snapshot, pass the API root for capability evaluation, and execute EvalScope in a child process. Dispatch Chat, Responses, and Anthropic through the native evaluator; dispatch Embedding and Rerank quality evaluation through EvalScope's RAGEval/MTEB backend with the selected API capability. Poll progress into `task_events`. Import predictions, scores, retrieval/ranking metrics, report JSON, and logs in one final transaction. The temporary directory is execution scratch and is not registered in any API.

- [ ] **Step 4: Verify OpenAI Chat, Responses, and Anthropic configuration mapping**

Run: `pytest tests/platform/execution/test_evaluation_*.py tests/platform/execution/test_embedding_rerank_evaluation.py -v`

Expected: PASS for mock Chat, Responses, Anthropic, Embedding, and Rerank quality runs.

- [ ] **Step 5: Commit**

```bash
git add evalscope_platform/workers/executors/evaluation.py evalscope_platform/workers/importers/evaluation.py tests/platform/execution
git commit -m "feat(platform): persist capability evaluations in MySQL"
```

### Task 10: Run performance tests and retain HTTP failures correctly

**Files:**
- Create: `evalscope_platform/workers/executors/performance.py`
- Create: `evalscope_platform/workers/importers/performance.py`
- Create: `evalscope/perf/plugin/api/anthropic_api.py`
- Modify: `evalscope/perf/utils/db_util.py`
- Modify: `evalscope/perf/utils/benchmark_util.py`
- Test: `tests/perf/test_result_error_storage.py`
- Test: `tests/perf/test_anthropic_api.py`
- Test: `tests/platform/execution/test_performance_import.py`

**Interfaces:**
- Produces: `execute_performance(task_id) -> PerfRun`, including every request's HTTP status, error body, timings, tokens, raw request/response, percentiles, and report snapshot.

- [ ] **Step 1: Write regression tests for the observed missing-error defect**

```python
def test_failed_request_round_trip_preserves_http_error(tmp_path):
    data = BenchmarkData(success=False, status_code=429, error='rate limit exceeded', request='{}')
    db_path = persist_and_close(tmp_path, data)
    row = sqlite3.connect(db_path).execute('SELECT status_code, error FROM result').fetchone()
    assert row == (429, 'rate limit exceeded')
```

- [ ] **Step 2: Run the regression test and verify current failure**

Run: `pytest tests/perf/test_result_error_storage.py -v`

Expected: FAIL because the current SQLite `result` table omits `status_code` and `error`.

- [ ] **Step 3: Persist status and error in EvalScope's transient performance database**

Add nullable `status_code INTEGER` and `error TEXT` columns and include them in encode/decode and insert paths. This is the only required core persistence change; it remains backward-compatible for ordinary EvalScope performance reports.

- [ ] **Step 4: Add the Anthropic Messages performance plugin**

Register `anthropic` and `anthropic_messages`, build `/v1/messages` payloads, set `x-api-key` and `anthropic-version`, parse JSON/SSE text deltas and usage, and populate the same `BenchmarkData` contract as OpenAI plugins.

- [ ] **Step 5: Implement the platform performance adapter and importer**

Resolve the complete endpoint from the snapshot, execute `run_perf_benchmark` in a child process, import the transient SQLite rows in batches of 1,000 into `perf_requests`, import summary/percentile JSON into normalized tables, and commit the report snapshot. A failed run still imports completed request records before marking the task failed.

- [ ] **Step 6: Run performance tests**

Run: `pytest tests/perf/test_result_error_storage.py tests/perf/test_anthropic_api.py tests/platform/execution/test_performance_import.py -v`

Expected: PASS, including 429 persistence and Anthropic SSE parsing.

- [ ] **Step 7: Commit**

```bash
git add evalscope/perf evalscope_platform/workers/executors/performance.py evalscope_platform/workers/importers/performance.py tests/perf tests/platform/execution/test_performance_import.py
git commit -m "feat(platform): persist complete performance results"
```

### Task 11: Add isolated Agent bridge execution and trace persistence

**Files:**
- Create: `evalscope_platform/workers/executors/agent.py`
- Create: `evalscope_platform/workers/importers/agent.py`
- Create: `evalscope_platform/services/container_policy.py`
- Test: `tests/platform/agent/test_container_policy.py`
- Test: `tests/platform/agent/test_trace_import.py`

**Interfaces:**
- Produces: Agent execution for `claude_code`, `codex`, `opencode`, and `gemini_cli`; every trace event is appended to `agent_trace_events`.

- [ ] **Step 1: Write tests that reject unsafe container configuration**

```python
@pytest.mark.parametrize('bad_config', [
    {'privileged': True},
    {'network_mode': 'host'},
    {'mounts': ['/:/host']},
])
def test_agent_policy_rejects_host_escape(bad_config):
    with pytest.raises(UnsafeContainerConfig):
        validate_container_policy(bad_config)
```

- [ ] **Step 2: Run tests and verify failure**

Run: `pytest tests/platform/agent -v`

Expected: FAIL because Agent platform adapters do not exist.

- [ ] **Step 3: Implement the mandatory container policy**

Require a read-only root filesystem, non-root UID, dropped Linux capabilities, `no-new-privileges`, explicit CPU/memory/PID/disk/timeout limits, no host mounts, and an allowlisted outbound network. Inject only the selected task credential and erase the container after trace import.

- [ ] **Step 4: Wrap the existing EvalScope Agent bridge**

Map the framework enum to the existing bridge configuration, run the disposable container on the private Agent worker, stream trace records and logs to MySQL in ordinal order, and preserve the final exit reason even after cancellation or timeout.

- [ ] **Step 5: Run tests and commit**

Run: `pytest tests/platform/agent -v`

Expected: PASS for four mocked framework adapters and all unsafe-policy cases.

```bash
git add evalscope_platform/workers/executors/agent.py evalscope_platform/workers/importers/agent.py evalscope_platform/services/container_policy.py tests/platform/agent
git commit -m "feat(platform): add isolated Agent evaluation workers"
```

### Task 12: Build report and comparison APIs from MySQL

**Files:**
- Create: `evalscope_platform/services/comparisons.py`
- Create: `evalscope_platform/schemas/reports.py`
- Create: `evalscope_platform/api/routers/reports.py`
- Create: `evalscope_platform/api/routers/comparisons.py`
- Test: `tests/platform/reports/test_report_api.py`
- Test: `tests/platform/reports/test_comparisons.py`

**Interfaces:**
- Produces: paginated task/report/request APIs and `build_comparison(task_ids, baseline_target_id) -> ComparisonReport`.

- [ ] **Step 1: Write tests for pagination, report ownership, metric normalization, and regression deltas**

```python
def test_comparison_marks_latency_regression(session, baseline_run, candidate_run):
    report = build_comparison(session, [baseline_run.task_id, candidate_run.task_id], baseline_run.target_id)
    latency = next(item for item in report.items if item.metric_key == 'latency_p95_s' and item.target_id == candidate_run.target_id)
    assert latency.delta_percent == pytest.approx(20.0)
    assert latency.regression_status == 'regressed'
```

- [ ] **Step 2: Run tests and verify failure**

Run: `pytest tests/platform/reports -v`

Expected: FAIL because report services do not exist.

- [ ] **Step 3: Implement structured report projections**

Render reports from MySQL only. Paginate `perf_requests`, `eval_samples`, logs, and Agent trace events by stable `(ordinal, id)` cursors. Normalize common quality, reliability, latency, TTFT, TPOT, throughput, and error metrics. Preserve protocol-specific metrics inside a namespaced JSON field.

- [ ] **Step 4: Implement baseline comparison rules**

For higher-is-better metrics, negative delta is regression; for latency/error metrics, positive delta is regression. Store thresholds in the comparison request snapshot so a future configuration edit cannot change an existing comparison.

- [ ] **Step 5: Run tests and commit**

Run: `pytest tests/platform/reports -v`

Expected: PASS.

```bash
git add evalscope_platform/services/comparisons.py evalscope_platform/schemas/reports.py evalscope_platform/api/routers/reports.py evalscope_platform/api/routers/comparisons.py tests/platform/reports
git commit -m "feat(platform): add MySQL reports and comparisons"
```

### Task 13: Replace frontend business state with authenticated platform workflows

**Files:**
- Create: `evalscope/web/src/auth/AuthContext.tsx`
- Create: `evalscope/web/src/auth/ProtectedRoute.tsx`
- Create: `evalscope/web/src/api/platform/*.ts`
- Create: `evalscope/web/src/pages/platform/LoginPage.tsx`
- Create: `evalscope/web/src/pages/platform/VendorsPage.tsx`
- Create: `evalscope/web/src/pages/platform/DatasetsPage.tsx`
- Create: `evalscope/web/src/pages/platform/TemplatesPage.tsx`
- Create: `evalscope/web/src/pages/platform/TasksPage.tsx`
- Create: `evalscope/web/src/pages/platform/TaskDetailPage.tsx`
- Create: `evalscope/web/src/pages/platform/ReportsPage.tsx`
- Create: `evalscope/web/src/pages/platform/ComparePage.tsx`
- Modify: `evalscope/web/src/App.tsx`
- Modify: `evalscope/web/src/layouts/MainLayout.tsx`
- Modify: `evalscope/web/src/components/nav/TopNav.tsx`
- Test: `evalscope/web/src/pages/platform/*.test.tsx`

**Interfaces:**
- Consumes: the API surface defined above and the CSRF token returned by `/api/auth/me`.
- Produces: login, supplier, model, dataset, template, task, live progress, report, per-request, trace, and comparison workflows.

- [ ] **Step 1: Write route and CSRF client tests**

```tsx
it('redirects an anonymous visitor to login', async () => {
  renderAppAt('/tasks', { meResponse: { status: 401 } })
  expect(await screen.findByRole('heading', { name: /sign in/i })).toBeInTheDocument()
})

it('adds the csrf token to mutations', async () => {
  await platformClient.post('/api/vendors', { name: 'Vendor' })
  expect(fetchMock).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({
    headers: expect.objectContaining({ 'X-CSRF-Token': 'csrf-token' }),
  }))
})
```

- [ ] **Step 2: Run frontend tests and verify failure**

Run: `cd evalscope/web && npm test -- --run src/pages/platform`

Expected: FAIL because the platform pages do not exist.

- [ ] **Step 3: Implement auth and typed API clients**

Use `credentials: 'include'`, keep the CSRF token in React memory, refresh it through `/api/auth/me`, and never persist API keys or session material in local/session storage. Redirect 401 responses to `/login` and show 403 CSRF errors without retrying the mutation.

- [ ] **Step 4: Implement business pages using existing UI and chart components**

The supplier form accepts one normalized root per connection and capability-level endpoint overrides. Task forms select stored connection/model/dataset/template records instead of accepting raw keys. Performance forms display computed request/token budgets and require the high-load confirmation returned by preflight. Reports query MySQL APIs and do not accept filesystem paths.

- [ ] **Step 5: Run frontend tests, lint, and build**

Run: `cd evalscope/web && npm test -- --run`

Expected: all Vitest tests pass.

Run: `cd evalscope/web && npm run lint && npm run build`

Expected: both commands exit 0.

- [ ] **Step 6: Commit**

```bash
git add evalscope/web/src
git commit -m "feat(platform): add authenticated testing workflows"
```

### Task 14: Add public-node and isolated-worker deployment assets

**Files:**
- Create: `deploy/app/compose.yaml`
- Create: `deploy/app/Caddyfile`
- Create: `deploy/app/.env.example`
- Create: `deploy/worker/evalscope-worker.service`
- Create: `deploy/worker/worker.env.example`
- Create: `deploy/worker/README.md`
- Create: `Dockerfile.platform`
- Create: `docs/platform/deployment.md`
- Test: `tests/platform/deploy/test_compose_config.py`

**Interfaces:**
- Public node: Caddy/Nginx -> FastAPI/static React; private MySQL only.
- Private nodes: trusted evaluation/performance worker and separate Agent worker with rootless Docker/OCI.

- [ ] **Step 1: Write deployment configuration tests**

```python
def test_compose_does_not_publish_mysql(compose_config):
    assert 'ports' not in compose_config['services']['mysql']


def test_public_proxy_sets_https_security_headers(caddyfile_text):
    assert 'Strict-Transport-Security' in caddyfile_text
    assert 'Content-Security-Policy' in caddyfile_text
```

- [ ] **Step 2: Add production settings examples without secrets**

Document required `.env` keys with empty values only. Set `Secure` cookies, trusted proxy networks, MySQL TLS, least-privilege API and worker database users, and private-network firewall rules. Explicitly document the accepted risks: no MFA, plaintext prompt/response bodies, and backups not enforced by application startup.

- [ ] **Step 3: Add separate Agent worker setup**

Run the worker service as an unprivileged host user against rootless Docker/OCI. The service must not bind a public port. Configure only private MySQL connectivity and the credential master key; restrict its database grants to task claim, heartbeat, logs, traces, and results required by workers.

- [ ] **Step 4: Validate deployment assets**

Run: `docker compose -f deploy/app/compose.yaml config`

Expected: exit 0 and no MySQL host port mapping.

Run: `pytest tests/platform/deploy/test_compose_config.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add deploy Dockerfile.platform docs/platform/deployment.md tests/platform/deploy
git commit -m "chore(platform): add secure split-node deployment"
```

### Task 15: Cut over `evalscope service` and remove legacy service contracts

**Files:**
- Modify: `evalscope/cli/start_service.py`
- Delete: `evalscope/service/app.py`
- Delete: `evalscope/service/blueprints/`
- Delete: `evalscope/service/perf_archive.py`
- Delete: obsolete file-scan tests under `tests/service/`
- Create: `tests/platform/e2e/test_supplier_to_comparison.py`
- Modify: `docs/zh/user_guides/service.md`
- Modify: `docs/en/user_guides/service.md`

**Interfaces:**
- Produces: `evalscope service` starts the new platform API; `evalscope-platform worker` starts a worker. No legacy filesystem/report API remains.

- [ ] **Step 1: Write a complete mock supplier acceptance test**

```python
def test_supplier_to_comparison_flow(platform, mock_openai_server):
    admin = platform.login()
    connection = admin.create_connection(root=mock_openai_server.root, key='secret')
    model = admin.add_model(connection, 'mock-model')
    dataset = admin.upload_jsonl('qa', b'{"question":"2+2?","answer":"4"}\n')
    template = admin.create_evaluation_template(model=model, dataset=dataset)
    task = admin.run_template(template)
    platform.worker.run_until_idle()
    report = admin.get_report(task)
    assert report['status'] == 'succeeded'
    assert report['samples'][0]['response'] == '4'
```

- [ ] **Step 2: Run the acceptance test before cutover**

Run: `pytest tests/platform/e2e/test_supplier_to_comparison.py -v -m mysql`

Expected: PASS against the new routers and worker.

- [ ] **Step 3: Switch the CLI and remove old service modules**

Change `ServiceCMD.execute()` to start `evalscope_platform.api.main:create_app` through Uvicorn. Replace `--outputs` with database-backed settings; reject the obsolete flag with a clear migration error. Remove the old Flask blueprints and file scanners after confirming no non-service imports depend on them.

- [ ] **Step 4: Run the complete acceptance gate**

Run: `pytest tests/platform -v -m "not live"`

Expected: all platform tests pass.

Run: `pytest tests/perf/test_perf_basic.py::TestPerfBasic::test_multi_parallel_sweep -v -s`

Expected: PASS.

Run: `pytest tests/cli/test_all.py::TestRun::test_ci_lite -v -s -p no:warnings`

Expected: PASS.

Run: `make lint`

Expected: exit 0.

Run: `cd evalscope/web && npm test -- --run && npm run build`

Expected: all tests pass and the production bundle builds.

- [ ] **Step 5: Perform public-edge acceptance**

Verify HTTPS redirect, secure cookie flags, five-failure lockout, CSRF rejection, authenticated supplier CRUD, a successful mock evaluation, a successful rate-limited performance run, cancellation, Agent container policy rejection, MySQL-only report retrieval, and absence of every `DELETE` route.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(platform): replace legacy EvalScope service"
```

---

## Launch Acceptance Criteria

- Anonymous access reaches only static login assets, `/health`, and `/api/auth/login`.
- Administrator bootstrap runs exactly once; password reset invalidates every existing session.
- API keys never appear in API responses, logs, task snapshots, report JSON, or frontend storage.
- One supplier can hold multiple connection profiles, credential versions, capabilities, and models.
- Evaluation uses API root; performance uses the derived full endpoint or an explicit capability override.
- `/models` synchronization is non-destructive and manual model entry works without `/models`.
- Custom JSONL/CSV and built-in benchmarks produce immutable MySQL dataset versions.
- Every task records an immutable template, endpoint, model, dataset, quota, and credential-version snapshot.
- Two concurrent workers cannot claim the same task; expired leases recover; cancellation terminates the child process.
- Capability, performance, and Agent reports load without reading an `outputs/` directory.
- Performance failures retain HTTP status and error body; a 429 is distinguishable from timeout and transport failure.
- The quota preflight blocks over-limit tests and requires explicit confirmation at 80% of a configured ceiling.
- Multi-target runs produce a stable comparison against the selected baseline.
- No API, worker, or foreign-key cascade can delete persisted test data.
- Public deployment passes HTTPS, cookie, CSRF, lockout, CSP, and private-MySQL checks.
- Agent containers cannot run privileged, use host networking, or mount host paths.

## Explicitly Deferred

- AIGC and multimodal inputs.
- Local checkpoints and LiteLLM.
- Multiple administrators, RBAC, tenants, SSO, TOTP, and email recovery.
- Scheduled tests, alert delivery, and recurring regression jobs.
- Report/data deletion and retention cleanup.
- Legacy `outputs/` import.
- Application-enforced backup and restore gates.
- Search over encrypted or unencrypted raw prompt/response bodies beyond indexed metadata fields.

## Known Accepted Risks

- Public password-only authentication has higher account-takeover risk than MFA; compensate with strong password policy, lockout, audit, TLS, and restricted administration practices.
- Prompt, response, log, and Agent trace bodies are plaintext in MySQL by decision; database access therefore grants content access.
- Permanent retention without an enforced backup gate can still lose all data after database corruption or operator failure.
- MySQL tables will grow without retention. Begin with correct composite indexes and batch insertion; introduce partitioning only after observed table size and query plans justify it.

## Self-Review Results

- **Spec coverage:** Every confirmed requirement maps to Tasks 1-15 and the launch criteria.
- **Placeholders:** The plan contains no incomplete implementation markers; every task names exact files, interfaces, commands, and expected results.
- **Type consistency:** Protocol, task status, immutable snapshot, credential version, report snapshot, and comparison names are consistent across schema, services, workers, APIs, and frontend tasks.
- **Scope control:** AIGC, multimodal, scheduling, deletion, legacy migration, and mandatory backups remain outside the first release exactly as confirmed.
