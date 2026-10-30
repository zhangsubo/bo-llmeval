"""连接级运行时限流。

每次请求先在数据库中预留一个事件，再发出 HTTP 请求。PostgreSQL 下使用
连接级 advisory lock 串行化检查和预留，避免多个 Worker 同时越过同一限额。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from evalplat.models.connection_rate_event import ConnectionRateEvent


@dataclass(frozen=True)
class ConnectionRateDecision:
    allowed: bool
    event_id: uuid.UUID | None = None
    reason: str | None = None


def acquire_connection_slot(
    db: Session,
    connection_id: uuid.UUID,
    limits: dict[str, Any] | None,
    *,
    request_id: int | None = None,
    estimated_tokens: int = 0,
    now: datetime | None = None,
    lease_seconds: int = 1_800,
) -> ConnectionRateDecision:
    """检查连接限额并预留一个事件；无配置时不写入事件。"""

    normalized = limits if isinstance(limits, dict) else {}
    active_limits = {
        name: _limit_value(normalized, name, f"max_{name}")
        for name in ("concurrency", "rps", "rpm", "tpm")
    }
    active_limits = {name: value for name, value in active_limits.items() if value is not None}
    if not active_limits:
        return ConnectionRateDecision(allowed=True)
    if estimated_tokens < 0 or lease_seconds <= 0:
        raise ValueError("estimated_tokens 和 lease_seconds 必须为正数")

    current = now or datetime.now(timezone.utc)
    _lock_connection(db, connection_id)
    stale = list(
        db.scalars(
            select(ConnectionRateEvent).where(
                ConnectionRateEvent.connection_id == connection_id,
                ConnectionRateEvent.status == "active",
                ConnectionRateEvent.expires_at <= current,
            )
        )
    )
    for event in stale:
        event.status = "expired"
        event.released_at = current
    if stale:
        # Sessions in production and the test harness both disable
        # autoflush; make expiry visible to the following counter queries.
        db.flush()

    # Concurrency is a lease property, not a rolling-window metric: an
    # active request remains in-flight even when it started more than a minute
    # ago.  RPM/TPM/RPS use the separate rolling window below.
    active_events = list(
        db.scalars(
            select(ConnectionRateEvent).where(
                ConnectionRateEvent.connection_id == connection_id,
                ConnectionRateEvent.status == "active",
            )
        )
    )
    events = list(
        db.scalars(
            select(ConnectionRateEvent).where(
                ConnectionRateEvent.connection_id == connection_id,
                ConnectionRateEvent.started_at >= current - timedelta(seconds=60),
                ConnectionRateEvent.status.in_(("active", "released")),
            )
        )
    )
    active_count = len(active_events)
    if _exceeds(active_count, active_limits.get("concurrency")):
        return ConnectionRateDecision(False, reason="connection_concurrency_limit")
    last_second = sum(
        _as_utc(event.started_at) >= current - timedelta(seconds=1) for event in events
    )
    if _exceeds(last_second, active_limits.get("rps")):
        return ConnectionRateDecision(False, reason="connection_rps_limit")
    if _exceeds(len(events), active_limits.get("rpm")):
        return ConnectionRateDecision(False, reason="connection_rpm_limit")
    token_window = sum(
        max(event.actual_tokens or 0, event.reserved_tokens)
        for event in events
    )
    if _exceeds(token_window + estimated_tokens, active_limits.get("tpm")):
        return ConnectionRateDecision(False, reason="connection_tpm_limit")

    event = ConnectionRateEvent(
        connection_id=connection_id,
        request_id=request_id,
        status="active",
        started_at=current,
        expires_at=current + timedelta(seconds=lease_seconds),
        reserved_tokens=max(0, estimated_tokens),
    )
    db.add(event)
    db.flush()
    # The reservation must be visible before the network request begins.
    db.commit()
    db.refresh(event)
    return ConnectionRateDecision(allowed=True, event_id=event.id)


def release_connection_slot(
    db: Session,
    event_id: uuid.UUID | None,
    *,
    actual_tokens: int | None = None,
    now: datetime | None = None,
) -> bool:
    """释放预留并写回实际 Token；重复释放是幂等的。"""

    if event_id is None:
        return False
    event = db.get(ConnectionRateEvent, event_id)
    if event is None:
        return False
    if event.status == "active":
        event.status = "released"
        event.released_at = now or datetime.now(timezone.utc)
        if actual_tokens is not None:
            event.actual_tokens = max(0, int(actual_tokens))
        try:
            db.commit()
        except Exception:
            # A transient commit failure must not strand an active lease until
            # its full expiry window. Roll back the broken transaction and
            # retry the idempotent release once using a fresh row lock.
            db.rollback()
            retry = db.scalar(
                select(ConnectionRateEvent)
                .where(ConnectionRateEvent.id == event_id)
                .with_for_update()
            )
            if retry is None:
                return False
            if retry.status == "active":
                retry.status = "released"
                retry.released_at = now or datetime.now(timezone.utc)
                if actual_tokens is not None:
                    retry.actual_tokens = max(0, int(actual_tokens))
            db.commit()
    return True


def _lock_connection(db: Session, connection_id: uuid.UUID) -> None:
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        key = int.from_bytes(uuid.UUID(str(connection_id)).bytes[:8], "big", signed=True)
        db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": key})


def _limit_value(limits: dict[str, Any], *names: str) -> float | None:
    for name in names:
        value = limits.get(name)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValueError(f"连接限额 {name} 必须为数字") from None
    return None


def _exceeds(current: float, limit: float | None) -> bool:
    return limit is not None and (limit <= 0 or current >= limit)


def _as_utc(value: datetime) -> datetime:
    """SQLite drops timezone metadata; normalize it before window arithmetic."""

    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
