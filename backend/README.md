# TalentScope Backend

FastAPI modular monolith skeleton for TalentScope.

## Stack

- Python 3.12
- FastAPI / Pydantic v2
- SQLAlchemy 2.x / Alembic / psycopg 3
- Celery + Redis
- pgvector

## Quick start

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp ../.env.example ../.env

uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Health:

- `GET /api/v1/health/live`
- `GET /api/v1/health/ready`

Migrations (operational schema source after baseline):

```bash
alembic upgrade head
```

The initial revision embeds the MVP DDL (self-contained) and does not read
`db/schema.sql` at runtime, so Backend Docker images can migrate empty databases.
Worker:

```bash
celery -A app.tasks.celery_app.celery_app worker -Q document,analysis,index -l info
```

Business features (Person CRUD, document workflow, AI analysis, search) are **not** implemented in this skeleton.
