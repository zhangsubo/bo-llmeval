"""Tokenizer 缓存下载/上传边界测试。"""

import io
import sys
import types
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from evalplat.models.tokenizer import Tokenizer
from evalplat.services import tokenizer_service
from evalplat.core.errors import ConflictError


def test_builtin_tokenizer_download_is_ready(db, tmp_path):
    tokenizer = Tokenizer(name="cl100k", source="builtin", resource_id="cl100k_base", status="pending")
    db.add(tokenizer)
    db.commit()

    result = tokenizer_service.download_tokenizer(db, tokenizer.id, tmp_path)

    assert result.status == "ready"
    assert result.local_path is None
    assert result.checksum


def test_tokenizer_upload_rejects_zip_path_traversal(db, tmp_path):
    tokenizer = Tokenizer(name="上传", source="upload", status="pending")
    db.add(tokenizer)
    db.commit()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("../escape.json", "bad")

    with pytest.raises(ValueError, match="非法路径"):
        tokenizer_service.upload_tokenizer_archive(
            db, tokenizer.id, "tokenizer.zip", archive.getvalue(), tmp_path
        )

    db.refresh(tokenizer)
    assert tokenizer.status == "pending"
    assert not list(tmp_path.iterdir())


def test_tokenizer_upload_rejects_windows_zip_path_traversal(db, tmp_path):
    tokenizer = Tokenizer(name="上传", source="upload", status="pending")
    db.add(tokenizer)
    db.commit()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(r"..\escape.json", "bad")

    with pytest.raises(ValueError, match="非法路径"):
        tokenizer_service.upload_tokenizer_archive(
            db, tokenizer.id, "tokenizer.zip", archive.getvalue(), tmp_path
        )

    assert not list(tmp_path.iterdir())


def test_tokenizer_download_does_not_duplicate_fresh_claim(db, tmp_path):
    tokenizer = Tokenizer(
        name="remote", source="huggingface", resource_id="org/model",
        status="downloading",
    )
    db.add(tokenizer)
    db.commit()

    with pytest.raises(ConflictError, match="正在下载"):
        tokenizer_service.download_tokenizer(db, tokenizer.id, tmp_path)


def test_tokenizer_stale_claim_can_be_retried(db, tmp_path):
    tokenizer = Tokenizer(
        name="builtin", source="builtin", resource_id="cl100k_base",
        status="downloading",
    )
    db.add(tokenizer)
    db.commit()
    tokenizer.updated_at = datetime.now(timezone.utc) - timedelta(hours=2)
    db.commit()

    result = tokenizer_service.download_tokenizer(db, tokenizer.id, tmp_path)

    assert result.status == "ready"
    assert result.error is None


def test_tokenizer_stale_claim_removes_orphaned_published_target(db, tmp_path, monkeypatch):
    tokenizer = Tokenizer(
        name="remote", source="huggingface", resource_id="org/model",
        revision="refs/heads/main", status="downloading",
    )
    db.add(tokenizer)
    db.commit()
    tokenizer.updated_at = datetime.now(timezone.utc) - timedelta(hours=2)
    db.commit()
    target = tmp_path / f"tokenizer-{tokenizer.id}-v{tokenizer_service._safe_revision_component(tokenizer.revision)}"
    target.mkdir()
    (target / "orphan.json").write_text("orphan", encoding="utf-8")

    def fake_snapshot_download(*, local_dir, **_kwargs):
        path = Path(local_dir)
        path.mkdir(exist_ok=True)
        (path / "tokenizer.json").write_text("ok", encoding="utf-8")
        return str(path)

    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(snapshot_download=fake_snapshot_download))

    result = tokenizer_service.download_tokenizer(db, tokenizer.id, tmp_path)

    assert result.status == "ready"
    assert (target / "tokenizer.json").is_file()
    assert not (target / "orphan.json").exists()


def test_tokenizer_remote_ready_requires_published_evidence(db):
    tokenizer = Tokenizer(name="remote", source="huggingface", resource_id="org/model", status="pending")
    db.add(tokenizer)
    db.commit()

    with pytest.raises(ValueError, match="checksum"):
        tokenizer_service.update_tokenizer_status(db, tokenizer.id, "ready")


def test_revision_is_always_one_safe_path_component():
    component = tokenizer_service._safe_revision_component("refs/heads/../../escape")

    assert "/" not in component
    assert ".." not in component
