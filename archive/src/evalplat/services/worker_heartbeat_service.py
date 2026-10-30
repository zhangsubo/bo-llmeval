"""Worker 心跳和健康状态。

心跳使用 PostgreSQL 的 ``system_settings`` 保存，避免为仅一类短状态再引入
独立表；值按 Worker ID 分组，过期 Worker 可被健康检查和任务预检发现。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from evalplat.models.system_setting import SystemSetting

NORMAL_HEARTBEAT_KEY = "worker_heartbeat"
CODE_HEARTBEAT_KEY = "code_worker_heartbeat"
DEFAULT_STALE_RETENTION_SECONDS = 2 * 60 * 60


def record_heartbeat(
    db: Session,
    worker_id: str,
    *,
    worker_type: str = "normal",
    status: str = "healthy",
    detail: str | None = None,
) -> None:
    """记录单个 Worker 最近心跳，调用方应在短事务中使用。"""

    key = CODE_HEARTBEAT_KEY if worker_type == "code" else NORMAL_HEARTBEAT_KEY
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        # Serialize the read/merge/write even when the key does not exist yet.
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": key})
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == key).with_for_update())
    value: dict[str, Any] = dict(row.value) if row and isinstance(row.value, dict) else {}
    workers = dict(value.get("workers") or {})
    workers[worker_id] = {
        "status": status,
        "heartbeat_at": datetime.now(timezone.utc).isoformat(),
        **({"detail": detail[:200]} if detail else {}),
    }
    value["workers"] = workers
    value["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        if row is None:
            db.add(SystemSetting(key=key, value=value))
        else:
            row.value = value
        db.commit()
    except IntegrityError:
        # A non-PostgreSQL test/deployment without advisory locks can still
        # race on first insert; retry through the normal locked update path.
        db.rollback()
        row = db.scalar(select(SystemSetting).where(SystemSetting.key == key).with_for_update())
        if row is None:
            raise
        value["workers"] = {**(row.value or {}).get("workers", {}), worker_id: value["workers"][worker_id]}
        row.value = value
        db.commit()


def clear_heartbeat(
    db: Session,
    worker_id: str,
    *,
    worker_type: str = "normal",
) -> None:
    """Remove a worker's heartbeat after a graceful shutdown.

    Crashed workers intentionally remain in the heartbeat map until they age
    out, but normal container restarts should not leave a permanently stale
    instance in the health panel.
    """

    key = CODE_HEARTBEAT_KEY if worker_type == "code" else NORMAL_HEARTBEAT_KEY
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": key})
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == key).with_for_update())
    if row is None or not isinstance(row.value, dict):
        return
    workers = dict(row.value.get("workers") or {})
    if worker_id not in workers:
        return
    workers.pop(worker_id, None)
    value = dict(row.value)
    value["workers"] = workers
    value["updated_at"] = datetime.now(timezone.utc).isoformat()
    row.value = value
    db.commit()


def health_snapshot(
    db: Session,
    *,
    worker_type: str = "normal",
    timeout_seconds: int = 120,
    stale_retention_seconds: int | None = None,
) -> dict[str, Any]:
    """返回 Worker 集群健康状态和每个近期实例的过期信息。

    Heartbeats are stored durably so a crashed worker remains diagnosable, but
    container IDs and PIDs change on every restart.  Without a retention
    window, old instances would make a healthy cluster appear degraded
    forever.  Keep recent stale entries for operator visibility, then omit
    historical entries from the live health snapshot.
    """

    key = CODE_HEARTBEAT_KEY if worker_type == "code" else NORMAL_HEARTBEAT_KEY
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
    value = row.value if row and isinstance(row.value, dict) else {}
    workers = value.get("workers") or {}
    now = datetime.now(timezone.utc)
    retention = (
        max(timeout_seconds * 2, DEFAULT_STALE_RETENTION_SECONDS)
        if stale_retention_seconds is None
        else max(stale_retention_seconds, timeout_seconds)
    )
    details: list[dict[str, Any]] = []
    for worker_id, raw in workers.items():
        info = raw if isinstance(raw, dict) else {}
        heartbeat = info.get("heartbeat_at")
        age = None
        healthy = False
        try:
            if heartbeat:
                timestamp = datetime.fromisoformat(str(heartbeat).replace("Z", "+00:00"))
                age = max(0.0, (now - timestamp).total_seconds())
                if age > retention:
                    continue
                healthy = age <= timeout_seconds and info.get("status", "healthy") in {"healthy", "ready", "running"}
        except (TypeError, ValueError):
            healthy = False
        details.append({"worker_id": worker_id, "status": "healthy" if healthy else "stale", "age_seconds": round(age, 1) if age is not None else None, "detail": info.get("detail")})
    healthy_count = sum(1 for item in details if item["status"] == "healthy")
    if not details:
        status = "unknown"
    elif healthy_count == 0:
        status = "unhealthy"
    elif healthy_count < len(details):
        # A restarted/crashed instance may remain in the map until operators
        # inspect it.  An online instance can still consume work, so expose a
        # degraded cluster rather than blocking the whole queue.
        status = "degraded"
    else:
        status = "healthy"
    return {"status": status, "workers": details, "timeout_seconds": timeout_seconds}
