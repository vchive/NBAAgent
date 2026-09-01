# syntax=docker/dockerfile:1.7
# Fixture-first runtime image for local review and reproducible demos.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PUBLIC_DATA_MODE=fixture \
    LLM_MODE=mock \
    RUNTIME_PROFILE=template \
    HERMES_LITE_MODE=off

WORKDIR /app

# Keep the package index configurable: mainland-China mirrors are dramatically
# faster for the public interview host, while CI can pass the canonical PyPI
# URL when required.
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

# Install third-party dependencies before copying application source. This
# preserves the expensive dependency layer when only Python/HTML code changes.
COPY pyproject.toml README.md ./

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --prefer-binary --default-timeout=120 --retries=5 \
    --index-url "${PIP_INDEX_URL}" \
    "setuptools>=69" \
    "fastapi>=0.115,<1" \
    "hermes-agent==0.19.0" \
    "httpx>=0.27,<1" \
    "pydantic>=2.7,<3" \
    "uvicorn>=0.30,<1" \
    && addgroup --system --gid 10001 nbaagent \
    && adduser --system --uid 10001 --gid 10001 --home /nonexistent --no-create-home nbaagent

COPY apps ./apps
COPY docs ./docs

# Install this project itself without dependency resolution; dependencies are
# already present in the cached layer above.
RUN python -m pip install --no-deps --no-build-isolation . \
    && mkdir -p /app/data \
    && chown -R nbaagent:nbaagent /app

USER nbaagent

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=8s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=2)"

CMD ["uvicorn", "apps.api.src.main:app", "--host", "0.0.0.0", "--port", "8000"]
