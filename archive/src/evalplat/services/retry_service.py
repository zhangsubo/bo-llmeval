"""人工重试和历史任务复测。"""

from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone

from sqlalchemy import String, func, select
from sqlalchemy.orm import Session

from evalplat.core.errors import ConflictError, NotFoundError
from evalplat.models.connection import Connection
from evalplat.models.model_deployment import ModelDeployment
from evalplat.models.model_price import ModelPrice
from evalplat.models.run import Run
from evalplat.models.task import Task
from evalplat.services.task_service import build_run_snapshot
from evalplat.services.audit_service import record_event


RETRYABLE_RUN_STATUSES = {"failed", "interrupted_failed", "partially_completed"}


def retry_run(db: Session, run_id: uuid.UUID) -> Run:
    """复制一个失败 Run 的配置并创建新的 queued Run。"""

    # Lock the parent Task first. Submit/cancel/report/cleanup all use this
    # order, so retry cannot race with a task-level lifecycle transition.
    source = db.get(Run, run_id)
    if source is None:
        raise NotFoundError("Run 不存在")
    task = db.scalar(select(Task).where(Task.id == source.task_id).with_for_update()) if source.task_id else None
    source = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if source is None:
        raise NotFoundError("Run 不存在")
    if source.status not in RETRYABLE_RUN_STATUSES:
        raise ConflictError("只有失败或部分完成的 Run 可以人工重试")
    if source.task_id is None or source.model_deployment_id is None:
        raise ConflictError("Run 缺少任务或模型目标，无法重试")
    # The source row lock serializes concurrent retries. Returning the
    # existing child also makes repeated requests safe after a client timeout.
    existing = db.scalar(
        select(Run)
        .where(Run.retry_of_run_id == source.id)
        .order_by(Run.created_at.desc(), Run.id.desc())
        .limit(1)
    )
    if existing is not None:
        return existing
    deployment = db.scalar(
        select(ModelDeployment)
        .where(ModelDeployment.id == source.model_deployment_id)
        .with_for_update()
    )
    connection = (
        db.scalar(
            select(Connection)
            .where(Connection.id == deployment.connection_id)
            .with_for_update()
        )
        if deployment
        else None
    )
    if not task or not deployment or deployment.status != "active" or not connection or connection.status != "active":
        raise ConflictError("当前模型部署或连接不可用，无法重试")
    if not connection.api_key_ciphertext:
        raise ConflictError("当前连接未配置 API Key，无法重试")

    # Preserve the source Run's effective task policy.  ``build_run_snapshot``
    # refreshes current connection identity, credentials, TLS and price while
    # clamping these values against the current connection hard limits.  The
    # retry must not silently widen a stricter policy from the original run.
    snapshot = copy.deepcopy(source.run_snapshot or {})
    # Rebuild all deployment, resource and price metadata through the same
    # snapshot boundary used by initial submission. Runtime connection values
    # are intentionally refreshed for a manual retry; the source Run remains
    # immutable and is still available for audit/comparison.
    snapshot = build_run_snapshot(db, snapshot, deployment, connection)
    seq = db.scalar(select(func.max(Run.seq)).where(Run.task_id == task.id)) or 0
    now = datetime.now(timezone.utc)
    retried = Run(task_id=task.id, model_deployment_id=deployment.id, retry_of_run_id=source.id, seq=seq + 1, status="queued", run_snapshot=snapshot, queued_at=now)
    db.add(retried)
    task.status = "queued"
    task.queued_at = now
    task.started_at = None
    task.finished_at = None
    db.flush()
    record_event(db, "run_retried", target_type="run", target_id=source.id, diff={"source_run_id": str(source.id), "new_run_id": str(retried.id)})
    db.commit()
    db.refresh(retried)
    return retried


def _diff_value(old: object, new: object) -> dict[str, object] | None:
    return None if old == new else {"old": old, "new": new}


def _retest_diff(db: Session, task: Task) -> list[dict[str, object]]:
    config = task.config_snapshot or {}
    diffs: list[dict[str, object]] = []
    for raw_id in config.get("model_deployment_ids", []):
        try:
            deployment_id = uuid.UUID(str(raw_id))
        except (ValueError, TypeError):
            continue
        deployment = db.get(ModelDeployment, deployment_id)
        connection = db.get(Connection, deployment.connection_id) if deployment else None
        old_snapshot = next(
            (run.run_snapshot or {} for run in db.scalars(select(Run).where(Run.task_id == task.id, Run.model_deployment_id == deployment_id).order_by(Run.seq.desc()))),
            {},
        )
        if not deployment or not connection:
            diffs.append({"deployment_id": str(deployment_id), "field": "availability", "old": "active", "new": "missing"})
            continue
        for field, old, new in (
            ("base_url", old_snapshot.get("base_url"), connection.base_url),
            ("tls_skip_verify", old_snapshot.get("tls_skip_verify"), connection.tls_skip_verify),
            ("credential_version", old_snapshot.get("credential_version"), connection.credential_version),
        ):
            change = _diff_value(old, new)
            if change:
                diffs.append({"deployment_id": str(deployment_id), "field": field, **change})
        latest_price = db.scalar(select(ModelPrice).where(ModelPrice.model_deployment_id == deployment_id).order_by(ModelPrice.price_version.desc()).limit(1))
        change = _diff_value(old_snapshot.get("price_version"), latest_price.price_version if latest_price else None)
        if change:
            diffs.append({"deployment_id": str(deployment_id), "field": "price_version", **change})
    return diffs


def retest_task(
    db: Session,
    task_id: uuid.UUID,
    *,
    idempotency_key: str | None = None,
) -> tuple[Task, list[dict[str, object]]]:
    """从历史任务创建新草稿，保持原配置版本并返回当前差异。"""

    source = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if source is None:
        raise NotFoundError("任务不存在")
    if source.status not in {"completed", "partially_completed", "failed", "cancelled"}:
        raise ConflictError("只有终态任务可以复测")
    if idempotency_key is not None:
        idempotency_key = idempotency_key.strip()
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("Idempotency-Key 不能为空且长度不能超过 128")
        existing = db.scalar(
            select(Task).where(Task.retest_idempotency_key == idempotency_key)
        )
        if existing is not None:
            source_marker = str((existing.config_snapshot or {}).get("source_task_id") or "")
            if source_marker != str(source.id):
                raise ConflictError("Idempotency-Key 已用于其他复测任务")
            return existing, _retest_diff(db, source)

    # Repeated clicks without a header should still not produce a pile of
    # identical drafts while the first one remains editable.
    existing_draft = db.scalar(
        select(Task)
        .where(
            Task.status == "draft",
            Task.config_snapshot.cast(String).contains(f'"source_task_id": "{source.id}"'),
        )
        .order_by(Task.created_at.asc(), Task.id.asc())
    )
    if existing_draft is not None:
        return existing_draft, _retest_diff(db, source)
    config = copy.deepcopy(source.config_snapshot or {})
    config["source_task_id"] = str(source.id)
    new_task = Task(
        name=f"{source.name or '任务'} (复测)",
        template_version_id=source.template_version_id,
        run_mode=source.run_mode,
        status="draft",
        config_snapshot=config,
        retest_idempotency_key=idempotency_key,
        remark=source.remark,
    )
    diff = _retest_diff(db, source)
    db.add(new_task)
    db.flush()
    record_event(db, "task_retested", target_type="task", target_id=source.id, diff={"new_task_id": str(new_task.id), "changes": diff})
    db.commit()
    db.refresh(new_task)
    return new_task, diff
