"""对比组 CRUD 测试。"""

import pytest


class TestComparisonGroupsCRUD:
    def test_create_group(self, client):
        resp = client.post("/api/v1/comparison-groups", json={"name": "GPT 系列对比"})
        assert resp.status_code == 201
        assert resp.json()["name"] == "GPT 系列对比"

    def test_list_groups(self, client):
        client.post("/api/v1/comparison-groups", json={"name": "G1"})
        client.post("/api/v1/comparison-groups", json={"name": "G2"})
        resp = client.get("/api/v1/comparison-groups")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 2

    def test_get_group(self, client):
        gid = client.post("/api/v1/comparison-groups", json={"name": "G1"}).json()["id"]
        resp = client.get(f"/api/v1/comparison-groups/{gid}")
        assert resp.status_code == 200
        assert resp.json()["id"] == gid

    def test_update_group(self, client):
        gid = client.post("/api/v1/comparison-groups", json={"name": "G1"}).json()["id"]
        resp = client.patch(f"/api/v1/comparison-groups/{gid}", json={"name": "Updated"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated"

    def test_update_group_rejects_unknown_default_tokenizer(self, client):
        gid = client.post("/api/v1/comparison-groups", json={"name": "G1"}).json()["id"]
        resp = client.patch(
            f"/api/v1/comparison-groups/{gid}",
            json={"default_tokenizer_id": "00000000-0000-0000-0000-000000000000"},
        )
        assert resp.status_code == 404

    def test_members_are_listed_and_counted(self, client):
        supplier_id = client.post(
            "/api/v1/suppliers",
            json={"name": "Supplier", "short_name": "sup", "type": "原厂"},
        ).json()["id"]
        connection_id = client.post(
            f"/api/v1/suppliers/{supplier_id}/connections",
            json={
                "name": "Connection",
                "base_url": "https://api.openai.com",
                "api_key": "sk-test",
            },
        ).json()["id"]
        deployment_id = client.post(
            f"/api/v1/connections/{connection_id}/models",
            json={"model_id": "model-a"},
        ).json()["id"]
        group_id = client.post(
            "/api/v1/comparison-groups", json={"name": "Group"}
        ).json()["id"]

        assert client.post(
            f"/api/v1/comparison-groups/{group_id}/members",
            json={"model_deployment_id": deployment_id},
        ).status_code == 200

        groups = client.get("/api/v1/comparison-groups").json()
        group = next(item for item in groups["items"] if item["id"] == group_id)
        assert group["member_count"] == 1

        members = client.get(f"/api/v1/comparison-groups/{group_id}/members").json()
        assert members["total"] == 1
        assert members["items"][0]["id"] == deployment_id

        assert client.delete(
            f"/api/v1/comparison-groups/{group_id}/members/{deployment_id}"
        ).status_code == 200
        groups_after = client.get("/api/v1/comparison-groups").json()
        group_after = next(item for item in groups_after["items"] if item["id"] == group_id)
        assert group_after["member_count"] == 0

    def test_delete_returns_405(self, client):
        gid = client.post("/api/v1/comparison-groups", json={"name": "G1"}).json()["id"]
        resp = client.delete(f"/api/v1/comparison-groups/{gid}")
        assert resp.status_code == 405
