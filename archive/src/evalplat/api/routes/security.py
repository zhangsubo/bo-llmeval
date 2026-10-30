"""安全加固 API 路由（Phase 10）。"""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import select

from evalplat.api.deps import get_db
from evalplat.services import security_service
from evalplat.services.audit_service import record_event
from evalplat.core.errors import ConflictError
from evalplat.core.ssrf import SSRFGuard
from evalplat.models.connection import Connection

router = APIRouter(prefix="/settings", tags=["安全设置"])
WHITELIST_CONFIRMATION_TEXT = "CONFIRM"


class WhitelistResponse(BaseModel):
    """白名单响应体。"""

    domains: list[str] = Field(default_factory=list)
    cidrs: list[str] = Field(default_factory=list)
    affected_connection_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="本次配置可能影响的 active 连接",
    )


class WhitelistUpdate(BaseModel):
    """白名单更新请求体。"""

    domains: list[str] = Field(default_factory=list, description="允许的域名列表（支持 *.example.com 通配符）")
    cidrs: list[str] = Field(default_factory=list, description="允许的 CIDR 列表")
    confirmation: str = Field(
        ...,
        description=f"必须输入确认文本 {WHITELIST_CONFIRMATION_TEXT}",
    )


@router.get("/whitelist", response_model=WhitelistResponse)
def get_whitelist(db: Session = Depends(get_db)):
    """获取当前 URL 白名单配置。"""
    from evalplat.models.system_setting import SystemSetting

    setting = db.scalar(
        select(SystemSetting).where(SystemSetting.key == security_service.WHITELIST_SETTING_KEY)
    )
    if not setting:
        return WhitelistResponse()
    value = setting.value if isinstance(setting.value, dict) else {}
    return WhitelistResponse(
        domains=value.get("domains", []),
        cidrs=value.get("cidrs", []),
    )


@router.put("/whitelist", response_model=WhitelistResponse)
def update_whitelist(payload: WhitelistUpdate, db: Session = Depends(get_db)):
    """更新 URL 白名单配置。"""
    from evalplat.models.system_setting import SystemSetting

    if payload.confirmation != WHITELIST_CONFIRMATION_TEXT:
        raise ConflictError(f"修改白名单必须输入确认文本 {WHITELIST_CONFIRMATION_TEXT}")
    data = {"domains": payload.domains, "cidrs": payload.cidrs}
    try:
        # Fail before mutating the setting so malformed CIDRs cannot poison
        # the shared SSRF guard used by every outbound request boundary.
        SSRFGuard(allowed_domains=data["domains"], allowed_cidrs=data["cidrs"])
    except ValueError as exc:
        raise ValueError(f"白名单配置无效: {exc}") from exc
    affected_connection_ids = list(
        db.scalars(select(Connection.id).where(Connection.status == "active"))
    )
    setting = db.scalar(
        select(SystemSetting).where(SystemSetting.key == security_service.WHITELIST_SETTING_KEY)
    )
    if setting:
        setting.value = data
    else:
        setting = SystemSetting(key=security_service.WHITELIST_SETTING_KEY, value=data)
        db.add(setting)
    db.flush()
    record_event(
        db,
        "whitelist_updated",
        target_type="system_setting",
        target_id=setting.id,
        diff={
            "domains": data["domains"],
            "cidrs": data["cidrs"],
            "affected_connection_count": len(affected_connection_ids),
        },
    )
    db.commit()
    db.refresh(setting)
    return WhitelistResponse(
        domains=data["domains"],
        cidrs=data["cidrs"],
        affected_connection_ids=affected_connection_ids,
    )
