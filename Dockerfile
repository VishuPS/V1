FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_ENV=production \
    AUTO_CREATE_TABLES=false \
    PORT=8000

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY requirements-prod.lock ./
RUN python -m pip install --no-cache-dir -r requirements-prod.lock

COPY pyproject.toml README.md alembic.ini ./
COPY app ./app
COPY migrations ./migrations
COPY scripts/import_verified_beauty_products.py ./scripts/import_verified_beauty_products.py
RUN python -m pip install --no-cache-dir --no-deps .

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '8000') + '/health', timeout=3)"

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"${PORT:-8000}\" --workers 1 --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-127.0.0.1}\""]
