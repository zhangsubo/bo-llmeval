"""代码执行队列和独立 code worker 的契约测试。"""

import uuid

from evalplat.models.code_execution import CodeExecution
from evalplat.models.attempt import Attempt
from evalplat.models.request import Request
from evalplat.models.run import Run
from evalplat.models.task import Task
from evalplat.services.code_execution_service import enqueue_code_execution, claim_next_code_execution
from evalplat.services.code_worker import process_one
from evalplat.services.sandbox_service import DEFAULT_SANDBOX_IMAGE
from evalplat.services.run_worker import process_one as process_run
from evalplat.services.task_service import cancel_task


def test_code_execution_is_enqueued_from_completed_request(db):
    run = Run(
        task_id=uuid.uuid4(),
        model_deployment_id=uuid.uuid4(),
        status="completed",
        run_snapshot={"quality_mode": "code"},
    )
    db.add(run)
    db.flush()
    request = Request(
        id=1,
        run_id=run.id,
        sample_index=3,
        kind="quality",
        status="completed",
        input_messages={"messages": [{"role": "user", "content": "solve"}]},
    )
    db.add(request)
    db.commit()

    created = enqueue_code_execution(
        db,
        run,
        request,
        code="def add(a, b): return a + b",
        tests="assert add(1, 2) == 3",
        sample_index=3,
    )

    assert created.status == "pending"
    assert created.run_id == run.id
    assert created.request_id == request.id
    assert created.tests == "assert add(1, 2) == 3"


def test_code_execution_api_redacts_credentials(client, db):
    secret = "sk-code-worker-secret-12345678"
    run = Run(task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed")
    db.add(run)
    db.flush()
    execution = CodeExecution(
        run_id=run.id,
        code=f"print('{secret}')",
        tests=f"assert '{secret}'",
        stdout=f"Bearer {secret}",
        test_results={"actual": secret},
        error_summary=f"failed with {secret}",
    )
    db.add(execution)
    db.commit()

    response = client.get(f"/api/v1/code-executions/{execution.id}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert secret not in response.text
    assert body["test_results"]["actual"] == "sk-c****5678"


def test_code_worker_executes_and_persists_result(db):
    run = Run(task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed")
    db.add(run)
    db.flush()
    request = Request(id=1, run_id=run.id, kind="quality", status="completed")
    db.add(request)
    db.flush()
    execution = CodeExecution(
        run_id=run.id,
        request_id=request.id,
        sample_index=0,
        code="def add(a, b): return a + b",
        tests="assert add(1, 2) == 3",
    )
    db.add(execution)
    db.commit()

    def sandbox(**kwargs):
        assert kwargs["tests"] == "assert add(1, 2) == 3"
        return {
            "status": "passed",
            "compile_status": "passed",
            "run_status": "passed",
            "exit_code": 0,
            "stdout": "ok",
            "stderr": "",
            "test_results": {"passed": 1, "failed": 0},
        }

    result = process_one(db, "code-worker-a", sandbox_runner=sandbox)

    db.refresh(execution)
    assert result["status"] == "passed"
    assert execution.status == "passed"
    assert execution.compile_status == "passed"
    assert execution.run_status == "passed"
    assert execution.test_results == {"passed": 1, "failed": 0}
    assert execution.stdout == "ok"


def test_code_worker_maps_timeout_to_terminal_timeout(db):
    run = Run(task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed")
    db.add(run)
    db.flush()
    execution = CodeExecution(
        run_id=run.id,
        code="while True: pass",
        tests="",
    )
    db.add(execution)
    db.commit()

    result = process_one(
        db,
        "code-worker-a",
        sandbox_runner=lambda **_kwargs: {
            "status": "timeout",
            "stdout": "",
            "stderr": "timed out",
        },
    )

    db.refresh(execution)
    assert result["status"] == "timeout"
    assert execution.status == "timeout"
    assert execution.error_category == "timeout"


def test_code_worker_normalizes_invalid_phase_statuses(db):
    run = Run(task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed")
    db.add(run)
    db.flush()
    execution = CodeExecution(run_id=run.id, code="pass", tests="")
    db.add(execution)
    db.commit()

    result = process_one(
        db,
        "code-worker-a",
        sandbox_runner=lambda **_kwargs: {
            "status": "passed",
            "compile_status": "compiler-crashed",
            "run_status": "passed",
        },
    )

    db.refresh(execution)
    assert result["status"] == "failed"
    assert execution.status == "failed"
    assert execution.compile_status == "not_run"
    assert execution.run_status == "passed"
    assert execution.error_category == "invalid_sandbox_result"
    assert execution.lease_owner is None


def test_code_execution_lease_covers_sandbox_timeout(db):
    from datetime import datetime, timezone

    run = Run(task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed")
    db.add(run)
    db.flush()
    execution = CodeExecution(
        run_id=run.id,
        code="pass",
        tests="",
        timeout_seconds=300,
    )
    db.add(execution)
    db.commit()

    claimed = claim_next_code_execution(db, "code-worker-a", lease_seconds=60)

    assert claimed is not None
    expires = claimed.lease_expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    remaining = (expires - datetime.now(timezone.utc)).total_seconds()
    assert remaining >= 320


def test_code_worker_rejects_legacy_image_when_immutable_policy_enabled(db, monkeypatch):
    run = Run(task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed")
    db.add(run)
    db.flush()
    execution = CodeExecution(
        run_id=run.id,
        code="print('should not run')",
        tests="",
        container_image="python:3.11-slim",
    )
    db.add(execution)
    db.commit()
    monkeypatch.setattr(
        "evalplat.services.code_worker.get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "sandbox_image": DEFAULT_SANDBOX_IMAGE,
                "sandbox_require_immutable_image": True,
            },
        )(),
    )
    called = False

    def sandbox(**_kwargs):
        nonlocal called
        called = True
        return {"status": "passed"}

    result = process_one(db, "code-worker-a", sandbox_runner=sandbox)

    db.refresh(execution)
    assert result["status"] == "failed"
    assert execution.status == "failed"
    assert execution.error_category == "sandbox_policy"
    assert called is False


def test_normal_worker_only_enqueues_code_job(db, monkeypatch):
    run = Run(
        task_id=uuid.uuid4(),
        model_deployment_id=uuid.uuid4(),
        status="queued",
        run_snapshot={
            "evaluation_kind": "quality",
            "quality_mode": "code",
            "quality_samples": [
                {
                    "messages": [{"role": "user", "content": "solve"}],
                    "tests": "assert add(1, 2) == 3",
                }
            ],
        },
    )
    db.add(run)
    db.commit()

    def fake_quality_samples(db, run, samples, *_args, **_kwargs):
        request = Request(
            id=1,
            run_id=run.id,
            sample_index=0,
            kind="quality",
            status="completed",
            input_messages={"messages": samples[0]["messages"]},
        )
        db.add(request)
        db.flush()
        db.add(Attempt(id=1, request_id=request.id, attempt_index=1, extracted_fields={"content": "def add(a, b): return a + b"}))
        db.commit()
        return [{"status": "completed", "request_id": request.id}]

    monkeypatch.setattr("evalplat.services.run_worker.execute_quality_samples", fake_quality_samples)
    result = process_run(db, "worker-a", object(), object())

    job = db.query(CodeExecution).filter(CodeExecution.run_id == run.id).one()
    db.refresh(run)
    assert result["status"] == "awaiting_code"
    assert run.status == "awaiting_code"
    assert job.status == "pending"
    assert job.code == "def add(a, b): return a + b"
    assert job.tests == "assert add(1, 2) == 3"
    assert job.sample_index == 0


def test_normal_worker_strips_markdown_code_fence_before_enqueue(db, monkeypatch):
    run = Run(
        task_id=uuid.uuid4(),
        model_deployment_id=uuid.uuid4(),
        status="queued",
        run_snapshot={
            "evaluation_kind": "quality",
            "quality_mode": "code",
            "quality_samples": [{"messages": [{"role": "user", "content": "solve"}]}],
        },
    )
    db.add(run)
    db.commit()

    def fake_quality_samples(db, run, samples, *_args, **_kwargs):
        request = Request(
            id=2,
            run_id=run.id,
            sample_index=0,
            kind="quality",
            status="completed",
            input_messages={"messages": samples[0]["messages"]},
        )
        db.add(request)
        db.flush()
        db.add(Attempt(
            id=2,
            request_id=request.id,
            attempt_index=1,
            extracted_fields={"content": "```python\ndef add(a, b): return a + b\n```"},
        ))
        db.commit()
        return [{"status": "completed", "request_id": request.id}]

    monkeypatch.setattr("evalplat.services.run_worker.execute_quality_samples", fake_quality_samples)
    result = process_run(db, "worker-a", object(), object())

    job = db.query(CodeExecution).filter(CodeExecution.run_id == run.id).one()
    db.refresh(run)
    assert result["status"] == "awaiting_code"
    assert run.status == "awaiting_code"
    assert job.code == "def add(a, b): return a + b"


def _awaiting_code_run(db, *, execution_count: int = 1):
    task = Task(name="代码任务", run_mode="serial", status="running")
    db.add(task)
    db.flush()
    run = Run(
        task_id=task.id,
        model_deployment_id=uuid.uuid4(),
        status="awaiting_code",
        run_snapshot={"quality_mode": "code"},
    )
    db.add(run)
    db.flush()
    request = Request(
        id=100,
        run_id=run.id,
        sample_index=0,
        kind="quality",
        status="completed",
    )
    db.add(request)
    db.flush()
    for index in range(execution_count):
        db.add(CodeExecution(
            run_id=run.id,
            request_id=request.id if index == 0 else None,
            sample_index=index,
            code="def add(a, b): return a + b",
            tests="assert add(1, 2) == 3",
        ))
    db.commit()
    return task, run


def test_last_code_job_finalizes_run_and_task(db):
    task, run = _awaiting_code_run(db)

    result = process_one(
        db,
        "code-worker-a",
        sandbox_runner=lambda **_kwargs: {
            "status": "passed",
            "compile_status": "passed",
            "run_status": "passed",
        },
    )

    db.refresh(run)
    db.refresh(task)
    assert result["status"] == "passed"
    assert run.status == "completed"
    assert task.status == "completed"


def test_code_test_failure_is_a_completed_evaluation(db):
    _task, run = _awaiting_code_run(db)

    process_one(
        db,
        "code-worker-a",
        sandbox_runner=lambda **_kwargs: {
            "status": "failed",
            "compile_status": "passed",
            "run_status": "failed",
            "error_category": "test_failure",
        },
    )

    db.refresh(run)
    assert run.status == "completed"


def test_code_worker_preserves_duration_budget_truncation(db):
    _task, run = _awaiting_code_run(db)
    run.error_summary = "Run runtime budget exceeded"
    db.commit()

    process_one(
        db,
        "code-worker-a",
        sandbox_runner=lambda **_kwargs: {
            "status": "passed",
            "compile_status": "passed",
            "run_status": "passed",
        },
    )

    db.refresh(run)
    assert run.status == "partially_completed"
    assert run.error_summary == "Run runtime budget exceeded"


def test_run_waits_for_all_code_jobs(db):
    _task, run = _awaiting_code_run(db, execution_count=2)
    runner = lambda **_kwargs: {"status": "passed", "compile_status": "passed", "run_status": "passed"}

    process_one(db, "code-worker-a", sandbox_runner=runner)
    db.refresh(run)
    assert run.status == "awaiting_code"

    process_one(db, "code-worker-a", sandbox_runner=runner)
    db.refresh(run)
    assert run.status == "completed"


def test_cancelling_task_cancels_pending_code_jobs(db):
    task, run = _awaiting_code_run(db)
    job = db.query(CodeExecution).filter(CodeExecution.run_id == run.id).one()

    cancelled = cancel_task(db, task.id)

    db.refresh(run)
    db.refresh(job)
    assert cancelled.status == "cancelled"
    assert run.status == "cancelled"
    assert job.status == "cancelled"
