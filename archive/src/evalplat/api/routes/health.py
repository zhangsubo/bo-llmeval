"""健康检查路由。

/healthz — 存活探针，不访问 DB。
/readyz — 就绪探针，执行 SELECT 1，返回 DB 延迟。
"""

import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from evalplat.db.session import engine

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict:
    """存活探针，不访问数据库。"""
    return {"status": "ok"}


@router.get("/readyz")
def readyz() -> JSONResponse:
    """就绪探针，执行 SELECT 1 校验数据库可达。"""
    try:
        start = time.monotonic()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        latency_ms = round((time.monotonic() - start) * 1000, 2)
        return JSONResponse(
            content={"status": "ready", "db_latency_ms": latency_ms}
        )
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "db_latency_ms": None},
        )
