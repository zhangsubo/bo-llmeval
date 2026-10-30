"""Worker heartbeat lifecycle tests."""

from datetime import datetime, timedelta, timezone

from evalplat.services.worker_heartbeat_service import (
    clear_heartbeat,
    health_snapshot,
    record_heartbeat,
)
from evalplat.models.system_setting import SystemSetting


def test_clear_heartbeat_removes_only_gracefully_stopped_worker(db):
    record_heartbeat(db, "worker-a")
    record_heartbeat(db, "worker-b")

    clear_heartbeat(db, "worker-a")

    snapshot = health_snapshot(db)
    assert [item["worker_id"] for item in snapshot["workers"]] == ["worker-b"]
    assert snapshot["status"] == "healthy"


def test_clear_heartbeat_uses_code_worker_namespace(db):
    record_heartbeat(db, "worker-a", worker_type="code")
    record_heartbeat(db, "worker-a")

    clear_heartbeat(db, "worker-a", worker_type="code")

    assert health_snapshot(db, worker_type="code")["workers"] == []
    assert len(health_snapshot(db, worker_type="normal")["workers"]) == 1


def test_stale_instance_degrades_but_does_not_hide_online_worker(db):
    record_heartbeat(db, "worker-online")
    row = db.query(SystemSetting).filter(SystemSetting.key == "worker_heartbeat").one()
    value = dict(row.value)
    value["workers"]["worker-stale"] = {
        "status": "healthy",
        "heartbeat_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
    }
    row.value = value
    db.commit()

    snapshot = health_snapshot(db, timeout_seconds=120)

    assert snapshot["status"] == "degraded"
    assert {item["status"] for item in snapshot["workers"]} == {"healthy", "stale"}


def test_historical_stale_instance_is_omitted_from_live_snapshot(db):
    record_heartbeat(db, "worker-online")
    row = db.query(SystemSetting).filter(SystemSetting.key == "worker_heartbeat").one()
    value = dict(row.value)
    value["workers"]["worker-history"] = {
        "status": "healthy",
        "heartbeat_at": (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),
    }
    row.value = value
    db.commit()

    snapshot = health_snapshot(db, timeout_seconds=120)

    assert snapshot["status"] == "healthy"
    assert [item["worker_id"] for item in snapshot["workers"]] == ["worker-online"]
