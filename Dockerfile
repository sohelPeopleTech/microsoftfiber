# Capacity Intelligence Platform — Module 5
#
# Two stages so the build tools that compile numpy/scipy wheels do not travel to
# production. The runtime image carries the interpreter, the installed packages
# and the application, and nothing else.

FROM python:3.12-slim AS build

# Build-only: some wheels for scipy/statsmodels compile from source on slim.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gfortran \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# Dependency metadata first, so a code change does not re-resolve the whole
# dependency tree on every build.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install ".[webapp]"

# --------------------------------------------------------------------------

FROM python:3.12-slim AS runtime

# Runs unprivileged. Container Apps does not require it, but a web process that
# never needs to write outside its own tree has no reason to be root.
RUN useradd --create-home --uid 10001 appuser

WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY --chown=appuser:appuser src/     ./src/
COPY --chown=appuser:appuser webapp/  ./webapp/
COPY --chown=appuser:appuser data/    ./data/
COPY --chown=appuser:appuser config.json pyproject.toml README.md ./

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

USER appuser
EXPOSE 8000

# /health is unauthenticated by design, so the platform can probe it without a
# session. Everything else redirects to the login.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,os,sys; sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",8000)}/health', timeout=4).status==200 else 1)"

WORKDIR /app/webapp
# Shell form so $PORT is expanded — Container Apps injects the target port.
CMD uvicorn api:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'
