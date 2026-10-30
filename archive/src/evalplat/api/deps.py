"""公共 FastAPI 依赖项。

get_db 从 db.session re-export；PaginationParams 提供通用分页参数。
"""

from typing import Annotated

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from evalplat.core.ssrf import SSRFGuard
from evalplat.db.session import get_db
from evalplat.services.security_service import build_ssrf_guard

# re-export，路由层统一从 deps 导入
__all__ = ["get_db", "DbSession", "PaginationParams", "get_ssrf_guard"]


DbSession = Annotated[Session, Depends(get_db)]


def get_ssrf_guard(db: Session = Depends(get_db)) -> SSRFGuard:
    """按请求读取当前系统白名单，供所有出站边界复用。"""

    return build_ssrf_guard(db)


class PaginationParams:
    """通用分页参数。"""

    def __init__(
        self,
        skip: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> None:
        self.skip = skip
        self.limit = limit
