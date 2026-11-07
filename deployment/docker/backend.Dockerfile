# INM backend image: API (role 01), network worker (role 02) and the ops CLI share this image; the role is
# chosen by SIMS_ROLE + command in compose.yaml. Non-root, no build tools at runtime, no secrets baked in.
ARG PYTHON_IMAGE=python:3.13-slim-bookworm
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r /app/requirements.lock \
 && groupadd -g 10001 inm && useradd -u 10001 -g inm -M -s /usr/sbin/nologin inm \
 && mkdir -p /data && chown inm:inm /data
COPY VERSION release.json /app/
COPY backend /app/backend
COPY workers /app/workers
RUN rm -rf /app/backend/tests && python -m compileall -q /app/backend /app/workers
ARG INM_GIT_COMMIT=unknown
ENV INM_GIT_COMMIT=${INM_GIT_COMMIT} SIMS_DATA_DIR=/data
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
