# AutoForge webhook service image.
#   docker build -t autoforge .
#   docker run --env-file .env -p 127.0.0.1:8080:8080 -v autoforge-data:/data autoforge
# Configuration comes from environment variables (see .env.example); no secrets are baked in.

# ---- build: compile the package into a wheel ------------------------------------------
FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --upgrade pip \
 && pip wheel --no-cache-dir --wheel-dir /wheels .

# ---- runtime: minimal image, non-root user, state on a volume -------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CHECKPOINT_DB=/data/checkpoints.sqlite \
    JOBS_DB=/data/jobs.sqlite
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin autoforge \
 && mkdir -p /data /app \
 && chown autoforge /data
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels
USER autoforge
WORKDIR /app
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status != 200)"]
CMD ["autoforge", "serve", "--host", "0.0.0.0", "--port", "8080"]
