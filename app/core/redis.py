"""Async Redis client and connection pool helpers."""

from collections.abc import AsyncGenerator

from redis.asyncio import ConnectionPool, Redis

from app.core.config import settings

# better add these to hint it's expected as Redis, if no initialization, it's None
_pool: ConnectionPool | None = None
_redis: Redis | None = None


async def init_redis() -> None:
    """Create the shared connection pool and verify connectivity with PING."""
    # 池与客户端是进程级单例
    global _pool, _redis
    _pool = ConnectionPool.from_url(
        settings.redis_url,
        decode_responses=True,
        max_connections=20,
    )
    _redis = Redis(connection_pool=_pool)
    # 快速失败，避免“服务已起但 Redis 不通、第一个 chat 才爆”
    await _redis.ping()


async def close_redis() -> None:
    """Gracefully close the Redis client and disconnect the pool."""
    global _pool, _redis
    if _redis is not None:
        #  关redis客户端
        await _redis.aclose()
        _redis = None
    if _pool is not None:
        # 断开池内所有连接
        await _pool.disconnect()
        _pool = None


async def get_redis() -> AsyncGenerator[Redis, None]:
    """Yield the shared async Redis client for FastAPI dependency injection."""
    if _redis is None:
        raise RuntimeError("Redis client is not initialized; check application lifespan.")
    # 交出的是客户端对象，不是“独占一条连接直到响应结束”
    # 连接的借还粒度是单次 Redis 命令（由 redis-py 池管理）
    yield _redis
