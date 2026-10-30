"""Run 查询测试。"""

import uuid

import pytest


class TestRunsQuery:
    def _create_run(self, client, db):
        """辅助：创建一个 task 并插入 run 记录。"""
        # 创建 task
        resp = client.post("/api/v1/tasks", json={"name": "T", "run_mode": "serial"})
        task_id = resp.json()["id"]

        # 直接插入 run（run 创建不在本 Phase 路由内）
        from evalplat.models.run import Run
        run = Run(
            id=uuid.uuid4(),
            task_id=uuid.UUID(task_id),
            model_deployment_id=uuid.uuid4(),
            seq=1,
            status="queued",
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return task_id, str(run.id)

    def test_list_runs(self, client, db):
        task_id, run_id = self._create_run(client, db)
        resp = client.get(f"/api/v1/tasks/{task_id}/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert len(data["items"]) >= 1

    def test_get_run(self, client, db):
        task_id, run_id = self._create_run(client, db)
        resp = client.get(f"/api/v1/runs/{run_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == run_id

    def test_get_run_not_found(self, client):
        resp = client.get("/api/v1/runs/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_list_requests_empty(self, client, db):
        task_id, run_id = self._create_run(client, db)
        resp = client.get(f"/api/v1/runs/{run_id}/requests")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_list_runs_with_status_filter(self, client, db):
        task_id, run_id = self._create_run(client, db)
        resp = client.get(f"/api/v1/tasks/{task_id}/runs?status=queued")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    def test_request_detail_includes_input_and_attempts(self, client, db):
        _, run_id = self._create_run(client, db)
        from evalplat.models.attempt import Attempt
        from evalplat.models.request import Request

        request = Request(
            run_id=uuid.UUID(run_id),
            kind="quality",
            phase="steady",
            status="completed",
            input_messages={"messages": [{"role": "user", "content": "2+2?"}], "expected": "4"},
        )
        request.id = 101
        db.add(request)
        db.flush()
        db.add(Attempt(id=201, request_id=request.id, attempt_index=1, http_status=200, raw_response='{"ok":true}'))
        db.commit()

        response = client.get(f"/api/v1/runs/{run_id}/requests/{request.id}")

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["input_messages"]["expected"] == "4"
        assert len(data["attempts"]) == 1
        assert data["attempts"][0]["http_status"] == 200

    def test_request_list_excludes_quality_body(self, client, db):
        _, run_id = self._create_run(client, db)
        from evalplat.models.request import Request

        request = Request(
            run_id=uuid.UUID(run_id),
            kind="quality",
            status="completed",
            input_messages={"messages": [{"role": "user", "content": "secret prompt"}]},
            quality_result={"actual": "secret answer", "score": 1.0},
        )
        request.id = 103
        db.add(request)
        db.commit()

        response = client.get(f"/api/v1/runs/{run_id}/requests")

        assert response.status_code == 200
        item = response.json()["items"][0]
        assert "quality_result" not in item
        assert "input_messages" not in item

    def test_run_snapshot_paths_are_reduced_to_basenames(self, client, db):
        from evalplat.models.run import Run

        task_id = client.post("/api/v1/tasks", json={"name": "Paths", "run_mode": "serial"}).json()["id"]
        run = Run(
            task_id=uuid.UUID(task_id),
            status="completed",
            run_snapshot={
                "dataset_snapshot": {"storage_path": "/srv/evalplat/data/datasets/data.jsonl"},
                "tokenizer_snapshot": {"local_path": "/srv/evalplat/data/tokenizers/tok"},
            },
        )
        db.add(run)
        db.commit()

        response = client.get(f"/api/v1/runs/{run.id}")

        assert response.status_code == 200
        snapshot = response.json()["run_snapshot"]
        assert snapshot["dataset_snapshot"]["storage_path"] == "data.jsonl"
        assert snapshot["tokenizer_snapshot"]["local_path"] == "tok"

    def test_cancel_run_redacts_snapshot_paths(self, client, db):
        from evalplat.models.run import Run

        task_id = client.post("/api/v1/tasks", json={"name": "Cancel path", "run_mode": "serial"}).json()["id"]
        run = Run(
            task_id=uuid.UUID(task_id),
            status="queued",
            run_snapshot={
                "dataset_snapshot": {"storage_path": r"C:\\srv\\data\\secret.jsonl"},
                "tokenizer_snapshot": {"local_path": r"C:\\srv\\tok-secret"},
            },
        )
        db.add(run)
        db.commit()

        response = client.post(f"/api/v1/runs/{run.id}/cancel")

        assert response.status_code == 200
        snapshot = response.json()["run_snapshot"]
        assert snapshot["dataset_snapshot"]["storage_path"] == "secret.jsonl"
        assert snapshot["tokenizer_snapshot"]["local_path"] == "tok-secret"

    def test_request_detail_rejects_cross_run_request(self, client, db):
        _, run_id = self._create_run(client, db)
        from evalplat.models.request import Request

        from evalplat.models.run import Run
        other_run = Run(id=uuid.uuid4(), task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="failed")
        db.add(other_run)
        db.flush()
        request = Request(run_id=other_run.id, kind="perf", status="failed")
        request.id = 102
        db.add(request)
        db.commit()

        response = client.get(f"/api/v1/runs/{run_id}/requests/{request.id}")

        assert response.status_code == 404
