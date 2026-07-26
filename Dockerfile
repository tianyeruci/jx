FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
ARG INSTALL_TARGET=.
COPY pyproject.toml requirements.txt ./
COPY dify_client.py ./
COPY ai_management ./ai_management
COPY knowledge_cleaner ./knowledge_cleaner
COPY scripts ./scripts
COPY test_data ./test_data
COPY alembic.ini ./
COPY migrations ./migrations
RUN pip install --no-cache-dir "${INSTALL_TARGET}"

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/var/uploads \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "ai_management.main:app", "--host", "0.0.0.0", "--port", "8000"]
