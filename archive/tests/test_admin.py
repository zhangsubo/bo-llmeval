"""系统管理 API 测试（Phase 11）。"""

import uuid

from fastapi.testclient import TestClient


class TestDashboard:
    """仪表盘 API 测试。"""

    def test_dashboard_empty(self, client: TestClient):
        """无任务时仪表盘应返回零计数。"""
        resp = client.get("/api/v1/admin/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tasks"]["total"] == 0
        assert data["tasks"]["queued"] == 0
        assert data["recent_errors"] == []

    def test_dashboard_with_tasks(self, client: TestClient, db):
        """有任务时仪表盘应正确统计。"""
        from evalplat.models.task import Task

        # 创建不同状态的任务
        for status in ["queued", "running", "completed", "completed", "failed"]:
            task = Task(run_mode="serial", status=status)
            db.add(task)
        db.commit()

        resp = client.get("/api/v1/admin/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tasks"]["total"] == 5
        assert data["tasks"]["queued"] == 1
        assert data["tasks"]["running"] == 1
        assert data["tasks"]["completed"] == 2
        assert data["tasks"]["failed"] == 1


class TestHealth:
    """健康检查 API 测试。"""

    def test_health_check(self, client: TestClient):
        """健康检查应返回数据库组件状态。"""
        resp = client.get("/api/v1/admin/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("healthy", "degraded", "unhealthy")
        assert len(data["components"]) >= 1
        db_comp = next(c for c in data["components"] if c["name"] == "database")
        assert db_comp["status"] == "healthy"

    def test_health_hides_database_exception_and_rolls_back(self):
        from types import SimpleNamespace

        from evalplat.api.routes.admin import get_health

        class BrokenSession:
            bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

            def __init__(self):
                self.rolled_back = False

            def execute(self, _statement):
                raise RuntimeError("postgresql://admin:secret@example.internal")

            def rollback(self):
                self.rolled_back = True

        session = BrokenSession()
        response = get_health(session)

        assert response.status == "unhealthy"
        database = next(item for item in response.components if item.name == "database")
        assert database.detail == "数据库检查失败"
        assert "secret" not in str(response)
        assert session.rolled_back is True


class TestSettings:
    """系统设置 API 测试。"""

    def test_list_settings_empty(self, client: TestClient):
        """无设置时返回空列表。"""
        resp = client.get("/api/v1/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_create_setting_via_patch(self, client: TestClient):
        """PATCH 不存在的 key 应创建新设置。"""
        resp = client.patch("/api/v1/settings/test_key", json={"value": {"foo": "bar"}})
        assert resp.status_code == 200
        data = resp.json()
        assert data["key"] == "test_key"
        assert data["value"] == {"foo": "bar"}

    def test_update_existing_setting(self, client: TestClient):
        """PATCH 已存在的 key 应更新值。"""
        client.patch("/api/v1/settings/my_key", json={"value": {"v": 1}})
        resp = client.patch("/api/v1/settings/my_key", json={"value": {"v": 2}})
        assert resp.status_code == 200
        data = resp.json()
        assert data["value"] == {"v": 2}

    def test_list_settings_after_create(self, client: TestClient):
        """创建设置后列表应包含它。"""
        client.patch("/api/v1/settings/s1", json={"value": {"a": 1}})
        client.patch("/api/v1/settings/s2", json={"value": {"b": 2}})

        resp = client.get("/api/v1/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        keys = [item["key"] for item in data["items"]]
        assert "s1" in keys
        assert "s2" in keys
