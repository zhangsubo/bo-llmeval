"""测试 fixtures：TestClient + DB session。

兼容策略：SQLiteUUID（UUID↔str）、JSONB→JSON、GENERATED ALWAYS AS IDENTITY→移除。
lifespan mock 跳过 PostgreSQL 连接检查。
"""

import os
import uuid
from contextlib import asynccontextmanager

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://x:x@localhost/x")
os.environ.setdefault("TEST_DATABASE_URL", "postgresql+psycopg://x:x@localhost/x")
os.environ.setdefault("APP_MASTER_KEY", "S23qeuMUp7KWdXsoDV6X9-0T76tfE7JrEuDy8KMafA0=")
os.environ.setdefault("LOG_LEVEL", "DEBUG")

from unittest.mock import patch  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import CHAR, BigInteger, Integer, TypeDecorator, create_engine, event  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from evalplat.db.base import Base  # noqa: E402
from evalplat.models import *  # noqa: F401, E402

from sqlalchemy import JSON  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.schema import ColumnDefault  # noqa: E402
from sqlalchemy.sql.sqltypes import Uuid as SA_Uuid  # noqa: E402


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


class SQLiteUUID(TypeDecorator):
    impl = CHAR(36)
    cache_ok = True
    def process_bind_param(self, value, dialect):
        return str(value) if value is not None else value
    def process_result_value(self, value, dialect):
        return uuid.UUID(value) if value is not None else value


for table in Base.metadata.tables.values():
    for col in table.columns:
        if isinstance(col.type, (PG_UUID, SA_Uuid)):
            col.type = SQLiteUUID()
            if col.primary_key:
                col.default = ColumnDefault(uuid.uuid4)
        elif isinstance(col.type, JSONB):
            col.type = JSON()
        if isinstance(col.type, BigInteger) and col.primary_key:
            # SQLite only aliases an exact INTEGER primary key to ROWID;
            # BIGINT + Identity otherwise remains NOT NULL without a value.
            col.type = Integer()
            col.autoincrement = True
            if col.server_default is not None and 'GENERATED' in str(getattr(col.server_default, 'arg', '')):
                col.server_default = None
        # 移除 gen_random_uuid() server_default 并补充 Python default
        if col.server_default is not None and 'gen_random_uuid' in str(getattr(col.server_default, 'arg', '')):
            col.server_default = None
        # 所有无默认值的 PK 列补充 Python default
        if col.primary_key and col.default is None and col.server_default is None and not col.autoincrement:
            col.default = ColumnDefault(uuid.uuid4)


_test_engine = create_engine(
    "sqlite:///file:memdb1?mode=memory&cache=shared&uri=true",
    connect_args={"check_same_thread": False},
)

@event.listens_for(_test_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    dbapi_conn.cursor().execute("PRAGMA foreign_keys=ON")

_TestSessionLocal = sessionmaker(bind=_test_engine, autoflush=False, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _setup_db():
    Base.metadata.create_all(bind=_test_engine)
    yield
    with _test_engine.connect() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
        conn.commit()


@pytest.fixture()
def db() -> Session:
    session = _TestSessionLocal()
    try:
        yield session
    finally:
        try:
            session.close()
        except Exception:
            pass  # SQLite shared memory 可能已关闭


@pytest.fixture()
def client() -> TestClient:
    import evalplat.api.routes.health as health_mod
    import evalplat.main as main_mod
    import evalplat.db.session as session_mod

    def _override_get_db():
        s = _TestSessionLocal()
        try:
            yield s
        finally:
            s.close()

    # 持久 patch engine 引用（在测试期间保持活跃）
    with (
        patch.object(main_mod, "engine", _test_engine),
        patch.object(health_mod, "engine", _test_engine),
        patch.object(session_mod, "engine", _test_engine),
    ):
        app = main_mod.create_app()

        app.dependency_overrides[session_mod.get_db] = _override_get_db

        @asynccontextmanager
        async def _mock_lifespan(_app):
            yield

        app.router.lifespan_context = _mock_lifespan

        yield TestClient(app, raise_server_exceptions=False)
