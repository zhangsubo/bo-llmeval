from __future__ import annotations

import uuid

from evalplat.core.crypto import CryptoService
from evalplat.core.ssrf import SSRFGuard
from evalplat.models.attempt import Attempt
from evalplat.models.connection import Connection
from evalplat.models.request import Request
from evalplat.models.run import Run
from evalplat.models.supplier import Supplier
from evalplat.services.quality_executor import execute_quality_sample
from evalplat.services.run_executor import execute_chat_request


class _Guard:
    def check_url(self, _url):
        return True, "OK"


def test_quality_retry_reuses_request_and_records_each_attempt(db):
    run = Run(status="running", run_snapshot={"connection_id": str(uuid.uuid4()), "model_id": "demo"})
    db.add(run)
    db.commit()
    calls = []

    def fake_executor(db, run, messages, crypto, guard, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {"status": "failed", "request_id": kwargs["request"].id, "http_status": 503, "error_category": "temporary_network", "error": "temporary"}
        return {"status": "completed", "request_id": kwargs["request"].id}

    result = execute_quality_sample(
        db,
        run,
        {"messages": [{"role": "user", "content": "2+2?"}], "expected": "4"},
        CryptoService("S23qeuMUp7KWdXsoDV6X9-0T76tfE7JrEuDy8KMafA0="),
        SSRFGuard(),
        max_retries=2,
        executor=fake_executor,
    )

    assert result["status"] == "completed"
    assert len(calls) == 2
    assert calls[0]["request"] is calls[1]["request"]
    assert calls[0]["attempt_index"] == 1
    assert calls[1]["attempt_index"] == 2


def test_quality_sample_persists_native_score(db):
    supplier = Supplier(name="评分供应商", short_name="score", type="原厂")
    db.add(supplier)
    db.flush()
    crypto = CryptoService("S23qeuMUp7KWdXsoDV6X9-0T76tfE7JrEuDy8KMafA0=")
    provider_secret = "provider-secret-xyz"
    connection = Connection(
        supplier_id=supplier.id,
        name="评分连接",
        base_url="https://api.example.com",
        api_key_ciphertext=crypto.encrypt(provider_secret),
    )
    db.add(connection)
    db.flush()
    run = Run(status="running", run_snapshot={"connection_id": str(connection.id), "model_id": "demo"})
    db.add(run)
    db.commit()

    class Response:
        status_code = 200
        headers = {}
        text = '{"choices":[{"message":{"content":"4"}}]}'

        def json(self):
            return {"choices": [{"message": {"content": "4"}}], "usage": {}}

    class Client:
        def __init__(self, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def post(self, *_args, **_kwargs): return Response()

    result = execute_quality_sample(
        db,
        run,
        {
            "messages": [{"role": "user", "content": f"2+2? {provider_secret}"}],
            "expected": "4",
            "scoring_method": "exact_match",
        },
        crypto,
        _Guard(),
        executor=lambda *args, **kwargs: execute_chat_request(*args, **kwargs, client_factory=Client),
    )
    request = db.query(Request).filter(Request.run_id == run.id).one()
    assert result["quality_result"]["score"] == 1.0
    assert request.quality_result["mapping_status"] == "mapped"
    assert provider_secret not in str(request.input_messages)


def test_quality_retry_persists_two_attempts_with_real_transport(db):
    supplier = Supplier(name="供应商", short_name="quality", type="原厂")
    db.add(supplier)
    db.flush()
    crypto = CryptoService("S23qeuMUp7KWdXsoDV6X9-0T76tfE7JrEuDy8KMafA0=")
    connection = Connection(
        supplier_id=supplier.id,
        name="连接",
        base_url="https://api.example.com",
        api_key_ciphertext=crypto.encrypt("secret"),
    )
    db.add(connection)
    db.flush()
    run = Run(
        task_id=uuid.uuid4(),
        status="running",
        run_snapshot={
            "connection_id": str(connection.id),
            "model_id": "demo-model",
        },
    )
    db.add(run)
    db.commit()

    class Response:
        def __init__(self, status_code):
            self.status_code = status_code
            self.headers = {"x-request-id": f"request-{status_code}"}
            self.text = "temporary" if status_code >= 400 else '{"ok": true}'

        def json(self):
            return {"choices": [{"message": {"content": "4"}}], "usage": {}}

    class Client:
        calls = 0

        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, *_args, **_kwargs):
            type(self).calls += 1
            return Response(503 if self.calls == 1 else 200)

    result = execute_quality_sample(
        db,
        run,
        {"messages": [{"role": "user", "content": "2+2?"}], "expected": "4"},
        crypto,
        _Guard(),
        max_retries=2,
        executor=lambda *args, **kwargs: execute_chat_request(
            *args, **kwargs, client_factory=Client
        ),
    )

    request = db.query(Request).filter(Request.run_id == run.id).one()
    attempts = db.query(Attempt).filter(Attempt.request_id == request.id).order_by(Attempt.attempt_index).all()
    assert result["status"] == "completed"
    assert request.status == "completed"
    assert [attempt.attempt_index for attempt in attempts] == [1, 2]


def test_quality_executor_exception_converges_pending_request(db):
    run = Run(
        status="running",
        run_snapshot={"connection_id": str(uuid.uuid4()), "model_id": "demo"},
    )
    db.add(run)
    db.commit()

    def exploding_executor(*_args, **_kwargs):
        raise ValueError("连接快照损坏")

    result = execute_quality_sample(
        db,
        run,
        {"messages": [{"role": "user", "content": "ping"}], "expected": "pong"},
        CryptoService("S23qeuMUp7KWdXsoDV6X9-0T76tfE7JrEuDy8KMafA0="),
        _Guard(),
        executor=exploding_executor,
    )

    request = db.query(Request).filter(Request.run_id == run.id).one()
    attempt = db.query(Attempt).filter(Attempt.request_id == request.id).one()
    assert result["status"] == "failed"
    assert request.status == "failed"
    assert attempt.error_category == "quality_executor_error"
    assert "连接快照损坏" in (attempt.error_body or "")


def test_quality_executor_failure_result_converges_pending_request(db):
    run = Run(
        status="running",
        run_snapshot={"connection_id": str(uuid.uuid4()), "model_id": "demo"},
    )
    db.add(run)
    db.commit()

    def failed_executor(*_args, **_kwargs):
        return {
            "status": "failed",
            "http_status": 503,
            "error_category": "temporary_network",
            "error": "upstream unavailable",
        }

    result = execute_quality_sample(
        db,
        run,
        {"messages": [{"role": "user", "content": "ping"}], "expected": "pong"},
        CryptoService("S23qeuMUp7KWdXsoDV6X9-0T76tfE7JrEuDy8KMafA0="),
        _Guard(),
        max_retries=0,
        executor=failed_executor,
    )

    request = db.query(Request).filter(Request.run_id == run.id).one()
    attempt = db.query(Attempt).filter(Attempt.request_id == request.id).one()
    assert result["status"] == "failed"
    assert request.status == "failed"
    assert attempt.error_category == "temporary_network"
    assert attempt.finished_at is not None
