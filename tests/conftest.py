"""pytest 公共夹具。

数据库策略（重要，决定测试能否在没有 Docker 的环境下运行）：
- 默认使用 SQLite 内存库，保证任何机器上都能完整执行 TDD；
- 设置环境变量 ``TEST_DATABASE_URL``（形如 ``postgresql+psycopg://postgres:postgres@127.0.0.1:5432/aitkp_test``）
  时改用真实 PostgreSQL，同一套测试即可用于集成验证。

因此：**测试代码不得依赖 PostgreSQL 独有语法**（例如 ``text[] @> ...``、``jsonb_path_ops``）。
需要 PG 专属行为的用例请单独标记，见 ``tests/test_pg_only.py``。
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# 导入 models 包会把所有 ORM 实体注册到 Base.metadata
from app import models  # noqa: F401  # pragma: no cover - 仅用于注册副作用
from app.core.db import Base, get_session


def _make_engine() -> Engine:
    """按环境变量选择测试数据库，默认 SQLite 内存库。"""
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        # 注意：这里会 drop_all + create_all，TEST_DATABASE_URL 必须指向专用测试库。
        engine = create_engine(url, pool_pre_ping=True)
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        return engine

    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # 内存库在所有连接间共享
    )


@pytest.fixture()
def engine() -> Iterator[Engine]:
    eng = _make_engine()
    if eng.dialect.name == "sqlite":
        Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


@pytest.fixture()
def db_session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def app(db_session: Session):
    """构建应用并把数据库会话依赖指向测试库。

    延迟导入 ``create_app``：纯单元测试（模型 / Schema）不需要启动整个应用。
    """
    from app.main import create_app

    application = create_app()

    def override_get_session() -> Iterator[Session]:
        yield db_session

    application.dependency_overrides[get_session] = override_get_session
    return application


@pytest.fixture()
def client(app) -> Iterator[TestClient]:
    """TestClient。

    ``raise_server_exceptions=False``：让未处理异常走我们自己的 500 handler，
    从而可以测试错误信封，而不是让异常直接冒泡到测试里。
    """
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
