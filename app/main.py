"""FastAPI application entrypoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.api.v1.auth import router as auth_router
from app.api.v1.chat import router as chat_router
from app.api.v1.health import router as health_router
from app.core.config import settings
from app.core.database import Base, engine
from app.core.redis import close_redis, init_redis
from app.models import User  # noqa: F401 — register metadata for create_all, 一定要加载


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Initialize DB tables and Redis on startup; dispose resources on shutdown."""
    async with engine.begin() as conn:
        # 拿连接并开事务
        # 按 metadata 对缺失表发 CREATE TABLE（已存在则跳过）
        await conn.run_sync(Base.metadata.create_all)
    await init_redis()
    try:
        yield
    finally:
        await close_redis()
        await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health_router, prefix=settings.api_v1_prefix)
app.include_router(auth_router, prefix=settings.api_v1_prefix)
app.include_router(chat_router, prefix=settings.api_v1_prefix)


def main() -> None:
    """Run the ASGI application with Uvicorn."""
    uvicorn.run(
        # Import string "module.path:variable" — required when reload=True so
        # Uvicorn can re-import the module on code changes. Passing the `app`
        # object directly would break reliable hot-reload.
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
    )


if __name__ == "__main__":
    main()
