FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
COPY docs/phase-0/bangalore-aoi.geojson ./docs/phase-0/bangalore-aoi.geojson
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir .

EXPOSE 8000
CMD ["uvicorn", "akasha.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
