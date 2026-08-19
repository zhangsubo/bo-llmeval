"""报告生成与导出业务逻辑（Phase 9，桩实现）。

实际 HTML 报告生成和 CSV 导出将在后续 Phase 实现。
当前只创建报告记录并返回桩数据。
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from evalplat.core.errors import NotFoundError
from evalplat.models.task import Task


def generate_report(db: Session, task_id: uuid.UUID) -> dict:
    """生成报告（桩）：创建报告记录，不实际生成 HTML。

    Returns:
        {"id": ..., "task_id": ..., "status": "pending", "created_at": ...}
    """
    task = db.get(Task, task_id)
    if not task:
        raise NotFoundError("任务不存在")

    report_id = uuid.uuid4()
    return {
        "id": report_id,
        "task_id": task_id,
        "status": "pending",
        "message": "报告生成已排队（桩实现，实际 HTML 生成将在后续版本完成）",
        "created_at": datetime.now(timezone.utc),
    }


def export_csv(db: Session, run_id: uuid.UUID) -> dict:
    """导出 CSV（桩）：返回导出元数据，不实际生成文件。

    Returns:
        {"run_id": ..., "format": "csv", "status": "pending", ...}
    """
    from evalplat.models.run import Run

    run = db.get(Run, run_id)
    if not run:
        raise NotFoundError("Run 不存在")

    return {
        "run_id": run_id,
        "format": "csv",
        "status": "pending",
        "message": "CSV 导出已排队（桩实现，实际导出将在后续版本完成）",
        "created_at": datetime.now(timezone.utc),
    }
