"""列表查询筛选在服务端执行的回归测试。"""

from datetime import datetime, timedelta, timezone

from evalplat.models.connection import Connection
from evalplat.models.model_deployment import ModelDeployment
from evalplat.models.report import Report
from evalplat.models.run import Run
from evalplat.models.supplier import Supplier
from evalplat.models.task import Task


def _connection_graph(db, *, supplier_name: str, connection_name: str, model_id: str):
    supplier = Supplier(name=supplier_name, short_name=supplier_name.lower(), type="other")
    db.add(supplier)
    db.flush()
    connection = Connection(
        supplier_id=supplier.id,
        name=connection_name,
        base_url="https://api.example.com",
    )
    db.add(connection)
    db.flush()
    deployment = ModelDeployment(
        connection_id=connection.id,
        model_id=model_id,
        display_name=f"{model_id} display",
        source="manual",
    )
    db.add(deployment)
    db.flush()
    return supplier, connection, deployment


def test_connection_and_model_keyword_filters(client, db):
    supplier, connection, deployment = _connection_graph(
        db, supplier_name="Acme", connection_name="北京主链路", model_id="alpha-model"
    )
    _connection_graph(db, supplier_name="Other", connection_name="备用链路", model_id="beta-model")
    db.commit()

    connection_response = client.get(
        f"/api/v1/suppliers/{supplier.id}/connections?keyword=北京"
    )
    assert connection_response.status_code == 200
    assert [item["id"] for item in connection_response.json()["items"]] == [str(connection.id)]

    model_response = client.get("/api/v1/model-deployments?keyword=alpha-model")
    assert model_response.status_code == 200
    assert [item["id"] for item in model_response.json()["items"]] == [str(deployment.id)]


def test_task_keyword_and_deployment_filters(client, db):
    supplier, _connection, deployment = _connection_graph(
        db, supplier_name="Task Supplier", connection_name="Task Connection", model_id="task-model"
    )
    task = Task(
        name="只命中这个任务",
        run_mode="serial",
        status="draft",
        config_snapshot={"model_deployment_ids": [str(deployment.id)]},
    )
    other = Task(name="另一个任务", run_mode="serial", status="draft", config_snapshot={})
    db.add_all([task, other])
    db.commit()

    keyword_response = client.get("/api/v1/tasks?keyword=只命中")
    assert keyword_response.status_code == 200
    assert [item["id"] for item in keyword_response.json()["items"]] == [str(task.id)]

    supplier_response = client.get(f"/api/v1/tasks?supplier_id={supplier.id}")
    assert supplier_response.status_code == 200
    assert [item["id"] for item in supplier_response.json()["items"]] == [str(task.id)]


def test_flat_report_list_filters_by_supplier_status_and_date(client, db):
    supplier, _connection, deployment = _connection_graph(
        db, supplier_name="Report Supplier", connection_name="Report Connection", model_id="report-model"
    )
    task = Task(name="报告任务", run_mode="serial", status="completed", config_snapshot={})
    db.add(task)
    db.flush()
    run = Run(
        task_id=task.id,
        model_deployment_id=deployment.id,
        status="completed",
        run_snapshot={},
    )
    db.add(run)
    db.flush()
    created_at = datetime.now(timezone.utc) - timedelta(days=1)
    report = Report(
        task_id=task.id,
        version=1,
        status="completed",
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(report)
    db.commit()

    response = client.get(
        "/api/v1/reports",
        params={
            "supplier_id": str(supplier.id),
            "status": "completed",
            "created_from": (created_at - timedelta(minutes=1)).isoformat(),
            "created_to": (created_at + timedelta(minutes=1)).isoformat(),
        },
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == str(report.id)
