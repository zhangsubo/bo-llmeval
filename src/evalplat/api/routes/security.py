"""安全加固 API 路由（Phase 10）。"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from evalplat.api.deps import get_db
from evalplat.services import security_service

router = APIRouter(prefix="/settings", tags=["安全设置"])


class WhitelistResponse(BaseModel):
    """白名单响应体。"""

    domains: list[str] = Field(default_factory=list)
    cidrs: list[str] = Field(default_factory=list)


class WhitelistUpdate(BaseModel):
    """白名单更新请求体。"""

    domains: list[str] = Field(default_factory=list, description="允许的域名列表（支持 *.example.com 通配符）")
    cidrs: list[str] = Field(default_factory=list, description="允许的 CIDR 列表")


@router.get("/whitelist", response_model=WhitelistResponse)
def get_whitelist(db: Session = Depends(get_db)):
    """获取当前 URL 白名单配置。"""
    from sqlalchemy import select
    from evalplat.models.system_setting import SystemSetting

    setting = db.scalar(
        select(SystemSetting).where(SystemSetting.key == security_service.WHITELIST_SETTING_KEY)
    )
    if not setting:
        return WhitelistResponse()
    return WhitelistResponse(**setting.value)


@router.put("/whitelist", response_model=WhitelistResponse)
def update_whitelist(payload: WhitelistUpdate, db: Session = Depends(get_db)):
    """更新 URL 白名单配置。"""
    from sqlalchemy import select
    from evalplat.models.system_setting import SystemSetting

    data = payload.model_dump()
    setting = db.scalar(
        select(SystemSetting).where(SystemSetting.key == security_service.WHITELIST_SETTING_KEY)
    )
    if setting:
        setting.value = data
    else:
        setting = SystemSetting(key=security_service.WHITELIST_SETTING_KEY, value=data)
        db.add(setting)
    db.commit()
    db.refresh(setting)
    return WhitelistResponse(**setting.value)
