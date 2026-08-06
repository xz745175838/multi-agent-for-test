"""Health check API endpoints."""

import asyncio

from fastapi import APIRouter

from app.core.config import settings
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Return service health status after a short async wait.

    Demonstrates that route handlers are fully asynchronous and must not
    block the event loop with synchronous I/O.
    """
    await asyncio.sleep(0.01)
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        environment=settings.app_env,
    )
