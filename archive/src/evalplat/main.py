"""FastAPI 应用工厂。

``uvicorn evalplat.main:create_app --factory`` 可运行。
lifespan 启动时校验 DB 可达，不可达则启动失败。
"""

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from sqlalchemy import text

from evalplat.api.router import api_router
from evalplat.api.routes.health import router as health_router
from evalplat.config import get_settings
from evalplat.core.errors import register_exception_handlers
from evalplat.core.logging import setup_logging
from evalplat.db.session import engine
from evalplat.db.session import SessionLocal
from evalplat.services.template_service import ensure_builtin_templates
from evalplat.services.audit_service import bind_request_context, reset_request_context


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """启动时校验 DB 可达，DB 不可达时启动失败。"""
    settings = get_settings()
    setup_logging(settings.log_level)

    # 启动时 SELECT 1 校验数据库连接
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    with SessionLocal() as db:
        ensure_builtin_templates(db)

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
    # Keep liveness/readiness probes available at conventional root paths for
    # container orchestrators, while retaining the versioned API endpoints.
    app.include_router(health_router)
    app.include_router(api_router, prefix="/api/v1")

    @app.middleware("http")
    async def audit_request_context(request, call_next):
        tokens = bind_request_context(
            source_ip=request.client.host if request.client else None,
        )
        try:
            return await call_next(request)
        finally:
            reset_request_context(tokens)

    return app
