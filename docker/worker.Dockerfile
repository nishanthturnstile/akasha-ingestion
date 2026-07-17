FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GDAL_CACHEMAX=512 \
    GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR \
    CPL_VSIL_CURL_ALLOWED_EXTENSIONS=.tif,.tiff

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libcurl4 libexpat1 libsqlite3-0 \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY worker.py ./worker.py
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
COPY docs/phase-0/bangalore-aoi.geojson ./docs/phase-0/bangalore-aoi.geojson
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir .

CMD ["celery", "-A", "akasha.jobs.celery_app.celery_app", "worker", "--loglevel=INFO"]
