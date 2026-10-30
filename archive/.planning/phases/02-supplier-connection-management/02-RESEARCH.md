# Phase 2: 供应商与连接管理 - Research

**Researched:** 2026-08-19
**Domain:** API Key 加密存储 / 连接测试 / 模型同步 / SSRF 防护 / 价格版本化 / CRUD API 模式
**Confidence:** HIGH（PRD 规格明确，技术方案均有官方库/文档支撑；加密方案和 SSRF 防护为安全关键路径，需 plan 阶段确认细节）

## Summary

Phase 2 在 Phase 1 建立的数据库 schema 和 FastAPI 骨架上，实现供应商、连接、模型部署、对比组和价格的完整业务逻辑。核心挑战集中在三个领域：

1. **API Key 加密必须在首次写入时就正确实现。** PRD §11.2 要求应用层加密存 PostgreSQL，主密钥来自 `.env`，页面只显示掩码，`credential_version` 引用版本号而非密钥本身。推荐 `cryptography` 库的 Fernet 对称加密——比 AES-GCM 简单一个数量级，自带完整性校验（HMAC），密钥轮转只需 re-encrypt 所有活跃连接的密文并递增 `credential_version` [CITED: cryptography.io/fernet]。
2. **连接测试是 Phase 2 的技术核心。** PRD §8.2.4 要求分别测试流式和非流式 Chat Completions，记录响应状态、耗时、供应商 request ID，并支持 `/v1/models` 自动同步。流式测试需要逐 chunk 解析 SSE 并计算 TTFT，这为 Phase 5 的完整性能采集打下基础。
3. **SSRF 防护是安全关键路径。** PRD §8.16.1 要求域名/CIDR 白名单、永久禁止地址（云元数据、loopback、link-local）、DNS rebinding 防护，且在 Base URL 保存、连接测试、任务预检和实际请求前均需校验。防护逻辑必须在 Phase 2 建立，Phase 10 安全加固阶段只做审计和强化。

**Primary recommendation:** 按实体依赖顺序分三波实现——Wave 1 做供应商 CRUD + 连接 CRUD（含加密）+ SSRF 基础校验；Wave 2 做连接测试 + 模型同步 + 模型部署 CRUD；Wave 3 做对比组 + 价格管理 + API Key 替换逻辑。

## Project Constraints (from AGENTS.md / PRD)

| 来源 | 约束 |
|------|------|
| PRD §11.2 | API Key 应用层加密存 PostgreSQL，主密钥来自受保护 `.env`，页面只显示掩码 |
| PRD §8.2.2 | 一个连接只绑定一把 API Key；多账号/多 Key 必须建立多条连接 |
| PRD §8.2.3 | 关联连接存在排队中或运行中任务时禁止替换 API Key |
| PRD §8.2.4 | 平台不跟随 HTTP 3xx；3xx 记为兼容性错误 |
| PRD §8.2.4 | 至少一种模式（流式/非流式）成功时可标记连接为"可用" |
| PRD §8.3.1 | 模型部署允许停用或归档，不提供物理删除 |
| PRD §8.3.2 | 一个模型部署首版最多属于一个对比组 |
| PRD §8.3.3 | 单价不得为负；非基准币种汇率必须大于 0 |
| PRD §8.16.1 | 云元数据、loopback、link-local、multicast/broadcast 不可通过白名单放行 |
| PRD §8.16.1 | 每次建立新连接池前重新解析域名并校验全部地址（DNS rebinding 防护） |
| PRD §11.4 | "忽略 TLS 错误"默认关闭，不放宽 SSRF 白名单或永久禁止地址 |
| AGENTS.md | 所有时间使用 UTC 存储 |
| AGENTS.md | PostgreSQL 是唯一数据库，不使用 Redis/Celery |

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SUPP-01 | 创建供应商记录（名称、简称、类型、状态、备注） | 见 Architecture Patterns → Pattern 1（供应商 CRUD） |
| SUPP-02 | 创建服务连接（Base URL、API Key、区域、限额） | 见 Pattern 2（连接 CRUD + 加密）+ Pattern 3（URL 规范化） |
| SUPP-03 | 测试连接的流式和非流式 Chat Completions | 见 Pattern 4（连接测试）+ Code Examples |
| SUPP-04 | 通过 /v1/models 自动同步模型列表 | 见 Pattern 5（模型同步） |
| SUPP-05 | 手工录入模型 ID | 见 Pattern 5（手工录入与同步冲突处理） |
| SUPP-06 | 替换 API Key，阻止运行中任务的密钥替换 | 见 Pattern 6（Key 替换 + 运行中检查） |
| SUPP-07 | API Key 加密存储 | 见 Pattern 2（Fernet 加密）+ Code Examples |
| SUPP-08 | 域名/CIDR 白名单阻止 SSRF | 见 Pattern 7（SSRF 防护） |
| MODEL-01 | 创建模型部署记录 | 见 Pattern 1（模型部署 CRUD） |
| MODEL-02 | 创建模型对比组 | 见 Pattern 1（对比组 CRUD） |
| MODEL-04 | 维护模型价格 | 见 Pattern 8（价格版本化） |
</phase_requirements>

## Standard Stack

### New Dependencies for Phase 2

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `cryptography` | 44.x | Fernet 对称加密 | Python 生态标准加密库，Fernet 自带 HMAC 完整性校验 [VERIFIED: pypi.org/project/cryptography + cryptography.io/fernet] |
| `httpx` | 0.28.1 | HTTP 客户端（连接测试） | Phase 1 已引入用于测试；Phase 2 作为运行时 HTTP 客户端调用供应商 API [VERIFIED: PyPI registry] |
| `ipaddress` | stdlib | IP 地址/CIDR 校验 | Python 标准库，SSRF 防护的基础 [CITED: docs.python.org/3/library/ipaddress.html] |
| `urllib.parse` | stdlib | URL 解析和规范化 | Python 标准库，Base URL 校验 [CITED: docs.python.org/3/library/urllib.parse.html] |
| `tiktoken` | latest | Tokenizer（内置 encoding） | OpenAI 官方 Tokenizer，Phase 3 详细集成；Phase 2 可选引入用于连接测试的 Token 估算 [VERIFIED: github.com/openai/tiktoken] |

**Note:** `httpx` 已在 Phase 1 pyproject.toml 中作为 dev 依赖引入。Phase 2 需将其提升为运行时依赖（连接测试服务使用）。

### Installation

```bash
pip install "cryptography>=44.0.0"
# httpx 已在 Phase 1 引入，无需重复安装
```

## Architecture Patterns

### Pattern 1: 实体 CRUD API 模式（扩展 Phase 1 suppliers 样板）

**What:** Phase 2 需要为 5 个实体组实现完整 CRUD：供应商、连接、模型部署、对比组、模型价格。遵循 Phase 1 建立的三层分离模式（Route → Pydantic Schema → ORM Service）。

**关键设计决策：**
- 供应商：无物理删除，只有 status 流转（active/disabled/archived）→ DELETE 返回 405，PATCH 支持 status 变更
- 连接：FK 到供应商，创建时触发加密写入
- 模型部署：FK 到连接，可选 FK 到对比组
- 对比组：独立实体，成员关系通过 `model_deployments.comparison_group_id` 维护（PRD §8.3.2："一个模型部署首版最多属于一个对比组"→ 用 FK 列而非关联表）
- 价格：追加式版本表，不改历史行

**路由结构（建议）：**

```
/api/v1/suppliers                          # CRUD（Phase 1 样板已建）
/api/v1/suppliers/{id}/connections         # 连接 CRUD（嵌套路由）
/api/v1/connections/{id}/test              # 连接测试
/api/v1/connections/{id}/sync-models       # 模型同步
/api/v1/connections/{id}/models            # 手工录入模型
/api/v1/model-deployments                  # 模型部署 CRUD（平铺路由）
/api/v1/model-deployments/{id}/prices      # 价格 CRUD
/api/v1/comparison-groups                  # 对比组 CRUD
```

### Pattern 2: API Key 加密存储

**What:** 使用 `cryptography.fernet.Fernet` 对 API Key 进行应用层加密。Fernet 是 AES-128-CBC + HMAC-SHA256 的组合，提供加密 + 完整性校验，比裸 AES-GCM 简单且不易出错 [CITED: cryptography.io/fernet#how-it-works]。

**加密流程：**
1. 页面录入明文 API Key → HTTPS POST 到后端
2. 后端用 `Fernet(master_key)` 加密 → 存 `connections.api_key_ciphertext`（bytes）
3. 响应只返回掩码（如 `sk-...abc`，保留前 3 后 3）
4. 连接测试/实际请求时解密 → 内存中使用 → 不落日志

**主密钥管理：**
- `APP_MASTER_KEY` 来自 `.env`，base64 编码的 32 字节随机密钥
- 生成方式：`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
- Phase 2 不实现自动密钥轮转；轮转逻辑属 Phase 10（手动 re-encrypt + 递增 credential_version）

**credential_version 设计：**
- `connections.credential_version` 整数，初始值 1
- 每次 API Key 替换递增
- 任务快照只引用版本号（PRD §6.2："不保存 API Key 明文或密文副本"）

**Why Fernet over AES-GCM：**
- Fernet 自带 timestamp + HMAC，解密时自动校验完整性
- AES-GCM 需要手动管理 nonce（绝不重用）和 tag，实现复杂度高
- 内部平台对加密性能要求不高（几十个连接，非热路径），Fernet 的简单性是正确选择

### Pattern 3: Base URL 规范化

**What:** PRD §8.2.2 要求 Base URL 使用标准 URL parser 规范化，禁止嵌入用户名/密码、非 HTTP(S) scheme、混淆斜杠。

**规范化规则：**
1. 只允许 `http` 或 `https` scheme
2. 去除 userinfo（用户名/密码嵌入 URL）
3. 去除末尾 `/`
4. 去除 path 中的 `/v1` 后缀（平台自动派生）
5. 主机名小写化
6. 禁止 IP 字面量中的混淆表示（如 `0x7f000001`、`2130706433`、`017700000001`）

**派生端点：**
- `/v1/models` → `{base_url}/v1/models`
- `/v1/chat/completions` → `{base_url}/v1/chat/completions`

### Pattern 4: 连接测试

**What:** 分别发送非流式和流式 Chat Completions 冒烟请求，记录完整结果。

**测试流程（PRD §8.2.4）：**
1. 校验前置条件：Base URL 格式、网络白名单、DNS/IP、TLS、API Key 已配置
2. 可选：先调 `/v1/models` 同步模型列表（失败不阻断）
3. 用户选择或输入 model ID
4. 发送非流式 Chat Completions（简单 prompt，如 "Hello"）
5. 发送流式 Chat Completions（同 prompt，SSE 解析）
6. 记录结果：状态码、耗时、供应商 request ID、错误正文（脱敏）

**流式 SSE 解析要点：**
- OpenAI Chat Completions 流式响应为 `text/event-stream`，每个 `data:` 行是 JSON
- 终止信号：`data: [DONE]`
- 需要计算 TTFT（首 token 时间）= 首个有效 chunk 到达时间 - 请求发送时间
- Phase 2 只做冒烟验证（成功/失败 + TTFT），完整性能指标属 Phase 5

**HTTP 客户端配置：**
- 使用 `httpx.Client`（同步，与 Phase 1 同步 SQLAlchemy 架构一致）
- 超时：连接超时 15s，读取超时 30s（PRD §9.5 默认值）
- 禁止跟随重定向（PRD §8.2.4："平台不跟随 HTTP 3xx"）
- `follow_redirects=False`

**3xx 处理：** HTTP 3xx 记为兼容性错误，不跟随。这与 PRD 要求一致。

### Pattern 5: 模型列表同步

**What:** 通过 `/v1/models` 自动同步模型列表，同时支持手工录入作为兜底。

**OpenAI `/v1/models` 响应格式（[VERIFIED: platform.openai.com/docs/api-reference/models/list]）：**

```json
{
  "object": "list",
  "data": [
    {
      "id": "gpt-4o",
      "object": "model",
      "created": 1700000000,
      "owned_by": "openai"
    }
  ]
}
```

**同步策略：**
1. 调用 `GET {base_url}/v1/models`（Bearer 认证）
2. 解析响应，提取 model id 列表
3. 对比已有 `model_deployments`（同一 connection_id）
4. 新增：自动创建 `model_deployments` 记录，source='synced'
5. 已存在（同 connection_id + model_id）：跳过，不覆盖人工元数据
6. 不删除：供应商下线的模型只标记为发现缺失，不自动归档（人工决策）

**手工录入与同步冲突：**
- 手工录入的记录 source='manual'
- 同步发现同 model_id 时：若已存在 source='manual' 的记录，跳过（不覆盖人工配置）
- 用户可在 UI 上将 synced 记录补充元数据（上下文窗口、流式能力等），但不得改写 model_id

**失败处理：**
- `/v1/models` 不可用不阻断接入（PRD §8.2.4："不可用不阻断接入"）
- 记录错误信息，用户可手工录入 model ID 作为兜底

### Pattern 6: API Key 替换

**What:** 替换连接的 API Key，包含运行中任务检查。

**替换流程（PRD §8.2.3）：**
1. 检查关联连接是否有排队中或运行中的任务（查询 `runs` 表 WHERE connection_id=? AND status IN ('queued', 'running')）
2. 有运行中任务 → 返回 409 Conflict，消息："关联连接存在运行中任务，无法替换密钥"
3. 无运行中任务 → 加密新 Key → 更新 `api_key_ciphertext` → 递增 `credential_version` → 删除旧密文（物理覆写）
4. 写审计日志（不含新旧 Key 内容）
5. 响应返回新掩码

**幂等性：** 替换操作本身不需要幂等键（非队列操作），但需要并发安全——两个替换请求同时到达时，用 `SELECT ... FOR UPDATE` 锁定连接行。

### Pattern 7: SSRF 防护

**What:** 阻止向内网、云元数据服务和其他禁止地址发送请求。

**永久禁止地址（PRD §8.16.1）：**
- Loopback: `127.0.0.0/8`, `::1/128`
- Link-local: `169.254.0.0/16`, `fe80::/10`
- Cloud metadata: `169.254.169.254/32`（AWS/GCP/Azure）, `fd00:ec2::254/128`（AWS IPv6）
- Multicast/Broadcast: `224.0.0.0/4`, `ff00::/8`, `255.255.255.255/32`
- 未指定地址: `0.0.0.0/8`, `::/128`
- 私有地址（默认禁止，白名单可放行）: `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `fc00::/7`

**校验流程：**
1. 解析 Base URL 提取主机名
2. DNS 解析获取所有 IP 地址（`socket.getaddrinfo`）
3. 对每个 IP 校验：
   a. 是否在永久禁止范围内 → 拒绝
   b. 是否在白名单允许范围内 → 通过
   c. 都不匹配 → 拒绝（默认拒绝）
4. 将已校验的 IP 绑定到本次连接（HTTP 连接使用已校验 IP，Host header 和 TLS SNI 使用原域名）

**DNS rebinding 防护：**
- 每次建立新连接池前重新解析域名
- 使用 `httpx` 的自定义 `transport` 将连接绑定到已校验 IP
- 不使用 DNS 缓存，或缓存 TTL 极短

**实现方式：**
- 使用 Python 标准库 `ipaddress` 模块进行 CIDR 匹配
- 使用 `socket.getaddrinfo` 进行 DNS 解析
- 封装为 `SSRFGuard` 类，提供 `check_url(url) -> (allowed, reason)` 接口
- 在 Base URL 保存、连接测试、任务预检和实际请求前调用

**白名单存储：** PRD §8.16.1 要求白名单通过后台页面维护并存入 PostgreSQL。建议用 `system_settings` 表的 JSONB 键值存储 `allowed_domains` 和 `allowed_cidrs`，Phase 11 再做管理 UI。Phase 2 先实现校验逻辑 + 从 DB 读取配置。

### Pattern 8: 价格版本化

**What:** 追加式价格记录，不修改历史行。汇率变化只影响未来任务。

**设计：**
- `model_prices` 表每行是一个价格版本
- 新价格 INSERT 新行，不 UPDATE 旧行
- 任务入队时从最新有效价格创建快照
- 价格版本号（`price_version`）自增，由 `(model_deployment_id, price_version)` 唯一约束保证

**字段：**
- `input_token_price`, `output_token_price`, `cached_token_price`: numeric，不得为负
- `unit`: 计价单位（如 "per_1k_tokens", "per_1m_tokens"）
- `currency`: 原始币种（如 "USD", "CNY"）
- `exchange_rate`: 对基准币种的汇率，> 0
- `effective_from`: 生效时间（UTC）
- `price_version`: 版本号，自增
- `source`: "manual" | "external"（首版只有 manual）
- 预留：`external_supplier_id`, `external_model_id`, `external_version`, `last_synced_at`

**币种处理：**
- 系统基准币种默认 CNY（从 `system_settings` 读取）
- 非基准币种价格由管理员手工维护汇率
- 缺少有效汇率时不得用于折算成本对比（PRD §8.3.3）

### 数据模型变更（Phase 2 新增/修改）

Phase 1 已建好全部表结构。Phase 2 主要是业务逻辑层，不需要 schema 变更。可能的微调：

| 表 | 变更 | 原因 |
|----|------|------|
| `connections` | 确认 `api_key_ciphertext` 类型为 `LargeBinary`（bytea） | Fernet 输出 bytes |
| `connections` | 确认 `credential_version` 有 `server_default=text("1")` | 初始版本 |
| `system_settings` | 新增键值：`allowed_domains`, `allowed_cidrs`, `base_currency` | SSRF 白名单 + 币种配置 |

## Code Examples

### Fernet 加密/解密

```python
# Source: cryptography.io/fernet (官方文档)
from cryptography.fernet import Fernet

# 初始化（从 .env 读取主密钥）
master_key = settings.app_master_key  # base64-encoded 32 bytes
f = Fernet(master_key)

# 加密
plaintext = "sk-abc123def456".encode()
ciphertext = f.encrypt(plaintext)  # bytes，含 timestamp + HMAC

# 解密
decrypted = f.decrypt(ciphertext).decode()  # "sk-abc123def456"

# 掩码
def mask_api_key(key: str, prefix_len: int = 3, suffix_len: int = 3) -> str:
    if len(key) <= prefix_len + suffix_len:
        return "*" * len(key)
    return key[:prefix_len] + "***" + key[-suffix_len:]
# "sk-abc123def456" → "sk-***456"
```

### 连接测试（非流式 + 流式）

```python
# Source: httpx 官方文档 + OpenAI Chat Completions API
import httpx
import json
import time

def test_connection_non_stream(base_url: str, api_key: str, model_id: str) -> dict:
    """非流式 Chat Completions 冒烟测试"""
    url = f"{base_url}/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": 5,
        "stream": False,
    }
    start = time.monotonic()
    try:
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            resp = client.post(url, json=payload, headers=headers)
        elapsed_ms = (time.monotonic() - start) * 1000
        return {
            "status": "success" if resp.status_code == 200 else "error",
            "http_status": resp.status_code,
            "elapsed_ms": round(elapsed_ms, 1),
            "vendor_request_id": resp.headers.get("x-request-id"),
            "error_body": resp.text[:500] if resp.status_code != 200 else None,
        }
    except httpx.HTTPError as e:
        return {"status": "error", "error": str(e)}

def test_connection_stream(base_url: str, api_key: str, model_id: str) -> dict:
    """流式 Chat Completions 冒烟测试"""
    url = f"{base_url}/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": 5,
        "stream": True,
    }
    ttft_ms = None
    start = time.monotonic()
    try:
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            with client.stream("POST", url, json=payload, headers=headers) as resp:
                first_chunk = True
                for line in resp.iter_lines():
                    if line.startswith("data: ") and line != "data: [DONE]":
                        if first_chunk:
                            ttft_ms = (time.monotonic() - start) * 1000
                            first_chunk = False
        elapsed_ms = (time.monotonic() - start) * 1000
        return {
            "status": "success",
            "ttft_ms": round(ttft_ms, 1) if ttft_ms else None,
            "elapsed_ms": round(elapsed_ms, 1),
        }
    except httpx.HTTPError as e:
        return {"status": "error", "error": str(e)}
```

### SSRF 防护

```python
# Source: Python 标准库 ipaddress + socket
import ipaddress
import socket
from urllib.parse import urlparse

# 永久禁止网段
PERMANENTLY_BLOCKED = [
    ipaddress.ip_network("127.0.0.0/8"),      # loopback
    ipaddress.ip_network("::1/128"),            # loopback IPv6
    ipaddress.ip_network("169.254.0.0/16"),     # link-local
    ipaddress.ip_network("fe80::/10"),          # link-local IPv6
    ipaddress.ip_network("169.254.169.254/32"), # cloud metadata
    ipaddress.ip_network("0.0.0.0/8"),         # unspecified
    ipaddress.ip_network("224.0.0.0/4"),       # multicast
    ipaddress.ip_network("ff00::/8"),          # multicast IPv6
    ipaddress.ip_network("255.255.255.255/32"), # broadcast
]

def resolve_and_check(url: str, allowed_cidrs: list[str]) -> tuple[bool, str]:
    """解析 URL 并校验所有解析到的 IP 是否安全"""
    parsed = urlparse(url)
    hostname = parsed.hostname
    
    # 禁止 URL 中嵌入 userinfo
    if parsed.username or parsed.password:
        return False, "URL 中不允许嵌入用户名/密码"
    
    # DNS 解析
    try:
        infos = socket.getaddrinfo(hostname, None)
        ips = {info[4][0] for info in infos}
    except socket.gaierror:
        return False, f"DNS 解析失败: {hostname}"
    
    # 校验每个 IP
    allowed_nets = [ipaddress.ip_network(c) for c in allowed_cidrs]
    for ip_str in ips:
        ip = ipaddress.ip_address(ip_str)
        
        # 永久禁止
        for blocked in PERMANENTLY_BLOCKED:
            if ip in blocked:
                return False, f"IP {ip_str} 在永久禁止范围 {blocked}"
        
        # 白名单校验（默认拒绝）
        if not any(ip in net for net in allowed_nets):
            return False, f"IP {ip_str} 不在白名单中"
    
    return True, "OK"
```

### `/v1/models` 同步

```python
def sync_models(connection_id: str, base_url: str, api_key: str, db: Session) -> dict:
    """同步供应商模型列表"""
    url = f"{base_url}/v1/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    
    try:
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            resp = client.get(url, headers=headers)
        if resp.status_code != 200:
            return {"status": "error", "message": f"HTTP {resp.status_code}"}
    except httpx.HTTPError as e:
        return {"status": "error", "message": str(e)}
    
    data = resp.json()
    model_ids = [m["id"] for m in data.get("data", [])]
    
    # 获取已存在的 model_deployments
    existing = db.query(ModelDeployment).filter(
        ModelDeployment.connection_id == connection_id
    ).all()
    existing_ids = {m.model_id: m for m in existing}
    
    added = 0
    for model_id in model_ids:
        if model_id not in existing_ids:
            db.add(ModelDeployment(
                connection_id=connection_id,
                model_id=model_id,
                display_name=model_id,
                source="synced",
                status="active",
            ))
            added += 1
        # 已存在（无论 source=synched/manual）→ 跳过
    
    db.commit()
    return {"status": "ok", "total": len(model_ids), "added": added, "skipped": len(model_ids) - added}
```

### API Key 替换（含运行中检查）

```python
def replace_api_key(connection_id: str, new_key: str, db: Session) -> dict:
    """替换连接的 API Key"""
    # SELECT ... FOR UPDATE 防止并发替换
    conn = db.query(Connection).filter(
        Connection.id == connection_id
    ).with_for_update().first()
    
    if not conn:
        raise NotFoundError("连接不存在")
    
    # 检查运行中任务
    active_runs = db.query(Run).join(Task).filter(
        Task.connection_id == connection_id,  # 需要确认 FK 路径
        Run.status.in_(["queued", "running"]),
    ).count()
    
    if active_runs > 0:
        raise ConflictError(f"关联连接存在 {active_runs} 个运行中任务，无法替换密钥")
    
    # 加密新 Key
    f = Fernet(settings.app_master_key)
    conn.api_key_ciphertext = f.encrypt(new_key.encode())
    conn.credential_version = conn.credential_version + 1
    
    db.commit()
    
    # 审计日志（不含 Key 内容）
    log_audit("api_key_replaced", target_type="connection", target_id=connection_id)
    
    return {"status": "ok", "new_mask": mask_api_key(new_key)}
```

## Anti-Patterns to Avoid

- **把加密/解密散落在各 route handler 中：** 加密逻辑封装在 service 层（如 `crypto.py`），route handler 只调用 service。
- **在日志中记录 API Key 明文或密文：** structlog 配置脱敏过滤器，`Authorization` header 和 `api_key_*` 字段一律过滤（PRD §11.2）。
- **连接测试跟随重定向：** PRD 明确"平台不跟随 HTTP 3xx"；`httpx` 设置 `follow_redirects=False`。
- **同步 `/v1/models` 时删除供应商下线的模型：** 只新增，不删除。下线模型的人工决策应由管理员执行。
- **价格记录用 UPDATE 而非 INSERT：** 价格是追加式版本表，永远 INSERT 新行。
- **SSRF 校验只在保存时做一次：** PRD 要求每次建立新连接池前重新解析域名——DNS 记录可能已变。
- **在连接测试中使用长超时：** 冒烟测试用短超时（连接 15s，读取 30s），避免阻塞 UI。
- **把 `/v1` 路径硬编码在 Base URL 中：** PRD §8.2.2 明确"Base URL 只填写域名和可选业务前缀，不填写 `/v1`"。

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| API Key 加密 | 手写 AES-CBC/GCM + PKCS7 padding | `cryptography.fernet.Fernet` | Fernet 自带 HMAC 完整性校验 + 时间戳，比手写 AES 少 90% 代码且无 nonce 重用风险 |
| URL 解析 | 正则匹配 URL 各部分 | `urllib.parse.urlparse` + `ipaddress` | 标准库处理 edge case（IPv6、端口、编码）比手写正则可靠 |
| CIDR 匹配 | 手写 IP 范围判断 | `ipaddress.ip_network` + `in` 运算符 | 标准库正确处理 IPv4/IPv6、前缀长度、特殊地址 |
| HTTP 流式请求 | 手写 SSE 逐行解析 + chunked transfer | `httpx.Client.stream()` | httpx 处理 chunked encoding、连接复用、超时 |
| JSON Schema 校验 | 手写字段检查 | Pydantic v2 models | FastAPI 内建 422 格式统一 |
| 数据库锁 | 应用层"先查再改"两步操作 | `SELECT ... FOR UPDATE` | 并发安全，防止两个替换请求同时执行 |

## Common Pitfalls

### Pitfall 1: Fernet 密钥丢失导致所有密文不可恢复
**What goes wrong:** `.env` 中 `APP_MASTER_KEY` 丢失或被覆盖，所有 `api_key_ciphertext` 无法解密。
**How to avoid:** 部署文档明确标注主密钥备份要求；`.env.example` 中注释说明生成方式和备份重要性。
**Warning signs:** 连接测试报"解密失败"。

### Pitfall 2: DNS rebinding 突破 SSRF 防护
**What goes wrong:** 保存时 DNS 解析到安全 IP，请求时解析到内网 IP。
**How to avoid:** 每次建立新连接池前重新解析；使用已校验 IP 建立连接（而非让 HTTP 客户端自行解析）。PRD §8.16.1 明确要求"将连接绑定到本次已校验地址"。
**Warning signs:** 安全扫描发现内网访问。

### Pitfall 3: 流式响应 SSE 解析不完整
**What goes wrong:** 只读取前几个 chunk 就认为测试成功，忽略了后续错误帧。
**How to avoid:** 必须读到 `data: [DONE]` 终止信号才算完整；中间任何 `error` 字段记为失败。
**Warning signs:** 连接测试"成功"但实际请求失败。

### Pitfall 4: 连接测试超时阻塞 UI
**What goes wrong:** 供应商 API 响应慢，同步测试请求阻塞 FastAPI 线程池。
**How to avoid:** 使用 `httpx` 的显式超时（connect=15s, read=30s）；考虑异步测试（但 Phase 1 选择了同步架构，所以用线程池 + 超时即可）。
**Warning signs:** 前端请求超时。

### Pitfall 5: 价格汇率为 0 导致成本计算除零
**What goes wrong:** 管理员录入价格时汇率留空或填 0，后续成本计算除零。
**How to avoid:** 数据库 CHECK 约束 `exchange_rate > 0`；Pydantic 校验 `gt=0`；PRD §8.3.3 明确"缺少有效汇率时不得用于折算成本对比"。
**Warning signs:** 成本对比页面报错。

### Pitfall 6: Base URL 中嵌入的 userinfo 绕过认证
**What goes wrong:** 用户在 Base URL 中填入 `http://user:pass@supplier.com`，绕过了 Bearer API Key 认证。
**How to avoid:** URL 规范化时检测并拒绝 userinfo；PRD §8.2.2 明确"禁止嵌入用户名/密码"。
**Warning signs:** 连接测试使用了错误的认证方式。

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | 使用 Fernet 而非 AES-GCM 做 API Key 加密 | Pattern 2 | 低：两者安全性相当，Fernet 更简单；若需要 AES-GCM 只需换加密层 |
| A2 | `api_key_ciphertext` 列类型为 `LargeBinary`（bytea） | Pattern 2 | 低：Phase 1 建表时已定义，若为 Text 需要迁移 |
| A3 | 白名单配置存储在 `system_settings` JSONB 中 | Pattern 7 | 低：Phase 11 可能拆为独立表，但键值存储足够 Phase 2 使用 |
| A4 | 连接测试使用同步 httpx（非 async） | Pattern 4 | 低：与 Phase 1 同步架构一致；性能不是连接测试的瓶颈 |
| A5 | 模型同步只新增不删除 | Pattern 5 | 中：PRD 未明确规定删除策略；保守方案是只增不删 |
| A6 | 价格 `exchange_rate` CHECK 约束为 `> 0` | Pattern 8 | 低：PRD §8.3.3 明确要求 |
| A7 | 连接测试的 model ID 由用户选择（非自动选择） | Pattern 4 | 低：PRD §8.2.4 "用户选择或手工录入准确 model ID" |

## Open Questions

1. **连接测试的 model ID 选择逻辑？** — PRD 说"用户选择或手工录入"，但 `/v1/models` 同步和连接测试的先后顺序如何？建议：先同步模型列表（可选），再让用户从列表中选择或手工输入 model ID 进行测试。
   - **Recommendation:** 同步是可选步骤，失败不阻断；测试时用户必须提供 model ID。

2. **`connections.api_key_ciphertext` 列在 Phase 1 中定义为什么类型？** — 需要确认 Phase 1 的 0001 迁移中是 `LargeBinary`（bytea）还是 `Text`。如果是 Text，Fernet 的 base64 输出可以直接存 Text；如果是 LargeBinary，存原始 bytes。
   - **Recommendation:** 查 Phase 1 迁移文件确认。Fernet 输出是 base64 bytes，两种类型都可以。

3. **白名单的默认值？** — 系统初始化时白名单应该为空（默认拒绝所有）还是包含常见供应商域名？
   - **Recommendation:** 默认为空，管理员手动配置。安全默认值为拒绝一切。

4. **连接测试是否需要在前端实时展示进度？** — 流式测试可以实时显示 chunk 到达，但 Phase 2 可能还没有前端。
   - **Recommendation:** Phase 2 后端返回完整结果即可，前端实时进度属后续阶段。

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `cryptography` | API Key 加密 | 需安装 | latest (44.x) | stdlib `hashlib` 只有哈希，无加密 |
| `httpx` | 连接测试 | Phase 1 已引入 | 0.28.1 | `urllib.request`（功能受限） |
| `ipaddress` | SSRF 防护 | stdlib | — | — |
| `urllib.parse` | URL 规范化 | stdlib | — | — |

**Missing dependencies with no fallback:** `cryptography`（Fernet 无标准库替代）
**Action:** 在 Phase 2 Wave 0 添加 `cryptography` 到 pyproject.toml 运行时依赖。

## Validation Architecture

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command |
|--------|----------|-----------|-------------------|
| SUPP-01 | 供应商 CRUD（POST 201, GET 200, PATCH 200, DELETE 405） | integration | `pytest tests/test_suppliers_api.py -x`（Phase 1 已建，可能需要扩展） |
| SUPP-02 | 连接 CRUD + 加密写入 | integration | `pytest tests/test_connections_api.py -x` |
| SUPP-03 | 连接测试（流式/非流式冒烟） | integration | `pytest tests/test_connection_test.py -x` |
| SUPP-04 | 模型同步 | integration | `pytest tests/test_model_sync.py -x` |
| SUPP-05 | 手工录入模型 ID | integration | `pytest tests/test_model_sync.py::test_manual录入 -x` |
| SUPP-06 | API Key 替换 + 运行中阻止 | integration | `pytest tests/test_api_key_replace.py -x` |
| SUPP-07 | 加密存储 + 掩码显示 | unit | `pytest tests/test_encryption.py -x` |
| SUPP-08 | SSRF 防护 | unit | `pytest tests/test_ssrf_guard.py -x` |
| MODEL-01 | 模型部署 CRUD | integration | `pytest tests/test_model_deployments_api.py -x` |
| MODEL-02 | 对比组 CRUD | integration | `pytest tests/test_comparison_groups_api.py -x` |
| MODEL-04 | 价格 CRUD（追加式） | integration | `pytest tests/test_prices_api.py -x` |

**边界用例（必须覆盖）：**
- 加密：空 API Key、超长 API Key、Unicode 字符
- URL 规范化：末尾斜杠、userinfo 嵌入、IPv6 地址、非 HTTP scheme
- SSRF：loopback、link-local、云元数据、IPv6 私有地址、DNS 解析失败
- 连接测试：超时、非 200 状态码、SSE 中间断流、3xx 重定向
- 价格：负数单价（应拒绝）、零汇率（应拒绝）、并发价格录入
- API Key 替换：有运行中任务（应拒绝）、并发替换、旧密文清理

---

*研究完成时间：2026-08-19*
