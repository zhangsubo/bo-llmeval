"""同步 SQLAlchemy engine 与会话工厂。

架构决策（01-01 Task 1 checkpoint 确认）：同步 engine + Session，
FastAPI 会把同步 route 放进线程池执行；Alembic 集成最直接。

连接池显式参数（RESEARCH.md Pitfall 5）：Phase 4 Worker 认领 Run 的
长事务可能占用连接，池参数必须显式声明而非依赖默认值；
pool_pre_ping=True 防止拿到被服务端断开的空闲连接。
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from evalplat.config import get_settings

_settings = get_settings()

engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：按请求提供 Session，结束关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
