"""历史任务清理的预览、批次固化和幂等执行。

清理属于高风险运维动作：预览/创建时确定范围，批次 manifest 固化后，
执行和重试都只消费这份清单，不再次按截止时间查询扩大范围。
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from evalplat.config import get_settings
from evalplat.constants import HISTORY_CLEANUP_CONFIRMATION_TEXT, RunStatus, TaskStatus
from evalplat.core.errors import ConflictError, NotFoundError
from evalplat.core.paths import public_basename
from evalplat.models.attempt import Attempt
from evalplat.models.code_execution import CodeExecution
from evalplat.models.cleanup_batch import CleanupBatch
from evalplat.models.connection_rate_event import ConnectionRateEvent
from evalplat.models.note import Note
from evalplat.models.report import Report
from evalplat.models.request import Request
from evalplat.models.run import Run
from evalplat.models.task import Task
from evalplat.services.audit_service import record_event


TERMINAL_TASK_STATUSES = {
    TaskStatus.COMPLETED,
    TaskStatus.PARTIALLY_COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
}
ACTIVE_RUN_STATUSES = set(RunStatus.ACTIVE)

_ENTITY_KEYS = (
    "tasks",
    "runs",
    "requests",
    "attempts",
    "code_executions",
    "connection_rate_events",
    "notes",
    "reports",
    "files",
    "file_bytes",
)


def _cleanup_stale_seconds() -> int:
    """Return the operator-configured heartbeat timeout with a safe default."""

    try:
        value = int(getattr(get_settings(), "history_cleanup_stale_seconds", 900))
    except (TypeError, ValueError):
        value = 900
    return max(value, 60)


def _empty_counts() -> dict[str, int]:
    return {key: 0 for key in _ENTITY_KEYS}


def _normalize_cutoff(cutoff_at: datetime) -> datetime:
    if cutoff_at.tzinfo is None:
        return cutoff_at.replace(tzinfo=timezone.utc)
    return cutoff_at.astimezone(timezone.utc)


def _validated_statuses(statuses: list[str] | None) -> list[str]:
    values = list(dict.fromkeys(statuses or sorted(TERMINAL_TASK_STATUSES)))
    if not values:
        raise ConflictError("至少选择一个终态任务状态")
    invalid = set(values) - TERMINAL_TASK_STATUSES
    if invalid:
        raise ConflictError(f"只能清理终态任务，非法状态: {', '.join(sorted(invalid))}")
    return values


def _report_file_paths(report: Report) -> list[str]:
    paths: list[str] = []
    for raw in (report.summary_path, report.details_path):
        if raw:
            paths.append(str(raw))
    snapshot = report.snapshot or {}
    page_paths = snapshot.get("page_paths") if isinstance(snapshot, dict) else None
    if isinstance(page_paths, dict):
        paths.extend(str(path) for path in page_paths.values() if path)
    # Preserve order while deduplicating paths from summary/details/page_paths.
    return list(dict.fromkeys(paths))


def _rooted_report_path(raw_path: str) -> Path | None:
    root = Path(get_settings().report_dir).expanduser().resolve()
    raw_value = str(raw_path)
    windows_path = PureWindowsPath(raw_value)
    if windows_path.drive:
        # Legacy rows may contain an absolute path from a Windows host while
        # the current report directory is on POSIX. The persisted report name
        # is controlled by the generator, so resolve that legacy path by its
        # basename inside the configured root.
        filename = windows_path.name
        if filename in {"", ".", ".."}:
            return None
        candidate = root / filename
    else:
        candidate = Path(raw_value.replace("\\", "/")).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        candidate = candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        return None
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _safe_file_size(raw_path: str) -> int:
    candidate = _rooted_report_path(raw_path)
    if candidate is None:
        return 0
    try:
        return candidate.stat().st_size if candidate.is_file() else 0
    except OSError:
        return 0


def _task_has_active_work(db: Session, task_id: uuid.UUID) -> bool:
    return db.scalar(
        select(func.count())
        .select_from(Run)
        .where(Run.task_id == task_id, Run.status.in_(ACTIVE_RUN_STATUSES))
    ) > 0


def _task_is_safe_to_delete(db: Session, task_id: uuid.UUID) -> bool:
    """执行阶段再次确认任务没有被重新激活或进入报告生成。"""

    # Keep the task row lock through the surrounding transaction. Submit,
    # retry, cancel and report generation use the same row as their seam, so
    # they cannot reactivate or publish a report between this check and delete.
    task = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if task is None:
        return True
    if task.status not in TERMINAL_TASK_STATUSES or _task_has_active_work(db, task_id):
        return False
    return not bool(
        db.scalar(
            select(func.count()).select_from(Report).where(
                Report.task_id == task_id, Report.status == "generating"
            )
        )
    )


def _task_entry(db: Session, task: Task) -> dict[str, Any]:
    runs = list(db.scalars(select(Run).where(Run.task_id == task.id).order_by(Run.seq, Run.id)))
    run_ids = [run.id for run in runs]
    request_ids: list[int] = []
    if run_ids:
        request_ids = list(db.scalars(select(Request.id).where(Request.run_id.in_(run_ids))))
    report_rows = list(db.scalars(select(Report).where(Report.task_id == task.id)))
    report_files = [path for report in report_rows for path in _report_file_paths(report)]
    # CSV/JSON exports are addressed by the Run UUID and are not Report rows.
    # Include their deterministic names in the immutable manifest so exports
    # already present, or created before execution starts, are reclaimed too.
    report_files.extend(
        f"run-{run_id}.{extension}"
        for run_id in run_ids
        for extension in ("csv", "json")
    )
    counts = _empty_counts()
    counts["tasks"] = 1
    counts["runs"] = len(run_ids)
    counts["requests"] = len(request_ids)
    if request_ids:
        counts["attempts"] = db.scalar(
            select(func.count()).select_from(Attempt).where(Attempt.request_id.in_(request_ids))
        ) or 0
        counts["connection_rate_events"] = db.scalar(
            select(func.count()).select_from(ConnectionRateEvent).where(ConnectionRateEvent.request_id.in_(request_ids))
        ) or 0
    # ``request_id`` is nullable for legacy or manually-created code jobs, but
    # every CodeExecution still belongs to a Run.  Count by run_id so the
    # preview matches the deletion path even when request evidence is absent.
    if run_ids:
        counts["code_executions"] = db.scalar(
            select(func.count()).select_from(CodeExecution).where(CodeExecution.run_id.in_(run_ids))
        ) or 0
    counts["notes"] = db.scalar(
        select(func.count()).select_from(Note).where(
            Note.target_type == "task", Note.target_id == task.id
        )
    ) or 0
    if run_ids:
        counts["notes"] += db.scalar(
            select(func.count()).select_from(Note).where(
                Note.target_type == "run", Note.target_id.in_(run_ids)
            )
        ) or 0
    counts["reports"] = len(report_rows)
    counts["files"] = sum(
        1
        for raw_path in set(report_files)
        if (candidate := _rooted_report_path(raw_path)) is not None and candidate.is_file()
    )
    counts["file_bytes"] = sum(_safe_file_size(raw_path) for raw_path in set(report_files))
    return {
        "task_id": str(task.id),
        "status": task.status,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        "run_ids": [str(value) for value in run_ids],
        "report_files": list(dict.fromkeys(report_files)),
        "counts": counts,
    }


def preview_cleanup(
    db: Session, cutoff_at: datetime, statuses: list[str] | None = None
) -> dict[str, Any]:
    """只读生成清理预览。"""

    normalized_statuses = _validated_statuses(statuses)
    cutoff = _normalize_cutoff(cutoff_at)
    tasks = list(
        db.scalars(
            select(Task)
            .where(Task.status.in_(normalized_statuses), Task.finished_at <= cutoff)
            .order_by(Task.finished_at, Task.id)
        )
    )
    entries: list[dict[str, Any]] = []
    for task in tasks:
        if _task_has_active_work(db, task.id):
            continue
        if db.scalar(
            select(func.count()).select_from(Report).where(
                Report.task_id == task.id, Report.status == "generating"
            )
        ):
            continue
        entries.append(_task_entry(db, task))
    counts = _empty_counts()
    files: list[str] = []
    for entry in entries:
        for key, value in entry["counts"].items():
            counts[key] += int(value)
        files.extend(entry["report_files"])
    return {
        "cutoff_at": cutoff,
        "statuses": normalized_statuses,
        "task_ids": [uuid.UUID(entry["task_id"]) for entry in entries],
        "tasks": entries,
        "planned_counts": counts,
        "report_files": list(dict.fromkeys(files)),
        "estimated_bytes": counts["file_bytes"],
    }


def create_cleanup_batch(
    db: Session, cutoff_at: datetime, statuses: list[str] | None = None
) -> CleanupBatch:
    """固化一次清理预览为不可变 manifest。"""

    preview = preview_cleanup(db, cutoff_at, statuses)
    batch = CleanupBatch(
        cutoff_at=preview["cutoff_at"],
        statuses=preview["statuses"],
        manifest={
            "tasks": preview["tasks"],
            "task_ids": [str(task_id) for task_id in preview["task_ids"]],
            "report_files": preview["report_files"],
            "estimated_bytes": preview["estimated_bytes"],
        },
        planned_counts=preview["planned_counts"],
        deleted_counts=_empty_counts(),
        failed_items=[],
    )
    db.add(batch)
    db.flush()
    record_event(
        db,
        "history_cleanup_batch_created",
        target_type="cleanup_batch",
        target_id=batch.id,
        diff={"task_count": len(preview["task_ids"]), "planned_counts": preview["planned_counts"]},
    )
    db.commit()
    db.refresh(batch)
    return batch


def get_cleanup_batch(db: Session, batch_id: uuid.UUID) -> CleanupBatch:
    batch = db.get(CleanupBatch, batch_id)
    if batch is None:
        raise NotFoundError("清理批次不存在")
    return batch


def task_cleanup_in_progress(db: Session, task_id: uuid.UUID) -> bool:
    """判断任务是否已被某个运行中的清理批次锁定。"""

    return str(task_id) in _running_cleanup_task_ids(db)


def _running_cleanup_task_ids(db: Session) -> set[str]:
    task_ids: set[str] = set()
    for batch in db.scalars(select(CleanupBatch).where(CleanupBatch.status == "running")):
        if _cleanup_batch_is_stale(batch):
            # A dead process must not permanently block report generation or
            # downloads. The next retry converts the row to ``failed``.
            continue
        task_ids.update(str(value) for value in ((batch.manifest or {}).get("task_ids") or []))
    return task_ids


def _cleanup_batch_is_stale(batch: CleanupBatch, *, now: datetime | None = None) -> bool:
    """Whether a running cleanup batch has missed its progress heartbeat."""

    if batch.status != "running":
        return False
    activity = batch.updated_at or batch.started_at or batch.created_at
    if activity is None:
        return False
    normalized = activity.replace(tzinfo=timezone.utc) if activity.tzinfo is None else activity.astimezone(timezone.utc)
    current = now or datetime.now(timezone.utc)
    return (current - normalized).total_seconds() > _cleanup_stale_seconds()


def _persist_cleanup_checkpoint(
    db: Session,
    batch: CleanupBatch,
    counts: Counter[str],
    failed_items: list[dict[str, Any]],
) -> None:
    """Persist progress so a worker crash leaves a recoverable checkpoint."""

    batch.deleted_counts = {key: int(counts.get(key, 0)) for key in _ENTITY_KEYS}
    batch.failed_items = list(failed_items)
    batch.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(batch)


def report_file_cleanup_in_progress(db: Session, candidate: Path) -> bool:
    """判断报告文件所属任务是否正在清理。"""

    normalized = str(candidate.resolve(strict=False))
    running_task_ids = _running_cleanup_task_ids(db)
    if not running_task_ids:
        return False
    export_match = re.fullmatch(r"run-([0-9a-fA-F-]{36})\.(csv|json)", candidate.name)
    if export_match:
        try:
            run = db.get(Run, uuid.UUID(export_match.group(1)))
        except ValueError:
            run = None
        if run is not None and run.task_id is not None:
            return str(run.task_id) in running_task_ids
    for report in db.scalars(select(Report)):
        normalized_paths: set[str] = set()
        for raw_path in _report_file_paths(report):
            path = _rooted_report_path(raw_path)
            if path is not None:
                normalized_paths.add(str(path))
        if normalized in normalized_paths and str(report.task_id) in running_task_ids:
            return True
    return False


def report_file_registered(db: Session, candidate: Path) -> bool:
    """Return whether a published static report path is registered in DB.

    The report directory is shared with operational exports and may contain
    stale/manual files.  Download endpoints must expose only files recorded
    by a completed immutable Report row.
    """

    normalized = str(candidate.resolve(strict=False))
    for report in db.scalars(select(Report).where(Report.status == "completed")):
        for raw_path in _report_file_paths(report):
            path = _rooted_report_path(raw_path)
            if path is not None and str(path) == normalized:
                return True
    return False


def _delete_report_files(paths: list[str], failed_items: list[dict[str, Any]], counts: Counter[str]) -> bool:
    ok = True
    for raw_path in list(dict.fromkeys(paths)):
        candidate = _rooted_report_path(raw_path)
        if candidate is None:
            failed_items.append({"kind": "file", "path": public_basename(raw_path), "error": "路径不在 report_dir 内"})
            ok = False
            continue
        try:
            if candidate.exists():
                if not candidate.is_file():
                    raise OSError("目标不是普通文件")
                size = candidate.stat().st_size
                candidate.unlink()
                counts["files"] += 1
                counts["file_bytes"] += size
        except OSError as exc:
            failed_items.append({"kind": "file", "path": public_basename(raw_path), "error": "文件删除失败"})
            ok = False
    return ok


def _delete_task_rows(db: Session, task_id: uuid.UUID, counts: Counter[str]) -> None:
    task = db.get(Task, task_id)
    if task is None:
        return
    run_ids = list(db.scalars(select(Run.id).where(Run.task_id == task_id)))
    request_ids = list(db.scalars(select(Request.id).where(Request.run_id.in_(run_ids)))) if run_ids else []
    if request_ids:
        result = db.execute(delete(CodeExecution).where(CodeExecution.request_id.in_(request_ids)))
        counts["code_executions"] += result.rowcount or 0
        result = db.execute(delete(ConnectionRateEvent).where(ConnectionRateEvent.request_id.in_(request_ids)))
        counts["connection_rate_events"] += result.rowcount or 0
        result = db.execute(delete(Attempt).where(Attempt.request_id.in_(request_ids)))
        counts["attempts"] += result.rowcount or 0
        result = db.execute(delete(Request).where(Request.id.in_(request_ids)))
        counts["requests"] += result.rowcount or 0
    if run_ids:
        result = db.execute(delete(CodeExecution).where(CodeExecution.run_id.in_(run_ids)))
        counts["code_executions"] += result.rowcount or 0
        result = db.execute(delete(Note).where(Note.target_type == "run", Note.target_id.in_(run_ids)))
        counts["notes"] += result.rowcount or 0
        result = db.execute(delete(Run).where(Run.id.in_(run_ids)))
        counts["runs"] += result.rowcount or 0
    result = db.execute(delete(Note).where(Note.target_type == "task", Note.target_id == task_id))
    counts["notes"] += result.rowcount or 0
    result = db.execute(delete(Report).where(Report.task_id == task_id))
    counts["reports"] += result.rowcount or 0
    db.execute(delete(Task).where(Task.id == task_id))
    counts["tasks"] += 1


def execute_cleanup_batch(
    db: Session,
    batch_id: uuid.UUID,
    *,
    confirmation: str | None = None,
    retry: bool = False,
) -> CleanupBatch:
    """执行或补偿重试一个批次，保证同一批次并发调用互斥。"""

    if confirmation != HISTORY_CLEANUP_CONFIRMATION_TEXT:
        raise ConflictError(
            f"执行历史清理必须输入确认文本 {HISTORY_CLEANUP_CONFIRMATION_TEXT}"
        )
    batch = db.scalar(select(CleanupBatch).where(CleanupBatch.id == batch_id).with_for_update())
    if batch is None:
        raise NotFoundError("清理批次不存在")
    if batch.status == "completed":
        return batch
    if batch.status == "running":
        if not _cleanup_batch_is_stale(batch):
            raise ConflictError("清理批次正在执行")
        # A process may have died after publishing ``running``. Convert the
        # stale marker to a retryable terminal state before continuing.
        batch.status = "failed"
        batch.finished_at = datetime.now(timezone.utc)
        batch.error = "清理批次执行心跳超时，可重试"
        batch.failed_items = [{"kind": "batch", "error": batch.error}]
        batch.updated_at = datetime.now(timezone.utc)
        record_event(
            db,
            "history_cleanup_stale_recovered",
            target_type="cleanup_batch",
            target_id=batch.id,
            result="failed",
        )
        db.commit()
        db.refresh(batch)
        if not retry:
            raise ConflictError("清理批次执行已超时，请使用 retry 继续")
    if retry and batch.status not in {"failed", "partial"}:
        raise ConflictError("只有失败或部分失败的批次可以重试")
    if not retry and batch.status not in {"pending", "failed", "partial"}:
        raise ConflictError("当前批次状态不可执行")

    batch.status = "running"
    batch.started_at = batch.started_at or datetime.now(timezone.utc)
    batch.error = None
    batch.failed_items = []
    db.flush()
    record_event(db, "history_cleanup_started", target_type="cleanup_batch", target_id=batch.id, diff={"retry": retry})
    # Publish the cleaning state before touching files/rows so report
    # generation and download requests in other sessions can observe it.
    db.commit()
    db.refresh(batch)

    counts = Counter(batch.deleted_counts or {})
    failed_items: list[dict[str, Any]] = []
    task_entries = list((batch.manifest or {}).get("tasks") or [])
    for entry in task_entries:
        task_id = str(entry.get("task_id"))
        if not _task_is_safe_to_delete(db, uuid.UUID(task_id)):
            failed_items.append({"kind": "task", "task_id": task_id, "error": "任务已重新运行或报告正在生成"})
            _persist_cleanup_checkpoint(db, batch, counts, failed_items)
            continue
        report_files = list(dict.fromkeys(entry.get("report_files") or []))
        unsafe_paths = [raw_path for raw_path in report_files if _rooted_report_path(str(raw_path)) is None]
        if unsafe_paths:
            failed_items.extend(
                {"kind": "file", "task_id": task_id, "path": public_basename(str(raw_path)), "error": "路径不在 report_dir 内"}
                for raw_path in unsafe_paths
            )
            failed_items.append({"kind": "task", "task_id": task_id, "error": "报告文件路径不安全，保留任务数据"})
            _persist_cleanup_checkpoint(db, batch, counts, failed_items)
            continue
        counts_before = Counter(counts)
        try:
            with db.begin_nested():
                _delete_task_rows(db, uuid.UUID(task_id), counts)
            # Make database deletion durable before touching the filesystem.
            # The manifest remains available for a later file-GC retry.
            db.commit()
        except Exception as exc:
            counts = counts_before
            failed_items.append({"kind": "task", "task_id": task_id, "error": "任务数据清理失败"})
            _persist_cleanup_checkpoint(db, batch, counts, failed_items)
            continue
        if not _delete_report_files(report_files, failed_items, counts):
            failed_items.append({"kind": "task", "task_id": task_id, "error": "报告文件删除失败，数据库记录已清理，可重试文件 GC"})
        _persist_cleanup_checkpoint(db, batch, counts, failed_items)

    batch.deleted_counts = {key: int(counts.get(key, 0)) for key in _ENTITY_KEYS}
    batch.failed_items = failed_items
    batch.finished_at = datetime.now(timezone.utc)
    if failed_items:
        successful_tasks = len(task_entries) - sum(item.get("kind") == "task" for item in failed_items)
        batch.status = "partial" if successful_tasks > 0 else "failed"
        batch.error = f"{len(failed_items)} 个清理项失败"
        result = "partial" if batch.status == "partial" else "failed"
    else:
        batch.status = "completed"
        batch.error = None
        result = "success"
    record_event(
        db,
        "history_cleanup_completed",
        target_type="cleanup_batch",
        target_id=batch.id,
        result=result,
        diff={"status": batch.status, "deleted_counts": batch.deleted_counts, "failed_count": len(failed_items)},
    )
    db.commit()
    db.refresh(batch)
    return batch
