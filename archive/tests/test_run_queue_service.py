"""Run 队列状态和 fencing token 测试。"""

import uuid

import pytest

from evalplat.core.errors import ConflictError
from evalplat.models.run import Run
from evalplat.models.request import Request
from evalplat.models.attempt import Attempt
from evalplat.models.system_setting import SystemSetting
from evalplat.models.task import Task
from evalplat.services import run_queue_service
from evalplat.services.task_precheck_service import get_global_concurrency_limit


def _queued_run(db, queued_at):
    run = Run(task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), queued_at=queued_at)
    db.add(run)
    db.flush()
    return run


def test_claim_is_fifo_and_increments_fencing_token(db):
    from datetime import datetime, timezone

    later = _queued_run(db, datetime(2026, 1, 2, tzinfo=timezone.utc))
    first = _queued_run(db, datetime(2026, 1, 1, tzinfo=timezone.utc))
    db.commit()

    claimed = run_queue_service.claim_next_run(db, "worker-a")
    assert claimed.id == first.id
    assert claimed.status == "running"
    assert claimed.lease_owner == "worker-a"
    assert claimed.fencing_token == 1
    assert db.get(Run, later.id).status == "queued"


def test_finish_rejects_stale_worker_and_accepts_owner(db):
    run = _queued_run(db, None)
    db.commit()
    claimed = run_queue_service.claim_next_run(db, "worker-a")

    with pytest.raises(ConflictError):
        run_queue_service.finish_run(db, run.id, "worker-b", claimed.fencing_token, "completed")

    finished = run_queue_service.finish_run(db, run.id, "worker-a", claimed.fencing_token, "completed")
    assert finished.status == "completed"
    assert finished.finished_at is not None
    assert finished.lease_owner is None


def test_finish_converges_open_request_and_attempt_evidence(db):
    run = _queued_run(db, None)
    db.commit()
    claimed = run_queue_service.claim_next_run(db, "worker-a")
    request = Request(
        run_id=run.id,
        kind="perf",
        phase="steady",
        status="running",
    )
    db.add(request)
    db.flush()
    attempt = Attempt(
        request_id=request.id,
        attempt_index=1,
        started_at=claimed.started_at,
    )
    db.add(attempt)
    db.commit()

    finished = run_queue_service.finish_run(
        db, run.id, "worker-a", claimed.fencing_token, "completed"
    )

    db.refresh(request)
    db.refresh(attempt)
    assert finished.status == "partially_completed"
    assert request.status == "failed"
    assert attempt.finished_at is not None
    assert attempt.error_category == "run_finished_incomplete"


def test_expired_run_becomes_interrupted_failed(db):
    from datetime import datetime, timedelta, timezone

    run = Run(
        task_id=uuid.uuid4(),
        model_deployment_id=uuid.uuid4(),
        status="running",
        lease_owner="dead-worker",
        lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    db.add(run)
    db.commit()

    count = run_queue_service.fail_expired_runs(db)

    db.refresh(run)
    assert count == 1
    assert run.status == "interrupted_failed"
    assert run.lease_owner is None
    assert run.finished_at is not None


def test_expired_run_converges_open_evidence(db):
    from datetime import datetime, timedelta, timezone

    run = Run(
        task_id=uuid.uuid4(),
        model_deployment_id=uuid.uuid4(),
        status="running",
        lease_owner="dead-worker",
        lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    db.add(run)
    db.flush()
    request = Request(run_id=run.id, kind="quality", status="pending")
    db.add(request)
    db.flush()
    attempt = Attempt(request_id=request.id, attempt_index=1)
    db.add(attempt)
    db.commit()

    run_queue_service.fail_expired_runs(db)

    db.refresh(request)
    db.refresh(attempt)
    assert request.status == "failed"
    assert attempt.error_category == "worker_lease_expired"
    assert attempt.finished_at is not None


def test_expired_cancelling_run_becomes_cancelled_and_reconciles_task(db):
    from datetime import datetime, timedelta, timezone

    task = Task(name="取消中的任务", run_mode="serial", status="cancelling")
    db.add(task)
    db.flush()
    run = Run(
        task_id=task.id,
        model_deployment_id=uuid.uuid4(),
        status="cancelling",
        lease_owner="dead-worker",
        lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    db.add(run)
    db.commit()

    assert run_queue_service.fail_expired_runs(db) == 1

    db.refresh(run)
    db.refresh(task)
    assert run.status == "cancelled"
    assert run.error_summary == "worker lease expired while cancelling"
    assert task.status == "cancelled"


def test_claim_respects_global_concurrency_limit(db):
    running = Run(task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="running", lease_owner="w")
    queued = Run(task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="queued")
    db.add_all([running, queued])
    db.commit()

    assert run_queue_service.claim_next_run(db, "other", max_concurrency=1) is None
    db.refresh(queued)
    assert queued.status == "queued"


def test_claim_counts_cancelling_run_against_global_concurrency_limit(db):
    cancelling = Run(
        task_id=uuid.uuid4(),
        model_deployment_id=uuid.uuid4(),
        status="cancelling",
        lease_owner="w",
    )
    queued = Run(
        task_id=uuid.uuid4(),
        model_deployment_id=uuid.uuid4(),
        status="queued",
    )
    db.add_all([cancelling, queued])
    db.commit()

    assert run_queue_service.claim_next_run(db, "other", max_concurrency=1) is None
    db.refresh(queued)
    assert queued.status == "queued"


def test_claim_counts_awaiting_code_run_against_global_concurrency_limit(db):
    awaiting_code = Run(
        task_id=uuid.uuid4(),
        model_deployment_id=uuid.uuid4(),
        status="awaiting_code",
    )
    queued = Run(
        task_id=uuid.uuid4(),
        model_deployment_id=uuid.uuid4(),
        status="queued",
    )
    db.add_all([awaiting_code, queued])
    db.commit()

    assert run_queue_service.claim_next_run(db, "other", max_concurrency=1) is None
    db.refresh(queued)
    assert queued.status == "queued"


def test_global_concurrency_limit_prefers_system_setting(db, monkeypatch):
    db.add(SystemSetting(key="global_max_parallel_runs", value={"value": 2}))
    db.commit()
    monkeypatch.setattr("evalplat.services.task_precheck_service.get_settings", lambda: type("S", (), {"worker_max_concurrency": 9})())
    assert get_global_concurrency_limit(db) == 2


def test_serial_task_never_claims_two_active_runs(db):
    task = Task(name="串行", run_mode="serial", status="queued")
    db.add(task)
    db.flush()
    active = Run(task_id=task.id, model_deployment_id=uuid.uuid4(), status="running", lease_owner="w")
    queued = Run(task_id=task.id, model_deployment_id=uuid.uuid4(), status="queued")
    db.add_all([active, queued])
    db.commit()

    assert run_queue_service.claim_next_run(db, "other") is None
    db.refresh(queued)
    assert queued.status == "queued"


def test_serial_queue_head_blocks_later_task_in_strict_fifo(db):
    from datetime import datetime, timezone

    serial_task = Task(name="串行队列头", run_mode="serial", status="running")
    later_task = Task(name="后续任务", run_mode="parallel", status="queued")
    db.add_all([serial_task, later_task])
    db.flush()
    active = Run(
        task_id=serial_task.id,
        model_deployment_id=uuid.uuid4(),
        status="running",
        lease_owner="worker-a",
    )
    head = Run(
        task_id=serial_task.id,
        model_deployment_id=uuid.uuid4(),
        status="queued",
        queued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    later = Run(
        task_id=later_task.id,
        model_deployment_id=uuid.uuid4(),
        status="queued",
        queued_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    db.add_all([active, head, later])
    db.commit()

    assert run_queue_service.claim_next_run(db, "worker-b") is None
    db.refresh(head)
    db.refresh(later)
    assert head.status == "queued"
    assert later.status == "queued"
