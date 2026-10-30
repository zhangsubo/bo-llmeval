"""连接 CRUD 测试。"""

import pytest


class TestConnectionsCRUD:
    def _create_supplier(self, client):
        resp = client.post("/api/v1/suppliers", json={"name": "Test", "short_name": "t", "type": "原厂"})
        return resp.json()["id"]

    def test_create_connection(self, client):
        sid = self._create_supplier(client)
        resp = client.post(f"/api/v1/suppliers/{sid}/connections", json={
            "name": "TestConn",
            "base_url": "https://api.openai.com",
            "api_key": "sk-test123abc456def789",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "TestConn"
        assert "api_key_masked" in data
        assert data["api_key_masked"] != "sk-test123abc456def789"  # 不是明文
        assert "***" in data["api_key_masked"]  # 是掩码
        assert data["credential_version"] == 1

    def test_connection_key_masked_in_list(self, client):
        sid = self._create_supplier(client)
        client.post(f"/api/v1/suppliers/{sid}/connections", json={
            "name": "C1", "base_url": "https://api.openai.com", "api_key": "sk-abc123def456"
        })
        resp = client.get(f"/api/v1/suppliers/{sid}/connections")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) >= 1
        assert "***" in items[0]["api_key_masked"]

    def test_global_connection_list_is_paginated_and_excludes_archived_by_default(self, client):
        sid = self._create_supplier(client)
        first = client.post(f"/api/v1/suppliers/{sid}/connections", json={
            "name": "active", "base_url": "https://api.openai.com", "api_key": "sk-active"
        }).json()["id"]
        archived = client.post(f"/api/v1/suppliers/{sid}/connections", json={
            "name": "archived", "base_url": "https://api.anthropic.com", "api_key": "sk-archived"
        }).json()["id"]
        assert client.delete(f"/api/v1/connections/{archived}").status_code == 204

        response = client.get("/api/v1/connections", params={"limit": 1})
        assert response.status_code == 200
        assert response.json()["total"] == 1
        assert response.json()["items"][0]["id"] == first

        archived_response = client.get(
            "/api/v1/connections",
            params={"status": "archived", "limit": 1},
        )
        assert archived_response.json()["total"] == 1
        assert archived_response.json()["items"][0]["id"] == archived

    def test_create_connection_normalizes_url(self, client):
        sid = self._create_supplier(client)
        resp = client.post(f"/api/v1/suppliers/{sid}/connections", json={
            "name": "C1", "base_url": "https://api.openai.com/v1/", "api_key": "sk-test"
        })
        assert resp.status_code == 201
        assert resp.json()["base_url"] == "https://api.openai.com"  # 去掉了 /v1/

    def test_create_connection_rejects_userinfo(self, client):
        sid = self._create_supplier(client)
        resp = client.post(f"/api/v1/suppliers/{sid}/connections", json={
            "name": "C1", "base_url": "http://user:pass@api.openai.com", "api_key": "sk-test"
        })
        assert resp.status_code in (400, 422)

    def test_create_connection_rejects_non_http(self, client):
        sid = self._create_supplier(client)
        resp = client.post(f"/api/v1/suppliers/{sid}/connections", json={
            "name": "C1", "base_url": "ftp://files.example.com", "api_key": "sk-test"
        })
        assert resp.status_code in (400, 422)

    def test_create_connection_preserves_ipv6_url_authority(self, client, monkeypatch):
        from unittest.mock import patch

        sid = self._create_supplier(client)
        with patch(
            "socket.getaddrinfo",
            return_value=[(10, 1, 6, "", ("2606:4700:4700::1111", 443, 0, 0))],
        ):
            response = client.post(
                f"/api/v1/suppliers/{sid}/connections",
                json={
                    "name": "IPv6",
                    "base_url": "https://[2606:4700:4700::1111]:443/v1/",
                    "api_key": "sk-test",
                },
            )
        assert response.status_code == 201
        assert response.json()["base_url"] == "https://[2606:4700:4700::1111]:443"

    def test_delete_soft_archives_connection(self, client):
        sid = self._create_supplier(client)
        create_resp = client.post(f"/api/v1/suppliers/{sid}/connections", json={
            "name": "C1", "base_url": "https://api.openai.com", "api_key": "sk-test"
        })
        cid = create_resp.json()["id"]
        resp = client.delete(f"/api/v1/connections/{cid}")
        assert resp.status_code == 204

        get_resp = client.get(f"/api/v1/connections/{cid}")
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "archived"

    def test_update_connection(self, client):
        sid = self._create_supplier(client)
        create_resp = client.post(f"/api/v1/suppliers/{sid}/connections", json={
            "name": "C1", "base_url": "https://api.openai.com", "api_key": "sk-test"
        })
        cid = create_resp.json()["id"]
        resp = client.patch(f"/api/v1/connections/{cid}", json={"name": "Updated"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated"

    def test_update_connection_can_clear_optional_configuration(self, client):
        sid = self._create_supplier(client)
        created = client.post(
            f"/api/v1/suppliers/{sid}/connections",
            json={
                "name": "C1",
                "base_url": "https://api.openai.com",
                "api_key": "sk-test",
                "region": "us-east-1",
                "service_tier": "enterprise",
                "timeouts": {"read": 120},
                "limits": {"rps": 2},
                "remark": "temporary",
            },
        )
        cid = created.json()["id"]

        response = client.patch(
            f"/api/v1/connections/{cid}",
            json={
                "region": None,
                "service_tier": None,
                "timeouts": None,
                "limits": None,
                "remark": None,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["region"] is None
        assert data["service_tier"] is None
        assert data["timeouts"] is None
        assert data["limits"] is None
        assert data["remark"] is None

    def test_create_connection_for_nonexistent_supplier(self, client):
        resp = client.post("/api/v1/suppliers/00000000-0000-0000-0000-000000000000/connections", json={
            "name": "C1", "base_url": "https://api.openai.com", "api_key": "sk-test"
        })
        assert resp.status_code == 404

    def test_tls_skip_verify_requires_explicit_confirmation(self, client):
        sid = self._create_supplier(client)
        payload = {
            "name": "不安全连接",
            "base_url": "https://api.openai.com",
            "api_key": "sk-test",
            "tls_skip_verify": True,
        }
        rejected = client.post(f"/api/v1/suppliers/{sid}/connections", json=payload)
        assert rejected.status_code == 400

        accepted = client.post(
            f"/api/v1/suppliers/{sid}/connections",
            json={**payload, "tls_skip_verify_confirm": True},
        )
        assert accepted.status_code == 201
        assert accepted.json()["tls_skip_verify"] is True

    def test_enabling_tls_skip_verify_on_update_requires_confirmation(self, client):
        sid = self._create_supplier(client)
        created = client.post(
            f"/api/v1/suppliers/{sid}/connections",
            json={"name": "C1", "base_url": "https://api.openai.com", "api_key": "sk-test"},
        )
        cid = created.json()["id"]

        rejected = client.patch(f"/api/v1/connections/{cid}", json={"tls_skip_verify": True})
        assert rejected.status_code == 400
        accepted = client.patch(
            f"/api/v1/connections/{cid}",
            json={"tls_skip_verify": True, "tls_skip_verify_confirm": True},
        )
        assert accepted.status_code == 200
        assert accepted.json()["tls_skip_verify"] is True

    def test_connection_limits_are_validated_at_write_boundary(self, client):
        sid = self._create_supplier(client)
        rejected = client.post(
            f"/api/v1/suppliers/{sid}/connections",
            json={
                "name": "坏限额",
                "base_url": "https://api.openai.com",
                "api_key": "sk-test",
                "limits": {"max_concurrency": 0},
            },
        )
        assert rejected.status_code == 400

        accepted = client.post(
            f"/api/v1/suppliers/{sid}/connections",
            json={
                "name": "好限额",
                "base_url": "https://api.openai.com",
                "api_key": "sk-test",
                "limits": {"max_concurrency": 2, "tpm": "1000"},
            },
        )
        assert accepted.status_code == 201

    def test_update_connection_rejects_invalid_status(self, client):
        sid = self._create_supplier(client)
        created = client.post(
            f"/api/v1/suppliers/{sid}/connections",
            json={"name": "C1", "base_url": "https://api.openai.com", "api_key": "sk-test"},
        )
        cid = created.json()["id"]

        rejected = client.patch(f"/api/v1/connections/{cid}", json={"status": "unknown"})

        assert rejected.status_code == 422

    def test_update_connection_service_rejects_base_url_without_ssrf_revalidation(self, db):
        from evalplat.models.connection import Connection
        from evalplat.models.supplier import Supplier
        from evalplat.services.connection_service import update_connection

        supplier = Supplier(name="供应商", short_name="sup", type="other")
        db.add(supplier)
        db.flush()
        connection = Connection(
            supplier_id=supplier.id,
            name="连接",
            base_url="https://api.example.com",
        )
        db.add(connection)
        db.commit()

        with pytest.raises(ValueError, match="Base URL"):
            update_connection(db, connection.id, base_url="http://127.0.0.1")

    def test_model_creation_rejects_missing_connection(self, client):
        response = client.post(
            "/api/v1/connections/00000000-0000-0000-0000-000000000000/models",
            json={"model_id": "gpt-4o"},
        )
        assert response.status_code == 404
