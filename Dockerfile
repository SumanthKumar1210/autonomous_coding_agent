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

# ---- sandbox: build tools + the stdlib-only sandbox server (docker compose service "sandbox") ----
#   docker build --target sandbox -t autoforge-sandbox .
# Runs repository checks for the service. No secrets, no service dependencies; in compose it has no
# internet access. The server starts as root with only SETUID/SETGID/KILL and runs every job as "runner".
FROM python:3.12-slim AS sandbox
ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1
RUN apt-get update  && apt-get install -y --no-install-recommends gcc g++ make cmake libc6-dev  && rm -rf /var/lib/apt/lists/*  && pip install --no-cache-dir pytest==9.2.0 ruff==0.17.5  && useradd --system --uid 10002 --user-group --no-create-home --shell /usr/sbin/nologin runner  && mkdir -p /work
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir --no-deps /wheels/autoforge-*.whl && rm -rf /wheels
WORKDIR /work
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3   CMD ["python", "-c", "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=4).status != 200)"]
CMD ["python", "-I", "-m", "autoforge.sandbox.server", "--host", "0.0.0.0", "--port", "8090", "--work-dir", "/work", "--job-user", "runner"]

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
# The eval suite (fixtures + cases) for `autoforge eval`, versioned with the code it measures.
COPY evals /app/evals
USER autoforge
WORKDIR /app
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status != 200)"]
CMD ["autoforge", "serve", "--host", "0.0.0.0", "--port", "8080"]
