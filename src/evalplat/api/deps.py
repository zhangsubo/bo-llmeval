"""公共 FastAPI 依赖项。

get_db 从 db.session re-export；PaginationParams 提供通用分页参数。
"""

from typing import Annotated

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from evalplat.db.session import get_db

# re-export，路由层统一从 deps 导入
__all__ = ["get_db", "DbSession", "PaginationParams"]


DbSession = Annotated[Session, Depends(get_db)]


class PaginationParams:
    """通用分页参数。"""

    def __init__(
        self,
        skip: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> None:
        self.skip = skip
        self.limit = limit
