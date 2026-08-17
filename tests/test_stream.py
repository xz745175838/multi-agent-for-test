"""SSE 流式接口测试（项目实际路由：POST /api/v1/chat/stream）。"""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock


@pytest.mark.integration
async def test_chat_stream_requires_auth(client: AsyncClient) -> None:
    """无 JWT 时流式接口应拒绝访问。"""
    response = await client.post(
        "/api/v1/chat/stream",
        json={"prompt": "hello stream", "model": "gpt-4o"},
    )
    assert response.status_code == 401


@pytest.mark.integration
async def test_chat_stream_sse_headers_and_payload(
    client: AsyncClient,
    auth_headers: dict[str, str],
    redis_client: AsyncMock,
) -> None:
    """模拟 SSE 客户端：校验 Content-Type，并异步迭代 data: 行。
    当前流式入口为 JWT 保护的 POST /api/v1/chat/stream。
    """
    async with client.stream(
        "POST",
        "/api/v1/chat/stream",
        headers=auth_headers,
        json={"prompt": "hello world", "model": "gpt-4o"},
    ) as response:
        assert response.status_code == 200
        content_type = response.headers.get("content-type", "")
        assert "text/event-stream" in content_type
        assert response.headers.get("cache-control") == "no-cache"
        assert response.headers.get("x-accel-buffering") == "no"

        data_lines: list[str] = []
        async for line in response.aiter_lines():
            if not line:
                continue
            # SSE 帧：data: {...} 或终止哨兵 data: [DONE]
            assert line.startswith("data: "), f"非 SSE data 行: {line!r}"
            data_lines.append(line)

        assert data_lines, "应至少收到一行 SSE data"
        assert data_lines[-1] == "data: [DONE]"

        # 中间帧应为 JSON 对象（含 content 字段）
        payload_lines = [ln for ln in data_lines if ln != "data: [DONE]"]
        assert payload_lines, "除 [DONE] 外应有 Token 数据帧"
        for line in payload_lines:
            raw = line.removeprefix("data: ").strip()
            chunk = json.loads(raw)
            assert "content" in chunk
            assert isinstance(chunk["content"], str)

    # 每次成功发起流式请求应对 Redis 计数 +1
    assert redis_client.incr.await_count >= 1
