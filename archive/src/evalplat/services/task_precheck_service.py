"""任务入队前的集中式预检。

预检只读取资源和配置，不发送供应商请求。它返回可展示的结构化检查项，
让路由、任务服务和前端共享同一套阻断规则。
"""

from __future__ import annotations

import shutil
import uuid
import copy
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from evalplat.config import get_settings
from evalplat.core.ssrf import SSRFGuard
from evalplat.models.connection import Connection
from evalplat.models.dataset import Dataset, DatasetVersion
from evalplat.models.model_deployment import ModelDeployment
from evalplat.models.system_setting import SystemSetting
from evalplat.models.tokenizer import Tokenizer
from evalplat.services.load_profile_service import build_schedule
from evalplat.services.length_preset_service import get_length_preset
from evalplat.services.tokenizer_service import count_tokens
from evalplat.services.sandbox_service import get_sandbox_policy
from evalplat.services.dataset_service import validate_ready_version

WORKER_HEARTBEAT_SETTING_KEY = "worker_heartbeat"
CODE_WORKER_HEARTBEAT_SETTING_KEY = "code_worker_heartbeat"
GLOBAL_CONCURRENCY_SETTING_KEY = "global_max_parallel_runs"
_EVALSCOPE_CONNECTION_LIMIT_KEYS = (
    "max_concurrency",
    "concurrency",
    "rps",
    "max_rps",
    "rpm",
    "max_rpm",
    "tpm",
    "max_tpm",
)


def merge_restrictive_policy(
    requested: Any,
    connection_policy: Any,
) -> dict[str, Any]:
    """Merge task policy with a connection policy without widening limits.

    Task-level limits/timeouts are overrides for a single run, but the
    connection remains the hard upper bound. Numeric values are therefore
    clamped to the connection value when both sides provide one. Non-numeric
    or task-only keys are preserved and validated by their respective
    consumers.
    """

    base = copy.deepcopy(connection_policy) if isinstance(connection_policy, dict) else {}
    if not isinstance(requested, dict):
        return base
    for key, value in requested.items():
        connection_value = base.get(key)
        try:
            requested_number = float(value)
            connection_number = float(connection_value) if connection_value is not None else None
        except (TypeError, ValueError):
            requested_number = connection_number = None
        if connection_number is not None and requested_number > 0 and connection_number > 0:
            base[key] = min(requested_number, connection_number)
            # Preserve integer shape for fields such as max_requests.
            if isinstance(value, int) and isinstance(connection_value, int):
                base[key] = int(base[key])
        else:
            base[key] = copy.deepcopy(value)
    return base


def policy_widenings(requested: Any, connection_policy: Any) -> list[tuple[str, Any, Any]]:
    """Return requested numeric policy values that exceed connection limits."""

    if not isinstance(requested, dict) or not isinstance(connection_policy, dict):
        return []
    violations: list[tuple[str, Any, Any]] = []
    for key, value in requested.items():
        if key not in connection_policy:
            continue
        try:
            requested_number = float(value)
            connection_number = float(connection_policy[key])
        except (TypeError, ValueError):
            continue
        if requested_number > connection_number > 0:
            violations.append((key, value, connection_policy[key]))
    return violations


def _check(
    name: str,
    status: str,
    detail: str,
    *,
    target: str | None = None,
    current: Any = None,
    limit: Any = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": name,
        "status": status,
        "detail": detail,
    }
    if target is not None:
        result["target"] = target
    if current is not None:
        result["current"] = current
    if limit is not None:
        result["limit"] = limit
    return result


def _setting_value(db: Session, key: str) -> dict[str, Any] | None:
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
    return row.value if row and isinstance(row.value, dict) else None


def get_global_concurrency_limit(db: Session, fallback: int | None = None) -> int:
    """Read the operator-configured global Run limit with env fallback."""

    default = fallback if fallback is not None else get_settings().worker_max_concurrency
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == GLOBAL_CONCURRENCY_SETTING_KEY))
    raw: Any = row.value if row is not None else None
    if isinstance(raw, dict):
        raw = raw.get("value", raw.get("limit", raw.get("max_concurrency")))
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _heartbeat_check(
    db: Session,
    key: str,
    *,
    timeout_seconds: int,
    name: str,
    required: bool,
) -> dict[str, Any]:
    from evalplat.services.worker_heartbeat_service import health_snapshot

    worker_type = "code" if key == CODE_WORKER_HEARTBEAT_SETTING_KEY else "normal"
    snapshot = health_snapshot(db, worker_type=worker_type, timeout_seconds=timeout_seconds)
    details = snapshot.get("workers") or []
    if not details:
        return _check(
            name,
            "failed" if required else "warning",
            "未发现 Worker 心跳",
        )
    healthy = [item for item in details if item.get("status") == "healthy"]
    stale = [item for item in details if item.get("status") != "healthy"]
    if not healthy:
        return _check(
            name,
            "failed" if required else "warning",
            "未发现在线 Worker",
            current=stale,
            limit=timeout_seconds,
        )
    if stale:
        return _check(
            name,
            "warning",
            "部分 Worker 心跳过期或状态异常",
            current={"healthy": len(healthy), "stale": stale},
            limit=timeout_seconds,
        )
    return _check(name, "passed", "Worker 在线", current=len(healthy))


def _disk_checks(db: Session) -> list[dict[str, Any]]:
    settings = get_settings()
    configured = _setting_value(db, "precheck") or {}
    try:
        minimum_mb = int(configured.get("min_free_disk_mb", 100))
    except (TypeError, ValueError):
        minimum_mb = 100
    checks: list[dict[str, Any]] = []
    for name, raw_path in (
        ("report_disk", settings.report_dir),
        ("dataset_disk", settings.dataset_dir),
        ("evalscope_disk", settings.evalscope_dir),
    ):
        path = Path(raw_path).expanduser()
        try:
            path.mkdir(parents=True, exist_ok=True)
            free_mb = shutil.disk_usage(path).free / (1024 * 1024)
        except OSError:
            # Filesystem exceptions often include an absolute host path. The
            # precheck is persisted and returned to the browser, so keep the
            # detail actionable without exposing deployment internals.
            checks.append(_check(name, "failed", "无法检查目录"))
            continue
        checks.append(
            _check(
                name,
                "passed" if free_mb >= minimum_mb else "failed",
                "磁盘空间充足" if free_mb >= minimum_mb else "磁盘剩余空间不足",
                current=round(free_mb, 1),
                limit=minimum_mb,
            )
        )
    return checks


def _estimate_prompt_tokens(
    db: Session,
    config: dict[str, Any],
) -> tuple[int | None, str]:
    preset_value = config.get("input_length_preset")
    if preset_value:
        try:
            preset = get_length_preset(preset_value)
        except ValueError as exc:
            return None, str(exc)
        if preset is not None:
            tokenizer_id = config.get("tokenizer_id")
            if not tokenizer_id:
                return None, "输入长度预设要求配置 Tokenizer"
            try:
                tokenizer = db.get(Tokenizer, uuid.UUID(str(tokenizer_id)))
            except (ValueError, TypeError):
                return None, "Tokenizer ID 格式无效"
            if not tokenizer or tokenizer.status != "ready":
                return None, "输入长度预设要求 Tokenizer 已就绪"
            return preset.target_input_tokens, f"已使用 {preset.label} 输入长度预设"
    messages = config.get("messages")
    if not isinstance(messages, list) or not messages:
        samples = config.get("quality_samples")
        if isinstance(samples, list) and samples:
            messages = samples[0].get("messages") if isinstance(samples[0], dict) else None
    if not isinstance(messages, list) or not messages:
        return None, "任务未提供 messages，无法估算输入长度"
    tokenizer_id = config.get("tokenizer_id")
    if not tokenizer_id:
        return None, "未配置 Tokenizer，无法精确估算输入长度"
    try:
        tokenizer = db.get(Tokenizer, uuid.UUID(str(tokenizer_id)))
    except (ValueError, TypeError):
        return None, "Tokenizer ID 格式无效"
    if not tokenizer or tokenizer.status != "ready":
        return None, "Tokenizer 未就绪，无法估算输入长度"
    value = count_tokens(tokenizer, messages)
    return (value, "输入 Token 已按指定 Tokenizer 估算") if value is not None else (None, "Tokenizer 加载失败，无法估算输入长度")


def _estimate_quality_total_tokens(
    db: Session,
    config: dict[str, Any],
    requested_output: int,
    planned_request_count: int,
) -> int | None:
    """Estimate all native code-quality provider attempts under a hard cap."""

    tokenizer_id = config.get("tokenizer_id")
    if not tokenizer_id:
        return None
    try:
        tokenizer = db.get(Tokenizer, uuid.UUID(str(tokenizer_id)))
    except (ValueError, TypeError):
        return None
    if tokenizer is None or tokenizer.status != "ready":
        return None
    samples = config.get("quality_samples")
    if isinstance(samples, list) and samples:
        total = 0
        for sample in samples:
            if not isinstance(sample, dict):
                return None
            messages = sample.get("messages")
            if not isinstance(messages, list) or not messages:
                return None
            prompt_tokens = count_tokens(tokenizer, messages)
            if prompt_tokens is None:
                return None
            params = sample.get("generation_params")
            sample_output = requested_output
            if isinstance(params, dict):
                raw = params.get("max_tokens", params.get("max_completion_tokens"))
                if raw is not None:
                    try:
                        sample_output = int(raw)
                    except (TypeError, ValueError):
                        return None
            if sample_output <= 0:
                return None
            if sample_output < 0:
                return None
            total += int(prompt_tokens) + sample_output
        return total
    if planned_request_count <= 0:
        return 0
    if requested_output <= 0:
        return None
    messages = config.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    prompt_tokens = count_tokens(tokenizer, messages)
    if prompt_tokens is None:
        return None
    return (int(prompt_tokens) + requested_output) * planned_request_count


def _limit_value(limits: dict[str, Any], *names: str) -> float | None:
    for name in names:
        value = limits.get(name)
        if value is None:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


def run_task_precheck(
    db: Session,
    config: dict[str, Any],
    *,
    run_mode: str,
    ssrf_guard: SSRFGuard | None = None,
) -> tuple[list[dict[str, Any]], list[tuple[ModelDeployment, Connection]]]:
    """返回检查项和可用于创建快照的有效目标上下文。"""

    checks: list[dict[str, Any]] = []
    contexts: list[tuple[ModelDeployment, Connection]] = []
    raw_ids = config.get("model_deployment_ids") or []
    if not raw_ids:
        checks.append(_check("targets", "failed", "至少选择一个模型部署"))
    for raw_id in raw_ids:
        try:
            deployment_id = uuid.UUID(str(raw_id))
        except (ValueError, TypeError):
            checks.append(_check("deployment", "failed", "模型部署 ID 格式无效", target=str(raw_id)))
            continue
        deployment = db.get(ModelDeployment, deployment_id)
        if not deployment:
            checks.append(_check("deployment", "failed", "模型部署不存在", target=str(deployment_id)))
            continue
        if deployment.status != "active":
            checks.append(_check("deployment", "failed", "模型部署未启用", target=str(deployment_id), current=deployment.status, limit="active"))
            continue
        # Task submission and credential replacement share the Connection row
        # as their concurrency boundary.  Locking it here means a successful
        # key replacement cannot race with a submit that is about to enqueue a
        # Run using the same deployment.
        connection = db.scalar(
            select(Connection)
            .where(Connection.id == deployment.connection_id)
            .with_for_update()
        )
        if not connection or connection.status != "active":
            checks.append(_check("connection", "failed", "连接不存在或未启用", target=str(deployment_id)))
            continue
        if not connection.api_key_ciphertext:
            checks.append(_check("credential", "failed", "连接未配置 API Key", target=str(deployment_id)))
            continue
        if ssrf_guard is not None:
            allowed, reason = ssrf_guard.check_url(connection.base_url)
            if not allowed:
                checks.append(_check("ssrf", "failed", f"连接地址未通过 SSRF 校验: {reason}", target=str(deployment_id)))
                continue
        contexts.append((deployment, connection))
        checks.append(_check("deployment", "passed", "目标可用", target=str(deployment_id)))

        supports = deployment.supports_streaming if config.get("stream") else deployment.supports_non_streaming
        mode_name = "stream" if config.get("stream") else "nonstream"
        if supports is False:
            checks.append(_check("capability", "failed", f"目标不支持 {mode_name} Chat Completions", target=str(deployment_id)))
        elif supports is None:
            checks.append(_check("capability", "warning", f"目标尚未确认 {mode_name} 能力", target=str(deployment_id)))
        else:
            checks.append(_check("capability", "passed", f"目标支持 {mode_name} Chat Completions", target=str(deployment_id)))

        params = config.get("generation_params") or {}
        try:
            length_preset = get_length_preset(config.get("input_length_preset"))
        except ValueError as exc:
            checks.append(_check("input_length_preset", "failed", str(exc), target=str(deployment_id)))
            length_preset = None
        max_tokens = params.get("max_tokens", params.get("max_completion_tokens"))
        try:
            requested_output = int(max_tokens) if max_tokens is not None else (length_preset.max_output_tokens if length_preset else 0)
        except (TypeError, ValueError):
            requested_output = 0
        if deployment.max_output_tokens and requested_output > deployment.max_output_tokens:
            checks.append(_check("max_output_tokens", "failed", "请求输出长度超过模型限制", target=str(deployment_id), current=requested_output, limit=deployment.max_output_tokens))
        else:
            checks.append(_check("max_output_tokens", "passed", "输出长度限制满足", target=str(deployment_id), current=requested_output, limit=deployment.max_output_tokens))
        prompt_tokens, prompt_detail = _estimate_prompt_tokens(db, config)
        if deployment.context_window and prompt_tokens is not None:
            total = prompt_tokens + requested_output
            context_status = "passed" if total <= deployment.context_window else "failed"
            checks.append(_check("context_window", context_status, "输入与输出未超出上下文窗口" if context_status == "passed" else "输入与输出超过上下文窗口", target=str(deployment_id), current=total, limit=deployment.context_window))
        elif length_preset is not None and length_preset.requires_all_targets:
            checks.append(_check("context_window", "failed", "超长输入预设要求所有目标明确支持上下文窗口", target=str(deployment_id), limit=length_preset.target_input_tokens + requested_output))
        else:
            checks.append(_check("context_window", "warning", prompt_detail, target=str(deployment_id), limit=deployment.context_window))

        requested_limits = config.get("limits")
        connection_limits = connection.limits or {}
        for key, requested_value, hard_value in policy_widenings(requested_limits, connection_limits):
            checks.append(
                _check(
                    "task_limit",
                    "failed",
                    f"任务级 {key} 不能超过连接硬限制",
                    target=str(deployment_id),
                    current=requested_value,
                    limit=hard_value,
                )
            )
        limits = merge_restrictive_policy(requested_limits, connection_limits)
        if (
            config.get("evaluation_engine", "evalscope") == "evalscope"
            and config.get("evaluation_kind") in {"performance", "quality"}
        ):
            unsupported_limits = [
                key for key in _EVALSCOPE_CONNECTION_LIMIT_KEYS
                if limits.get(key) is not None
            ]
            if unsupported_limits:
                checks.append(_check(
                    "evalscope_connection_limits",
                    "failed",
                    "EvalScope 执行无法逐请求接入连接级限流，禁止入队",
                    target=str(deployment_id),
                    current=unsupported_limits,
                ))
        profile = config.get("load_profile") or {"mode": "fixed_concurrency", "concurrency": 1}
        request_count = int(config.get("request_count", 1) or 1)
        warmup_count = int(config.get("warmup_count", 0) or 0)
        if config.get("evaluation_kind") == "quality":
            quality_samples = config.get("quality_samples")
            planned_request_count = len(quality_samples) if isinstance(quality_samples, list) and quality_samples else request_count
        else:
            planned_request_count = request_count + warmup_count + (1 if "warmup_count" in config else 0)
        try:
            schedule = build_schedule(profile, planned_request_count)
            checks.append(_check("load_profile", "passed", "负载配置有效", target=str(deployment_id), current=len(schedule)))
        except (TypeError, ValueError) as exc:
            checks.append(_check("load_profile", "failed", str(exc), target=str(deployment_id)))
            schedule = []
        mode = profile.get("mode") if isinstance(profile, dict) else None
        planned_requests = len(schedule)
        request_limit = _limit_value(limits, "max_requests", "max_request_count")
        if request_limit is not None:
            checks.append(_check(
                "request_count_limit",
                "passed" if planned_requests <= request_limit else "failed",
                "计划请求数未超过连接限制" if planned_requests <= request_limit else "计划请求数超过连接限制",
                target=str(deployment_id), current=planned_requests, limit=request_limit,
            ))
        token_limit = _limit_value(limits, "max_total_tokens")
        if token_limit is not None:
            estimated_total_tokens = None if prompt_tokens is None else (prompt_tokens + requested_output) * planned_requests
            if config.get("evaluation_engine", "evalscope") == "evalscope":
                # EvalScope owns dataset loading and may expand a benchmark
                # beyond the platform's request_count. Without a bounded
                # per-sample token contract, admitting the Run would make the
                # hard token cap advisory. Fail closed until the adapter can
                # reserve tokens per provider request.
                checks.append(_check(
                    "total_token_limit",
                    "failed",
                    "EvalScope 任务无法安全估算并逐请求执行 max_total_tokens，禁止入队",
                    target=str(deployment_id),
                    current=estimated_total_tokens,
                    limit=token_limit,
                ))
                continue
            if config.get("quality_mode") == "code":
                estimated_total_tokens = _estimate_quality_total_tokens(
                    db,
                    config,
                    requested_output,
                    planned_requests,
                )
                if estimated_total_tokens is None:
                    checks.append(_check(
                        "total_token_limit",
                        "failed",
                        "代码质量任务无法安全估算总 Token，禁止入队",
                        target=str(deployment_id),
                        limit=token_limit,
                    ))
                    continue
            checks.append(_check(
                "total_token_limit",
                "warning" if estimated_total_tokens is None else ("passed" if estimated_total_tokens <= token_limit else "failed"),
                "无法估算任务总 Token" if estimated_total_tokens is None else ("预计总 Token 未超过连接限制" if estimated_total_tokens <= token_limit else "预计总 Token 超过连接限制"),
                target=str(deployment_id), current=estimated_total_tokens, limit=token_limit,
            ))
        duration_limit = _limit_value(limits, "max_duration_seconds")
        if duration_limit is not None:
            timeout_config = merge_restrictive_policy(
                config.get("timeouts"), connection.timeouts or {}
            )
            try:
                request_timeout = float(timeout_config.get("read", 120))
            except (TypeError, ValueError):
                request_timeout = 120.0
            estimated_duration = (float(schedule[-1].offset_seconds) if schedule else 0.0) + request_timeout
            checks.append(_check(
                "duration_limit",
                "passed" if estimated_duration <= duration_limit else "failed",
                "预计任务时长未超过连接限制" if estimated_duration <= duration_limit else "预计任务时长超过连接限制",
                target=str(deployment_id), current=round(estimated_duration, 2), limit=duration_limit,
            ))
        concurrency = profile.get("concurrency") if mode == "fixed_concurrency" else profile.get("max_inflight") if isinstance(profile, dict) else None
        limit = _limit_value(limits, "max_concurrency", "concurrency")
        if limit is not None and concurrency is not None:
            checks.append(_check("connection_concurrency", "passed" if float(concurrency) <= limit else "failed", "连接并发限制满足" if float(concurrency) <= limit else "超过连接并发限制", target=str(deployment_id), current=concurrency, limit=limit))
        requested_rps = profile.get("rps") if isinstance(profile, dict) and mode == "fixed_rps" else None
        if requested_rps is not None:
            rps_limit = _limit_value(limits, "rps", "max_rps")
            if rps_limit is not None:
                checks.append(_check("connection_rps", "passed" if float(requested_rps) <= rps_limit else "failed", "连接 RPS 限制满足" if float(requested_rps) <= rps_limit else "超过连接 RPS 限制", target=str(deployment_id), current=requested_rps, limit=rps_limit))
            rpm_limit = _limit_value(limits, "rpm", "max_rpm")
            if rpm_limit is not None:
                requested_rpm = float(requested_rps) * 60
                checks.append(_check("connection_rpm", "passed" if requested_rpm <= rpm_limit else "failed", "连接 RPM 限制满足" if requested_rpm <= rpm_limit else "超过连接 RPM 限制", target=str(deployment_id), current=round(requested_rpm, 2), limit=rpm_limit))
        # TPM needs a conservative upper bound. If no tokenizer/length is known,
        # report a warning instead of pretending the estimate is precise.
        tpm_limit = _limit_value(limits, "tpm", "max_tpm")
        if tpm_limit is not None:
            estimated = None
            if prompt_tokens is not None:
                estimated = (prompt_tokens + requested_output) * max(1, int(config.get("request_count", 1) or 1))
            checks.append(_check("connection_tpm", "warning" if estimated is None else ("passed" if estimated <= tpm_limit else "failed"), "无法精确估算 TPM" if estimated is None else ("连接 TPM 限制满足" if estimated <= tpm_limit else "超过连接 TPM 限制"), target=str(deployment_id), current=estimated, limit=tpm_limit))

    if config.get("dataset_version_id"):
        try:
            dataset = db.get(DatasetVersion, uuid.UUID(str(config["dataset_version_id"])))
        except (ValueError, TypeError):
            dataset = None
        if dataset is None:
            checks.append(_check("dataset", "failed", "数据集版本不存在或未就绪"))
        else:
            reason = None
            parent = db.get(Dataset, dataset.dataset_id)
            expected_kind = "quality" if config.get("evaluation_kind") == "quality" else "performance"
            if parent is None:
                reason = "数据集父记录不存在"
            elif parent.kind != expected_kind:
                reason = f"数据集用途为 {parent.kind}，与 {expected_kind} 任务不匹配"
            else:
                reason = validate_ready_version(
                    dataset,
                    storage_root=get_settings().dataset_dir,
                )
            checks.append(
                _check(
                    "dataset",
                    "passed" if reason is None else "failed",
                    "数据集版本已就绪" if reason is None else reason,
                )
            )
    if config.get("tokenizer_id"):
        try:
            tokenizer = db.get(Tokenizer, uuid.UUID(str(config["tokenizer_id"])))
        except (ValueError, TypeError):
            tokenizer = None
        checks.append(_check("tokenizer", "passed" if tokenizer and tokenizer.status == "ready" else "failed", "Tokenizer 已就绪" if tokenizer and tokenizer.status == "ready" else "Tokenizer 不存在或未就绪"))
    if config.get("input_length_preset") and not config.get("tokenizer_id"):
        checks.append(_check("input_length_preset", "failed", "输入长度预设要求配置 Tokenizer"))

    if config.get("quality_mode") == "code":
        policy = get_sandbox_policy()
        image = str(config.get("code_sandbox_image") or policy.configured_image)
        try:
            policy.validate(image)
        except ValueError as exc:
            checks.append(_check("sandbox_image", "failed", str(exc), current=image, limit=sorted(policy.allowed_images)))
        else:
            checks.append(_check("sandbox_image", "passed", "代码沙箱镜像受控", current=image))

    if run_mode not in {"serial", "parallel"}:
        checks.append(_check("run_mode", "failed", "运行模式必须为 serial 或 parallel", current=run_mode))
    global_limit = get_global_concurrency_limit(db)
    requested_runs = len(contexts)
    if run_mode == "parallel" and requested_runs > global_limit:
        # The global limit controls how many Runs are *running* at once; it
        # must not reject a larger task because excess Runs can remain queued.
        checks.append(_check("global_concurrency", "warning", "并行 Run 将按系统上限分批执行", current=requested_runs, limit=global_limit))
    else:
        checks.append(_check("global_concurrency", "passed", "系统全局并发限制满足", current=requested_runs, limit=global_limit))

    heartbeat_timeout = int((_setting_value(db, "precheck") or {}).get("worker_heartbeat_timeout_seconds", 120))
    # The SQLite test harness has no real worker process. Production
    # PostgreSQL deployments require a live heartbeat before queueing.
    require_worker = not (db.bind is not None and db.bind.dialect.name == "sqlite")
    checks.append(_heartbeat_check(db, WORKER_HEARTBEAT_SETTING_KEY, timeout_seconds=heartbeat_timeout, name="worker_health", required=require_worker))
    if config.get("quality_mode") == "code":
        checks.append(_heartbeat_check(db, CODE_WORKER_HEARTBEAT_SETTING_KEY, timeout_seconds=heartbeat_timeout, name="code_worker_health", required=require_worker))
    else:
        checks.append(_check("code_worker_health", "passed", "当前任务不需要代码 Worker"))
    checks.extend(_disk_checks(db))
    return checks, contexts


def has_blocking_failure(checks: list[dict[str, Any]]) -> bool:
    """预检是否存在阻断项。"""

    return any(check.get("status") == "failed" for check in checks)
