"""父 Task 状态聚合测试。"""

import uuid

from evalplat.models.run import Run
from evalplat.models.task import Task
from evalplat.services.task_lifecycle_service import mark_running, sync_from_runs


def _task_with_runs(db, statuses):
    task = Task(name="任务", run_mode="parallel", status="queued")
    db.add(task)
    db.flush()
    for index, status in enumerate(statuses, start=1):
        db.add(Run(task_id=task.id, model_deployment_id=uuid.uuid4(), seq=index, status=status))
    db.commit()
    return task


def test_mark_running_sets_parent_start_time(db):
    task = _task_with_runs(db, ["running", "queued"])
    mark_running(db, task.id)
    db.refresh(task)
    assert task.status == "running"
    assert task.started_at is not None


def test_all_completed_completes_task(db):
    task = _task_with_runs(db, ["completed", "completed"])
    synced = sync_from_runs(db, task.id)
    assert synced.status == "completed"
    assert synced.finished_at is not None


def test_mixed_terminal_runs_partially_complete_task(db):
    task = _task_with_runs(db, ["completed", "failed"])
    synced = sync_from_runs(db, task.id)
    assert synced.status == "partially_completed"


def test_awaiting_code_run_keeps_task_running(db):
    task = _task_with_runs(db, ["awaiting_code"])
    synced = sync_from_runs(db, task.id)
    assert synced.status == "running"
    assert synced.finished_at is None


def test_cancelled_sibling_does_not_make_queued_task_running(db):
    task = _task_with_runs(db, ["cancelled", "queued"])

    synced = sync_from_runs(db, task.id)

    assert synced.status == "queued"


def test_lifecycle_refreshes_parent_after_external_cancellation(db):
    """A long-lived worker session must not resurrect a cancelled Task."""

    from tests.conftest import _TestSessionLocal

    task = _task_with_runs(db, ["queued"])
    operator = _TestSessionLocal()
    try:
        # Prime the worker identity map with the pre-cancel state.
        assert db.get(Task, task.id).status == "queued"
        external = operator.get(Task, task.id)
        external.status = "cancelling"
        operator.commit()

        refreshed = sync_from_runs(db, task.id)
        assert refreshed is not None
        assert refreshed.status == "cancelling"
    finally:
        operator.close()
