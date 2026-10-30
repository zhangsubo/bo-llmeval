"""Run worker 编排行为测试。"""

import uuid
import threading
import time
from datetime import datetime, timezone

from cryptography.fernet import Fernet

from evalplat.core.crypto import CryptoService
from evalplat.core.ssrf import SSRFGuard
from evalplat.models.attempt import Attempt
from evalplat.models.connection import Connection
from evalplat.models.code_execution import CodeExecution
from evalplat.models.run import Run
from evalplat.models.task import Task
from evalplat.models.dataset import Dataset, DatasetVersion
from evalplat.models.request import Request
from evalplat.models.supplier import Supplier
from evalplat.services.run_worker import process_one


class _Unused:
    pass


def test_executor_setup_failure_marks_run_failed(db):
    run = Run(
        task_id=uuid.uuid4(),
        model_deployment_id=uuid.uuid4(),
        status="queued",
        run_snapshot={"messages": [{"role": "user", "content": "ping"}]},
    )
    db.add(run)
    db.commit()

    def failing_executor(*_args, **_kwargs):
        raise ValueError("快照无效")

    result = process_one(db, "worker-a", _Unused(), _Unused(), executor=failing_executor)

    db.refresh(run)
    assert result["status"] == "failed"
    assert run.status == "failed"
    assert run.error_summary == "快照无效"


def test_cancelling_task_stops_before_executor(db):
    task = Task(name="任务", run_mode="serial", status="cancelling")
    db.add(task)
    db.flush()
    run = Run(task_id=task.id, model_deployment_id=uuid.uuid4(), status="queued")
    db.add(run)
    db.commit()
    called = False

    def executor(*_args, **_kwargs):
        nonlocal called
        called = True
        return {"status": "completed"}

    result = process_one(db, "worker-a", _Unused(), _Unused(), executor=executor)

    db.refresh(run)
    assert called is False
    assert result["status"] == "cancelled"
    assert run.status == "cancelled"


def test_worker_exception_is_recovered_to_failed_run(db, monkeypatch):
    """An exception outside the executor must not leave the claimed Run running."""

    task = Task(name="异常收敛", run_mode="serial", status="queued")
    db.add(task)
    db.flush()
    run = Run(task_id=task.id, status="queued", queued_at=datetime.now(timezone.utc))
    db.add(run)
    db.commit()

    def explode(*_args, **_kwargs):
        raise RuntimeError("orchestration failed")

    monkeypatch.setattr("evalplat.services.run_worker.mark_running", explode)

    result = process_one(db, "worker-a", _Unused(), _Unused())

    db.refresh(run)
    assert result["status"] == "failed"
    assert run.status == "failed"
    assert "orchestration failed" in (run.error_summary or "")


def test_cancelled_task_stops_before_executor(db):
    task = Task(name="已取消任务", run_mode="serial", status="cancelled")
    db.add(task)
    db.flush()
    run = Run(task_id=task.id, model_deployment_id=uuid.uuid4(), status="queued")
    db.add(run)
    db.commit()

    called = False

    def executor(*_args, **_kwargs):
        nonlocal called
        called = True
        return {"status": "completed"}

    result = process_one(db, "worker-a", _Unused(), _Unused(), executor=executor)

    assert called is False
    assert result["status"] == "cancelled"
    db.refresh(run)
    assert run.status == "cancelled"


def test_worker_cancel_check_reads_external_transaction_state(db):
    """Cancellation must be visible despite the worker session identity map."""
    from tests.conftest import _TestSessionLocal
    from evalplat.services.run_worker import _cancel_requested

    task = Task(name="外部取消任务", run_mode="serial", status="queued")
    db.add(task)
    db.flush()
    run = Run(task_id=task.id, model_deployment_id=uuid.uuid4(), status="running")
    db.add(run)
    db.commit()

    # Prime the worker session's identity map with the pre-cancel state.
    assert _cancel_requested(db, run.id) is False
    operator = _TestSessionLocal()
    try:
        operator.get(Task, task.id).status = "cancelling"
        operator.commit()
    finally:
        operator.close()

    assert _cancel_requested(db, run.id) is True


def test_worker_consumes_request_schedule(db):
    run = Run(
        task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="queued",
        run_snapshot={"request_count": 3, "load_profile": {"mode": "fixed_rps", "rps": 1000000}},
    )
    db.add(run)
    db.commit()
    calls = 0

    def executor(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {"status": "completed"}

    result = process_one(db, "worker-a", _Unused(), _Unused(), executor=executor)

    assert calls == 3
    assert result["status"] == "completed"


def test_worker_assigns_cold_start_warmup_and_steady_phases(db):
    run = Run(
        task_id=uuid.uuid4(),
        model_deployment_id=uuid.uuid4(),
        status="queued",
        run_snapshot={"request_count": 2, "warmup_count": 2},
    )
    db.add(run)
    db.commit()
    phases = []

    def executor(*_args, **kwargs):
        phases.append(kwargs["phase"])
        return {"status": "completed"}

    result = process_one(db, "worker-a", _Unused(), _Unused(), executor=executor)

    assert result["status"] == "completed"
    assert phases == ["cold_start", "warmup", "warmup", "steady", "steady"]


def test_duration_budget_marks_truncated_performance_run_partial(db):
    run = Run(
        task_id=uuid.uuid4(),
        model_deployment_id=uuid.uuid4(),
        status="queued",
        run_snapshot={
            "request_count": 2,
            "limits": {"max_duration_seconds": 0.001},
            "load_profile": {"mode": "fixed_concurrency", "concurrency": 1},
        },
    )
    db.add(run)
    db.commit()

    def executor(*_args, **_kwargs):
        time.sleep(0.01)
        return {"status": "completed"}

    result = process_one(db, "worker-a", _Unused(), _Unused(), executor=executor)

    db.refresh(run)
    assert result["status"] == "partially_completed"
    assert run.status == "partially_completed"
    assert run.error_summary == "Run runtime budget exceeded"


def test_worker_uses_independent_sessions_for_concurrent_requests(db):
    run = Run(
        task_id=uuid.uuid4(),
        model_deployment_id=uuid.uuid4(),
        status="queued",
        run_snapshot={
            "request_count": 4,
            "load_profile": {"mode": "fixed_concurrency", "concurrency": 2},
        },
    )
    db.add(run)
    db.commit()
    lock = threading.Lock()
    active = 0
    peak = 0

    class SessionStub:
        def get(self, model, run_id):
            return run if model is Run and run_id == run.id else None

        def rollback(self):
            pass

        def close(self):
            pass

    def executor(*_args, **_kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        return {"status": "completed"}

    result = process_one(
        db,
        "worker-a",
        _Unused(),
        _Unused(),
        executor=executor,
        session_factory=SessionStub,
    )

    assert result["status"] == "completed"
    assert peak == 2


def test_fixed_rps_records_schedule_drops_at_inflight_limit(db):
    run = Run(
        task_id=uuid.uuid4(),
        model_deployment_id=uuid.uuid4(),
        status="queued",
        run_snapshot={
            "request_count": 3,
            "load_profile": {"mode": "fixed_rps", "rps": 1_000_000, "max_inflight": 1},
        },
    )
    db.add(run)
    db.commit()
    calls = 0

    class SessionStub:
        def get(self, model, run_id):
            return run if model is Run and run_id == run.id else None

        def rollback(self):
            pass

        def close(self):
            pass

    def executor(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        time.sleep(0.04)
        return {"status": "completed"}

    result = process_one(
        db,
        "worker-a",
        _Unused(),
        _Unused(),
        executor=executor,
        session_factory=SessionStub,
    )

    assert result["status"] == "partially_completed"
    dropped = db.query(Attempt).filter(Attempt.error_category == "schedule_dropped").count()
    assert calls >= 1
    assert dropped >= 1
    assert calls + dropped == 3


def test_quality_dataset_load_failure_finishes_run_as_failed(db, tmp_path, monkeypatch):
    dataset = Dataset(name="质量集", kind="quality")
    db.add(dataset)
    db.flush()
    version = DatasetVersion(
        dataset_id=dataset.id,
        version=1,
        status="ready",
        storage_path=str(tmp_path / "missing.jsonl"),
    )
    db.add(version)
    db.flush()
    run = Run(
        task_id=uuid.uuid4(),
        model_deployment_id=uuid.uuid4(),
        status="queued",
        run_snapshot={
            "evaluation_kind": "quality",
            "dataset_version_id": str(version.id),
        },
    )
    db.add(run)
    db.commit()
    monkeypatch.setattr("evalplat.services.run_worker.get_settings", lambda: type("S", (), {"dataset_dir": str(tmp_path)})())

    result = process_one(db, "worker-a", _Unused(), _Unused())

    db.refresh(run)
    assert result["status"] == "failed"
    assert run.status == "failed"


def test_quality_cancellation_after_successful_sample_does_not_complete_run(db, monkeypatch):
    """A cancelled quality prefix must not be mistaken for a full benchmark."""

    task = Task(name="质量取消", run_mode="serial", status="queued")
    db.add(task)
    db.flush()
    run = Run(
        task_id=task.id,
        model_deployment_id=uuid.uuid4(),
        status="queued",
        run_snapshot={
            "evaluation_kind": "quality",
            "evaluation_engine": "native",
            "quality_mode": "text",
            "quality_samples": [
                {"messages": [{"role": "user", "content": "one"}]},
                {"messages": [{"role": "user", "content": "two"}]},
            ],
        },
    )
    db.add(run)
    db.commit()

    def stop_after_prefix(local_db, *_args, **_kwargs):
        local_db.get(Task, task.id).status = "cancelling"
        local_db.commit()
        return [{"status": "completed"}]

    monkeypatch.setattr(
        "evalplat.services.run_worker.execute_quality_samples",
        stop_after_prefix,
    )

    result = process_one(db, "worker-a", _Unused(), _Unused())

    db.refresh(run)
    assert result["status"] == "cancelled"
    assert run.status == "cancelled"


def _evalscope_run(db, crypto: CryptoService) -> Run:
    supplier = Supplier(name="EvalScope 供应商", short_name="evalscope", type="原厂")
    db.add(supplier)
    db.flush()
    connection = Connection(
        supplier_id=supplier.id,
        name="EvalScope 连接",
        base_url="https://api.example.com",
        api_key_ciphertext=crypto.encrypt("sk-evalscope-secret"),
    )
    db.add(connection)
    db.flush()
    run = Run(
        task_id=uuid.uuid4(),
        model_deployment_id=uuid.uuid4(),
        status="queued",
        run_snapshot={
            "evaluation_kind": "quality",
            "evaluation_engine": "evalscope",
            "connection_id": str(connection.id),
            "base_url": "https://api.example.com/frozen",
            "model_id": "demo-model",
            "evalscope_datasets": ["demo_dataset"],
        },
    )
    db.add(run)
    db.commit()
    return run


class _AllowGuard:
    def check_url(self, _url):
        return True, "OK"


def test_worker_executes_evalscope_and_persists_evidence(db, tmp_path, monkeypatch):
    crypto = CryptoService(Fernet.generate_key().decode())
    run = _evalscope_run(db, crypto)
    monkeypatch.setattr(
        "evalplat.services.run_worker.get_settings",
        lambda: type("S", (), {"evalscope_dir": str(tmp_path)})(),
    )
    seen = {}

    class FakeEngine:
        def run(self, config):
            seen.update(config)
            return {"accuracy": 1.0}

    result = process_one(
        db,
        "worker-a",
        crypto,
        _AllowGuard(),
        evalscope_engine=FakeEngine(),
    )

    db.refresh(run)
    request = db.query(Request).filter(Request.run_id == run.id).one()
    attempt = db.query(Attempt).filter(Attempt.request_id == request.id).one()
    assert result["status"] == "failed"
    assert run.status == "failed"
    assert request.status == "failed"
    assert attempt.extracted_fields == {"evalscope": {"accuracy": 1.0}}
    assert request.quality_result["mapping_status"] == "unmapped"
    assert attempt.error_category == "result_unmapped"
    assert "逐样本" in (attempt.error_body or "")
    assert seen["api_url"] == "https://api.example.com/frozen/v1"
    assert seen["api_key"] == "sk-evalscope-secret"


def test_worker_routes_evalscope_performance_and_maps_records(db, tmp_path, monkeypatch):
    crypto = CryptoService(Fernet.generate_key().decode())
    supplier = Supplier(name="性能供应商", short_name="perf-evalscope", type="other")
    db.add(supplier)
    db.flush()
    connection = Connection(
        supplier_id=supplier.id,
        name="性能连接",
        base_url="https://api.example.com",
        api_key_ciphertext=crypto.encrypt("sk-perf-secret"),
    )
    db.add(connection)
    db.flush()
    run = Run(
        task_id=uuid.uuid4(),
        model_deployment_id=uuid.uuid4(),
        status="queued",
        run_snapshot={
            "evaluation_kind": "performance",
            "evaluation_engine": "evalscope",
            "connection_id": str(connection.id),
            "base_url": "https://api.example.com/frozen",
            "model_id": "demo-model",
            "messages": [{"role": "user", "content": "hello"}],
            "request_count": 1,
        },
    )
    db.add(run)
    db.commit()
    monkeypatch.setattr(
        "evalplat.services.run_worker.get_settings",
        lambda: type("S", (), {"evalscope_dir": str(tmp_path)})(),
    )

    class FakeEngine:
        def run(self, config):
            assert config["model"] == "demo-model"
            return {
                "status": "completed",
                "engine": "evalscope",
                "kind": "performance",
                "records": [
                    {
                        "sample_index": 0,
                        "status": "completed",
                        "stream_mode": "nonstream",
                        "request_body": {"messages": [{"role": "user", "content": "hello"}]},
                        "latency_ms": 12.5,
                        "vendor_usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
                        "raw_response": "{\"choices\": []}",
                    }
                ],
            }

    result = process_one(db, "worker-a", crypto, _AllowGuard(), evalscope_engine=FakeEngine())

    db.refresh(run)
    request = db.query(Request).filter(Request.run_id == run.id).one()
    attempt = db.query(Attempt).filter(Attempt.request_id == request.id).one()
    assert result["status"] == "completed"
    assert run.status == "completed"
    assert request.vendor_total_tokens == 5
    assert float(attempt.latency_ms) == 12.5


def test_worker_maps_evalscope_sample_results_to_quality_evidence(db, tmp_path, monkeypatch):
    crypto = CryptoService(Fernet.generate_key().decode())
    run = _evalscope_run(db, crypto)
    monkeypatch.setattr(
        "evalplat.services.run_worker.get_settings",
        lambda: type("S", (), {"evalscope_dir": str(tmp_path)})(),
    )

    class FakeEngine:
        def run(self, _config):
            return {"results": [{"sample_id": "a", "score": 1.0}, {"sample_id": "b", "score": 0.0}], "accuracy": 0.5}

    result = process_one(db, "worker-a", crypto, _AllowGuard(), evalscope_engine=FakeEngine())

    requests = (
        db.query(Request)
        .filter(Request.run_id == run.id)
        .order_by(Request.sample_index)
        .all()
    )
    assert result["status"] == "completed"
    assert len(requests) == 2
    assert [request.sample_index for request in requests] == [0, 1]
    assert all(request.quality_result["mapping_status"] == "mapped" for request in requests)
    assert all(request.quality_result["sample_count"] == 1 for request in requests)
    assert [request.quality_result["sample"]["sample_index"] for request in requests] == [0, 1]


def test_worker_converges_sandbox_policy_rejection_to_failed_run(db, monkeypatch):
    run = Run(
        status="queued",
        task_id=uuid.uuid4(),
        run_snapshot={
            "evaluation_kind": "quality",
            "quality_mode": "code",
            "code_sandbox_image": "python:3.11-slim",
            "quality_samples": [{"messages": [{"role": "user", "content": "solve"}]}],
        },
    )
    db.add(run)
    db.commit()

    def fake_quality_samples(db, run, samples, *_args, **_kwargs):
        request = Request(
            id=3,
            run_id=run.id,
            sample_index=0,
            kind="quality",
            status="completed",
            input_messages={"messages": samples[0]["messages"]},
        )
        db.add(request)
        db.flush()
        db.add(Attempt(id=3, request_id=request.id, attempt_index=1, extracted_fields={"content": "print(1)"}))
        db.commit()
        return [{"status": "completed", "request_id": request.id}]

    monkeypatch.setattr("evalplat.services.run_worker.execute_quality_samples", fake_quality_samples)
    monkeypatch.setattr(
        "evalplat.services.code_execution_service.get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "sandbox_image": "python:3.11-slim@sha256:" + "a" * 64,
                "sandbox_require_immutable_image": True,
            },
        )(),
    )

    result = process_one(db, "worker-a", object(), object())

    db.refresh(run)
    assert result["status"] == "failed"
    assert run.status == "failed"
    assert db.query(CodeExecution).filter(CodeExecution.run_id == run.id).count() == 0


def test_worker_converges_evalscope_unavailable_to_failed(db, tmp_path, monkeypatch):
    crypto = CryptoService(Fernet.generate_key().decode())
    run = _evalscope_run(db, crypto)
    monkeypatch.setattr(
        "evalplat.services.run_worker.get_settings",
        lambda: type("S", (), {"evalscope_dir": str(tmp_path)})(),
    )

    class UnavailableEngine:
        def run(self, _config):
            from evalplat.services.evaluation_engine import EvaluationEngineUnavailable

            raise EvaluationEngineUnavailable("EvalScope 未安装")

    result = process_one(
        db,
        "worker-a",
        crypto,
        _AllowGuard(),
        evalscope_engine=UnavailableEngine(),
    )

    db.refresh(run)
    request = db.query(Request).filter(Request.run_id == run.id).one()
    attempt = db.query(Attempt).filter(Attempt.request_id == request.id).one()
    assert result["status"] == "failed"
    assert run.status == "failed"
    assert request.status == "failed"
    assert attempt.error_category == "engine_unavailable"
    assert "未安装" in (attempt.error_body or "")


def test_evalscope_result_redacts_api_key_before_persisting(db, tmp_path, monkeypatch):
    crypto = CryptoService(Fernet.generate_key().decode())
    run = _evalscope_run(db, crypto)
    monkeypatch.setattr(
        "evalplat.services.run_worker.get_settings",
        lambda: type("S", (), {"evalscope_dir": str(tmp_path)})(),
    )

    class LeakyEngine:
        def run(self, _config):
            return {
                "api_key": "sk-evalscope-secret",
                "message": "request failed for sk-evalscope-secret",
            }

    process_one(db, "worker-a", crypto, _AllowGuard(), evalscope_engine=LeakyEngine())

    attempt = db.query(Attempt).one()
    assert "sk-evalscope-secret" not in (attempt.raw_response or "")
    assert "sk-evalscope-secret" not in str(attempt.extracted_fields)
    assert attempt.extracted_fields["evalscope"]["api_key"] == "[REDACTED]"


def test_evalscope_performance_exception_redacts_api_key(db, tmp_path, monkeypatch):
    """A failure while building/running EvalScope must not persist its config secret."""
    from evalplat.services.performance_executor import execute_evalscope_performance

    crypto = CryptoService(Fernet.generate_key().decode())
    supplier = Supplier(name="性能脱敏供应商", short_name="perf-redact", type="other")
    db.add(supplier)
    db.flush()
    connection = Connection(
        supplier_id=supplier.id,
        name="性能脱敏连接",
        base_url="https://api.example.com",
        api_key_ciphertext=crypto.encrypt("provider-private-secret"),
    )
    db.add(connection)
    db.flush()
    run = Run(
        task_id=uuid.uuid4(),
        model_deployment_id=uuid.uuid4(),
        status="running",
        run_snapshot={
            "connection_id": str(connection.id),
            "model_id": "demo-model",
            "base_url": "https://api.example.com",
        },
    )
    db.add(run)
    db.commit()
    monkeypatch.setattr(
        "evalplat.services.performance_executor.run_evalscope_performance",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("adapter config contains provider-private-secret")
        ),
    )

    result = execute_evalscope_performance(
        db, run, crypto, _AllowGuard(), work_dir=tmp_path,
    )

    attempt = db.query(Attempt).filter(Attempt.request_id == db.query(Request).one().id).one()
    assert result["status"] == "failed"
    assert "provider-private-secret" not in (result["error"] or "")
    assert "provider-private-secret" not in (attempt.error_body or "")


def test_evalscope_configuration_error_is_persisted_and_run_fails(db, tmp_path, monkeypatch):
    crypto = CryptoService(Fernet.generate_key().decode())
    run = _evalscope_run(db, crypto)
    run.run_snapshot = {key: value for key, value in run.run_snapshot.items() if key != "evalscope_datasets"}
    db.commit()
    monkeypatch.setattr(
        "evalplat.services.run_worker.get_settings",
        lambda: type("S", (), {"evalscope_dir": str(tmp_path)})(),
    )

    result = process_one(db, "worker-a", crypto, _AllowGuard())

    db.refresh(run)
    assert result["status"] == "failed"
    assert run.status == "failed"
    request = db.query(Request).filter(Request.run_id == run.id).one()
    assert request.status == "failed"
