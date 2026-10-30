"""任务 CRUD、submit、cancel 测试。"""

import uuid

import pytest


class TestTasksCRUD:
    def _create_deployment(self, client):
        supplier = client.post(
            "/api/v1/suppliers",
            json={"name": "目标供应商", "short_name": "target", "type": "原厂"},
        ).json()
        connection = client.post(
            f"/api/v1/suppliers/{supplier['id']}/connections",
            json={
                "name": "目标连接",
                "base_url": "https://api.openai.com",
                "api_key": "sk-test-key",
            },
        ).json()
        return client.post(
            f"/api/v1/connections/{connection['id']}/models",
            json={"model_id": "model-a"},
        ).json()["id"]

    def test_create_task(self, client):
        resp = client.post("/api/v1/tasks", json={
            "name": "评测任务1", "run_mode": "serial"
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "评测任务1"
        assert data["status"] == "draft"
        assert data["run_mode"] == "serial"
        assert "id" in data

    def test_create_task_with_idempotency_key(self, client):
        resp = client.post("/api/v1/tasks", json={
            "name": "幂等任务", "run_mode": "serial", "idempotency_key": "key-001"
        })
        assert resp.status_code == 201
        assert resp.json()["idempotency_key"] == "key-001"

    def test_template_fields_are_materialized_into_draft_snapshot(self, client):
        template = client.post("/api/v1/templates", json={"name": "模板快照"}).json()
        version = client.post(
            f"/api/v1/templates/{template['id']}/versions",
            json={
                "test_type": "performance",
                "scenario": "fixed_rps",
                "request_count": 8,
                "warmup_count": 2,
                "timeouts": {"total": 30},
                "limits": {"rps": 4},
            },
        ).json()

        response = client.post(
            "/api/v1/tasks",
            json={"name": "模板任务", "run_mode": "serial", "template_version_id": version["id"]},
        )

        assert response.status_code == 201
        snapshot = response.json()["config_snapshot"]
        assert snapshot["test_type"] == "performance"
        assert snapshot["scenario"] == "fixed_rps"
        assert snapshot["request_count"] == 8
        assert snapshot["warmup_count"] == 2
        assert snapshot["timeouts"] == {"total": 30}
        assert snapshot["limits"] == {"rps": 4}

    def test_code_template_overrides_schema_defaults(self, client):
        template = client.post("/api/v1/templates", json={"name": "代码模板"}).json()
        version = client.post(
            f"/api/v1/templates/{template['id']}/versions",
            json={"test_type": "code", "scenario": "code_generation", "request_count": 1},
        ).json()

        response = client.post(
            "/api/v1/tasks",
            json={"name": "代码模板任务", "run_mode": "serial", "template_version_id": version["id"]},
        )

        assert response.status_code == 201
        snapshot = response.json()["config_snapshot"]
        assert snapshot["evaluation_kind"] == "quality"
        assert snapshot["quality_mode"] == "code"
        assert snapshot["evaluation_engine"] == "native"

    def test_list_tasks(self, client):
        client.post("/api/v1/tasks", json={"name": "A", "run_mode": "serial"})
        client.post("/api/v1/tasks", json={"name": "B", "run_mode": "parallel"})
        resp = client.get("/api/v1/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 2
        assert len(data["items"]) >= 2

    def test_list_tasks_with_status_filter(self, client):
        create_resp = client.post("/api/v1/tasks", json={"name": "A", "run_mode": "serial"})
        task_id = create_resp.json()["id"]
        deployment_id = self._create_deployment(client)
        client.patch(f"/api/v1/tasks/{task_id}", json={"name": "A"})
        # Task targets are part of the immutable draft config; create a task
        # with the target explicitly for the submit path.
        task_id = client.post(
            "/api/v1/tasks",
            json={"name": "A queued", "run_mode": "serial", "model_deployment_ids": [deployment_id]},
        ).json()["id"]
        client.post(f"/api/v1/tasks/{task_id}/submit")
        client.post("/api/v1/tasks", json={"name": "B", "run_mode": "serial"})

        resp = client.get("/api/v1/tasks?status=queued")
        assert resp.status_code == 200
        data = resp.json()
        assert all(item["status"] == "queued" for item in data["items"])

    def test_get_task(self, client):
        create_resp = client.post("/api/v1/tasks", json={"name": "Test", "run_mode": "serial"})
        tid = create_resp.json()["id"]
        resp = client.get(f"/api/v1/tasks/{tid}")
        assert resp.status_code == 200
        assert resp.json()["id"] == tid

    def test_update_task(self, client):
        create_resp = client.post("/api/v1/tasks", json={"name": "Old", "run_mode": "serial"})
        tid = create_resp.json()["id"]
        resp = client.patch(f"/api/v1/tasks/{tid}", json={"name": "New"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "New"

    @pytest.mark.parametrize(
        ("changes", "message"),
        [
            ({"evaluation_kind": "performance", "quality_mode": "code"}, "quality_mode=code"),
            ({"evaluation_kind": "quality", "evaluation_engine": "evalscope", "quality_mode": "code"}, "quality_mode=code"),
        ],
    )
    def test_update_task_rejects_incompatible_evaluation_config(self, client, changes, message):
        task_id = client.post(
            "/api/v1/tasks", json={"name": "Config", "run_mode": "serial"}
        ).json()["id"]
        response = client.patch(f"/api/v1/tasks/{task_id}", json=changes)
        assert response.status_code == 400
        assert message in response.json()["error"]["message"]

    def test_update_task_allows_evalscope_performance(self, client):
        task_id = client.post(
            "/api/v1/tasks", json={"name": "EvalScope performance", "run_mode": "serial"}
        ).json()["id"]
        response = client.patch(
            f"/api/v1/tasks/{task_id}",
            json={"evaluation_kind": "performance", "evaluation_engine": "evalscope"},
        )
        assert response.status_code == 200
        assert response.json()["config_snapshot"]["evaluation_engine"] == "evalscope"

    def test_update_non_draft_task_fails(self, client):
        deployment_id = self._create_deployment(client)
        create_resp = client.post("/api/v1/tasks", json={"name": "Test", "run_mode": "serial", "model_deployment_ids": [deployment_id]})
        tid = create_resp.json()["id"]
        client.post(f"/api/v1/tasks/{tid}/submit")
        resp = client.patch(f"/api/v1/tasks/{tid}", json={"name": "New"})
        assert resp.status_code == 409

    def test_not_found_404(self, client):
        resp = client.get("/api/v1/tasks/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_missing_fields_422(self, client):
        resp = client.post("/api/v1/tasks", json={"name": "Test"})
        assert resp.status_code == 422

    def test_invalid_run_mode_422(self, client):
        resp = client.post(
            "/api/v1/tasks", json={"name": "Test", "run_mode": "invalid"}
        )
        assert resp.status_code == 422


class TestTaskSubmit:
    def _create_deployment(self, client):
        supplier = client.post(
            "/api/v1/suppliers",
            json={"name": "提交供应商", "short_name": "submit", "type": "原厂"},
        ).json()
        connection = client.post(
            f"/api/v1/suppliers/{supplier['id']}/connections",
            json={"name": "提交连接", "base_url": "https://api.openai.com", "api_key": "sk-submit-key"},
        ).json()
        return client.post(
            f"/api/v1/connections/{connection['id']}/models",
            json={"model_id": "model-submit"},
        ).json()["id"]

    def test_submit_task(self, client):
        deployment_id = self._create_deployment(client)
        create_resp = client.post("/api/v1/tasks", json={"name": "Test", "run_mode": "serial", "model_deployment_ids": [deployment_id]})
        tid = create_resp.json()["id"]
        resp = client.post(f"/api/v1/tasks/{tid}/submit")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "queued"
        assert data["queued_at"] is not None

    def test_submit_non_draft_fails(self, client):
        deployment_id = self._create_deployment(client)
        create_resp = client.post("/api/v1/tasks", json={"name": "Test", "run_mode": "serial", "model_deployment_ids": [deployment_id]})
        tid = create_resp.json()["id"]
        client.post(f"/api/v1/tasks/{tid}/submit")
        resp = client.post(f"/api/v1/tasks/{tid}/submit")
        assert resp.status_code == 409


class TestTaskCancel:
    def test_cancel_draft_task(self, client):
        create_resp = client.post("/api/v1/tasks", json={"name": "Test", "run_mode": "serial"})
        tid = create_resp.json()["id"]
        resp = client.post(f"/api/v1/tasks/{tid}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_cancel_queued_task(self, client):
        deployment_id = TestTaskSubmit()._create_deployment(client)
        create_resp = client.post("/api/v1/tasks", json={"name": "Test", "run_mode": "serial", "model_deployment_ids": [deployment_id]})
        tid = create_resp.json()["id"]
        client.post(f"/api/v1/tasks/{tid}/submit")
        resp = client.post(f"/api/v1/tasks/{tid}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_cancel_completed_task_fails(self, client):
        """已完成任务不可取消。"""
        create_resp = client.post("/api/v1/tasks", json={"name": "Test", "run_mode": "serial"})
        tid = create_resp.json()["id"]
        # 直接改状态模拟完成（绕过 service 层约束）
        from tests.conftest import _TestSessionLocal
        from evalplat.models.task import Task
        s = _TestSessionLocal()
        task = s.get(Task, tid)
        task.status = "completed"
        s.commit()
        s.close()

        resp = client.post(f"/api/v1/tasks/{tid}/cancel")
        assert resp.status_code == 409

    def test_cancel_running_task_enters_cancelling(self, client):
        from tests.conftest import _TestSessionLocal
        from evalplat.models.task import Task

        tid = client.post("/api/v1/tasks", json={"name": "Test", "run_mode": "serial"}).json()["id"]
        session = _TestSessionLocal()
        task = session.get(Task, tid)
        task.status = "running"
        session.commit()
        session.close()

        resp = client.post(f"/api/v1/tasks/{tid}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelling"

    def test_cancel_running_task_marks_child_runs_cancelling(self, client):
        """Task cancellation must fence an already-claimed Run."""

        from tests.conftest import _TestSessionLocal
        from evalplat.models.run import Run
        from evalplat.models.task import Task

        tid = client.post(
            "/api/v1/tasks", json={"name": "Test", "run_mode": "serial"}
        ).json()["id"]
        session = _TestSessionLocal()
        task = session.get(Task, tid)
        run = Run(
            task_id=task.id,
            model_deployment_id=uuid.uuid4(),
            status="running",
            fencing_token=1,
        )
        session.add(run)
        task.status = "running"
        session.commit()
        session.close()

        resp = client.post(f"/api/v1/tasks/{tid}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelling"

        session = _TestSessionLocal()
        try:
            refreshed = session.get(Run, run.id)
            assert refreshed is not None
            assert refreshed.status == "cancelling"
        finally:
            session.close()
