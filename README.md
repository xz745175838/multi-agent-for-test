# RAG & Multi-Agent Intelligent API Platform

Production-grade enterprise platform for RAG, multi-agent API choreography, and automated code/doc audit.

## Tech Stack

- **Python 3.11+** with strict type hints
- **FastAPI** (ASGI, asyncio)
- **Pydantic v2** / **pydantic-settings**
- **PostgreSQL 16** (asyncpg)
- **Redis 7** (redis-py async)
- Planned: Qdrant, LangGraph / LangChain, Docker SDK sandbox

## Project Structure

```text
app/
├── api/v1/          # Versioned HTTP routes
├── core/            # Settings and shared infrastructure
├── models/          # ORM / domain models (Day 2+)
├── schemas/         # Pydantic request/response schemas
├── services/        # Business logic layer (Day 2+)
└── main.py          # FastAPI application entrypoint
```

## Prerequisites

- Python 3.11+
- Docker & Docker Compose
- A virtual environment (recommended)

## Quick Start

### 1. Clone and create a virtualenv

```bash
git clone <your-repo-url>.git
cd multi-agent-for-test
python3 -m venv venv
source venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
```

### 4. Start infrastructure (PostgreSQL 16 + Redis 7)

```bash
docker compose up -d
```

Verify containers are healthy:

```bash
docker compose ps
```

### 5. Run the API

```bash
python -m app.main
# or
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 6. Health check

```bash
curl http://localhost:8000/api/v1/health
```

Expected response:

```json
{
  "status": "ok",
  "service": "RAG & Multi-Agent Platform",
  "environment": "development"
}
```

Interactive docs: [http://localhost:8000/docs](http://localhost:8000/docs)

## Configuration

Settings are loaded via `pydantic-settings` from environment variables / `.env`.

Computed connection strings (see `app/core/config.py`):

| Field | Example |
| --- | --- |
| `async_database_url` | `postgresql+asyncpg://postgres:postgres@localhost:5432/multi_agent` |
| `redis_url` | `redis://localhost:6379/0` |

## Day 1 Scope

- [x] FastAPI async application skeleton
- [x] Modular package layout (`api`, `core`, `models`, `schemas`, `services`)
- [x] Environment-driven settings with computed DSNs
- [x] Docker Compose for PostgreSQL 16 & Redis 7 (with healthchecks)
- [x] Async `/api/v1/health` endpoint

## Roadmap (upcoming)

- Async SQLAlchemy models & migrations
- Redis session / cache integration
- Qdrant vector store
- Multi-agent orchestration (LangGraph)
- RAG pipelines & code/doc audit agents

## License

Proprietary / private — update as needed.
