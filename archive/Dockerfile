FROM python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534

# 非 root 运行（安全基线 T-01-04 缓解）
RUN useradd --create-home --uid 1000 appuser

WORKDIR /app

# 先复制依赖清单以利用构建缓存
COPY pyproject.toml ./
COPY src/ ./src/
# 迁移脚本与 alembic 配置（compose migrate 服务在容器内执行 alembic upgrade head）
COPY alembic.ini ./
COPY migrations/ ./migrations/

RUN pip install --no-cache-dir '.[evalscope]'

USER appuser

CMD ["uvicorn", "evalplat.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
