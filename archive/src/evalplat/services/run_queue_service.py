"""Run 持久化队列：认领、续租和带 fencing token 的终态写入。"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, func, select, text
from sqlalchemy.orm import Session, aliased

from evalplat.constants import RunStatus
from evalplat.core.errors import ConflictError
from evalplat.models.run import Run
from evalplat.models.task import Task
from evalplat.models.request import Request
from evalplat.models.attempt import Attempt


def claim_next_run(db: Session, worker_id: str, lease_seconds: int = 60, max_concurrency: int | None = None) -> Run | None:
    """按 queued_at FIFO 认领一个 Run。

    PostgreSQL 使用 ``FOR UPDATE SKIP LOCKED``，多个 worker 不会互相阻塞。
    """
    if not worker_id or len(worker_id) > 64:
        raise ValueError("worker_id 必须为 1-64 个字符")
    if lease_seconds <= 0:
        raise ValueError("lease_seconds 必须大于 0")
    if max_concurrency is not None and max_concurrency <= 0:
        raise ValueError("max_concurrency 必须大于 0")

    # Serialize the global capacity check with other PostgreSQL workers.
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": 918273645})
    if max_concurrency is not None:
        # ``cancelling`` can still hold an in-flight provider request and
        # ``awaiting_code`` still represents an unfinished quality Run whose
        # sandbox work is consuming the platform's execution capacity. Keep
        # both states inside the global count until the Run reaches a terminal
        # state, otherwise cancellation/code hand-off briefly increases the
        # logical parallel Run count above the configured limit.
        active_capacity = db.scalar(
            select(func.count())
            .select_from(Run)
            .where(
                Run.status.in_(
                    (RunStatus.RUNNING, RunStatus.CANCELLING, RunStatus.AWAITING_CODE)
                )
            )
        ) or 0
        if active_capacity >= max_concurrency:
            db.rollback()
            return None

    serial_sibling = aliased(Run)
    serial_task_has_active_sibling = exists(
        select(1)
        .select_from(Task)
        .join(serial_sibling, serial_sibling.task_id == Task.id)
        .where(
            Task.id == Run.task_id,
            Task.run_mode == "serial",
            serial_sibling.id != Run.id,
            serial_sibling.status.in_(
                (RunStatus.RUNNING, RunStatus.AWAITING_CODE, RunStatus.CANCELLING)
            ),
        )
    )
    run = db.scalar(
        select(Run)
        .where(Run.status == "queued")
        .order_by(Run.queued_at.asc(), Run.id.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if run is None:
        return None

    # Strict FIFO: a serial Task's queue head is allowed to block later Tasks.
    # Skipping it would turn the global queue into an implicit priority queue,
    # contradicting EXEC-03 and making observed order depend on task shape.
    if db.scalar(select(serial_task_has_active_sibling).where(Run.id == run.id)):
        db.rollback()
        return None

    now = datetime.now(timezone.utc)
    run.status = "running"
    run.lease_owner = worker_id
    run.lease_expires_at = now + timedelta(seconds=lease_seconds)
    run.fencing_token += 1
    run.heartbeat_at = now
    run.started_at = run.started_at or now
    db.commit()
    db.refresh(run)
    return run


def renew_lease(db: Session, run_id: uuid.UUID, worker_id: str, fencing_token: int, lease_seconds: int = 60) -> Run:
    """续租；worker 身份和 fencing token 任一不匹配都拒绝。"""
    run = db.scalar(
        select(Run).where(
            Run.id == run_id,
            Run.status.in_((RunStatus.RUNNING, RunStatus.CANCELLING)),
            Run.lease_owner == worker_id,
            Run.fencing_token == fencing_token,
        ).with_for_update()
    )
    if run is None:
        raise ConflictError("Run lease 已失效")
    now = datetime.now(timezone.utc)
    run.lease_expires_at = now + timedelta(seconds=lease_seconds)
    run.heartbeat_at = now
    db.commit()
    db.refresh(run)
    return run


def _converge_open_evidence(
    db: Session,
    run_id: uuid.UUID,
    *,
    request_status: str,
    finished_at: datetime,
    error_category: str,
    error_body: str,
) -> int:
    """Close evidence rows left open by a failed or cancelled Run.

    Request/Attempt rows are part of the Run's durable evidence.  A worker can
    lose its lease after inserting one of these rows but before writing the
    final Run state, so terminalizing only ``runs`` would leave misleading
    ``pending``/``running`` records forever.  The caller owns the transaction
    and commits the Run plus evidence atomically.
    """

    requests = list(
        db.scalars(
            select(Request)
            .where(
                Request.run_id == run_id,
                Request.status.in_(('pending', 'running')),
            )
            .with_for_update()
        )
    )
    for request in requests:
        request.status = request_status
        request.finished_at = request.finished_at or finished_at
        request.started_at = request.started_at or finished_at
        attempts = list(
            db.scalars(
                select(Attempt)
                .where(
                    Attempt.request_id == request.id,
                    Attempt.finished_at.is_(None),
                )
                .with_for_update()
            )
        )
        for attempt in attempts:
            attempt.finished_at = finished_at
            attempt.error_category = error_category
            attempt.error_body = error_body[:500]
    return len(requests)


def finish_run(
    db: Session,
    run_id: uuid.UUID,
    worker_id: str,
    fencing_token: int,
    status: str,
    error_summary: str | None = None,
) -> Run:
    """以 fencing token 写入 Run 终态，防止旧 worker 覆盖新 worker。"""
    if status not in {"completed", "partially_completed", "failed", "cancelled"}:
        raise ValueError("Run 终态必须为 completed、partially_completed、failed 或 cancelled")
    run = db.scalar(
        select(Run).where(
            Run.id == run_id,
            Run.status.in_((RunStatus.RUNNING, RunStatus.CANCELLING)),
            Run.lease_owner == worker_id,
            Run.fencing_token == fencing_token,
        ).with_for_update()
    )
    if run is None:
        raise ConflictError("Run lease 已失效")
    if run.status == "cancelling" and status != "cancelled":
        status = "cancelled"
        error_summary = error_summary or "Run 在执行过程中被取消"
    finished_at = datetime.now(timezone.utc)
    evidence_status = "cancelled" if status == "cancelled" else "failed"
    evidence_category = "cancelled" if status == "cancelled" else "run_finished_incomplete"
    evidence_error = (
        "Run 在执行过程中被取消"
        if status == "cancelled"
        else (error_summary or "Run 结束时仍有未完成请求")
    )
    open_count = _converge_open_evidence(
        db,
        run.id,
        request_status=evidence_status,
        finished_at=finished_at,
        error_category=evidence_category,
        error_body=evidence_error,
    )
    if status == "completed" and open_count:
        # A completed Run cannot contain open evidence. Preserve the evidence
        # and expose the inconsistency instead of reporting a false success.
        status = "partially_completed"
        error_summary = error_summary or f"Run 结束时有 {open_count} 条请求未完成"
    run.status = status
    run.error_summary = error_summary
    run.finished_at = finished_at
    run.lease_owner = None
    run.lease_expires_at = None
    db.commit()
    db.refresh(run)
    return run


def wait_for_code(
    db: Session,
    run_id: uuid.UUID,
    worker_id: str,
    fencing_token: int,
) -> Run:
    """将已完成 HTTP 阶段的 Run 转入代码评分等待态。

    该转换仍使用原 Run lease 做 fencing 校验，但转换后立即释放 lease；
    code worker 通过自己的作业 lease 推动最终收敛，普通 Worker 不会把
    ``awaiting_code`` 误回收成 ``interrupted_failed``。
    """

    run = db.scalar(
        select(Run).where(
            Run.id == run_id,
            Run.status.in_((RunStatus.RUNNING, RunStatus.CANCELLING)),
            Run.lease_owner == worker_id,
            Run.fencing_token == fencing_token,
        ).with_for_update()
    )
    if run is None:
        raise ConflictError("Run lease 已失效")
    run.status = "cancelling" if run.status == "cancelling" else "awaiting_code"
    run.lease_owner = None
    run.lease_expires_at = None
    db.commit()
    db.refresh(run)
    return run


def fail_expired_runs(db: Session, now: datetime | None = None) -> int:
    """把 lease 已过期的运行中 Run 收敛为中断失败。

    首版不自动重试性能 Run，避免改变真实失败率；人工重试会创建新 Run。
    """
    current = now or datetime.now(timezone.utc)
    expired = list(
        db.scalars(
            select(Run)
            .where(
                Run.status.in_((RunStatus.RUNNING, RunStatus.CANCELLING)),
                Run.lease_expires_at.isnot(None),
                Run.lease_expires_at < current,
            )
            .with_for_update(skip_locked=True)
        )
    )
    for run in expired:
        terminal_status = "cancelled" if run.status == "cancelling" else "failed"
        if run.status == "cancelling":
            # Cancellation is an explicit operator decision. If the worker
            # disappears after accepting it, do not leave the Run (and its
            # parent Task) stuck forever in a non-terminal state.
            run.status = "cancelled"
            run.error_summary = "worker lease expired while cancelling"
        else:
            run.status = "interrupted_failed"
            run.error_summary = "worker lease expired"
        run.finished_at = current
        run.lease_owner = None
        run.lease_expires_at = None
        # A dead worker may have committed a Request/Attempt before losing its
        # lease. Converge that evidence in the same transaction as the Run so
        # no request remains permanently pending/running after recovery.
        _converge_open_evidence(
            db,
            run.id,
            request_status=terminal_status,
            finished_at=current,
            error_category=(
                "cancelled" if terminal_status == "cancelled" else "worker_lease_expired"
            ),
            error_body=(
                "worker lease expired while cancelling"
                if terminal_status == "cancelled"
                else "worker lease expired"
            ),
        )
    task_ids = {run.task_id for run in expired if run.task_id is not None}
    if expired:
        db.commit()
        # Lease recovery changes the child Run state just like normal worker
        # completion.  Reconcile parent Tasks in the same recovery pass so a
        # crashed worker cannot leave a task stuck in ``running`` forever.
        from evalplat.services.task_lifecycle_service import sync_from_runs

        for task_id in task_ids:
            sync_from_runs(db, task_id)
    return len(expired)
