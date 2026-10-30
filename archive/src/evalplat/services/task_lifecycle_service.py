"""根据子 Run 状态维护 Task 生命周期。"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from evalplat.constants import RunStatus
from evalplat.models.run import Run
from evalplat.models.task import Task


TERMINAL_RUN_STATUSES = set(RunStatus.TERMINAL)
ACTIVE_RUN_STATUSES = set(RunStatus.ACTIVE)


def mark_running(db: Session, task_id: uuid.UUID | None) -> None:
    if task_id is None:
        return
    # The worker session deliberately keeps ORM objects alive across commits.
    # Lock and refresh the parent row so an operator's concurrent cancellation
    # cannot be overwritten by a stale identity-map value.
    task = db.scalar(
        select(Task)
        .where(Task.id == task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if task and task.status == "queued":
        task.status = "running"
        task.started_at = task.started_at or datetime.now(timezone.utc)
        db.commit()


def sync_from_runs(db: Session, task_id: uuid.UUID | None) -> Task | None:
    """在 Run 状态变化后重新计算父 Task 状态。"""
    if task_id is None:
        return None
    # Serialize lifecycle aggregation with task-level cancel/submit/retry
    # transitions and force a fresh read from the database.
    task = db.scalar(
        select(Task)
        .where(Task.id == task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if task is None:
        return None
    statuses = list(db.scalars(select(Run.status).where(Run.task_id == task_id)))
    if not statuses:
        return task
    status_set = set(statuses)
    if status_set <= {"completed"}:
        task.status = "completed"
    elif status_set <= {"failed", "interrupted_failed"}:
        task.status = "failed"
    elif status_set <= {"cancelled"}:
        task.status = "cancelled"
    elif all(status in TERMINAL_RUN_STATUSES for status in statuses):
        task.status = "partially_completed"
    elif task.status == "cancelling" and any(
        status not in TERMINAL_RUN_STATUSES for status in statuses
    ):
        # Preserve an explicit task-level cancellation request until every
        # child Run reaches a terminal state.
        task.status = "cancelling"
    elif "running" in status_set or "awaiting_code" in status_set:
        task.status = "running"
    elif status_set <= ACTIVE_RUN_STATUSES | {"cancelled", "draft"}:
        # A cancelled sibling does not make a still-queued Task look running.
        # This is common for parallel tasks where an operator cancels one Run
        # while the remaining Runs have not started yet.
        task.status = "queued"
    else:
        # Unknown or future non-terminal Run states must not make a Task look
        # completed. Keeping it running is the conservative lifecycle choice.
        task.status = "running"
    if all(status in TERMINAL_RUN_STATUSES for status in statuses):
        task.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(task)
    return task
