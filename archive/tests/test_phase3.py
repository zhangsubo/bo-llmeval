"""Phase 3 测试：Tokenizer、数据集、模板管理。"""

import uuid

import pytest

from evalplat.models.dataset import DatasetVersion
from evalplat.services.dataset_service import update_version_status


class TestTokenizerCRUD:
    def test_create_tokenizer_rejects_invalid_source(self, client):
        response = client.post("/api/v1/tokenizers", json={"name": "bad", "source": "unknown"})
        assert response.status_code == 422

    def test_create_tokenizer(self, client):
        resp = client.post("/api/v1/tokenizers", json={"name": "gpt-4o-tokenizer", "source": "huggingface", "resource_id": "openai/gpt-4o"})
        assert resp.status_code == 201
        assert resp.json()["status"] == "pending"
        assert resp.json()["source"] == "huggingface"

    def test_list_tokenizers(self, client):
        client.post("/api/v1/tokenizers", json={"name": "T1", "source": "builtin"})
        client.post("/api/v1/tokenizers", json={"name": "T2", "source": "upload"})
        resp = client.get("/api/v1/tokenizers")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 2

    def test_list_tokenizers_supports_keyword(self, client):
        client.post("/api/v1/tokenizers", json={"name": "中文 tokenizer", "source": "builtin"})
        client.post("/api/v1/tokenizers", json={"name": "English tokenizer", "source": "builtin"})
        response = client.get("/api/v1/tokenizers?keyword=中文")
        assert response.status_code == 200
        assert response.json()["total"] == 1

    def test_update_status(self, client):
        tid = client.post("/api/v1/tokenizers", json={"name": "T1", "source": "builtin"}).json()["id"]
        resp = client.patch(f"/api/v1/tokenizers/{tid}/status?status=ready")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"

    def test_update_status_with_error(self, client):
        tid = client.post("/api/v1/tokenizers", json={"name": "T1", "source": "modelscope"}).json()["id"]
        resp = client.patch(f"/api/v1/tokenizers/{tid}/status?status=failed&error=download_timeout")
        assert resp.status_code == 200
        assert resp.json()["status"] == "failed"
        assert resp.json()["error"] == "download_timeout"


class TestDatasetCRUD:
    def test_create_dataset_rejects_invalid_kind(self, client):
        response = client.post("/api/v1/datasets", json={"name": "bad", "kind": "unknown"})
        assert response.status_code == 422

    def test_builtin_catalog(self, client):
        resp = client.get("/api/v1/datasets/builtins/catalog")
        assert resp.status_code == 200
        assert {item["key"] for item in resp.json()["items"]} == {
            "ifeval", "ceval", "mmlu", "gsm8k", "mgsm", "humaneval", "mbpp"
        }

    def test_create_dataset(self, client):
        resp = client.post("/api/v1/datasets", json={"name": "MMLU", "kind": "quality"})
        assert resp.status_code == 201
        assert resp.json()["kind"] == "quality"

    def test_list_datasets(self, client):
        client.post("/api/v1/datasets", json={"name": "D1", "kind": "quality"})
        client.post("/api/v1/datasets", json={"name": "D2", "kind": "performance"})
        resp = client.get("/api/v1/datasets?kind=quality")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_list_datasets_supports_keyword(self, client):
        client.post("/api/v1/datasets", json={"name": "MMLU 中文", "kind": "quality"})
        client.post("/api/v1/datasets", json={"name": "GSM8K", "kind": "quality"})
        resp = client.get("/api/v1/datasets?keyword=MMLU")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_create_version(self, client):
        ds = client.post("/api/v1/datasets", json={"name": "MMLU", "kind": "quality"}).json()
        resp = client.post(f"/api/v1/datasets/{ds['id']}/versions", json={
            "source": "huggingface", "sample_count": 1000
        })
        assert resp.status_code == 201
        assert resp.json()["version"] == 1
        assert resp.json()["status"] == "pending"

    def test_create_version_rejects_ready_without_published_file(self, client):
        ds = client.post("/api/v1/datasets", json={"name": "MMLU", "kind": "quality"}).json()
        resp = client.post(f"/api/v1/datasets/{ds['id']}/versions", json={"status": "ready"})
        assert resp.status_code == 400
        assert "pending" in resp.json()["error"]["message"]

    def test_list_dataset_versions_across_datasets(self, client):
        first = client.post("/api/v1/datasets", json={"name": "MMLU", "kind": "quality"}).json()
        second = client.post("/api/v1/datasets", json={"name": "GSM8K", "kind": "quality"}).json()
        first_version = client.post(f"/api/v1/datasets/{first['id']}/versions", json={}).json()
        client.post(f"/api/v1/datasets/{second['id']}/versions", json={})
        response = client.get("/api/v1/dataset-versions?keyword=MMLU")
        assert response.status_code == 200
        assert response.json()["total"] == 1
        assert response.json()["items"][0]["id"] == first_version["id"]

    def test_version_auto_increment(self, client):
        ds = client.post("/api/v1/datasets", json={"name": "D1", "kind": "quality"}).json()
        client.post(f"/api/v1/datasets/{ds['id']}/versions", json={})
        resp = client.post(f"/api/v1/datasets/{ds['id']}/versions", json={})
        assert resp.json()["version"] == 2

    def test_list_versions(self, client):
        ds = client.post("/api/v1/datasets", json={"name": "D1", "kind": "quality"}).json()
        for _ in range(3):
            client.post(f"/api/v1/datasets/{ds['id']}/versions", json={})
        resp = client.get(f"/api/v1/datasets/{ds['id']}/versions")
        assert resp.json()["total"] == 3

    def test_update_version_status_rejects_unknown_value(self, db):
        version = DatasetVersion(
            dataset_id=uuid.uuid4(), version=1, status="pending"
        )
        # The service should reject the value before trying to write a row.
        with pytest.raises(ValueError, match="状态无效"):
            update_version_status(db, version.id, "unknown")


class TestTemplateCRUD:
    def test_create_template(self, client):
        resp = client.post("/api/v1/templates", json={"name": "冒烟测试", "is_builtin": True})
        assert resp.status_code == 201
        assert resp.json()["is_builtin"] is True

    def test_list_templates(self, client):
        client.post("/api/v1/templates", json={"name": "T1", "is_builtin": True})
        client.post("/api/v1/templates", json={"name": "T2", "is_builtin": False})
        resp = client.get("/api/v1/templates?is_builtin=true")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_list_templates_supports_keyword(self, client):
        client.post("/api/v1/templates", json={"name": "标准基线", "is_builtin": False})
        client.post("/api/v1/templates", json={"name": "轻量质量", "is_builtin": False})
        response = client.get("/api/v1/templates?keyword=质量")
        assert response.status_code == 200
        assert response.json()["total"] == 1

    def test_create_version(self, client):
        tpl = client.post("/api/v1/templates", json={"name": "Custom", "is_builtin": False}).json()
        resp = client.post(f"/api/v1/templates/{tpl['id']}/versions", json={
            "test_type": "performance", "scenario": "baseline", "request_count": 100
        })
        assert resp.status_code == 201
        assert resp.json()["version"] == 1

    def test_builtin_template_rejects_direct_edit(self, client):
        tpl = client.post("/api/v1/templates", json={"name": "内置", "is_builtin": True}).json()
        resp = client.post(f"/api/v1/templates/{tpl['id']}/versions", json={"test_type": "perf"})
        assert resp.status_code == 409  # ConflictError

    def test_list_template_versions(self, client):
        tpl = client.post(
            "/api/v1/templates", json={"name": "可查询模板", "is_builtin": False}
        ).json()
        client.post(
            f"/api/v1/templates/{tpl['id']}/versions",
            json={"test_type": "performance", "request_count": 1},
        )
        client.post(
            f"/api/v1/templates/{tpl['id']}/versions",
            json={"test_type": "performance", "request_count": 2},
        )
        resp = client.get(f"/api/v1/templates/{tpl['id']}/versions")
        assert resp.status_code == 200
        assert resp.json()["total"] == 2
        assert [item["version"] for item in resp.json()["items"]] == [2, 1]

    def test_list_template_versions_across_templates(self, client):
        first = client.post("/api/v1/templates", json={"name": "标准基线"}).json()
        second = client.post("/api/v1/templates", json={"name": "轻量质量"}).json()
        first_version = client.post(
            f"/api/v1/templates/{first['id']}/versions", json={"scenario": "baseline"}
        ).json()
        client.post(f"/api/v1/templates/{second['id']}/versions", json={"scenario": "quality"})
        response = client.get("/api/v1/template-versions?keyword=baseline")
        assert response.status_code == 200
        assert response.json()["total"] == 1
        assert response.json()["items"][0]["id"] == first_version["id"]

    def test_copy_template(self, client):
        tpl = client.post("/api/v1/templates", json={"name": "内置冒烟", "is_builtin": True}).json()
        client.post(f"/api/v1/templates/{tpl['id']}/versions", json={"test_type": "smoke", "request_count": 5})
        resp = client.post(f"/api/v1/templates/{tpl['id']}/copy", json={"new_name": "自定义冒烟"})
        assert resp.status_code == 201
        assert resp.json()["name"] == "自定义冒烟"
        assert resp.json()["is_builtin"] is False
