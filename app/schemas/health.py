"""Health check response schemas."""

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Payload returned by the health check endpoint."""

    status: str = Field(description="Service health status", examples=["ok"])
    service: str = Field(description="Application service name")
    environment: str = Field(description="Current runtime environment")
