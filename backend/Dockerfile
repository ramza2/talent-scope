FROM python:3.12-slim AS base

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# LibreOffice (headless convert) + CJK fonts for Korean document rendering.
# Image size impact: LibreOffice packages are large; kept to writer/calc/impress
# rather than the full desktop metapackage where possible.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libreoffice-writer \
        libreoffice-calc \
        libreoffice-impress \
        fonts-noto-cjk \
        fonts-nanum \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

RUN pip install --upgrade pip \
    && pip install .

# Server-side pytest executes a small frontend TypeScript contract through
# `npx tsx`. Keep Node/npm out of the production API/worker image by installing
# them only in the dedicated test target selected by docker-compose.server.yml.
FROM base AS test
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/*

FROM base AS runtime

# Same runtime image runs API or Celery worker via command override.
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
