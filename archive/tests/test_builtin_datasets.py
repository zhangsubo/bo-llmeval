from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

from evalplat.models.dataset import Dataset, DatasetVersion
from evalplat.services.builtin_dataset_service import get_builtin_dataset
from evalplat.services.dataset_service import (
    builtin_catalog,
    download_builtin_dataset,
    load_samples,
)


class FakeProvider:
    def __init__(self, rows=None, error: Exception | None = None):
        self.rows = rows or []
        self.error = error
        self.calls = 0

    def iter_rows(self, spec):
        self.calls += 1
        if self.error:
            raise self.error
        yield from self.rows


def test_builtin_catalog_contains_all_required_quality_datasets():
    catalog = builtin_catalog()

    assert {item["key"] for item in catalog} == {
        "ifeval",
        "ceval",
        "mmlu",
        "gsm8k",
        "mgsm",
        "humaneval",
        "mbpp",
    }
    assert all(item["license"] for item in catalog)
    assert all(item["source_id"] for item in catalog)


def test_builtin_download_normalizes_rows_and_is_idempotent(db, tmp_path: Path):
    provider = FakeProvider(
        rows=[
            {"question": "2+2?", "answer_number": 4},
            {"prompt": "def add(a, b):", "test": "assert add(1, 2) == 3"},
        ]
    )

    version = download_builtin_dataset(db, "gsm8k", tmp_path, provider=provider)

    assert version.status == "ready"
    assert version.source_id == "AI-ModelScope/gsm8k"
    assert version.source_split == "test"
    assert version.sample_count == 2
    assert version.checksum
    assert version.error is None
    assert Path(version.storage_path).is_file()
    assert db.query(Dataset).filter(Dataset.builtin_key == "gsm8k").count() == 1

    samples = load_samples(version, storage_root=tmp_path)
    assert samples[0]["messages"] == [{"role": "user", "content": "2+2?"}]
    assert samples[0]["expected"] == 4
    assert samples[0]["row"]["builtin_dataset"] == "gsm8k"

    same_version = download_builtin_dataset(db, "gsm8k", tmp_path, provider=provider)
    assert same_version.id == version.id
    assert provider.calls == 1


def test_builtin_force_download_creates_a_new_immutable_version(db, tmp_path: Path):
    provider = FakeProvider(rows=[{"prompt": "hello", "answer": "world"}])

    first = download_builtin_dataset(db, "ifeval", tmp_path, provider=provider)
    second = download_builtin_dataset(db, "ifeval", tmp_path, provider=provider, force=True)

    assert first.id != second.id
    assert (first.version, second.version) == (1, 2)
    assert first.status == second.status == "ready"
    assert provider.calls == 2
    assert db.query(DatasetVersion).filter(DatasetVersion.source_id == "opencompass/ifeval").count() == 2


def test_builtin_download_failure_is_durable_and_retryable(db, tmp_path: Path):
    failing = FakeProvider(error=RuntimeError("upstream unavailable"))

    with pytest.raises(RuntimeError, match="upstream unavailable"):
        download_builtin_dataset(db, "mbpp", tmp_path, provider=failing)

    failed = db.query(DatasetVersion).filter(DatasetVersion.source_id == "google-research-datasets/mbpp").one()
    assert failed.status == "failed"
    assert failed.error == "upstream unavailable"
    assert failed.storage_path is None
    assert list(tmp_path.iterdir()) == []

    succeeding = FakeProvider(rows=[{"text": "return 1", "test_list": ["assert True"]}])
    recovered = download_builtin_dataset(db, "mbpp", tmp_path, provider=succeeding)
    assert recovered.status == "ready"
    assert recovered.version == failed.version + 1
    assert succeeding.calls == 1


def test_builtin_download_rejects_unknown_key(db, tmp_path: Path):
    with pytest.raises(ValueError, match="不支持的内置数据集"):
        download_builtin_dataset(db, "unknown", tmp_path)


def test_stale_builtin_processing_claim_can_be_retried(db, tmp_path: Path):
    dataset = Dataset(
        name="MBPP", kind="quality", is_builtin=True, builtin_key="mbpp",
    )
    db.add(dataset)
    db.flush()
    stale = DatasetVersion(
        dataset_id=dataset.id,
        version=1,
        source="modelscope",
        source_id="google-research-datasets/mbpp",
        status="processing",
    )
    db.add(stale)
    db.commit()
    orphan = tmp_path / "builtin-mbpp-v1.jsonl"
    orphan.write_text("orphan\n", encoding="utf-8")
    stale.storage_path = str(orphan)
    stale.updated_at = datetime.now(timezone.utc) - timedelta(hours=2)
    db.commit()

    result = download_builtin_dataset(
        db,
        "mbpp",
        tmp_path,
        provider=FakeProvider(rows=[{"text": "return 1", "test": "assert True"}]),
    )

    db.refresh(stale)
    assert stale.status == "failed"
    assert result.status == "ready"
    assert result.version == 2
    assert not orphan.exists()


def test_builtin_provider_uses_evalscope_metadata():
    spec = get_builtin_dataset("humaneval")

    assert spec.evalscope_name == "humaneval"
    assert spec.source == "modelscope"
    assert spec.subset == "openai_humaneval"
