"""Chat / streaming request and response schemas."""

from pydantic import BaseModel, Field


class ChatStreamRequest(BaseModel):
    """Inbound payload for SSE chat streaming."""

    prompt: str = Field(min_length=1, description="User prompt; must not be empty")
    model: str = Field(default="gpt-4o", description="Target model identifier")


class ChatStreamChunk(BaseModel):
    """Single streamed token/chunk payload embedded in an SSE `data:` line."""

    content: str
    finish_reason: str | None = None
