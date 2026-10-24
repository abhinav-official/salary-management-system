FROM python:3.14.8-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --uid 10001 --create-home salary \
    && mkdir -p /var/lib/salary && chown salary:salary /var/lib/salary
COPY --chown=salary:salary app ./app
USER salary
EXPOSE 8000
CMD ["sh", "-c", "python -m app.migrate && exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --workers 1 --no-proxy-headers"]

FROM runtime AS test
USER root
COPY requirements-dev.txt ./requirements-dev.txt
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY tests ./tests
USER salary
CMD ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]
