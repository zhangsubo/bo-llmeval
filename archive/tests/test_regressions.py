"""回归测试：跨供应商快照、安全证据和 lease 恢复。"""

import uuid
from datetime import datetime, timedelta, timezone

from evalplat.core.crypto import CryptoService
from evalplat.core.ssrf import SSRFGuard
from evalplat.models.attempt import Attempt
from evalplat.models.connection import Connection
from evalplat.models.model_deployment import ModelDeployment
from evalplat.models.run import Run
from evalplat.models.supplier import Supplier
from evalplat.services.run_executor import execute_chat_request
from evalplat.services.run_queue_service import fail_expired_runs
from evalplat.services.task_service import create_task, submit_task


class _Guard:
    def check_url(self, _url):
        return True, "OK"


class _Response:
    status_code = 200
    headers = {
        "x-request-id": "request-1",
        "content-type": "application/json",
        "set-cookie": "session=should-not-be-persisted",
    }
    text = '{"choices":[{"message":{"content":"safe"}}]}'

    def json(self):
        return {"choices": [{"message": {"content": "safe"}}], "usage": {}}


class _Client:
    def __init__(self, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def post(self, *_args, **_kwargs):
        return _Response()


def _deployment(db, suffix: str):
    supplier = Supplier(name=f"供应商-{suffix}", short_name=f"s-{suffix}", type="原厂")
    db.add(supplier)
    db.flush()
    connection = Connection(
        supplier_id=supplier.id,
        name=f"连接-{suffix}",
        base_url=f"https://api-{suffix}.example.com",
        api_key_ciphertext=b"ciphertext",
    )
    db.add(connection)
    db.flush()
    deployment = ModelDeployment(
        connection_id=connection.id,
        model_id=f"model-{suffix}",
        source="manual",
        status="active",
    )
    db.add(deployment)
    db.flush()
    return deployment, connection


def test_each_target_run_keeps_its_own_connection_snapshot(db):
    first, first_connection = _deployment(db, "one")
    second, second_connection = _deployment(db, "two")
    task = create_task(
        db,
        name="多供应商任务",
        run_mode="parallel",
        model_deployment_ids=[first.id, second.id],
    )

    submit_task(db, task.id)
    runs = db.query(Run).filter(Run.task_id == task.id).order_by(Run.seq).all()

    assert [run.run_snapshot["connection_id"] for run in runs] == [
        str(first_connection.id),
        str(second_connection.id),
    ]
    assert [run.run_snapshot["base_url"] for run in runs] == [
        first_connection.base_url,
        second_connection.base_url,
    ]


def test_expired_run_reconciles_parent_task(db):
    from evalplat.models.task import Task

    parent = Task(name="过期任务", run_mode="serial", status="running")
    db.add(parent)
    db.flush()
    run = Run(
        task_id=parent.id,
        status="running",
        lease_owner="dead-worker",
        lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    db.add(run)
    db.commit()

    assert fail_expired_runs(db) == 1
    db.refresh(parent)
    db.refresh(run)
    assert run.status == "interrupted_failed"
    assert parent.status == "failed"


def test_request_evidence_redacts_exact_api_key_and_headers(db):
    crypto = CryptoService("S23qeuMUp7KWdXsoDV6X9-0T76tfE7JrEuDy8KMafA0=")
    secret = "tiny-key"
    supplier = Supplier(name="安全供应商", short_name="safe", type="原厂")
    db.add(supplier)
    db.flush()
    connection = Connection(
        supplier_id=supplier.id,
        name="安全连接",
        base_url="https://api.example.com",
        api_key_ciphertext=crypto.encrypt(secret),
    )
    db.add(connection)
    db.flush()
    run = Run(
        task_id=uuid.uuid4(),
        model_deployment_id=uuid.uuid4(),
        status="running",
        run_snapshot={
            "connection_id": str(connection.id),
            "model_id": "model-a",
        },
    )
    db.add(run)
    db.commit()

    result = execute_chat_request(
        db,
        run,
        [{"role": "user", "content": "ping"}],
        crypto,
        _Guard(),
        client_factory=_Client,
    )

    attempt = db.query(Attempt).one()
    assert result["status"] == "completed"
    assert secret not in (attempt.raw_response or "")
    assert "set-cookie" not in (attempt.response_headers or {})
