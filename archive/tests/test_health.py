"""健康检查路由测试。"""

from fastapi.testclient import TestClient


def test_healthz_returns_200(client: TestClient) -> None:
    """healthz 返回 200 且 status=ok。"""
    resp = client.get("/api/v1/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    root_resp = client.get("/healthz")
    assert root_resp.status_code == 200
    assert root_resp.json() == {"status": "ok"}


def test_readyz_returns_200_with_latency(client: TestClient) -> None:
    """readyz 返回 200，包含 db_latency_ms。"""
    resp = client.get("/api/v1/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert isinstance(body["db_latency_ms"], (int, float))

    root_resp = client.get("/readyz")
    assert root_resp.status_code == 200
    root_body = root_resp.json()
    assert root_body["status"] == "ready"
    assert isinstance(root_body["db_latency_ms"], (int, float))


def test_error_structure_format(client: TestClient) -> None:
    """访问不存在的路由，验证统一错误 JSON 结构。"""
    resp = client.get("/api/v1/nonexistent")
    assert resp.status_code == 404
    body = resp.json()
    assert "error" in body
    assert "code" in body["error"]
    assert "message" in body["error"]
    assert "details" in body["error"]
