"""pytest 全局 Fixture：异步 DB 隔离、Redis Mock、HTTPX ASGI 客户端与鉴权辅助。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.redis import get_redis
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.user import User

# ---------------------------------------------------------------------------
# Session 作用域：内存 SQLite 引擎 + 建表 / 销毁（完全不污染开发库）
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def async_engine() -> AsyncIterator[AsyncEngine]:
    """会话级异步 Engine：使用 SQLite 内存库，测试间零外部依赖。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        # 内存库必须共享同一连接，否则其它连接看不到已建表
        poolclass=StaticPool,
        echo=False,
    )
    async with engine.begin() as conn:
        # 导入模型后 metadata 已注册 users 表
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def session_factory(
    async_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """基于测试 Engine 的 sessionmaker。"""
    return async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )


# ---------------------------------------------------------------------------
# Function 作用域：Savepoint 事务隔离 —— 每条用例结束后强制 rollback
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_session(
    async_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """每条测试独占一个外层事务 + 嵌套 Savepoint。

    路由内 ``await session.commit()`` 只会提交到 Savepoint；
    测试结束 rollback 外层事务，数据库不留脏数据。
    """
    connection = await async_engine.connect()
    outer_transaction = await connection.begin()
    session = session_factory(bind=connection)

    # 开启嵌套事务（Savepoint），供业务代码 commit
    await connection.begin_nested()

    @event.listens_for(session.sync_session, "after_transaction_end")
    def _restart_savepoint(sync_session: Any, transaction: Any) -> None:
        """业务 commit 结束后自动重新开 Savepoint，保持隔离模式可用。"""
        if transaction.nested and not transaction._parent.nested:  # noqa: SLF001
            sync_session.begin_nested()

    try:
        yield session
    finally:
        await session.close()
        # 无论用例成败，整单回滚，绝不落库
        if outer_transaction.is_active:
            await outer_transaction.rollback()
        await connection.close()


# ---------------------------------------------------------------------------
# Function 作用域：Redis Mock（不连真实 Redis）
# ---------------------------------------------------------------------------


@pytest.fixture
def redis_client() -> AsyncMock:
    """轻量 Redis Mock：仅实现本项目用到的 incr，并按用例隔离存储。"""
    store: dict[str, int] = {}

    async def _incr(key: str, amount: int = 1) -> int:
        store[key] = int(store.get(key, 0)) + amount
        return store[key]

    mock = AsyncMock()
    mock.incr = AsyncMock(side_effect=_incr)
    mock.ping = AsyncMock(return_value=True)
    mock.aclose = AsyncMock(return_value=None)
    # 便于断言时查看
    mock._store = store  # noqa: SLF001
    return mock


# ---------------------------------------------------------------------------
# Function 作用域：HTTPX AsyncClient（ASGITransport）
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession,
    redis_client: AsyncMock,
) -> AsyncIterator[AsyncClient]:
    """注入测试 Session / Redis，并关闭真实 lifespan（避免连生产 PG/Redis）。"""

    # FastAPI dependency_overrides：把生产依赖换成测试替身。
    # - get_db → 注入带外层事务/Savepoint 的 db_session，用例结束整单回滚，不污染真实库
    # - get_redis → 注入 AsyncMock redis_client，不连真实 Redis，且按用例隔离计数
    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    async def _override_get_redis() -> AsyncGenerator[AsyncMock, None]:
        yield redis_client
 

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_redis] = _override_get_redis

    # httpx 0.28 的 ASGITransport 默认不跑 lifespan，可避免连真实 PG/Redis
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Function 作用域：预置用户 + JWT Header
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession) -> User:
    """写入一名可登录的测试用户（随 Savepoint 在用例结束时回滚）。"""
    user = User(
        id=uuid.uuid4(),
        username=f"tester_{uuid.uuid4().hex[:8]}",
        hashed_password=hash_password("secret12"),
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def auth_headers(test_user: User) -> dict[str, str]:
    """生成有效 Bearer JWT，供受保护路由使用。"""
    token = create_access_token(subject=test_user.id)
    return {"Authorization": f"Bearer {token}"}
