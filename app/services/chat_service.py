"""Chat service: mock LLM streaming over Server-Sent Events."""

import asyncio
import json
import re
from collections.abc import AsyncGenerator

from app.schemas.chat import ChatStreamChunk


def _tokenize_prompt(prompt: str) -> list[str]:
    """Split a prompt into short tokens/phrases for typewriter-style streaming."""
    parts = [p for p in re.split(r"(\s+)", prompt) if p != ""]
    if not parts:
        return [prompt]
    return parts


async def mock_llm_stream_generator(prompt: str) -> AsyncGenerator[str, None]:
    """Yield SSE-framed chunks that simulate LLM token streaming.

    Each event follows the SSE wire format::

        data: {"content": "..."}\n\n

    The stream terminates with::

        data: [DONE]\n\n
    """
    tokens = _tokenize_prompt(prompt)
    for index, token in enumerate(tokens):
        # 模拟推理延迟，同时把控制权交回 Event Loop → 别的请求还能跑。这是流式接口能“边想边吐”又不堵死全站的关键。
        await asyncio.sleep(0.05)
        is_last = index == len(tokens) - 1
        chunk = ChatStreamChunk(
            content=token,
            finish_reason="stop" if is_last else None,
        )
        payload = chunk.model_dump(exclude_none=True)
        # `yield` 会把当前 SSE 帧交给调用方（如 StreamingResponse），并在此挂起；
        # 等消费方再 `__anext__` 拉取下一块时，从下一行（本循环下一轮）继续执行。
        # 把一帧 SSE 文本交给 StreamingResponse；生成器在此挂起。Starlette 编码成 bytes，
        # 经 Uvicorn 用 ASGI 的 http.response.body 发出去，且 more_body=True
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    # 循环结束后同样挂起并投递终止哨兵；消费方再前进一次后生成器正常结束。
    yield "data: [DONE]\n\n"
