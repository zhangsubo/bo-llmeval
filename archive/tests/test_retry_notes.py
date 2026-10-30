"""人工重试、历史复测和备注 API。"""

import uuid

from evalplat.models.connection import Connection
from evalplat.models.model_deployment import ModelDeployment
from evalplat.models.run import Run
from evalplat.models.supplier import Supplier
from evalplat.models.task import Task


def _target(db):
    supplier = Supplier(name="供应商", short_name="sup", type="原厂")
    db.add(supplier)
    db.flush()
    connection = Connection(supplier_id=supplier.id, name="连接", base_url="https://api.example.com", api_key_ciphertext=b"cipher")
    db.add(connection)
    db.flush()
    deployment = ModelDeployment(connection_id=connection.id, model_id="model-a", source="manual", status="active")
    db.add(deployment)
    db.flush()
    return connection, deployment


def test_retry_creates_new_run_and_refreshes_credential_snapshot(client, db):
    connection, deployment = _target(db)
    task = Task(name="任务", run_mode="serial", status="failed", config_snapshot={"model_deployment_ids": [str(deployment.id)], "messages": []})
    db.add(task)
    db.flush()
    source = Run(task_id=task.id, model_deployment_id=deployment.id, seq=1, status="failed", run_snapshot={"connection_id": str(connection.id), "model_id": "model-a", "credential_version": 1})
    db.add(source)
    db.commit()

    response = client.post(f"/api/v1/runs/{source.id}/retry")
    assert response.status_code == 201, response.text
    retried = response.json()
    assert retried["id"] != str(source.id)
    assert retried["status"] == "queued"
    db.expire_all()
    assert db.get(Task, task.id).status == "queued"


def test_retest_creates_draft_and_reports_changes(client, db):
    _connection, deployment = _target(db)
    task = Task(name="历史", run_mode="parallel", status="completed", config_snapshot={"model_deployment_ids": [str(deployment.id)], "request_count": 2})
    db.add(task)
    db.commit()

    response = client.post(f"/api/v1/tasks/{task.id}/retest")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["task"]["status"] == "draft"
    assert body["task"]["config_snapshot"]["source_task_id"] == str(task.id)


def test_retest_is_idempotent_for_repeated_clicks(client, db):
    _connection, deployment = _target(db)
    task = Task(
        name="历史幂等",
        run_mode="parallel",
        status="completed",
        config_snapshot={"model_deployment_ids": [str(deployment.id)]},
    )
    db.add(task)
    db.commit()

    first = client.post(f"/api/v1/tasks/{task.id}/retest", headers={"Idempotency-Key": "retest-1"})
    second = client.post(f"/api/v1/tasks/{task.id}/retest", headers={"Idempotency-Key": "retest-1"})

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["task"]["id"] == first.json()["task"]["id"]


def test_retest_rejects_key_reused_for_another_history(client, db):
    _connection, deployment = _target(db)
    tasks = [
        Task(name=f"历史-{index}", run_mode="serial", status="completed", config_snapshot={"model_deployment_ids": [str(deployment.id)]})
        for index in (1, 2)
    ]
    db.add_all(tasks)
    db.commit()

    assert client.post(f"/api/v1/tasks/{tasks[0].id}/retest", headers={"Idempotency-Key": "same-key"}).status_code == 201
    conflict = client.post(f"/api/v1/tasks/{tasks[1].id}/retest", headers={"Idempotency-Key": "same-key"})

    assert conflict.status_code == 409


def test_task_notes_can_be_created_listed_and_updated(client, db):
    task = Task(name="任务", run_mode="serial", status="draft")
    db.add(task)
    db.commit()

    created = client.post(f"/api/v1/tasks/{task.id}/notes", json={"body": "首次结论"})
    assert created.status_code == 201, created.text
    note_id = created.json()["id"]
    listed = client.get(f"/api/v1/tasks/{task.id}/notes")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["body"] == "首次结论"
    updated = client.patch(f"/api/v1/notes/{note_id}", json={"body": "修订结论"})
    assert updated.status_code == 200
    assert updated.json()["body"] == "修订结论"
