"""价格 CRUD 测试。"""

import pytest


class TestPricesCRUD:
    def _create_supplier_connection_model(self, client):
        sid = client.post("/api/v1/suppliers", json={"name": "T", "short_name": "t", "type": "原厂"}).json()["id"]
        cid = client.post(f"/api/v1/suppliers/{sid}/connections", json={
            "name": "C1", "base_url": "https://api.openai.com", "api_key": "sk-test"
        }).json()["id"]
        mid = client.post(f"/api/v1/connections/{cid}/models", json={
            "model_id": "gpt-4o", "display_name": "GPT-4o"
        }).json()["id"]
        return mid

    def test_create_price(self, client):
        mid = self._create_supplier_connection_model(client)
        resp = client.post(f"/api/v1/model-deployments/{mid}/prices", json={
            "input_token_price": "0.005",
            "output_token_price": "0.015",
            "exchange_rate": "7.2",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["price_version"] == 1
        assert data["currency"] == "CNY"

    def test_price_version_increments(self, client):
        mid = self._create_supplier_connection_model(client)
        client.post(f"/api/v1/model-deployments/{mid}/prices", json={
            "input_token_price": "0.005", "output_token_price": "0.015", "exchange_rate": "7.2"
        })
        resp = client.post(f"/api/v1/model-deployments/{mid}/prices", json={
            "input_token_price": "0.006", "output_token_price": "0.018", "exchange_rate": "7.3"
        })
        assert resp.status_code == 201
        assert resp.json()["price_version"] == 2

    def test_reject_negative_price(self, client):
        mid = self._create_supplier_connection_model(client)
        resp = client.post(f"/api/v1/model-deployments/{mid}/prices", json={
            "input_token_price": "-0.005", "output_token_price": "0.015", "exchange_rate": "7.2"
        })
        assert resp.status_code == 422

    def test_reject_zero_exchange_rate(self, client):
        mid = self._create_supplier_connection_model(client)
        resp = client.post(f"/api/v1/model-deployments/{mid}/prices", json={
            "input_token_price": "0.005", "output_token_price": "0.015", "exchange_rate": "0"
        })
        assert resp.status_code == 422

    def test_list_prices_descending(self, client):
        mid = self._create_supplier_connection_model(client)
        for i in range(3):
            client.post(f"/api/v1/model-deployments/{mid}/prices", json={
                "input_token_price": f"0.00{i}", "output_token_price": f"0.01{i}", "exchange_rate": "7.2"
            })
        resp = client.get(f"/api/v1/model-deployments/{mid}/prices")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert items[0]["price_version"] == 3  # 最新在前

    def test_get_latest_price(self, client):
        mid = self._create_supplier_connection_model(client)
        client.post(f"/api/v1/model-deployments/{mid}/prices", json={
            "input_token_price": "0.005", "output_token_price": "0.015", "exchange_rate": "7.2"
        })
        client.post(f"/api/v1/model-deployments/{mid}/prices", json={
            "input_token_price": "0.006", "output_token_price": "0.018", "exchange_rate": "7.3"
        })
        resp = client.get(f"/api/v1/model-deployments/{mid}/prices/latest")
        assert resp.status_code == 200
        assert resp.json()["price_version"] == 2
