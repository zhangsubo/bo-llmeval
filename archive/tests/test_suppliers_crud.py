"""供应商 CRUD 测试。"""

import pytest


class TestSuppliersCRUD:
    def test_create_supplier(self, client):
        resp = client.post("/api/v1/suppliers", json={
            "name": "OpenAI", "short_name": "oai", "type": "原厂"
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "OpenAI"
        assert data["status"] == "active"
        assert "id" in data

    def test_list_suppliers(self, client):
        client.post("/api/v1/suppliers", json={"name": "A", "short_name": "a", "type": "原厂"})
        client.post("/api/v1/suppliers", json={"name": "B", "short_name": "b", "type": "云厂商"})
        resp = client.get("/api/v1/suppliers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 2
        assert len(data["items"]) >= 2

    def test_get_supplier(self, client):
        create_resp = client.post("/api/v1/suppliers", json={"name": "Test", "short_name": "t", "type": "原厂"})
        sid = create_resp.json()["id"]
        resp = client.get(f"/api/v1/suppliers/{sid}")
        assert resp.status_code == 200
        assert resp.json()["id"] == sid

    def test_update_supplier(self, client):
        create_resp = client.post("/api/v1/suppliers", json={"name": "Test", "short_name": "t", "type": "原厂"})
        sid = create_resp.json()["id"]
        resp = client.patch(f"/api/v1/suppliers/{sid}", json={"status": "disabled"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "disabled"

    def test_update_supplier_can_clear_remark(self, client):
        sid = client.post(
            "/api/v1/suppliers",
            json={"name": "Test", "short_name": "t", "type": "原厂", "remark": "temporary"},
        ).json()["id"]
        response = client.patch(f"/api/v1/suppliers/{sid}", json={"remark": None})
        assert response.status_code == 200
        assert response.json()["remark"] is None

    def test_update_supplier_rejects_invalid_status(self, client):
        sid = client.post(
            "/api/v1/suppliers",
            json={"name": "OpenAI", "short_name": "oai", "type": "原厂"},
        ).json()["id"]
        response = client.patch(f"/api/v1/suppliers/{sid}", json={"status": "unknown"})
        assert response.status_code == 422

    def test_delete_returns_405(self, client):
        create_resp = client.post("/api/v1/suppliers", json={"name": "Test", "short_name": "t", "type": "原厂"})
        sid = create_resp.json()["id"]
        resp = client.delete(f"/api/v1/suppliers/{sid}")
        assert resp.status_code == 405

    def test_missing_fields_422(self, client):
        resp = client.post("/api/v1/suppliers", json={"name": "Test"})
        assert resp.status_code == 422

    def test_not_found_404(self, client):
        resp = client.get("/api/v1/suppliers/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_list_with_keyword_filter(self, client):
        client.post("/api/v1/suppliers", json={"name": "OpenAI", "short_name": "oai", "type": "原厂"})
        client.post("/api/v1/suppliers", json={"name": "Anthropic", "short_name": "ant", "type": "原厂"})
        resp = client.get("/api/v1/suppliers?keyword=Open")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        assert resp.json()["items"][0]["name"] == "OpenAI"
