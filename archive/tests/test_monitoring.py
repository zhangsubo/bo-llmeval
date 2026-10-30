"""监控 API 测试。"""

import uuid

import pytest


class TestDashboard:
    def test_dashboard_empty(self, client):
        resp = client.get("/api/v1/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert "tasks" in data
        assert "runs" in data
        assert set(data["tasks"].keys()) >= {"queued", "running", "completed", "failed"}

    def test_dashboard_with_tasks(self, client):
        client.post("/api/v1/tasks", json={"name": "A", "run_mode": "serial"})
        resp = client.get("/api/v1/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        # draft 不在 dashboard 统计中，但 queued=0 仍应存在
        assert "tasks" in data


class TestTaskStatus:
    def test_task_status(self, client):
        create_resp = client.post("/api/v1/tasks", json={"name": "T", "run_mode": "serial"})
        tid = create_resp.json()["id"]
        resp = client.get(f"/api/v1/tasks/{tid}/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == tid
        assert data["task_status"] == "draft"
        assert data["total_runs"] == 0
        assert data["run_stats"] == {}

    def test_task_status_with_runs(self, client, db):
        create_resp = client.post("/api/v1/tasks", json={"name": "T", "run_mode": "serial"})
        tid = create_resp.json()["id"]

        from evalplat.models.run import Run
        run = Run(
            id=uuid.uuid4(),
            task_id=uuid.UUID(tid),
            model_deployment_id=uuid.uuid4(),
            seq=1,
            status="queued",
        )
        db.add(run)
        db.commit()

        resp = client.get(f"/api/v1/tasks/{tid}/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_runs"] == 1
        assert data["run_stats"]["queued"] == 1

    def test_task_status_not_found(self, client):
        resp = client.get("/api/v1/tasks/00000000-0000-0000-0000-000000000000/status")
        assert resp.status_code == 404

    def test_task_status_includes_load_and_error_summary(self, client, db):
        from datetime import datetime, timedelta, timezone

        from evalplat.models.attempt import Attempt
        from evalplat.models.request import Request
        from evalplat.models.run import Run

        task_id = uuid.UUID(
            client.post("/api/v1/tasks", json={"name": "RPS", "run_mode": "serial"}).json()["id"]
        )
        started_at = datetime.now(timezone.utc) - timedelta(seconds=2)
        run = Run(
            task_id=task_id,
            model_deployment_id=uuid.uuid4(),
            status="running",
            started_at=started_at,
            run_snapshot={"request_count": 2, "load_profile": {"mode": "fixed_rps", "rps": 3}},
        )
        db.add(run)
        db.flush()
        request = Request(
            id=1,
            run_id=run.id,
            kind="perf",
            status="failed",
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
        )
        db.add(request)
        db.flush()
        db.add(
            Attempt(
                id=1,
                request_id=request.id,
                attempt_index=1,
                error_category="timeout",
                finished_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

        response = client.get(f"/api/v1/tasks/{task_id}/status")
        assert response.status_code == 200
        data = response.json()
        assert data["target_rps"] == 3
        assert data["actual_rps"] is not None
        assert data["error_categories"] == {"timeout": 1}
        assert data["runs"][0]["last_error_at"] is not None

    def test_quality_run_progress_excludes_performance_warmup_phases(self, client, db):
        from evalplat.models.request import Request
        from evalplat.models.run import Run

        task_id = uuid.UUID(
            client.post("/api/v1/tasks", json={"name": "质量进度", "run_mode": "serial"}).json()["id"]
        )
        run = Run(
            task_id=task_id,
            model_deployment_id=uuid.uuid4(),
            status="running",
            run_snapshot={
                "evaluation_kind": "quality",
                "request_count": 2,
                "warmup_count": 3,
            },
        )
        db.add(run)
        db.flush()
        db.add_all([
            Request(id=1, run_id=run.id, kind="quality", status="completed"),
            Request(id=2, run_id=run.id, kind="quality", status="completed"),
        ])
        db.commit()

        response = client.get(f"/api/v1/tasks/{task_id}/status")

        assert response.status_code == 200
        detail = response.json()["runs"][0]
        assert detail["planned_requests"] == 2
        assert detail["progress"] == 1.0
