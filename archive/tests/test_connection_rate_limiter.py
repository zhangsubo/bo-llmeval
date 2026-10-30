"""连接运行时限流器契约测试。"""

import uuid
from datetime import datetime, timedelta, timezone

from evalplat.models.connection_rate_event import ConnectionRateEvent
from evalplat.models.connection import Connection
from evalplat.models.supplier import Supplier
from evalplat.services.connection_rate_limiter import (
    acquire_connection_slot,
    release_connection_slot,
)


def test_connection_limiter_enforces_concurrency_and_releases_slot(db):
    connection_id = _connection_id(db)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    limits = {"max_concurrency": 1, "max_rps": 10, "max_rpm": 100, "max_tpm": 1000}

    first = acquire_connection_slot(db, connection_id, limits, estimated_tokens=10, now=now)
    assert first.allowed is True
    assert first.event_id is not None

    second = acquire_connection_slot(db, connection_id, limits, estimated_tokens=10, now=now)
    assert second.allowed is False
    assert second.reason == "connection_concurrency_limit"

    release_connection_slot(db, first.event_id, actual_tokens=12, now=now)
    third = acquire_connection_slot(db, connection_id, limits, estimated_tokens=10, now=now)
    assert third.allowed is True


def test_connection_limiter_enforces_rpm_and_tpm_windows(db):
    connection_id = _connection_id(db)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    limits = {"rpm": 1, "tpm": 20, "max_concurrency": 4}

    first = acquire_connection_slot(db, connection_id, limits, estimated_tokens=15, now=now)
    assert first.allowed is True
    release_connection_slot(db, first.event_id, actual_tokens=15, now=now)

    denied = acquire_connection_slot(db, connection_id, limits, estimated_tokens=1, now=now)
    assert denied.allowed is False
    assert denied.reason == "connection_rpm_limit"

    later = acquire_connection_slot(
        db, connection_id, limits, estimated_tokens=1, now=now + timedelta(seconds=61)
    )
    assert later.allowed is True
    release_connection_slot(db, later.event_id, actual_tokens=1, now=now + timedelta(seconds=61))


def test_release_retries_after_transient_commit_failure(db, monkeypatch):
    connection_id = _connection_id(db)
    now = datetime.now(timezone.utc)
    decision = acquire_connection_slot(
        db, connection_id, {"concurrency": 1}, now=now,
    )
    original_commit = db.commit
    state = {"failed": False}

    def flaky_commit():
        if not state["failed"]:
            state["failed"] = True
            raise RuntimeError("transient commit error")
        return original_commit()

    monkeypatch.setattr(db, "commit", flaky_commit)

    assert release_connection_slot(db, decision.event_id, actual_tokens=3, now=now)
    event = db.get(ConnectionRateEvent, decision.event_id)
    assert event.status == "released"
    assert event.actual_tokens == 3


def test_connection_limiter_reclaims_expired_running_events(db):
    connection_id = _connection_id(db)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    stale = ConnectionRateEvent(
        connection_id=connection_id,
        status="active",
        started_at=now - timedelta(minutes=10),
        expires_at=now - timedelta(seconds=1),
        reserved_tokens=10,
    )
    db.add(stale)
    db.commit()

    decision = acquire_connection_slot(
        db,
        connection_id,
        {"max_concurrency": 1},
        estimated_tokens=1,
        now=now,
    )

    assert decision.allowed is True
    db.refresh(stale)
    assert stale.status == "expired"


def test_connection_limiter_keeps_long_running_active_request_in_concurrency_count(db):
    connection_id = _connection_id(db)
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db.add(
        ConnectionRateEvent(
            connection_id=connection_id,
            status="active",
            started_at=started,
            expires_at=started + timedelta(hours=1),
            reserved_tokens=1,
        )
    )
    db.commit()

    decision = acquire_connection_slot(
        db,
        connection_id,
        {"max_concurrency": 1},
        estimated_tokens=1,
        now=started + timedelta(seconds=61),
    )

    assert decision.allowed is False
    assert decision.reason == "connection_concurrency_limit"


def _connection_id(db):
    supplier = Supplier(name="限流供应商", short_name="rate", type="原厂")
    db.add(supplier)
    db.flush()
    connection = Connection(
        supplier_id=supplier.id,
        name="限流连接",
        base_url="https://api.example.com",
        api_key_ciphertext=b"ciphertext",
    )
    db.add(connection)
    db.commit()
    return connection.id
