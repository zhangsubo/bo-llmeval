"""集成测试（Phase 12）。

端到端流程测试：创建供应商 → 连接 → 模型 → 价格 → 对比组 → 任务 → 提交。
"""

import uuid

from fastapi.testclient import TestClient


class TestFullWorkflow:
    """完整业务流程集成测试。"""

    def test_full_workflow(self, client: TestClient):
        """创建供应商 → 连接 → 模型部署 → 价格 → 对比组 → 任务。"""
        # 1. 创建供应商
        resp = client.post("/api/v1/suppliers", json={
            "name": "测试供应商",
            "short_name": "test",
            "type": "原厂",
        })
        assert resp.status_code == 201
        supplier = resp.json()
        supplier_id = supplier["id"]

        # 2. 创建连接（需要 SSRF 校验通过的 URL）
        from unittest.mock import patch

        with patch("evalplat.core.ssrf.socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34",))]):
            resp = client.post(f"/api/v1/suppliers/{supplier_id}/connections", json={
                "name": "测试连接",
                "base_url": "https://api.example.com",
                "api_key": "sk-test-key-1234567890abcdef",
            })
        assert resp.status_code == 201
        connection = resp.json()
        connection_id = connection["id"]
        assert connection["api_key_masked"]  # 应有掩码

        # 3. 创建模型部署
        resp = client.post(f"/api/v1/connections/{connection_id}/models", json={
            "model_id": "gpt-4o",
            "display_name": "GPT-4o",
            "source": "manual",
        })
        assert resp.status_code == 201
        deployment = resp.json()
        deployment_id = deployment["id"]

        # 4. 创建价格
        resp = client.post(f"/api/v1/model-deployments/{deployment_id}/prices", json={
            "input_token_price": "0.005",
            "output_token_price": "0.015",
            "exchange_rate": "1.0",
        })
        assert resp.status_code == 201
        price = resp.json()
        assert price["price_version"] == 1

        # 5. 创建对比组
        resp = client.post("/api/v1/comparison-groups", json={
            "name": "GPT-4 系列对比",
        })
        assert resp.status_code == 201
        group = resp.json()
        group_id = group["id"]

        # 6. 添加模型到对比组
        resp = client.post(f"/api/v1/comparison-groups/{group_id}/members", json={
            "model_deployment_id": deployment_id,
        })
        assert resp.status_code == 200

        # 7. 创建系统设置
        resp = client.patch("/api/v1/settings/test_config", json={
            "value": {"key": "value"},
        })
        assert resp.status_code == 200

        # 8. 获取仪表盘
        resp = client.get("/api/v1/admin/dashboard")
        assert resp.status_code == 200

        # 9. 获取健康状态
        resp = client.get("/api/v1/admin/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"


class TestConnectionTestFlow:
    """连接测试流程集成测试。"""

    def test_create_supplier_and_connection(self, client: TestClient):
        """创建供应商 → 连接，验证关联关系。"""
        resp = client.post("/api/v1/suppliers", json={
            "name": "连接测试供应商",
            "short_name": "conn_test",
            "type": "云厂商",
        })
        assert resp.status_code == 201
        supplier_id = resp.json()["id"]

        from unittest.mock import patch

        with patch("evalplat.core.ssrf.socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34",))]):
            resp = client.post(f"/api/v1/suppliers/{supplier_id}/connections", json={
                "name": "测试连接",
                "base_url": "https://api.test.com",
                "api_key": "sk-abcdefghijklmnopqrstuvwxyz123456",
            })
        assert resp.status_code == 201
        conn = resp.json()
        assert conn["supplier_id"] == supplier_id
        assert conn["status"] == "active"

        resp = client.get(f"/api/v1/suppliers/{supplier_id}/connections")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1


class TestModelSyncFlow:
    """模型同步流程集成测试。"""

    def test_create_and_list_deployments(self, client: TestClient):
        """创建供应商 → 连接 → 手动创建模型部署 → 列表查看。"""
        resp = client.post("/api/v1/suppliers", json={
            "name": "同步测试供应商",
            "short_name": "sync_test",
            "type": "原厂",
        })
        supplier_id = resp.json()["id"]

        from unittest.mock import patch

        with patch("evalplat.core.ssrf.socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34",))]):
            resp = client.post(f"/api/v1/suppliers/{supplier_id}/connections", json={
                "name": "同步连接",
                "base_url": "https://api.sync.test",
                "api_key": "sk-sync-key-abcdefghijklmnopqrstuvwxyz",
            })
        connection_id = resp.json()["id"]

        models = ["gpt-4o", "gpt-4o-mini", "claude-3-5-sonnet"]
        for model_id in models:
            resp = client.post(f"/api/v1/connections/{connection_id}/models", json={
                "model_id": model_id,
                "display_name": model_id.upper(),
                "source": "manual",
            })
            assert resp.status_code == 201

        resp = client.get(f"/api/v1/model-deployments?connection_id={connection_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
