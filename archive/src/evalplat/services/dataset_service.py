"""数据集业务逻辑（PRD §8.5）。"""

import uuid
import csv
import hashlib
import io
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from evalplat.core.errors import ConflictError, NotFoundError
from evalplat.models.dataset import Dataset, DatasetVersion
from evalplat.services.builtin_dataset_service import (
    BuiltinDatasetProvider,
    EvalScopeDatasetProvider,
    BuiltinDatasetSpec,
    get_builtin_dataset,
    list_builtin_datasets,
    normalize_builtin_row,
)
from evalplat.services.security_service import redact_sensitive_text

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_BUILTIN_BYTES = 2 * 1024 * 1024 * 1024
ALLOWED_UPLOAD_SUFFIXES = {".jsonl", ".csv"}
DATASET_KINDS = {"performance", "quality"}
DATASET_VERSION_STATUSES = {"pending", "processing", "ready", "failed", "archived"}
BUILTIN_DATASET_STALE_PROCESSING_SECONDS = 60 * 60

logger = logging.getLogger(__name__)


def create_dataset(db: Session, name: str, kind: str, remark: str | None = None) -> Dataset:
    if kind not in DATASET_KINDS:
        raise ValueError("数据集用途必须为 performance 或 quality")
    ds = Dataset(name=name, kind=kind, remark=remark)
    db.add(ds)
    db.commit()
    db.refresh(ds)
    return ds


def get_dataset(db: Session, dataset_id: uuid.UUID) -> Dataset:
    ds = db.get(Dataset, dataset_id)
    if not ds:
        raise NotFoundError("数据集不存在")
    return ds


def list_datasets(
    db: Session,
    skip: int = 0,
    limit: int = 20,
    kind: str | None = None,
    keyword: str | None = None,
) -> tuple[list[Dataset], int]:
    stmt = select(Dataset)
    count_stmt = select(func.count()).select_from(Dataset)
    if kind:
        stmt = stmt.where(Dataset.kind == kind)
        count_stmt = count_stmt.where(Dataset.kind == kind)
    if keyword and (keyword := keyword.strip()):
        condition = Dataset.name.ilike(f"%{keyword}%")
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    total = db.scalar(count_stmt) or 0
    items = list(db.scalars(stmt.order_by(Dataset.created_at.desc()).offset(skip).limit(limit)))
    return items, total


def builtin_catalog() -> list[dict[str, Any]]:
    """Return the static catalog without requiring a database connection."""

    return list_builtin_datasets()


def create_version(db: Session, dataset_id: uuid.UUID, **kwargs) -> DatasetVersion:
    # Lock the logical parent so concurrent writers cannot allocate the same
    # version number. The unique constraint remains the final database guard.
    ds = db.scalar(select(Dataset).where(Dataset.id == dataset_id).with_for_update())
    if not ds:
        raise NotFoundError("数据集不存在")
    status = kwargs.get("status", "pending")
    if status not in DATASET_VERSION_STATUSES:
        raise ValueError("数据集版本状态无效")
    # This endpoint only creates a metadata placeholder. A version can become
    # ``ready`` only after the upload or builtin-download paths have published
    # a checksummed, loadable file; accepting ``ready`` here creates unusable
    # versions that look selectable by task precheck.
    if status != "pending":
        raise ValueError("手工创建数据集版本只能使用 pending 状态，请通过上传或内置下载生成 ready 版本")
    max_ver = db.scalar(
        select(func.max(DatasetVersion.version)).where(DatasetVersion.dataset_id == dataset_id)
    ) or 0
    ver = DatasetVersion(dataset_id=dataset_id, version=max_ver + 1, **kwargs)
    db.add(ver)
    db.commit()
    db.refresh(ver)
    return ver


def download_builtin_dataset(
    db: Session,
    builtin_key: str,
    storage_root: str | Path,
    *,
    provider: BuiltinDatasetProvider | None = None,
    force: bool = False,
) -> DatasetVersion:
    """Download one catalog entry into an immutable, checksummed version.

    Database state is claimed and committed before network I/O. A failed
    download therefore leaves a durable ``failed`` version that can be retried
    without exposing a partial file or holding a database lock for the whole
    transfer.
    """

    spec = get_builtin_dataset(builtin_key)
    root = Path(storage_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    version = _claim_builtin_version(db, spec, force=force, storage_root=root)
    if version.status == "ready":
        return version

    target = root / f"builtin-{spec.key}-v{version.version}.jsonl"
    # A process may die after opening its temporary file. Once the database
    # claim is stale, those leftovers are no longer owned by a live worker.
    for stale_temporary in root.glob(f".builtin-{spec.key}-v*.jsonl.*.tmp"):
        try:
            stale_temporary.unlink(missing_ok=True)
        except OSError:
            logger.warning("failed to remove stale builtin dataset temporary", extra={"path": str(stale_temporary)})
    temporary = root / f".{target.name}.{uuid.uuid4().hex}.tmp"
    selected_provider = provider or EvalScopeDatasetProvider()
    try:
        sample_count, checksum = _write_builtin_rows(
            spec,
            selected_provider.iter_rows(spec),
            temporary,
        )
        temporary.replace(target)
        current = db.scalar(
            select(DatasetVersion)
            .where(DatasetVersion.id == version.id)
            .with_for_update()
        )
        if current is None or current.status != "processing":
            target.unlink(missing_ok=True)
            raise ConflictError("内置数据集下载状态已被其他操作改变")
        current.status = "ready"
        current.storage_path = str(target)
        current.sample_count = sample_count
        current.checksum = checksum
        current.error = None
        db.commit()
        db.refresh(current)
        return current
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        _mark_builtin_failed(db, version.id, str(exc))
        raise


def _claim_builtin_version(
    db: Session,
    spec: BuiltinDatasetSpec,
    *,
    force: bool,
    storage_root: Path | None = None,
) -> DatasetVersion:
    """Create or claim a processing version while serializing per-key work."""

    dataset = db.scalar(
        select(Dataset)
        .where(Dataset.builtin_key == spec.key)
        .with_for_update()
    )
    if dataset is None:
        dataset = Dataset(
            name=spec.name,
            kind="quality",
            is_builtin=True,
            builtin_key=spec.key,
            remark=spec.description,
        )
        db.add(dataset)
        try:
            db.flush()
        except IntegrityError:
            # Another worker may have inserted the catalog row between our
            # lookup and flush. Re-open the transaction and serialize on it.
            db.rollback()
            dataset = db.scalar(
                select(Dataset)
                .where(Dataset.builtin_key == spec.key)
                .with_for_update()
            )
            if dataset is None:
                raise

    ready = db.scalar(
        select(DatasetVersion)
        .where(
            DatasetVersion.dataset_id == dataset.id,
            DatasetVersion.source_id == spec.source_id,
            DatasetVersion.status == "ready",
        )
        .order_by(DatasetVersion.version.desc())
    )
    if ready is not None and not force:
        db.commit()
        db.refresh(ready)
        return ready

    processing = db.scalar(
        select(DatasetVersion)
        .where(
            DatasetVersion.dataset_id == dataset.id,
            DatasetVersion.source_id == spec.source_id,
            DatasetVersion.status == "processing",
        )
        .order_by(DatasetVersion.version.desc())
    )
    if processing is not None:
        if not _is_stale_processing(processing.updated_at):
            db.rollback()
            raise ConflictError("该内置数据集正在下载")
        # A worker can disappear after claiming the row and before publishing
        # its temporary file. Convert the abandoned claim to a durable failed
        # version so a later request can retry without a permanent deadlock.
        processing.status = "failed"
        processing.error = "上一次内置数据集下载超时，已允许重新尝试"
        stale_path = processing.storage_path
        if stale_path and storage_root is not None:
            candidate = Path(stale_path).expanduser().resolve()
            if candidate.parent == storage_root:
                candidate.unlink(missing_ok=True)
        elif storage_root is not None:
            (storage_root / f"builtin-{spec.key}-v{processing.version}.jsonl").unlink(missing_ok=True)
        processing.storage_path = None
        db.flush()

    max_version = db.scalar(
        select(func.max(DatasetVersion.version)).where(
            DatasetVersion.dataset_id == dataset.id
        )
    ) or 0
    version = DatasetVersion(
        dataset_id=dataset.id,
        version=max_version + 1,
        source=spec.source,
        source_id=spec.source_id,
        source_revision=spec.revision,
        source_split=spec.split,
        source_subset=spec.subset,
        license=spec.license,
        language=spec.language,
        capability_tags=list(spec.capability_tags),
        scoring_method=spec.scoring_method,
        status="processing",
        storage_path=(
            str(storage_root / f"builtin-{spec.key}-v{max_version + 1}.jsonl")
            if storage_root is not None
            else None
        ),
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


def _is_stale_processing(updated_at: datetime | None, *, now: datetime | None = None) -> bool:
    if updated_at is None:
        return True
    current = now or datetime.now(timezone.utc)
    normalized = updated_at.replace(tzinfo=timezone.utc) if updated_at.tzinfo is None else updated_at.astimezone(timezone.utc)
    return current - normalized > timedelta(seconds=BUILTIN_DATASET_STALE_PROCESSING_SECONDS)


def _write_builtin_rows(
    spec: BuiltinDatasetSpec,
    rows: Iterable[Mapping[str, Any]],
    temporary: Path,
) -> tuple[int, str]:
    """Stream normalized rows to disk and calculate the published checksum."""

    digest = hashlib.sha256()
    count = 0
    written = 0
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for raw in rows:
            normalized = normalize_builtin_row(spec, raw)
            line = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")) + "\n"
            encoded = line.encode("utf-8")
            written += len(encoded)
            if written > MAX_BUILTIN_BYTES:
                raise ValueError("内置数据集超过 2GB 限制")
            handle.write(line)
            digest.update(encoded)
            count += 1
    if count == 0:
        raise ValueError("内置数据集为空")
    return count, digest.hexdigest()


def _mark_builtin_failed(db: Session, version_id: uuid.UUID, error: str) -> None:
    """Persist a bounded failure state after provider or publication errors."""

    try:
        db.rollback()
        current = db.get(DatasetVersion, version_id)
        if current is not None:
            current.status = "failed"
            current.error = redact_sensitive_text(error[:500]) or "内置数据集处理失败"
            current.storage_path = None
            db.commit()
    except Exception:
        db.rollback()
        logger.exception("failed to persist builtin dataset failure", extra={"version_id": str(version_id)})


def _validate_upload(
    filename: str,
    content: bytes,
    *,
    max_bytes: int = MAX_UPLOAD_BYTES,
) -> int:
    """完整读取并校验 JSONL/CSV，返回样本数；失败时不允许部分成功。"""

    if len(content) > max_bytes:
        limit = "100MB" if max_bytes == MAX_UPLOAD_BYTES else "2GB"
        raise ValueError(f"数据集文件不能超过 {limit}")
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        raise ValueError("仅支持 .jsonl 或 .csv 数据集文件")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("数据集必须使用 UTF-8 编码") from exc

    if suffix == ".jsonl":
        count = 0
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSONL 第 {line_number} 行格式错误") from exc
            if not isinstance(value, dict):
                raise ValueError(f"JSONL 第 {line_number} 行必须为对象")
            count += 1
    else:
        reader = csv.reader(io.StringIO(text, newline=""))
        try:
            fieldnames = next(reader)
        except StopIteration as exc:
            raise ValueError("CSV 必须包含非空表头") from exc
        if not fieldnames or any(not field.strip() for field in fieldnames):
            raise ValueError("CSV 必须包含非空表头")
        count = 0
        for row in reader:
            if len(row) != len(fieldnames):
                raise ValueError("CSV 存在列数不一致的行")
            count += 1
    if count == 0:
        raise ValueError("数据集不能为空")
    return count


def upload_version(
    db: Session,
    dataset_id: uuid.UUID,
    filename: str,
    content: bytes,
    storage_root: str | Path,
    **metadata,
) -> DatasetVersion:
    """校验整个上传文件后以唯一文件名原子落盘并创建 ready 版本。"""

    dataset = db.scalar(select(Dataset).where(Dataset.id == dataset_id).with_for_update())
    if not dataset:
        raise NotFoundError("数据集不存在")
    sample_count = _validate_upload(filename, content)
    max_ver = db.scalar(
        select(func.max(DatasetVersion.version)).where(DatasetVersion.dataset_id == dataset_id)
    ) or 0
    version = max_ver + 1
    root = Path(storage_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    checksum = hashlib.sha256(content).hexdigest()
    target = root / f"dataset-{dataset_id}-v{version}-{uuid.uuid4().hex}{Path(filename).suffix.lower()}"
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(target)
        version_row = DatasetVersion(
            dataset_id=dataset.id,
            version=version,
            source="upload",
            checksum=checksum,
            sample_count=sample_count,
            status="ready",
            storage_path=str(target),
            **metadata,
        )
        db.add(version_row)
        db.commit()
        db.refresh(version_row)
        return version_row
    except Exception:
        db.rollback()
        target.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)
        raise


def load_samples(
    version: DatasetVersion,
    *,
    storage_root: str | Path,
    limit: int | None = None,
) -> list[dict]:
    """读取 ready 数据集版本并统一映射为质量执行器样本。"""

    readiness_error = validate_ready_version(version, storage_root=storage_root)
    if readiness_error is not None:
        raise ValueError(readiness_error)
    root = Path(storage_root).expanduser().resolve()
    path = Path(version.storage_path).expanduser().resolve()
    if path.parent != root:
        raise ValueError("数据集文件路径不在配置的 dataset_dir 内")
    mapping = version.field_mapping or {}
    suffix = path.suffix.lower()
    samples: list[dict] = []

    def append_sample(index: int, row: dict) -> None:
        if not isinstance(row, dict):
            raise ValueError(f"数据集第 {index + 1} 行必须为对象")
        messages_key = mapping.get("messages", "messages")
        prompt_key = mapping.get("prompt", "prompt")
        answer_key = mapping.get("answer", mapping.get("expected", "answer"))
        messages = row.get(messages_key)
        if not isinstance(messages, list):
            prompt = row.get(prompt_key)
            if prompt is None and version.default_prompt:
                prompt = version.default_prompt
            if prompt is None:
                raise ValueError(f"数据集第 {index + 1} 行缺少 prompt/messages")
            messages = [{"role": "user", "content": str(prompt)}]
        expected = row.get(
            answer_key,
            row.get("expected", row.get("reference_answer", "")),
        )
        tests_key = mapping.get("tests", mapping.get("test_code", "tests"))
        tests = row.get(tests_key, row.get("test_code", row.get("test", "")))
        samples.append({
            "sample_index": index,
            "messages": messages,
            "expected": expected,
            "tests": tests if isinstance(tests, str) else str(tests or ""),
            "scoring_method": version.scoring_method,
            "row": row,
        })

    if suffix == ".jsonl":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for index, line in enumerate(handle):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"JSONL 第 {index + 1} 行格式错误") from exc
                append_sample(len(samples), row)
                if limit is not None and len(samples) >= limit:
                    break
    else:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for index, row in enumerate(csv.DictReader(handle)):
                append_sample(index, row)
                if limit is not None and len(samples) >= limit:
                    break
    return samples


def validate_ready_version(
    version: DatasetVersion,
    *,
    storage_root: str | Path,
) -> str | None:
    """Validate that a ``ready`` version can actually be loaded.

    Version metadata can be created before a file is uploaded, so checking only
    ``status=ready`` at task submission is insufficient. This helper performs
    deterministic checks at the queue boundary and returns a structured reason
    for the caller's precheck result.
    """

    if version.status != "ready":
        return "数据集版本未就绪"
    if not version.storage_path:
        return "ready 数据集缺少 storage_path"
    root = Path(storage_root).expanduser().resolve()
    path = Path(version.storage_path).expanduser().resolve()
    if path.parent != root:
        return "数据集文件路径不在配置的 dataset_dir 内"
    if not version.checksum:
        return "ready 数据集缺少 checksum"
    if version.sample_count is None or version.sample_count < 0:
        return "ready 数据集缺少有效 sample_count"
    max_bytes = MAX_BUILTIN_BYTES if version.source_id else MAX_UPLOAD_BYTES
    if not path.is_file():
        return "数据集文件不存在"
    try:
        size = path.stat().st_size
    except OSError:
        return "无法读取数据集文件"
    if size > max_bytes:
        limit = "2GB" if version.source_id else "100MB"
        return f"数据集文件超过 {limit} 限制"

    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        count = 0
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            if path.suffix.lower() == ".jsonl":
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"JSONL 第 {line_number} 行格式错误") from exc
                    if not isinstance(value, dict):
                        raise ValueError(f"JSONL 第 {line_number} 行必须为对象")
                    count += 1
            else:
                reader = csv.reader(handle)
                try:
                    fieldnames = next(reader)
                except StopIteration as exc:
                    raise ValueError("CSV 必须包含非空表头") from exc
                if not fieldnames or any(not field.strip() for field in fieldnames):
                    raise ValueError("CSV 必须包含非空表头")
                for row in reader:
                    if len(row) != len(fieldnames):
                        raise ValueError("CSV 存在列数不一致的行")
                    count += 1
        if count == 0:
            raise ValueError("数据集不能为空")
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return str(exc)
    if digest.hexdigest() != version.checksum:
        return "数据集 checksum 与版本记录不一致"
    if count != version.sample_count:
        return "数据集样本数与版本记录不一致"
    return None


def list_versions(db: Session, dataset_id: uuid.UUID, skip: int = 0, limit: int = 20) -> tuple[list[DatasetVersion], int]:
    stmt = select(DatasetVersion).where(DatasetVersion.dataset_id == dataset_id)
    count_stmt = select(func.count()).select_from(DatasetVersion).where(DatasetVersion.dataset_id == dataset_id)
    total = db.scalar(count_stmt) or 0
    items = list(db.scalars(stmt.order_by(DatasetVersion.version.desc()).offset(skip).limit(limit)))
    return items, total


def list_all_versions(
    db: Session,
    skip: int = 0,
    limit: int = 20,
    dataset_id: uuid.UUID | None = None,
    version_id: uuid.UUID | None = None,
    status: str | None = None,
    keyword: str | None = None,
) -> tuple[list[DatasetVersion], int]:
    """分页查询版本选择器所需的数据集版本。

    资源名称过滤在数据库端完成，避免管理台为每个数据集再发起版本请求。
    """

    stmt = select(DatasetVersion).join(Dataset, Dataset.id == DatasetVersion.dataset_id)
    count_stmt = (
        select(func.count())
        .select_from(DatasetVersion)
        .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
    )
    if dataset_id:
        condition = DatasetVersion.dataset_id == dataset_id
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    if version_id:
        condition = DatasetVersion.id == version_id
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    if status:
        condition = DatasetVersion.status == status
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    if keyword and (keyword := keyword.strip()):
        condition = or_(Dataset.name.ilike(f"%{keyword}%"), DatasetVersion.source_id.ilike(f"%{keyword}%"))
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    total = db.scalar(count_stmt) or 0
    items = list(
        db.scalars(
            stmt.order_by(DatasetVersion.created_at.desc(), DatasetVersion.version.desc())
            .offset(skip)
            .limit(limit)
        )
    )
    return items, total


def update_version_status(db: Session, version_id: uuid.UUID, status: str) -> DatasetVersion:
    if status not in DATASET_VERSION_STATUSES:
        raise ValueError("数据集版本状态无效")
    ver = db.get(DatasetVersion, version_id)
    if not ver:
        raise NotFoundError("数据集版本不存在")
    ver.status = status
    db.commit()
    db.refresh(ver)
    return ver
