# INM AI service (role 07). Minimal dependencies on purpose: no database, NATS, SSH or Aruba libraries exist in
# this image, so it cannot reach platform data or devices even if misconfigured.
ARG PYTHON_IMAGE=python:3.13-slim-bookworm
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY ai_service/requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r /app/requirements.lock && useradd -u 10002 -M -s /usr/sbin/nologin inmai
COPY VERSION /app/VERSION
COPY ai_service /app/ai_service
USER 10002
EXPOSE 8001
CMD ["python", "-m", "uvicorn", "ai_service.app:app", "--host", "0.0.0.0", "--port", "8001"]
