"""统一异常体系与错误响应格式。

所有业务异常继承 AppError；FastAPI 异常处理器保证
前端只看到 ``{"error": {"code": "...", "message": "...", "details": null}}`` 结构。
通用 Exception handler 记日志但不回显内部细节。
"""

import json
import logging
from decimal import Decimal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    """业务异常基类。"""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class NotFoundError(AppError):
    """资源不存在。"""

    def __init__(self, message: str = "资源不存在") -> None:
        super().__init__(code="not_found", message=message, status_code=404)


class ConflictError(AppError):
    """资源冲突（如重复创建）。"""

    def __init__(self, message: str = "资源冲突") -> None:
        super().__init__(code="conflict", message=message, status_code=409)


class ForbiddenError(AppError):
    """无权限访问。"""

    def __init__(self, message: str = "无权限") -> None:
        super().__init__(code="forbidden", message=message, status_code=403)


def _error_body(code: str, message: str, details: object = None) -> dict:
    """统一错误 JSON 结构。"""
    return {"error": {"code": code, "message": message, "details": details}}


def _safe_json_response(status_code: int, content: dict):
    """Response that handles Decimal and other non-standard JSON types."""
    from starlette.responses import Response

    body = json.dumps(content, ensure_ascii=False, default=str)
    return Response(
        content=body.encode("utf-8"),
        status_code=status_code,
        media_type="application/json",
    )


def register_exception_handlers(app: FastAPI) -> None:
    """注册统一异常处理器。"""

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(_request: Request, exc: StarletteHTTPException):
        return _safe_json_response(
            exc.status_code,
            _error_body("not_found" if exc.status_code == 404 else "http_error", exc.detail),
        )

    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError):
        return _safe_json_response(exc.status_code, _error_body(exc.code, exc.message))

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, exc: RequestValidationError):
        return _safe_json_response(422, _error_body("validation_error", "请求参数校验失败", details=exc.errors()))

    @app.exception_handler(ValueError)
    async def value_error_handler(_request: Request, exc: ValueError):
        return _safe_json_response(400, _error_body("bad_request", str(exc)))

    @app.exception_handler(Exception)
    async def generic_error_handler(_request: Request, exc: Exception):
        logger.exception("未处理异常: %s", exc)
        return _safe_json_response(500, _error_body("internal_error", "服务器内部错误"))
