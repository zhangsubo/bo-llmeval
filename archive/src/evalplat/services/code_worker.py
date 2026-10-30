"""独立代码 Worker：消费 CodeExecution 并调用隔离沙箱。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from evalplat.config import get_settings
from evalplat.services.code_execution_service import (
    claim_next_code_execution,
    finish_code_execution,
)
from evalplat.services.sandbox_service import get_sandbox_policy, run_python_sandbox
from evalplat.services.security_service import redact_sensitive_text


def process_one(
    db: Session,
    worker_id: str,
    *,
    sandbox_runner: Callable[..., dict[str, Any]] | None = None,
    lease_seconds: int = 60,
) -> dict[str, Any] | None:
    """认领并执行一条代码作业；没有待执行作业时返回 ``None``。"""

    execution = claim_next_code_execution(db, worker_id, lease_seconds=lease_seconds)
    if execution is None:
        return None
    runner = sandbox_runner or run_python_sandbox
    try:
        get_sandbox_policy(get_settings()).validate(execution.container_image)
        raw_result = runner(
            code=execution.code,
            tests=execution.tests,
            image=execution.container_image,
            timeout_seconds=execution.timeout_seconds,
            memory_mb=execution.memory_mb,
            cpus=float(execution.cpus),
            pids_limit=execution.pids_limit,
        )
        result = _sanitize_result(raw_result)
    except ValueError as exc:
        result = {
            "status": "failed",
            "compile_status": "pending",
            "run_status": "not_run",
            "error_category": "sandbox_policy",
            "error_summary": redact_sensitive_text(str(exc)[:500]),
        }
    except Exception as exc:
        result = {
            "status": "failed",
            "compile_status": "pending",
            "run_status": "not_run",
            "error_category": "worker_error",
            "error_summary": redact_sensitive_text(str(exc)[:500]),
        }

    status = _terminal_status(result.get("status"))
    if status == "timeout" and not result.get("error_category"):
        result["error_category"] = "timeout"
    if status == "unavailable" and not result.get("error_category"):
        result["error_category"] = "sandbox_unavailable"
    updated = finish_code_execution(
        db,
        execution.id,
        worker_id,
        execution.fencing_token,
        status,
        result,
    )
    return {
        "execution_id": str(updated.id),
        "run_id": str(updated.run_id),
        "status": updated.status,
        "compile_status": updated.compile_status,
        "run_status": updated.run_status,
    }


def _terminal_status(value: object) -> str:
    normalized = str(value or "failed").lower()
    if normalized in {"passed", "failed", "timeout", "unavailable", "cancelled"}:
        return normalized
    return "failed"


def _sanitize_result(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"status": "failed", "error_category": "invalid_sandbox_result", "error_summary": "沙箱返回结果格式错误"}
    result = dict(value)
    for key in ("stdout", "stderr", "error", "error_summary"):
        if key in result and result[key] is not None:
            result[key] = redact_sensitive_text(str(result[key])[-10_000:])
    return result
