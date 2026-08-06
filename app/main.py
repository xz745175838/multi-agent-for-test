"""FastAPI application entrypoint."""

import uvicorn
from fastapi import FastAPI

from app.api.v1.health import router as health_router
from app.core.config import settings

app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    version="0.1.0",
)

app.include_router(health_router, prefix=settings.api_v1_prefix)


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
