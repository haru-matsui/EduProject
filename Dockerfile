FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/backend

WORKDIR /app
COPY backend/requirements-lock.txt /app/backend/requirements-lock.txt
RUN python -m pip install --no-cache-dir -r /app/backend/requirements-lock.txt \
    && groupadd --gid 10001 eduproject \
    && useradd --uid 10001 --gid eduproject --no-create-home --shell /usr/sbin/nologin eduproject

COPY --chown=10001:10001 backend/app /app/backend/app
COPY --chown=10001:10001 backend/alembic /app/backend/alembic
COPY --chown=10001:10001 backend/alembic.ini /app/backend/alembic.ini
COPY --chown=10001:10001 frontend/index.html /app/frontend/index.html
COPY --chown=10001:10001 frontend/assets /app/frontend/assets
COPY --chown=10001:10001 scripts/container_entrypoint.py /app/scripts/container_entrypoint.py

USER 10001:10001
WORKDIR /app/backend
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3).close()"
CMD ["python", "/app/scripts/container_entrypoint.py"]
