"""Tokenizer 业务逻辑（PRD §8.4）。"""

import uuid
import json
import hashlib
import io
import logging
import re
import shutil
import tempfile
import zipfile
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from evalplat.core.errors import ConflictError, NotFoundError
from evalplat.models.tokenizer import Tokenizer
from evalplat.models.model_deployment import ModelDeployment
from evalplat.models.test_template import TestTemplateVersion
from evalplat.models.comparison_group import ComparisonGroup
from evalplat.services.security_service import redact_sensitive_text


logger = logging.getLogger(__name__)

MAX_TOKENIZER_UPLOAD_BYTES = 512 * 1024 * 1024
_BUILTIN_ENCODINGS = frozenset({"cl100k_base", "o200k_base", "p50k_base", "r50k_base"})
_RESOURCE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
TOKENIZER_SOURCES = {"modelscope", "huggingface", "upload", "builtin"}
TOKENIZER_STATUSES = {"pending", "downloading", "ready", "failed", "archived"}
TOKENIZER_STALE_DOWNLOAD_SECONDS = 60 * 60
_TOKENIZER_TRANSITIONS: dict[str, frozenset[str]] = {
    # Builtin encodings have no download phase and may be materialized
    # directly; update_tokenizer_status still validates the encoding/evidence.
    "pending": frozenset({"pending", "downloading", "ready", "failed"}),
    "downloading": frozenset({"downloading", "ready", "failed"}),
    "failed": frozenset({"failed", "pending", "downloading", "ready"}),
    "ready": frozenset({"ready", "archived"}),
    "archived": frozenset({"archived"}),
}


def create_tokenizer(db: Session, **kwargs) -> Tokenizer:
    source = kwargs.get("source")
    if source not in TOKENIZER_SOURCES:
        raise ValueError("Tokenizer 来源无效")
    tok = Tokenizer(status="pending", **kwargs)
    db.add(tok)
    db.commit()
    db.refresh(tok)
    return tok


def get_tokenizer(db: Session, tokenizer_id: uuid.UUID) -> Tokenizer:
    tok = db.get(Tokenizer, tokenizer_id)
    if not tok:
        raise NotFoundError("Tokenizer 不存在")
    return tok


def list_tokenizers(
    db: Session,
    skip: int = 0,
    limit: int = 20,
    status_filter: str | None = None,
    keyword: str | None = None,
) -> tuple[list[Tokenizer], int]:
    stmt = select(Tokenizer)
    count_stmt = select(func.count()).select_from(Tokenizer)
    if status_filter:
        stmt = stmt.where(Tokenizer.status == status_filter)
        count_stmt = count_stmt.where(Tokenizer.status == status_filter)
    if keyword and (keyword := keyword.strip()):
        condition = or_(Tokenizer.name.ilike(f"%{keyword}%"), Tokenizer.resource_id.ilike(f"%{keyword}%"))
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    total = db.scalar(count_stmt) or 0
    items = list(db.scalars(stmt.order_by(Tokenizer.created_at.desc()).offset(skip).limit(limit)))
    return items, total


def update_tokenizer_status(db: Session, tokenizer_id: uuid.UUID, status: str, error: str | None = None) -> Tokenizer:
    if status not in TOKENIZER_STATUSES:
        raise ValueError("Tokenizer 状态无效")
    tok = db.scalar(select(Tokenizer).where(Tokenizer.id == tokenizer_id).with_for_update())
    if tok is None:
        raise NotFoundError("Tokenizer 不存在")
    if status not in _TOKENIZER_TRANSITIONS.get(tok.status, frozenset()):
        raise ConflictError(f"Tokenizer 不允许从 {tok.status} 变更为 {status}")
    if status == "ready":
        # A ready row must represent usable tokenizer evidence. Builtins can
        # be materialized without a filesystem path; remote/upload resources
        # must have been published by their download/upload endpoints.
        if tok.source == "builtin":
            encoding = (tok.resource_id or "cl100k_base").strip()
            if encoding not in _BUILTIN_ENCODINGS:
                raise ValueError("不支持的内置 tiktoken encoding")
            tok.resource_id = encoding
            tok.checksum = tok.checksum or hashlib.sha256(encoding.encode()).hexdigest()
            tok.size_bytes = 0
            tok.local_path = None
        elif not tok.local_path or not tok.checksum:
            raise ValueError("Tokenizer 必须先完成下载或上传并生成 checksum 才能就绪")
    tok.status = status
    tok.error = error[:500] if error else None
    db.commit()
    db.refresh(tok)
    return tok


def mark_ready(db: Session, tokenizer_id: uuid.UUID, local_path: str, checksum: str | None = None, size_bytes: int | None = None) -> Tokenizer:
    tok = db.scalar(select(Tokenizer).where(Tokenizer.id == tokenizer_id).with_for_update())
    if tok is None:
        raise NotFoundError("Tokenizer 不存在")
    if tok.status not in {"pending", "downloading", "failed"}:
        raise ConflictError("只有未就绪的 Tokenizer 可以发布")
    if not local_path or not checksum:
        raise ValueError("Tokenizer 发布必须包含 local_path 和 checksum")
    published_path = Path(local_path).expanduser().resolve()
    if not published_path.is_dir():
        raise ValueError("Tokenizer local_path 必须为已发布目录")
    tok.status = "ready"
    tok.local_path = str(published_path)
    tok.checksum = checksum
    tok.size_bytes = size_bytes if size_bytes is not None else tok.size_bytes
    db.commit()
    db.refresh(tok)
    return tok


def delete_tokenizer(db: Session, tokenizer_id: uuid.UUID, storage_root: str | Path) -> None:
    """删除 Tokenizer：检查引用 → 清理本地文件 → 删除数据库记录。"""
    tok = db.get(Tokenizer, tokenizer_id)
    if tok is None:
        raise NotFoundError("Tokenizer 不存在")

    # 检查是否有活跃引用
    refs: list[str] = []
    dep_count = db.scalar(select(func.count()).where(ModelDeployment.tokenizer_id == tokenizer_id))
    if dep_count:
        refs.append(f"模型部署 {dep_count} 条")
    tpl_count = db.scalar(select(func.count()).where(TestTemplateVersion.tokenizer_id == tokenizer_id))
    if tpl_count:
        refs.append(f"测试模板版本 {tpl_count} 条")
    grp_count = db.scalar(select(func.count()).where(ComparisonGroup.default_tokenizer_id == tokenizer_id))
    if grp_count:
        refs.append(f"对比组 {grp_count} 条")
    if refs:
        raise ConflictError(f"该 Tokenizer 仍被引用，无法删除：{'；'.join(refs)}")

    # 清理本地文件
    if tok.local_path:
        local = Path(tok.local_path)
        if local.exists():
            if local.is_dir():
                shutil.rmtree(local, ignore_errors=True)
            else:
                local.unlink(missing_ok=True)
            logger.info("已清理 Tokenizer 本地文件: %s", tok.local_path)

    db.delete(tok)
    db.commit()


def download_tokenizer(db: Session, tokenizer_id: uuid.UUID, storage_root: str | Path) -> Tokenizer:
    """Download one tokenizer into a private cache and atomically mark it ready."""

    tok = db.scalar(select(Tokenizer).where(Tokenizer.id == tokenizer_id).with_for_update())
    if tok is None:
        raise NotFoundError("Tokenizer 不存在")
    if tok.status == "ready":
        raise ConflictError("Tokenizer 已就绪，不可覆盖")
    if tok.status == "archived":
        raise ConflictError("Tokenizer 已归档，不可下载")
    reclaimed_stale = False
    if tok.status == "downloading":
        if not _is_stale(tok.updated_at):
            raise ConflictError("Tokenizer 正在下载")
        reclaimed_stale = True
        tok.status = "failed"
        tok.error = "上一次 Tokenizer 下载超时，已允许重新尝试"
        db.commit()
        tok = db.scalar(select(Tokenizer).where(Tokenizer.id == tokenizer_id).with_for_update())
        if tok is None:
            raise NotFoundError("Tokenizer 不存在")
    root = Path(storage_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    _cleanup_stale_tokenizer_staging(root, tok.id)
    if tok.source == "builtin":
        encoding = (tok.resource_id or "cl100k_base").strip()
        if encoding not in _BUILTIN_ENCODINGS:
            tok.status = "failed"
            tok.error = "不支持的内置 tiktoken encoding"
            db.commit()
            raise ValueError(tok.error)
        tok.status = "ready"
        tok.error = None
        tok.checksum = hashlib.sha256(encoding.encode()).hexdigest()
        tok.size_bytes = 0
        tok.local_path = None
        db.commit()
        db.refresh(tok)
        return tok
    resource_id = (tok.resource_id or "").strip()
    if not _RESOURCE_ID_RE.fullmatch(resource_id):
        tok.status = "failed"
        tok.error = "Tokenizer resource_id 必须为 owner/name 格式"
        db.commit()
        raise ValueError(tok.error)
    tok.status = "downloading"
    tok.error = None
    db.commit()
    target = root / f"tokenizer-{tok.id}-v{_safe_revision_component(tok.revision)}"
    if reclaimed_stale and target.exists():
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        else:
            target.unlink(missing_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".tokenizer-{tok.id}-", dir=root))
    published_target: Path | None = None
    committed = False
    try:
        if tok.source == "huggingface":
            try:
                from huggingface_hub import snapshot_download as hf_snapshot_download
            except ImportError as exc:
                raise RuntimeError("未安装 huggingface_hub，请安装 evalplat[resources]") from exc
            # 只下载 tokenizer 相关文件，不拉模型权重
            _tokenizer_patterns = [
                "tokenizer*", "*.json", "*.model", "*.tiktoken",
                "vocab*", "merges*", "special_tokens*", "*.txt",
            ]
            try:
                downloaded = hf_snapshot_download(repo_id=resource_id, revision=tok.revision, local_dir=str(staging), local_dir_use_symlinks=False, allow_patterns=_tokenizer_patterns)  # type: ignore[call-overload]
            except TypeError:
                downloaded = hf_snapshot_download(repo_id=resource_id, revision=tok.revision, local_dir=str(staging), allow_patterns=_tokenizer_patterns)
            source_path = Path(downloaded).expanduser().resolve() if downloaded else staging.resolve()
        elif tok.source == "modelscope":
            try:
                from modelscope import snapshot_download as ms_snapshot_download  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError("未安装 modelscope，请安装 evalplat[resources]") from exc
            # 只下载 tokenizer 相关文件，不拉模型权重
            _tokenizer_includes = [
                "tokenizer*", "*.json", "*.model", "*.tiktoken",
                "vocab*", "merges*", "special_tokens*", "*.txt",
            ]
            downloaded = ms_snapshot_download(resource_id, revision=tok.revision, cache_dir=str(staging), include=_tokenizer_includes)
            source_path = Path(str(downloaded)).expanduser().resolve()
        else:
            raise ValueError("该 Tokenizer 来源不支持远程下载，请使用 ZIP 上传")
        if not source_path.exists() or not source_path.is_dir():
            raise ValueError("Tokenizer 下载结果不是有效目录")
        publish_root = source_path
        if source_path != staging and not source_path.is_relative_to(staging):
            # Keep the published path under the configured root and avoid
            # copying a cache path that escapes it.
            for item in source_path.iterdir():
                destination = staging / item.name
                if item.is_dir():
                    shutil.copytree(item, destination, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, destination)
            publish_root = staging
        size_bytes, checksum = _directory_digest(publish_root)
        if target.exists():
            raise ConflictError("Tokenizer 目标缓存已存在，请创建新版本")
        publish_root.replace(target)
        published_target = target
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        tok.local_path = str(target)
        tok.size_bytes = size_bytes
        tok.checksum = checksum
        tok.status = "ready"
        db.commit()
        committed = True
        db.refresh(tok)
        return tok
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        if published_target is not None and not committed:
            shutil.rmtree(published_target, ignore_errors=True)
        db.rollback()
        tok = db.get(Tokenizer, tokenizer_id)
        if tok is not None:
            tok.status = "failed"
            tok.error = redact_sensitive_text(str(exc)[:500]) or "Tokenizer 处理失败"
            db.commit()
            db.refresh(tok)
        raise


def _is_stale(updated_at: datetime | None, *, now: datetime | None = None) -> bool:
    if updated_at is None:
        return True
    current = now or datetime.now(timezone.utc)
    normalized = updated_at.replace(tzinfo=timezone.utc) if updated_at.tzinfo is None else updated_at.astimezone(timezone.utc)
    return current - normalized > timedelta(seconds=TOKENIZER_STALE_DOWNLOAD_SECONDS)


def _safe_revision_component(revision: str | None) -> str:
    """Convert an upstream revision into one filesystem-safe path component."""

    value = str(revision or "latest").strip()
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value):
        return value
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _cleanup_stale_tokenizer_staging(root: Path, tokenizer_id: uuid.UUID) -> None:
    """Remove abandoned staging directories left by a crashed downloader."""

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=TOKENIZER_STALE_DOWNLOAD_SECONDS)
    prefix = f".tokenizer-{tokenizer_id}-"
    for candidate in root.iterdir():
        if not candidate.name.startswith(prefix) or not candidate.is_dir():
            continue
        try:
            modified = datetime.fromtimestamp(candidate.stat().st_mtime, tz=timezone.utc)
            if modified < cutoff:
                shutil.rmtree(candidate, ignore_errors=True)
        except OSError:
            logger.warning("failed to remove stale tokenizer staging", extra={"path": str(candidate)})


def upload_tokenizer_archive(
    db: Session,
    tokenizer_id: uuid.UUID,
    filename: str,
    content: bytes,
    storage_root: str | Path,
) -> Tokenizer:
    """Safely extract a ZIP tokenizer directory and publish it atomically."""

    tok = db.scalar(select(Tokenizer).where(Tokenizer.id == tokenizer_id).with_for_update())
    if tok is None:
        raise NotFoundError("Tokenizer 不存在")
    if tok.source != "upload":
        raise ConflictError("只有 upload 来源的 Tokenizer 可以上传目录")
    if tok.status == "ready":
        raise ConflictError("Tokenizer 已就绪，不可覆盖")
    if len(content) > MAX_TOKENIZER_UPLOAD_BYTES:
        raise ValueError("Tokenizer 压缩包不能超过 512MB")
    if Path(filename or "").suffix.lower() != ".zip":
        raise ValueError("Tokenizer 目录必须以 ZIP 压缩包上传")
    root = Path(storage_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".tokenizer-{tok.id}-", dir=root))
    published_target: Path | None = None
    committed = False
    try:
        extracted_bytes = 0
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            for item in archive.infolist():
                raw_name = str(item.filename or "").replace("\\", "/")
                name = Path(raw_name)
                if (
                    not raw_name
                    or raw_name.startswith("/")
                    or re.match(r"^[A-Za-z]:/", raw_name)
                    or name.is_absolute()
                    or ".." in name.parts
                    or (item.external_attr >> 16 and stat.S_ISLNK(item.external_attr >> 16))
                ):
                    raise ValueError("Tokenizer 压缩包包含非法路径")
                if item.file_size > MAX_TOKENIZER_UPLOAD_BYTES:
                    raise ValueError("Tokenizer 文件超过大小限制")
                extracted_bytes += item.file_size
                if extracted_bytes > MAX_TOKENIZER_UPLOAD_BYTES:
                    raise ValueError("Tokenizer 解压后文件总量超过 512MB")
                destination = (staging / Path(*name.parts)).resolve()
                if destination != staging and staging not in destination.parents:
                    raise ValueError("Tokenizer 压缩包包含非法路径")
                if item.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item, "r") as source, destination.open("wb") as target_file:
                    shutil.copyfileobj(source, target_file, length=1024 * 1024)
        size_bytes, checksum = _directory_digest(staging)
        if size_bytes == 0:
            raise ValueError("Tokenizer 压缩包不能为空")
        target = root / f"tokenizer-{tok.id}-upload"
        if target.exists():
            raise ConflictError("Tokenizer 目标缓存已存在，请创建新版本")
        staging.replace(target)
        published_target = target
        tok.local_path = str(target)
        tok.size_bytes = size_bytes
        tok.checksum = checksum
        tok.status = "ready"
        tok.error = None
        db.commit()
        committed = True
        db.refresh(tok)
        return tok
    except Exception as exc:
        if published_target is not None and not committed:
            shutil.rmtree(published_target, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)
        db.rollback()
        try:
            tok = db.get(Tokenizer, tokenizer_id)
            if tok is not None:
                # Validation or publication failures leave an upload tokenizer
                # retryable; the cache directory has already been removed.
                tok.status = "pending"
                tok.error = redact_sensitive_text(str(exc)[:500]) or "Tokenizer 处理失败"
                db.commit()
                db.refresh(tok)
        except Exception:
            db.rollback()
        raise


def _directory_digest(root: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode()
        data = path.read_bytes()
        total += len(data)
        digest.update(relative)
        digest.update(b"\0")
        digest.update(hashlib.sha256(data).digest())
    return total, digest.hexdigest()


def count_tokens(tokenizer: Tokenizer, value: Any) -> int | None:
    """使用已就绪 Tokenizer 计算文本/消息 token 数。

    依赖按来源可选加载；不可用时返回 ``None``，调用方不得用近似值冒充。
    """
    if tokenizer.status != "ready":
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    try:
        if tokenizer.source == "builtin":
            import tiktoken

            encoding = tiktoken.get_encoding(tokenizer.resource_id or "cl100k_base")
            return len(encoding.encode(text))
        if tokenizer.local_path:
            from transformers import AutoTokenizer

            encoder = AutoTokenizer.from_pretrained(tokenizer.local_path, local_files_only=True)
            return len(encoder.encode(text, add_special_tokens=False))
    except Exception:
        return None
    return None


def generate_token_text(tokenizer: Tokenizer, token_count: int, *, seed: int = 0) -> str:
    """用已就绪 Tokenizer 生成指定数量的可复现文本 Token。

    ``seed`` 同时写入一个确定性的样本标记并旋转 token 序列，使不同请求
    即使超过基础词样本长度也不会周期性复用同一内容；不引入随机数或外部状态。
    """

    if tokenizer.status != "ready":
        raise ValueError("Tokenizer 未就绪")
    if token_count <= 0:
        raise ValueError("token_count 必须大于 0")
    source = f" evalplat benchmark input sample {seed} alpha beta gamma delta epsilon zeta"
    try:
        if tokenizer.source == "builtin":
            import tiktoken

            encoding = tiktoken.get_encoding(tokenizer.resource_id or "cl100k_base")
            pattern = encoding.encode(source)
            if not pattern:
                raise ValueError("Tokenizer 未生成可用 token")
            offset = seed % len(pattern)
            rotated = pattern[offset:] + pattern[:offset]
            values = (rotated * ((token_count + len(rotated) - 1) // len(rotated)))[:token_count]
            return encoding.decode(values)
        if tokenizer.local_path:
            from transformers import AutoTokenizer

            encoder = AutoTokenizer.from_pretrained(tokenizer.local_path, local_files_only=True)
            pattern = encoder.encode(source, add_special_tokens=False)
            if not pattern:
                raise ValueError("Tokenizer 未生成可用 token")
            offset = seed % len(pattern)
            rotated = pattern[offset:] + pattern[:offset]
            values = (rotated * ((token_count + len(rotated) - 1) // len(rotated)))[:token_count]
            return cast(str, encoder.decode(values, skip_special_tokens=False))
    except Exception as exc:
        raise ValueError("Tokenizer 无法生成输入文本") from exc
    raise ValueError("该 Tokenizer 来源不支持本地 Token 生成")
