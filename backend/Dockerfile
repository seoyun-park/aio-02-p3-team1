FROM python:3.12-slim AS runtime

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY data ./data
COPY scripts ./scripts
COPY migrations ./migrations

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

# Tests inherit the exact runtime application and dependency versions.
FROM runtime AS test
COPY requirements-dev.txt .
RUN pip freeze > /tmp/runtime-constraints.txt && \
    pip install --no-cache-dir -c /tmp/runtime-constraints.txt -r requirements-dev.txt
COPY tests ./tests
CMD ["python", "-m", "pytest", "--cov=app", "--cov-report=xml:/reports/backend-coverage.xml", "--cov-report=term-missing:skip-covered", "--cov-fail-under=70", "--junitxml=/reports/backend-tests.xml"]

FROM runtime AS production
