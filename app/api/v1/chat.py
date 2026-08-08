"""Chat API: JWT-protected SSE streaming endpoint."""

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis

from app.api.v1.auth import get_current_user
from app.core.redis import get_redis
from app.models.user import User
from app.schemas.chat import ChatStreamRequest
from app.services.chat_service import mock_llm_stream_generator

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/stream")
async def chat_stream(
    payload: ChatStreamRequest,
    current_user: User = Depends(get_current_user),
    redis: Redis = Depends(get_redis),
) -> StreamingResponse:
    """Stream a mock LLM response as Server-Sent Events.

    Requires a valid Bearer JWT. Increments a per-user call counter in Redis.
    """
    counter_key = f"chat:stream:calls:{current_user.id}"
    # _redis 是共享客户端；内部命令（如 incr）需要连接时，向 _pool 借一条空闲连接（没有则在 max_connections=20 内新建
    # 在 Event Loop 上非阻塞等待 Redis 响应；等待期间 Loop 可跑别的协程
    # 命令完成后，连接归还池（不是拆掉 TCP），下个请求可立刻复用
    # 多请求并发时，池里可以有多条连接并行跑命令；超过 max_connections 时，后续借用会在池内等待（背压），避免无限制打爆 Redis
    await redis.incr(counter_key)

    # model is accepted for API compatibility; mock stream uses prompt only for now
    _ = payload.model

    return StreamingResponse(
        mock_llm_stream_generator(payload.prompt),
        media_type="text/event-stream",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            # 没有 X-Accel-Buffering: no，在 Nginx 后面部署时最容易出现“服务端在 yield，前端却半天不动
        },
    )
