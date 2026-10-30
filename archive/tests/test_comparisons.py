"""结果对比 API 测试（Phase 8）。"""

import uuid

from fastapi.testclient import TestClient


def _create_run(db, task_id=None, model_deployment_id=None, status="completed", snapshot=None):
    """辅助：在 DB 中创建一个 Run。"""
    from evalplat.models.run import Run

    run = Run(
        task_id=task_id or uuid.uuid4(),
        model_deployment_id=model_deployment_id or uuid.uuid4(),
        status=status,
        run_snapshot=snapshot,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


class TestComparisons:
    """对比 API 测试。"""

    def test_compare_comparable_runs(self, client: TestClient, db):
        """两个使用相同数据集和 Tokenizer 的 Run 应该是可比的。"""
        ds_id = str(uuid.uuid4())
        tok_id = str(uuid.uuid4())
        snapshot = {"dataset_id": ds_id, "tokenizer_id": tok_id, "params": {"temperature": 0.7}}

        run1 = _create_run(db, snapshot=snapshot)
        run2 = _create_run(db, snapshot=snapshot)

        resp = client.post("/api/v1/comparisons", json={"run_ids": [str(run1.id), str(run2.id)]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["comparability"] == "comparable"
        assert data["differences"] == []
        assert len(data["runs"]) == 2

    def test_compare_redacts_snapshot_paths(self, client: TestClient, db):
        snapshot = {
            "dataset_snapshot": {"storage_path": r"C:\\srv\\data\\secret.jsonl"},
            "tokenizer_snapshot": {"local_path": r"C:\\srv\\tok-secret"},
        }
        run1 = _create_run(db, snapshot=snapshot)
        run2 = _create_run(db, snapshot=snapshot)

        response = client.post("/api/v1/comparisons", json={"run_ids": [str(run1.id), str(run2.id)]})

        assert response.status_code == 200
        public_snapshot = response.json()["runs"][0]["run_snapshot"]
        assert public_snapshot["dataset_snapshot"]["storage_path"] == "secret.jsonl"
        assert public_snapshot["tokenizer_snapshot"]["local_path"] == "tok-secret"

    def test_compare_different_datasets(self, client: TestClient, db):
        """使用不同数据集的 Run 应该不可比。"""
        run1 = _create_run(db, snapshot={"dataset_id": str(uuid.uuid4()), "tokenizer_id": "tok1"})
        run2 = _create_run(db, snapshot={"dataset_id": str(uuid.uuid4()), "tokenizer_id": "tok1"})

        resp = client.post("/api/v1/comparisons", json={"run_ids": [str(run1.id), str(run2.id)]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["comparability"] == "not_comparable"
        assert any(d["dimension"] == "dataset" for d in data["differences"])

    def test_compare_different_tokenizers(self, client: TestClient, db):
        """使用不同 Tokenizer 的 Run 应该不可比。"""
        ds_id = str(uuid.uuid4())
        run1 = _create_run(db, snapshot={"dataset_id": ds_id, "tokenizer_id": "tok_a"})
        run2 = _create_run(db, snapshot={"dataset_id": ds_id, "tokenizer_id": "tok_b"})

        resp = client.post("/api/v1/comparisons", json={"run_ids": [str(run1.id), str(run2.id)]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["comparability"] == "not_comparable"
        assert any(d["dimension"] == "tokenizer" for d in data["differences"])

    def test_compare_nonexistent_run(self, client: TestClient, db):
        """对比不存在的 Run 应返回 404。"""
        run1 = _create_run(db)
        fake_id = str(uuid.uuid4())

        resp = client.post("/api/v1/comparisons", json={"run_ids": [str(run1.id), fake_id]})
        assert resp.status_code == 404

    def test_compare_too_few_runs(self, client: TestClient):
        """只传 1 个 Run ID 应返回 422。"""
        resp = client.post("/api/v1/comparisons", json={"run_ids": [str(uuid.uuid4())]})
        assert resp.status_code == 422

    def test_compare_different_params(self, client: TestClient, db):
        """使用不同 temperature 的 Run 应标记参数差异。"""
        ds_id = str(uuid.uuid4())
        tok_id = str(uuid.uuid4())
        run1 = _create_run(db, snapshot={"dataset_id": ds_id, "tokenizer_id": tok_id, "params": {"temperature": 0.7}})
        run2 = _create_run(db, snapshot={"dataset_id": ds_id, "tokenizer_id": tok_id, "params": {"temperature": 1.0}})

        resp = client.post("/api/v1/comparisons", json={"run_ids": [str(run1.id), str(run2.id)]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["comparability"] == "not_comparable"
        assert any(d["dimension"] == "params.temperature" for d in data["differences"])

    def test_compare_uses_current_task_snapshot_keys(self, client: TestClient, db):
        snapshot = {
            "dataset_version_id": str(uuid.uuid4()),
            "tokenizer_id": str(uuid.uuid4()),
            "generation_params": {"temperature": 0.2},
            "load_profile": {"mode": "fixed_rps", "rps": 2},
            "stream": True,
        }
        run1 = _create_run(db, snapshot=snapshot)
        run2 = _create_run(db, snapshot={**snapshot, "generation_params": {"temperature": 0.7}})

        response = client.post("/api/v1/comparisons", json={"run_ids": [str(run1.id), str(run2.id)]})

        assert response.status_code == 200
        data = response.json()
        assert data["comparability"] == "not_comparable"
        assert any(d["dimension"] == "params.temperature" for d in data["differences"])

    def test_compare_checks_execution_scenario_and_resource_revisions(self, client: TestClient, db):
        """Strict comparability must include scenario, request phases and checksums."""
        dataset_id = str(uuid.uuid4())
        tokenizer_id = str(uuid.uuid4())
        base = {
            "dataset_version_id": dataset_id,
            "dataset_snapshot": {"id": dataset_id, "version": 1, "checksum": "ds-a", "kind": "performance"},
            "tokenizer_id": tokenizer_id,
            "tokenizer_snapshot": {"id": tokenizer_id, "revision": "r1", "checksum": "tok-a"},
            "test_type": "performance",
            "scenario": "baseline",
            "request_count": 10,
            "warmup_count": 2,
            "messages": [{"role": "user", "content": "hello"}],
        }
        run1 = _create_run(db, snapshot=base)
        run2 = _create_run(
            db,
            snapshot={
                **base,
                "scenario": "stability",
                "warmup_count": 3,
                "tokenizer_snapshot": {"id": tokenizer_id, "revision": "r2", "checksum": "tok-b"},
            },
        )

        response = client.post("/api/v1/comparisons", json={"run_ids": [str(run1.id), str(run2.id)]})

        assert response.status_code == 200
        dimensions = {item["dimension"] for item in response.json()["differences"]}
        assert {"scenario", "warmup_count", "tokenizer"}.issubset(dimensions)
