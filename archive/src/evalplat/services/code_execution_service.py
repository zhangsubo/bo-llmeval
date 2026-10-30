"""代码执行作业队列和证据持久化。

该模块只负责 CodeExecution 的生命周期；Docker 调用由
``sandbox_service`` 负责，作业循环由 ``code_worker`` 负责。
"""

from __future__ import annotations

import uuid
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from evalplat.core.errors import ConflictError, NotFoundError
from evalplat.config import get_settings
from evalplat.models.attempt import Attempt
from evalplat.models.code_execution import CodeExecution
from evalplat.models.request import Request
from evalplat.models.run import Run
from evalplat.models.task import Task
from evalplat.services.sandbox_service import get_sandbox_policy

TERMINAL_STATUSES = {"passed", "failed", "timeout", "unavailable", "cancelled"}
PENDING_STATUSES = {"pending", "running"}
PHASE_STATUSES = {"pending", "passed", "failed", "not_run"}
OPERATIONAL_FAILURE_CATEGORIES = {
    "worker_error",
    "worker_lease_expired",
    "sandbox_unavailable",
    "invalid_sandbox_result",
}


def enqueue_code_execution(
    db: Session,
    run: Run,
    request: Request | None,
    *,
    code: str,
    tests: str = "",
    sample_index: int | None = None,
    container_image: str | None = None,
    timeout_seconds: int = 10,
    memory_mb: int = 256,
    cpus: float = 1.0,
    pids_limit: int = 64,
) -> CodeExecution:
    """创建一个待执行作业；调用方决定何时提交外层事务。"""

    if run.id is None:
        raise ValueError("Run 必须先持久化")
    if request is not None and request.run_id != run.id:
        raise ValueError("Request 不属于当前 Run")
    if not isinstance(code, str):
        raise ValueError("代码必须为字符串")
    if not isinstance(tests, str):
        raise ValueError("测试代码必须为字符串")
    container_image = get_sandbox_policy(get_settings()).validate(container_image)
    if timeout_seconds <= 0 or memory_mb <= 0 or cpus <= 0 or pids_limit <= 0:
        raise ValueError("沙箱资源限制必须为正数")

    execution = CodeExecution(
        run_id=run.id,
        request_id=request.id if request is not None else None,
        sample_index=sample_index,
        code=code,
        tests=tests,
        container_image=container_image,
        timeout_seconds=timeout_seconds,
        memory_mb=memory_mb,
        cpus=cpus,
        pids_limit=pids_limit,
    )
    db.add(execution)
    db.flush()
    return execution


def enqueue_code_jobs(
    db: Session,
    run: Run,
    samples: list[dict[str, Any]],
    results: list[dict[str, Any]],
    *,
    snapshot: dict[str, Any] | None = None,
) -> int:
    """把已完成的代码生成 Request 转换为幂等的沙箱作业。"""

    created = 0
    config = snapshot or {}
    for sample, result in zip(samples, results, strict=False):
        if result.get("status") != "completed":
            continue
        raw_request_id = result.get("request_id")
        if raw_request_id is None:
            continue
        request = db.get(Request, raw_request_id)
        if request is None or request.run_id != run.id:
            continue
        existing = db.scalar(
            select(CodeExecution).where(CodeExecution.request_id == request.id)
        )
        if existing is not None:
            continue
        attempt = db.scalar(
            select(Attempt)
            .where(Attempt.request_id == request.id)
            .order_by(Attempt.attempt_index.desc())
            .limit(1)
        )
        fields = attempt.extracted_fields if attempt is not None else {}
        fields = fields if isinstance(fields, dict) else {}
        code = fields.get("content") or fields.get("text") or ""
        if not isinstance(code, str):
            code = str(code)
        code = _extract_code_block(code)
        tests = sample.get("tests", sample.get("test_code", sample.get("test", "")))
        if not isinstance(tests, str):
            tests = str(tests)
        enqueue_code_execution(
            db,
            run,
            request,
            code=code,
            tests=tests,
            # Requests are assigned their canonical zero-based index by the
            # quality executor. Keep code evidence aligned even when a custom
            # sample omitted (or supplied a conflicting) sample_index.
            sample_index=request.sample_index,
            container_image=str(
                config.get("code_sandbox_image")
                or get_sandbox_policy(get_settings()).configured_image
            ),
            timeout_seconds=int(config.get("code_timeout_seconds", 10) or 10),
            memory_mb=int(config.get("code_memory_mb", 256) or 256),
            cpus=float(config.get("code_cpus", 1.0) or 1.0),
            pids_limit=int(config.get("code_pids_limit", 64) or 64),
        )
        created += 1
    return created


def _extract_code_block(value: str) -> str:
    """Remove one Markdown code fence commonly emitted by code models."""

    match = re.search(r"```(?:python|py)?\s*\n?(.*?)```", value, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else value.strip()


def get_code_execution(db: Session, execution_id: uuid.UUID) -> CodeExecution:
    execution = db.get(CodeExecution, execution_id)
    if execution is None:
        raise NotFoundError("代码执行记录不存在")
    return execution


def claim_next_code_execution(
    db: Session,
    worker_id: str,
    *,
    lease_seconds: int = 60,
) -> CodeExecution | None:
    """按创建顺序认领一条作业，并递增 fencing token。"""

    if not worker_id or len(worker_id) > 64:
        raise ValueError("worker_id 必须为 1-64 个字符")
    if lease_seconds <= 0:
        raise ValueError("lease_seconds 必须大于 0")
    execution = db.scalar(
        select(CodeExecution)
        .where(CodeExecution.status == "pending")
        .order_by(CodeExecution.created_at.asc(), CodeExecution.id.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if execution is None:
        return None
    now = datetime.now(timezone.utc)
    # A sandbox is allowed to run for up to 300 seconds.  The default 60s
    # queue lease must cover that bounded execution plus a reconciliation
    # margin, otherwise the sweeper can fence a healthy worker mid-run.
    effective_lease_seconds = max(lease_seconds, int(execution.timeout_seconds or 0) + 30)
    execution.status = "running"
    execution.lease_owner = worker_id
    execution.lease_expires_at = now + timedelta(seconds=effective_lease_seconds)
    execution.fencing_token += 1
    execution.started_at = execution.started_at or now
    db.commit()
    db.refresh(execution)
    return execution


def finish_code_execution(
    db: Session,
    execution_id: uuid.UUID,
    worker_id: str,
    fencing_token: int,
    status: str,
    result: dict[str, Any] | None = None,
) -> CodeExecution:
    """以 worker 身份和 fencing token 写回不可变终态结果。"""

    if status not in TERMINAL_STATUSES:
        raise ValueError(f"非法代码执行终态: {status}")
    execution = db.scalar(
        select(CodeExecution)
        .where(
            CodeExecution.id == execution_id,
            CodeExecution.status == "running",
            CodeExecution.lease_owner == worker_id,
            CodeExecution.fencing_token == fencing_token,
        )
        .with_for_update()
    )
    if execution is None:
        raise ConflictError("代码执行 lease 已失效")

    payload = result or {}
    execution.status = status

    # Sandbox runners are an integration boundary.  Normalize their phase
    # values before persistence so a malformed response cannot violate the
    # database CHECK constraints and strand an otherwise claimed job in
    # ``running`` until lease expiry.
    invalid_phase_values: list[str] = []
    compile_status = _normalize_phase_status(
        payload.get("compile_status"), execution.compile_status, invalid_phase_values, "compile_status"
    )
    run_status = _normalize_phase_status(
        payload.get("run_status"), execution.run_status, invalid_phase_values, "run_status"
    )
    execution.compile_status = compile_status
    execution.run_status = run_status
    execution.stdout = _bounded_text(payload.get("stdout"))
    execution.stderr = _bounded_text(payload.get("stderr"))
    raw_results = payload.get("test_results")
    execution.test_results = raw_results if isinstance(raw_results, dict) else None
    raw_exit_code = payload.get("exit_code")
    execution.exit_code = int(raw_exit_code) if isinstance(raw_exit_code, int) else None
    raw_duration = payload.get("duration_ms")
    execution.duration_ms = float(raw_duration) if isinstance(raw_duration, (int, float)) else None
    execution.error_category = _bounded_text(payload.get("error_category"), 32)
    execution.error_summary = _bounded_text(payload.get("error_summary") or payload.get("error"), 500)
    if invalid_phase_values:
        execution.status = "failed"
        execution.error_category = "invalid_sandbox_result"
        execution.error_summary = _bounded_text(
            "沙箱返回非法阶段状态: " + ", ".join(invalid_phase_values), 500
        )
    execution.finished_at = datetime.now(timezone.utc)
    execution.lease_owner = None
    execution.lease_expires_at = None
    db.commit()
    db.refresh(execution)
    reconcile_run_after_code_execution(db, execution.run_id)
    return execution


def reconcile_run_after_code_execution(db: Session, run_id: uuid.UUID) -> Run | None:
    """在代码作业全部结束后收敛等待中的 Run 和父 Task。

    代码测试失败是有效的质量结果，不应把 Run 误报成 Worker 失败；只有
    请求失败、沙箱不可用/超时或取消才会产生非 ``completed`` 的 Run 终态。
    ``with_for_update`` 保证多个 code worker 同时完成最后作业时只有一个
    worker 写入 Run 终态。
    """

    run = db.scalar(
        select(Run).where(Run.id == run_id).with_for_update()
    )
    if run is None or run.status not in {"awaiting_code", "cancelling"}:
        return run

    executions = list(
        db.scalars(
            select(CodeExecution)
            .where(CodeExecution.run_id == run_id)
            .order_by(CodeExecution.sample_index, CodeExecution.created_at)
        )
    )
    if not executions or any(item.status in PENDING_STATUSES for item in executions):
        return run

    requests = list(
        db.scalars(
            select(Request).where(Request.run_id == run_id).order_by(Request.id)
        )
    )
    if any(request.status not in {"completed", "failed", "cancelled"} for request in requests):
        return run

    task = db.get(Task, run.task_id) if run.task_id else None
    if (task and task.status == "cancelling") or run.status == "cancelling":
        final_status = "cancelled"
        error_summary = "任务在代码评分完成前被取消"
    else:
        completed_requests = sum(request.status == "completed" for request in requests)
        failed_requests = sum(request.status == "failed" for request in requests)
        cancelled_requests = sum(request.status == "cancelled" for request in requests)
        operational_failures = [
            item
            for item in executions
            if item.status in {"timeout", "unavailable", "cancelled"}
            or item.error_category in OPERATIONAL_FAILURE_CATEGORIES
        ]
        budget_truncated = run.error_summary == "Run runtime budget exceeded"
        if cancelled_requests and not completed_requests:
            final_status = "cancelled"
        elif failed_requests and not completed_requests:
            final_status = "failed"
        elif budget_truncated:
            # The normal worker may stop scheduling quality samples when the
            # duration budget expires. Code jobs only represent the admitted
            # prefix, so even a fully passing prefix is incomplete.
            final_status = "partially_completed"
        elif failed_requests or cancelled_requests or operational_failures:
            final_status = "partially_completed"
        else:
            # ``failed`` code status normally means the generated answer did
            # not pass its assertions, which is a completed evaluation result.
            final_status = "completed"
        error_summary = (
            run.error_summary
            if budget_truncated
            else next(
                (
                    item.error_summary
                    for item in operational_failures
                    if item.error_summary
                ),
                None,
            )
        )

    run.status = final_status
    run.error_summary = error_summary
    run.finished_at = datetime.now(timezone.utc)
    run.lease_owner = None
    run.lease_expires_at = None
    db.commit()
    db.refresh(run)

    if run.task_id:
        # Import lazily to keep the code execution lifecycle module independent
        # from the task orchestration module at import time.
        from evalplat.services.task_lifecycle_service import sync_from_runs

        sync_from_runs(db, run.task_id)
    return run


def fail_expired_code_executions(
    db: Session,
    now: datetime | None = None,
) -> int:
    """回收失联 code worker 的作业，不自动重跑生成结果。"""

    current = now or datetime.now(timezone.utc)
    expired = list(
        db.scalars(
            select(CodeExecution)
            .where(
                CodeExecution.status == "running",
                CodeExecution.lease_expires_at.isnot(None),
                CodeExecution.lease_expires_at < current,
            )
            .with_for_update(skip_locked=True)
        )
    )
    run_ids = {execution.run_id for execution in expired}
    for execution in expired:
        execution.status = "failed"
        execution.error_category = "worker_lease_expired"
        execution.error_summary = "code worker lease expired"
        execution.finished_at = current
        execution.lease_owner = None
        execution.lease_expires_at = None
    if expired:
        db.commit()
        for run_id in run_ids:
            reconcile_run_after_code_execution(db, run_id)
    return len(expired)


def list_code_executions(
    db: Session,
    run_id: uuid.UUID,
    *,
    skip: int = 0,
    limit: int = 20,
) -> tuple[list[CodeExecution], int]:
    """分页读取一个 Run 的代码执行证据。"""

    from sqlalchemy import func

    if db.get(Run, run_id) is None:
        raise NotFoundError("Run 不存在")
    statement = (
        select(CodeExecution)
        .where(CodeExecution.run_id == run_id)
        .order_by(CodeExecution.sample_index.asc(), CodeExecution.created_at.asc())
        .offset(skip)
        .limit(limit)
    )
    total = db.scalar(
        select(func.count()).select_from(CodeExecution).where(CodeExecution.run_id == run_id)
    ) or 0
    return list(db.scalars(statement)), total


def _bounded_text(value: object, limit: int = 10_000) -> str | None:
    if value is None:
        return None
    return str(value)[-limit:]


def _normalize_phase_status(
    value: object,
    fallback: str,
    invalid_values: list[str],
    field_name: str,
) -> str:
    """Return a database-valid phase status and record malformed values."""

    if value is None or value == "":
        return fallback if fallback in PHASE_STATUSES else "pending"
    normalized = str(value).strip().lower()
    if normalized in PHASE_STATUSES:
        return normalized
    invalid_values.append(f"{field_name}={normalized[:64]}")
    return "not_run"
