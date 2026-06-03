# stac-publish: reusable dual-write (CKAN + STAC) task image.
# Light pip-only image — it just talks HTTP, no geo/raster processing.
#
# Build context is the repo root: `docker build -f tasks/publish/Dockerfile .`
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates bash \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-task.txt /app/requirements-task.txt
RUN pip install --no-cache-dir -r requirements-task.txt

# Only what the task imports: the shared core + the task package.
COPY stacmap /app/stacmap
COPY tasks /app/tasks

ENV PYTHONPATH=/app PYTHONUNBUFFERED=1

ENTRYPOINT ["bash", "/app/tasks/publish/run.sh"]
