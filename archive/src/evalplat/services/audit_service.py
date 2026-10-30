"""关键操作审计记录。"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from typing import Any

from sqlalchemy.orm import Session

from evalplat.models.audit_event import AuditEvent


_actor_type: ContextVar[str] = ContextVar("evalplat_audit_actor_type", default="anonymous")
_actor_id: ContextVar[str | None] = ContextVar("evalplat_audit_actor_id", default=None)
_source_ip: ContextVar[str | None] = ContextVar("evalplat_audit_source_ip", default=None)


def bind_request_context(*, source_ip: str | None, actor_type: str = "anonymous", actor_id: str | None = None):
    """Bind request metadata for audit events emitted during one HTTP request."""

    return (
        _actor_type.set(actor_type),
        _actor_id.set(actor_id),
        _source_ip.set(source_ip),
    )


def reset_request_context(tokens) -> None:
    """Restore context after the request to prevent metadata leaking across tasks."""

    type_token, id_token, ip_token = tokens
    _actor_type.reset(type_token)
    _actor_id.reset(id_token)
    _source_ip.reset(ip_token)


def record_event(
    db: Session,
    action: str,
    *,
    target_type: str | None = None,
    target_id: uuid.UUID | str | None = None,
    result: str = "success",
    diff: dict[str, Any] | None = None,
    actor_type: str | None = None,
    actor_id: str | None = None,
    source_ip: str | None = None,
) -> AuditEvent:
    """向当前事务加入审计事件；调用方决定何时提交。"""

    event = AuditEvent(
        action=action,
        actor_type=actor_type or _actor_type.get(),
        actor_id=actor_id if actor_id is not None else _actor_id.get(),
        source_ip=source_ip if source_ip is not None else _source_ip.get(),
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        result=result,
        diff=diff,
    )
    db.add(event)
    return event
