from pathlib import Path

import pytest

from evalplat.models.dataset import Dataset, DatasetVersion
from evalplat.services.dataset_service import load_samples, upload_version, validate_ready_version


def test_upload_jsonl_validates_entire_file_and_records_checksum(db, tmp_path: Path):
    dataset = Dataset(name="质量集", kind="quality")
    db.add(dataset)
    db.commit()

    version = upload_version(
        db,
        dataset.id,
        "samples.jsonl",
        b'{"messages": [], "expected": "ok"}\n{"messages": [], "expected": "yes"}\n',
        tmp_path,
    )

    assert version.status == "ready"
    assert version.sample_count == 2
    assert version.checksum is not None
    assert Path(version.storage_path).read_bytes().startswith(b"{")


def test_upload_rejects_malformed_row_without_creating_version_or_file(db, tmp_path: Path):
    dataset = Dataset(name="质量集", kind="quality")
    db.add(dataset)
    db.commit()

    with pytest.raises(ValueError, match="第 2 行"):
        upload_version(db, dataset.id, "bad.jsonl", b'{"ok": true}\nnot-json\n', tmp_path)

    assert list(tmp_path.iterdir()) == []
    assert db.query(DatasetVersion).count() == 0


def test_upload_csv_rejects_inconsistent_columns(db, tmp_path: Path):
    dataset = Dataset(name="质量集", kind="quality")
    db.add(dataset)
    db.commit()

    with pytest.raises(ValueError, match="列数不一致"):
        upload_version(db, dataset.id, "bad.csv", b"prompt,answer\nhello\n", tmp_path)


def test_load_samples_maps_prompt_and_answer_from_ready_version(db, tmp_path: Path):
    dataset = Dataset(name="质量集", kind="quality")
    db.add(dataset)
    db.commit()
    version = upload_version(
        db,
        dataset.id,
        "samples.jsonl",
        b'{"question":"2+2?","answer":"4"}\n',
        tmp_path,
        field_mapping={"prompt": "question", "answer": "answer"},
    )

    samples = load_samples(version, storage_root=tmp_path)

    assert samples[0]["messages"] == [{"role": "user", "content": "2+2?"}]
    assert samples[0]["expected"] == "4"


def test_load_samples_rejects_version_path_outside_dataset_root(db, tmp_path: Path):
    version = DatasetVersion(
        dataset_id=__import__("uuid").uuid4(),
        version=1,
        status="ready",
        storage_path=str(tmp_path.parent / "outside.jsonl"),
    )

    with pytest.raises(ValueError, match="dataset_dir"):
        load_samples(version, storage_root=tmp_path)


def test_validate_ready_version_requires_loadable_immutable_evidence(db, tmp_path: Path):
    dataset = Dataset(name="质量集", kind="quality")
    db.add(dataset)
    db.flush()
    path = tmp_path / "samples.jsonl"
    content = b'{"prompt":"ping","answer":"pong"}\n'
    path.write_bytes(content)
    version = DatasetVersion(
        dataset_id=dataset.id,
        version=1,
        status="ready",
        storage_path=str(path),
        checksum=__import__("hashlib").sha256(content).hexdigest(),
        sample_count=1,
    )
    assert validate_ready_version(version, storage_root=tmp_path) is None
    version.checksum = "0" * 64
    assert "checksum" in (validate_ready_version(version, storage_root=tmp_path) or "")
