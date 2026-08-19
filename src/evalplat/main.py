"""FastAPI 应用工厂。

``uvicorn evalplat.main:create_app --factory`` 可运行。
lifespan 启动时校验 DB 可达，不可达则启动失败。
"""

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from sqlalchemy import text

from evalplat.api.router import api_router
from evalplat.config import get_settings
from evalplat.core.errors import register_exception_handlers
from evalplat.core.logging import setup_logging
from evalplat.db.session import engine


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """启动时校验 DB 可达，DB 不可达时启动失败。"""
    settings = get_settings()
    setup_logging(settings.log_level)

    # 启动时 SELECT 1 校验数据库连接
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))

    yield


def create_app() -> FastAPI:
    """应用工厂函数。"""
    app = FastAPI(
        title="EvalPlat",
        description="LLM API 服务评测管理平台",
        version="0.1.0",
        lifespan=lifespan,
    )

    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")

    return app
